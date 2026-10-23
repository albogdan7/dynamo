# KV Cache Eviction Policy — Agent Sandbox

Your task: write a KV cache eviction policy that minimizes latency
when serving LLM requests.

## Background

An LLM server caches computed "key-value" tensors so it can reuse
work across requests that share a common prefix (e.g. the same system
prompt). When the cache is full, a block must be evicted to make room.
Which block you evict determines how often future requests hit the cache
vs. paying the cost of recomputation.

A cache **hit** → fast response (~50 ms).
A cache **miss** → slow response (~50 ms + 3 ms per missed block).

## Your job

Edit **`policy.py`** — specifically the `select_victim(blocks)` method.

```
policy.py     ← EDIT THIS
submit.py     ← run this to evaluate (do not edit)
results/      ← your scores appear here after each run
```

## The API

```python
class EvictionPolicy:
    def select_victim(self, blocks: list[dict]) -> int:
        ...
```

`blocks` is a list of every currently evictable block. Each block is:

| Field              | Type  | Meaning                                      |
|--------------------|-------|----------------------------------------------|
| `block_id`         | int   | Unique ID — **return this to evict the block** |
| `num_hits`         | int   | Times this block was reused by a request      |
| `last_access_time` | float | Simulated time it was last touched (seconds)  |
| `creation_time`    | float | Simulated time it entered the cache (seconds) |
| `size_tokens`      | int   | Always 16                                     |

You **must** return a `block_id` that appears in the list.

## How to evaluate

```bash
python submit.py
```

This prints your scores and writes a JSON file to `results/`.

## Metrics (lower TTFT = better, higher hit rate = better)

| Metric              | What it means                                    |
|---------------------|--------------------------------------------------|
| `overall_hit_rate`  | Fraction of cache lookups that were hits         |
| `mean_ttft_ms`      | Average time-to-first-token (lower = better)     |
| `p95_ttft_ms`       | 95th-percentile TTFT (tail latency)              |
| `evictions`         | Total evictions triggered                        |

## Baseline (LRU)

The default policy evicts the least-recently-used block:

```
overall_hit_rate  ~0.60
mean_ttft_ms      ~53 ms
p95_ttft_ms       ~62 ms
```

Beat it. Ideas to try:

- **Frequency-based**: prefer evicting blocks with low `num_hits`
- **Hybrid LFU-LRU**: weight both recency and frequency
- **Recency + frequency decay**: combine `last_access_time` and `num_hits`
  with a decay factor so old high-hit blocks don't get permanent protection
- **Size-aware**: all blocks are the same size here, but you could experiment

## Rules

- Only edit `policy.py`.
- `select_victim` must return a `block_id` from the provided list.
- You cannot access future requests, trace data, or the simulator internals.
- You can add helper methods or state to `EvictionPolicy` (e.g. a counter).
