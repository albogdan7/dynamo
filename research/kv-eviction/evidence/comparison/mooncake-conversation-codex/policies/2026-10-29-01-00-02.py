class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Retain earlier allocations across allocation churn. Allocation
        # order is used only as a causal age signal, not as prefix depth.
        # Release count breaks ties without being interpreted as hit count.
        return min(
            blocks,
            key=lambda block: (
                -block["creation_order"],
                block["num_releases"],
                block["lru_rank"],
            ),
        )["block_id"]
