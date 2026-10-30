class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        from math import log2

        # Age in committed-access events captures recency even when inactive
        # releases are delayed by overlapping readers. This is a logical age,
        # not a prediction of elapsed time or a measure of prefix depth.
        newest_access = max(block["last_access_order"] for block in blocks)

        def priority(block):
            # Bound frequency protection so historically popular copies can
            # age out. Releases do not count as evidence of cache reuse.
            reuse = min(4.0, log2(max(1, block["hit_count"])))
            age = newest_access - block["last_access_order"] + 1
            return (
                age / (1.0 + reuse),
                -block["lru_rank"],
            )

        victim = max(
            blocks,
            key=priority,
        )
        return victim["block_id"]
