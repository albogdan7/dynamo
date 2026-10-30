class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Restrict competition to the older half of the inactive queue so
        # recently released copies have a bounded period of protection.
        candidates = sorted(blocks, key=lambda block: block["lru_rank"])
        candidates = candidates[: max(1, (len(candidates) + 1) // 2)]

        # Among these older copies, prefer fewer releases and then newer
        # allocations. This favors retained copies over allocation churn;
        # neither counter is treated as a hit count or prefix depth.
        return min(
            candidates,
            key=lambda block: (
                min(block["num_releases"], 3),
                -block["creation_order"],
                block["lru_rank"],
            ),
        )["block_id"]
