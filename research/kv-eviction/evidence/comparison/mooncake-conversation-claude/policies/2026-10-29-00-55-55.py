class EvictionPolicy:
    WINDOW = 512

    def select_victim(self, blocks: list[dict]) -> int:
        # Among the oldest blocks in the inactive queue, evict the most
        # recently allocated one (deepest in a prefix chain), so shared
        # prefix blocks survive longer. Previously reused blocks are kept.
        lo = min(b["lru_rank"] for b in blocks)
        limit = lo + self.WINDOW
        best = None
        best_key = None
        for b in blocks:
            if b["lru_rank"] >= limit:
                continue
            key = (min(b["num_releases"], 2), -b["creation_order"])
            if best_key is None or key < best_key:
                best_key = key
                best = b
        return best["block_id"]
