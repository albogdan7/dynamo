class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Follow native inactive-queue recency. Historical use counts do not
        # establish that a physical copy will be needed again soon.
        return min(blocks, key=lambda block: block["lru_rank"])["block_id"]
