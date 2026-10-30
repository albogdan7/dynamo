class EvictionPolicy:
    def __init__(self):
        pass

    def select_victim(self, blocks: list[dict]) -> int:
        # Give copies that re-entered the inactive queue a bounded second
        # chance. Use queue ranks, since release counters are logical events
        # rather than elapsed time or request-frequency measurements.
        # Half a candidate queue of protection still allows these copies
        # to age out instead of accumulating permanent frequency credit.
        protection = max(1, len(blocks) // 2)
        return min(
            blocks,
            key=lambda block: (
                block["lru_rank"]
                + protection * (block["num_releases"] > 1),
                block["lru_rank"],
            ),
        )["block_id"]
