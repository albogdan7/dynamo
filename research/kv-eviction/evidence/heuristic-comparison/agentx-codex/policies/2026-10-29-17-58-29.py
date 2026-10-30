class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Release order can lag committed access when readers overlap.
        # Correct that ordering only near the oldest end of the native queue.
        oldest_rank = min(block["lru_rank"] for block in blocks)
        cutoff = oldest_rank + 7
        return min(
            (block for block in blocks if block["lru_rank"] <= cutoff),
            key=lambda block: (
                block["last_access_order"],
                block["lru_rank"],
            ),
        )["block_id"]
