"""Newest eligible leaf first: a deliberately contrasting integration baseline."""


class EvictionPolicy:
    def select_victim(self, candidates):
        return max(candidates, key=lambda block: block["lru_rank"])["block_id"]
