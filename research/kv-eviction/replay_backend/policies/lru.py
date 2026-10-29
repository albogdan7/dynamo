"""Oldest eligible leaf first; rank 0 is the engine's LRU victim."""


class EvictionPolicy:
    def select_victim(self, candidates):
        return min(candidates, key=lambda block: block["lru_rank"])["block_id"]

