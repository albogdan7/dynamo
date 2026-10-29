class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Retain older physical allocations by evicting the newest eligible
        # allocation. Allocation order is only a causal counter; this policy
        # does not interpret it as prefix depth or infer content from block IDs.
        # Resolve equal allocation counters using the native inactive queue.
        return max(
            blocks,
            key=lambda block: (block["creation_order"], -block["lru_rank"]),
        )["block_id"]
