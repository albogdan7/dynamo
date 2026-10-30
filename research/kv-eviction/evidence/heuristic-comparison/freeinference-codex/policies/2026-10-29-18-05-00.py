class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Protect recently used copies with demonstrated reuse. Limit the
        # protected segment so old popularity cannot lock up the cache.
        # Release counts are lifecycle events, not accesses.
        protected_limit = (3 * len(blocks)) // 4
        reused = sorted(
            (block for block in blocks if block["hit_count"] > 1),
            key=lambda block: (
                block["last_access_order"],
                block["lru_rank"],
            ),
            reverse=True,
        )
        protected = {
            block["block_id"] for block in reused[:protected_limit]
        }

        # Native queue order breaks ties and supplies recency for probation.
        # IDs are used only for membership and for returning the victim.
        return min(
            (block for block in blocks if block["block_id"] not in protected),
            key=lambda block: block["lru_rank"],
        )["block_id"]
