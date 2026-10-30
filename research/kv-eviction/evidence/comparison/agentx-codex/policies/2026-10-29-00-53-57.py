class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Limit allocation-age preference to the oldest eighth of the queue.
        # This gives older surviving copies modest protection while allowing
        # inactive copies to age out regardless of their allocation history.
        # Allocation order is only a lifecycle signal, not prefix depth.
        window = max(1, len(blocks) // 8)
        oldest = sorted(blocks, key=lambda block: block["lru_rank"])[:window]
        victim = min(
            oldest,
            key=lambda block: (-block["creation_order"], block["lru_rank"]),
        )
        return victim["block_id"]
