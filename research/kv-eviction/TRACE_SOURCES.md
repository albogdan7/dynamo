# Trace sources and replay contract

Each source revision and downloaded-byte SHA-256 is pinned in `kv_experiment/sources.py`. The manifest records the selected population, normalization, source and output hashes, and per-window settings. Add sources through this small registry and a thin adapter, reusing the existing native loaders.

| CLI trace | Download / cohort | Source blocks | Engine/cache in the pilot | Execution |
| --- | --- | ---: | --- | --- |
| `mooncake-toolagent` | Full 23,608-request release; 256 tuning + 256 evaluation requests | 512 tokens | 512-token blocks / 131,072 tokens | Fixed arrivals, explicitly 10× inter-arrival timing |
| `mooncake-conversation` | Full 12,031-request release; 256 + 256 requests | 512 | 512 / 131,072 | Same fixed-arrival settings as tool/agent |
| `agentx` | First 16 complete source plays, bounded to 128 MiB; four qualified complete plays, 60 + 192 requests | 64 | 512 / 196,608 | Closed-loop; serial top-level plays because cross-play wall-clock arrivals are unavailable; native subagent spawn/join within each play |
| `freeinference` | Complete 2026-05-20 day (17 root sessions / 383 total requests); six qualified complete sessions, 139 + 33 requests | 16 | 512 / 81,920 | Closed-loop sequential sessions; observed global root arrivals and observed think/tool delays |

## Semantics

Mooncake keeps its existing JSONL representation. Session adapters use the existing **`dynamo.agentic_mooncake` version 2** header and request/dependency schema. The native loader owns materialization, queueing, active-KV accounting and generation. Source 16/64-token identities are conservatively coarsened into the same 512-token engine pages. The policy interface and scorer do not change between traces.

Requests use completion-triggered sequence edges for observed think/tool gaps. A faster or slower simulated predecessor moves its dependent request's readiness; the recorded predecessor's service time is used only to derive the gap, not as simulated service time. AgentX uses the existing native Weka importer for spawn/join relationships and logs its selected nested-timestamp interpretation. Descendant absolute time floors are removed so they cannot hide closed-loop speedups. AgentX's inter-play serialization is synthetic and explicit; these results are not an official AgentX benchmark.

KV identities are namespaced by recorded model (and FreeInference provider), preventing cross-model cache reuse. Timings still use one generic, uncalibrated native polynomial worker; real model/backend performance and weights are not modeled. Compare agents/policies **within a trace/configuration**, not absolute goodput across these different workloads and cache capacities. The observation span includes human/tool idle time.

## Qualification and limitations

AgentX selects the first complete source plays that contain subagents, have at most 128 model requests, have strictly positive output counts and fit a 196,608-token per-request bound. Source play ordinals **2, 7** are tuning; **10, 13** are evaluation. No request is cut out of a selected play. The source documents 64-token-block input counting artifacts; these are not exact tokenizer lengths. The entire downloaded source prefix is checksummed, and every rejected play has explicit reasons in the manifest.

FreeInference selects complete, chronological sequential sessions with positive reported completion counts, no nested subagents and no overlapping requests in a stream. The day has **101 explicitly zero reported completion counts**, some with tool calls; their true generated lengths are ambiguous, so affected whole sessions are excluded rather than replayed as invented prefill-only requests. Seven sessions qualify; the pilot uses three per split (source ordinals **0, 1, 3** and **4, 13, 14**). Full nested FreeInference join semantics are not supported. Input lengths are the source's `o200k_base` role-token sums, not provider prompt counts; output lengths use provider-reported completion counts. Unknown partial input tails receive private identities and never gain invented sharing.

These are small, qualified subsets with selection bias. Session-disjoint splits prevent directly reusing the same session as tuning/evaluation, but do not establish statistical independence or generalization to the full public corpus. The initial FreeInference evaluation has an all-pass SLO and AgentX tuning is nearly all-pass; report these limitations instead of changing thresholds to favor a policy.

## Full-release coverage

The full-trace baseline experiment uses complete pinned releases instead of the pilot windows above. `kv_experiment/releases.json` pins every file (byte count and SHA-256). Download and inventory all four releases with:

```bash
python3 research/kv-eviction/baseline_comparison.py fetch \
  --config research/kv-eviction/configs/full-trace-baselines.json
```

This writes, under the ignored `.cache/full-trace/<trace>/` directory, `ledger.jsonl.gz` with one row per source request and `inventory.json` with counts by flag, model, day, file and session. Each ledger row records a stable source ID, file, line and in-line location, session, day, model, the recorded input/output counts and flags. FreeInference day files are also checked against the publisher's weekly manifests (decompressed SHA-256 and line count). Publisher totals are compared with observed counts and never forced to agree.

Adapter contract:

- Every source request appears exactly once; duplicate identities fail. Nothing is selected by length, cache fit, output count or performance.
- Lengths are copied, never invented: Mooncake `input_length`/`output_length`; AgentX `in`/`out` (`in` is a 64-token hash-block count × 64); FreeInference input is the `o200k_base` role-token sum and output is the provider completion count.
- A request is infeasible when `ceil((input + output) / 512)` exceeds the frozen capacity in blocks. Capacity is never raised to fit it.
- Zero or missing outputs are ambiguous and block. FreeInference nested subagents and overlapping requests in one session also block, because this adapter has no faithful spawn/join or overlap translation yet; AgentX subagents are supported by the native importer.
- One blocked request blocks its whole trace: the trace is not normalized or replayed, so no filtered run is reported as a full trace.
- A ready Mooncake trace becomes one recording: the verified source file, with original arrivals, a cold start and cache retained throughout.

Inventory at the pinned revisions and frozen capacities:

| Trace | Source requests | Status | Blocking flags (may overlap) | Tokens needed to fit every request |
| --- | ---: | --- | --- | ---: |
| Mooncake tool/agent | 23,608 | ready | none | 126,976 (frozen 131,072) |
| Mooncake conversation | 12,031 | ready | none | 126,976 (frozen 131,072) |
| AgentX | 98,827: 393 plays, 56,798 main turns, 42,029 in 1,697 subagent groups | blocked | 38,806 exceed 196,608 tokens; 41 zero outputs | 996,864 |
| FreeInference | 1,222,199: 12,002 root and 2,034 nested sessions, 119 day files over 112 days | blocked | 813,184 exceed 81,920 tokens; 35,617 nested; 4,727 zero completions (4,403 on tool-call responses); 17 overlaps | 1,260,032 |

All publisher counts reconcile. FreeInference's README totals (12,002 sessions, 1,186,582 requests, 1,213,347 tool calls) count root sessions only; nested subagent sessions add 35,617 requests. Even with unlimited capacity, 41 AgentX and 40,007 FreeInference requests would remain blocked by source semantics. Unblocking either trace needs an explicit, separately frozen decision on capacity and on those semantics.

## Attribution and immutable references

- [Mooncake FAST'25 release](https://github.com/kvcache-ai/Mooncake/blob/bd7526307049ee1612cfcb90daceffb128994ade/FAST25-release/README.md), revision `bd7526307049ee1612cfcb90daceffb128994ade`; repository Apache-2.0 license.
- [AgentX / SemiAnalysis Weka dataset](https://huggingface.co/datasets/semianalysisai/cc-traces-weka-062126/blob/23f152f6f0f9399a85901b89a6458def0ef16729/README.md), revision `23f152f6f0f9399a85901b89a6458def0ef16729`; Apache-2.0. Published full dataset: 393 plays / 98,827 model requests; the pilot uses only the bounded subset above.
- [FreeInference agentic trace](https://huggingface.co/datasets/harvardMadsys/freeinference_agentic_trace/blob/16d3c04dae6ccaa0c48a9064e204e3a8f1eb9ade/README.md), revision `16d3c04dae6ccaa0c48a9064e204e3a8f1eb9ade`; **CC BY 4.0**. Credit William Nixon, Muxin Tian, Yunjia Zheng, Haryadi S. Gunawi and Juncheng Yang, *From Requests to Sessions: A Large-Scale Characterization of Human-Driven Agentic Workloads* (2026). The filtered, model-isolated replay is our adaptation; no source endorsement is implied.

TraceLab and GAIATrace remain research candidates rather than supported loaders; their released prefix identities and serving-replay suitability have not been validated here.
