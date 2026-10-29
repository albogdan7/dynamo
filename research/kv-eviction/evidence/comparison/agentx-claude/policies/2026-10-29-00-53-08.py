class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Native LRU is the best measured so far; every larger deviation raised
        # evictions. Only break ties within the two oldest ranks, preferring the
        # most recently created block (likely a deeper, less shareable prefix block).
        oldest = sorted(blocks, key=lambda b: b["lru_rank"])[:2]
        return max(oldest, key=lambda b: (b["creation_order"], -b["lru_rank"]))["block_id"]
