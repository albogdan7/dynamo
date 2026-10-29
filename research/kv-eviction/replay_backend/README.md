# AISimulate-backed eviction research runner

This is an executable integration with **the same AISimulate core version pinned
by this Dynamo checkout**, not another Python cache simulator. It runs the native
SGLang-style scheduler, radix cache, allocation/locking, prefill, decode, and
virtual-time offline replay. It directly uses `aisimulate-core`, avoiding the
unrelated Dynamo deployment stack and Python extension build.

The first milestone is deliberately narrow: one aggregated worker, one DP rank,
stateless Python leaf selection, a pressure fixture, and native-LRU parity tests.
It is not yet wired into `sandbox/submit.py` or Dynamo's public replay bindings.

## Run

From this directory, with Python 3 and the repository's Rust 1.96.1 toolchain:

```bash
python3 prepare_core.py
cargo build --locked
python3 evaluate.py --native-lru
python3 evaluate.py --policy policies/lru.py
python3 evaluate.py --policy policies/mru.py
```

`prepare_core.py` downloads the exact pinned crate, verifies its SHA-256, and
applies `custom_eviction.patch`. It does not change Dynamo's root dependencies.
The patch also restores two missing upstream JSON test fixtures and adjusts
their `include_str!` paths so the crate's native tests compile. Those fixtures
come unchanged from `ai-dynamo/aisimulate` revision
`9572f2e6829d01900e35203aed6b029b1848df9c`, under
`python/aisimulate/tests/fixtures/deepep_ll_{dtype_candidates,calibration_viability}.json`.
That is the source revision recorded in the published crate's `.cargo_vcs_info.json`;
these test-only files do not change simulation behavior.
The downloaded source and build output are ignored by Git. Repeating preparation
is a no-op when archive and patch identities match. After changing the patch,
move the existing `.deps/aisimulate-core` aside and prepare a clean copy.
Use `--archive /path/to/core.crate` to prepare from an already downloaded archive.

`evaluate.py` prints the metrics and saves JSON under `outputs/`. Use `--spec`
to supply another AISimulate `ReplaySpec`, `--output` to select the output path,
and `--timeout` to bound the entire run. Arrival times in this format are **ms**,
unlike the old simulator's seconds. Explicit token IDs are required to preserve
prefix identity; do not replace production prefix hashes with arbitrary tokens.

The current fixture is a deterministic integration test, not a representative
production benchmark. Its **fixed 2 ms prefill / 1 ms decode timings are uncalibrated**.
They prove scheduling and policy wiring, not GPU latency or relative policy speed.

## Policy contract

```python
class EvictionPolicy:
    def select_victim(self, candidates):
        return min(candidates, key=lambda c: c["lru_rank"])["block_id"]
```

Only unlocked, non-root radix **leaves** reach the policy. The engine determines
how many tail pages to remove from a selected compressed edge, maintains parent
dependencies, and protects active references. A candidate contains:

| Field | Meaning |
| --- | --- |
| `block_id` | Opaque radix node identifier; return one from the current list |
| `lru_rank` | Engine's eligible-leaf recency order, 0 = oldest |
| `size_tokens` | Tokens on this compressed leaf edge; variable-sized |
| `prefix_tokens` | Number of ancestor tokens before this edge |

This is intentionally a new metadata contract. The old sandbox's
`creation_time`, `last_access_time`, and `num_hits` are not supplied: the native
cache does not provide all of those semantics, and inventing them would be misleading.
Token IDs, hashes, trace contents, and future requests are not in the messages.

Decisions must be deterministic functions of the current candidates. Each call
gets a fresh policy namespace and instance from a frozen source snapshot whose
hash is verified at startup. Imported-module state and external side effects
remain outside this reset contract. The engine can roll back tentative admissions, so
stateful policies need a separate checkpoint/rollback design before support.
The scoped thread-local hook is restricted by the runner to synchronous,
single-worker SGLang replay; it must not be used for parallel engines.

`policy_worker.py` runs in a separate process with isolated Python import mode.
Startup and each decision have a five-second timeout. Invalid IDs, exceptions,
or timeouts fail the run instead of scoring an LRU fallback. The wrapper has a
whole-run timeout and terminates the process group on expiry.
**This is process separation, not an OS security sandbox. Run trusted local
policies only.** Filesystem/network restrictions and memory/output limits are
required before adversarial or unrestricted generated-code evaluation.

## Evidence and metrics

The result records the engine version, source/patch/worker/dependency-lock hashes,
full replay spec, policy source, native summary, per-request metrics, and every
custom decision attempt. Decision attempts can include rolled-back admissions;
they are diagnostic evidence, not committed eviction counts. Native LRU uses `-`
as the policy argument and has no custom decision log.

TTFT is reported by the engine from arrival through first token. Decode continues
after that token and consumes simulated resources. The old Python FIFO clock
and `50 + 3 * missed_blocks` formula are not used here.

## Verify

```bash
python3 -B -m unittest discover -s . -p test_replay.py -v
cargo test --locked -p aisimulate-core --lib custom_eviction
```

Tests cover Python/native LRU parity, different victims and reuse under MRU,
repeatability, waiting through decode completion, invalid decisions, timeouts,
and rejection of multiple workers. Native cache tests cover protected leaves,
parent preservation, invalid-ID atomicity, and cleanup of the scoped hook.

## Remaining project work

1. Add a verified production-trace loader with faithful prefix/session identities.
2. Add calibrated model/GPU timing. This runner currently uses native timing
   configurations; it does not install Dynamo's external profile callback.
3. Add memory/network/filesystem confinement and bounded transport for untrusted policies.
4. Separate tuning, validation, and held-out evaluation in the search harness.
5. Port selected policies to real SGLang and compare rankings on matching workloads.
6. If needed, upstream an engine-owned policy provider API instead of this narrow
   research patch, with transactional state for stateful policies.

The existing `../sim/` and `../sandbox/` remain the legacy prototype. Results
from the two engines are not directly comparable.
