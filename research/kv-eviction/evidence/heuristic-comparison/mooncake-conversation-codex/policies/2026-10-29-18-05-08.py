class EvictionPolicy:
    def __init__(self):
        self._access_clock = 0

    def select_victim(self, blocks: list[dict]) -> int:
        # Candidate observations provide a causal lower bound on the access
        # clock. Preserve it when the newest observed copy leaves the queue.
        self._access_clock = max(
            self._access_clock,
            max(block["last_access_order"] for block in blocks),
        )
        now = self._access_clock

        # Protect recently reused copies in a bounded segment. Actual accesses,
        # rather than inactive-queue releases, provide the reuse evidence.
        # Expire protection after two cache capacities of observed accesses.
        reused = [
            block for block in blocks
            if block["hit_count"] > 1
            and now - block["last_access_order"] <= 512
        ]
        reused.sort(
            key=lambda block: (
                block["last_access_order"], -block["lru_rank"]
            ),
            reverse=True,
        )

        # Reserve at most half the cache for reuse protection, and always
        # leave a current candidate available for eviction.
        limit = min(128, len(blocks) - 1)
        protected = {block["block_id"] for block in reused[:limit]}
        victim = min(
            (block for block in blocks if block["block_id"] not in protected),
            key=lambda block: (block["lru_rank"], block["last_access_order"]),
        )
        return victim["block_id"]
