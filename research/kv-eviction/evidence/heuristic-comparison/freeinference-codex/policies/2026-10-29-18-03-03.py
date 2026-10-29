class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        from math import log2

        # Give demonstrated reuse a bounded recency bonus. Each doubling of
        # physical uses protects a copy by one eighth of the candidate queue,
        # up to half the queue; stale popular copies can still be evicted.
        bonus_scale = len(blocks) / 8.0

        def priority(block):
            reuse = min(4.0, log2(max(1, block["hit_count"])))
            return (
                block["lru_rank"] + bonus_scale * reuse,
                block["last_access_order"],
                block["lru_rank"],
            )

        victim = min(
            blocks,
            key=priority,
        )
        return victim["block_id"]
