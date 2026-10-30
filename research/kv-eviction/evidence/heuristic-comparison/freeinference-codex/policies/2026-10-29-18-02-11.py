class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Restrict frequency protection to the oldest quarter of the native
        # inactive queue. Recently released copies get a reuse opportunity,
        # while a stale popular copy cannot receive unlimited protection.
        ordered = sorted(blocks, key=lambda block: block["lru_rank"])
        window = max(1, (len(ordered) + 3) // 4)
        victim = min(
            ordered[:window],
            key=lambda block: (block["hit_count"], block["lru_rank"]),
        )
        return victim["block_id"]
