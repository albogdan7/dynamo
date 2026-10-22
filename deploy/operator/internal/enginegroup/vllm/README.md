<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# vLLM Engine Group membership adapter

This package implements the Go membership adapter for vLLM's correlated external
Elastic EP API in [vLLM #62082](https://github.com/vllm-project/vllm/pull/62082).
It owns neither process launch, discovery registration nor physical release.

## Membership contract

The adapter calls every existing rank, including retirees, with the same operation
ID and absolute DP target plus each rank's frozen API instance ID. Native vLLM
status is authoritative for transaction phase, effective topology, process identity
and operation epoch.

Dynamo persists target/profile/process bindings and terminal correlation receipts.
It observes before replaying an ambiguous request and resubmits only the identical
target. It does not implement a second native collective state machine.

Supported shapes are contiguous append, high-rank-suffix retirement and re-append
of a fully retired suffix. ReplicaLayout maps each allocation to its complete
native-member range, including packed protocol layouts. Sparse survivor masking,
arbitrary selected retirement, native remapping and complete-world retirement
are unsupported. Membership support does
not imply safe physical shrink.

Direct native HTTP and existing Dynamo worker control routes share the same
request/status codec and transaction authority.

## Ownership

| Owner | Responsibility |
|---|---|
| Engine Group controller | Persist targets, order operations, correlate evidence and authorize exact release. |
| Workload manager | Allocate capacity, expose slots/Pod UIDs and enforce the supported release contract. |
| Engine launcher | Consume an incarnation-bound launch assignment and start the native engine. |
| vLLM | Commit membership and report native topology, lifetime and serving state. |
| Dynamo serving runtime | Verify serving progress and register eligible endpoints from native evidence and runtime/KV prerequisites. Honor outstanding withdrawal intent. |

The serving runtime does not wait for a second controller permission,
acknowledgment or status write. Preparation may leave existing members serving;
native readiness determines whether inference is actually available.

Engine-managed drain requires an integration-supplied ManagedDrainValidator.
It validates every frozen native process identity before effects and submission.
The native adapter does not query worker-specific endpoints; without a validator,
it rejects EngineManagedDrain.

## Integration boundaries

[#16929](https://github.com/ai-dynamo/dynamo/pull/16929) composes launch-only
bootstrap, Grove capacity, DGD rendering and native runtime resolution.
[#17018](https://github.com/ai-dynamo/dynamo/pull/17018) enables the existing
Python worker's readiness-driven serving and the narrow managed-growth pathway.

Launch consumption is not inference readiness. A joiner's API can depend on
membership preparation, so launch delivery and client construction cannot wait
for that API to become ready.

Retirement needs durable withdrawal and exact-incarnation terminal drain, not
discovery absence or a wrapper-local request count. Physical release additionally
requires the coordinator's exact Pod-UID/topology authorization.

Sidecar integration follows [DEP #14657](https://github.com/ai-dynamo/dynamo/issues/14657)
and [DEP #14897](https://github.com/ai-dynamo/dynamo/issues/14897). Remote native
controls, identity correlation, serving evidence and durable withdrawal must compose
with the serving runtime's health and KV lifecycle. This adapter's HTTP protocol
does not establish native gRPC or sidecar conformance.

## Validation

Tests cover native protocol validation, exact all-rank fan-out, plan-specific
preflight, ambiguous transport outcomes and restart recovery of durable bindings.


## Grove bootstrap

### Launch-only bootstrap

Each allocation uses one template. Its resolved launch assignment supplies stable
group and member identities, rank, rendezvous, initial/join role, and the
operation-bound DP target. All joiners receive the full resize target, not
`rank + 1`. Initial participants form one complete world without a synthetic grow.

The integration slice implements Pod-UID-bound launch assignments through the
`nvidia.com/dynamo-vllm-launch` Pod annotation and a Downward API volume:

```text
Initial world: immutable native declaration + initial size
Growth:       persisted capacity target + exact joining Pod UID
                                |
                      Go launch assignment
                                |
                  Pod annotation / Downward API
                                |
              launch-only Python entrypoint -> exec Dynamo vLLM worker
```

`VLLMGroveCapacity` composes Grove's physical observation with the launch profile's
native bindings. It reuses the member-clique `/scale` and publishes joining
assignments when Grove creates the Pods. Every joiner receives the complete
resize target. Initial formation uses the same template without a synthetic grow.
Assignments are immutable, published with Pod UID and resource-version
preconditions, and replayed without relaunching retained processes. A replacement
does not inherit the old Pod's assignment. Reusing a released slot requires explicit
restoration and a new launch binding; the composed planner currently rejects it.

Creation seeds an empty launch-binding record on the Engine Group. Before any
command is published, capacity reconciliation persists each slot's exact Pod UID
and assignment digest there, then waits for that cached observation. The record
survives Pod deletion; a new occupant or lost binding fails closed instead of
restarting initial formation. It records launch identity, not engine transaction
phases. Existing Group and Pod lifecycle finalizers must protect effects first.

The launcher has no Kubernetes credentials, engine control loop, proxy, or
discovery owner. It replaces itself with the assigned native server or Dynamo worker. A launch receipt makes
allocation readiness observable before the joiner's API is available; it proves
only assignment consumption, never membership, health, verification, or admission.
Container restarts are prohibited. The Pod-bound launch lifetime and vLLM's native
API instance ID remain distinct and require correlation by the runtime resolver.

Do not wait for inference readiness or registration before submitting the resize
that lets a joiner initialize. Conversely, do not register a joiner because its
Pod or launch receipt is ready.


## Worker serving integration

### Existing Python-worker transport

The Python worker forwards external ownership through
`/engine/control/scale_elastic_ep` and `/engine/control/is_scaling_elastic_ep`.
`NewWorkerClient` uses these routes with the same native request/status codec as
`NewClient`; vLLM still owns operation serialization, replay, and topology
certainty. Dynamo requires the operation and expected-instance IDs and does not
turn a lost response into rejection or take the legacy Ray restart path.
The existing Ray controls retain their behavior.

This transport requires #62082. A managed component declares `python3 -m
dynamo.vllm`; its launch-only bootstrap supplies rank and rendezvous without
replacing the existing worker lifecycle. Native-only launches are still usable by
the low-level adapter but cannot opt into this complete deployment pathway.

The worker starts its inference listener without discovery publication. Initial
formation and every committed growth must pass a one-token inference probe at
every rank. The worker runs this check and registers independently of the
controller. Proof is bound to the native API instance and committed operation/epoch;
the controller correlates observed evidence with its topology generation and Pod UID.
Preparation can keep the old topology serving; the native request gate, not the
duration of a resize call, determines whether inference is paused. Native evidence
is checked again after registration to fence an in-flight topology change.
Registration failure is inconclusive and retryable, not permission
to restart the engine.

The worker checks vLLM's process-local scaling and retirement gates before accepting
each inference request, not only during discovery polling. Direct `AsyncLLM` calls
do not pass through vLLM's HTTP admission middleware. Preparation alone does not
close admission; vLLM closes it when entering its native drain/commit path.
Requests racing discovery withdrawal return HTTP 503, not a generic internal error.

Managed launch assignments set `VLLM_ELASTIC_EP_DRAIN_REQUESTS=1` for initial and
joining allocations, overriding image defaults. Profile resolution rejects explicit
disabled or unresolved values. vLLM owns draining within the correlated resize;
the setting itself, discovery withdrawal, and a successful HTTP response are not
independent drain-completion evidence or authorization to delete a Pod.

For planned shrink, `EngineManagedDrain` lets membership submission follow durable
withdrawal-intent acceptance: vLLM performs admission closure and request draining
inside that transaction. Physical release still waits for completed native retirement,
with the same operation/epoch and API instance, draining enabled, and confirmed discovery
unregistration. The traffic journal retains that terminal proof after the endpoint
disappears. A missing endpoint or a discovery-only withdrawal cannot create proof.

The worker integration supplies `ValidateWorkerManagedDrain` to the native
adapter's `ManagedDrainValidator`. Preflight checks every old process against its
frozen API instance before preparatory effects, again for the exact target, and
before submission. The native adapter does not query worker endpoints itself;
without a validator it rejects `EngineManagedDrain`.

### Grove scale-in steering

The vLLM realization renders a Dynamo-owned `dynamo.nvidia.com/retained` readiness
gate. It initializes absent gates to True and never reopens a gate closed for retirement.
After native drain and retirement are proven, it closes the selected victim's gate,
waits for the kubelet to report that Pod NotReady, and requires all retained Pods to
be assigned, Running and Ready before reducing the child PodClique's `/scale` by one.
The journal pins the victim and retained Pod UIDs before any effect, and acknowledges
release only after the expected UID is absent with every retained UID preserved.

This is a steering workaround, **not atomic UID-authorized deletion**. A concurrent
failure can still change Grove's sorting outcome; any unexpected UID loss reports
recovery-required and is never acknowledged as successful release. Exact authorized
release remains tracked by Grove GREP #823.
Shrink proceeds one suffix allocation at a time and never below the initial size or
the clique's `minAvailable`. It therefore does not require lowering Grove's runtime
threshold. SGLang does not enable this scale-in path.

`grovecapacity/readiness_release.go` owns gate initialization and readiness-based
scale-in. `grovecapacity/release.go` independently checks the accepted victim and
retained UIDs and derives release receipts. A different Grove release mechanism
replaces the steering implementation and its template/runtime wiring, not the
coordinator, native drain protocol, or release-proof checks.


### Serving readiness belongs in the existing worker

The existing worker owns registration. No additional controller permission or
controller acknowledgment is required, including after initial formation and growth.

Registration requires all of:

- Authoritative committed membership and the required topology-bound serving proof.
- Engine serving readiness and valid KV state for the current engine lifetime.
- No shutdown or withdrawal in progress.

Every registration path must use the same native evidence, including startup,
resume, and health/KV recovery. After an asynchronous registration, recheck its
evidence; if it changed, withdraw. Registration is an observation, not a controller target.

Keep target acceptance separate from slow registration and drain effects. Status
reads and target retries must not wait on a lock held for an inference request's
lifetime. Discovery absence alone is not drain completion, and a wrapper-local
request counter cannot prove engine drain once requests bypass that wrapper.

Engine lifetime, worker/sidecar lifetime, discovery identity, and Pod UID are
separate identities. Restarting a sidecar must not relaunch an engine, inherit
serving proof without revalidation, or be mistaken for a new engine lifetime.

### Sidecar dependencies

[DEP #14657](https://github.com/ai-dynamo/dynamo/issues/14657) separates the engine
from Dynamo. The serving lifecycle stack for
[DEP #14897](https://github.com/ai-dynamo/dynamo/issues/14897) provides shared-worker
recovery hooks in [#16752](https://github.com/ai-dynamo/dynamo/pull/16752), native
health and lifetime identity in [#16753](https://github.com/ai-dynamo/dynamo/pull/16753),
and KV recovery in [#16754](https://github.com/ai-dynamo/dynamo/pull/16754) and
[#16755](https://github.com/ai-dynamo/dynamo/pull/16755). Engine Group admission
must compose with that lifecycle rather than replace it.

The Python worker uses the generic unregistered-listener primitive without
depending on sidecar lifecycle code. A sidecar integration must follow native
serving readiness and runtime/KV prerequisites, honor durable withdrawal, and
preserve exact engine/member identity. It requires no second controller grant.
Status controls remain reachable before registration and during recovery.
Expected inference unavailability is not authority to restart the engine.

The stack's health and KV checks do not prove Engine Group membership or
topology-bound serving verification. Native identity binding and real admission
integration remain required before enabling the sidecar deployment path.

The inspected #62082 revision exposes correlated Elastic EP through Python and
HTTP, not the native gRPC control service. The sidecar path still needs supported
remote membership/status controls and engine-authoritative serving and drain
evidence. Do not substitute health for membership, depend on in-process
`AsyncLLM` or middleware globals across containers, or advertise sidecar
conformance before those controls exist.

## Validation and delivery

CPU tests cover native protocol correlation, runtime-owned registration, initial
verification without a synthetic resize, multi-rank append, ambiguous registration,
topology changes during registration, pause/resume, and coordinator reconstruction
at durable boundaries. API-server tests cover Grove Scale, Pod-UID-bound launch
delivery, restart while bootstrap is pending, and timestamped serving-proof
persistence. Launch consumption remains distinct from inference readiness.

The DGD pathway resolves the immutable native declaration,
renders one PCSG with one member clique, seeds the child once, and rolls up
engine-authoritative readiness without resetting its live scale target.

Shared graph assembly and DGD child reconciliation select a backend without
interpreting its engine geometry. The vLLM renderer owns native profile validation,
launch preparation and allocation probes; it reuses the standard Grove Pod
construction for metadata, secrets, resources and placement. Runtime composition,
not the capacity adapter, assembles native membership clients.

`ResolveVLLMGroveCapacity` verifies the complete DGD/PCS/PCSG/clique ownership
chain and derives rendezvous from the exact rank-zero Pod. `ResolveVLLMMembership`
composes direct per-Pod clients with the native membership adapter and a journal
scoped to the physical clique UID. Resolution uses the production Pod-cache
projection and does not contact vLLM: an API-unready joiner must not block launch
delivery or membership preparation. Pod-bound launch lifetime remains separate
from the native API instance ID pinned by membership observation.

`VLLMWorkerRuntimeProvider` composes these pieces when Grove is enabled. Initial
launch delivery runs only after lifecycle finalizers exist, and before engine
observation. The runtime establishes serving proof without a controller request;
the checkpoint records observed proof, not permission. Group-global negative traffic
targets retain accepted withdrawal/drain intent across restarts. Native engines
retain transaction authority.

Only the single-process allocation launcher is enabled for aggregated generation: no
LoRA, RL, pooling, disaggregation, packed allocations, process restart, replacement,
or group retirement is claimed. The planner accepts absolute bounded targets,
grows a fresh contiguous suffix, and shrinks through one-victim retirements. It
rejects restoration into previously released slots. Unsupported plans are rejected
rather than silently weakening their safety contracts.

Mock-adapter scenarios cover two four-member allocations growing to three
(EP8 → EP12), then retiring four members and releasing one Pod (EP12 → EP8).
Release waits if even one hosted member lacks terminal drain evidence. The core
coordinator separately covers EP8 → EP7 → EP4 → EP8 survivor recovery with mocks;
these scenarios do not establish real-engine packed launch or recovery support.

[`poc.yaml`](poc.yaml) is a generic source-build fixture; its development image
tag must be built with vLLM #62082. [`growth-test.yaml`](growth-test.yaml) pins the
worker image and GPU settings used by the functional B200 test below. Its model
PVC must be populated before deployment.

### GPU growth test

The composed draft passes a single-DGD DP2 -> DP3 test on B200 GPUs:

- Initial status converges without a manual wake-up or status patch.
- The real Engine Group Scale subresource grows the existing member clique.
- Operator restart during a persisted native transition resumes the same target.
- Ten nonempty frontend completions succeed before, during and after growth.
- The controller observes a fresh generation-2 serving proof with its actual
  timestamp. Desired, allocated, available and active counts converge to three.
- Original Pod UIDs/native instance IDs, the clique UID and shared PodSpec are
  preserved; all engine containers have zero restarts.

The test uses this operator:

```text
docker.io/jmancuso768/dynamo-enginegroup-operator@sha256:9d33b5eb118004e1d87a687cb3babad12c9e9b5bcf1fed805b149fa640a4ccd5
```

The worker image is pinned in the manifest. It contains matching Dynamo Python
and native wheels plus vLLM #62082 at
`a004a3e801948dc7469700eb02e3bad2215144a2`. These are test-build artifacts, not
a released compatibility promise.

Use an isolated vcluster with Grove, the Engine Group CRD/controller, at least
three available B200 GPUs and the manifest's model PVC. Set an explicit context
and namespace; do not change a shared host operator for this test.

```bash
: "${TEST_CONTEXT:?Set the isolated vcluster context}"
: "${TEST_NAMESPACE:?Set the test namespace}"
kubectl --context "$TEST_CONTEXT" -n "$TEST_NAMESPACE" apply -f growth-test.yaml
kubectl --context "$TEST_CONTEXT" -n "$TEST_NAMESPACE" wait \
  dgd/vllm-enginegroup-followup --for=condition=Ready --timeout=20m
kubectl --context "$TEST_CONTEXT" -n "$TEST_NAMESPACE" get \
  dgdeg/vllm-enginegroup-followup-worker-0 -o yaml
kubectl --context "$TEST_CONTEXT" -n "$TEST_NAMESPACE" scale \
  dgdeg/vllm-enginegroup-followup-worker-0 --replicas=3
kubectl --context "$TEST_CONTEXT" -n "$TEST_NAMESPACE" wait \
  dgdeg/vllm-enginegroup-followup-worker-0 \
  --for=jsonpath='{.status.activeNativeMemberCount}'=3 --timeout=20m
kubectl --context "$TEST_CONTEXT" -n "$TEST_NAMESPACE" wait \
  dgdeg/vllm-enginegroup-followup-worker-0 --for=condition=TargetReached --timeout=20m
```

Send real frontend completions before, during and after the scale request. Save
the initial and final Pod UIDs, container restart counts, native per-rank status,
clique PodSpec and Engine Group verification proof. Check the new member count
and current observed generation, not only a condition that was already True
before scaling. For restart coverage, capture
the persisted membership target while progressing, restart only the isolated
operator, and require the same ID/digest to reach commit.

This is growth-only functional evidence, not an availability benchmark. Shrink,
failure recovery, packed members, durable drain, sidecar deployment and terminal
retirement remain unqualified. Deleting a live test DGD does not exercise a
supported retirement workflow; administrative cleanup must prove old processes
stopped before clearing any safety finalizer.

Generation startup does not import pooling-only native bootstrap helpers by
name. The isolated-interpreter regression in
`components/src/dynamo/vllm/tests/test_embedding_bootstrap_import.py` requires
the native vLLM environment and checks this startup boundary without loading a
model.

This slice changes no API fields or generated schema. Including `engineGroup` in
`UsesPCSG` selects its workload shape only; the existing hub-only creation block
continues to use its unchanged conversion/preservation rules.
