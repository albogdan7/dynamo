class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Protect repeatedly used physical copies from one-use scans. Bound
        # frequency credit so formerly popular copies eventually compete on
        # recency with other established copies. Lifecycle release counts do
        # not imply accesses and are deliberately excluded.
        victim = min(
            blocks,
            key=lambda block: (
                min(max(1, block["hit_count"]), 4),
                block["last_access_order"],
                block["lru_rank"],
            ),
        )
        return victim["block_id"]
