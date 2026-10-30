class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Frequency-tiered LRU: blocks with fewer physical uses go first,
        # then least recently accessed. Ties prefer the later-created block
        # (deeper prefix), keeping shared roots resident.
        best = min(
            blocks,
            key=lambda b: (
                min(b["hit_count"], 3),
                b["last_access_order"],
                -b["creation_order"],
                b["lru_rank"],
            ),
        )
        return best["block_id"]
