"""Run the four frozen workloads with both real agents and fixed heuristics."""

import argparse
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKLOADS = {
    "mooncake-toolagent": [
        "--window-requests",
        "256",
        "--arrival-scale",
        "10",
        "--cache-tokens",
        "131072",
    ],
    "mooncake-conversation": [
        "--window-requests",
        "256",
        "--arrival-scale",
        "10",
        "--cache-tokens",
        "131072",
    ],
    "agentx": ["--sessions-per-split", "2", "--cache-tokens", "196608"],
    "freeinference": ["--sessions-per-split", "3", "--cache-tokens", "81920"],
}
MODELS = {"claude": "claude-sonnet-5-5", "codex": "gpt-6.1-sol"}


def run_one(output, trace, agent):
    name = f"{trace}-{agent}"
    command = [
        sys.executable,
        str(ROOT / "experiment.py"),
        "run",
        "--agent",
        agent,
        "--model",
        MODELS[agent],
        "--trace",
        trace,
        *WORKLOADS[trace],
        "--ttft-slo-ms",
        "1000",
        "--itl-slo-ms",
        "50",
        "--submissions",
        "5",
        "--output",
        str(output / name),
    ]
    print(f"Starting {name}; log {output / (name + '.log')}", flush=True)
    with (output / f"{name}.log").open("w") as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    print(f"Completed {name}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Fresh directory beneath research/kv-eviction",
    )
    parser.add_argument("--jobs", type=int, default=2, choices=range(1, 5))
    args = parser.parse_args()
    output = args.output.resolve() if args.output.is_absolute() else ROOT / args.output
    output = output.resolve()
    output.relative_to(ROOT)
    output.mkdir(parents=True, exist_ok=False)
    errors = []
    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
        jobs = {
            executor.submit(run_one, output, trace, agent): (trace, agent)
            for trace in WORKLOADS
            for agent in MODELS
        }
        for job in as_completed(jobs):
            try:
                job.result()
            except subprocess.CalledProcessError as error:
                errors.append(jobs[job])
                print(f"Failed {jobs[job]}: {error}; inspect its log", flush=True)
    if errors:
        raise RuntimeError(f"Failed experiments preserved: {errors}")


if __name__ == "__main__":
    main()
