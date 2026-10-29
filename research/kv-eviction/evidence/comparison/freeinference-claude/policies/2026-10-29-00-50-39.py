class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        # Consider only the oldest quarter of the inactive queue so recency still
        # matters. Within it, evict the deepest block of a chain (latest created),
        # which keeps shared prefixes alive; break ties toward fewer releases, then
        # oldest.
        ordered = sorted(blocks, key=lambda b: b["lru_rank"])
        window = ordered[: max(1, len(ordered) // 4)]
        best = min(
            window,
            key=lambda b: (-b["creation_order"], b["num_releases"], b["lru_rank"]),
        )
        return best["block_id"]
