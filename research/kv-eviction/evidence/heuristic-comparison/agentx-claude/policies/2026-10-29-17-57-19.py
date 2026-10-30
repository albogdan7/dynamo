class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # LRU-dominant: previous window variants matched or trailed native LRU,
        # so only deviate when the oldest block is clearly hot and its
        # neighbour was used once.
        oldest = sorted(blocks, key=lambda b: b["lru_rank"])[:2]
        first = oldest[0]
        if len(oldest) > 1 and first["hit_count"] >= 5 and oldest[1]["hit_count"] <= 1:
            return oldest[1]["block_id"]
        return first["block_id"]
