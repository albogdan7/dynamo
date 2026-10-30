class EvictionPolicy:
    """Frequency-first eviction with deepest-first tie-breaking.

    Evict the least-used blocks first (LFU/SLRU did best on token hit rate).
    Among equally used blocks of similar age, evict the most recently created
    one: later-allocated blocks tend to be deeper in a prefix chain, so
    dropping them first keeps shared prefixes reusable.
    """

    def select_victim(self, blocks: list[dict]) -> int:
        best = min(
            blocks,
            key=lambda b: (
                b["hit_count"],
                b["lru_rank"] // 32,
                -b["creation_order"],
            ),
        )
        return best["block_id"]
