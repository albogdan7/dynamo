class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Blend native recency with bounded physical-copy release history.
        # Each additional release grants 24 queue positions of protection,
        # up to 72 positions. This is a logical-history heuristic: releases
        # are neither cache-hit counts nor elapsed time.
        # The finite bonus lets sufficiently old copies become evictable
        # even after repeated releases, avoiding permanent protection.
        return min(
            blocks,
            key=lambda block: (
                block["lru_rank"]
                + 24 * max(0, min(block["num_releases"] - 1, 3)),
                block["lru_rank"],
                -block["creation_order"],
            ),
        )["block_id"]
