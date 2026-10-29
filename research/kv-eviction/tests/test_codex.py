"""Exercise the actual Codex permission boundary without a model call."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kv_experiment.agents import codex_command


@pytest.mark.skipif(
    os.environ.get("KV_RUN_CODEX_TESTS") != "1",
    reason="Set KV_RUN_CODEX_TESTS=1 with native Codex CLI/helper installed",
)
def test_codex_commands_cannot_read_trace_write_runtime_or_access_network(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "withheld.txt"
    outside.write_text("withheld test data")
    command, _ = codex_command(workspace, "gpt-6.1-sol")
    index = next(
        index
        for index, value in enumerate(command)
        if value.startswith("permissions.kv_experiment=")
    )
    shell = """
    printf inside > allowed.txt || exit 10
    if cat "$1"; then exit 11; fi
    if printf forbidden > "$2"; then exit 12; fi
    /usr/bin/curl --version >/dev/null || exit 14
    if /usr/bin/curl -v --noproxy '*' --connect-timeout 1 --max-time 2 http://1.1.1.1 >/dev/null 2>network-error.txt; then exit 13; fi
    printf boundary-ok
    """
    probe = [
        command[0],
        "sandbox",
        "-P",
        "kv_experiment",
        "-C",
        str(workspace),
        "-c",
        command[index],
        "/bin/sh",
        "-c",
        shell,
        "probe",
        str(outside),
        str(workspace / ".runtime/codex"),
    ]
    result = subprocess.run(
        probe, capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "boundary-ok"
    assert (workspace / "allowed.txt").read_text() == "inside"
    assert outside.read_text() == "withheld test data"
    assert "Operation not permitted" in (workspace / "network-error.txt").read_text()


@pytest.mark.skipif(
    os.environ.get("KV_RUN_CODEX_TESTS") != "1", reason="Native Codex required"
)
def test_codex_cannot_inject_project_configuration(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    command, _ = codex_command(workspace, "gpt-6.1-sol")
    index = next(
        index
        for index, value in enumerate(command)
        if value.startswith("permissions.kv_experiment=")
    )
    probe = [
        command[0],
        "sandbox",
        "-P",
        "kv_experiment",
        "-C",
        str(workspace),
        "-c",
        command[index],
        "/bin/sh",
        "-c",
        "mkdir .codex",
    ]
    result = subprocess.run(
        probe, capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode != 0
    assert not (workspace / ".codex").exists()
