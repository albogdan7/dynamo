# Full-trace KV eviction baselines: results

Run `2026-10-30-12-14-44`, audited 2026-10-30. Specification: [FULL_TRACE_BASELINE_SPEC.md](FULL_TRACE_BASELINE_SPEC.md). Generated outputs: [HTML report](reports/full-trace-baselines.html), [CSV](reports/full-trace-baselines.csv) and [compact evidence](evidence/full-trace-baselines/). Every number below comes from that evidence. These are uncalibrated simulation results, not GPU measurements.

## Answer

**On the two workloads that can be replayed completely, the choice of eviction heuristic changes cache effectiveness modestly at most, and simulated TTFT and ITL by less than 1%. No heuristic changes the SLO outcome.**

- **Mooncake tool/agent.** Input-token hit rate ranges from 24.94% (Random) to 28.83% (LFU), against 26.23% for LRU. LFU and SLRU recompute about 3.5% fewer input tokens and evict about 3.5% fewer blocks. Downstream, mean TTFT changes by −0.71% (LFU) to +0.45% (Random 0), mean ITL by −0.56% to +0.44%, and p95 ITL by −0.52% to +0.33%.
- **Mooncake conversation.** The hit rate is 4.25–4.27% for every policy. LFU and SLRU made exactly LRU's choices: their raw outputs are byte-identical. The other heuristics are slightly worse than LRU: at most +0.22% mean TTFT and +0.47% p95 ITL.
- **SLO.** Every policy passes exactly 1 of 23,608 tool/agent requests and 1 of 12,031 conversation requests. At the recorded arrival rate, this single simulated worker is overloaded, so SLO attainment cannot separate the policies.
- **AgentX and FreeInference were not replayed.** Under the frozen capacities, 39% and 66.5% of their requests cannot fit in the cache. Both also contain ambiguous zero-output counts, and FreeInference has nested and overlapping requests the adapter cannot represent. Both traces are blocked rather than filtered (see [Blocked traces](#blocked-traces)).

The spec's decision rule asks for a substantial, reproducible latency or SLO improvement before pursuing agent optimization; fewer evictions alone are not enough. No heuristic shows a substantial improvement here, even though KV pressure is verified (next section). By the spec's own wording, this means **limited demonstrated headroom for this policy interface under this protocol**. It does not prove that an agent cannot improve it.

## Is KV capacity under pressure?

Yes, and the pressure comes from active memory, not just a cache full of reclaimable blocks. Values are LRU's, duration-weighted over the whole run. Every other policy's occupancy shares are within 0.2 percentage points of these; its queue and preemption counts are in the report.

| | Tool/agent | Conversation |
| --- | ---: | ---: |
| Physical occupancy (active + inactive), mean | 99.51% | 99.72% |
| Active (referenced or reserved) occupancy, mean | 85.27% | 83.53% |
| Time with active ≥ 95% of capacity | 32.42% | 27.64% |
| Time with active at 100% of capacity | 3.04% | 3.09% |
| Inactive (evictable) share, mean | 14.24% | 16.19% |
| Running requests, mean (limit 32) | 9.2 | 8.6 |
| Waiting requests, mean / sampled peak | 10,079 / 19,876 | 5,153 / 10,125 |
| Preemptions | 372 | 330 |
| Replay span for a 3,537 s trace | 18,811 s | 18,610 s |

Interpretation:

1. **A heuristic can only choose among inactive blocks.** On average that is 14–16% of capacity. The active 84–85% is outside the policy interface.
2. **The worker is far past saturation.** Requests wait about 2.2 hours on average (mean TTFT 8,031 s on tool/agent), and draining the trace takes about 5.3× its recorded duration. Only about 9 requests run at once, well below the 32-sequence limit, while active KV fills most of the cache. Concurrency therefore appears bounded by KV capacity. Better reuse of inactive blocks saves prefill work: LFU's −3.5% recomputation shortens the replay by 0.6% (112 s). That saving is small next to the hours of queueing that dominate TTFT.
3. **The tool/agent differences are systematic, not concentrated in one period.** LFU's hit-rate lead over LRU stays between +2.41 and +2.76 percentage points in every 10-minute arrival window. The three random seeds lie within 0.02 percentage points of each other.

## Results

Changes are relative to LRU on the same trace: percentage points for hit rate, relative % otherwise. The HTML report has every metric, including occupancy p95/peaks, queue statistics and per-window tables.

### Mooncake tool/agent (23,608 requests, 131,072-token cache)

| Policy | Input-token hit rate | Recomputed input tokens | Evicted blocks | Mean TTFT | p95 TTFT | Mean ITL | p95 ITL | SLO passes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| LRU | 26.23% | 149.9 M | 290,475 | 8,031 s | 14,615 s | 39.64 ms | 116.03 ms | 1 / 23,608 |
| FIFO | 26.49% (+0.26 pp) | 150.6 M (+0.45%) | 291,791 (+0.45%) | 8,038 s (+0.08%) | 14,631 s (+0.11%) | 39.71 ms (+0.16%) | 116.08 ms (+0.04%) | 1 / 23,608 |
| MRU | 25.37% (−0.86 pp) | 152.8 M (+1.93%) | 296,101 (+1.94%) | 8,060 s (+0.36%) | 14,675 s (+0.41%) | 39.81 ms (+0.41%) | 116.34 ms (+0.27%) | 1 / 23,608 |
| LFU | 28.83% (+2.61 pp) | 144.6 M (−3.52%) | 280,154 (−3.55%) | 7,974 s (−0.71%) | 14,509 s (−0.73%) | 39.42 ms (−0.56%) | 115.43 ms (−0.52%) | 1 / 23,608 |
| SLRU | 28.68% (+2.45 pp) | 144.9 M (−3.32%) | 280,767 (−3.34%) | 7,977 s (−0.67%) | 14,515 s (−0.69%) | 39.43 ms (−0.53%) | 115.46 ms (−0.49%) | 1 / 23,608 |
| Random 0 | 24.94% (−1.29 pp) | 153.3 M (+2.25%) | 297,055 (+2.27%) | 8,068 s (+0.45%) | 14,686 s (+0.48%) | 39.82 ms (+0.44%) | 116.39 ms (+0.31%) | 1 / 23,608 |
| Random 1 | 24.96% (−1.27 pp) | 153.1 M (+2.17%) | 296,816 (+2.18%) | 8,066 s (+0.43%) | 14,684 s (+0.47%) | 39.81 ms (+0.42%) | 116.36 ms (+0.28%) | 1 / 23,608 |
| Random 2 | 24.94% (−1.28 pp) | 153.2 M (+2.19%) | 296,877 (+2.20%) | 8,066 s (+0.43%) | 14,684 s (+0.47%) | 39.82 ms (+0.44%) | 116.41 ms (+0.33%) | 1 / 23,608 |

### Mooncake conversation (12,031 requests, 131,072-token cache)

| Policy | Input-token hit rate | Recomputed input tokens | Evicted blocks | Mean TTFT | p95 TTFT | Mean ITL | p95 ITL | SLO passes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| LRU | 4.27% | 138.8 M | 272,671 | 7,970 s | 14,412 s | 38.35 ms | 111.66 ms | 1 / 12,031 |
| FIFO | 4.27% (+0.00 pp) | 140.3 M (+1.07%) | 275,568 (+1.06%) | 7,987 s (+0.21%) | 14,443 s (+0.22%) | 38.42 ms (+0.19%) | 112.18 ms (+0.46%) | 1 / 12,031 |
| MRU | 4.25% (−0.02 pp) | 140.4 M (+1.12%) | 275,717 (+1.12%) | 7,988 s (+0.22%) | 14,445 s (+0.23%) | 38.42 ms (+0.20%) | 112.19 ms (+0.47%) | 1 / 12,031 |
| LFU | identical to LRU | | | | | | | |
| SLRU | identical to LRU | | | | | | | |
| Random 0 | 4.26% (−0.02 pp) | 139.7 M (+0.61%) | 274,330 (+0.61%) | 7,980 s (+0.11%) | 14,430 s (+0.13%) | 38.39 ms (+0.11%) | 111.90 ms (+0.21%) | 1 / 12,031 |
| Random 1 | 4.25% (−0.02 pp) | 139.7 M (+0.68%) | 274,516 (+0.68%) | 7,981 s (+0.13%) | 14,432 s (+0.14%) | 38.39 ms (+0.12%) | 111.95 ms (+0.25%) | 1 / 12,031 |
| Random 2 | 4.25% (−0.02 pp) | 139.7 M (+0.65%) | 274,422 (+0.64%) | 7,980 s (+0.13%) | 14,431 s (+0.14%) | 38.39 ms (+0.11%) | 111.88 ms (+0.19%) | 1 / 12,031 |

On conversation, FIFO's first-admission hit rate matches LRU's, yet it recomputes 1.07% more tokens. Recomputation also counts prefill repeated after preemption, and FIFO preempts slightly more (333 versus 330).

## Verification

All 26 replays completed with no failures, retries or substitute scores. The `audit` command re-verified every job's inputs and recomputed its metrics and occupancy from raw files, without the experiment modules.

- **Determinism.** A second native LRU replay is byte-identical to the first (requests, tokens, evictions, occupancy).
- **Callback parity.** LRU implemented as a Python callback in the policy container is byte-identical to native LRU after about 290,000 (tool/agent) and 273,000 (conversation) container decisions.
- **Instrumentation parity.** Native LRU without telemetry produces byte-identical request, token and eviction records.
- **Conservation.** `active + inactive + free = capacity` holds at every telemetry sample of every instrumented run, using the engine's own free list.
- **Coverage.** Each run contains every source request exactly once, and all requests completed. Telemetry preemptions equal request readmissions.
- **Sampling error.** On the first 2,000 requests, a 10 ms cadence changes any occupancy statistic by at most 0.14 (tool/agent) and 0.02 (conversation) percentage points versus the frozen 100 ms cadence.
- **Report integrity.** The HTML and CSV are generated only from checksummed evidence, and regenerating them is byte-stable.

## Blocked traces

Neither trace was replayed. Filtering would select sessions by cache fit or output, which the spec forbids. Exact counts are in [TRACE_SOURCES.md](TRACE_SOURCES.md#full-release-coverage) and the report's coverage table.

| Trace | Source requests | Exceed frozen cache | Other blockers | Tokens needed to fit every request |
| --- | ---: | ---: | --- | ---: |
| AgentX | 98,827 | 38,806 of 196,608 tokens (39.3%) | 41 zero outputs | 996,864 |
| FreeInference | 1,222,199 | 813,184 of 81,920 tokens (66.5%) | 4,727 zero completions (4,403 on tool-call responses); 35,617 nested; 17 overlapping | 1,260,032 |

Raising capacity alone would not unblock either trace. Unblocking needs explicit decisions on capacity, zero-output semantics, and FreeInference's nested and overlapping requests, all frozen before any scoring.

## Limitations

- Timing is the pinned engine's uncalibrated polynomial model on one illustrative vLLM worker. Absolute TTFTs reflect that worker's overload at Mooncake's recorded cluster-scale arrival rate. Only the comparison across policies is meaningful.
- The SLO fails for every policy, so these runs cannot show SLO benefits. Following the spec, timestamps and thresholds were not changed to produce a ranking.
- Each workload is one deterministic recording per policy. Repeats establish reproducibility, not statistical independence.
- The heuristics see only native inactive candidates and their lifecycle and access counters. These results bound what such fixed rules achieve here, not what other interfaces (admission, scheduling, offload) or an agent might achieve.
- Earlier subset pilots used a 10× slower arrival rate and are not comparable. The PR #5 pilot saw SLRU about 2% below LRU in mean TTFT. Whether a non-saturated load turns the tool/agent hit-rate gains into larger latency gains is an open question for a separately frozen protocol; it was not tested here.

## Reproduce

The commands, outputs and resume behavior are in the [README](README.md#full-trace-heuristic-baselines). This sweep took about one hour on an 18-core machine with eight parallel jobs, mostly Docker round trips for the callback policies. To regenerate the report from the committed evidence without simulation:

```bash
python3 research/kv-eviction/export_report.py \
  --baseline-evidence research/kv-eviction/evidence/full-trace-baselines \
  --output research/kv-eviction/reports/full-trace-baselines.html
```
