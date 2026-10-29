class EvictionPolicy:
    WINDOW = 2

    def select_victim(self, blocks: list[dict]) -> int:
        # LRU-dominant: only look at the two oldest candidates and skip the
        # oldest if it has been reused while its neighbour was never reused.
        oldest = sorted(blocks, key=lambda b: b["lru_rank"])[: self.WINDOW]
        first = oldest[0]
        if len(oldest) > 1 and first["hit_count"] > 2 and oldest[1]["hit_count"] <= 1:
            return oldest[1]["block_id"]
        return first["block_id"]
