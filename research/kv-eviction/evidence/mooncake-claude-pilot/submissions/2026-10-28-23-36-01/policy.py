class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Evict least-frequently-released blocks first (num_releases is a reuse
        # frequency signal: blocks released multiple times have proven reuse
        # value and should be retained over cold single-release blocks).
        # Break ties by LRU rank (oldest first) to match native behavior.
        return min(blocks, key=lambda b: (b["num_releases"], b["lru_rank"]))["block_id"]
