class EvictionPolicy:
    def __init__(self):
        pass

    def select_victim(self, blocks: list[dict]) -> int:
        # Bound the second chance by queue position. A copy that has
        # re-entered the inactive queue gets a quarter-queue recency bonus,
        # but an old protected copy can still lose to a recent new copy.
        # Release counts are lifecycle evidence, not hit-frequency estimates.
        protection = max(1, len(blocks) // 4)
        return min(
            blocks,
            key=lambda block: (
                block["lru_rank"]
                + protection * (block["num_releases"] > 1),
                block["lru_rank"],
            ),
        )["block_id"]
