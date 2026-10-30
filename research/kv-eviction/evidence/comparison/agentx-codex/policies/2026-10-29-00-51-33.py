class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Give repeatedly released physical copies a bounded second chance.
        # Queue age still dominates once a block becomes sufficiently stale.
        protection = len(blocks) / 4

        def priority(block):
            repeat_releases = min(max(block["num_releases"] - 1, 0), 2)
            rank = block["lru_rank"]
            return (rank + protection * repeat_releases, rank)

        return min(blocks, key=priority)["block_id"]
