"""Rescore native token events: ITL percentiles use individual gaps, never TPOT."""

import itertools
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path


def percentile(samples: list[float], quantile: float = 0.95) -> float | None:
    """Nearest-rank empirical quantile; no sketch or interpolation."""
    return sorted(samples)[math.ceil(quantile * len(samples)) - 1] if samples else None


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def score(
    directory: Path,
    ttft_slo_ms: float,
    itl_slo_ms: float,
    write_request_scores: bool = False,
) -> dict:
    rows = read_jsonl(directory / "requests.jsonl")
    native = json.loads((directory / "native-report.json").read_text())
    events = defaultdict(list)
    for event in read_jsonl(directory / "tokens.jsonl"):
        timestamp = event["time_ms"]
        if not math.isfinite(timestamp):
            raise ValueError("Non-finite token timestamp")
        events[event["uuid"]].append(timestamp)
    ttfts, gaps = [], []
    passing, completed, input_tokens, reused = 0, 0, 0, 0
    request_scores = []
    if not rows:
        raise ValueError("Replay returned no request records")
    if len(rows) != native["num_requests"] or len({row["uuid"] for row in rows}) != len(
        rows
    ):
        raise ValueError("Request records disagree with native request count")
    for row in rows:
        times = events.pop(row["uuid"], [])
        if any(right < left for left, right in itertools.pairwise(times)):
            raise ValueError("Nonmonotonic token timestamps")
        if len(times) != row["output_length"]:
            raise ValueError("Token event count disagrees with native report")
        complete = row["terminal_status"] == "completed"
        eligible = row["requested_output_length"] > 0
        if complete and len(times) != row["requested_output_length"]:
            raise ValueError("Completed request emitted an unexpected token count")
        ttft = times[0] - row["arrival_time_ms"] if times else None
        if ttft is not None and ttft < 0:
            raise ValueError("Token emitted before arrival")
        request_gaps = [right - left for left, right in itertools.pairwise(times)]
        tail = percentile(request_gaps)
        meets = complete and eligible and ttft is not None and ttft <= ttft_slo_ms
        meets = meets and (tail is None or tail <= itl_slo_ms)
        passing += int(meets)
        completed += int(complete)
        if complete and eligible:
            ttfts.append(ttft)
            gaps.extend(request_gaps)
        input_tokens += row["input_length"]
        admissions = row["admission_history"]
        cached = admissions[0]["reused_input_tokens"] if admissions else 0
        if not 0 <= cached <= row["input_length"]:
            raise ValueError("Invalid first-admission prefix accounting")
        reused += cached
        request_scores.append(
            {
                "request_id": row["request_id"],
                "ttft_ms": ttft,
                "mean_itl_ms": statistics.mean(request_gaps) if request_gaps else None,
                "p95_itl_ms": tail,
                "meets_slo": meets,
            }
        )
    if events:
        raise ValueError("Token events have no matching request")
    if (
        input_tokens != native["total_input_tokens"]
        or sum(row["output_length"] for row in rows) != native["total_output_tokens"]
        or completed != native["completed_requests"]
    ):
        raise ValueError(
            "Request records disagree with native token/completion accounting"
        )
    duration_ms = max(row["terminal_time_ms"] for row in rows) - min(
        row["arrival_time_ms"] for row in rows
    )
    if duration_ms <= 0 or not math.isfinite(duration_ms):
        raise ValueError("Invalid observation duration")
    eligible_count = sum(row["requested_output_length"] > 0 for row in rows)
    result = {
        "valid": True,
        "requests": len(rows),
        "completed": completed,
        "mean_ttft_ms": statistics.mean(ttfts) if ttfts else None,
        "p95_ttft_ms": percentile(ttfts),
        "mean_itl_ms": statistics.mean(gaps) if gaps else None,
        "p95_itl_ms": percentile(gaps),
        "itl_samples": len(gaps),
        "goodput_rps": passing * 1000 / duration_ms,
        "slo_attainment_pct": 100 * passing / eligible_count
        if eligible_count
        else None,
        "slo_passes": passing,
        "slo_eligible_requests": eligible_count,
        "zero_output_requests": len(rows) - eligible_count,
        "observation_duration_ms": duration_ms,
        "input_tokens": input_tokens,
        "reused_input_tokens": reused,
        "token_hit_pct": 100 * reused / input_tokens if input_tokens else None,
        "recomputed_input_tokens": native["committed_prefill_tokens"],
        "evictions": json.loads((directory / "evictions.json").read_text())[
            "evictions"
        ],
        "ttft_slo_ms": ttft_slo_ms,
        "itl_slo_ms": itl_slo_ms,
        "quantile": "nearest rank",
        "itl_slo": "per-request p95 of individual gaps",
        "slo_warning": "SLO attainment is <=5% or >=95%; check whether this load supports meaningful comparisons"
        if eligible_count
        and (passing / eligible_count <= 0.05 or passing / eligible_count >= 0.95)
        else None,
    }
    if write_request_scores:
        (directory / "request-scores.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in request_scores)
        )
    return result
