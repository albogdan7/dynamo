# Fixed heuristic comparison

All controls choose only the native inactive, unreferenced, unpinned candidates. They use the same worker, capacity, trace, SLO and timing model as the agent submissions. Definitions and executable sources are in `kv_experiment/baselines.py`; each run freezes them in its manifest and saves the actual files.

| Control | Exact rule |
| --- | --- |
| LRU | Native inactive queue head |
| FIFO | Oldest physical allocation; explicitly allocation-order FIFO rather than cache-publication FIFO |
| MRU | Native inactive queue tail |
| LFU | Fewest committed physical-copy uses, then native LRU |
| SLRU | Two tiers: fewer than two uses are probationary; other copies are protected. Native LRU within each tier. This is the SGLang-style hit-threshold variant, not a bounded protected queue. |
| Random | Uniform candidate choice; seeds 0, 1 and 2 each replayed independently from fresh PRNG state. Candidate input order is normalized by native LRU rank. No seed selection or averaging. |

`hit_count` starts at one for the initial computed use and increments on each committed cached-prefix activation, including concurrent readers and committed readmissions. Canceled reservations and release events do not increment it. Counts belong to physical copies and reset on reallocation. `last_access_order` is a separate logical clock, preserving the existing creation/release counters so instrumentation does not silently alter earlier policies.

The legacy leaf-first control is still retained in raw results as a native-LRU equivalence check, but omitted from the visible report because it is redundant under unique native release order.

## Run and regenerate

From the repository root, with Docker, both signed-in agent CLIs, the native Codex helper, Rust/Cargo and zstd available:

```bash
python3 research/kv-eviction/prepare_native.py
python3 research/kv-eviction/run_comparison.py \
  --output experiments/my-heuristic-comparison --jobs 4
```

The command launches eight **new** agent searches: five submissions each from `claude-sonnet-5-5` and requested `gpt-6.1-sol`, on each of the four fixed workloads. The agents receive all baseline **tuning** scores and can use actual access counters. This is a fresh experiment, not a relabeling of the earlier policies. Held-out evaluation is never returned as feedback. All nine baseline variants, including the redundant leaf-first control, are replayed on both splits; the random seeds are fixed before the search.

To audit raw timing arithmetic, replay each selected policy again, and package the compact evidence in a **fresh** destination:

```bash
python3 research/kv-eviction/collect_comparison.py \
  --runs research/kv-eviction/experiments/my-heuristic-comparison \
  --output research/kv-eviction/evidence/my-heuristic-comparison \
  --activate-report
```

The collector refuses incomplete runs, mismatched raw sources/configuration, failed arithmetic or replay checks, and changes in native-LRU metrics relative to the original controls. Each raw run stores an exact controller-source ZIP and SHA-256 hashes. Compact evidence retains every submitted source, score and failure; it does not include raw token events or agent runtimes.

`evidence/current-comparison.json` selects the completed evidence directory used by `export_report.py`. The `--activate-report` option updates that pointer only after collection succeeds. Then run:

```bash
python3 research/kv-eviction/export_report.py
```

For an additional fresh raw audit during HTML generation, pass `--raw-root` pointing to the directory that contains the eight original run folders. The HTML baseline selector switches the added comparison column while keeping LRU and both selected agent policies visible. Every source remains available in its dialog and embedded reproduction bundle.
