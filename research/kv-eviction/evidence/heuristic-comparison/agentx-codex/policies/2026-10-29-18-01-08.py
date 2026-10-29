class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Retain recency as the primary signal, with a bounded allowance for
        # demonstrated reuse. Initial computation alone earns no allowance.
        # Saturation prevents historically hot copies from staying forever.
        def priority(block):
            reuses = max(0, block["hit_count"] - 1)
            allowance = min(4, reuses)
            return (block["lru_rank"] + allowance, block["lru_rank"])

        return min(blocks, key=priority)["block_id"]
