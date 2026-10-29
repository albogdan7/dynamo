"""Coding-agent adapters. The controller provides tuning feedback between turns."""

import json
import os
import signal
import subprocess
import time
from pathlib import Path


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
