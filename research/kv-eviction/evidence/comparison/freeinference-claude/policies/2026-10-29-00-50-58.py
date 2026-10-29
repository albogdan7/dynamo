class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Within the oldest half of the inactive queue, evict blocks that were
        # released only once (never proven reusable) first, preferring the deepest
        # chain block (latest created), then the oldest.
        ordered = sorted(blocks, key=lambda b: b["lru_rank"])
        window = ordered[: max(1, len(ordered) // 2)]
        best = min(
            window,
            key=lambda b: (b["num_releases"], -b["creation_order"], b["lru_rank"]),
        )
        return best["block_id"]
