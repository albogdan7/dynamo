class EvictionPolicy:
    WINDOW = 4

    def select_victim(self, blocks: list[dict]) -> int:
        # LRU-dominant: among the few oldest candidates, drop the least-used
        # one (single-use blocks first), breaking ties by age.
        oldest = sorted(blocks, key=lambda b: b["lru_rank"])[: self.WINDOW]
        best = min(oldest, key=lambda b: (b["hit_count"], b["lru_rank"]))
        return best["block_id"]
