class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        return min(blocks, key=lambda block: block["lru_rank"])["block_id"]
