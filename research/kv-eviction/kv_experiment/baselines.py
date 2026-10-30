"""Fixed, untuned eviction controls over the same native eligible candidates."""

RANDOM_SEEDS = (0, 1, 2)


def minimum(expression):
    return (
        "class EvictionPolicy:\n"
        "    def select_victim(self, blocks: list[dict]) -> int:\n"
        f'        return min(blocks, key=lambda block: {expression})["block_id"]\n'
    )


def policies():
    result = {
        "fifo": {
            "label": "FIFO",
            "source": minimum('block["creation_order"]'),
            "note": "Allocation-order FIFO: evict the oldest physical copy, rather than the oldest cache publication.",
        },
        "mru": {
            "label": "MRU",
            "source": minimum('-block["lru_rank"]'),
            "note": "Evict the newest eligible copy in the native inactive release queue; the opposite end from LRU.",
        },
        "lfu": {
            "label": "LFU",
            "source": minimum('(block["hit_count"], block["lru_rank"])'),
            "note": "Least physical-copy accesses, then native LRU. Initial computed use counts once; each committed cached-prefix activation adds one, including overlapping readers. Canceled reservations do not count. Counts reset on reallocation.",
        },
        "slru": {
            "label": "SLRU",
            "source": minimum('(block["hit_count"] >= 2, block["lru_rank"])'),
            "note": "SGLang-style segmented LRU: copies with at least two actual accesses are protected behind the probationary tier; native LRU within each tier. This is the two-tier hit-threshold variant, not a bounded protected queue.",
        },
    }
    for seed in RANDOM_SEEDS:
        result[f"random-{seed}"] = {
            "label": f"Random {seed}",
            "seed": seed,
            "source": (
                "import random\n\n"
                "class EvictionPolicy:\n"
                "    def __init__(self):\n"
                f"        self.rng = random.Random({seed})\n\n"
                "    def select_victim(self, blocks: list[dict]) -> int:\n"
                '        return self.rng.choice(sorted(blocks, key=lambda block: block["lru_rank"]))["block_id"]\n'
            ),
            "note": f"Uniform random replacement over native eligible candidates, ordered by LRU rank. Fixed seed {seed}; fresh PRNG per replay. All three seeds are reported separately without selecting a best seed.",
        }
    return result


# Python reference for the native "last gap" retention rule; used to check that
# the callback interface reproduces the native implementation exactly.
LAST_GAP_RETENTION = """class EvictionPolicy:
    def __init__(self):
        self.last_completion = {}
        self.last_gap = {}

    def on_pause(self, pause: dict):
        session = pause["session_id"]
        previous = self.last_completion.get(session)
        if previous is not None and pause["arrival_ms"] - previous >= 0:
            self.last_gap[session] = pause["arrival_ms"] - previous
        self.last_completion[session] = pause["now_ms"]
        gap = self.last_gap.get(session)
        return None if gap is None else pause["now_ms"] + gap
"""
