# KV eviction evaluation

Native serving replay; **uncalibrated polynomial timing, not GPU measurements**.
All policies use identical fixed-arrival evaluation data and fresh state.
Native release order has no ties: the leaf-first creation tiebreak is equivalent to native LRU here.

| Metric | native-lru | leaf-first-lru | selected |
| --- | ---: | ---: | ---: |
| Mean TTFT ms | 1316.24 | 1316.24 | 1290.04 |
| p95 TTFT ms | 3218.01 | 3218.01 | 3218.01 |
| Mean ITL ms | 27.8487 | 27.8487 | 27.8048 |
| p95 ITL ms | 34.01 | 34.01 | 34.01 |
| Goodput req/s | 0.105761 | 0.105761 | 0.105794 |
| SLO attainment % | 17.9688 | 17.9688 | 17.9688 |
| Input-token hit % | 37.1234 | 37.1234 | 38.2122 |
| Recomputed input tokens | 1.24181e+06 | 1.24181e+06 | 1.2203e+06 |
| Evictions (512-token blocks) | 2153 | 2153 | 2111 |

ITL pools consecutive output-token gaps; SLO checks each request's p95 gap. Quantiles use nearest rank.
Goodput includes queueing and drain. Zero-output requests are excluded from SLO eligibility; one-token outputs have no ITL samples.
Failures remain in the eligible denominator. Input hit rate uses first admission; recompute includes repeated prefill after preemption.

See manifest.json, requests.jsonl, tokens.jsonl and request-scores.jsonl for provenance and rescoring.

Arrival-time multiplier: 10.0. Source windows, timestamps and exact configuration are recorded in manifest.json.

| Selected vs baseline | Metric | Change |
| --- | --- | ---: |
| native-lru | Mean TTFT ms | -1.99% |
| native-lru | p95 TTFT ms | +0% |
| native-lru | Mean ITL ms | -0.1579% |
| native-lru | p95 ITL ms | +0% |
| native-lru | Goodput req/s | +0.03186% |
| native-lru | SLO attainment % | +0 percentage points |
| native-lru | Input-token hit % | +1.08882 percentage points |
| native-lru | Recomputed input tokens | -1.732% |
| native-lru | Evictions (512-token blocks) | -1.951% |
| leaf-first-lru | Mean TTFT ms | -1.99% |
| leaf-first-lru | p95 TTFT ms | +0% |
| leaf-first-lru | Mean ITL ms | -0.1579% |
| leaf-first-lru | p95 ITL ms | +0% |
| leaf-first-lru | Goodput req/s | +0.03186% |
| leaf-first-lru | SLO attainment % | +0 percentage points |
| leaf-first-lru | Input-token hit % | +1.08882 percentage points |
| leaf-first-lru | Recomputed input tokens | -1.732% |
| leaf-first-lru | Evictions (512-token blocks) | -1.951% |
