"""Standalone pinned baseline reproduction and Mooncake download commands."""

import argparse
from pathlib import Path

from kv_experiment.legacy import reproduce
from kv_experiment.traces import ROOT, fetch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("fetch-trace")
    command = commands.add_parser("reproduce")
    command.add_argument(
        "--output", type=Path, default=Path("experiments/reproduction")
    )
    args = parser.parse_args()
    if args.command == "fetch-trace":
        print(fetch())
    else:
        output = (
            args.output.resolve()
            if args.output.is_absolute()
            else (ROOT / args.output).resolve()
        )
        output.relative_to(ROOT)
        reproduce(output)


if __name__ == "__main__":
    main()
