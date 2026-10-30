class EvictionPolicy:
    """LFU with LRU tie-breaking.

    Tuning feedback showed LFU/SLRU gave the best token hit rate, while
    bucketed deepest-first tie-breaking was slightly worse. Evict the least
    used block; among equals, evict the oldest in the native inactive queue.
    """

    def select_victim(self, blocks: list[dict]) -> int:
        best = min(blocks, key=lambda b: (b["hit_count"], b["lru_rank"]))
        return best["block_id"]
