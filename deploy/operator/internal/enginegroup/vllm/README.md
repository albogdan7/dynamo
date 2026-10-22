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
