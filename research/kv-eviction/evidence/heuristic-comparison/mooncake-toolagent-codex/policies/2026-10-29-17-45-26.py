class EvictionPolicy:
    def __init__(self):
        self._age = 0
        self._entries = {}

    def select_victim(self, blocks: list[dict]) -> int:
        # GreedyDual frequency: eviction pressure ages prior popularity.
        # Allocation identity prevents a recycled block ID inheriting credit.
        victim = None
        victim_key = None
        for block in blocks:
            block_id = block["block_id"]
            creation = block["creation_order"]
            hits = block["hit_count"]
            access = block["last_access_order"]
            previous = self._entries.get(block_id)
            if (previous is None or previous[0] != creation
                    or previous[1] != hits or previous[2] != access):
                priority = self._age + max(1, hits)
                self._entries[block_id] = (creation, hits, access, priority)
            else:
                priority = previous[3]

            key = (priority, block["lru_rank"])
            if victim_key is None or key < victim_key:
                victim = block_id
                victim_key = key

        # The caller supplies at least one eligible candidate.
        self._age = max(self._age, victim_key[0])
        del self._entries[victim]
        return victim
