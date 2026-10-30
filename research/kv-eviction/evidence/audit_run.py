"""Independent arithmetic check of a saved run; no experiment-module imports."""

import hashlib
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path


def quantile(values):
    return sorted(values)[math.ceil(0.95 * len(values)) - 1] if values else None


def held_quantile(pairs, quantile=0.95):
    total = sum(duration for _, duration in pairs)
    running = 0.0
    for value, duration in sorted(pairs):
        running += duration
        if running >= quantile * total * (1 - 1e-12):
            return value
    return None


def audit_occupancy(directory: Path, saved: dict, requests: list[dict]) -> None:
    """Recompute sample-and-hold occupancy and cross-check native preemptions."""
    samples = [
        json.loads(line)
        for line in (directory / "occupancy.jsonl").read_text().splitlines()
    ]
    if [sample["ordinal"] for sample in samples] != list(range(len(samples))):
        raise ValueError("Occupancy samples are missing or reordered")
    if samples[0]["kind"] != "baseline" or samples[-1]["sampled_at_ms"] != max(
        row["terminal_time_ms"] for row in requests
    ):
        raise ValueError("Occupancy lacks a baseline or does not reach the run end")
    for sample in samples:
        for rank in sample["ranks"]:
            if (
                rank["active_blocks"] + rank["inactive_blocks"] + rank["free_blocks"]
                != rank["total_blocks"]
            ):
                raise ValueError("Occupancy conservation failed")
    held = [
        samples[index + 1]["sampled_at_ms"] - samples[index]["sampled_at_ms"]
        for index in range(len(samples) - 1)
    ]
    held.append(0.0)
    busy = [
        sample["router_pending_requests"]
        + sum(r["running_requests"] + r["waiting_requests"] for r in sample["ranks"])
        > 0
        for sample in samples
    ]
    if sum(sample["preemptions"] for sample in samples) != saved["preemptions"]:
        raise ValueError("Occupancy preemption total mismatch")
    if saved["preemptions"] != sum(row["readmission_count"] for row in requests):
        raise ValueError("Telemetry preemptions disagree with request readmissions")
    checks = {
        "observation_ms": samples[-1]["sampled_at_ms"] - samples[0]["sampled_at_ms"],
        "request_active_ms": sum(d for d, b in zip(held, busy) if b),
    }
    for key, value in checks.items():
        if not math.isclose(value, saved[key], rel_tol=1e-9, abs_tol=1e-6):
            raise ValueError(f"Occupancy {key} mismatch")
    for position, rank_key in enumerate(saved["ranks"]):
        summary = saved["ranks"][rank_key]
        capacity = summary["capacity_blocks"]
        for period, mask in (
            ("whole_run", [True] * len(samples)),
            ("request_active", busy),
        ):
            for name, extract in (
                (
                    "physical_blocks",
                    lambda r: r["active_blocks"] + r["inactive_blocks"],
                ),
                ("active_blocks", lambda r: r["active_blocks"]),
                ("waiting_requests", lambda r: r["waiting_requests"]),
            ):
                values = [extract(s["ranks"][position]) for s in samples]
                pairs = [(v, d) for v, d, m in zip(values, held, mask) if m]
                total = sum(d for _, d in pairs)
                expected = {
                    "mean": sum(v * d for v, d in pairs) / total if total else None,
                    "p95": held_quantile(pairs) if total else None,
                    "peak_sampled": max(
                        (v for v, m in zip(values, mask) if m), default=None
                    ),
                }
                if name.endswith("_blocks"):
                    for level in (95, 100):
                        above = sum(d for v, d in pairs if v * 100 >= level * capacity)
                        expected[f"time_fraction_at_or_above_{level}pct"] = (
                            above / total if total else None
                        )
                for key, value in expected.items():
                    actual = summary[period][name][key]
                    if (value is None) != (actual is None) or (
                        value is not None
                        and not math.isclose(value, actual, rel_tol=1e-9, abs_tol=1e-9)
                    ):
                        raise ValueError(f"Occupancy {period}/{name}/{key} mismatch")


def audit_summary(directory: Path, saved: dict, native: dict) -> tuple:
    """Recompute metrics from per-request token summaries (large agentic runs)."""
    requests = [
        json.loads(line)
        for line in (directory / "requests.jsonl").read_text().splitlines()
    ]
    summaries = {}
    for line in (directory / "token-summary.jsonl").read_text().splitlines():
        row = json.loads(line)
        summaries[row["uuid"]] = row
    pooled = json.loads((directory / "token-pooled.json").read_text())
    ttfts, latencies, passes, gap_time, gap_count = [], [], 0, 0.0, 0
    for request in requests:
        summary = summaries.get(request["uuid"])
        if (summary["tokens"] if summary else 0) != request["output_length"]:
            raise ValueError("Token summary count mismatch")
        if request["terminal_status"] == "completed":
            latencies.append(request["terminal_time_ms"] - request["arrival_time_ms"])
        if (
            request["terminal_status"] != "completed"
            or request["requested_output_length"] == 0
        ):
            continue
        first = summary["first_ms"] - request["arrival_time_ms"]
        ttfts.append(first)
        gap_time += summary["last_ms"] - summary["first_ms"]
        gap_count += summary["tokens"] - 1
        tail = summary["gap_p95_ms"]
        passes += first <= saved["ttft_slo_ms"] and (
            tail is None or tail <= saved["itl_slo_ms"]
        )
    if pooled["gap_count"] != sum(max(r["output_length"] - 1, 0) for r in requests):
        raise ValueError("Pooled gap count mismatch")
    duration = max(row["terminal_time_ms"] for row in requests) - min(
        row["arrival_time_ms"] for row in requests
    )
    hits = sum(
        row["admission_history"][0]["reused_input_tokens"]
        if row["admission_history"]
        else 0
        for row in requests
    )
    calculated = {
        "mean_request_latency_ms": statistics.mean(latencies),
        "p95_request_latency_ms": quantile(latencies),
        "mean_ttft_ms": statistics.mean(ttfts),
        "p95_ttft_ms": quantile(ttfts),
        "mean_itl_ms": gap_time / gap_count if gap_count else None,
        "p95_itl_ms": pooled["gap_p95_ms"],
        "goodput_rps": passes * 1000 / duration,
        "slo_attainment_pct": 100
        * passes
        / sum(row["requested_output_length"] > 0 for row in requests),
        "token_hit_pct": 100 * hits / sum(row["input_length"] for row in requests),
        "recomputed_input_tokens": native["committed_prefill_tokens"],
        "evictions": json.loads((directory / "evictions.json").read_text())[
            "evictions"
        ],
    }
    return calculated, passes, requests


def audit(directory: Path) -> dict:
    if not (directory / "tokens.jsonl").exists():
        saved = json.loads((directory / "metrics.json").read_text())
        native = json.loads((directory / "native-report.json").read_text())
        calculated, passes, requests = audit_summary(directory, saved, native)
        return verify(directory, saved, native, calculated, passes, requests, 1e-9)
    requests = [
        json.loads(line)
        for line in (directory / "requests.jsonl").read_text().splitlines()
    ]
    timestamps = defaultdict(list)
    for line in (directory / "tokens.jsonl").read_text().splitlines():
        event = json.loads(line)
        timestamps[event["uuid"]].append(event["time_ms"])
    saved = json.loads((directory / "metrics.json").read_text())
    native = json.loads((directory / "native-report.json").read_text())
    ttfts, gaps, passes = [], [], 0
    for request in requests:
        times = timestamps[request["uuid"]]
        if len(times) != request["output_length"]:
            raise ValueError("Token count mismatch")
        if (
            request["terminal_status"] != "completed"
            or request["requested_output_length"] == 0
        ):
            continue
        first = times[0] - request["arrival_time_ms"]
        differences = [
            times[index] - times[index - 1] for index in range(1, len(times))
        ]
        ttfts.append(first)
        gaps.extend(differences)
        tail = quantile(differences)
        passes += first <= saved["ttft_slo_ms"] and (
            tail is None or tail <= saved["itl_slo_ms"]
        )
    duration = max(row["terminal_time_ms"] for row in requests) - min(
        row["arrival_time_ms"] for row in requests
    )
    hits = sum(
        row["admission_history"][0]["reused_input_tokens"]
        if row["admission_history"]
        else 0
        for row in requests
    )
    calculated = {
        "mean_ttft_ms": statistics.mean(ttfts),
        "p95_ttft_ms": quantile(ttfts),
        "mean_itl_ms": statistics.mean(gaps) if gaps else None,
        "p95_itl_ms": quantile(gaps),
        "goodput_rps": passes * 1000 / duration,
        "slo_attainment_pct": 100
        * passes
        / sum(row["requested_output_length"] > 0 for row in requests),
        "token_hit_pct": 100 * hits / sum(row["input_length"] for row in requests),
        "recomputed_input_tokens": native["committed_prefill_tokens"],
        "evictions": json.loads((directory / "evictions.json").read_text())[
            "evictions"
        ],
    }
    return verify(directory, saved, native, calculated, passes, requests, 1e-12)


def verify(directory, saved, native, calculated, passes, requests, tolerance) -> dict:
    """Compare recomputed and saved metrics, then occupancy; return a receipt."""
    for key, value in calculated.items():
        if value is None and saved[key] is None:
            continue
        if not math.isclose(value, saved[key], rel_tol=1e-12, abs_tol=1e-9):
            raise ValueError(f"{directory}: {key} mismatch")
    if "occupancy" in saved:
        audit_occupancy(directory, saved["occupancy"], requests)
    for key in ("mean_ttft_ms", "mean_itl_ms"):
        if not math.isclose(
            calculated[key], native[key], rel_tol=tolerance, abs_tol=1e-9
        ):
            raise ValueError(f"Native collector disagrees on {key}")
    result = {
        "matches": True,
        "requests": len(requests),
        "slo_passes": passes,
        "metrics_sha256": hashlib.sha256(
            (directory / "metrics.json").read_bytes()
        ).hexdigest(),
    }
    if "occupancy" in saved:
        # Older receipts predate telemetry; keep their audit records unchanged.
        result["occupancy_audited"] = True
    return result


if __name__ == "__main__":
    root = Path(sys.argv[1])
    results = {
        str(path.parent.relative_to(root)): audit(path.parent)
        for path in sorted(root.rglob("metrics.json"))
    }
    if not results:
        raise ValueError("No saved replay metrics found")
    print(json.dumps(results, indent=2))
