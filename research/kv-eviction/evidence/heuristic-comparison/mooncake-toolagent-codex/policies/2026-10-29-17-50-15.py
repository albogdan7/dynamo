class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Preserve evidence of actual reuse across scans. Saturating the
        # frequency prevents very popular copies from gaining unlimited
        # priority; native inactive-queue recency breaks frequency ties.
        victim = min(
            blocks,
            key=lambda block: (
                min(8, max(1, block["hit_count"])),
                block["lru_rank"],
            ),
        )
        return victim["block_id"]
