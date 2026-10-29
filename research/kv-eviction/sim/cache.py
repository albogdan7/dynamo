"""
Prefix-cache simulator.

Replays a request trace against a fixed-capacity block cache, calling the
agent's eviction policy whenever a new block cannot fit. Computes serving
metrics that the agent uses to improve its policy.

INTEGRITY RULE: the policy's select_victim() only receives the current cache
state — never future requests, trace data, or simulator internals.
"""

from __future__ import annotations

import time
import statistics
from dataclasses import dataclass, field
from typing import Any

BLOCK_SIZE = 16          # tokens per cache block (must match trace.py)
BASE_TTFT_MS = 50.0      # fixed execution overhead to first token (ms)
RECOMPUTE_MS = 3.0       # added per missed block (ms); models GPU recompute cost


# ---------------------------------------------------------------------------
# Internal block representation (never exposed to the policy directly)
# ---------------------------------------------------------------------------

@dataclass
class _Block:
    block_id: int
    token_key: tuple          # the actual token sequence (not shown to policy)
    creation_time: float
    last_access_time: float
    num_hits: int = 0
    size_tokens: int = BLOCK_SIZE

    def to_candidate(self) -> dict:
        """Convert to the dict the policy receives. No token content, no future info."""
        return {
            "block_id":         self.block_id,
            "num_hits":         self.num_hits,
            "last_access_time": self.last_access_time,
            "creation_time":    self.creation_time,
            "size_tokens":      self.size_tokens,
        }


# ---------------------------------------------------------------------------
# Prefix cache
# ---------------------------------------------------------------------------

class PrefixCache:
    def __init__(self, capacity_blocks: int, policy):
        self.capacity = capacity_blocks
        self.policy = policy
        self._store: dict[tuple, _Block] = {}   # token_key → Block
        self._next_id = 0
        self.eviction_count = 0
        self._invalid_policy_fallbacks = 0

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def process_request(self, tokens: list[int], arrival_time: float) -> dict:
        """
        Process one request at service start. Returns execution-only metrics.

        ``arrival_time`` is the cache access timestamp; the queue runner passes
        service start here, not the original external arrival time.

        Radix-style prefix matching: walk token blocks left-to-right,
        stop at the first miss. All blocks from that miss onward are added
        to the cache (triggering evictions as needed).
        """
        blocks = _tokenize(tokens)
        hit_count = 0

        for i, key in enumerate(blocks):
            if key in self._store:
                blk = self._store[key]
                blk.last_access_time = arrival_time
                blk.num_hits += 1
                hit_count += 1
            else:
                # Radix rule: first miss breaks the prefix chain.
                # Add this block and all subsequent ones.
                for missed_key in blocks[i:]:
                    if missed_key not in self._store:
                        self._add_block(missed_key, arrival_time)
                break

        miss_count = len(blocks) - hit_count
        ttft_ms = BASE_TTFT_MS + miss_count * RECOMPUTE_MS

        return {
            "hit_blocks":  hit_count,
            "miss_blocks": miss_count,
            "ttft_ms":     ttft_ms,
            "hit_rate":    hit_count / max(len(blocks), 1),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _add_block(self, key: tuple, t: float):
        if len(self._store) >= self.capacity:
            self._evict(t)
        blk = _Block(
            block_id=self._next_id,
            token_key=key,
            creation_time=t,
            last_access_time=t,
        )
        self._next_id += 1
        self._store[key] = blk

    def _evict(self, current_time: float):
        candidates = [b.to_candidate() for b in self._store.values()]
        if not candidates:
            return

        # Call the agent's policy — only give it the safe candidate dicts.
        try:
            victim_id = self.policy.select_victim(candidates)
        except Exception:
            victim_id = None

        # Find the block with that ID.
        victim_key = next(
            (k for k, b in self._store.items() if b.block_id == victim_id),
            None,
        )

        if victim_key is None:
            # Invalid or crashed policy: fall back to evicting the oldest block.
            self._invalid_policy_fallbacks += 1
            victim_key = min(self._store, key=lambda k: self._store[k].last_access_time)

        del self._store[victim_key]
        self.eviction_count += 1


# ---------------------------------------------------------------------------
# Simulation runner
# ---------------------------------------------------------------------------

def run(trace: list[dict], policy, capacity_blocks: int = 256) -> dict:
    """
    Replay *trace* through a PrefixCache of *capacity_blocks* using *policy*.

    Model one FIFO server occupied until each request's first token. Cache
    accesses happen at service start; the next request cannot use the cache
    until the preceding request finishes this execution stage. TTFT includes
    queueing. This deliberately excludes batching and subsequent decode work,
    so throughput measures first-token completions, not full generations.

    Trace arrivals must be nondecreasing, in seconds. Ties retain input order.
    Returns a metrics dict suitable for writing to results/.
    """
    if not trace:
        raise ValueError("trace must contain at least one request")
    if any(b["arrival_time"] < a["arrival_time"] for a, b in zip(trace, trace[1:])):
        raise ValueError("trace must be ordered by arrival_time")
    cache = PrefixCache(capacity_blocks=capacity_blocks, policy=policy)

    per_request = []
    wall_start = time.perf_counter()
    server_available_s = trace[0]["arrival_time"]

    for req in trace:
        arrival_s = req["arrival_time"]
        start_s = max(arrival_s, server_available_s)
        result = cache.process_request(req["input_tokens"], start_s)
        execution_ms = result["ttft_ms"]
        waiting_ms = (start_s - arrival_s) * 1000.0
        server_available_s = start_s + execution_ms / 1000.0
        result["waiting_ms"] = waiting_ms
        result["execution_ms"] = execution_ms
        result["ttft_ms"] = waiting_ms + execution_ms
        result["request_id"] = req["id"]
        per_request.append(result)

    wall_elapsed = time.perf_counter() - wall_start

    ttfts      = [r["ttft_ms"]   for r in per_request]
    waits      = [r["waiting_ms"] for r in per_request]
    executions = [r["execution_ms"] for r in per_request]
    hit_rates  = [r["hit_rate"]  for r in per_request]
    hit_blocks = sum(r["hit_blocks"]  for r in per_request)
    miss_blocks= sum(r["miss_blocks"] for r in per_request)

    return {
        "timing_model":         "fifo_single_server_first_token_v1",
        "num_requests":         len(trace),
        "capacity_blocks":      capacity_blocks,
        "evictions":            cache.eviction_count,
        "invalid_policy_fallbacks": cache._invalid_policy_fallbacks,
        # TTFT
        "mean_ttft_ms":         round(statistics.mean(ttfts), 2),
        "p50_ttft_ms":          round(statistics.median(ttfts), 2),
        "p95_ttft_ms":          round(_percentile(ttfts, 95), 2),
        "p99_ttft_ms":          round(_percentile(ttfts, 99), 2),
        "mean_waiting_ms":      round(statistics.mean(waits), 2),
        "p95_waiting_ms":       round(_percentile(waits, 95), 2),
        "mean_execution_ms":    round(statistics.mean(executions), 2),
        # Hit rate
        "overall_hit_rate":     round(hit_blocks / max(hit_blocks + miss_blocks, 1), 4),
        "mean_request_hit_rate":round(statistics.mean(hit_rates), 4),
        # First-token completions over the interval including queue drain.
        "sim_throughput_rps":   round(len(trace) / (server_available_s - trace[0]["arrival_time"]), 2),
        # Wall time (how fast the sim ran)
        "sim_wall_time_s":      round(wall_elapsed, 3),
    }


def _tokenize(tokens: list[int]) -> list[tuple]:
    """Split token list into fixed-size block keys."""
    n = (len(tokens) // BLOCK_SIZE) * BLOCK_SIZE
    return [tuple(tokens[i:i + BLOCK_SIZE]) for i in range(0, n, BLOCK_SIZE)]


def _percentile(data: list[float], p: int) -> float:
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = (p / 100) * (len(sorted_data) - 1)
    lo, hi = int(idx), min(int(idx) + 1, len(sorted_data) - 1)
    return sorted_data[lo] + (sorted_data[hi] - sorted_data[lo]) * (idx - lo)
