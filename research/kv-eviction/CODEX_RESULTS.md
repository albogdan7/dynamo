# Codex comparison

2026-10-29. `gpt-6.1-sol` and `claude-sonnet-5-5`, five submissions each, same Mooncake tool/agent 256-request tuning/evaluation windows, 131,072-token cache, tenfold inter-arrival timing, native polynomial timing, 1,000 ms TTFT / 50 ms per-request p95 ITL SLO.

**Both searches selected release-history heuristics and produced identical held-out performance. The submitted source differs (binary second-chance versus release-count ranking); identical metrics do not prove the policies are universally equivalent.** This is one pilot per model, not evidence that either model is better.

| Evaluation metric | Native LRU | Claude | Codex |
| --- | ---: | ---: | ---: |
| Mean TTFT (ms) | 1,316.24 | 1,290.04 | 1,290.04 |
| p95 TTFT (ms) | 3,218.01 | 3,218.01 | 3,218.01 |
| Mean ITL (ms) | 27.8487 | 27.8048 | 27.8048 |
| p95 ITL (ms) | 34.01 | 34.01 | 34.01 |
| Goodput (req/s) | 0.105761 | 0.105794 | 0.105794 |
| SLO passing requests | 46/256 | 46/256 | 46/256 |
| Input-token hit rate | 37.1234% | 38.2122% | 38.2122% |
| Evictions | 2,153 | 2,111 | 2,111 |

Codex completed five valid submissions. Its individual tuning trials and selected source are retained in `evidence/codex-mooncake-toolagent/`; full local artifacts are under `experiments/codex-mooncake-toolagent/`. Its source snapshots retain the runtime implementation used for the run. Failed setup probes are preserved separately under `experiments/codex-smoke-probe/` and received no performance scores.

Timings remain **uncalibrated simulation**, not GPU measurements. All 20 tests pass, including actual native CLI runtime permissions, hidden-file/write/network denial and configuration-injection prevention. The selected-policy replay matched exactly; the independent auditor checked all ten saved replays. Shell tools are disabled for both agents; Claude can read its policy using file tools, whereas Codex receives the same source in its prompt and edits through the patch tool. Codex's CLI does not return a server-resolved model ID or monetary cost; neither value is invented.
