#!/usr/bin/env python3
"""Agentic KV retention experiment (AGENTIC_RETENTION_SPEC.md). No agent calls.

validate-cache-sim: compare the timing-free cache model with Mooncake Table 1.
"""

import argparse
import json
from pathlib import Path

from kv_experiment.cachesim import validate
from kv_experiment.inventory import fetch
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    cache = commands.add_parser("validate-cache-sim", help="Reproduce Mooncake Table 1")
    cache.add_argument(
        "--output",
        type=Path,
        default=ROOT / "evidence/cache-sim-validation.json",
    )
    args = parser.parse_args()
    {"validate-cache-sim": validate_cache_sim}[args.command](args)


if __name__ == "__main__":
    main()
