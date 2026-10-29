class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Primary: evict least-reused blocks first.
        # Tiebreak: among equal num_releases, evict newest-created block —
        # older blocks are more likely to be shared common prefixes in a
        # repetitive flat workload, so we preserve them over recent unique blocks.
        return min(blocks, key=lambda b: (b["num_releases"], -b["creation_order"]))["block_id"]
