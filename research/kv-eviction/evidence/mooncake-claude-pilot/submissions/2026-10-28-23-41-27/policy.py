class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        return min(blocks, key=lambda b: (b["num_releases"], b["lru_rank"]))["block_id"]
