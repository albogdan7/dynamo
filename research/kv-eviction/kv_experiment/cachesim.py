"""Timing-free prefix-cache simulation: hit ratio versus capacity and policy.

This checks the cache model against Mooncake's published trace analysis
(Qin et al., FAST'25, arXiv 2407.00079v3, Table 1) before any serving
experiment relies on it. Requests are processed in arrival order with no
queueing. A request hits the longest prefix of its block hashes that is
cached, then every block it uses becomes resident; capacity counts blocks.
"""

import heapq
import json
import math
from collections import OrderedDict
from pathlib import Path

# Published Table 1: block hit ratio on the 23,608-request tool/agent trace.
MOONCAKE_TABLE_1 = {
    "source": "Mooncake (FAST'25), arXiv 2407.00079v3, Table 1",
    "capacities": [None, 100_000, 50_000, 30_000, 10_000, 1_000],
    "lru": [0.51, 0.51, 0.50, 0.48, 0.40, 0.30],
    "lfu": [0.51, 0.51, 0.49, 0.43, 0.35, 0.30],
    "length_aware": [0.51, 0.50, 0.48, 0.42, 0.35, 0.30],
}
POLICIES = ("lru", "lfu", "length_aware")


class Cache:
    """Block cache whose victim minimizes a policy key; ties go to LRU."""

    def __init__(self, capacity: int | None, policy: str):
        self.capacity, self.policy = capacity, policy
        self.clock = 0
        self.blocks = OrderedDict()  # hash -> (count, deepest position, last use)
        self.heap = []

    def key(self, block):
        count, position, last_use = self.blocks[block]
        if self.policy == "lru":
            return (last_use,)
        if self.policy == "lfu":
            return (count, last_use)
        # Our reading of LengthAwareCache: LFU, evicting later-position blocks first.
        return (count, -position, last_use)

    def touch(self, block, position: int) -> None:
        self.clock += 1
        count, deepest, _ = self.blocks.pop(block, (0, 0, 0))
        self.blocks[block] = (count + 1, max(deepest, position), self.clock)
        heapq.heappush(self.heap, (self.key(block), block))

    def evict(self) -> None:
        while True:
            key, block = heapq.heappop(self.heap)
            if block in self.blocks and self.key(block) == key:
                del self.blocks[block]
                return

    def request(self, hashes: list[int]) -> int:
        hits = 0
        for block in hashes:
            if block not in self.blocks:
                break
            hits += 1
        for position, block in enumerate(hashes):
            self.touch(block, position)
        while self.capacity is not None and len(self.blocks) > self.capacity:
            self.evict()
        # Keep the lazy heap bounded relative to the live block set.
        if len(self.heap) > 4 * max(len(self.blocks), 1) + 1024:
            self.heap = [(self.key(b), b) for b in self.blocks]
            heapq.heapify(self.heap)
        return hits


def hit_ratio(rows: list[dict], capacity: int | None, policy: str) -> float:
    cache = Cache(capacity, policy)
    hits = total = 0
    for row in rows:
        hits += cache.request(row["hash_ids"])
        total += len(row["hash_ids"])
    return hits / total


def validate(trace: Path) -> dict:
    """Reproduce Table 1's grid; report differences without fitting anything."""
    rows = [json.loads(line) for line in trace.read_text().splitlines()]
    result = {
        "trace_requests": len(rows),
        "hit_definition": "longest cached prefix of each request's 512-token block hashes / all request blocks",
        "published": MOONCAKE_TABLE_1,
        "simulated": {},
    }
    for policy in POLICIES:
        result["simulated"][policy] = [
            hit_ratio(rows, capacity, policy)
            for capacity in MOONCAKE_TABLE_1["capacities"]
        ]
    differences = [
        abs(simulated - published)
        for policy in POLICIES
        for simulated, published in zip(
            result["simulated"][policy], MOONCAKE_TABLE_1[policy], strict=True
        )
    ]
    result["max_absolute_difference"] = max(differences)
    result["lru_at_least_lfu_everywhere"] = all(
        lru >= lfu - 1e-12
        for lru, lfu in zip(
            result["simulated"]["lru"], result["simulated"]["lfu"], strict=True
        )
    )
    assert not any(math.isnan(d) for d in differences)
    return result
