# KV eviction evaluation

Native serving replay; **uncalibrated polynomial timing, not GPU measurements**.
All policies use identical fixed-arrival evaluation data and fresh state.
Native release order has no ties: the leaf-first creation tiebreak is equivalent to native LRU here.

| Metric | native-lru | leaf-first-lru | selected |
| --- | ---: | ---: | ---: |
| Mean TTFT ms | 271987 | 271987 | 271156 |
| p95 TTFT ms | 494530 | 494530 | 493171 |
| Mean ITL ms | 39.3699 | 39.3699 | 39.295 |
| p95 ITL ms | 115.883 | 115.883 | 115.355 |
| Goodput req/s | 0.00276688 | 0.00276688 | 0.00277241 |
| SLO attainment % | 0.2 | 0.2 | 0.2 |
| Input-token hit % | 29.6446 | 29.6446 | 30.4763 |
| Recomputed input tokens | 5.72076e+06 | 5.72076e+06 | 5.65333e+06 |
| Evictions (512-token blocks) | 10820 | 10820 | 10688 |

ITL pools consecutive output-token gaps; SLO checks each request's p95 gap. Quantiles use nearest rank.
Goodput includes queueing and drain. Zero-output requests are excluded from SLO eligibility; one-token outputs have no ITL samples.
Failures remain in the eligible denominator. Input hit rate uses first admission; recompute includes repeated prefill after preemption.

See manifest.json, requests.jsonl, tokens.jsonl and request-scores.jsonl for provenance and rescoring.
