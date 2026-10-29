class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Limit protection to a small band at the old end of the queue.
        # Repeated inactive releases provide a bounded retention preference;
        # they are lifecycle events, not hit counts. Equal preferences use LRU.
        oldest_rank = min(block["lru_rank"] for block in blocks)
        window = max(1, len(blocks) // 8)
        cutoff = oldest_rank + window
        victim = min(
            (block for block in blocks if block["lru_rank"] < cutoff),
            key=lambda block: (
                min(block["num_releases"], 3),
                block["lru_rank"],
            ),
        )
        return victim["block_id"]
