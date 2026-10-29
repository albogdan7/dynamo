class EvictionPolicy:
    """Protect reused blocks; among the rest, evict the deepest chain blocks first.

    Prefix reuse needs a contiguous chain from the start, so within a request
    the later-created (deeper) blocks are the least valuable. Blocks that have
    been reused (hit_count > 1) are evicted last, oldest access first.
    """

    def select_victim(self, blocks: list[dict]) -> int:
        def key(b):
            if b["hit_count"] > 1:
                return (1, b["last_access_order"], 0)
            return (0, 0, -b["creation_order"])

        return min(blocks, key=key)["block_id"]
