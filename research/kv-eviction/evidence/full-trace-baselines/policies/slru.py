class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        return min(
            blocks, key=lambda block: (block["hit_count"] >= 2, block["lru_rank"])
        )["block_id"]
