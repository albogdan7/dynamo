class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        return min(blocks, key=lambda block: block["creation_order"])["block_id"]
