class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Under thrashing, LRU yields almost no hits. Protect blocks that
        # have already been reused (num_releases > 1), and among the rest
        # evict the most recently released block (MRU) so older, possibly
        # shared prefix blocks stay resident.
        best = None
        best_key = None
        for b in blocks:
            key = (min(b["num_releases"], 2), -b["release_order"])
            if best_key is None or key < best_key:
                best_key = key
                best = b
        return best["block_id"]
