class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Blocks released more than once were reused, so keep them longer.
        # Among equals, evict the oldest in the native queue.
        best = min(
            blocks,
            key=lambda b: (min(b["num_releases"], 2), b["lru_rank"]),
        )
        return best["block_id"]
