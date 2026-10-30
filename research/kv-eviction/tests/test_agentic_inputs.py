"""Split coverage and session-chain semantics used by the retention oracle."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kv_experiment.agentic import (
    oracle_gaps,
    split_plays,
    unbounded_blocks,
    write_split,
)


def row(request, play, session, deps=(), output=4, hashes=(1,)):
    return {
        "request_id": request,
        "play_id": play,
        "session_id": session,
        "output_length": output,
        "hash_ids": list(hashes),
        "dependencies": [
            {
                "request_id": parent,
                "trigger": "completion",
                "relation": relation,
                "delay_ms": delay,
            }
            for parent, relation, delay in deps
        ],
    }


def test_split_is_seeded_complete_and_content_blind():
    rows = [row(f"r{i}", f"p{i % 7}", "s") for i in range(21)]
    first = split_plays(rows, 0, 3)
    assert len(first["tuning"]) == 3 and len(first["evaluation"]) == 4
    assert set(first["tuning"]) | set(first["evaluation"]) == {
        f"p{i}" for i in range(7)
    }
    # The split depends only on play source order and the seed, not row contents.
    edited = [dict(r, output_length=999, hash_ids=[42]) for r in rows]
    assert split_plays(edited, 0, 3) == first


def test_oracle_follows_sequence_edges_not_file_order():
    rows = [
        row("c", "p", "s", [("b", "sequence", 30.0)]),
        row("a", "p", "s"),
        row("b", "p", "s", [("a", "sequence", 10.0), ("x", "replay_barrier", 0.0)]),
        row("x", "p", "t"),
    ]
    gaps, broken = oracle_gaps(rows)
    assert gaps == {"s": [10.0, 30.0, None], "t": [None]}
    assert broken == []


def test_branching_sessions_are_reported_not_guessed():
    rows = [
        row("a", "p", "s"),
        row("b", "p", "s", [("a", "sequence", 5.0)]),
        row("c", "p", "s", [("a", "sequence", 7.0)]),
    ]
    gaps, broken = oracle_gaps(rows)
    assert gaps == {} and broken == ["s"]


def test_written_split_keeps_plays_contiguous_in_assigned_order(tmp_path):
    rows = [row("a1", "A", "s"), row("b1", "B", "t"), row("a2", "A", "s")]
    path = tmp_path / "split.jsonl"
    write_split(path, {"schema": "x"}, rows, ["B", "A"])
    written = [json.loads(line) for line in path.read_text().splitlines()[1:]]
    assert [r["request_id"] for r in written] == ["b1", "a1", "a2"]
    assert [r["source_play_ordinal"] for r in written] == [0, 1, 1]


def test_unbounded_capacity_covers_distinct_hashes_tails_and_outputs():
    rows = [
        row("a", "p", "s", hashes=(1, 2), output=600),
        row("b", "p", "s", hashes=(1, 3)),
    ]
    assert unbounded_blocks(rows, 512) == 3 + (2 + 2) + (2 + 1)
