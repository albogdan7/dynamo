# Full-trace KV eviction baseline experiment

Status: implemented and run; see [FULL_TRACE_RESULTS.md](FULL_TRACE_RESULTS.md). Updated 2026-10-30.

## Question

Do common eviction heuristics improve simulated serving performance on complete workloads, and is KV capacity actually under pressure? The existing subset results cannot answer this. A larger trace alone does not guarantee a larger policy effect.

## Workloads

| Trace | Required coverage |
| --- | --- |
| Mooncake tool/agent | All 23,608 requests in the pinned release |
| Mooncake conversation | All 12,031 requests in the pinned release |
| AgentX | All 393 published plays / 98,827 model requests; verify against downloaded files |
| FreeInference | Every trace file in the pinned release, across all available days and sessions; inventory exact counts before running |

Pin revisions and checksums using [TRACE_SOURCES.md](TRACE_SOURCES.md). Remove pilot download limits, request windows and session qualification filters. Preserve prefix identities, model/provider isolation, complete dependencies and tool delays. Keep original timestamps unchanged: **no arrival scaling or load sweep**. For AgentX, missing cross-play timestamps require an explicitly documented serialization; do not invent concurrent arrival times. Preserve within-play concurrency.

Full AgentX/FreeInference coverage requires loader work, including nested sessions, overlaps and ambiguous zero completion counts. Do not invent missing lengths, silently discard requests, or call a filtered run “full trace.” Report any unresolved source limitation before execution. Validate counts and semantics first; FreeInference coverage means the entire pinned release, not the single pilot day.

## Preliminary experiment: heuristics only

- Compare **LRU, FIFO, MRU, LFU, SLRU, and Random seeds 0, 1, 2**, using the definitions in [HEURISTIC_BASELINES.md](HEURISTIC_BASELINES.md). No agent calls, tuning or held-out split in this phase.
- Replay every complete workload once per policy/seed, from cold state; retain cache across the replay rather than resetting at pilot boundaries. Keep model/provider KV namespaces separate. Drain all requests.
- Freeze one simulated worker, scheduler, timing model and 512-token blocks across policies. Retain current per-trace capacities: Mooncake 131,072 tokens; AgentX 196,608; FreeInference 81,920. Record these explicitly; do not silently raise capacity for larger requests. Report infeasible requests as blockers.
- Repeat LRU to check determinism and verify callback LRU against native LRU. Save each random seed separately. These are uncalibrated simulator results, not measured GPU latencies.

## Metrics

| Measure | Report |
| --- | --- |
| TTFT, including queueing | Mean and p95, ms/request |
| ITL, consecutive output-token gaps | Pooled mean and p95, ms/gap |
| SLO | TTFT ≤ 1,000 ms **and** request p95 ITL ≤ 50 ms |
| SLO goodput / attainment | Passing requests/s; passing/eligible count and % |
| Cache effectiveness | Input-token hit rate (%), recomputed input tokens, evicted 512-token blocks |
| **KV occupancy** | Per-worker/rank active/protected, inactive/reclaimable, free and capacity blocks; physical occupancy = active + inactive |
| **Occupancy summary** | Time-weighted mean and p95, peak, and fraction of time ≥95% / 100% capacity, for physical and active occupancy separately |
| Pressure / completion | Preemptions, queue length, completed/failed requests, observation duration (s) |

Use simulated-time occupancy samples with a recorded cadence and explicit baseline/final samples; label sampled peaks and threshold durations as estimates. Include idle periods in whole-run statistics and also report request-active intervals separately. A physically full cache of reclaimable blocks does **not** by itself establish active-memory pressure.

Keep current nearest-rank percentile and SLO accounting: one-token outputs have no ITL requirement; report zero-output eligibility separately. Goodput includes tool/human idle time and drain, from first arrival to last terminal event.

## Evidence and decision

Save a timestamped run directory with source inventory/checksums, exact command/configuration, policy sources/seeds, request/token events, occupancy timeline and audited metrics. A script regenerates CSV and a self-contained HTML report: one table per trace, every heuristic as a column, raw values and changes from LRU. Keep previous pilot results separate.

Verify occupancy conservation, request coverage, metric arithmetic and identical serving results with telemetry enabled/disabled. Report failed runs without fallback scores. Compare all baselines under identical inputs and settings; do not pick favorable random seeds.

Proceed to agent experiments only after reviewing this sweep. Meaningful heuristic differences identify useful scenarios. Negligible differences—especially under verified pressure—mean limited demonstrated headroom for this policy interface; they do not prove that an agent cannot improve it.

## PR stack

Three implementation PRs: **occupancy → trace loading → full-trace baseline sweep and report**. This separates measurement correctness, workload correctness and experimental conclusions.

All work stays in `albogdan7/dynamo`. Existing open prerequisites are [#2 baseline](https://github.com/albogdan7/dynamo/pull/2), [#3 Claude runner](https://github.com/albogdan7/dynamo/pull/3), [#4 Codex runner](https://github.com/albogdan7/dynamo/pull/4), and [#5 trace loaders, heuristics and pilot report](https://github.com/albogdan7/dynamo/pull/5). They currently all target `main`; make their review dependencies explicit as **#2 → #3 → #4 → #5** by targeting each child at its parent branch. These contain existing work; the new comparison does not invoke either agent runner. PR #1 is the original evaluator, not an additional merge prerequisite for this stack.

Build the following focused PRs on top of #5. Each branch starts from its predecessor and initially targets that branch, so its diff contains only its own changes. After each parent merges, rebase the child onto fork `main`, retarget it to `main`, and rerun its checks. Remove already-merged commits if the parent was squash-merged. No PRs are merged automatically.

| New PR / branch | Change | Ready-to-merge evidence |
| --- | --- | --- |
| A. `research/kv-occupancy` | Native virtual-time telemetry, occupancy aggregation and raw output | Conservation and time-weighting checks; instrumented/uninstrumented serving metrics match |
| B. `research/full-trace-loaders` | Full source inventory/downloads; complete Mooncake, AgentX and FreeInference loading | Exact coverage accounting, nested/overlap fixtures, no silent filtering or fabricated lengths; unresolved source semantics block the affected loader |
| C. `research/full-trace-baselines` | Baseline-only sweep CLI, immutable manifests, independent audit/resume; execute full sweep and publish compact evidence, CSV and offline HTML | Small end-to-end fixture, native/callback LRU parity, repeatability and failure handling; full-run coverage/metric audits pass; report regenerates; no agent credentials needed |

Keep historical pilot evidence intact. Review each PR for scope, tests and reproducible commands; resolve applicable checks before declaring it ready. A specification is not evidence that a full-trace run succeeded.

## Design decisions

**1. Occupancy:** use the native simulated-time telemetry API, per worker/rank. Record active/protected, inactive/reclaimable, free and total blocks, alongside waiting/running requests and preemptions. Check `active + inactive + free = capacity` using the engine's reservation accounting. Use duration-weighted statistics rather than averaging event samples; distinguish whole-run and request-active periods. Record cadence and interval convention, and test a finer cadence on a bounded fixture to quantify sampling error. Occupancy establishes pressure; it does not itself establish that an eviction heuristic improves latency.

**2. Trace loading:** separate downloading, source adaptation and validation. Each thin source adapter produces the existing shared replay schema; it never sees a policy or score. Normalize once, save immutable normalized files and hashes, then feed those identical files to every baseline. This prevents policy-dependent preprocessing.

- **Coverage:** inventory every release file and account for every source request exactly once, with stable source IDs and original file/row locations. Save counts by day, model and session. Never select sessions by length, cache fit, positive outputs or observed performance. Invalid/unsupported records remain in the coverage ledger and block a full-coverage claim.
- **Timing:** preserve fixed recorded arrivals and tie order. For dependency-aware sessions, preserve spawn/join, overlap and recorded tool/think gaps; readiness may follow simulated predecessor completion. Document this distinction from fixed timestamp replay. Missing cross-session timing is an explicit source limitation, never silently fabricated concurrency.
- **KV identity and lengths:** retain recorded token counts and prefix ancestry, namespace sharing by model/provider, and document block conversion/partial-tail handling. Preserve genuinely zero outputs when the source semantics establish zero; ambiguous zero or missing counts block that source until resolved. Do not replace them with guesses. Any conservative private-tail identity is explicitly counted and disclosed.
- **Cross-file boundaries:** deduplicate only by established source identity, not text similarity. Preserve source session boundaries; do not infer cache sharing across unrelated sessions. Without a shared clock, replay independent recordings separately with cold starts and report each boundary. Aggregate request/gap distributions from raw observations; aggregate goodput as total passes / total observation time, not mean per-session rates. Do not concatenate unrelated days or serialize plays as though the resulting throughput were observed global concurrency.
- **Validation:** hand-check representative source records against normalized output, including nested calls, overlaps, partial blocks and zero outputs. Check counts, units, dependency acyclicity, namespace isolation and coverage on the full corpus. Publish a short adapter contract and assumptions beside the inventory. These steps reduce avoidable selection bias; source collection bias and missing semantics remain limitations.

**3. Full-trace sweep:** freeze source hashes, adapter version, cache capacities, scheduler, timing model, SLO and random seeds before scoring. Run all heuristics on identical inputs with fresh state per independent recording, retaining cache throughout each recording. Use original fixed timestamps, no load variation and no tuning split. Preserve every result/failure and compare paired metrics against LRU; do not silently resize caches or change SLOs after seeing scores. Report full-workload aggregates plus per-day/play/session diagnostics so idle time or a few large sessions cannot conceal where differences arise. Deterministic repeats establish reproducibility, not statistical independence.

## Proposed file changes

Everything below is inside `research/kv-eviction/`; `(new)` denotes planned files, not existing commands.

```text
FULL_TRACE_BASELINE_SPEC.md       this specification
configs/full-trace-baselines.json (new) frozen traces, policies, capacities, SLO, cadence
baseline_comparison.py           (new) fetch / run / audit commands; no agent calls
kv_experiment/
  sources.py                     full-release inventory and pinned checksums
  traces.py, sessions.py          full loaders; preserve request/dependency semantics
  occupancy.py                   (new) simulated-time occupancy summaries
  comparison.py                  (new) baseline sweep orchestration and manifests
  baselines.py, runtime.py        reuse heuristics and replay; add telemetry artifacts
native/src/main.rs               emit occupancy samples through native telemetry
evidence/audit_run.py            extend independent audit to occupancy and coverage
export_report.py                add baseline-only report mode; reuse HTML exporter
tests/
  test_occupancy.py               (new) occupancy arithmetic and telemetry parity
  test_full_trace_comparison.py   (new) complete coverage, sweep and reproduction
  test_session_traces.py          extend nested/overlap/ambiguous-input checks
experiments/<date-hh-mm-ss>/      ignored raw runs; never overwrite
  manifest.json, source-inventory.json, config.json
  <trace>/<policy>/
    policy.py, manifest.json, requests.jsonl, tokens.jsonl
    occupancy.jsonl, metrics.json, audit.json
evidence/full-trace-baselines/   compact audited scores, sources and reproduction metadata
reports/full-trace-baselines.html
reports/full-trace-baselines.csv
```

## Proposed reproduction commands

**Implemented in PRs A–C.** Run from the fork repository root. Requires Python, Rust/Cargo, Docker for existing policy isolation, and zstd; no Claude/Codex login or GPU. `prepare_native.py` already exists.

```bash
python3 research/kv-eviction/prepare_native.py

python3 research/kv-eviction/baseline_comparison.py fetch \
  --config research/kv-eviction/configs/full-trace-baselines.json

python3 research/kv-eviction/baseline_comparison.py run \
  --config research/kv-eviction/configs/full-trace-baselines.json \
  --output research/kv-eviction/experiments/2026-10-30-14-00-00

python3 research/kv-eviction/baseline_comparison.py audit \
  --run research/kv-eviction/experiments/2026-10-30-14-00-00 \
  --evidence research/kv-eviction/evidence/full-trace-baselines

python3 research/kv-eviction/export_report.py \
  --baseline-evidence research/kv-eviction/evidence/full-trace-baselines \
  --output research/kv-eviction/reports/full-trace-baselines.html
```

Choose a fresh date/time directory for a new run. Fetch validates the entire inventory; run executes all four workloads and all eight policy/seed variants, plus repeated native LRU and callback parity checks. Resume only skips completed, audited jobs whose source/configuration/policy/binary hashes match. Audit fails on missing coverage, failed jobs or inconsistent arithmetic. Export regenerates HTML and sibling CSV without rerunning simulation; the HTML embeds compact evidence and the report generator for offline sharing. A fresh simulation additionally requires the pinned trace downloads and recorded native build.

Report policy differences in serving metrics alongside hit rate, eviction counts and measured pressure. Treat a substantial, reproducible latency or SLO improvement as evidence to pursue agent optimization; fewer evictions alone are insufficient. Report all-pass/all-fail SLOs and overload explicitly rather than changing timestamps or thresholds to obtain a favorable ranking. This preliminary sweep is exploratory: any later agent comparison needs a separately frozen evaluation protocol.
