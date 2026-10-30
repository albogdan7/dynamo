#!/usr/bin/env python3
"""Agentic KV retention experiment (AGENTIC_RETENTION_SPEC.md). No agent calls.

validate-cache-sim: compare the timing-free cache model with Mooncake Table 1.
prepare:            import every AgentX play once, split plays and inventory them.
check-regression:   replay full Mooncake LRU with this engine; compare with PR #8.
"""

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

from kv_experiment.agentic import OUTPUT, prepare
from kv_experiment.cachesim import validate
from kv_experiment.comparison import replay_config
from kv_experiment.inventory import fetch
from kv_experiment.runtime import NATIVE
from kv_experiment.traces import ROOT


def validate_cache_sim(args) -> None:
    (trace,) = fetch("mooncake-toolagent")
    result = validate(trace)
    output = args.output.resolve()
    output.relative_to(ROOT)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")
    capacities = [
        "inf" if c is None else f"{c:,}" for c in result["published"]["capacities"]
    ]
    print(
        f"{'blocks':>8} " + " ".join(f"{p:>21}" for p in ("lru", "lfu", "length_aware"))
    )
    for index, capacity in enumerate(capacities):
        cells = (
            f"{result['simulated'][p][index]:.3f} (pub {result['published'][p][index]:.2f})"
            for p in ("lru", "lfu", "length_aware")
        )
        print(f"{capacity:>8} " + " ".join(f"{c:>21}" for c in cells))
    print(
        f"Max |simulated - published| = {result['max_absolute_difference']:.3f}; saved {output}"
    )


def prepare_inputs(args) -> None:
    inventory = prepare(json.loads(args.config.read_text()))
    for split, item in inventory["splits"].items():
        print(
            f"{split:10} {len(item['plays']):3} plays {item['requests']:6,} requests "
            f"{item['zero_output_requests']:2} zero-output, "
            f"{len(item['non_chain_sessions'])} non-chain sessions"
        )
    print(f"Saved {OUTPUT / 'inventory.json'}")


def check_regression(args) -> None:
    """Native LRU must reproduce the audited full-trace LRU raw outputs exactly."""
    evidence = ROOT / "evidence/full-trace-baselines"
    config = json.loads((evidence / "config.json").read_text())
    receipts = json.loads((evidence / "job-receipts.json").read_text())
    inventory = json.loads((ROOT / ".cache/full-trace/inventory.json").read_text())
    report = {}
    for trace in ("mooncake-toolagent", "mooncake-conversation"):
        (recording,) = inventory["traces"][trace]["recordings"]
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            settings = replay_config(
                config, trace, recording, config["telemetry"]["sample_interval_ms"]
            )
            (work / "config.json").write_text(json.dumps(settings))
            (work / "run").mkdir()
            subprocess.run(
                [
                    str(NATIVE),
                    str(ROOT / recording["path"]),
                    str(work / "config.json"),
                    str(work / "run"),
                    "native-lru",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            expected = receipts[f"{trace}/lru"]["raw_sha256"]
            report[trace] = {
                name: hashlib.sha256((work / "run" / name).read_bytes()).hexdigest()
                == expected[name]
                for name in ("requests.jsonl", "tokens.jsonl", "occupancy.jsonl")
            }
        print(trace, report[trace])
    if not all(all(item.values()) for item in report.values()):
        raise SystemExit("LRU outputs changed relative to the audited full-trace sweep")
    print("LRU outputs are byte-identical to the audited full-trace sweep")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    cache = commands.add_parser("validate-cache-sim", help="Reproduce Mooncake Table 1")
    cache.add_argument(
        "--output",
        type=Path,
        default=ROOT / "evidence/cache-sim-validation.json",
    )
    prepare_parser = commands.add_parser("prepare", help="Import and split AgentX")
    prepare_parser.add_argument("--config", type=Path, required=True)
    commands.add_parser("check-regression", help="Compare LRU with PR #8's receipts")
    args = parser.parse_args()
    {
        "validate-cache-sim": validate_cache_sim,
        "prepare": prepare_inputs,
        "check-regression": check_regression,
    }[args.command](args)


if __name__ == "__main__":
    main()
