"""Time-weighted KV occupancy from native simulated-time telemetry samples.

Convention: sample-and-hold. A gauge sampled at t[k] describes [t[k], t[k+1]);
the closing sample carries no duration. The engine emits a final sample unless
the run ends exactly on a periodic boundary; either way the closing sample must
coincide with the last terminal request event, which detects truncation. Samples use a
fixed cadence, so peaks and threshold durations are estimates: an excursion that
starts and ends between two samples is not observed.

Physical occupancy is active plus inactive (reclaimable) blocks. A physically
full cache of reclaimable blocks is not, by itself, active-memory pressure.
"""

import itertools
import json
import math
from pathlib import Path

CONVENTION = (
    "sample-and-hold: sample k covers [t_k, t_k+1); the closing sample has no duration"
)
THRESHOLDS = (0.95, 1.0)


def read_samples(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def validate(samples: list[dict], sample_interval_ms: float, end_ms: float) -> None:
    """Reject truncated, reordered or non-conserving telemetry before summarizing."""
    if len(samples) < 2 or samples[0]["kind"] != "baseline":
        raise ValueError("Occupancy needs a baseline and a closing sample")
    if not math.isclose(samples[-1]["sampled_at_ms"], end_ms, rel_tol=0, abs_tol=1e-6):
        raise ValueError("Occupancy does not reach the last terminal request event")
    origin = samples[0]["sampled_at_ms"]
    ranks = None
    for index, sample in enumerate(samples):
        if sample["ordinal"] != index:
            raise ValueError("Occupancy sample ordinals are not consecutive")
        if (
            index
            and sample["kind"] != "periodic"
            and (sample["kind"] != "final" or index != len(samples) - 1)
        ):
            raise ValueError("Only the closing sample may follow periodic samples")
        if index:
            previous = samples[index - 1]["sampled_at_ms"]
            if sample["sampled_at_ms"] < previous or not math.isclose(
                sample["interval_start_ms"], previous, rel_tol=0, abs_tol=1e-9
            ):
                raise ValueError("Occupancy samples are not contiguous in time")
        if sample["kind"] == "periodic" and not math.isclose(
            sample["sampled_at_ms"],
            origin + index * sample_interval_ms,
            rel_tol=1e-12,
            abs_tol=1e-6,
        ):
            raise ValueError("Periodic sample does not follow the recorded cadence")
        identities = [(rank["worker_id"], rank["dp_rank"]) for rank in sample["ranks"]]
        if ranks is None:
            ranks = identities
        elif identities != ranks:
            raise ValueError("Worker ranks changed during a fixed-worker replay")
        for rank in sample["ranks"]:
            parts = [
                rank[key] for key in ("active_blocks", "inactive_blocks", "free_blocks")
            ]
            if any(type(value) is not int or value < 0 for value in parts):
                raise ValueError("Occupancy block counts must be nonnegative integers")
            if sum(parts) != rank["total_blocks"]:
                raise ValueError(
                    f"Occupancy conservation failed at sample {index}: "
                    "active + inactive + free != capacity"
                )


def weighted_percentile(pairs: list[tuple[float, float]], quantile: float = 0.95):
    """Smallest value whose cumulative held duration reaches the quantile."""
    total = sum(duration for _, duration in pairs)
    if total <= 0:
        return None
    cumulative = 0.0
    for value, duration in sorted(pairs):
        cumulative += duration
        if cumulative >= quantile * total * (1 - 1e-12):
            return value
    return max(value for value, _ in pairs)


def gauge(pairs: list[tuple[float, float]], peak: float, capacity: int | None) -> dict:
    total = sum(duration for _, duration in pairs)
    result = {
        "mean": sum(value * duration for value, duration in pairs) / total
        if total
        else None,
        "p95": weighted_percentile(pairs),
        "peak_sampled": peak,
    }
    if capacity:
        for threshold in THRESHOLDS:
            held = sum(d for value, d in pairs if value >= threshold * capacity)
            result[f"time_fraction_at_or_above_{round(threshold * 100)}pct"] = (
                held / total if total else None
            )
        for key in ("mean", "p95", "peak_sampled"):
            value = result[key]
            result[f"{key}_pct"] = None if value is None else 100 * value / capacity
    return result


def in_system(sample: dict) -> int:
    return sample["router_pending_requests"] + sum(
        rank["running_requests"] + rank["waiting_requests"] for rank in sample["ranks"]
    )


def summarize(samples: list[dict], sample_interval_ms: float, end_ms: float) -> dict:
    validate(samples, sample_interval_ms, end_ms)
    durations = [
        right["sampled_at_ms"] - left["sampled_at_ms"]
        for left, right in itertools.pairwise(samples)
    ] + [0.0]
    periods = {
        "whole_run": [True] * len(samples),
        # Estimated from the state at each interval start.
        "request_active": [in_system(sample) > 0 for sample in samples],
    }
    ranks = {}
    for position, identity in enumerate(samples[0]["ranks"]):
        capacity = identity["total_blocks"]
        series = {
            "physical_blocks": [
                s["ranks"][position]["active_blocks"]
                + s["ranks"][position]["inactive_blocks"]
                for s in samples
            ],
            "active_blocks": [s["ranks"][position]["active_blocks"] for s in samples],
            "inactive_blocks": [
                s["ranks"][position]["inactive_blocks"] for s in samples
            ],
            "free_blocks": [s["ranks"][position]["free_blocks"] for s in samples],
            "waiting_requests": [
                s["ranks"][position]["waiting_requests"] for s in samples
            ],
            "running_requests": [
                s["ranks"][position]["running_requests"] for s in samples
            ],
        }
        summary = {"worker_id": identity["worker_id"], "dp_rank": identity["dp_rank"]}
        summary["capacity_blocks"] = capacity
        for period, mask in periods.items():
            summary[period] = {}
            for name, values in series.items():
                pairs = [
                    (v, d)
                    for v, d, keep in zip(values, durations, mask, strict=True)
                    if keep
                ]
                peak = max(
                    (v for v, keep in zip(values, mask, strict=True) if keep),
                    default=None,
                )
                summary[period][name] = gauge(
                    pairs, peak, capacity if name.endswith("_blocks") else None
                )
        ranks[f"{identity['worker_id']}:{identity['dp_rank']}"] = summary
    return {
        "convention": CONVENTION,
        "sample_interval_ms": sample_interval_ms,
        "samples": len(samples),
        "first_sample_ms": samples[0]["sampled_at_ms"],
        "closing_sample_ms": samples[-1]["sampled_at_ms"],
        "closing_sample_kind": samples[-1]["kind"],
        "observation_ms": samples[-1]["sampled_at_ms"] - samples[0]["sampled_at_ms"],
        "request_active_ms": sum(
            d
            for d, keep in zip(durations, periods["request_active"], strict=True)
            if keep
        ),
        "preemptions": sum(sample["preemptions"] for sample in samples),
        "conservation": "active + inactive + free == capacity at every sample",
        "estimates": "Peaks and threshold fractions are sampled estimates.",
        "ranks": ranks,
    }
