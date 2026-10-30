# Share and regenerate the results tables

Send **`reports/kv-eviction-results.html`** as a file attachment. It opens offline in a browser, including on a phone. It shows tuning and held-out performance for each trace, comparing LRU, FIFO, MRU, LFU, two-tier SLRU, all three random seeds, and both selected agent policies as adjacent columns. Best values are shaded green, including ties at displayed precision; all-equal rows and diagnostic eviction counts/observation spans are unranked. Each metric includes relative change from LRU. Buttons beside the values open the exact policy source in a modal. Styles and the small dialog script are inline; there are no external assets or network requests. Browsers can print it or save it as a PDF.

The HTML includes a **Reproduction files** link with an embedded ZIP containing the generator, compact evidence, all 40 submitted policy sources and original controller source snapshots. No companion file is needed to read or forward the report. A recipient who wants to regenerate it can download and extract that ZIP, then run from its root:

```bash
python3 research/kv-eviction/export_report.py
```

Python 3.12+ and its standard library are sufficient. No installation, model call, account, GPU, Docker or internet connection is required. The same command works in a full fork checkout. It creates HTML, Markdown, full precision evaluation and submission CSVs, agent usage totals, `reproduction.zip` and a checksummed report manifest. The ZIP is also saved separately for browsers whose file preview does not allow embedded downloads.

The compact bundle excludes raw traces, token events, agent transcripts, credentials and runtimes. It can regenerate the numerical report and verify policy/source integrity and saved audit receipts. A full Git checkout and the documented tools are required to run new experiments. Fresh agent generation is nondeterministic; replaying a recorded policy is the numerical reproduction path.

To independently recompute raw timing arithmetic, provide the directory containing all eight original full run folders:

```bash
python3 research/kv-eviction/export_report.py \
  --raw-root research/kv-eviction/experiments/heuristic-rerun-2026-10-29 \
  --output /tmp/kv-eviction-audited-report
```

Missing raw files fail clearly rather than falling back to receipts. The JSON manifest records which verification mode ran. Either mode rejects changed policy or source hashes, inconsistent selection, failed receipts and mismatched comparisons. The raw audit additionally checks request/token arithmetic, workload and configuration hashes, and original metrics against the compact evidence.

HTML, Markdown, CSV and the reproduction ZIP are byte deterministic with identical inputs and verification mode. The verification mode is recorded in the JSON manifest; the concise HTML contains only the results tables. Regenerating from compact evidence preserves the same HTML and numerical results. The manifest records input/output checksums and the generator checksum. Generated reports must be rebuilt from the script and evidence rather than edited manually.
