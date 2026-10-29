"""Audit completed real runs and package compact evidence without editing raw data."""

import argparse
import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from evidence.audit_run import audit
from kv_experiment.traces import ROOT, digest
from run_comparison import MODELS, WORKLOADS
from summarize import METRICS, summarize


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def collect_one(runs, output, trace, agent):
    name = f"{trace}-{agent}"
    raw = runs / name
    bundle = output / name
    bundle.mkdir()
    manifest, baselines, selection, evaluation = (
        read(raw / file)
        for file in (
            "manifest.json",
            "baselines.json",
            "selection.json",
            "evaluation.json",
        )
    )
    if digest(raw / "config.json") != manifest["config_sha256"]:
        raise ValueError("Raw configuration changed")
    for split in ("tuning", "evaluation"):
        if (
            digest(raw / f"{split}-trace.jsonl")
            != manifest["trace"]["windows"][split]["sha256"]
        ):
            raise ValueError("Raw workload changed")
    audit_paths = [
        raw / f"{baseline}-{split}"
        for baseline in baselines
        for split in ("tuning", "evaluation")
    ]
    audit_paths.append(raw / "evaluation")
    submissions = []
    (bundle / "policies").mkdir()
    (bundle / "baselines").mkdir()
    baseline_sources = {}
    for baseline_name, specification in manifest["heuristic_baselines"].items():
        source = raw / f"baseline-{baseline_name}.py"
        if source.read_text() != specification["source"]:
            raise ValueError("Baseline source differs from frozen manifest")
        destination = bundle / "baselines" / f"{baseline_name}.py"
        shutil.copyfile(source, destination)
        baseline_sources[baseline_name] = {
            "path": str(destination.relative_to(ROOT)),
            "sha256": digest(destination),
        }
    for directory in sorted((raw / "submissions").iterdir()):
        status = read(directory / "submission.json")
        source = directory / "policy.py"
        policy_path = None
        if source.is_file():
            destination = bundle / "policies" / f"{directory.name}.py"
            shutil.copyfile(source, destination)
            policy_path = str(destination.relative_to(ROOT))
            if status.get("valid") and digest(source) != status["policy_sha256"]:
                raise ValueError("Submitted policy changed")
        if status.get("valid"):
            audit_paths.append(directory / "tuning")
        metadata = directory / "agent-metadata.json"
        submissions.append(
            {
                "id": directory.name,
                "status": status,
                "policy_path": policy_path,
                "source_sha256": digest(source) if source.is_file() else None,
                "agent_metadata": read(metadata) if metadata.is_file() else None,
            }
        )
    audits = {str(path.relative_to(raw)): audit(path) for path in audit_paths}
    write(bundle / "independent-audit.json", audits)
    replay_output = runs / f"replay-{name}"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "experiment.py"),
            "evaluate",
            str(raw),
            "--output",
            str(replay_output),
        ],
        check=True,
    )
    replay_check = read(replay_output / "replay-check.json")
    if not replay_check["matches"]:
        raise ValueError("Fresh selected-policy replay mismatch")
    shutil.copyfile(replay_output / "replay-check.json", bundle / "replay-check.json")
    snapshot = bundle / "controller-source.zip"
    shutil.copyfile(raw / "controller-source.zip", snapshot)
    record = {
        "schema_version": 1,
        "trace": trace,
        "agent": agent,
        "model": manifest["model"],
        "original_run": str(raw.relative_to(ROOT)),
        "manifest": manifest,
        "baselines": baselines,
        "baseline_sources": baseline_sources,
        "evaluation": evaluation,
        "selection": selection,
        "submissions": submissions,
        "source_snapshot_path": str(snapshot.relative_to(ROOT)),
        "source_snapshot_sha256": digest(snapshot),
        "fresh_replay_matches": True,
        "audited_replays": len(audits),
    }
    write(output / "runs" / f"{name}.json", record)
    prior = read(ROOT / "evidence/comparison/runs" / f"{name}.json")
    fields = [*METRICS, "requests", "slo_passes"]
    checks_by_split = {
        split: {
            field: baselines["native-lru"][split][field]
            == prior["baselines"]["native-lru"][split][field]
            for field in fields
        }
        for split in ("tuning", "evaluation")
    }
    if not all(all(checks.values()) for checks in checks_by_split.values()):
        raise ValueError(f"Native LRU changed after instrumentation: {name}")
    print(
        f"Packaged {name}: {len(audits)} arithmetic checks and exact fresh replay",
        flush=True,
    )
    return name, checks_by_split


def collect(runs, output, activate=False):
    runs, output = runs.resolve(), output.resolve()
    output.relative_to(ROOT)
    # Refuse partial suites before creating a destination.
    names = [f"{trace}-{agent}" for trace in WORKLOADS for agent in MODELS]
    for name in names:
        for file in (
            "manifest.json",
            "baselines.json",
            "selection.json",
            "evaluation.json",
        ):
            if not (runs / name / file).is_file():
                raise ValueError(f"Incomplete experiment: {name}/{file}")
    output.mkdir(parents=True, exist_ok=False)
    (output / "runs").mkdir()
    compatibility = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        jobs = [
            executor.submit(collect_one, runs, output, trace, agent)
            for trace in WORKLOADS
            for agent in MODELS
        ]
        for job in as_completed(jobs):
            name, checks = job.result()
            compatibility[name] = checks
    write(output / "native-lru-compatibility.json", compatibility)
    summarize(output / "runs", output)
    write(
        output / "COMPLETE.json",
        {
            "runs": names,
            "audited_replays": sum(
                read(output / "runs" / f"{name}.json")["audited_replays"]
                for name in names
            ),
            "fresh_agent_searches": 8,
            "random_seeds": [0, 1, 2],
        },
    )
    if activate:
        write(
            ROOT / "evidence/current-comparison.json",
            {"directory": str(output.relative_to(ROOT))},
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--activate-report",
        action="store_true",
        help="Select this evidence only after all checks succeed",
    )
    args = parser.parse_args()
    collect(args.runs, args.output, args.activate_report)
