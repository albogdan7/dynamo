class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # A second inactive release is evidence that a cached copy survived
        # long enough to be used again. Give those copies a protected tier,
        # while retaining native recency ordering within each tier.
        return min(
            blocks,
            key=lambda block: (
                block["num_releases"] > 1,
                block["lru_rank"],
            ),
        )["block_id"]
