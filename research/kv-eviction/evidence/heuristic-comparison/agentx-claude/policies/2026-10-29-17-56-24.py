class EvictionPolicy:
    WINDOW = 3

    def select_victim(self, blocks: list[dict]) -> int:
        # LRU-dominant: among the oldest candidates, prefer the oldest
        # single-use block (likely a private tail); otherwise plain LRU.
        oldest = sorted(blocks, key=lambda b: b["lru_rank"])[: self.WINDOW]
        for b in oldest:
            if b["hit_count"] <= 1:
                return b["block_id"]
        return oldest[0]["block_id"]
