class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # LRU-anchored: among the oldest few candidates, evict the least-reused
        # block (shared prefixes accumulate hits), then the deeper/later-created
        # block (leaf-like), then the older one.
        window = sorted(blocks, key=lambda b: b["lru_rank"])[:16]
        best = min(
            window,
            key=lambda b: (
                min(b["hit_count"], 2),
                -b["creation_order"],
                b["lru_rank"],
            ),
        )
        return best["block_id"]
