# KV eviction research notes

Research recorded on 2026-10-28. [PLAN.md](PLAN.md) defines scope, [README.md](README.md) documents the implementation, and [REPORT.md](REPORT.md) records tested results. Candidate sources below are research leads, not supported or benchmarked inputs.

## Current simulator boundary

The legacy Python evaluator is a cache-only proxy. Its stored source, seeds and scores are preserved by the pinned reproduction command. The native serving adapter instead uses the fork's pinned AISimulate core and its existing Mooncake loader. Native candidate metadata describes inactive allocation/release history; it does not expose active blocks, future requests, exact token content, program scheduling or TTL control.

Mooncake JSONL plus a provenance manifest is the initial input contract: millisecond timestamp, input/output token counts and ordered reusable prefix-block identities. Keep partial-block and prefix semantics in the existing native loader. Thin adapters for future data must preserve timing, sessions/dependencies and identity rather than inferring shared content from lengths.

## Traces worth adding

| Source | KV-eviction value | Validation/adaptation needed |
| --- | --- | --- |
| [Mooncake conversation](https://github.com/kvcache-ai/Mooncake/blob/bd7526307049ee1612cfcb90daceffb128994ade/FAST25-release/README.md) | 12,031 released requests with 512-token prefix identities; complements tool/agent traffic. | Same native loader; independently pin/validate bytes, window and license. |
| [AgentX / SemiAnalysis Weka coding traces](https://huggingface.co/datasets/semianalysisai/cc-traces-weka-062126/blob/main/README.md) | Coding sessions, shared prefixes, subagent fan-out and idle periods. | Preserve session trees and end-to-start delays. Counts derived from 64-token blocks have documented artifacts. Any synthetic cross-session arrival schedule must be explicit. A custom simulation is not an official AgentX benchmark result. |
| [FreeInference agentic trace](https://github.com/HarvardMadSys/freeinference_agentic_trace) | Nested requests/tool calls and chained 16-token identities support reuse across tool waits. | Pin a complete-session subset; raw release is about 108 GB decompressed. Keep tokenizer-derived counts distinct from provider usage. |
| [TraceLab](https://github.com/uw-syfi/TraceLab) | Real Claude/Codex rounds and cache/tool timing. | Verify exact reusable prefix identities. Observed hits and token counts alone cannot reconstruct counterfactual eviction. |
| [GAIATrace / Vidur-Agent](https://arxiv.org/abs/2606.01725) | General agent workflows beyond coding, with token-level trace claims. | Verify released artifacts, license, dependencies and prefix reconstruction before promising a loader. |

Flat arrivals do not represent closed-loop tool dependencies. A benchmark task list is not a serving trace. Shared prefixes across contiguous tuning/evaluation windows do not establish session independence.

## Continuum reproduction assessment

Assumed reference: Continuum / Continnum, *Efficient and Robust Multi-Turn LLM Agent Scheduling with KV Cache Time-to-Live*, arXiv:2511.02230v7. Pin the paper version because it differs from the earlier preview implementation.

Sources:

- Paper: https://arxiv.org/html/2511.02230v7
- Preview implementation: https://github.com/Hanchenli/vllm-continuum, inspected head 316a58794a6ff86b216e579b74fd56ed0c5a911f.
- The preview README explicitly says it provides Continuum-style scheduling **without the estimation in the paper**. Running it is not sufficient to claim a reproduction of the full paper algorithm.

Required additions for a simulator implementation:

1. Program-aware traces: program ID, turn/dependency order, output length, tool identity, observed tool duration, termination, and prefix identities or tokens. Plain Mooncake JSONL has timing/length/hash fields but does not supply all these program/tool labels. Do not infer tool execution time by subtracting request arrivals or treat hash overlap as a reliable program ID.
2. Closed-loop replay: a dependent turn becomes ready after its predecessor completes plus its tool delay. Fixed absolute request arrivals cannot faithfully represent feedback from scheduling improvements into subsequent turns. Models may see the next tool identity if available at the decision time, but never the future realized duration or remaining turns as an oracle.
3. A serving scheduler and memory model: active versus inactive cache, program-level FCFS, decode-time cache growth, prefill/reload costs, TTL pins/expiry/unpinning, returning-request priority, and memory-pressure/deadlock handling.
4. The paper's TTL estimator, including online per-tool duration histories, cold-start behavior, empirical duration distributions, queue-delay estimates, and memory opportunity costs. Keep fixed-TTL and full-estimator variants separate.
5. Ablations: request FCFS with baseline cache behavior; program FCFS alone; program FCFS with fixed TTL; program FCFS with estimated TTL. Compare job completion time and queue delay as well as TTFT, ITL, cache reuse, and SLO goodput. Test long/failed tools and saturated memory.

Reproduction levels must remain distinct:

- Algorithm simulation: verifies a documented implementation under explicit timing assumptions; does not reproduce published GPU performance.
- Preview-code replication: run pinned upstream vLLM baseline and Continuum preview on the same GPU/model and agent workload. The public README describes an example with Llama-3.1-70B on four H100 GPUs; start with a smaller supported configuration only as an explicitly different experiment.
- Paper-result reproduction: needs the full estimator, exact workload artifacts and versions, matching model/hardware/settings, profiling, load sweeps, and repeated measurements. Verify availability of the paper's complete traces and scripts before promising identical figures. Production/private-testbed results cannot be assumed reproducible from public summary JSON files.

For an eventual Dynamo integration, establish the reference behavior in the vLLM backend first; a block-level inactive-pool callback alone cannot control program admission, scheduling priority, or TTL pinning. Do not begin a full engine port as part of answering the feasibility question.
