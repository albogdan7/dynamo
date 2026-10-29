"""Reproduce the pinned legacy policies without importing the serving runner."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from .traces import ROOT, digest


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def reproduce(output: Path, source_metadata: dict | None = None):
    """Replay legacy source in isolated scratch directories without editing it."""
    output.mkdir(parents=True, exist_ok=False)
    baseline_commit = "dc61f2bbfbed0de800a7b2399188be82737f5bd0"
    files = {}
    for relative in (
        "sim/cache.py",
        "sim/trace.py",
        "sandbox/policy.py",
        "sandbox/submit.py",
        "sandbox/policy_runner.py",
        "results/run_20261026_125852.json",
    ):
        files[relative] = subprocess.run(
            [
                "git",
                "-C",
                str(ROOT.parents[1]),
                "show",
                f"{baseline_commit}:research/kv-eviction/{relative}",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    previous = json.loads(files["results/run_20261026_125852.json"])
    policies = {
        "lru": previous["policy_source"],
        "leaf-first-lru": files["sandbox/policy.py"],
    }
    results = {}
    for name, source in policies.items():
        for repeat in range(2):
            scratch = output / f"{name}-{repeat + 1}"
            for relative, content in files.items():
                if relative.startswith("results/"):
                    continue
                destination = scratch / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(content)
            (scratch / "sandbox/policy.py").write_text(source)
            with (scratch / "command-output.txt").open("w") as stdout:
                subprocess.run(
                    [sys.executable, str(scratch / "sandbox/submit.py")],
                    check=True,
                    stdout=stdout,
                    cwd=scratch,
                )
            result = json.loads(next((scratch / "results").glob("*.json")).read_text())
            for split in ("tuning", "eval"):
                result[split].pop("sim_wall_time_s", None)
            current = {split: result[split] for split in ("tuning", "eval")}
            if name in results and results[name] != current:
                raise ValueError(f"Legacy {name} did not reproduce exactly")
            results[name] = current
    write_json(
        output / "reproduction.json",
        {
            "source": source_metadata
            if source_metadata is not None
            else {"controller_sha256": digest(Path(__file__)), "python": sys.version},
            "baseline_commit": baseline_commit,
            "baseline_file_sha256": {
                relative: hashlib.sha256(content.encode()).hexdigest()
                for relative, content in files.items()
            },
            "results": results,
            "limitations": "Legacy 50ms + 3ms/missed-block proxy; no ITL or achieved goodput. Eval plain LRU is 60.57ms/75.71%/843 evictions, not tuning's 60.66ms/75.49%/853.",
        },
    )
    print(json.dumps(results, indent=2))
