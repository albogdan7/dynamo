"""Evict deepest-prefix leaves first; break ties by oldest recency."""


class EvictionPolicy:
    def select_victim(self, candidates):
        return min(candidates, key=lambda c: (-c["prefix_tokens"], c["lru_rank"]))[
            "block_id"
        ]
