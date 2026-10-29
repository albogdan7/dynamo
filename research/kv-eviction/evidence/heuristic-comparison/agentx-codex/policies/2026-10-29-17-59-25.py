class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Keep native recency dominant, with a bounded retention bonus for
        # confirmed physical-copy reuse. Saturation prevents old popular
        # copies from remaining protected indefinitely.
        return min(
            blocks,
            key=lambda block: (
                block["lru_rank"]
                + 4 * min(2, max(0, block["hit_count"] - 1)),
                block["lru_rank"],
            ),
        )["block_id"]
