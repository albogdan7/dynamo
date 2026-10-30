class EvictionPolicy:
    WINDOW = 16

    def select_victim(self, blocks: list[dict]) -> int:
        # Among the oldest inactive blocks, evict one that has been released
        # the fewest times (never-reused copies first); blocks that were
        # already reused are likely shared prefixes. Ties fall back to LRU.
        window = sorted(blocks, key=lambda b: b["lru_rank"])[: self.WINDOW]
        victim = min(window, key=lambda b: (b["num_releases"], b["lru_rank"]))
        return victim["block_id"]
