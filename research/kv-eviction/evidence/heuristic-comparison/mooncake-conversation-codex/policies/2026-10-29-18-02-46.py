from math import log2


class EvictionPolicy:
    def __init__(self):
        self._access_clock = 0

    def select_victim(self, blocks: list[dict]) -> int:
        # Candidate observations provide a causal lower bound on the access
        # clock. Preserve it when the newest observed copy leaves the queue.
        self._access_clock = max(
            self._access_clock,
            max(block["last_access_order"] for block in blocks),
        )
        now = self._access_clock

        # One cache capacity of committed block accesses is the aging scale.
        # Logarithmic frequency rewards actual reuse without allowing a burst
        # of overlapping readers to protect a stale copy indefinitely.
        def eviction_key(block):
            age = now - block["last_access_order"]
            utility = (1.0 + log2(max(1, block["hit_count"]))) / (256 + age)
            return utility, block["last_access_order"], block["lru_rank"]

        return min(blocks, key=eviction_key)["block_id"]
