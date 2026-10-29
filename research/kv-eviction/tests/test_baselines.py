"""Check heuristic decisions and actual native committed-access accounting."""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kv_experiment.baselines import policies
from kv_experiment.runtime import replay
from kv_experiment.traces import ROOT


def instance(name):
    namespace = {}
    exec(policies()[name]["source"], namespace)  # noqa: S102 - fixed trusted baseline sources, never model submissions
    return namespace["EvictionPolicy"]()


def test_heuristics_choose_distinct_candidates_and_seeded_random_repeats():
    blocks = [
        {"block_id": 10, "creation_order": 3, "lru_rank": 0, "hit_count": 5},
        {"block_id": 20, "creation_order": 1, "lru_rank": 1, "hit_count": 2},
        {"block_id": 30, "creation_order": 2, "lru_rank": 2, "hit_count": 1},
    ]
    assert instance("fifo").select_victim(blocks) == 20
    assert instance("mru").select_victim(blocks) == 30
    assert instance("lfu").select_victim(blocks) == 30
    assert instance("slru").select_victim(blocks) == 30
    # LFU distinguishes protected frequencies; two-tier SLRU instead uses LRU.
    blocks[-1]["hit_count"] = 3
    assert instance("lfu").select_victim(blocks) == 20
    assert instance("slru").select_victim(blocks) == 10
    for seed in (0, 1, 2):
        left, right = instance(f"random-{seed}"), instance(f"random-{seed}")
        assert [left.select_victim(blocks) for _ in range(30)] == [
            right.select_victim(list(reversed(blocks))) for _ in range(30)
        ]


@pytest.mark.skipif(
    os.environ.get("KV_RUN_CONTAINER_TESTS") != "1",
    reason="Native adapter and Docker required",
)
def test_native_access_count_includes_overlapping_readers(tmp_path):
    trace = tmp_path / "trace.jsonl"
    rows = [
        {
            "timestamp": time,
            "input_length": 512 * len(hashes),
            "output_length": 40,
            "hash_ids": hashes,
        }
        for time, hashes in (
            (0, [1, 2]),
            (10000, [1, 2]),
            (10001, [1, 2]),
            (30000, [3, 4, 5, 6, 7, 8]),
        )
    ]
    trace.write_text("".join(json.dumps(row) + "\n" for row in rows))
    config = json.loads((ROOT / "profiles/interactive.json").read_text())
    config["rank"].update(
        num_gpu_blocks=8,
        max_num_seqs=3,
        timing_model={"type": "fixed", "prefill_ms": 10, "decode_ms": 20},
    )
    profile = tmp_path / "config.json"
    profile.write_text(json.dumps(config))
    policy = tmp_path / "policy.py"
    policy.write_text(
        'import sys,json\nclass EvictionPolicy:\n    def select_victim(self, blocks):\n        print(json.dumps(blocks), file=sys.stderr)\n        return min(blocks,key=lambda b:b["lru_rank"])["block_id"]\n'
    )
    result = replay(trace, profile, tmp_path / "replay", policy, 1000, 50)
    assert result["evictions"] > 0
    candidates = [
        row
        for line in (tmp_path / "replay/policy-stderr.txt").read_text().splitlines()
        for row in json.loads(line)
    ]
    assert any(row["hit_count"] == 3 and row["num_releases"] == 2 for row in candidates)
    assert all(row["last_access_order"] > 0 for row in candidates)
