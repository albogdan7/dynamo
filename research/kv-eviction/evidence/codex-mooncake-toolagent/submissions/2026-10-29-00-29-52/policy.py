class EvictionPolicy:
    def __init__(self):
        pass

    def select_victim(self, blocks: list[dict]) -> int:
        # Use a small probationary segment to absorb one-time allocations.
        # Re-entering the inactive queue earns protection, not a hit count.
        # Bound that protection by reserving only three quarters of the
        # current candidate set for copies with multiple releases.
        protected = [block for block in blocks if block["num_releases"] > 1]
        probation = [block for block in blocks if block["num_releases"] <= 1]
        protected_limit = (3 * len(blocks)) // 4
        if probation and len(protected) <= protected_limit:
            pool = probation
        elif protected:
            pool = protected
        else:
            pool = blocks
        return min(pool, key=lambda block: block["lru_rank"])["block_id"]
