#!/usr/bin/env python3
"""Full-trace heuristic baseline comparison. No agent calls or credentials.

fetch: download pinned releases, ledger every source request and normalize
       only traces whose every request the replay contract can represent.
"""

import argparse
import hashlib
import json
from pathlib import Path

from kv_experiment.inventory import build
from kv_experiment.traces import ROOT

INVENTORY = ROOT / ".cache/full-trace"


def read_config(path: Path) -> tuple[dict, str]:
    data = path.read_bytes()
    return json.loads(data), hashlib.sha256(data).hexdigest()


def fetch(args) -> None:
    config, config_sha256 = read_config(args.config)
    inventory = {"config_sha256": config_sha256, "traces": {}}
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    downloader = commands.add_parser("fetch", help="Download and inventory sources")
    downloader.add_argument("--config", type=Path, required=True)
    downloader.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    {"fetch": fetch}[args.command](args)


if __name__ == "__main__":
    main()
