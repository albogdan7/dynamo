class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Keep recency dominant: consider only a small band at the old end
        # of the inactive queue. Within that band, reclaim newer allocations
        # first. Allocation order is a heuristic, not a measure of depth.
        oldest_rank = min(block["lru_rank"] for block in blocks)
        window = max(1, len(blocks) // 8)
        cutoff = oldest_rank + window
        victim = min(
            (block for block in blocks if block["lru_rank"] < cutoff),
            key=lambda block: (-block["creation_order"], block["lru_rank"]),
        )
        return victim["block_id"]
