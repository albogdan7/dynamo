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

        def retention_key(block):
            # A cache-sized grace interval limits sensitivity to the ordering
            # of accesses within a batch. Repeated uses earn protection, which
            # gradually expires as other committed accesses are observed.
            age = max(0, clock - block["last_access_order"])
            value = max(1, block["hit_count"]) / (256 + age)
            return (value, block["lru_rank"])

        return min(blocks, key=retention_key)["block_id"]
