class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        return min(blocks, key=lambda block: (block["hit_count"], block["lru_rank"]))[
            "block_id"
        ]
