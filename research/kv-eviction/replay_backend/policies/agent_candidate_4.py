"""Evict shallowest-prefix leaves first; break ties by newest recency."""


class EvictionPolicy:
    def select_victim(self, candidates):
        return min(candidates, key=lambda c: (c["prefix_tokens"], -c["lru_rank"]))[
            "block_id"
        ]
