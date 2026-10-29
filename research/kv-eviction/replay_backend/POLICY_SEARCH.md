# Bounded coding-agent policy experiment

The first agent experiment uses `fixtures/pressure.json` as **tuning data**.
It is a 12-request integration fixture, not a held-out benchmark. The agent is
instructed not to inspect the trace, simulator, or saved replay JSON (which
contains the full replay spec). It sees the policy interface and the evaluator's
printed aggregate feedback. This instruction boundary is not a security sandbox.

## Agent task

Write up to six deterministic, stateless candidate policies in
`policies/agent_candidate_N.py`, then copy the best tested candidate to
`policies/agent_policy.py`.

The interface is `EvictionPolicy.select_victim(candidates) -> block_id`.
Candidates are dictionaries containing:

- `block_id`: opaque identifier to return, not a numeric scoring feature.
- `lru_rank`: recency rank, with 0 the oldest eligible leaf.
- `size_tokens`: size of the compressed leaf edge.
- `prefix_tokens`: length of the ancestor prefix.

Use only those metadata, with no imports, file access, external state, or
assumptions about future requests. Do not modify the evaluator or engine.

Evaluate each candidate using:

```bash
python3 -B evaluate.py --policy policies/agent_candidate_N.py
```

Minimize committed prefill tokens and maximize prefix reuse. Require all 12
requests to complete successfully. Do not optimize the number of policy decision
attempts: it can include rolled-back admissions. The fixture's fixed timings
cannot demonstrate a latency gain from reducing prefill work.

Record each attempted heuristic, its scores, and the saved result path. Preserve
unsuccessful candidates too. Select based on observed results; a tie or a loss
to a baseline is a valid outcome. Do not claim generalization from this search.

## Evaluation boundary

This tests a coding agent proposing code and improving it through feedback.
It does not train the agent with RL. Future experiments need separate tuning,
validation, and hidden final workloads, matched search budgets across agents,
and calibrated execution timings before making performance claims.

## First completed experiment

A coding subagent proposed and evaluated six candidates using only the allowed
metadata and printed aggregate feedback. The coordinating agent reran both
baselines and the selected policy, verified identical replay-spec hashes, and
checked that saved policy hashes match the candidate source files.

| Policy | Eviction ordering | Committed prefill tokens | Prefix reuse |
| --- | --- | ---: | ---: |
| LRU baseline | Oldest | 84 | 41.67% |
| MRU baseline | Newest | 80 | 44.44% |
| Candidate 1 | Smallest edge, then oldest | 88 | 38.89% |
| Candidate 2 | Largest edge, then oldest | 84 | 41.67% |
| Candidate 3 | Deepest ancestor prefix, then oldest | 84 | 41.67% |
| Candidate 4 | Shallowest ancestor prefix, then newest | 84 | 41.67% |
| **Candidate 5 (selected)** | **Largest edge, then newest** | **80** | **44.44%** |
| Candidate 6 | Smallest edge, then newest | 88 | 38.89% |

All runs completed 12 requests. Mean and p95 TTFT were both 2 ms under the fixed
timing model. Candidate 5 saves 4 prefill tokens (4.76%) versus LRU but **ties
MRU**. This experiment establishes neither superiority over all baselines nor
generalization or latency improvement. The fixture was reused for selection;
there was no held-out test in this experiment.

The selected source is `policies/agent_policy.py`. All six candidate sources are
preserved. `agent_trial_summary.json` records exact ratios, source/workload
hashes, and local output paths. Full replay records remain in ignored `outputs/`.

```bash
python3 evaluate.py --policy policies/agent_policy.py
```
