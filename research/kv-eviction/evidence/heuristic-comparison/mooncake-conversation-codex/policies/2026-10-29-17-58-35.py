from math import log2


class EvictionPolicy:
    def __init__(self):
        self._access_clock = 0

    def select_victim(self, blocks: list[dict]) -> int:
        # Only observed committed accesses advance this logical clock.
        self._access_clock = max(
            self._access_clock,
            max(block["last_access_order"] for block in blocks),
        )
        clock = self._access_clock

        def eviction_key(block):
            # Exponential aging gives each doubling of observed uses one
            # cache-sized interval of protection. Cap the credit so formerly
            # popular copies can eventually leave the cache.
            age = max(0, clock - block["last_access_order"])
            uses = min(16, max(1, block["hit_count"]))
            priority = age - 256 * log2(uses)
            return (priority, -block["lru_rank"])

        return max(blocks, key=eviction_key)["block_id"]
