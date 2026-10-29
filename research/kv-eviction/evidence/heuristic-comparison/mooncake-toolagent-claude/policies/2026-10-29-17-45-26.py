class EvictionPolicy:
    """Frequency-first eviction: drop the least-used block, oldest access first.

    Tuning showed frequency-aware policies (LFU/SLRU) beat recency-only ones
    on token hit rate, so evict single-use blocks before reused ones.
    """

    def select_victim(self, blocks: list[dict]) -> int:
        best = min(
            blocks,
            key=lambda b: (b["hit_count"], b["last_access_order"], b["lru_rank"]),
        )
        return best["block_id"]
