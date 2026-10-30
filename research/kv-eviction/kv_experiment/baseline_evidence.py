"""Audit a completed full-trace sweep from raw files and package compact evidence.

The audit fails on missing coverage, failed or incomplete jobs, changed inputs,
inconsistent arithmetic or a failed parity check. It never reruns a simulation.
"""

import json
import math
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from evidence.audit_run import audit

from .comparison import job_manifest, plan, policy_table, read_json, write_json
from .inventory import sha256
from .traces import ROOT

RAW_FILES = ("requests.jsonl", "tokens.jsonl", "evictions.json", "occupancy.jsonl")
# Paired checks: (check job, raw files that must be byte-identical to native LRU).
PARITY = {
    "lru-repeat": RAW_FILES,
    "lru-callback": RAW_FILES,
    "lru-no-telemetry": RAW_FILES[:3],
}
OCCUPANCY_SERIES = (
    "physical_blocks",
    "active_blocks",
    "inactive_blocks",
    "free_blocks",
    "waiting_requests",
    "running_requests",
)


def require(condition, message: str) -> None:
    if not condition:
        raise ValueError(message)


def arrival_windows(directory: Path, window_ms: float) -> list[dict]:
    """Per arrival-time window: where in the recording policy differences arise."""
    rows = [
        json.loads(line)
        for line in (directory / "requests.jsonl").read_text().splitlines()
    ]
    scores = [
        json.loads(line)
        for line in (directory / "request-scores.jsonl").read_text().splitlines()
    ]
    windows = {}
    for row, score in zip(rows, scores, strict=True):
        require(
            row["request_id"] == score["request_id"], "Request scores are misaligned"
        )
        item = windows.setdefault(
            math.floor(row["arrival_time_ms"] / window_ms),
            {"requests": 0, "ttfts": [], "input": 0, "reused": 0, "passes": 0},
        )
        item["requests"] += 1
        item["passes"] += bool(score["meets_slo"])
        if score["ttft_ms"] is not None and row["terminal_status"] == "completed":
            item["ttfts"].append(score["ttft_ms"])
        item["input"] += row["input_length"]
        history = row["admission_history"]
        item["reused"] += history[0]["reused_input_tokens"] if history else 0
    return [
        {
            "window": index,
            "arrival_start_s": index * window_ms / 1000,
            "requests": item["requests"],
            "slo_passes": item["passes"],
            "mean_ttft_ms": sum(item["ttfts"]) / len(item["ttfts"])
            if item["ttfts"]
            else None,
            "token_hit_pct": 100 * item["reused"] / item["input"]
            if item["input"]
            else None,
        }
        for index, item in sorted(windows.items())
    ]


def cadence_error(coarse: dict, fine: dict) -> dict:
    """Absolute differences between the frozen and finer sampling cadences."""
    result = {}
    left, right = coarse["occupancy"], fine["occupancy"]
    for rank, summary in left["ranks"].items():
        for period in ("whole_run", "request_active"):
            for series in ("physical_blocks", "active_blocks"):
                a, b = summary[period][series], right["ranks"][rank][period][series]
                for key in (
                    "mean_pct",
                    "p95_pct",
                    "peak_sampled_pct",
                    "time_fraction_at_or_above_95pct",
                    "time_fraction_at_or_above_100pct",
                ):
                    if a[key] is not None and b[key] is not None:
                        # Express every statistic in percentage points.
                        scale = 1 if key.endswith("_pct") else 100
                        result[f"{rank}/{period}/{series}/{key}"] = {
                            "frozen_pct": a[key] * scale,
                            "finer_pct": b[key] * scale,
                            "absolute_difference_pp": abs(a[key] - b[key]) * scale,
                        }
    return {
        "frozen_interval_ms": left["sample_interval_ms"],
        "finer_interval_ms": right["sample_interval_ms"],
        "frozen_samples": left["samples"],
        "finer_samples": right["samples"],
        "max_absolute_difference_pp": max(
            (item["absolute_difference_pp"] for item in result.values()), default=None
        ),
        "differences": result,
    }


def compact(metrics: dict) -> dict:
    """Scores and occupancy for evidence; raw per-request data stays in the run."""
    result = {k: v for k, v in metrics.items() if k != "occupancy"}
    occupancy = metrics.get("occupancy")
    if occupancy:
        result["occupancy"] = {
            key: occupancy[key]
            for key in (
                "convention",
                "sample_interval_ms",
                "samples",
                "observation_ms",
                "request_active_ms",
                "preemptions",
                "closing_sample_kind",
            )
        }
        result["occupancy"]["ranks"] = {
            rank: {
                "capacity_blocks": summary["capacity_blocks"],
                **{
                    period: {
                        series: summary[period][series] for series in OCCUPANCY_SERIES
                    }
                    for period in ("whole_run", "request_active")
                },
            }
            for rank, summary in occupancy["ranks"].items()
        }
    return result


def check_job(run: Path, job: dict, identity: dict, slo: dict) -> dict:
    """Re-verify one job's inputs and recompute its metrics from raw files."""
    directory = run / job["trace"] / job["job"]
    name = f"{job['trace']}/{job['job']}"
    require(not (directory / "failure.json").exists(), f"Failed job: {name}")
    require((directory / "audit.json").is_file(), f"Incomplete job: {name}")
    require(
        read_json(directory / "manifest.json") == job_manifest(job, identity, slo),
        f"Job inputs changed: {name}",
    )
    receipt = audit(directory)
    require(receipt == read_json(directory / "audit.json"), f"Re-audit differs: {name}")
    metrics = read_json(directory / "metrics.json")
    require(
        math.isclose(
            metrics["goodput_rps"],
            metrics["slo_passes"] * 1000 / metrics["observation_duration_ms"],
            rel_tol=1e-12,
        ),
        f"Goodput arithmetic: {name}",
    )
    if "occupancy" in metrics:
        # Telemetry spans first arrival (time zero) to the last terminal event.
        require(
            math.isclose(
                metrics["occupancy"]["observation_ms"],
                metrics["observation_duration_ms"],
                rel_tol=1e-12,
            ),
            f"Occupancy span differs from the observation span: {name}",
        )
    files = {
        file: sha256(directory / file)
        for file in RAW_FILES
        if (directory / file).exists()
    }
    return {"metrics": metrics, "receipt": receipt, "raw_sha256": files}


def build(run: Path, output: Path, window_s: float = 600) -> dict:
    run, output = run.resolve(), output.resolve()
    require(not output.exists(), f"Evidence destination exists: {output}")
    manifest = read_json(run / "manifest.json")
    config = read_json(run / "config.json")
    inventory = read_json(run / "source-inventory.json")
    identity = manifest["identity"]
    require(
        sha256(run / "config.json") == identity["config_sha256"],
        "Run configuration changed",
    )
    require(
        sha256(run / "source-inventory.json") == identity["inventory_sha256"],
        "Run source inventory changed",
    )
    require(
        sha256(ROOT / "native/target/release/kv-eviction-replay")
        == identity["native_binary_sha256"],
        "Native binary differs from the one that produced this run",
    )
    table = policy_table(config)
    jobs = plan(config, inventory, run, materialize=False)
    checked = {}
    for job in jobs:
        print(f"Auditing {job['trace']}/{job['job']}", flush=True)
        checked[job["trace"], job["job"]] = check_job(run, job, identity, config["slo"])
    traces = {}
    for trace, item in inventory["traces"].items():
        summary = {
            key: item[key]
            for key in (
                "status",
                "cache_tokens",
                "capacity_blocks",
                "observed",
                "publisher_count_checks",
                "publisher_counts_match",
                "replayable_requests",
                "blocked_requests",
                "blocked_excluding_capacity",
                "cache_tokens_to_fit_every_request",
                "flag_counts",
                "blocker_counts",
                "flag_definitions",
                "by_model",
                "by_day",
                "by_file",
                "sessions_with_blocked_requests",
                "ledger",
                "files",
            )
        }
        summary["revision"], summary["license"] = item["revision"], item["license"]
        traces[trace] = summary
        if item["status"] != "ready":
            continue
        (recording,) = item["recordings"]
        policies = {name: checked[trace, name] for name in config["policies"]}
        for name, result in policies.items():
            metrics = result["metrics"]
            require(
                metrics["requests"]
                == recording["requests"]
                == item["replayable_requests"]
                == item["observed"]["requests"],
                f"Coverage mismatch: {trace}/{name}",
            )
            rows = (run / trace / name / "requests.jsonl").read_text().splitlines()
            require(
                sorted(int(json.loads(line)["request_id"]) for line in rows)
                == list(range(recording["requests"])),
                f"Request identities do not cover the recording: {trace}/{name}",
            )
        parity = {}
        for check, files in PARITY.items():
            reference = checked[trace, "lru"]["raw_sha256"]
            actual = checked[trace, check]["raw_sha256"]
            parity[check] = {file: actual[file] == reference[file] for file in files}
            require(
                all(parity[check].values()), f"Parity check failed: {trace}/{check}"
            )
        coarse = checked[trace, "cadence-check/telemetry"]
        fine = checked[trace, "cadence-check/fine-telemetry"]
        require(
            coarse["raw_sha256"]["tokens.jsonl"] == fine["raw_sha256"]["tokens.jsonl"],
            f"Cadence changed serving results: {trace}",
        )
        summary.update(
            recording={k: recording[k] for k in ("id", "sha256", "requests", "timing")},
            policies={
                name: compact(result["metrics"]) for name, result in policies.items()
            },
            parity=parity,
            cadence_check={
                "bounded_prefix_requests": config["telemetry"]["finer_check"][
                    "requests"
                ],
                **cadence_error(coarse["metrics"], fine["metrics"]),
            },
            arrival_windows={
                name: arrival_windows(run / trace / name, window_s * 1000)
                for name in config["policies"]
            },
        )
    previous_attempts = sorted(
        str(path.relative_to(run))
        for path in run.glob("*/**/*.attempt-*")
        if path.is_dir()
    )
    output.mkdir(parents=True)
    (output / "policies").mkdir()
    for name, policy in table.items():
        if policy["source"] is not None:
            shutil.copyfile(
                run / "policies" / f"{name}.py", output / "policies" / f"{name}.py"
            )
    shutil.copyfile(run / "config.json", output / "config.json")
    shutil.copyfile(run / "manifest.json", output / "run-manifest.json")
    write_json(output / "source-inventory.json", traces)
    write_json(
        output / "job-receipts.json",
        {
            f"{trace}/{job}": {
                "manifest": read_json(run / trace / job / "manifest.json"),
                "audit": result["receipt"],
                "raw_sha256": result["raw_sha256"],
            }
            for (trace, job), result in sorted(checked.items())
        },
    )
    results = {
        "schema_version": 1,
        "run": run.name,
        "audited_at": datetime.now(ZoneInfo("America/Chicago")).isoformat(),
        "settings": {
            "slo": config["slo"],
            "block_tokens": config["block_tokens"],
            "telemetry": config["telemetry"],
            "timing_profile": read_json(ROOT / config["timing_profile"]),
            "arrival_window_s": window_s,
        },
        "policies": {
            name: {k: v for k, v in policy.items() if k != "source"}
            for name, policy in table.items()
        },
        "traces": traces,
        "audited_jobs": len(checked),
        "previous_attempts": previous_attempts,
        "limitation": manifest["limitation"],
    }
    write_json(output / "results.json", results)
    files = sorted(path for path in output.rglob("*") if path.is_file())
    write_json(
        output / "COMPLETE.json",
        {str(path.relative_to(output)): sha256(path) for path in files},
    )
    return results
