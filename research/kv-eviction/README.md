# KV eviction experiments

Full-trace heuristic comparison with KV occupancy measurements: [results](FULL_TRACE_RESULTS.md), [report](reports/full-trace-baselines.html) and [commands](#full-trace-heuristic-baselines). The agent reports below describe earlier subset pilots.

Run from the fork repository root. This release supports **Claude Code or Codex** with **Mooncake tool/agent, Mooncake conversation, AgentX and a qualified FreeInference subset**. It launches the agent, evaluates submitted Python policies in containers, selects using tuning data, and saves an independent evaluation. [TRACE_SOURCES.md](TRACE_SOURCES.md) defines each source, qualification and schedule.

The [HTML report](reports/kv-eviction-results.html) contains the current fresh agent and heuristic comparison. [EXTENSION_RESULTS.md](EXTENSION_RESULTS.md) and [REPORT.md](REPORT.md) retain earlier comparisons and release findings.

The [shareable HTML report](reports/kv-eviction-results.html) shows tuning and held-out metrics for each trace, percentage changes from LRU and policy source dialogs. It is a single offline file with the report generator and compact evidence embedded as a downloadable ZIP. [REPORT_EXPORT.md](REPORT_EXPORT.md) explains sharing and regeneration:

```bash
python3 research/kv-eviction/export_report.py
```

## Setup

Requires Python 3.12+, Rust/Cargo (tested with 1.98), Docker, and signed-in Claude Code with `--restricted` and `--safe-mode` (tested with 2.1.285). No GPU or API key is required when using an existing Claude subscription. Standard CLI token/model access limits still apply.

```bash
python3 research/kv-eviction/prepare_native.py
```

This downloads a checksummed AISimulate crate, applies the small research-only [native/core.patch](native/core.patch), and builds the adapter using its checked-in Cargo lockfile. Downloads/builds live in ignored directories. It does not change Dynamo's root dependency or require the Linux-only simulator wheel. Native scheduling, active KV protection, prefill and decode remain in the pinned engine.

```bash
python3 research/kv-eviction/experiment.py fetch-traces
```

Downloads the pinned public [Mooncake tool/agent trace](https://github.com/kvcache-ai/Mooncake/blob/bd7526307049ee1612cfcb90daceffb128994ade/FAST25-release/README.md): 23,608 requests, timestamps in milliseconds, 512-token prefix hashes. The downloader checks SHA-256, request counts, ordered finite arrivals, lengths, hash counts and prefix ancestry. The native loader handles prefix lowering and partial blocks.

## Run

[BASELINE.md](BASELINE.md) also documents standalone baseline/trace commands that do not need the serving runner.

Reproduce both legacy policies twice from pinned Git objects, regardless of working-copy policy edits (a shallow clone may first need `git fetch origin dc61f2bbfbed0de800a7b2399188be82737f5bd0`):

```bash
python3 research/kv-eviction/experiment.py reproduce
```

Compare native baselines at the original arrival rate:

```bash
python3 research/kv-eviction/experiment.py baseline \
  --window-requests 1000 --output experiments/original-rate-baselines
```

The original rate overloads this illustrative single-worker profile. For a qualified lower-load pilot, explicitly spread arrivals by a factor of ten; this changes the workload timing and is recorded in the manifest:

```bash
python3 research/kv-eviction/experiment.py run \
  --agent claude --model claude-sonnet-5-5 \
  --trace mooncake-toolagent --window-requests 256 --arrival-scale 10 \
  --cache-tokens 131072 --ttft-slo-ms 1000 --itl-slo-ms 50 \
  --submissions 5 --output experiments/my-claude-run
```

Use a model your account supports; an alias such as `sonnet` is resolved and logged. Each submission counts against the budget, including duplicates and failures. Claude can read/edit its private workspace but has no shell, code-running or network tools, customizations or MCP servers. The trusted controller supplies tuning feedback between invocations. Policy execution happens separately in a pinned, non-root, read-only Docker container with no network, host credentials or trace mount; resource and decision-time limits apply. CLI credentials stay with the CLI, outside the policy container.

The default windows are source rows `[0,1000)` and `[11804,12804)`. `--window-requests 256` uses the first 256 rows of each window. No shuffling or warmup; cache, policy and scheduling state restart for every replay. Source overlap does not establish session independence. This is fixed-arrival replay, not a dependency-aware agent benchmark.

Outputs are relative to `research/kv-eviction/`, must stay inside that directory, and are never overwritten. Reusing an output name fails. With no `--output`, a timestamped name is generated.

For Codex, use the same command with `--agent codex --model gpt-6.1-sol` and a separate output directory. The native Codex CLI and matching `codex-code-mode-host` must be installed (tested with 0.164.0). The runner copies the trusted runtime into a write-protected workspace subtree; agent commands cannot read the trace or credentials, write the runtime/configuration, use the network, or invoke shell/app/plugin/subagent tools. It requires a successful `policy.py` file-change event; a completed CLI turn alone is not accepted as a submission. Codex receives the current policy in its prompt because shell/read commands are disabled. [Official permissions documentation](https://learn.chatgpt.com/docs/permissions).

Codex JSON reports token usage but no server-resolved model ID or monetary cost. The runner records the exact requested model ID and leaves unavailable fields null with a reason.

## Fixed heuristic baselines

Every new run also evaluates allocation-order FIFO, MRU, actual-access LFU, two-tier SLRU and random replacement with seeds 0, 1 and 2. All use the same native eligibility and scoring. [HEURISTIC_BASELINES.md](HEURISTIC_BASELINES.md) defines the variants and commands for fresh matched agent searches, auditing and report generation.

## Additional traces

FreeInference downloading also requires the `zstd` command. Fetch any registered source with the same command:

```bash
python3 research/kv-eviction/experiment.py fetch-traces --trace agentx
```

Use `--trace mooncake-conversation` with the same fixed-arrival options as tool/agent. AgentX and FreeInference use **complete sessions**, so `--sessions-per-split` replaces request-window sizing:

```bash
python3 research/kv-eviction/experiment.py run \
  --agent codex --model gpt-6.1-sol --trace agentx \
  --sessions-per-split 2 --cache-tokens 196608 --submissions 5 \
  --output experiments/my-agentx-run
```

```bash
python3 research/kv-eviction/experiment.py run \
  --agent claude --model claude-sonnet-5-5 --trace freeinference \
  --sessions-per-split 3 --cache-tokens 81920 --submissions 5 \
  --output experiments/my-freeinference-run
```

The policy interface, scoring and output layout are unchanged. Session runs also retain the selected raw source rows and canonical version-2 agentic-Mooncake trace. Model/provider KV domains are separated; unknown partial tails never gain invented sharing. Read [TRACE_SOURCES.md](TRACE_SOURCES.md) before interpreting these filtered subsets. The initial FreeInference evaluation has an all-pass SLO, which is reported as a limitation.

## Metrics

Compare mean/p95 simulated TTFT, mean/p95 simulated ITL, SLO goodput in requests/s, SLO attainment, first-admission input-token hit rate, recomputed input tokens and evictions. The report includes changes against both baselines. Latency summaries use completed positive-output requests; failures remain in the SLO denominator and raw records. Recomputed tokens include repeated prefill after preemption, so they need not equal input tokens minus first-admission reuse.

**ITL uses individual consecutive token gaps**, pooled across completed requests. Longer outputs contribute more gaps. Percentiles use nearest rank. Per-request p95 gaps determine SLO eligibility: **TTFT ≤ 1,000 ms and p95 ITL ≤ 50 ms**. One-token output has no ITL criterion; zero-output requests are explicitly excluded from SLO eligibility. Goodput divides passing requests by simulated first-arrival-to-last-terminal duration, including drain. Agent runtime and simulator wall time are separate.

**Timing is the pinned engine's uncalibrated polynomial model. These are simulation estimates, not measured GPU results.** Thresholds and load must be frozen before agent comparison. Near-zero/all-pass attainment is flagged. Do not rank models from an overloaded or one-off pilot. This release records CLI token usage and API-equivalent cost estimates separately from actual subscription billing.

Policy IPC/Python decision wall time does not advance the simulated clock; real serving overhead is unvalidated.

The legacy experiment uses `50 ms + 3 ms/missed block`; it cannot measure ITL or achieved goodput. Native candidate metadata uses allocation/release counters and LRU queue rank, not legacy timestamps/hit counts. Native release order has no ties; its creation-order tiebreak therefore matches LRU. Do not claim the legacy leaf-first gain transfers to native serving.

## Full-trace heuristic baselines

[FULL_TRACE_BASELINE_SPEC.md](FULL_TRACE_BASELINE_SPEC.md) defines this experiment and [FULL_TRACE_RESULTS.md](FULL_TRACE_RESULTS.md) interprets the current results. It replays complete public workloads with their original timestamps under LRU, FIFO, MRU, LFU, SLRU and random seeds 0, 1 and 2. There are no agent calls, credentials, arrival scaling or tuning. Frozen settings live in [configs/full-trace-baselines.json](configs/full-trace-baselines.json). Run from the repository root; Python 3.12+, Rust/Cargo, Docker and `zstd` are required.

```bash
python3 research/kv-eviction/prepare_native.py
python3 research/kv-eviction/baseline_comparison.py fetch \
  --config research/kv-eviction/configs/full-trace-baselines.json
python3 research/kv-eviction/baseline_comparison.py run \
  --config research/kv-eviction/configs/full-trace-baselines.json \
  --output research/kv-eviction/experiments/$(date +%Y-%m-%d-%H-%M-%S)
python3 research/kv-eviction/baseline_comparison.py audit \
  --run research/kv-eviction/experiments/<that directory> \
  --evidence research/kv-eviction/evidence/<new name>
python3 research/kv-eviction/export_report.py \
  --baseline-evidence research/kv-eviction/evidence/full-trace-baselines \
  --output research/kv-eviction/reports/full-trace-baselines.html
```

`fetch` downloads and verifies about 2.3 GB of pinned sources, then ledgers every source request (see [TRACE_SOURCES.md](TRACE_SOURCES.md#full-release-coverage)). Traces with any request the replay contract cannot represent are blocked rather than filtered. `run` replays each ready trace once per policy from a cold cache, plus native LRU again (determinism), LRU as a container callback (callback/native parity), LRU without telemetry (instrumentation parity), and a bounded-prefix run at a 10x finer occupancy cadence (sampling error). Each job is audited as it finishes. A failed job keeps its logs and a `failure.json`, never a substitute score. Rerunning `run` with the same output directory resumes: it skips completed, audited jobs whose workload, configuration, policy, runtime and binary hashes match, and preserves interrupted attempts as `<job>.attempt-N`. The current sweep took about one hour on an 18-core machine with eight parallel jobs, mostly Docker round trips for callback policies.

`audit` touches no simulation. It re-verifies every job's inputs, recomputes metrics and occupancy from raw request, token and occupancy files, checks request coverage and parity, and writes compact, checksummed evidence (`results.json`, `job-receipts.json`, `source-inventory.json`, policy sources and the run manifest). It fails on a failed or incomplete job, changed inputs or inconsistent arithmetic. `export_report.py --baseline-evidence` verifies the evidence checksums and writes a self-contained HTML page (one table per trace, every heuristic as a column, changes from LRU) and a sibling CSV in long format. The page embeds the compact evidence and generator for offline sharing.

## KV occupancy telemetry

Add `"telemetry": {"sample_interval_ms": 100}` to a replay configuration to record per-rank KV occupancy from the simulator's native virtual-time telemetry API. Each replay then writes `occupancy.jsonl`: active (referenced or reserved), inactive (reclaimable eviction candidates), free and total 512-token blocks, running/waiting requests and interval preemptions. Without the key, telemetry is disabled; enabling it does not change request, token or eviction records.

`metrics.json` gains an `occupancy` summary from `kv_experiment/occupancy.py`. Physical occupancy is active plus inactive. Statistics are duration-weighted with a sample-and-hold convention: a sample covers the interval until the next one, and the closing sample, which must coincide with the last terminal request, has no duration. Each series reports the mean, time-weighted p95 and sampled peak, plus the fraction of time at or above 95% and 100% of capacity, for the whole run (including idle time) and separately for intervals that start with a request in the system. Peaks and threshold fractions are sampled estimates.

Every sample must satisfy `active + inactive + free = capacity`, using the engine's own free list rather than a derived value. The independent audit (`evidence/audit_run.py`) recomputes the summaries and checks telemetry preemptions against request readmissions. A full physical cache of reclaimable blocks is not active-memory pressure.

## Saved files and retrieval

```text
experiments/my-claude-run/
  manifest.json                 # Source/config/CLI/model/trace/SLO/budget
  controller-source.zip         # Exact research sources at run setup
  config.json
  tuning-trace.jsonl
  evaluation-trace.jsonl
  prompt.md
  baselines.json
  native-lru-{tuning,evaluation}/
  leaf-first-lru-{tuning,evaluation}/
  submissions/YYYY-MM-DD-HH-MM-SS[-02]/
    policy.py
    submission.json             # Score/status/hash/ordinal; failures retained
    prompt.md
    agent-command.json
    agent.jsonl
    agent-stderr.txt
    agent-metadata.json         # Resolved model, usage, cost when available
    tuning/
      metrics.json
      requests.jsonl
      tokens.jsonl              # Actual native output-token timestamps
      request-scores.jsonl
      native-report.json
      evictions.json
      native-stderr.txt
      policy-stderr.txt
  search-summary.json
  selection.json
  selected-policy.py
  evaluation.json
  evaluation/                   # Same raw timing/accounting files
  comparisons.json
  summary.md
```

IDs use `America/Chicago`; metadata stores ISO timestamps with UTC offsets. A collision gets `-02`, `-03`, etc. Invalid policy IDs, exceptions or timeouts fail the submission; there is no scored LRU fallback. Failed replays keep their available logs but have no successful `metrics.json`.

Re-evaluate the selected policy without calling an agent (choose a fresh output path):

```bash
python3 research/kv-eviction/experiment.py evaluate \
  research/kv-eviction/experiments/my-claude-run \
  --output experiments/my-claude-repeat
```

The command verifies policy, trace, configuration and native-binary checksums, then checks that the new metrics match the saved evaluation. It writes `replay-check.json`; a mismatch fails clearly. `--allow-native-rebuild` explicitly permits testing a different native build while still requiring the saved performance to match; the receipt records both binary hashes.

Rescore the raw timing files independently of the agent and simulator, without changing the saved files:

```bash
python3 research/kv-eviction/experiment.py rescore \
  research/kv-eviction/experiments/my-claude-run/evaluation \
  --ttft-slo-ms 1000 --itl-slo-ms 50
```

Full run outputs are ignored by Git. The mutable agent workspace/runtime can be excluded from export because submitted source and runtime hashes are already retained. Export them explicitly for retrieval; the plan, report and compact evidence belong outside this directory:

```bash
tar --exclude='*/agent-workspace' -czf research/kv-eviction/experiments/my-claude-run.tar.gz \
  -C research/kv-eviction/experiments my-claude-run
```

## Verification

A standalone test environment avoids importing the full serving stack:

```bash
uv venv --python 3.12 research/kv-eviction/.venv
uv pip install --python research/kv-eviction/.venv/bin/python pytest ruff
KV_RUN_CODEX_TESTS=1 KV_RUN_CONTAINER_TESTS=1 research/kv-eviction/.venv/bin/python -m pytest \
  -q -c research/kv-eviction/pytest.ini research/kv-eviction/tests
```

Container tests use the real Docker boundary; Codex tests use the installed native CLI permission boundary without a model call. Omit the corresponding environment flag to skip tests when that runtime is unavailable. Native tests require the built adapter and are skipped when absent. The hand-calculated metrics test distinguishes tail ITL from average TPOT and checks failed requests and exact SLO boundaries. Other tests cover queueing/accounting, callback/native LRU equivalence and repeatability, malformed traces, invalid policies, deadlines, cleanup and host-file/write/network denial. Actual model trials and CLI boundary probes are separate manual evidence, not mocked tests.
