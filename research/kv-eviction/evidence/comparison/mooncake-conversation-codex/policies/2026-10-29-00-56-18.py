class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Prefer evicting copies with fewer inactive releases. Releases are
        # physical-copy lifecycle evidence, not request hits. Cap their weight
        # so heavily recycled copies do not accumulate unlimited protection.
        # Native queue order resolves ties without interpreting block IDs or
        # allocation counters as content or prefix depth.
        return min(
            blocks,
            key=lambda block: (min(block["num_releases"], 3), block["lru_rank"]),
        )["block_id"]
