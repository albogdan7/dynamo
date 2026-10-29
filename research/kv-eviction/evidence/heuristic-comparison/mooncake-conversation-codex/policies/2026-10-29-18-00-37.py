class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Reserve at most three quarters of the inactive candidates for
        # copies with demonstrated reuse. Initial computation is one use;
        # queue releases do not count as evidence of another access.
        reused = [block for block in blocks if block["hit_count"] > 1]
        probation = [block for block in blocks if block["hit_count"] <= 1]
        protected_limit = max(1, 3 * len(blocks) // 4)

        if probation and len(reused) <= protected_limit:
            candidates = probation
        elif reused:
            candidates = reused
        else:
            candidates = blocks

        # Committed access recency allows old popular copies to age out.
        # Native queue rank breaks ties without interpreting opaque IDs.
        return min(
            candidates,
            key=lambda block: (
                block["last_access_order"],
                block["lru_rank"],
            ),
        )["block_id"]
