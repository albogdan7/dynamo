# Initial Codex and additional trace comparison

Historical comparison. The [current HTML report](reports/kv-eviction-results.html) uses eight fresh agent searches and the fixed heuristic baselines described in [HEURISTIC_BASELINES.md](HEURISTIC_BASELINES.md).

Completed 2026-10-29. **Codex support and three additional traces are implemented in the fork.** Full metrics are in [results.csv](evidence/comparison/results.csv) and [results.json](evidence/comparison/results.json); source/cohort details are in [TRACE_SOURCES.md](TRACE_SOURCES.md).

## Findings

- The small Mooncake tool/agent gain did **not** generalize to a clear win on the added workloads. Both agents matched LRU on conversation and AgentX. Codex slightly worsened FreeInference mean TTFT; its SLO passing count did not change.
- Neither agent showed a meaningful goodput advantage. All selected policies had unchanged p95 TTFT and p95 ITL relative to LRU on their respective evaluation cohorts.
- These are single fixed-budget pilots under **uncalibrated simulation**, not GPU results or a reliable model ranking. FreeInference evaluation was all-pass under the fixed SLO, limiting discrimination.

## Held-out comparison

Five submitted policies per search, `claude-sonnet-5-5` versus requested Codex model `gpt-6.1-sol`. The comparison contains **40 valid scored submissions across eight searches**: 35 generated for this extension plus the five previously generated Claude tool/agent submissions. Data, settings, budgets, baselines and selection rules match within each pair.

| Trace | LRU mean TTFT (ms) | Claude (ms) | Codex (ms) | SLO passes: LRU / Claude / Codex |
| --- | ---: | ---: | ---: | --- |
| mooncake-toolagent | 1316.24 | 1290.04 | 1290.04 | 46 / 46 / 46 of 256 |
| mooncake-conversation | 3460.40 | 3460.40 | 3460.40 | 83 / 83 / 83 of 256 |
| agentx | 860.17 | 860.17 | 860.17 | 163 / 163 / 163 of 192 |
| freeinference | 86.95 | 86.95 | 87.46 | 33 / 33 / 33 of 33 |

**Tails were identical across all three policies within each trace:**

| Trace | p95 TTFT (ms) | p95 individual-gap ITL (ms) | Goodput, LRU → Claude / Codex (req/s) |
| --- | ---: | ---: | --- |
| mooncake-toolagent | 3218.01 | 34.01 | 0.105760554 → 0.105794248 / 0.105794248 |
| mooncake-conversation | 19887.94 | 34.00 | 0.101215112 → 0.101215112 / 0.101215112 |
| agentx | 5242.19 | 34.01 | 0.0341197008 → 0.0341197008 / 0.0341197008 |
| freeinference | 472.44 | 27.19 | 0.00187989519 → 0.00187989519 / 0.00187989441 |

Tool/agent mean TTFT improved about 1.99%, but goodput improved only 0.03186% because drain shortened slightly; the same 46 requests passed. FreeInference Codex mean TTFT increased about 0.59%, with 80 rather than 78 evictions and 33/33 passing requests. The full CSV also includes mean ITL, token-hit rate, recomputed input tokens and eviction counts.

## Method and limits

- SLO fixed before searches: TTFT ≤ 1,000 ms **and** each request’s p95 individual-token-gap ITL ≤ 50 ms. Nearest-rank percentiles. Selection uses tuning goodput, lower mean TTFT, then earlier submission; evaluation starts fresh. ITL is not average TPOT.
- Mooncake: 256 requests per split, 131,072 cache tokens, explicit 10× inter-arrival timing. AgentX: two complete plays per split, 60/192 requests, 196,608 tokens, serialized top-level plays with native spawn/join. FreeInference: three complete qualified sessions per split, 139/33 requests, 81,920 tokens, observed root arrivals and closed-loop think/tool gaps.
- FreeInference excludes whole sessions with ambiguous zero completion counts, nested subagents or overlapping streams. The selected positive sequential subset is small and biased; full nested FreeInference replay is not claimed. AgentX uses a bounded, filtered subset and an explicitly synthetic inter-play schedule, not the official AgentX benchmark.
- One generic polynomial worker models timing. Source model/provider identities isolate KV reuse, but real model weights/performance and policy Python/IPC overhead are not simulated. Idle time remains in goodput’s denominator; absolute goodput across different traces/cache budgets is not comparable.
- Both agents can edit only their policy workspace and receive tuning feedback. Claude reads through file tools; Codex receives the same current source in its prompt and edits through its patch tool. Codex reports token usage but not a server-resolved model ID or monetary cost; unavailable values remain null. No efficiency or cost ranking is inferred.

## Verification and saved outputs

- **25 tests passed**, including real Codex filesystem/network/configuration isolation and real Docker policy isolation; native completion/tool-delay, spawn/join and 16→512-token prefix accounting checks pass.
- **All eight selected policies reproduce their saved metrics** on fresh replay. Earlier tool/agent results were explicitly checked against the updated wrapper with both binary hashes recorded; its native baseline also matches.
- Independent arithmetic matched **80 saved replays**. Source snapshots match every recorded source-file hash. The comparison generator rejects mismatched workload hashes, settings, SLOs, budgets or baselines.
- Committed compact evidence retains all 40 submitted sources, their scores/usage, source snapshots, audits and replay checks under `evidence/comparison/`. Full raw runs and compressed archives remain local under `experiments/`; mutable agent workspaces are excluded from exports. No failed setup probe received a performance score.

Regenerate the CSV/JSON from the committed evidence without a model or simulator call:

```bash
python3 research/kv-eviction/summarize.py research/kv-eviction/evidence/comparison/runs
```

[README.md](README.md) contains runnable commands and retrieval instructions. Codex is published in [PR #4](https://github.com/albogdan7/dynamo/pull/4); the additional-trace change is [PR #5](https://github.com/albogdan7/dynamo/pull/5). Both target the fork’s `main`; merge them in dependency order after #2 and #3.
