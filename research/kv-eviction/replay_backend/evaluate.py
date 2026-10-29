"""Evaluate one trusted policy using real AISimulate replay and save its record."""

import argparse
import datetime
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=ROOT / "policies" / "lru.py")
    parser.add_argument("--native-lru", action="store_true")
    parser.add_argument("--spec", type=Path, default=ROOT / "fixtures" / "pressure.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    binary = ROOT / "target" / "debug" / "kv-policy-replay"
    if not binary.exists():
        parser.error("Build first: python3 prepare_core.py && cargo build --locked")
    command = [str(binary), str(args.spec), "-" if args.native_lru else str(args.policy), sys.executable]
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, start_new_session=True) as process:
        try:
            stdout, stderr = process.communicate(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            parser.exit(1, "Replay timed out; no score saved.\n")
    if process.returncode:
        parser.exit(1, f"Replay failed; no score saved.\n{stderr}")
    record = json.loads(stdout)
    output = args.output or ROOT / "outputs" / (datetime.datetime.now(datetime.timezone.utc).strftime("run_%Y%m%d_%H%M%S_%f.json"))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=output.parent, delete=False) as temporary:
        temporary.write(stdout)
        temporary_path = Path(temporary.name)
    temporary_path.replace(output)
    summary = record["summary"]
    print(f"Engine: AISimulate {record['engine_version']} (SGLang)")
    print(f"Timing model: {record['timing_model']}")
    print(f"Completed requests: {summary['completed_requests']}")
    print(f"Prefix reuse: {summary['prefix_cache_reused_ratio']:.2%}")
    print(f"Committed prefill tokens: {summary['committed_prefill_tokens']}")
    print(f"Mean / p95 TTFT: {summary['mean_ttft_ms']} / {summary['p95_ttft_ms']} ms")
    print(f"Policy decision attempts: {len(record['eviction_decision_attempts'])}")
    print(f"Saved: {output}")


if __name__ == "__main__":
    main()
