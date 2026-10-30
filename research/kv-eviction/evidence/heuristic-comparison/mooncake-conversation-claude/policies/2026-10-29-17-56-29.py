class EvictionPolicy:
    """Evict never-reused blocks first (oldest first); keep proven-shared blocks.

    Blocks with higher hit_count are likely shared prefixes (e.g. system
    prompts or earlier conversation turns), so they are evicted last. Ties
    fall back to native LRU order.
    """

    def select_victim(self, blocks: list[dict]) -> int:
        best = min(blocks, key=lambda b: (b["hit_count"], b["lru_rank"]))
        return best["block_id"]
