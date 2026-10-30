"""Check session semantics against native clocks and actual prefix accounting."""

import gzip
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kv_experiment import inventory
from kv_experiment.inventory import Tally, ledger_part, publisher_checks
from kv_experiment.runtime import NATIVE, replay
from kv_experiment.sessions import free_rows, qualify_free, session_windows
from kv_experiment.traces import ROOT


def session(identity="s", model="m", output=1):
    requests = []
    for index in range(2):
        requests.append(
            {
                "role_tokens": {"system": 515},
                "block_ids": list(range(32)),
                "provider_tokens": {"completion": output},
                "tool_calls": [],
                "start_ms": index * 100,
                "end_ms": index * 100 + 20,
                "model": model,
                "provider": "provider",
            }
        )
    return {"session_id": identity, "requests": requests}


def test_model_domains_and_unknown_partial_tails_are_never_shared():
    rows = free_rows([session(), session("other", "different-model")])
    assert rows[0]["hash_ids"][:32] == rows[1]["hash_ids"][:32]
    assert rows[0]["hash_ids"][-1] != rows[1]["hash_ids"][-1]
    assert not set(rows[0]["hash_ids"]) & set(rows[2]["hash_ids"])
    assert rows[1]["dependencies"] == [
        {
            "request_id": "s:0",
            "trigger": "completion",
            "relation": "sequence",
            "delay_ms": 80,
        }
    ]
    assert rows[1]["not_before_ms"] == 0


def test_ambiguous_zero_and_nested_sessions_are_rejected_whole():
    bad = session(output=0)
    assert qualify_free(bad)
    with pytest.raises(ValueError, match="qualified complete sessions"):
        free_rows([bad])
    nested = session()
    nested["requests"][0]["tool_calls"] = [{"subagent": session("child")}]
    assert "nested sessions require unsupported join semantics" in qualify_free(nested)


def test_windows_keep_complete_session_boundaries(tmp_path):
    source = tmp_path / "raw.jsonl"
    source.write_text(
        "".join(json.dumps(session(str(index))) + "\n" for index in range(4))
    )
    output = tmp_path / "windows"
    output.mkdir()
    manifest = session_windows("freeinference", source, output, 2, 1)
    assert manifest["windows"]["tuning"]["requests"] == 4
    assert manifest["windows"]["evaluation"]["requests"] == 4
    assert set(manifest["windows"]["tuning"]["session_ids"]).isdisjoint(
        manifest["windows"]["evaluation"]["session_ids"]
    )


@pytest.fixture
def agentic_input(tmp_path):
    if not NATIVE.exists():
        pytest.skip("Build the native adapter first")
    rows = free_rows([session(output=2)])
    # Source's 80ms wait follows simulated completion, not its recorded end.
    path = tmp_path / "trace.jsonl"
    header = {
        "schema": "dynamo.agentic_mooncake",
        "version": 2,
        "block_size": 16,
        "hash_id_scope": "local",
        "source": {"format": "test", "digest": "0" * 64},
    }
    path.write_text(
        json.dumps(header) + "\n" + "".join(json.dumps(row) + "\n" for row in rows)
    )
    config = json.loads((ROOT / "profiles/interactive.json").read_text())
    config["trace"] = {"format": "agentic_mooncake", "source_block_size": 16}
    config["rank"].update(
        num_gpu_blocks=8,
        max_num_seqs=2,
        max_num_batched_tokens=512,
        timing_model={"type": "fixed", "prefill_ms": 10, "decode_ms": 20},
    )
    profile = tmp_path / "config.json"
    profile.write_text(json.dumps(config))
    return path, profile


def test_native_closed_loop_wait_and_coarsening(agentic_input, tmp_path):
    trace, profile = agentic_input
    metrics = replay(trace, profile, tmp_path / "first", None, 1000, 50)
    rows = {
        row["request_id"]: row
        for row in map(
            json.loads, (tmp_path / "first/requests.jsonl").read_text().splitlines()
        )
    }
    assert rows["s:1"]["arrival_time_ms"] == rows["s:0"]["terminal_time_ms"] + 80
    assert metrics["reused_input_tokens"] == 512
    assert (
        metrics["recomputed_input_tokens"] == 518
    )  # 515 cold + 3 private-tail tokens.
    config = json.loads(profile.read_text())
    config["rank"]["timing_model"]["prefill_ms"] = 30
    profile.write_text(json.dumps(config))
    replay(trace, profile, tmp_path / "slower", None, 1000, 50)
    slower = {
        row["request_id"]: row
        for row in map(
            json.loads, (tmp_path / "slower/requests.jsonl").read_text().splitlines()
        )
    }
    assert slower["s:1"]["arrival_time_ms"] == slower["s:0"]["terminal_time_ms"] + 80
    assert slower["s:1"]["arrival_time_ms"] > rows["s:1"]["arrival_time_ms"]


def test_native_spawn_and_join_use_correct_clock_edges(agentic_input, tmp_path):
    trace, profile = agentic_input
    objects = list(map(json.loads, trace.read_text().splitlines()))
    header, parent, child = objects
    parent["request_id"] = "parent"
    parent["output_length"] = 8
    child["request_id"] = "child"
    child["output_length"] = 12
    child["dependencies"] = [
        {
            "request_id": "parent",
            "trigger": "dispatch",
            "relation": "spawn",
            "delay_ms": 5,
        }
    ]
    join = dict(
        child,
        request_id="join",
        output_length=1,
        dependencies=[
            {
                "request_id": "parent",
                "trigger": "completion",
                "relation": "sequence",
                "delay_ms": 50,
            },
            {
                "request_id": "child",
                "trigger": "completion",
                "relation": "join",
                "delay_ms": 0,
            },
        ],
    )
    trace.write_text(
        "".join(json.dumps(row) + "\n" for row in [header, parent, child, join])
    )
    replay(trace, profile, tmp_path / "joined", None, 1000, 50)
    rows = {
        row["request_id"]: row
        for row in map(
            json.loads, (tmp_path / "joined/requests.jsonl").read_text().splitlines()
        )
    }
    assert rows["child"]["arrival_time_ms"] == 5
    assert rows["join"]["arrival_time_ms"] == max(
        rows["parent"]["terminal_time_ms"] + 50, rows["child"]["terminal_time_ms"]
    )


def ledger(tmp_path, kind, lines, capacity_blocks, compress=False):
    """Classify fixture source lines exactly as the full-release inventory does."""
    source = tmp_path / f"{kind}.jsonl"
    source.write_text("".join(json.dumps(line) + "\n" for line in lines))
    if compress:
        subprocess.run(["zstd", "-q", "-f", str(source)], check=True)
        source = source.with_suffix(".jsonl.zst")
    part = tmp_path / f"{kind}.ledger.gz"
    _, tally, decompressed = ledger_part(
        (kind, str(source), "day/2026-01-01.jsonl.zst", capacity_blocks, str(part))
    )
    with gzip.open(part, "rt") as handle:
        rows = [json.loads(line) for line in handle]
    return rows, tally, decompressed


def free_request(start, end, completion=4, tokens=600, subagent=None):
    return {
        "start_ms": start,
        "end_ms": end,
        "model": "m",
        "provider": "p",
        "role_tokens": {"system": tokens},
        "provider_tokens": {"completion": completion},
        "tool_calls": [{"subagent": subagent}] if subagent else [],
    }


def test_free_ledger_counts_nested_overlap_zero_and_capacity_once(tmp_path):
    child = {
        "session_id": "child",
        "requests": [free_request(30, 40), free_request(41, 50)],
    }
    root = {
        "session_id": "root",
        "requests": [
            free_request(0, 20, subagent=child),
            free_request(15, 60, completion=0),  # overlaps its predecessor
            free_request(70, 80, tokens=1100),  # 3 blocks with output > 2-block cache
        ],
    }
    rows, tally, decompressed = ledger(
        tmp_path, "freeinference", [root], 2, compress=True
    )
    assert [(row["source_id"], row["location"]) for row in rows] == [
        ("root:0", "$.requests[0]"),
        ("child:0", "$.requests[0].tool_calls[0].subagent.requests[0]"),
        ("child:1", "$.requests[0].tool_calls[0].subagent.requests[1]"),
        ("root:1", "$.requests[1]"),
        ("root:2", "$.requests[2]"),
    ]
    flags = {row["source_id"]: row["flags"] for row in rows}
    assert flags["root:0"] == []
    assert flags["child:0"] == flags["child:1"] == ["nested_subagent"]
    assert flags["root:1"] == ["overlap", "zero_output"]
    assert flags["root:2"] == ["infeasible", "input_exceeds_capacity"]
    # The prompt-only capacity flag is diagnostic; every other flag blocks here.
    assert rows[4]["blocked_by"] == ["infeasible"]
    assert all(row["blocked_by"] == row["flags"] for row in rows[:4])
    # Nothing is dropped or invented: lengths are copied from the source.
    assert [row["output_tokens"] for row in rows] == [4, 4, 4, 0, 4]
    assert tally.totals["blocked_excluding_capacity"] == 3
    assert decompressed["lines"] == 1
    with pytest.raises(ValueError, match="publisher"):
        publisher_checks({"day": decompressed}, {"day": {**decompressed, "lines": 2}})


def test_agentx_subagents_are_supported_but_zero_output_blocks(tmp_path):
    play = {
        "id": "play",
        "requests": [
            {"type": "s", "model": "a", "in": 640, "out": 20},
            {
                "type": "subagent",
                "agent_id": "x",
                "requests": [
                    {"model": "b", "in": 64, "out": 0},
                    {"model": "b", "in": 128, "out": 3},
                ],
            },
        ],
    }
    rows, tally, _ = ledger(tmp_path, "weka", [play], 4)
    assert [row["source_id"] for row in rows] == ["play:0", "play:1.0", "play:1.1"]
    assert rows[2]["flags"] == ["subagent"] and rows[2]["blocked_by"] == []
    assert rows[1]["blocked_by"] == ["zero_output"]
    assert tally.nested_sessions == {"play/subagent[1]"}


def test_duplicate_source_identity_is_rejected():
    tally = Tally()
    row = {
        "source_id": "s:0",
        "flags": [],
        "blocked_by": [],
        "model": None,
        "day": None,
        "file": "f",
        "session_id": "s",
        "input_tokens": 1,
        "output_tokens": 1,
    }
    tally.add(dict(row))
    with pytest.raises(ValueError, match="Duplicate"):
        tally.add(dict(row))


def test_only_unblocked_full_release_is_normalized(tmp_path, monkeypatch):
    rows = [
        {"timestamp": 0, "input_length": 600, "output_length": 2, "hash_ids": [1, 2]},
        {"timestamp": 5, "input_length": 512, "output_length": 0, "hash_ids": [1]},
    ]
    source = tmp_path / "trace.jsonl"
    source.write_text("".join(json.dumps(row) + "\n" for row in rows))
    release = {
        "kind": "mooncake",
        "revision": "r",
        "license": "l",
        "source_block_size": 512,
        "published": {"requests": 2},
        "files": [{"path": "trace.jsonl", "role": "trace"}],
    }
    monkeypatch.setitem(inventory.RELEASES, "fixture", release)
    monkeypatch.setattr(inventory, "fetch", lambda trace: [source])
    monkeypatch.setattr(inventory, "ROOT", tmp_path)
    blocked = inventory.build("fixture", 4 * 512, tmp_path / "blocked", workers=1)
    assert blocked["status"] == "blocked" and blocked["recordings"] == []
    assert blocked["blocker_counts"] == {"zero_output": 1}
    assert blocked["publisher_counts_match"]
    rows[1]["output_length"] = 1
    source.write_text("".join(json.dumps(row) + "\n" for row in rows))
    ready = inventory.build("fixture", 4 * 512, tmp_path / "ready", workers=1)
    assert ready["status"] == "ready" and ready["replayable_requests"] == 2
    (recording,) = ready["recordings"]
    assert (tmp_path / recording["path"]).read_bytes() == source.read_bytes()
    with pytest.raises(ValueError, match="whole 512-token blocks"):
        inventory.build("fixture", 1000, tmp_path / "partial", workers=1)
