class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Rank in committed-access order, so release batching does not make
        # an old access appear recent. A bounded frequency bonus protects
        # reused copies without letting historical popularity pin them.
        ordered = sorted(blocks, key=lambda block: (
            block["last_access_order"], -block["lru_rank"]
        ))
        size = len(ordered)

        def retention(item):
            rank, block = item
            reuses = max(0, block["hit_count"] - 1)
            bonus = min(reuses, 4) * size / 16.0
            return (rank + bonus, block["lru_rank"],
                    -block["creation_order"])

        return min(enumerate(ordered), key=retention)[1]["block_id"]
