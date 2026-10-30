class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Limit departures from native recency to a small cold window.
        # Release counts describe this physical copy's history, so cap
        # their influence rather than granting permanent protection.
        oldest = min(block["lru_rank"] for block in blocks)
        cold = [block for block in blocks
                if block["lru_rank"] < oldest + 16]
        # Within the cold window, prefer copies with fewer releases.
        # For equal histories, retain older allocations that have survived
        # longer; allocation order is used only as a relative age signal.
        return min(
            cold,
            key=lambda block: (
                min(block["num_releases"], 4),
                -block["creation_order"],
                block["lru_rank"],
            ),
        )["block_id"]
