"""Agentic retention sweep (AGENTIC_RETENTION_SPEC.md): plan, calibrate and run.

Every job replays one AgentX split from a cold cache with C closed-loop lanes,
one policy and the frozen engine settings. Jobs reuse the full-trace sweep's
executor: separate processes, an independent audit per job, failures kept
without scores and resume only on identical hashes.
"""

import json
import platform
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .agentic import OUTPUT, read_trace
from .baselines import LAST_GAP_RETENTION, policies
from .comparison import command_output, execute, identity, read_json, write_json
from .inventory import sha256
from .runtime import NATIVE
from .traces import ROOT

CHECK_SPLIT, CHECK_LANES = "evaluation", 32
POLICY_ORDER = (
    "lru",
    "lfu",
    "slru",
    "random-0",
    "last-gap",
    "global-median",
    "continuum-ttl",
    "oracle",
    "unbounded",
)


def policy_table(config: dict, calibration: dict) -> dict:
    """Frozen policies; `research` is the native configuration (None = engine LRU)."""
    continuum = {
        "policy": "continuum",
        "reload_quadratic": calibration["quadratic"],
        "min_history": config["continuum"]["min_history"],
        "cold_mean_ms": config["continuum"]["cold_mean_ms"],
    }
    return {
        "lru": ("LRU", None, "Native engine LRU; never protects."),
        "lfu": ("LFU", {"order": "lfu"}, "Fewest physical-copy accesses, then LRU."),
        "slru": (
            "SLRU",
            {"order": "slru"},
            "Copies used at least twice evicted after others; LRU within tiers.",
        ),
        "random-0": (
            "Random 0",
            {"order": "random", "seed": 0},
            "Uniform among eligible blocks, seed 0.",
        ),
        "last-gap": (
            "Last gap",
            {"retention": {"policy": "last_gap"}},
            "Protect until now + this session's last observed pause.",
        ),
        "global-median": (
            "Global median",
            {"retention": {"policy": "global_median"}},
            "Protect until now + median observed pause (log-binned).",
        ),
        "continuum-ttl": (
            "Continuum TTL",
            {"retention": continuum},
            "argmax_t P(pause<=t)*reload(context) - t over observed pauses; retention only, soft protection.",
        ),
        "oracle": (
            "Oracle (bound)",
            {"retention": {"policy": "oracle", "slack_ms": config["oracle_slack_ms"]}},
            "Protect until the recorded next arrival plus slack; uses future data.",
        ),
        "unbounded": (
            "Unbounded cache (bound)",
            None,
            "LRU with enough capacity that nothing is evicted.",
        ),
    }


def replay_config(
    config: dict, lanes: int, cache_blocks: int, research, telemetry_ms
) -> dict:
    profile = read_json(ROOT / config["timing_profile"])
    if profile["rank"]["block_size"] != config["block_tokens"]:
        raise ValueError("Timing profile block size differs from the frozen block size")
    profile["rank"].update(num_gpu_blocks=cache_blocks, **config["scheduler"])
    profile["trace"] = {
        "format": "agentic_mooncake",
        "source_block_size": 64,
        "agentic_lanes": lanes,
    }
    profile["token_capture"] = "summary"
    if telemetry_ms:
        profile["telemetry"] = {"sample_interval_ms": telemetry_ms}
    if research is not None:
        profile["research_policy"] = research
    return profile


def calibrate_reload(config: dict) -> dict:
    """Offline prefill time for one cold request of n tokens; quadratic fit."""
    sizes = [4_096, 16_384, 65_536, 262_144, 524_288, 917_504]
    times = []
    for size in sizes:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            row = {
                "timestamp": 0,
                "input_length": size,
                "output_length": 1,
                "hash_ids": list(range(1, size // config["block_tokens"] + 1)),
            }
            (work / "trace.jsonl").write_text(json.dumps(row) + "\n")
            profile = replay_config(config, 1, config["cache_blocks"], None, None)
            profile["trace"] = {
                "format": "mooncake",
                "source_block_size": config["block_tokens"],
            }
            (work / "config.json").write_text(json.dumps(profile))
            (work / "run").mkdir()
            subprocess.run(
                [
                    str(NATIVE),
                    str(work / "trace.jsonl"),
                    str(work / "config.json"),
                    str(work / "run"),
                    "native-lru",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            (request,) = map(
                json.loads, (work / "run/requests.jsonl").read_text().splitlines()
            )
            times.append(request["ttft_ms"])
    # Least squares for a*n^2 + b*n + c via the 3x3 normal equations.
    rows = [[n * n, n, 1.0] for n in sizes]
    normal = [[sum(r[i] * r[j] for r in rows) for j in range(3)] for i in range(3)]
    target = [sum(r[i] * t for r, t in zip(rows, times, strict=True)) for i in range(3)]
    coefficients = solve(normal, target)
    fitted = [sum(c * x for c, x in zip(coefficients, r, strict=True)) for r in rows]
    return {
        "method": "single cold request per size, TTFT under the frozen engine settings",
        "sizes_tokens": sizes,
        "prefill_ms": times,
        "quadratic": coefficients,
        "max_relative_residual": max(
            abs(f - t) / t for f, t in zip(fitted, times, strict=True)
        ),
    }


def solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting (3x3)."""
    a = [row[:] + [value] for row, value in zip(matrix, vector, strict=True)]
    n = len(a)
    for column in range(n):
        pivot = max(range(column, n), key=lambda r: abs(a[r][column]))
        a[column], a[pivot] = a[pivot], a[column]
        for row in range(column + 1, n):
            factor = a[row][column] / a[column][column]
            a[row] = [x - factor * y for x, y in zip(a[row], a[column], strict=True)]
    result = [0.0] * n
    for row in reversed(range(n)):
        result[row] = (
            a[row][n] - sum(a[row][k] * result[k] for k in range(row + 1, n))
        ) / a[row][row]
    return result


def freeze(path: Path, content: str, materialize: bool) -> Path:
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
    config: dict,
    inventory: dict,
    calibration: dict,
    output: Path,
    materialize: bool = True,
) -> list[dict]:
    """Freeze inputs (or verify them for an audit) and return every job."""
    table = policy_table(config, calibration)
    inputs = output / "inputs"
    workloads, oracles = {}, {}
    for split, item in inventory["splits"].items():
        for kind, key, store in (
            ("path", "sha256", workloads),
            ("oracle_path", "oracle_sha256", oracles),
        ):
            source = ROOT / item[kind]
            destination = inputs / Path(item[kind]).name
            if materialize and not destination.exists():
                inputs.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
            if sha256(destination) != item[key]:
                raise ValueError(f"Frozen input changed: {destination}")
            store[split] = destination
    configs = output / "replay-configs"

    def frozen_config(name, lanes, blocks, research, telemetry_ms):
        content = (
            json.dumps(
                replay_config(config, lanes, blocks, research, telemetry_ms),
                indent=2,
                allow_nan=False,
            )
            + "\n"
        )
        return freeze(configs / f"{name}.json", content, materialize)

    jobs = []
    telemetry = config["telemetry"]["sample_interval_ms"]
    for split in ("tuning", "evaluation"):
        for lanes in config["lanes"]:
            for name in POLICY_ORDER:
                _, research, _ = table[name]
                if name == "oracle":
                    research = {
                        "retention": {
                            **research["retention"],
                            "oracle_gaps": str(oracles[split]),
                        }
                    }
                blocks = (
                    inventory["splits"][split]["unbounded_cache_blocks"]
                    if name == "unbounded"
                    else config["cache_blocks"]
                )
                jobs.append(
                    {
                        "trace": f"{split}/lanes-{lanes}",
                        "job": name,
                        "workload": workloads[split],
                        "config": frozen_config(
                            f"{split}-lanes-{lanes}-{name}",
                            lanes,
                            blocks,
                            research,
                            telemetry,
                        ),
                        "policy": None,
                    }
                )
    # Paired checks on the evaluation split at C = 32.
    base = {
        "trace": f"{CHECK_SPLIT}/lanes-{CHECK_LANES}",
        "workload": workloads[CHECK_SPLIT],
        "policy": None,
    }
    jobs.append(
        {
            **base,
            "job": "check-lru-repeat",
            "config": configs / f"{CHECK_SPLIT}-lanes-{CHECK_LANES}-lru.json",
        }
    )
    jobs.append(
        {
            **base,
            "job": "check-lru-no-telemetry",
            "config": frozen_config(
                f"{CHECK_SPLIT}-lanes-{CHECK_LANES}-lru-no-telemetry",
                CHECK_LANES,
                config["cache_blocks"],
                None,
                None,
            ),
        }
    )
    # Bounded fixtures: callback parity (Docker) and a finer telemetry cadence.
    header, rows = read_trace(workloads["tuning"])
    plays = list(dict.fromkeys(row["play_id"] for row in rows))
    fixtures = {
        "parity": config["parity_fixture"]["plays"],
        "cadence": config["telemetry"]["finer_check"]["plays"],
    }
    for name, count in fixtures.items():
        selected = set(plays[:count])
        content = (
            json.dumps(header)
            + "\n"
            + "".join(json.dumps(r) + "\n" for r in rows if r["play_id"] in selected)
        )
        workloads[name] = freeze(inputs / f"{name}-fixture.jsonl", content, materialize)
    parity_lanes = config["parity_fixture"]["lanes"]
    sources = {
        "lfu": policies()["lfu"]["source"],
        "last-gap": LAST_GAP_RETENTION,
    }
    for name, source in sources.items():
        sources[name] = freeze(output / "policies" / f"{name}.py", source, materialize)
    parity = {"trace": "fixtures/parity", "workload": workloads["parity"]}
    jobs += [
        {
            **parity,
            "job": "native-lfu",
            "policy": None,
            "config": frozen_config(
                "parity-native-lfu",
                parity_lanes,
                config["cache_blocks"],
                {"order": "lfu"},
                telemetry,
            ),
        },
        {
            **parity,
            "job": "callback-lfu",
            "policy": sources["lfu"],
            "config": frozen_config(
                "parity-lru-profile",
                parity_lanes,
                config["cache_blocks"],
                None,
                telemetry,
            ),
        },
        {
            **parity,
            "job": "native-last-gap",
            "policy": None,
            "config": frozen_config(
                "parity-native-last-gap",
                parity_lanes,
                config["cache_blocks"],
                {"retention": {"policy": "last_gap"}},
                telemetry,
            ),
        },
        {
            **parity,
            "job": "callback-last-gap",
            "policy": sources["last-gap"],
            "config": frozen_config(
                "parity-callback-last-gap",
                parity_lanes,
                config["cache_blocks"],
                {"retention": {"policy": "callback"}},
                telemetry,
            ),
        },
    ]
    finer = config["telemetry"]["finer_check"]
    cadence = {
        "trace": "fixtures/cadence",
        "workload": workloads["cadence"],
        "policy": None,
    }
    jobs += [
        {
            **cadence,
            "job": "telemetry",
            "config": frozen_config(
                "cadence-telemetry",
                finer["lanes"],
                config["cache_blocks"],
                None,
                telemetry,
            ),
        },
        {
            **cadence,
            "job": "fine-telemetry",
            "config": frozen_config(
                "cadence-fine-telemetry",
                finer["lanes"],
                config["cache_blocks"],
                None,
                finer["sample_interval_ms"],
            ),
        },
    ]
    # Callback jobs are slowest (Docker round trips); start them first.
    return sorted(jobs, key=lambda job: job["policy"] is None)


def run(
    config_path: Path,
    output: Path,
    workers: int,
    inventory_path: Path = OUTPUT / "inventory.json",
) -> dict:
    config = read_json(config_path)
    calibration_path = ROOT / config["continuum"]["reload_calibration"]
    calibration, inventory = read_json(calibration_path), read_json(inventory_path)
    if not NATIVE.exists():
        raise FileNotFoundError(
            "Run python3 research/kv-eviction/prepare_native.py first"
        )
    run_identity = identity(sha256(config_path), sha256(inventory_path))
    run_identity["calibration_sha256"] = sha256(calibration_path)
    manifest_path = output / "manifest.json"
    if manifest_path.exists():
        if read_json(manifest_path)["identity"] != run_identity:
            raise ValueError(
                "Existing run has different inputs, code or binary; use a fresh directory"
            )
        print(f"Resuming {output}", flush=True)
    else:
        output.mkdir(parents=True)
        for source, name in (
            (config_path, "config.json"),
            (inventory_path, "inventory.json"),
            (calibration_path, "reload-calibration.json"),
        ):
            shutil.copyfile(source, output / name)
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
                "docker": command_output(
                    ["docker", "version", "--format", "{{.Server.Version}}"]
                ),
                "policies": {
                    name: {"label": label, "research": research, "note": note}
                    for name, (label, research, note) in policy_table(
                        config, calibration
                    ).items()
                },
                "limitation": "Uncalibrated native polynomial timing on one simulated worker; soft retention; not measured GPU latency.",
            },
        )
    jobs = plan(config, inventory, calibration, output)
    results = {}
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
                f"[{done}/{len(jobs)}] {results[name]['status']:9} {name} ({results[name]['seconds']:.0f}s)",
                flush=True,
            )
    status = {"jobs": dict(sorted(results.items()))}
    status["failed"] = sorted(
        name for name, item in results.items() if item["status"] == "failed"
    )
    write_json(output / "run-status.json", status)
    return status
