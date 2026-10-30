class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Segmented LRU: never-reused blocks go first, then LRU order.
        # Ties on recency prefer the later-created block (deeper prefix).
        best = min(
            blocks,
            key=lambda b: (
                min(b["hit_count"], 2),
                b["lru_rank"],
                -b["creation_order"],
            ),
        )
        return best["block_id"]
