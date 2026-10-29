# KV eviction policy research

- **[AISimulate replay backend](replay_backend/README.md):** working native
  SGLang-style replay with a custom Python leaf-eviction hook, baseline policies,
  deterministic pressure fixture, and integration tests. Start here for the
  proposal's simulation-backed policy evaluation.
- **[Legacy sandbox](sandbox/README.md):** original Python policy exercise using
  `sim/cache.py`. Its hand-written FIFO queue and fixed recomputation costs are
  illustrative; it does not use Dynamo/AISimulate scheduling.

The new backend proves custom decisions affect the real simulation cache.
Its sample timing is uncalibrated, and GPU validation, production workloads,
untrusted-code confinement, and RL training remain future work.
