class EvictionPolicy:
    """Evict never-reused blocks by recency; keep proven-reuse blocks longer.

    In multi-turn conversations the next turn re-reads the whole previous
    context, so the most recently released blocks are the likeliest to be hit
    soon. Blocks that were never reused go first, oldest first. Among reused
    blocks, lower hit_count goes first, then oldest access.
    """

    def select_victim(self, blocks: list[dict]) -> int:
        def key(b):
            hits = b["hit_count"]
            if hits > 1:
                return (1, hits, b["last_access_order"], b["lru_rank"])
            return (0, 0, b["last_access_order"], b["lru_rank"])

        return min(blocks, key=key)["block_id"]
