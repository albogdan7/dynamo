"""Heuristic-only full-trace sweep: frozen inputs, one replay per trace and policy.

Every job starts from a cold cache and replays a complete recording with its
original timestamps. Paired checks replay native LRU again (determinism), as a
Python callback (callback/native parity) and without telemetry (instrumentation
parity). Each job is audited independently as soon as it finishes. Failures are
recorded beside the job and never replaced by another score.
"""

import json
import platform
import shutil
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from evidence.audit_run import audit

from .baselines import minimum
from .baselines import policies as heuristic_policies
from .inventory import sha256
from .runtime import IMAGE, NATIVE, replay
from .traces import ROOT

LRU_CALLBACK = minimum('block["lru_rank"]')
# Replay, scoring and audit code. Workloads, configurations and policy sources
# are pinned per job by hash; a resumed run must also match these files.
RUNTIME_FILES = [
    "kv_experiment/metrics.py",
    "kv_experiment/occupancy.py",
    "kv_experiment/policy_worker.py",
    "kv_experiment/runtime.py",
    "kv_experiment/summary_metrics.py",
    "evidence/audit_run.py",
    "native/core.patch",
    "native/src/main.rs",
    "native/Cargo.lock",
]


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def read_json(path: Path):
    return json.loads(path.read_text())


def policy_table(config: dict) -> dict:
    """Compared policies, then paired checks; a None source means native LRU."""
    heuristics = heuristic_policies()
    table = {}
    for name in config["policies"]:
        if name == "lru":
            table[name] = {
                "label": "LRU",
                "source": None,
                "note": "Native inactive-queue head: the least recently released eligible block.",
            }
        else:
            table[name] = {
                key: heuristics[name][key] for key in ("label", "source", "note")
            }
        table[name].update(kind="policy", telemetry=True)
    checks = {
        "lru-repeat": (
            None,
            True,
            "Native LRU replayed again; outputs must be identical.",
        ),
        "lru-callback": (
            LRU_CALLBACK,
            True,
            "LRU as a Python callback in the policy container; serving outputs must equal native LRU.",
        ),
        "lru-no-telemetry": (
            None,
            False,
            "Native LRU without telemetry; serving outputs must equal the instrumented run.",
        ),
    }
    for name, (source, telemetry, note) in checks.items():
        table[name] = {
            "label": name,
            "source": source,
            "note": note,
            "kind": "check",
            "telemetry": telemetry,
        }
    return table


def command_output(command: list[str]) -> str | None:
    try:
        return subprocess.run(
            command, capture_output=True, text=True, check=True, timeout=30
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def identity(config_sha256: str, inventory_sha256: str) -> dict:
    """Everything a completed job's numbers depend on."""
    return {
        "config_sha256": config_sha256,
        "inventory_sha256": inventory_sha256,
        "native_binary_sha256": sha256(NATIVE),
        "policy_container": IMAGE,
        "runtime_sha256": {name: sha256(ROOT / name) for name in RUNTIME_FILES},
    }


def replay_config(
    config: dict, trace: str, recording: dict, telemetry_ms: float | None
) -> dict:
    profile = read_json(ROOT / config["timing_profile"])
    if profile["rank"]["block_size"] != config["block_tokens"]:
        raise ValueError("Timing profile block size differs from the frozen block size")
    profile["rank"]["num_gpu_blocks"] = (
        config["traces"][trace]["cache_tokens"] // config["block_tokens"]
    )
    profile["trace"] = {
        "format": recording["format"],
        "source_block_size": recording["source_block_size"],
    }
    if telemetry_ms:
        profile["telemetry"] = {"sample_interval_ms": telemetry_ms}
    return profile


def freeze(path: Path, content: str, materialize: bool) -> Path:
    """Write a frozen run input once; afterwards it must never change."""
    if path.exists():
        if path.read_text() != content:
            raise ValueError(
                f"Frozen run input differs from this configuration: {path}"
            )
    elif materialize:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    else:
        raise ValueError(f"Missing frozen run input: {path}")
    return path


def plan(
    config: dict, inventory: dict, output: Path, materialize: bool = True
) -> list[dict]:
    """Freeze (or, for an audit, only verify) inputs; return jobs, callbacks first."""
    jobs = []
    table = policy_table(config)
    for name, policy in table.items():
        if policy["source"] is not None:
            freeze(output / "policies" / f"{name}.py", policy["source"], materialize)
    for trace in config["traces"]:
        item = inventory["traces"][trace]
        if item["status"] != "ready":
            continue
        if len(item["recordings"]) != 1:
            raise ValueError(
                "Aggregating several independent recordings is not implemented"
            )
        (recording,) = item["recordings"]
        inputs = output / "inputs" / trace
        workload = inputs / "full.jsonl"
        if materialize and not workload.exists():
            inputs.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / recording["path"], workload)
        if sha256(workload) != recording["sha256"]:
            raise ValueError(f"Normalized workload changed: {trace}")
        cadence = config["telemetry"]
        variants = {
            "telemetry": cadence["sample_interval_ms"],
            "no-telemetry": None,
            "fine-telemetry": cadence["finer_check"]["sample_interval_ms"],
        }
        configs = {
            variant: freeze(
                output / "replay-configs" / f"{trace}-{variant}.json",
                json.dumps(
                    replay_config(config, trace, recording, interval),
                    indent=2,
                    allow_nan=False,
                )
                + "\n",
                materialize,
            )
            for variant, interval in variants.items()
        }
        for name, policy in table.items():
            jobs.append(
                {
                    "trace": trace,
                    "job": name,
                    "workload": workload,
                    "config": configs[
                        "telemetry" if policy["telemetry"] else "no-telemetry"
                    ],
                    "policy": None
                    if policy["source"] is None
                    else output / "policies" / f"{name}.py",
                }
            )
        # Quantify sampling error on a bounded prefix with a finer cadence.
        lines = workload.read_text().splitlines(keepends=True)
        fixture = freeze(
            inputs / "cadence-fixture.jsonl",
            "".join(lines[: cadence["finer_check"]["requests"]]),
            materialize,
        )
        for variant in ("telemetry", "fine-telemetry"):
            jobs.append(
                {
                    "trace": trace,
                    "job": f"cadence-check/{variant}",
                    "workload": fixture,
                    "config": configs[variant],
                    "policy": None,
                }
            )
    return sorted(jobs, key=lambda job: job["policy"] is None)


def job_manifest(job: dict, run_identity: dict, slo: dict) -> dict:
    return {
        "trace": job["trace"],
        "job": job["job"],
        "workload_sha256": sha256(job["workload"]),
        "replay_config_sha256": sha256(job["config"]),
        "policy_sha256": None if job["policy"] is None else sha256(job["policy"]),
        "policy_mode": "native-lru" if job["policy"] is None else "container-callback",
        "slo": slo,
        **run_identity,
    }


def execute(job: dict, output: Path, run_identity: dict, config: dict) -> dict:
    directory = output / job["trace"] / job["job"]
    expected = job_manifest(job, run_identity, config["slo"])
    if (directory / "audit.json").is_file():
        if read_json(directory / "manifest.json") == expected:
            return {"status": "skipped", "seconds": 0}
        raise ValueError(
            f"Completed job {directory} has different inputs; use a fresh run"
        )
    if directory.exists():
        # Preserve incomplete or failed attempts; never overwrite raw data.
        attempt = 1
        while directory.with_name(f"{directory.name}.attempt-{attempt}").exists():
            attempt += 1
        directory.rename(directory.with_name(f"{directory.name}.attempt-{attempt}"))
    directory.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        replay(
            job["workload"],
            job["config"],
            directory,
            job["policy"],
            config["slo"]["ttft_ms"],
            config["slo"]["per_request_p95_itl_ms"],
            timeout=config["job_timeout_seconds"],
        )
        if job["policy"] is not None:
            shutil.copyfile(job["policy"], directory / "policy.py")
        write_json(directory / "manifest.json", expected)
        write_json(directory / "audit.json", audit(directory))
        return {"status": "completed", "seconds": time.monotonic() - started}
    except (
        Exception
    ) as error:  # noqa: BLE001 - every failure is preserved, none is fatal to other jobs
        directory.mkdir(parents=True, exist_ok=True)
        if job["policy"] is not None:
            shutil.copyfile(job["policy"], directory / "policy.py")
        write_json(directory / "manifest.json", expected)
        failure = {"error_type": type(error).__name__, "error": str(error)}
        write_json(directory / "failure.json", failure)
        return {"status": "failed", "seconds": time.monotonic() - started, **failure}


def run(config_path: Path, inventory_path: Path, output: Path, workers: int) -> dict:
    config_sha256, inventory_sha256 = sha256(config_path), sha256(inventory_path)
    config, inventory = read_json(config_path), read_json(inventory_path)
    capacities = {
        trace: item["cache_tokens"] for trace, item in inventory["traces"].items()
    }
    if capacities != {t: v["cache_tokens"] for t, v in config["traces"].items()}:
        raise ValueError(
            "Inventory traces or capacities differ from the configuration; rerun fetch"
        )
    if not NATIVE.exists():
        raise FileNotFoundError(
            "Run python3 research/kv-eviction/prepare_native.py first"
        )
    run_identity = identity(config_sha256, inventory_sha256)
    manifest_path = output / "manifest.json"
    if manifest_path.exists():
        if read_json(manifest_path)["identity"] != run_identity:
            raise ValueError(
                "Existing run has different inputs, code or binary; use a fresh directory"
            )
        print(f"Resuming {output}", flush=True)
    else:
        output.mkdir(parents=True)
        shutil.copyfile(config_path, output / "config.json")
        shutil.copyfile(inventory_path, output / "source-inventory.json")
        repository = ROOT.parents[1]
        write_json(
            manifest_path,
            {
                "schema_version": 1,
                "created_at": datetime.now(ZoneInfo("America/Chicago")).isoformat(),
                "command": [sys.executable, *sys.argv],
                "identity": run_identity,
                "git_commit": command_output(
                    ["git", "-C", str(repository), "rev-parse", "HEAD"]
                ),
                "git_status": command_output(
                    [
                        "git",
                        "-C",
                        str(repository),
                        "status",
                        "--porcelain",
                        "--",
                        str(ROOT),
                    ]
                ),
                "python": sys.version,
                "platform": platform.platform(),
                "cargo": command_output(["cargo", "--version"]),
                "docker": command_output(
                    ["docker", "version", "--format", "{{.Server.Version}}"]
                ),
                "policies": policy_table(config),
                "blocked_traces": {
                    trace: {
                        "blocker_counts": item["blocker_counts"],
                        "blocked_requests": item["blocked_requests"],
                        "source_requests": item["observed"]["requests"],
                    }
                    for trace, item in inventory["traces"].items()
                    if item["status"] != "ready"
                },
                "limitation": "Uncalibrated native polynomial timing on one simulated worker; not measured GPU latency.",
            },
        )
    jobs = plan(config, inventory, output)
    results = {}
    # Separate processes: one job's scoring must not starve another's policy deadline.
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                execute, job, output, run_identity, config
            ): f"{job['trace']}/{job['job']}"
            for job in jobs
        }
        for done, future in enumerate(as_completed(futures), 1):
            name = futures[future]
            results[name] = future.result()
            print(
                f"[{done}/{len(jobs)}] {results[name]['status']:9} {name} "
                f"({results[name]['seconds']:.0f}s)",
                flush=True,
            )
    status = {"jobs": dict(sorted(results.items()))}
    status["failed"] = sorted(
        name for name, item in results.items() if item["status"] == "failed"
    )
    write_json(output / "run-status.json", status)
    return status
