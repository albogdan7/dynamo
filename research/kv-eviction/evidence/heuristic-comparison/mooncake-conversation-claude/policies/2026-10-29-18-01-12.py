class EvictionPolicy:
    """Protect blocks that have proven reuse; evict the rest by recency.

    Blocks with hit_count > 1 or multiple releases are shared-prefix candidates
    and are kept, ranked by hit_count then recency. Among never-reused blocks,
    the most recently created one is evicted first, since it is the deepest
    (least likely to be a shared prefix) and a prefix hit needs a contiguous
    chain from the start.
    """

    def select_victim(self, blocks: list[dict]) -> int:
        def key(b):
            hits = b["hit_count"]
            if hits > 1 or b["num_releases"] > 1:
                return (1, hits, b["last_access_order"], b["lru_rank"])
            return (0, -b["creation_order"], 0, b["lru_rank"])

        return min(blocks, key=key)["block_id"]
