import math


class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Bound frequency protection to a few positions in the inactive queue.
        # Old popular copies must still age out, and releases are not accesses.
        def priority(block):
            uses = max(1, block["hit_count"])
            protection = 2.0 * math.log2(min(uses, 16))
            return (
                block["lru_rank"] + protection,
                block["last_access_order"],
                block["lru_rank"],
            )

        return min(blocks, key=priority)["block_id"]
