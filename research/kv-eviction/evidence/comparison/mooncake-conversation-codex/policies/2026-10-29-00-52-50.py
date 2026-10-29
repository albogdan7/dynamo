class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Release counters describe physical-copy reuse, not token hit counts.
        # Give repeatedly released copies a bounded retention bonus, measured
        # in logical release events. Aging lets old copies lose that bonus.
        newest_release = max(block["release_order"] for block in blocks)
        horizon = max(1, len(blocks))

        def priority(block):
            age = newest_release - block["release_order"]
            reuse = min(3, max(0, block["num_releases"] - 1))
            return (
                age - horizon * reuse,
                -block["lru_rank"],
            )

        return max(blocks, key=priority)["block_id"]
