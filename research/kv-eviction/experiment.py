#!/usr/bin/env python3
"""Reproduce, optimize, and rescore KV eviction policies. Stdlib controller."""

import argparse
import hashlib
import json
import math
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from kv_experiment.agents import DEFAULT_MODELS, invoke_agent
from kv_experiment.legacy import reproduce as reproduce_legacy
from kv_experiment.metrics import score
from kv_experiment.runtime import IMAGE, NATIVE, PolicyError, replay
from kv_experiment.traces import ROOT, digest, fetch, windows

LRU = """class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        return min(blocks, key=lambda block: block["lru_rank"])["block_id"]
"""
LEAF_FIRST = """class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        return min(blocks, key=lambda block: (block["release_order"], -block["creation_order"]))["block_id"]
"""
PROMPT = """Optimize a KV-cache eviction policy. Edit ONLY policy.py in this directory.
Implement class EvictionPolicy with select_victim(self, blocks: list[dict]) -> int.
Return one current candidate's integer block_id. State may persist within a replay.
Candidates are ONLY unreferenced, unpinned, inactive native vLLM blocks. Fields:
block_id: opaque integer, never a numeric hint about trace content;
lru_rank: 0 is oldest in the native inactive queue;
creation_order: monotonic allocation-event counter;
release_order: monotonic inactive-release-event counter;
num_releases: number of times this physical copy entered the inactive queue.
These are causal logical counters, NOT simulated timestamps, depth, or hit counts.
You cannot change scheduling, cache capacity, trace, scorer, timing, or the SLO.
You cannot access future requests or evaluation data. The controller executes your
policy in a network-free, read-only container, with a 2-second decision deadline.
Use Python standard library only. Do not print; the protocol owns stdout.
This is a flat fixed-arrival Mooncake replay, not a dependency-aware agent benchmark.
Timing is native polynomial and uncalibrated; scores are simulation estimates.
Cache capacity is {cache_tokens} tokens in 512-token blocks. Each tuning replay has
{window_requests} requests; inter-arrival times are multiplied by {arrival_scale}.
Maximize tuning goodput (completed requests meeting TTFT <= {ttft}ms AND
per-request p95 individual token gaps <= {itl}ms, per simulated second including drain).
Ties: lower mean TTFT, then earlier submission. ITL is not average TPOT.
Only one submitted policy is evaluated after this turn. Read the existing policy,
make a concrete candidate edit, and finish. You have no shell or evaluation tools.
Do not infer evaluation outcomes from tuning. The controller reports tuning feedback
on the next turn. Invalid policies fail; no fallback policy receives a successful score.

Tuning feedback so far:
{feedback}
"""


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def submission_dir(parent: Path) -> Path:
    stamp = datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d-%H-%M-%S")
    parent.mkdir(exist_ok=True)
    for suffix in range(1000):
        path = parent / (stamp if suffix == 0 else f"{stamp}-{suffix + 1:02}")
        try:
            path.mkdir()
            return path
        except FileExistsError:
            continue
    raise RuntimeError("Too many submissions in one second")


def command_output(command: list[str]) -> str:
    return subprocess.run(
        command, capture_output=True, text=True, check=True
    ).stdout.strip()


def source_fingerprint() -> dict:
    repo = ROOT.parents[1]
    files = sorted(
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and (
            len(path.relative_to(ROOT).parts) == 1
            or path.relative_to(ROOT).parts[0]
            in {"kv_experiment", "native", "profiles", "sim", "sandbox"}
        )
        and not any(
            part
            in {
                ".cache",
                ".venv",
                "__pycache__",
                ".pytest_cache",
                ".ruff_cache",
                "experiments",
                "target",
                "results",
                "evidence",
            }
            for part in path.relative_to(ROOT).parts
        )
    )
    hashes = {str(path.relative_to(repo)): digest(path) for path in files}
    return {
        "commit": command_output(["git", "-C", str(repo), "rev-parse", "HEAD"]),
        "files": hashes,
        "sha256": hashlib.sha256(
            json.dumps(hashes, sort_keys=True).encode()
        ).hexdigest(),
    }


def setup(args) -> tuple[Path, Path, dict]:
    output = (
        args.output.resolve()
        if args.output.is_absolute()
        else (ROOT / args.output).resolve()
    )
    output.relative_to(ROOT)
    output.mkdir(parents=True, exist_ok=False)
    profile = json.loads(args.timing_profile.read_text())
    if profile["rank"]["block_size"] != 512 or args.cache_tokens % 512:
        raise ValueError(
            "Mooncake requires 512-token blocks and a whole-block capacity"
        )
    profile["rank"]["num_gpu_blocks"] = args.cache_tokens // 512
    config = output / "config.json"
    write_json(config, profile)
    provenance = windows(fetch(), output, args.window_requests, args.arrival_scale)
    source = source_fingerprint()
    with zipfile.ZipFile(
        output / "controller-source.zip", "w", zipfile.ZIP_DEFLATED
    ) as archive:
        for filename in source["files"]:
            archive.write(ROOT.parents[1] / filename, filename)
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(ZoneInfo("America/Chicago")).isoformat(),
        "timezone": "America/Chicago",
        "command": [sys.executable, *sys.argv],
        "source": source,
        "trace": provenance,
        "timing": profile,
        "config_sha256": digest(config),
        "native_binary_sha256": digest(NATIVE),
        "policy_container": IMAGE,
        "policy_decision_timeout_seconds": 2,
        "python": sys.version,
        "cargo": command_output(["cargo", "--version"]),
        "slo": {"ttft_ms": args.ttft_slo_ms, "per_request_p95_itl_ms": args.itl_slo_ms},
        "selection": "highest tuning goodput, lowest tuning mean TTFT, earlier submission",
        "agent": getattr(args, "agent", None),
        "model": getattr(args, "model", None),
        "submission_budget": getattr(args, "submissions", 0),
        "warmup_requests": 0,
        "arrival_rate_scale": 1 / args.arrival_scale,
        "determinism": "canonical_v1",
        "limitation": "Uncalibrated polynomial timing; not measured GPU performance. Native inactive release order has no ties, so the creation-order tiebreak is equivalent to native LRU. Legacy results are a separate simulator.",
    }
    if manifest["agent"]:
        manifest["agent_cli_version"] = command_output([args.agent, "--version"])
    write_json(output / "manifest.json", manifest)
    return output, config, manifest


def baseline(args, output: Path, config: Path) -> dict:
    baselines = {}
    policy = output / "leaf-first-policy.py"
    policy.write_text(LEAF_FIRST)
    for name, candidate in (("native-lru", None), ("leaf-first-lru", policy)):
        baselines[name] = {}
        for split in ("tuning", "evaluation"):
            print(f"Baseline {name}: {split}", flush=True)
            baselines[name][split] = replay(
                output / f"{split}-trace.jsonl",
                config,
                output / f"{name}-{split}",
                candidate,
                args.ttft_slo_ms,
                args.itl_slo_ms,
            )
    write_json(output / "baselines.json", baselines)
    return baselines


def report(output: Path, baselines: dict, evaluation: dict | None = None) -> None:
    columns = [
        ("Mean TTFT ms", "mean_ttft_ms"),
        ("p95 TTFT ms", "p95_ttft_ms"),
        ("Mean ITL ms", "mean_itl_ms"),
        ("p95 ITL ms", "p95_itl_ms"),
        ("Goodput req/s", "goodput_rps"),
        ("SLO attainment %", "slo_attainment_pct"),
        ("Input-token hit %", "token_hit_pct"),
        ("Recomputed input tokens", "recomputed_input_tokens"),
        ("Evictions (512-token blocks)", "evictions"),
    ]
    policies = {name: splits["evaluation"] for name, splits in baselines.items()}
    if evaluation:
        policies["selected"] = evaluation
    lines = [
        "# KV eviction evaluation",
        "",
        "Native serving replay; **uncalibrated polynomial timing, not GPU measurements**.",
        "All policies use identical fixed-arrival evaluation data and fresh state.",
        "Native release order has no ties: the leaf-first creation tiebreak is equivalent to native LRU here.",
        "",
        "| Metric | " + " | ".join(policies) + " |",
        "| --- | " + " | ".join("---:" for _ in policies) + " |",
    ]
    for label, key in columns:
        values = [
            "unavailable" if metrics[key] is None else f"{metrics[key]:.6g}"
            for metrics in policies.values()
        ]
        lines.append(f"| {label} | " + " | ".join(values) + " |")
    lines += [
        "",
        "ITL pools consecutive output-token gaps; SLO checks each request's p95 gap. Quantiles use nearest rank.",
        "Goodput includes queueing and drain. Zero-output requests are excluded from SLO eligibility; one-token outputs have no ITL samples.",
        "Failures remain in the eligible denominator. Input hit rate uses first admission; recompute includes repeated prefill after preemption.",
        "",
        "See manifest.json, requests.jsonl, tokens.jsonl and request-scores.jsonl for provenance and rescoring.",
    ]
    manifest = json.loads((output / "manifest.json").read_text())
    lines += [
        "",
        f"Arrival-time multiplier: {manifest['trace'].get('arrival_time_multiplier', 1)}. Source windows, timestamps and exact configuration are recorded in manifest.json.",
    ]
    for name, metrics in policies.items():
        if metrics["slo_warning"]:
            lines += ["", f"**{name}: {metrics['slo_warning']}.**"]
    if evaluation:
        comparisons = {}
        lines += [
            "",
            "| Selected vs baseline | Metric | Change |",
            "| --- | --- | ---: |",
        ]
        for name, splits in baselines.items():
            comparisons[name] = {}
            for label, key in columns:
                reference, current = splits["evaluation"][key], evaluation[key]
                absolute = (
                    current - reference
                    if current is not None and reference is not None
                    else None
                )
                relative = (
                    100 * absolute / reference
                    if absolute is not None and reference
                    else None
                )
                comparisons[name][key] = {
                    "absolute": absolute,
                    "relative_pct": relative,
                }
                difference = (
                    f"{absolute:+.6g} percentage points"
                    if key in {"token_hit_pct", "slo_attainment_pct"}
                    and absolute is not None
                    else f"{relative:+.4g}%"
                    if relative is not None
                    else "unavailable (zero/missing baseline)"
                )
                lines.append(f"| {name} | {label} | {difference} |")
        write_json(output / "comparisons.json", comparisons)
    (output / "summary.md").write_text("\n".join(lines) + "\n")


def run(args, output: Path, config: Path, baselines: dict) -> None:
    workspace = output / "agent-workspace"
    workspace.mkdir()
    (workspace / "policy.py").write_text(LRU)
    feedback = [
        {"policy": name, "tuning": values["tuning"]}
        for name, values in baselines.items()
    ]
    candidates = []
    model = args.model
    for index in range(args.submissions):
        submission = submission_dir(output / "submissions")
        print(
            f"Submission {index + 1}/{args.submissions}: {submission.name}", flush=True
        )
        prompt = PROMPT.format(
            ttft=args.ttft_slo_ms,
            itl=args.itl_slo_ms,
            cache_tokens=args.cache_tokens,
            window_requests=args.window_requests,
            arrival_scale=args.arrival_scale,
            feedback=json.dumps(feedback, indent=2),
        )
        if index == 0:
            (output / "prompt.md").write_text(prompt)
        status = {
            "id": submission.name,
            "ordinal": index + 1,
            "created_at": datetime.now(ZoneInfo("America/Chicago")).isoformat(),
            "valid": False,
        }
        started = time.monotonic()
        phase = "agent"
        try:
            agent = invoke_agent(args.agent, workspace, prompt, model, submission)
            write_json(submission / "agent-metadata.json", agent)
            if agent["resolved_model"]:
                model = agent["resolved_model"]
            shutil.copyfile(workspace / "policy.py", submission / "policy.py")
            phase = "policy"
            metrics = replay(
                output / "tuning-trace.jsonl",
                config,
                submission / "tuning",
                submission / "policy.py",
                args.ttft_slo_ms,
                args.itl_slo_ms,
            )
            status.update(
                valid=True,
                tuning=metrics,
                policy_sha256=digest(submission / "policy.py"),
            )
            status["duplicate"] = any(
                digest(previous / "policy.py") == status["policy_sha256"]
                for previous, _ in candidates
            )
            candidates.append((submission, metrics))
        except (
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            TimeoutError,
            subprocess.SubprocessError,
        ) as error:
            status["error_type"] = type(error).__name__
            status["failure_phase"] = phase
            status["failure_kind"] = (
                error.kind
                if isinstance(error, PolicyError)
                else "timeout"
                if isinstance(error, TimeoutError)
                else "protocol_error"
                if isinstance(error, (ValueError, TypeError))
                else "process_failure"
            )
            status["error"] = str(error)
            if (workspace / "policy.py").is_file() and not (
                submission / "policy.py"
            ).exists():
                shutil.copyfile(workspace / "policy.py", submission / "policy.py")
            print(f"  Invalid submission: {error}", flush=True)
        status["wall_seconds"] = time.monotonic() - started
        write_json(submission / "submission.json", status)
        feedback.append(status)
    failures = [item for item in feedback if item.get("valid") is False]
    write_json(
        output / "search-summary.json",
        {
            "submitted_policies": args.submissions,
            "valid_submissions": len(candidates),
            "invalid_submissions": len(failures),
            "agent_failures": sum(
                item["failure_phase"] == "agent" for item in failures
            ),
            "policy_failures": sum(
                item["failure_phase"] == "policy" for item in failures
            ),
            "policy_timeouts": sum(
                item["failure_phase"] == "policy"
                and item["error_type"] == "TimeoutError"
                for item in failures
            ),
            "policy_exceptions": sum(
                item["failure_phase"] == "policy"
                and item["failure_kind"] == "policy_exception"
                for item in failures
            ),
            "invalid_policy_decisions": sum(
                item["failure_phase"] == "policy"
                and item["failure_kind"] in {"invalid_decision", "protocol_error"}
                for item in failures
            ),
        },
    )
    if not candidates:
        raise RuntimeError(
            "No valid agent submission; failure artifacts were preserved"
        )
    selected, tuning = max(
        candidates, key=lambda item: (item[1]["goodput_rps"], -item[1]["mean_ttft_ms"])
    )
    shutil.copyfile(selected / "policy.py", output / "selected-policy.py")
    write_json(
        output / "selection.json",
        {
            "submission_id": selected.name,
            "tuning": tuning,
            "policy_sha256": digest(output / "selected-policy.py"),
        },
    )
    print("Evaluating selected policy with fresh state", flush=True)
    evaluation = replay(
        output / "evaluation-trace.jsonl",
        config,
        output / "evaluation",
        output / "selected-policy.py",
        args.ttft_slo_ms,
        args.itl_slo_ms,
    )
    write_json(output / "evaluation.json", evaluation)
    report(output, baselines, evaluation)


def reproduce(output: Path):
    reproduce_legacy(output, source_metadata=source_fingerprint())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("fetch-traces")
    repro = commands.add_parser("reproduce")
    repro.add_argument("--output", type=Path, default=Path("experiments/reproduction"))
    for name in ("baseline", "run"):
        command = commands.add_parser(name)
        command.add_argument(
            "--trace", choices=["mooncake-toolagent"], default="mooncake-toolagent"
        )
        command.add_argument("--cache-tokens", type=int, default=131072)
        command.add_argument("--window-requests", type=int, default=1000)
        command.add_argument(
            "--arrival-scale",
            type=float,
            default=1,
            help="Multiply inter-arrival timing; 10 gives one tenth the offered load",
        )
        command.add_argument(
            "--timing-profile", type=Path, default=ROOT / "profiles/interactive.json"
        )
        command.add_argument("--ttft-slo-ms", type=float, default=1000)
        command.add_argument("--itl-slo-ms", type=float, default=50)
        command.add_argument(
            "--output",
            type=Path,
            default=Path(
                f"experiments/{name}-" + datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
            ),
        )
        if name == "run":
            command.add_argument(
                "--agent", choices=["claude", "codex"], default="claude"
            )
            command.add_argument(
                "--model", help="Exact model ID; default depends on agent"
            )
            command.add_argument("--submissions", type=int, default=5)
    rescore = commands.add_parser("rescore")
    rescore.add_argument("directory", type=Path)
    rescore.add_argument("--ttft-slo-ms", type=float, default=1000)
    rescore.add_argument("--itl-slo-ms", type=float, default=50)
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("run_directory", type=Path)
    evaluate.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "run":
        args.model = args.model or DEFAULT_MODELS[args.agent]
    if args.command == "fetch-traces":
        print(fetch())
    elif args.command == "reproduce":
        output = (
            args.output.resolve() if args.output.is_absolute() else ROOT / args.output
        )
        output.resolve().relative_to(ROOT)
        reproduce(output)
    elif args.command == "rescore":
        print(
            json.dumps(
                score(args.directory, args.ttft_slo_ms, args.itl_slo_ms), indent=2
            )
        )
    elif args.command == "evaluate":
        directory = args.run_directory.resolve()
        directory.relative_to(ROOT)
        output = (
            args.output.resolve()
            if args.output.is_absolute()
            else (ROOT / args.output).resolve()
        )
        output.relative_to(ROOT)
        manifest = json.loads((directory / "manifest.json").read_text())
        selection = json.loads((directory / "selection.json").read_text())
        if digest(directory / "selected-policy.py") != selection["policy_sha256"]:
            raise ValueError("Selected policy checksum changed")
        if digest(directory / "config.json") != manifest["config_sha256"]:
            raise ValueError("Replay configuration checksum changed")
        if (
            digest(directory / "evaluation-trace.jsonl")
            != manifest["trace"]["windows"]["evaluation"]["sha256"]
        ):
            raise ValueError("Evaluation trace checksum changed")
        if digest(NATIVE) != manifest["native_binary_sha256"]:
            raise ValueError(
                "Native replay binary changed; use the original build for exact reproduction"
            )
        result = replay(
            directory / "evaluation-trace.jsonl",
            directory / "config.json",
            output,
            directory / "selected-policy.py",
            manifest["slo"]["ttft_ms"],
            manifest["slo"]["per_request_p95_itl_ms"],
        )
        expected = json.loads((directory / "evaluation.json").read_text())
        comparisons = {
            key: result[key] == value
            for key, value in expected.items()
            if key not in {"simulator_wall_seconds", "slo_warning"}
        }
        write_json(
            output / "replay-check.json",
            {"matches": all(comparisons.values()), "fields": comparisons},
        )
        if not all(comparisons.values()):
            raise ValueError(
                "Selected policy did not reproduce; inspect replay-check.json"
            )
        print(json.dumps(result, indent=2))
    else:
        if (
            args.cache_tokens <= 0
            or args.window_requests <= 0
            or getattr(args, "submissions", 1) <= 0
        ):
            parser.error("Capacity, window and submission budget must be positive")
        if any(
            not math.isfinite(value) or value <= 0
            for value in (args.ttft_slo_ms, args.itl_slo_ms, args.arrival_scale)
        ):
            parser.error("SLO thresholds must be finite and positive")
        output, config, manifest = setup(args)
        baselines = baseline(args, output, config)
        report(output, baselines)
        if args.command == "run":
            run(args, output, config, baselines)
        print(f"Saved {output / 'summary.md'}")


if __name__ == "__main__":
    main()
