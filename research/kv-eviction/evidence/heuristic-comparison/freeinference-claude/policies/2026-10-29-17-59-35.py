class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Native LRU (best in tuning) among the oldest few candidates, preferring
        # the least-used block, then later-created (deeper prefix) on ties.
        window = sorted(blocks, key=lambda b: b["lru_rank"])[:4]
        best = min(
            window,
            key=lambda b: (
                min(b["hit_count"], 2) if b["lru_rank"] > 0 else 0,
                b["lru_rank"],
                -b["creation_order"],
            ),
        )
        return best["block_id"]
