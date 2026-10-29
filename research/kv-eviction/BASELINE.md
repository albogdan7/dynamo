# Reproduce the baseline and pin one trace

These commands work from the repository root with Python 3.12+, without a serving simulator, Docker or model credentials.

```bash
python3 research/kv-eviction/baseline.py reproduce
python3 research/kv-eviction/baseline.py fetch-trace
```

Reproduction reads the original simulator, runner and policies from pinned Git commit `dc61f2bbfbed0de800a7b2399188be82737f5bd0`. A shallow clone missing that commit needs `git fetch origin dc61f2bbfbed0de800a7b2399188be82737f5bd0`. It runs plain LRU and leaf-first LRU twice, compares all scoring values except simulator wall time, and saves the policy source, command output and metrics under `research/kv-eviction/experiments/reproduction/`. An existing output directory is never overwritten; choose another with `--output experiments/my-reproduction`.

| Evaluation metric | LRU | Leaf-first LRU |
| --- | ---: | ---: |
| Mean proxy TTFT | 60.57 ms | 60.54 ms |
| Block-hit rate | 75.71% | 75.77% |
| Evictions | 843 | 840 |

These compare the same 312-request evaluation split. The larger comparison previously shown mixed LRU tuning values with leaf-first evaluation values. This is a cache-only proxy (`50 ms + 3 ms/missed block`), not serving latency: it cannot measure ITL or achieved goodput. Compact reproduction evidence is saved in [evidence/legacy-reproduction.json](evidence/legacy-reproduction.json).

The downloader pins the public Mooncake tool/agent source revision and SHA-256, checks all 23,608 requests, millisecond arrivals, 512-token hash counts and prefix ancestry, and saves the original JSONL in `.cache/`. `kv_experiment/traces.py` creates contiguous cold-cache tuning/evaluation windows for the serving experiment; the native loader remains responsible for prefix lowering and partial blocks. The standard window offsets are 0 and 11,804, with explicit optional arrival-time scaling.

The automated Claude experiment and serving metrics are delivered in the next PR. This PR adds only baseline reproduction, trace provenance/window preparation and validation.
