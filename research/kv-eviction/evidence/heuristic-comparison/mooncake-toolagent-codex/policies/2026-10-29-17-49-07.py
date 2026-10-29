class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Age frequency credit in committed-access units. Two cachefuls of
        # accesses halve a copy's score, allowing stale popular copies to
        # expire while preserving evidence of repeated use during scans.
        # Retain the largest observed counter when newer copies are active
        # and therefore absent from the candidate list.
        self._access_clock = max(
            getattr(self, "_access_clock", 0),
            max(block["last_access_order"] for block in blocks),
        )
        clock = self._access_clock
        victim = min(
            blocks,
            key=lambda block: (
                max(1, block["hit_count"])
                / (512 + clock - block["last_access_order"]),
                block["last_access_order"],
                block["lru_rank"],
            ),
        )
        return victim["block_id"]
