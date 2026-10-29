class EvictionPolicy:
    def __init__(self):
        pass

    def select_victim(self, blocks: list[dict]) -> int:
        # Give copies that have re-entered the inactive queue a second
        # chance. Release counts describe physical-copy lifecycle events,
        # so use only a binary distinction, not a frequency estimate.
        # Prefer the oldest probationary copy whenever one is available;
        # otherwise let native recency age the protected copies out.
        return min(
            blocks,
            key=lambda block: (
                block["num_releases"] > 1,
                block["lru_rank"],
            ),
        )["block_id"]
