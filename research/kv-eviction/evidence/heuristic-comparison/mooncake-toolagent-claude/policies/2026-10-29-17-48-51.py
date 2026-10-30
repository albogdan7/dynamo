class EvictionPolicy:
    """LFU with recency-weighted frequency.

    Plain LFU/SLRU gave the best token hit rate in tuning, but frequent blocks
    that went cold stay forever. Scale hit_count by relative queue recency so
    stale, frequently used blocks become evictable while hot ones stay.
    Ties go to the oldest in the native inactive queue.
    """

    def select_victim(self, blocks: list[dict]) -> int:
        n = len(blocks)
        top = max(b["lru_rank"] for b in blocks) or 1

        def score(b):
            recency = 0.5 + 0.5 * b["lru_rank"] / top
            return (b["hit_count"] * recency, b["lru_rank"])

        return min(blocks, key=score)["block_id"]
