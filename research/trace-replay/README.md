# Mooncake and AgentX trace replay

Download pinned public trace bytes, then replay them with AISimulate's existing
Mooncake and Weka loaders. Requires Linux and Python 3.12+; no GPU, model weights,
or Dynamo build is needed. Run these commands from the repository root. The
AISimulate pin matches this fork's runtime dependency.

```bash
uv venv --python 3.12 research/trace-replay/.venv
uv pip install --python research/trace-replay/.venv/bin/python \
  --index-url https://pypi.nvidia.com --extra-index-url https://pypi.org \
  'aisimulate==0.13.0.dev202610160000000073'
source research/trace-replay/.venv/bin/activate

python research/trace-replay/fetch.py mooncake-toolagent
aisimulate predict -c research/trace-replay/mooncake.yaml \
  --capture-per-request --output-dir research/trace-replay/output/mooncake

python research/trace-replay/fetch.py agentx
aisimulate predict -c research/trace-replay/agentx.yaml \
  --capture-per-request --output-dir research/trace-replay/output/agentx
```

For Mooncake conversation, use the same config with a source-path override:

```bash
python research/trace-replay/fetch.py mooncake-conversation
aisimulate predict -c research/trace-replay/mooncake.yaml \
  --set 'traffic.source.paths=[research/trace-replay/data/mooncake-conversation.jsonl]' \
  --capture-per-request --output-dir research/trace-replay/output/conversation
```

The downloader checks SHA-256 on both new and cached files. It publishes a file
only after verification; interrupted or invalid downloads never become cached
traces. `--output-dir` changes the download directory; update the config path
accordingly. Raw traces and simulation outputs are ignored by Git.

## Replay semantics

- **Mooncake:** full tool/agent (23,608 requests) or conversation (12,031 requests)
  release, with the original 512-token prefix identities. `speedup: 0.1` explicitly
  spaces arrivals ten times farther apart, as in the earlier pilot; use
  `--set traffic.load.speedup=1.0` for original timing. Cache: 131,072 tokens.
- **AgentX:** the first 16 complete plays, including subagents, copied verbatim
  from the pinned Weka release. This is a bounded sample, not the full 393-play
  benchmark. No requests or plays are filtered by length or outcome. The native
  loader handles sequence/spawn/join dependencies and 64-token source blocks;
  `agentic_lanes: 1` runs plays serially with completion-relative dependencies.
  Nested timestamps use AISimulate's corpus-validated `auto` interpretation, which
  is logged by the CLI. Cache: 1,835,008 tokens, with 512-token engine blocks.

Both examples use the native vLLM scheduler and its **uncalibrated polynomial
timing model**, matching the earlier experiments' timing approach. TTFT/ITL are
simulation estimates, not H200 measurements or an official AgentX score. The
worker's model identity does not reproduce the different models in the source.
These configs are for loading and checking traces, not comparing eviction
policies or making deployment decisions.

The CLI saves `prediction.json` and per-request records under `--output-dir`.
Inspect completed/failed request counts as well as mean/p95 TTFT and ITL; a low
latency for only a fraction of requests is not a successful replay. ITL follows
AISimulate's native token-gap report semantics. Per-request `itl_ms` is an
average over that request's gaps; its percentile across requests differs from
the report's pooled token-gap percentile. Use a fresh output directory for each
run.

## Validation

The commands above were run with the pinned Linux wheel. All source requests
completed, including all 16 AgentX plays and their nested requests.

| Trace | Completed / source | Mean / p95 TTFT (ms) | Mean / p95 ITL (ms) |
| --- | ---: | ---: | ---: |
| Mooncake tool/agent | 23,608 / 23,608 | 3,991.51 / 19,872.09 | 31.20 / 34.02 |
| Mooncake conversation | 12,031 / 12,031 | 4,389.34 / 20,995.00 | 29.59 / 34.02 |
| AgentX first 16 plays | 4,439 / 4,439 | 99.64 / 257.28 | 16.00 / 26.18 |

AgentX's four zero-output requests remain in the replay as prompt-only requests;
they have no TTFT/ITL and are excluded from those latency samples. The native
loader resolved nested timestamps as `absolute`. Recursive source counting
matched all 4,439 report records. Every generated request had ordered timestamps;
TTFT and per-request mean ITL were checked against first/last token times. Mean
TTFT and pooled mean ITL also recomputed from those records match the reports.
P95 ITL comes from the native report, not from averaging per-request ITL values.

As a load sanity check, setting Mooncake tool/agent `traffic.load.speedup=0.01`
reduced mean TTFT to 2,855.52 ms and mean ITL to 30.31 ms, with full coverage.
This checks simulator behavior, not hardware accuracy.

## Sources

The source revisions and byte hashes are in [fetch.py](fetch.py), reused from
the earlier trace-loading PRs.

- [Mooncake FAST'25 release](https://github.com/kvcache-ai/Mooncake/blob/bd7526307049ee1612cfcb90daceffb128994ade/FAST25-release/README.md),
  revision `bd7526307049ee1612cfcb90daceffb128994ade`, Apache-2.0.
- [AgentX / SemiAnalysis Weka release](https://huggingface.co/datasets/semianalysisai/cc-traces-weka-062126/blob/23f152f6f0f9399a85901b89a6458def0ef16729/README.md),
  revision `23f152f6f0f9399a85901b89a6458def0ef16729`, Apache-2.0.
