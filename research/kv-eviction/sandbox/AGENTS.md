# KV Cache Eviction Policy Sandbox

You are an agent competing to write the best KV cache eviction policy.

**Your only job:** edit `policy.py` to minimize `mean_ttft_ms` and maximize `overall_hit_rate`.

## Workflow

1. Read `README.md` for the full task description and API.
2. Edit `policy.py` — only the `select_victim(blocks)` method matters.
3. Run `python submit.py` to evaluate and see your scores.
4. Iterate.

## Rules

- Only edit `policy.py`.
- Do not edit `submit.py`, `policy_runner.py`, or anything in `../sim/`.
- `select_victim` must return a `block_id` from the list it receives.
- You cannot access the trace data, future requests, or simulator internals.

## Goal

Beat the LRU baseline:

```
mean_ttft_ms      ~60 ms   (lower is better)
overall_hit_rate  ~75%     (higher is better)
```
