class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Evict blocks that were never reused (fewest releases) first; blocks
        # that have cycled through the inactive queue repeatedly are likely
        # shared prefixes. Break ties by native LRU order.
        return min(
            blocks, key=lambda b: (b["num_releases"], b["lru_rank"])
        )["block_id"]
