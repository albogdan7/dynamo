class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Restrict to the older half of the inactive queue so recency still matters,
        # then prefer blocks released least often (likely unshared tails), and among
        # those the most recently created (deeper in a prefix chain), then oldest.
        ordered = sorted(blocks, key=lambda b: b["lru_rank"])
        window = ordered[: max(1, (len(ordered) + 1) // 2)]
        best = min(
            window,
            key=lambda b: (b["num_releases"], -b["creation_order"], b["lru_rank"]),
        )
        return best["block_id"]
