# Initial Claude release report

Completed: 2026-10-29 (America/Chicago). This is the historical initial release; see [EXTENSION_RESULTS.md](EXTENSION_RESULTS.md) for current Codex/additional-trace support and comparisons.

Implemented on `research/agent-eviction-experiments` in `albogdan7/dynamo`. This is the **Claude + one trace release (PRs 1–2)**. Codex and additional traces remain the next PRs in [PLAN.md](PLAN.md). Baseline PR [#2](https://github.com/albogdan7/dynamo/pull/2) and the complete experiment are submitted against this fork’s `main`; merge the baseline first.

## What works

- One command runs actual restricted Claude Code, scores each submitted policy through the native serving simulator, selects on tuning data, and evaluates with fresh state. Every submitted policy, score, transcript and failure is saved.
- The pinned Mooncake tool/agent trace validates: 23,608 requests, 512-token prefix hashes, millisecond arrivals, verified SHA-256 and prefix ancestry. The existing native loader handles lowering.
- Native queueing/prefill/decode and active-KV eligibility are preserved. A 142-line research patch supplies inactive-block decisions and raw token timestamps; the root Dynamo dependency remains unchanged.
- TTFT, individual-gap ITL, SLO goodput, token reuse/recomputation and eviction counts are auditable. Replay checks policy/config/trace/binary hashes; rescoring is read-only. Source snapshots and per-file hashes are saved.

## Baseline reproduction

Pinned source commit: `dc61f2bbfbed0de800a7b2399188be82737f5bd0`. Both policies matched across two fresh runs each.

| Legacy evaluation | LRU | Leaf-first LRU |
| --- | ---: | ---: |
| Mean proxy TTFT | 60.57 ms | 60.54 ms |
| Input block-hit rate | 75.71% | 75.77% |
| Evictions | 843 | 840 |

The larger comparison previously shown mixed LRU tuning numbers with leaf-first evaluation. The table above compares the same evaluation split. These are **legacy cache-proxy results**: `50 ms + 3 ms/missed block`, with no ITL or achieved goodput. [Reproduction evidence](evidence/legacy-reproduction.json).

## Qualified real-agent pilot

Claude Code 2.1.285, exact model `claude-sonnet-5-5`, five attempts, five valid scored policies. Tuning source rows `[0,256)`; evaluation `[11804,12060)`. Cold cache and fresh policy/queue for each replay; no warmup. Cache 131,072 tokens. Inter-arrival timing explicitly multiplied by **10** after baseline qualification, giving one tenth the original offered load.

SLO fixed before this pilot: TTFT ≤ 1,000 ms and each request’s p95 individual-token-gap ITL ≤ 50 ms. Native polynomial timing is **assumed and uncalibrated**. No GPU accuracy claim.

| Evaluation metric | Native LRU | Selected policy |
| --- | ---: | ---: |
| Mean simulated TTFT (ms) | 1316.243 | 1290.044 |
| p95 simulated TTFT (ms) | 3218.0123 | 3218.0123 |
| Mean simulated ITL (ms) | 27.848734 | 27.804757 |
| p95 simulated ITL (ms) | 34.01 | 34.01 |
| SLO goodput (requests/s) | 0.10576055 | 0.10579425 |
| SLO attainment (%) | 17.96875 | 17.96875 |
| Input-token hit rate (%) | 37.12341 | 38.212225 |
| Recomputed input tokens | 1241807 | 1220303 |
| Evictions (512-token blocks) | 2153 | 2111 |

The selected policy retains copies that have entered the inactive queue more often, then breaks ties by LRU rank. This release count is a reuse signal, not an exact cache-hit count. Native release order is unique, so the creation-order leaf-first tiebreak has identical results to native LRU.

**Interpretation:** mean TTFT improved about 1.99%, but p95 TTFT and p95 ITL did not improve. Both policies passed the SLO for **46/256 requests**. Goodput improved only **0.03186%**, because the observation/drain duration shortened slightly; the number of SLO-passing requests did not increase. This is a small single-pilot result, not evidence of a robust model or policy advantage.

Fresh selected-policy replay matched all saved performance fields exactly. An independent arithmetic script checked all **10** saved replays (four baselines, five tuning submissions and evaluation), including SLO counts, pooled token gaps and native collector means. Its p95 uses nearest rank; native sketch percentiles can differ slightly. [Replay check](evidence/mooncake-claude-qualified/replay-check.json), [independent audit](evidence/mooncake-claude-qualified/independent-audit.json).

Five successful invocations reported 60.95 seconds of agent runtime and $0.1564092 API-equivalent cost. This is the CLI estimate, not an actual subscription charge. Exact usage breakdowns and per-policy performance are retained in [compact evidence](evidence/mooncake-claude-qualified/manifest.json).

## Original-rate overload test

A separate `claude-sonnet-4-6` pilot used 1,000 requests per split at original arrivals. LRU mean evaluation TTFT was about 272 seconds and only **2/1,000** requests met the SLO. The selected policy had the same passing count. This configuration is overloaded and unsuitable for a useful model comparison.

Five attempts produced four valid scored policies and one **240-second agent timeout**. The failed attempt and its available logs/source were retained with no successful fallback score. Successful invocations reported $0.798363 API-equivalent cost; the timed-out invocation has no final usage/cost result, so this is not a complete run cost. Different loads/windows also prevent a head-to-head ranking against the qualified pilot. [Overload evidence](evidence/mooncake-claude-pilot/manifest.json).

## Verification and limitations

- **18 tests passed**, with actual native replay and Docker enabled. They cover hand-calculated TTFT/ITL/SLO arithmetic, boundary thresholds and failed-request denominator; token/accounting consistency and queueing; callback/native LRU equivalence and fresh replay; a changed policy changing reuse; invalid IDs, exceptions, malformed stdout and hangs; cleanup and host-file/write/network denial.
- Actual Claude Read and Write probes outside its assigned workspace were denied. No secret was exposed and no outside file was created. [Isolation evidence](evidence/isolation-checks.json).
- A final real 64-request, one-submission smoke run exercised the finished controller after validation hardening. No mocked agent was used.
- Full traces and clocks are held by the controller. Claude has file tools restricted to its workspace; policy execution is non-root/read-only with no trace/credential mount or network. Invalid submissions never receive scored LRU fallback results.
- Timings are uncalibrated. Policy IPC/Python decision wall time does not advance the simulated clock; real serving overhead is unvalidated. This is flat fixed-arrival replay, not a dependency-aware agent benchmark. Only contiguous subsets were tested; shared sessions across windows and statistical generalization are not established.
- The qualified run’s exact source snapshot predates final error-reporting/rescoring hardening. The finalized runner reproduced its selected-policy performance; complete raw snapshots and hashes preserve what actually ran.

## Commands and outputs

[README.md](README.md) contains setup, commands, metrics, saved output structure, replay and export instructions. To independently audit the qualified run:

```bash
python3 research/kv-eviction/evidence/audit_run.py \
  research/kv-eviction/experiments/mooncake-claude-qualified
```

Full raw outputs and export archives are local and ignored by Git:

- `research/kv-eviction/experiments/mooncake-claude-qualified/`
- `research/kv-eviction/experiments/mooncake-claude-qualified.tar.gz`
- `research/kv-eviction/experiments/mooncake-claude-pilot/`
- `research/kv-eviction/experiments/mooncake-claude-pilot.tar.gz`

Compact evidence, submitted policy sources, hashes, this report and the plan are outside the ignored output directories. Codex and additional traces have **not** been implemented or benchmarked in this release.
