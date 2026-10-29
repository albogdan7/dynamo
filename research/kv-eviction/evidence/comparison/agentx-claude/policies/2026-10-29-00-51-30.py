class EvictionPolicy:
    WINDOW = 8

    def select_victim(self, blocks: list[dict]) -> int:
        # Among the oldest few inactive blocks, evict the most recently
        # allocated one (likely deeper in a prefix chain), so shared
        # prefixes survive longer. Ties fall back to LRU order.
        window = sorted(blocks, key=lambda b: b["lru_rank"])[: self.WINDOW]
        victim = max(
            window,
            key=lambda b: (b["creation_order"], -b["num_releases"], -b["lru_rank"]),
        )
        return victim["block_id"]
