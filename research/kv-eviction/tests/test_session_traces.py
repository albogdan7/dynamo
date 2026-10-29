"""Check session semantics against native clocks and actual prefix accounting."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
