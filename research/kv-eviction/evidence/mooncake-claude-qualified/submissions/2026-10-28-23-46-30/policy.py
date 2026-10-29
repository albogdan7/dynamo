class EvictionPolicy:
    WINDOW = 32

    def select_victim(self, blocks: list[dict]) -> int:
        # Lowest release count first (never-reused blocks). Among those, look
        # at the oldest LRU window and evict the most recently created block,
        # i.e. the deepest suffix of a chain, so shared prefixes survive.
        low = min(b["num_releases"] for b in blocks)
        pool = [b for b in blocks if b["num_releases"] == low]
        pool.sort(key=lambda b: b["lru_rank"])
        window = pool[: self.WINDOW]
        return max(window, key=lambda b: b["creation_order"])["block_id"]
