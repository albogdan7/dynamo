class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Prefer copies with fewer inactive releases, then newer allocations.
        # Release counts describe physical-copy history, not cache hits;
        # allocation order is used only as a causal ordering signal.
        return min(
            blocks,
            key=lambda block: (
                block["num_releases"],
                -block["creation_order"],
                block["lru_rank"],
            ),
        )["block_id"]
