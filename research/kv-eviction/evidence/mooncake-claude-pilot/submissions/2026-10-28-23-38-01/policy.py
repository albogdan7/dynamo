class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Evict cold (low num_releases) blocks first; among equal frequency,
        # evict the block idle longest by absolute release_order rather than
        # queue-position lru_rank — more precise for warm multi-release blocks.
        return min(blocks, key=lambda b: (b["num_releases"], b["release_order"]))["block_id"]
