class EvictionPolicy:
    """Evict single-use blocks first, deepest chain block first; keep reused blocks.

    Reused blocks (hit_count > 1) are ranked by hit_count, then recency, so
    frequently shared prefixes survive. Single-use blocks from the same
    request are evicted from the tail (latest creation) because a prefix hit
    needs a contiguous chain from the start.
    """

    def select_victim(self, blocks: list[dict]) -> int:
        def key(b):
            hits = b["hit_count"]
            if hits > 1:
                return (1, hits, b["last_access_order"], 0)
            return (0, 0, 0, -b["creation_order"])

        return min(blocks, key=key)["block_id"]
