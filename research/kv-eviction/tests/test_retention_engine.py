"""Native retention hooks: LRU equivalence, callback parity and oracle protection."""

import copy
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kv_experiment.baselines import LAST_GAP_RETENTION, policies
from kv_experiment.runtime import NATIVE, replay
from kv_experiment.traces import ROOT

pytestmark = pytest.mark.skipif(not NATIVE.exists(), reason="Build the native adapter")
containers = pytest.mark.skipif(
    os.environ.get("KV_RUN_CONTAINER_TESTS") != "1", reason="Docker required"
)
RAW = ("requests.jsonl", "tokens.jsonl")


def profile(tmp_path, name, trace_format, blocks, research=None):
    config = json.loads((ROOT / "profiles/interactive.json").read_text())
    config["rank"].update(
        num_gpu_blocks=blocks,
        max_num_seqs=4,
        timing_model={"type": "fixed", "prefill_ms": 10, "decode_ms": 20},
    )
    config["trace"] = {"format": trace_format, "source_block_size": 512}
    if research is not None:
        config["research_policy"] = research
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(config))
    return path


def same_outputs(left: Path, right: Path) -> bool:
    return all((left / f).read_bytes() == (right / f).read_bytes() for f in RAW)


@pytest.fixture
def mooncake(tmp_path):
    rows = [
        {
            "timestamp": i * 15,
            "input_length": 512 * len(h),
            "output_length": 3,
            "hash_ids": h,
        }
        for i, h in enumerate(
            [[1, 2], [1, 2, 3], [4, 5], [1, 2], [6, 7, 8], [4, 5, 9]] * 4
        )
    ]
    path = tmp_path / "mooncake.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


def agentic(tmp_path, rows):
    header = {
        "schema": "dynamo.agentic_mooncake",
        "version": 2,
        "block_size": 512,
        "hash_id_scope": "local",
        "source": {"format": "test", "digest": "0" * 64},
    }
    path = tmp_path / "agentic.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in [header, *rows]))
    return path


def request(request_id, session, hashes, deps=(), not_before=0.0, output=2):
    return {
        "request_id": request_id,
        "play_id": "play",
        "session_id": session,
        "model": "m",
        "input_length": 512 * len(hashes),
        "output_length": output,
        "hash_ids": hashes,
        "not_before_ms": not_before,
        "dependencies": [
            {
                "request_id": p,
                "trigger": "completion",
                "relation": "sequence",
                "delay_ms": d,
            }
            for p, d in deps
        ],
    }


@pytest.fixture
def returning_session(tmp_path):
    """A finishes first and returns after 1 s; C finishes later and never
    returns; B then needs room. LRU evicts A's older blocks, the oracle C's."""
    return agentic(
        tmp_path,
        [
            request("a0", "A", [1, 2, 3]),
            request("c0", "C", [7, 8, 9], not_before=200.0),
            request("b0", "B", [4, 5, 6], not_before=500.0),
            request("a1", "A", [1, 2, 3, 10], deps=[("a0", 1000.0)]),
        ],
    )


def test_protection_walk_without_protections_is_lru(tmp_path, mooncake):
    # Mooncake rows have no sessions, so nothing is protected and the
    # protection-aware walk must pick the LRU head every time.
    plain = profile(tmp_path, "plain", "mooncake", 6)
    walk = profile(
        tmp_path, "walk", "mooncake", 6, {"retention": {"policy": "last_gap"}}
    )
    lru = replay(mooncake, plain, tmp_path / "lru", None, 1000, 50)
    protected = replay(mooncake, walk, tmp_path / "walk-run", None, 1000, 50)
    assert lru["evictions"] > 0
    assert same_outputs(tmp_path / "lru", tmp_path / "walk-run")
    assert protected["evictions"] == lru["evictions"]


def test_oracle_protects_a_returning_session(tmp_path, returning_session):
    gaps = tmp_path / "gaps.json"
    gaps.write_text(json.dumps({"A": [1000.0, None], "B": [None], "C": [None]}))
    oracle = {
        "retention": {"policy": "oracle", "oracle_gaps": str(gaps), "slack_ms": 0}
    }
    lru = replay(
        returning_session,
        profile(tmp_path, "lru", "agentic_mooncake", 8),
        tmp_path / "lru",
        None,
        1000,
        50,
    )
    replay(
        returning_session,
        profile(tmp_path, "oracle", "agentic_mooncake", 8, oracle),
        tmp_path / "oracle",
        None,
        1000,
        50,
    )
    assert lru["evictions"] > 0

    def reuse(directory):
        rows = (tmp_path / directory / "requests.jsonl").read_text().splitlines()
        (a1,) = [json.loads(r) for r in rows if json.loads(r)["request_id"] == "a1"]
        return a1["admission_history"][0]["reused_input_tokens"]

    # LRU evicts part of A's older prefix; the oracle evicts C's instead.
    assert reuse("oracle") == 3 * 512 > reuse("lru")


@containers
def test_native_lfu_matches_python_callback(tmp_path, mooncake):
    native = profile(tmp_path, "native", "mooncake", 6, {"order": "lfu"})
    plain = profile(tmp_path, "plain", "mooncake", 6)
    source = tmp_path / "lfu.py"
    source.write_text(policies()["lfu"]["source"])
    replay(mooncake, native, tmp_path / "native-run", None, 1000, 50)
    replay(mooncake, plain, tmp_path / "callback-run", source, 1000, 50)
    assert same_outputs(tmp_path / "native-run", tmp_path / "callback-run")


@containers
def test_native_last_gap_matches_python_callback(tmp_path, returning_session):
    rows = [json.loads(line) for line in returning_session.read_text().splitlines()]
    extra = copy.deepcopy(rows[-1])
    extra.update(request_id="a2", hash_ids=[1, 2, 3, 10, 11], input_length=5 * 512)
    extra["dependencies"] = [
        {
            "request_id": "a1",
            "trigger": "completion",
            "relation": "sequence",
            "delay_ms": 900.0,
        }
    ]
    trace = agentic(tmp_path, [*rows[1:], extra])
    native = profile(
        tmp_path, "native", "agentic_mooncake", 8, {"retention": {"policy": "last_gap"}}
    )
    callback = profile(
        tmp_path,
        "callback",
        "agentic_mooncake",
        8,
        {"retention": {"policy": "callback"}},
    )
    source = tmp_path / "last_gap.py"
    source.write_text(LAST_GAP_RETENTION)
    replay(trace, native, tmp_path / "native-run", None, 1000, 50)
    result = replay(trace, callback, tmp_path / "callback-run", source, 1000, 50)
    assert result["policy_calls"] == 5  # one decision per completed request
    assert same_outputs(tmp_path / "native-run", tmp_path / "callback-run")
