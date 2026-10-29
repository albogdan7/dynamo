class EvictionPolicy:
    def __init__(self):
        self._release_clock = 0

    def select_victim(self, blocks: list[dict]) -> int:
        # Release counters provide a causal recency signal. Repeated releases
        # warrant bounded protection, but are not treated as cache-hit counts.
        self._release_clock = max(
            self._release_clock,
            max(block["release_order"] for block in blocks),
        )

        def eviction_priority(block):
            age = self._release_clock - block["release_order"] + 1
            protection = min(4, max(1, block["num_releases"]))
            return (age / protection, -block["lru_rank"])

        return max(blocks, key=eviction_priority)["block_id"]
