"""Coding-agent adapters. The controller provides tuning feedback between turns."""

import hashlib
import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

DEFAULT_MODELS = {"claude": "sonnet", "codex": "gpt-6.1-sol"}


def codex_command(workspace: Path, model: str) -> tuple[list[str], dict]:
    """Copy the trusted helper runtime into a read-protected subtree."""
    runtime = workspace / ".runtime"
    runtime.mkdir(exist_ok=True)
    hashes = {}
    for name in ("codex", "codex-code-mode-host"):
        installed = shutil.which(name)
        if installed is None:
            raise RuntimeError(
                f"Install the native Codex CLI and matching {name} helper"
            )
        source = Path(installed).resolve()
        checksum = hashlib.sha256(source.read_bytes()).hexdigest()
        target = runtime / name
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != checksum:
                raise RuntimeError("Codex runtime changed during this experiment")
        else:
            shutil.copy2(source, target)
        hashes[name] = checksum
    marker = workspace / ".kv-experiment-root"
    marker.touch(exist_ok=True)
    filesystem = {
        ":root": "deny",
        ":minimal": "read",
        str(workspace): "write",
        str(runtime): "read",
        str(marker): "read",
    }
    for name in (".codex", ".agents", ".git", "AGENTS.md", "CLAUDE.md", ".mcp.json"):
        filesystem[str(workspace / name)] = "deny"
    profile = (
        "{filesystem={"
        + ",".join(
            json.dumps(key) + "=" + json.dumps(value)
            for key, value in filesystem.items()
        )
        + "},network={enabled=false}}"
    )
    overrides = {
        "default_permissions": '"kv_experiment"',
        "permissions.kv_experiment": profile,
        "approval_policy": '"never"',
        "web_search": '"disabled"',
        "project_doc_max_bytes": "0",
        "project_root_markers": '[".kv-experiment-root"]',
        "shell_environment_policy.inherit": '"none"',
        "model_reasoning_effort": '"medium"',
    }
    for feature in (
        "apps",
        "plugins",
        "multi_agent",
        "shell_snapshot",
        "shell_tool",
        "skill_search",
        "remote_plugin",
    ):
        overrides[f"features.{feature}"] = "false"
    command = [
        str(runtime / "codex"),
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--strict-config",
        "--skip-git-repo-check",
        "--ephemeral",
        "--json",
        "-C",
        str(workspace),
        "--model",
        model,
    ]
    for key, value in overrides.items():
        command.extend(["-c", f"{key}={value}"])
    return command, {
        "runtime_sha256": hashes,
        "filesystem": filesystem,
        "network": False,
        "shell_tool": False,
        "reasoning_effort": "medium",
    }


def invoke_codex(
    workspace: Path, prompt: str, model: str, output: Path, timeout: float = 240
) -> dict:
    command, safety = codex_command(workspace.resolve(), model)
    # With shell disabled, give the agent its current policy directly; only
    # apply_patch is needed to submit a revision.
    prompt += (
        "\nUse apply_patch to edit policy.py. Current submitted source:\n\n"
        + (workspace / "policy.py").read_text()
    )
    command.append(prompt)
    (output / "agent-command.json").write_text(json.dumps(command, indent=2) + "\n")
    (output / "agent-safety.json").write_text(json.dumps(safety, indent=2) + "\n")
    (output / "prompt.md").write_text(prompt)
    started = time.monotonic()
    with (
        (output / "agent.jsonl").open("wb") as stdout,
        (output / "agent-stderr.txt").open("wb") as stderr,
    ):
        process = subprocess.Popen(
            command,
            cwd=workspace,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise TimeoutError(
                "Codex invocation exceeded its wall-clock budget"
            ) from None
    records = [
        json.loads(line)
        for line in (output / "agent.jsonl").read_text().splitlines()
        if line.strip()
    ]
    turns = [record for record in records if record.get("type") == "turn.completed"]
    failures = [
        record
        for record in records
        if record.get("type") in {"turn.failed", "error"}
        or record.get("item", {}).get("type") == "error"
    ]
    edits = [
        record
        for record in records
        if record.get("item", {}).get("type") == "file_change"
        and record["item"].get("status") == "completed"
        and any(
            Path(change["path"]).resolve() == (workspace / "policy.py").resolve()
            for change in record["item"].get("changes", [])
        )
    ]
    if returncode or not turns or failures or not edits:
        raise RuntimeError(
            "Codex did not complete a successful policy edit; inspect agent.jsonl and agent-stderr.txt"
        )
    return {
        "requested_model": model,
        "resolved_model": None,
        "model_identity_note": "Exact requested model ID; CLI JSON does not report the server-resolved ID",
        "runtime_seconds": time.monotonic() - started,
        "usage": turns[-1].get("usage"),
        "model_usage": None,
        "api_equivalent_usd": None,
        "cost_note": "CLI reports token usage but no monetary cost; no estimate invented",
    }


def invoke_agent(
    agent: str, workspace: Path, prompt: str, model: str, output: Path
) -> dict:
    return {"claude": invoke_claude, "codex": invoke_codex}[agent](
        workspace, prompt, model, output
    )


def invoke_claude(
    workspace: Path, prompt: str, model: str, output: Path, timeout: float = 240
) -> dict:
    help_text = subprocess.run(
        ["claude", "--help"], capture_output=True, text=True, check=True
    ).stdout
    if "--restricted" not in help_text or "--safe-mode" not in help_text:
        raise RuntimeError("Claude Code with --restricted and --safe-mode is required")
    command = [
        "claude",
        "--print",
        "--output-format",
        "stream-json",
        "--verbose",
        "--restricted",
        "--safe-mode",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--permission-mode",
        "acceptEdits",
        "--permission-prompts",
        "none",
        "--tools",
        "Read,Edit,Write,Glob,Grep",
        "--allowedTools",
        "Read,Edit,Write,Glob,Grep",
        "--no-session-persistence",
        "--max-turns",
        "10",
        "--model",
        model,
        "--",
        prompt,
    ]
    (output / "agent-command.json").write_text(json.dumps(command, indent=2) + "\n")
    (output / "prompt.md").write_text(prompt)
    environment = os.environ.copy()
    environment.pop("CLAUDECODE", None)
    started = time.monotonic()
    with (
        (output / "agent.jsonl").open("wb") as stdout,
        (output / "agent-stderr.txt").open("wb") as stderr,
    ):
        process = subprocess.Popen(
            command,
            cwd=workspace,
            env=environment,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise TimeoutError(
                "Claude invocation exceeded its wall-clock budget"
            ) from None
    records = [
        json.loads(line)
        for line in (output / "agent.jsonl").read_text().splitlines()
        if line.strip()
    ]
    results = [record for record in records if record.get("type") == "result"]
    if returncode or not results or results[-1].get("is_error"):
        raise RuntimeError(
            f"Claude failed (exit {returncode}); inspect agent.jsonl and agent-stderr.txt"
        )
    result = results[-1]
    initializations = [
        record
        for record in records
        if record.get("type") == "system" and record.get("subtype") == "init"
    ]
    return {
        "requested_model": model,
        "resolved_model": initializations[0].get("model") if initializations else None,
        "runtime_seconds": time.monotonic() - started,
        "usage": result.get("usage"),
        "model_usage": result.get("modelUsage"),
        "api_equivalent_usd": result.get("total_cost_usd"),
        "cost_note": "CLI-reported API-equivalent estimate, not subscription billing",
    }
