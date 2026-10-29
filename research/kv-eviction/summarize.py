"""Regenerate the matched-pilot metrics table from committed compact evidence."""

import argparse
import csv
import json
from pathlib import Path

METRICS = [
    "mean_ttft_ms",
    "p95_ttft_ms",
    "mean_itl_ms",
    "p95_itl_ms",
    "goodput_rps",
    "slo_attainment_pct",
    "token_hit_pct",
    "recomputed_input_tokens",
    "evictions",
]


def summarize(directory: Path, output: Path) -> None:
    grouped = {}
    for filename in sorted(directory.glob("*.json")):
        record = json.loads(filename.read_text())
        group = grouped.setdefault(record["trace"], {})
        if record["agent"] in group:
            raise ValueError("Select exactly one pilot per agent/trace")
        group[record["agent"]] = record
    if not grouped:
        raise ValueError("No compact run evidence found")
    rows, verification = [], {}
    for trace, records in sorted(grouped.items()):
        if set(records) != {"claude", "codex"}:
            raise ValueError(f"Need both agent pilots for {trace}")
        left, right = records["claude"], records["codex"]
        for field in ("config_sha256", "slo", "submission_budget", "selection"):
            if left["manifest"][field] != right["manifest"][field]:
                raise ValueError(
                    f"Mismatched {field} in {trace}; do not compare these runs"
                )
        for split in ("tuning", "evaluation"):
            if (
                left["manifest"]["trace"]["windows"][split]["sha256"]
                != right["manifest"]["trace"]["windows"][split]["sha256"]
            ):
                raise ValueError(f"Mismatched {split} data in {trace}")
            for metric in METRICS:
                if (
                    left["baselines"]["native-lru"][split][metric]
                    != right["baselines"]["native-lru"][split][metric]
                ):
                    raise ValueError(f"Different baseline {metric} in {trace}")
        verification[trace] = {"matched_settings_and_baselines": True}
        baseline = left["baselines"]["native-lru"]["evaluation"]
        policies = [
            ("LRU", None, baseline),
            ("Claude", left["model"], left["evaluation"]),
            ("Codex", right["model"], right["evaluation"]),
        ]
        for label, model, metrics in policies:
            rows.append(
                {
                    "trace": trace,
                    "policy": label,
                    "agent_model": model,
                    "requests": metrics["requests"],
                    "slo_passes": metrics["slo_passes"],
                    **{key: metrics[key] for key in METRICS},
                }
            )
    output.mkdir(parents=True, exist_ok=True)
    with (output / "results.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    (output / "results.json").write_text(
        json.dumps({"verification": verification, "results": rows}, indent=2) + "\n"
    )
    print(
        f"Verified {len(grouped)} matched trace comparisons; wrote {len(rows)} rows to {output}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    summarize(args.runs, args.output or args.runs.parent)
