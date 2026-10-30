"""Hand-checked prefix-hit and eviction semantics of the timing-free cache model."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kv_experiment.cachesim import Cache, hit_ratio


def test_hits_count_only_the_cached_prefix():
    cache = Cache(None, "lru")
    assert cache.request([1, 2, 3]) == 0
    assert cache.request([1, 2, 4]) == 2
    # Block 3 is cached, but the prefix stops at the first miss.
    assert cache.request([9, 3]) == 0


def test_lru_and_lfu_choose_different_victims():
    rows = [[1], [1], [2], [3], [1]]
    lru, lfu = Cache(2, "lru"), Cache(2, "lfu")
    hits = [[c.request(row) for row in rows] for c in (lru, lfu)]
    # LRU keeps {2, 3} after the fourth request and misses block 1.
    assert hits[0] == [0, 1, 0, 0, 0]
    # LFU protects twice-used block 1 and hits it.
    assert hits[1] == [0, 1, 0, 0, 1]


def test_length_aware_evicts_later_positions_among_equal_counts():
    cache = Cache(2, "length_aware")
    cache.request([1, 2])
    cache.request([3])  # evicts block 2 (deeper position), not block 1
    assert set(cache.blocks) == {1, 3}


def test_ratio_uses_all_request_blocks():
    rows = [{"hash_ids": [1, 2]}, {"hash_ids": [1, 2]}]
    assert hit_ratio(rows, None, "lru") == 0.5
