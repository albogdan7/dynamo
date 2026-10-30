# KV eviction agent experiments

Next experiment: [Full-trace baseline specification](FULL_TRACE_BASELINE_SPEC.md), now run; see [FULL_TRACE_RESULTS.md](FULL_TRACE_RESULTS.md). Review it before further agent tuning. The implementation history below describes the earlier pilots.

Updated: 2026-10-29. Implementation branch: `research/kv-eviction-additional-traces` in [albogdan7/dynamo](https://github.com/albogdan7/dynamo). All code, tests, documents and artifacts stay in this fork.

## Deliverable

One command launches a restricted coding agent, scores its submitted eviction policies, saves their source and performance, selects using tuning data, then evaluates with fresh state on withheld data. The initial release uses **Claude Code + Mooncake tool/agent**; the extensions add Codex and three traces. [README.md](README.md) documents runnable commands and output retrieval; [RESEARCH_NOTES.md](RESEARCH_NOTES.md) contains background research.

## PR stack

| PR | Scope | Acceptance |
| --- | --- | --- |
| 1. Baseline and one trace ([#2](https://github.com/albogdan7/dynamo/pull/2)) | Reproduce legacy LRU and leaf-first LRU; pin Mooncake tool/agent and deterministic windows. | Two matching reproductions per policy; trace checksum and provenance verified. |
| 2. Complete Claude experiment ([#3](https://github.com/albogdan7/dynamo/pull/3)) | Native serving replay with editable eviction, valid metrics, restricted Claude, isolated policy execution, saved submissions and report. One trace and one model per experiment. | A real agent edits policies; decisions affect eviction; every submission is retained; selected-policy replay and independent rescoring match. |
| 3. Codex ([#4](https://github.com/albogdan7/dynamo/pull/4)) | Add Codex to the same command, policy contract and artifact format. | Real restricted Codex run; same settings and search budget for comparison. |
| 4. Several traces ([#5](https://github.com/albogdan7/dynamo/pull/5)) | Add Mooncake conversation, then AgentX and a bounded FreeInference subset after verifying suitability. | Validated provenance, prefix identities and timing; both agent backends work without scorer changes. |

Order: **1 → 2 → 3 → 4**. PR 2 is the first usable release. Codex and the three additional traces are implemented; current comparisons are in [EXTENSION_RESULTS.md](EXTENSION_RESULTS.md). Continuum, MORI and real serving-engine ports are separate work.

## Experiment contract

- Preserve the original synthetic experiment as `legacy_cache_proxy`; its TTFT is `50 ms + 3 ms/missed block`. It cannot supply ITL or achieved goodput.
- Use the existing native Mooncake loader. Source: pinned FAST'25 tool/agent release, **23,608 requests**, 512-token prefix hashes, millisecond arrivals. No invented shared prefixes.
- Default windows: source rows `[0,1000)` for tuning and `[11804,12804)` for evaluation, no shuffle, cold state per window, no warmup. Record all settings and checksums.
- Cache: **131,072 tokens = 256 blocks**. Only inactive, unreferenced and unpinned native blocks are eviction candidates.
- The pinned serving engine has no custom-policy hook. A small, checksummed research patch builds an independent adapter inside this fork. It leaves the main Dynamo dependency and serving code unchanged.
- Policy fields describe native allocation/release counters and LRU order. They are not timestamps, depth or hit counts. Native release order is unique, so a creation-order tiebreak is equivalent to native LRU; do not claim the legacy improvement carries over.
- Native polynomial timing is **assumed, uncalibrated**. This is simulation, not GPU validation.
- Agents receive tuning feedback only. Selection: highest tuning goodput, lower mean TTFT, earlier submission. Evaluation starts a fresh policy, cache and queue.
- Log source/config/model/CLI versions, token usage, API-equivalent cost when supplied, exact commands, failures and all policy sources. Never replace invalid-policy results with successful LRU scores.
- Submission directories: `YYYY-MM-DD-HH-MM-SS`, Chicago timezone and ISO offset in metadata, collision suffix `-02`, `-03`, etc. Never overwrite a run.

## Individual metrics and SLO

| Metric | Unit | Direction |
| --- | --- | --- |
| Mean simulated TTFT, including queueing | ms/request | Lower |
| p95 simulated TTFT | ms/request | Lower |
| Mean simulated ITL over individual output-token gaps | ms/gap | Lower |
| p95 simulated ITL over those gaps | ms/gap | Lower |
| Simulated goodput: SLO-passing requests / observation duration | requests/s | Higher; primary selection metric |
| SLO attainment, including failed eligible requests in denominator | % | Higher |
| Recomputed input tokens, including repeated prefill | tokens | Lower |
| First-admission input-token cache-hit rate | % | Higher |
| Evictions, with block size recorded | blocks | Diagnostic |

SLO: **TTFT ≤ 1,000 ms AND each request's p95 individual ITL ≤ 50 ms**. Nearest-rank percentiles. One-token outputs have no ITL samples/criterion; zero-output requests are explicitly excluded from SLO eligibility. Observation duration includes drain, from first scored arrival to last terminal event. TPOT is not tail ITL.

Report raw values and changes against both baselines. Percentage rates use percentage-point changes. Keep invalid submissions, policy/agent failures, timeouts, submission count, runtime, token usage and reported cost separate from serving performance.

**Load qualification:** original-rate Mooncake overloads this illustrative single-worker profile and nearly all requests miss the SLO. Retain this as an overload test, not a useful model ranking. `--arrival-scale` explicitly multiplies inter-arrival timing; qualify and freeze a separate lower-load experiment before comparing models. Never change the SLO or workload to favor a candidate. Report any all-pass/all-fail or near-saturated SLO.

## Validation

1. Reproduce each legacy policy twice and preserve the paired results.
2. Verify trace counts, checksum, units, hash count, partial blocks and prefix ancestry.
3. Hand-check miss/reuse, queueing, one-token output, tail ITL, exact SLO boundaries, failed-request denominator, and native token/KV accounting.
4. Compare callback LRU with native LRU; demonstrate an alternative policy changes cache decisions. Reject invalid IDs, exceptions and hangs without fallback scores.
5. Test actual policy-container host-file/write/network denial and cleanup; test real Claude file-tool read/write boundaries.
6. Run a fixed-budget real Claude experiment; archive every submitted policy; replay the selected policy and rescore raw token events independently.
7. Keep the final report and compact evidence outside ignored experiment directories. Full raw artifacts are local until explicitly exported; document the export command.

## Status

Baseline reproduced twice per policy. Pinned trace validated. Native adapter built. Real restricted Claude smoke run completed. Native/container tests pass, including timeouts and isolation; real Claude outside-workspace read/write probes were denied. Two five-attempt pilots, lower-load qualification, exact selected-policy replay and independent arithmetic auditing are complete. The first release is implemented; see [REPORT.md](REPORT.md) for results and limitations. Codex support is implemented and its matched five-submission pilot reproduces Claude’s selected-policy metrics; Mooncake conversation, bounded AgentX and qualified FreeInference support are complete, with 25 passing tests and matched results recorded in [EXTENSION_RESULTS.md](EXTENSION_RESULTS.md). Baseline PR [#2](https://github.com/albogdan7/dynamo/pull/2) targets the fork’s `main`; the complete experiment branch includes that prerequisite and also targets the fork’s `main`. Merge the baseline PR first.

## Fixed heuristic comparison

Implemented allocation-order FIFO, MRU, committed-access LFU, two-tier SLRU and random seeds 0, 1 and 2. Eight fresh five-submission searches completed with all 40 submissions valid; 192 saved replay audits and eight exact selected-policy repeats passed. Native LRU remains identical to the original controls. The current HTML is generated from `evidence/current-comparison.json`; [HEURISTIC_BASELINES.md](HEURISTIC_BASELINES.md) contains rerun and collection commands.
