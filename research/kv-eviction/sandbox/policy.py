"""
KV Cache Eviction Policy
========================
Edit this file to implement your eviction policy.

Your goal: reduce mean_ttft_ms and increase overall_hit_rate.
Run `python submit.py` to evaluate your policy and see the metrics.

HOW IT WORKS
------------
When the KV cache is full and a new block needs to be stored, the simulator
calls `select_victim(blocks)` to ask your policy which block to evict.

You return the block_id of the block you want to remove.

WHAT YOU CAN SEE (the `blocks` argument)
-----------------------------------------
Each block is a dict with these fields:

    block_id         (int)   unique ID for this block
    num_hits         (int)   how many times a request reused this block
    last_access_time (float) simulated time this block was last used
    creation_time    (float) simulated time this block entered the cache
    size_tokens      (int)   number of tokens in this block (always 16)

WHAT YOU CANNOT SEE
--------------------
- Future requests
- The actual token content of blocks
- The evaluator or trace code

BASELINE SCORE (LRU)
---------------------
The default policy below is LRU. It scores roughly:
    mean_ttft_ms       ~53 ms
    overall_hit_rate   ~0.60

Beat it.
"""


class EvictionPolicy:

    def select_victim(self, blocks: list[dict]) -> int:
        """
        Choose one block to evict from the cache.

        Parameters
        ----------
        blocks : list of dict
            All currently evictable blocks (see fields above).

        Returns
        -------
        int
            The block_id of the block to evict.
        """
        # DEFAULT: Least Recently Used — evict the block accessed longest ago.
        return min(blocks, key=lambda b: b["last_access_time"])["block_id"]
