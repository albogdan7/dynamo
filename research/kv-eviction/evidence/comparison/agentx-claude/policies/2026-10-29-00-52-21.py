class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Tuning feedback shows every deviation from native LRU (release-count
        # windows) increased evictions and recomputed tokens, so evict the
        # oldest inactive block.
        return min(blocks, key=lambda b: b["lru_rank"])["block_id"]
