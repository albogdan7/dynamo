class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Restrict eviction to the oldest quarter of the inactive queue.
        # Within that window, favor copies with less release history.
        # Counts are physical-copy lifecycle signals, not cache-hit counts.
        oldest = sorted(blocks, key=lambda block: block["lru_rank"])
        window = oldest[:max(1, (len(oldest) + 3) // 4)]
        return min(
            window,
            key=lambda block: (
                block["num_releases"],
                block["lru_rank"],
                block["release_order"],
                block["creation_order"],
            ),
        )["block_id"]
