# Agentic KV retention experiment

Status: frozen 2026-10-30, before any policy was compared. Follows [FULL_TRACE_RESULTS.md](FULL_TRACE_RESULTS.md), which found limited headroom for eviction heuristics on a saturated single worker.

## Question

When agent sessions pause between requests, does a retention policy that predicts when a session will return reduce simulated request latency relative to LRU, at loads the worker can serve? How much headroom does an oracle show?

The literature motivating the question: Continuum (arXiv 2511.02230), KVFlow (NeurIPS'25) and InferCept (ICML'24) report gains when KV is retained across tool-call pauses; Alibaba's trace study (ATC'25) reports gains from workload-aware eviction when capacity is tight.

## Frozen protocol

| Parameter | Value |
| --- | --- |
| Workload | Complete AgentX release, pinned revision: 393 plays, 98,827 requests, subagents and recorded tool/think gaps |
| Normalization | Native Weka importer. Block identities are namespaced per play (0 cross-play shared blocks), so reuse is within-play only |
| Zero outputs | The 41 zero-output requests are replayed as prompt-only and excluded from SLO eligibility. This differs from the full-trace spec, which treated them as blockers |
| Split | Plays shuffled with seed 0: the first 131 form the tuning split, the remaining 262 the evaluation split. The decision uses the evaluation split only |
| Served model (capacity only) | Qwen2.5-7B-Instruct-1M, bf16, 57,344 bytes of KV per token |
| Hardware assumption | One H200 (141 GB), TP1. 3,584 × 512-token blocks = 1,835,008 tokens (105 GB); the largest request needs 996,864 tokens |
| Engine | One vLLM-style worker, FCFS, 512-token blocks, `max_num_seqs` 64, `max_num_batched_tokens` 8,192 |
| Timing | Pinned polynomial model, **uncalibrated**; it is not specific to the served model |
| Load | Closed loop: C concurrent plays (lanes). When a play finishes, the next play in split order starts. Within-play timing follows recorded gaps after simulated completions |
| Load levels | C ∈ {8, 32, 128}. Concurrent session contexts are roughly 1×, 4× and 16× the cache. These were fixed from capacity, not from scores. AgentX plays are idle most of the time, so LRU throughput does not saturate within 393 plays and cannot define the levels |
| Occupancy telemetry | 10 s cadence; a 1 s check on a bounded prefix measures sampling error |
| SLO | TTFT ≤ 1,000 ms and per-request p95 ITL ≤ 50 ms (unchanged) |

## Policy interface

On each request completion, the engine calls a retention policy with the session ID, the request's arrival and completion times, its input, cached and output token counts, and nothing about future requests. The policy returns a time until which that request's blocks are protected, or nothing.

Eviction chooses among the same inactive blocks as before:

1. Unprotected or expired blocks, in LRU order.
2. If only protected blocks remain, the block whose protection ends last. When protection times are predictions of the next use, this is Belady's rule applied to the predictions.

Protection never blocks admission or causes preemption. It is a soft priority, unlike Continuum's hard pinning, and the scheduler stays FCFS. "Never protect" is exactly LRU; this is checked byte-for-byte.

## Policies

| Policy | Rule |
| --- | --- |
| LRU | Native engine LRU |
| LFU, SLRU, Random (seed 0) | Native implementations of the previous heuristics; LFU parity is checked against the Python callback |
| Last gap | Protect until now + this session's last observed pause |
| Global median | Protect until now + the median of all observed pauses |
| Continuum TTL | TTL τ maximizing P(pause ≤ τ)·reload(context) − τ over a global log-binned pause histogram, after at least 100 pauses. Before that, an exponential prior with mean 1 s: τ = ln(reload/1 s) s. reload(n) is a quadratic fit to offline single-request prefill times under the same timing model. The queueing-benefit term is 0 because there is no program-level scheduling. AgentX has no tool names, so the pause history is global, not per tool |
| Oracle (bound) | Protect until the recorded gap after this request plus 60 s of slack. Exact for sequential continuations; a lower bound when the next request also waits on subagents |
| Unbounded cache (bound) | LRU with capacity at least the split's distinct block count; zero evictions is verified |

## Metrics and decision

Primary metric: mean request latency, from arrival to final token, including queueing. Play completion time is reported but not primary, because it is dominated by recorded idle gaps no policy can change. Secondary metrics: p95 request latency, mean and p95 TTFT, pooled ITL, SLO attainment, input-token hit rate, recomputed input tokens, evictions, protected evictions, occupancy and play completion time.

Decision rule:

- Proceed to agent policy search only if, on the evaluation split, one of these holds at two or more of the three load levels:
  - a non-oracle policy reduces mean request latency by at least 5% versus LRU, or
  - the oracle reduces it by at least 10%.
- Otherwise, report limited headroom for this interface.

All policies, load levels and splits are reported. None is dropped or selected after the fact.

## Checks

The following are run as part of the evidence:

- Determinism: LRU repeated.
- Instrumentation parity: telemetry on vs off.
- Callback parity: native vs Python callback for LFU and for last gap, on a bounded fixture.
- Unbounded-cache zero evictions.
- Coverage of every split request.
- Independent arithmetic audit.
- Telemetry sampling error.
- Regression: the new engine reproduces the full-trace sweep's LRU outputs byte-for-byte on both Mooncake traces.

The timing-free cache model was first checked against Mooncake Table 1 (`retention_experiment.py validate-cache-sim`). It reproduces the shape: saturation near 50k blocks, and LRU ahead of LFU by 0.03–0.06 at 10k–30k blocks. Absolute hit ratios are 0.03–0.07 higher than published. The paper states a mean input of 7,590 tokens for its analysis trace, versus 8,596 in the public release, so the traces may differ.

## Limitations

- Uncalibrated timing; one worker; soft rather than hard retention; no program-level scheduling.
- Closed-loop lanes impose a concurrency level that the AgentX source does not record. Each level is a scenario, not observed production load.
- Results bound fixed heuristics and an oracle on this interface. They do not show what a learned policy could achieve.
