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


def audit(directory: Path) -> dict:
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
    for key, value in calculated.items():
        if value is None and saved[key] is None:
            continue
        if not math.isclose(value, saved[key], rel_tol=1e-12, abs_tol=1e-9):
            raise ValueError(f"{directory}: {key} mismatch")
    for key in ("mean_ttft_ms", "mean_itl_ms"):
        if not math.isclose(calculated[key], native[key], rel_tol=1e-12, abs_tol=1e-9):
            raise ValueError(f"Native collector disagrees on {key}")
    return {
        "matches": True,
        "requests": len(requests),
        "slo_passes": passes,
        "metrics_sha256": hashlib.sha256(
            (directory / "metrics.json").read_bytes()
        ).hexdigest(),
    }


if __name__ == "__main__":
    root = Path(sys.argv[1])
    results = {
        str(path.parent.relative_to(root)): audit(path.parent)
        for path in sorted(root.rglob("metrics.json"))
    }
    if not results:
        raise ValueError("No saved replay metrics found")
    print(json.dumps(results, indent=2))
