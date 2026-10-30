# Agentic KV retention: results

Run `2026-10-30-18-02-25`, audited 2026-10-30. Protocol: [AGENTIC_RETENTION_SPEC.md](AGENTIC_RETENTION_SPEC.md), frozen before any policy was compared. Generated outputs: [HTML report](reports/agentic-retention.html), [CSV](reports/agentic-retention.csv) and [compact evidence](evidence/agentic-retention/). Every number below comes from that evidence. These are uncalibrated simulation results, not GPU measurements.

## Answer

**Negative under the frozen rule.** On the evaluation split, no retention or eviction policy cuts mean request latency by 5% versus LRU at any load level. Neither does the oracle bound by 10%; at most it improves latency by 0.19%. The verdict is limited demonstrated headroom for this retention interface.

**Capacity, not retention order, dominates.** An unbounded cache, also a bound, cuts mean request latency by 70–99%.

Change in mean request latency versus LRU on the evaluation split (262 plays, 74,031 requests). Positive means faster.

| Policy | 8 plays | 32 plays | 128 plays |
| --- | ---: | ---: | ---: |
| LRU | 21.19 s | 214.93 s | 724.89 s |
| LFU | −1.33% | +0.84% | +0.34% |
| SLRU | −0.21% | +0.29% | +0.17% |
| Random 0 | −17.23% | −1.22% | −0.57% |
| Last gap | −0.27% | −0.58% | +0.14% |
| Global median | −0.56% | −0.58% | +0.14% |
| Continuum TTL | −0.12% | −0.27% | +0.16% |
| Oracle (bound) | +0.07% | +0.15% | +0.19% |
| Unbounded cache (bound) | +69.91% | +96.97% | +98.89% |

The tuning split, which is not used for the decision, shows the same pattern. Its largest non-oracle gain is +1.69% (global median, 32 plays). The oracle reaches +3.65% there, still below the 10% threshold.

## What happens in the cache

| Evaluation split | 8 plays | 32 plays | 128 plays |
| --- | ---: | ---: | ---: |
| Input-token hit rate: LRU / oracle / unbounded | 96.2 / 96.7 / 98.4% | 19.3 / 20.9 / 98.4% | 21.7 / 22.0 / 98.4% |
| Recomputed input tokens, billions: LRU / oracle / unbounded | 0.65 / 0.57 / 0.28 | 13.80 / 13.52 / 0.28 | 13.40 / 13.34 / 0.28 |
| Mean TTFT: LRU / unbounded | 0.58 s / 0.10 s | 162 s / 0.12 s | 672 s / 0.59 s |
| Mean ITL: LRU / unbounded | 19.1 / 5.8 ms | 49.0 / 5.9 ms | 49.0 / 6.9 ms |
| SLO attainment: LRU / unbounded | 96.3 / 99.0% | 12.4 / 98.6% | 13.8 / 91.9% |

- **8 concurrent plays.** LRU already reuses 96.2% of input tokens. The retention policies and the oracle stay within 0.5 points of that; LFU (94.2%) and Random (75.8%) lower it.
  - Even so, requests take 3.3× longer than with an unbounded cache. Mean ITL triples (19.1 versus 5.8 ms) across outputs averaging about 1,077 tokens; preemptions are negligible (7).
  - LRU makes 1,331 first-admission reloads larger than 100k tokens, versus 1,078 for the oracle and 226 for an unbounded cache. Avoiding about a fifth of those reloads did not lower pooled ITL or mean latency here. We have not isolated why.
  - Mean TTFT moves more in relative terms: last gap 13% and oracle 11% below LRU's 580 ms. That mean is driven by a few very large reloads, and p95 TTFT moves in mixed directions.
- **32 and 128 concurrent plays.** Concurrent contexts far exceed the 1.8M-token cache. Hit rate collapses to about 20%, 13.3–13.8 billion input tokens are recomputed, and queueing pushes mean TTFT to minutes.
  - The retention policies and the oracle change the hit rate by at most 1.6 points; Random lowers it by up to 6.2 points. No policy changes latency by more than 1.3%.
  - Active (referenced) KV averages 45–48% of capacity, so much of the cache stays evictable. The problem is that the reuse working set does not fit.
- **Random replacement is clearly worse** (−17% at 8 plays), so policy choice can hurt. LRU is already close to the best of the policies tested.

## Interpretation

- **Agentic reuse is real.** AgentX contexts are long (mean input about 219k tokens) and almost all reusable within a play. An unbounded cache keeps 98.4% of input tokens.
- **The bottleneck in this setup is how many sessions' contexts fit, not which inactive blocks are kept.**
  - At low concurrency, LRU already keeps nearly everything that fits.
  - At high concurrency, nothing ordering-based can make the working set fit.
- **This matches the literature's conditions.** Published retention gains, as in Continuum and KVFlow, rely on hard pinning with scheduling changes, on prefetch from a host tier, or on capacity close to the working set. This run used only soft, eviction-order retention on one fixed-capacity GPU.
- **Untested directions consistent with these results** (none were run here):
  - more KV capacity per session: a host or SSD offload tier, or multiple workers with session-affine routing;
  - admission or concurrency control, so that contexts that do fit are not displaced;
  - scheduling that keeps a returning session's KV warm, as in Continuum's program-level FCFS.

## Verification

- 62 replays completed, with no failures or retries and no substitute scores. All were independently re-audited from raw request, token-summary and occupancy files.
- **Coverage.** Every one of the 98,827 AgentX requests is replayed in its split in every cell. The unbounded cache evicts nothing in any cell.
- **Parity, byte-identical in each case:**
  - LRU repeated;
  - LRU without telemetry;
  - native versus Python-callback LFU;
  - native versus Python-callback last gap.
- **Sampling error.** Finer 1 s telemetry changes occupancy statistics by at most 0.24 percentage points.
- **Regression and scoring.** The engine change reproduces the full-trace sweep's Mooncake LRU outputs byte-for-byte (`retention_experiment.py check-regression`). Per-request token summaries reproduce token-event metrics on a test fixture.
- **Simulator check.** The timing-free cache model reproduces the shape of Mooncake's published hit-ratio table (`validate-cache-sim`).
- **Provenance.** The run started from clean commit `2bf8fd7`. The HTML regenerates byte-for-byte from the evidence.

## Limitations

- **Timing and hardware.** Timing uses the pinned polynomial model, not calibrated for Qwen2.5-7B on an H200; one worker; fixed FCFS scheduling. The served model sets capacity only.
- **Soft retention.** Retention is a soft eviction priority: protected blocks never block admission, unlike Continuum's hard pins.
- **The oracle is not optimal.** It knows each session's true return time, but it is not block-level Belady over the whole cache.
- **Load levels are scenarios.** The concurrency levels are not observed AgentX load; AgentX does not record cross-play timing.
- **Changes from the earlier full-trace spec.** The 41 zero-output requests were replayed prompt-only. Token timing uses exact per-request summaries rather than individual events.
- **Statistics.** These are deterministic single runs; repeats establish reproducibility, not statistical independence.
