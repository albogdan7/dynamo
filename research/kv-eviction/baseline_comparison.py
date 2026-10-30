#!/usr/bin/env python3
"""Full-trace heuristic baseline comparison. No agent calls or credentials.

fetch: download pinned releases, ledger every source request and normalize
       only traces whose every request the replay contract can represent.
run:   replay every ready trace once per policy, plus paired LRU checks, from
       cold state with original timestamps; resumable and never overwriting.
"""

import argparse
import json
from pathlib import Path

from kv_experiment.comparison import run as run_sweep
from kv_experiment.inventory import build
from kv_experiment.traces import ROOT

INVENTORY = ROOT / ".cache/full-trace"


def fetch(args) -> None:
    config = json.loads(args.config.read_text())
    inventory = {"traces": {}}
    for trace, settings in config["traces"].items():
        print(f"Inventorying {trace}", flush=True)
        inventory["traces"][trace] = build(
            trace, settings["cache_tokens"], INVENTORY / trace, args.workers
        )
    (INVENTORY / "inventory.json").write_text(json.dumps(inventory, indent=1) + "\n")
    print(f"\n{'trace':24} {'status':8} {'requests':>10} {'blocked':>10}  blockers")
    for trace, item in inventory["traces"].items():
        blockers = ", ".join(f"{k}={v:,}" for k, v in item["blocker_counts"].items())
        print(
            f"{trace:24} {item['status']:8} {item['observed']['requests']:>10,} "
            f"{item['blocked_requests']:>10,}  {blockers or '-'}"
        )
    print(f"\nSaved {INVENTORY / 'inventory.json'}")


def run(args) -> None:
    output = args.output.resolve()
    output.relative_to(ROOT / "experiments")
    status = run_sweep(args.config, INVENTORY / "inventory.json", output, args.jobs)
    if status["failed"]:
        raise SystemExit(f"Failed jobs preserved in {output}: {status['failed']}")
    print(f"Completed {output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    downloader = commands.add_parser("fetch", help="Download and inventory sources")
    downloader.add_argument("--config", type=Path, required=True)
    downloader.add_argument("--workers", type=int, default=8)
    runner = commands.add_parser("run", help="Replay every policy on ready traces")
    runner.add_argument("--config", type=Path, required=True)
    runner.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Fresh experiments/<date> directory; an existing one is resumed",
    )
    runner.add_argument("--jobs", type=int, default=8, help="Parallel replays")
    args = parser.parse_args()
    {"fetch": fetch, "run": run}[args.command](args)


if __name__ == "__main__":
    main()
