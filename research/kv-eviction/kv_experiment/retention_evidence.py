"""Audit a completed retention sweep from raw files and package compact evidence.

Fails on a failed or incomplete job, changed inputs, incomplete coverage,
inconsistent arithmetic, a failed parity check or an unbounded-cache eviction.
Applies the frozen decision rule to the evaluation split. Never simulates.
"""

import json
import math
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from evidence.audit_run import audit

from .baseline_evidence import cadence_error, compact
from .comparison import job_manifest, read_json, write_json
from .inventory import sha256
from .retention import CHECK_LANES, CHECK_SPLIT, POLICY_ORDER, plan, policy_table
from .runtime import NATIVE

RAW = ("requests.jsonl", "token-summary.jsonl", "token-pooled.json", "evictions.json")
BOUNDS = {"oracle", "unbounded"}


def require(condition, message: str) -> None:
    if not condition:
        raise ValueError(message)


def check_job(run: Path, job: dict, identity: dict, slo: dict) -> dict:
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
        require(
            math.isclose(
                metrics["occupancy"]["observation_ms"],
                metrics["observation_duration_ms"],
                rel_tol=1e-12,
            ),
            f"Occupancy span differs from the observation span: {name}",
        )
    raw = {
        file: sha256(directory / file)
        for file in (*RAW, "occupancy.jsonl")
        if (directory / file).exists()
    }
    return {"metrics": metrics, "receipt": receipt, "raw_sha256": raw}


def same(left: dict, right: dict, files) -> dict:
    return {
        file: left["raw_sha256"][file] == right["raw_sha256"][file] for file in files
    }


def decide(results: dict, rule: dict, lanes: list[int]) -> dict:
    """The frozen rule, applied to every policy on the evaluation split."""
    metric = rule["metric"]
    levels = {}
    for lane in lanes:
        cell = results[rule["split"]][f"lanes-{lane}"]
        reference = cell["lru"][metric]
        reductions = {
            name: 100 * (1 - cell[name][metric] / reference)
            for name in POLICY_ORDER
            if name != "lru"
        }
        real = {k: v for k, v in reductions.items() if k not in BOUNDS}
        best = max(real, key=real.get)
        levels[lane] = {
            "lru_ms": reference,
            "reduction_pct": reductions,
            "best_real_policy": best,
            "best_real_reduction_pct": real[best],
            "real_meets_threshold": real[best] >= rule["real_policy_min_reduction_pct"],
            "oracle_meets_threshold": reductions["oracle"]
            >= rule["oracle_min_reduction_pct"],
        }
    real_levels = sum(item["real_meets_threshold"] for item in levels.values())
    oracle_levels = sum(item["oracle_meets_threshold"] for item in levels.values())
    proceed = max(real_levels, oracle_levels) >= rule["min_load_levels"]
    return {
        "rule": rule,
        "levels": levels,
        "real_policy_levels_meeting_threshold": real_levels,
        "oracle_levels_meeting_threshold": oracle_levels,
        "proceed_to_agent_search": proceed,
        "verdict": "headroom shown: proceed to agent policy search"
        if proceed
        else "limited demonstrated headroom for this retention interface",
    }


def build(run: Path, output: Path) -> dict:
    run, output = run.resolve(), output.resolve()
    require(not output.exists(), f"Evidence destination exists: {output}")
    manifest = read_json(run / "manifest.json")
    config = read_json(run / "config.json")
    inventory = read_json(run / "inventory.json")
    calibration = read_json(run / "reload-calibration.json")
    identity = manifest["identity"]
    require(
        sha256(run / "config.json") == identity["config_sha256"],
        "Configuration changed",
    )
    require(
        sha256(run / "inventory.json") == identity["inventory_sha256"],
        "Inventory changed",
    )
    require(
        sha256(run / "reload-calibration.json") == identity["calibration_sha256"],
        "Calibration changed",
    )
    require(
        sha256(NATIVE) == identity["native_binary_sha256"],
        "Native binary differs from the run",
    )
    jobs = plan(config, inventory, calibration, run, materialize=False)
    checked = {}
    for job in jobs:
        print(f"Auditing {job['trace']}/{job['job']}", flush=True)
        checked[f"{job['trace']}/{job['job']}"] = check_job(
            run, job, identity, config["slo"]
        )
    results, coverage = {}, {}
    for split, item in inventory["splits"].items():
        source = run / "inputs" / Path(item["path"]).name
        with source.open() as handle:
            next(handle)
            expected = sorted(json.loads(line)["request_id"] for line in handle)
        require(len(expected) == item["requests"], f"Split size changed: {split}")
        results[split] = {}
        for lane in config["lanes"]:
            cell = results[split][f"lanes-{lane}"] = {}
            for name in POLICY_ORDER:
                key = f"{split}/lanes-{lane}/{name}"
                rows = (run / key / "requests.jsonl").read_text().splitlines()
                require(
                    sorted(json.loads(r)["request_id"] for r in rows) == expected,
                    f"Coverage mismatch: {key}",
                )
                metrics = checked[key]["metrics"]
                if name == "unbounded":
                    require(
                        metrics["evictions"] == 0, f"Unbounded cache evicted: {key}"
                    )
                cell[name] = compact(metrics)
            coverage[f"{split}/lanes-{lane}"] = len(expected)
    check = f"{CHECK_SPLIT}/lanes-{CHECK_LANES}"
    parity = {
        "lru-repeat": same(
            checked[f"{check}/lru"],
            checked[f"{check}/check-lru-repeat"],
            (*RAW, "occupancy.jsonl"),
        ),
        "lru-no-telemetry": same(
            checked[f"{check}/lru"], checked[f"{check}/check-lru-no-telemetry"], RAW
        ),
        "lfu-native-vs-callback": same(
            checked["fixtures/parity/native-lfu"],
            checked["fixtures/parity/callback-lfu"],
            RAW,
        ),
        "last-gap-native-vs-callback": same(
            checked["fixtures/parity/native-last-gap"],
            checked["fixtures/parity/callback-last-gap"],
            RAW,
        ),
    }
    for name, files in parity.items():
        require(all(files.values()), f"Parity check failed: {name}")
    coarse, fine = (
        checked["fixtures/cadence/telemetry"],
        checked["fixtures/cadence/fine-telemetry"],
    )
    require(
        same(coarse, fine, RAW) == {f: True for f in RAW},
        "Telemetry cadence changed serving",
    )
    sampling = cadence_error(coarse["metrics"], fine["metrics"])
    decision = decide(results, config["decision_rule"], config["lanes"])
    previous = sorted(
        str(p.relative_to(run)) for p in run.glob("**/*.attempt-*") if p.is_dir()
    )
    output.mkdir(parents=True)
    (output / "policies").mkdir()
    for path in (run / "policies").glob("*.py"):
        shutil.copyfile(path, output / "policies" / path.name)
    for name in ("config.json", "inventory.json", "reload-calibration.json"):
        shutil.copyfile(run / name, output / name)
    shutil.copyfile(run / "manifest.json", output / "run-manifest.json")
    write_json(
        output / "job-receipts.json",
        {
            name: {
                "manifest": read_json(run / name / "manifest.json"),
                "audit": item["receipt"],
                "raw_sha256": item["raw_sha256"],
            }
            for name, item in sorted(checked.items())
        },
    )
    table = policy_table(config, calibration)
    summary = {
        "schema_version": 1,
        "run": run.name,
        "audited_at": datetime.now(ZoneInfo("America/Chicago")).isoformat(),
        "policies": {
            n: {"label": table[n][0], "note": table[n][2], "bound": n in BOUNDS}
            for n in POLICY_ORDER
        },
        "settings": {
            k: config[k]
            for k in (
                "served_model",
                "hardware",
                "block_tokens",
                "cache_blocks",
                "scheduler",
                "lanes",
                "slo",
                "split",
                "telemetry",
                "continuum",
                "oracle_slack_ms",
            )
        },
        "calibration": calibration,
        "inventory": {k: v for k, v in inventory.items() if k != "splits"}
        | {
            "splits": {
                s: {k: v for k, v in i.items() if k != "plays"}
                for s, i in inventory["splits"].items()
            }
        },
        "results": results,
        "coverage_requests": coverage,
        "parity": parity,
        "sampling_error": sampling,
        "decision": decision,
        "audited_jobs": len(checked),
        "previous_attempts": previous,
        "limitation": manifest["limitation"],
    }
    write_json(output / "results.json", summary)
    files = sorted(path for path in output.rglob("*") if path.is_file())
    write_json(
        output / "COMPLETE.json", {str(p.relative_to(output)): sha256(p) for p in files}
    )
    return summary
