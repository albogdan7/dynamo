import random

class EvictionPolicy:
    def __init__(self):
        self.rng = random.Random(2)

    def select_victim(self, blocks: list[dict]) -> int:
        return self.rng.choice(sorted(blocks, key=lambda block: block["lru_rank"]))["block_id"]
