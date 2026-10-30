class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Follow the native inactive queue without treating lifecycle counters
        # as evidence of reuse frequency or prefix position.
        victim = min(blocks, key=lambda block: block["lru_rank"])
        return victim["block_id"]
