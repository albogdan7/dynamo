from math import log2


class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Exponentially age frequency over one cache capacity of committed
        # block accesses (131072 / 512 = 256 blocks). Doubling physical-copy
        # uses earns 256 accesses of protection, rather than permanent credit.
        # In log space, the current access counter is a common offset and
        # cancels, so neither a clock estimate nor persistent state is needed.
        victim = blocks[0]
        victim_key = None
        for block in blocks:
            priority = (
                block["last_access_order"]
                + 256.0 * log2(max(1, block["hit_count"]))
            )
            key = (priority, block["lru_rank"])
            if victim_key is None or key < victim_key:
                victim = block
                victim_key = key
        return victim["block_id"]
