class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Among the oldest few inactive blocks, evict the deepest chain block
        # (latest created) first so shared prefixes survive, preferring blocks
        # released only once, then the oldest.
        ordered = sorted(blocks, key=lambda b: b["lru_rank"])
        window = ordered[: max(1, min(8, len(ordered) // 4))]
        best = min(
            window,
            key=lambda b: (b["num_releases"] > 1, -b["creation_order"], b["lru_rank"]),
        )
        return best["block_id"]
