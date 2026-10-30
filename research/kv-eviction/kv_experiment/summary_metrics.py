"""Score a replay from exact per-request token summaries instead of token events.

Same definitions as metrics.score: nearest-rank percentiles, pooled ITL over
individual gaps, per-request p95 gap for the SLO, failures in the denominator.
The engine writes each request's first/last token time and nearest-rank p95
gap, plus the pooled nearest-rank p95 gap. Pooled mean ITL is the sum of
(last - first) over the sum of (tokens - 1), which equals the mean of all gaps.
Agentic runs add request latency (arrival to terminal event) and play spans.
"""

import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

from .metrics import percentile, read_jsonl


def score_summary(
    directory: Path,
    ttft_slo_ms: float,
    itl_slo_ms: float,
    write_request_scores: bool = False,
) -> dict:
    rows = read_jsonl(directory / "requests.jsonl")
    native = json.loads((directory / "native-report.json").read_text())
    summaries = {s["uuid"]: s for s in read_jsonl(directory / "token-summary.jsonl")}
    pooled = json.loads((directory / "token-pooled.json").read_text())
    if not rows or len(rows) != native["num_requests"]:
        raise ValueError("Request records disagree with native request count")
    ttfts, latencies, request_scores = [], [], []
    gap_time = gap_count = 0
    passing = completed = input_tokens = reused = 0
    plays = defaultdict(lambda: [math.inf, -math.inf])
    for row in rows:
        summary = summaries.pop(row["uuid"], None)
        tokens = summary["tokens"] if summary else 0
        if tokens != row["output_length"]:
            raise ValueError("Token summary count disagrees with native report")
        complete = row["terminal_status"] == "completed"
        eligible = row["requested_output_length"] > 0
        if complete and tokens != row["requested_output_length"]:
            raise ValueError("Completed request emitted an unexpected token count")
        ttft = summary["first_ms"] - row["arrival_time_ms"] if summary else None
        if ttft is not None and ttft < 0:
            raise ValueError("Token emitted before arrival")
        tail = summary["gap_p95_ms"] if summary else None
        meets = complete and eligible and ttft is not None and ttft <= ttft_slo_ms
        meets = meets and (tail is None or tail <= itl_slo_ms)
        passing += int(meets)
        completed += int(complete)
        if complete and eligible:
            ttfts.append(ttft)
            gap_time += summary["last_ms"] - summary["first_ms"]
            gap_count += tokens - 1
        if complete:
            latencies.append(row["terminal_time_ms"] - row["arrival_time_ms"])
        play = plays[row.get("play_id")]
        play[0] = min(play[0], row["arrival_time_ms"])
        play[1] = max(play[1], row["terminal_time_ms"])
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
                "p95_itl_ms": tail,
                "meets_slo": meets,
            }
        )
    if summaries:
        raise ValueError("Token summaries have no matching request")
    if pooled["gap_count"] != sum(max(row["output_length"] - 1, 0) for row in rows):
        raise ValueError("Pooled gap count disagrees with request outputs")
    if (
        input_tokens != native["total_input_tokens"]
        or sum(row["output_length"] for row in rows) != native["total_output_tokens"]
        or completed != native["completed_requests"]
    ):
        raise ValueError("Request records disagree with native accounting")
    duration_ms = max(row["terminal_time_ms"] for row in rows) - min(
        row["arrival_time_ms"] for row in rows
    )
    eligible_count = sum(row["requested_output_length"] > 0 for row in rows)
    spans = [end - start for start, end in plays.values()]
    evictions = json.loads((directory / "evictions.json").read_text())
    result = {
        "valid": True,
        "requests": len(rows),
        "completed": completed,
        "mean_request_latency_ms": statistics.mean(latencies) if latencies else None,
        "p95_request_latency_ms": percentile(latencies),
        "mean_ttft_ms": statistics.mean(ttfts) if ttfts else None,
        "p95_ttft_ms": percentile(ttfts),
        "mean_itl_ms": gap_time / gap_count if gap_count else None,
        "p95_itl_ms": pooled["gap_p95_ms"],
        "itl_samples": gap_count,
        "goodput_rps": passing * 1000 / duration_ms,
        "slo_attainment_pct": 100 * passing / eligible_count
        if eligible_count
        else None,
        "slo_passes": passing,
        "slo_eligible_requests": eligible_count,
        "zero_output_requests": len(rows) - eligible_count,
        "observation_duration_ms": duration_ms,
        "plays": len(plays),
        "mean_play_span_ms": statistics.mean(spans),
        "p95_play_span_ms": percentile(spans),
        "input_tokens": input_tokens,
        "reused_input_tokens": reused,
        "token_hit_pct": 100 * reused / input_tokens if input_tokens else None,
        "recomputed_input_tokens": native["committed_prefill_tokens"],
        "evictions": evictions["evictions"],
        "protected_evictions": evictions.get("protected_evictions", 0),
        "ttft_slo_ms": ttft_slo_ms,
        "itl_slo_ms": itl_slo_ms,
        "quantile": "nearest rank",
        "itl_slo": "per-request p95 of individual gaps",
        "token_timing": "exact per-request summaries (token-summary.jsonl, token-pooled.json)",
    }
    if write_request_scores:
        (directory / "request-scores.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in request_scores)
        )
    return result
