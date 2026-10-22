<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# SGLang Engine Group growth

This integration creates one Engine Group from a DGD, renders a Grove PCSG with
one member PodClique, and grows it through the child's Scale subresource.
Grove owns Pod creation and placement. A shared bootstrap template derives the
native allocation role from the stable Grove slot.

## Contract and limits

The enabled profile has one native member per one-GPU Pod and one world per
opted-in component. The planner serializes width-one joins to reach an absolute
target. `LegacyGrowthAdapter` isolates the released SGLang API while the correlated
lifecycle surface in [SGLang #39834](https://github.com/sgl-project/sglang/pull/39834)
is under review.

A Dynamo-side journal retains intent but cannot manufacture engine idempotency or
prove an ambiguous native operation after restart. `TrafficProjection` reports
logical engine membership, not independent discovery registration or drain.
The verifier checks real inference. Serving eligibility belongs to the engine and
serving runtime, without a controller grant.

Shrink, survivor recovery, packed/multi-Pod execution, container restart and
terminal retirement are not enabled.

## Pinned B200 growth test

[`growth-test.yaml`](growth-test.yaml) records a functional EP2 -> EP3 test with:

- one DGD creating the Engine Group and complete Grove hierarchy;
- autonomous initial EP2/status convergence, without a manual wake-up;
- real Scale-driven growth and a fresh timestamped generation-2 serving proof;
- sixteen nonempty frontend completions before, during and after growth;
- preserved initial Pod UIDs/process identities and shared PodSpec, with zero
  container restarts; and
- a frontend-only parent update that preserves the live child/clique size of three.

The operator image is:

```text
docker.io/jmancuso768/dynamo-enginegroup-operator@sha256:13d110d1d30937955c183f911f7ec2ade3779438f3b16d848aeb0abbd63c2494
```

The worker image is pinned in the manifest and packages SGLang 0.5.18,
Torch 2.13.0+cu130, NIXL 1.4.0 and Mooncake 0.3.13.post1 with matching Dynamo
Python/native wheels. It is a test artifact, not a released compatibility promise.

### Image-only accommodations

The B200 test image isolates three compatibility repairs from production code:

1. Mooncake's device library is built for SM100 from release commit
   `719735896c86b56fabec6cf3e825fb2ea640597a`.
2. With `DYNAMO_POC_LOCAL_NIC_AUTO=1`, a test-only startup hook selects the single
   visible GPU's closest PIX NIC and sets
   `UCX_NET_DEVICES=<nic>:1,cuda0-<nic>:1`. It rejects conflicting settings.
3. The NIXL dispatcher converts expert indices using the installed NIXL
   `topk_idx_t`, rather than assuming int64.

The manifest uses unquantized BF16, explicit DeepGEMM and both BF16 dispatch
switches. Inference traffic is required while this released join protocol
progresses. Neither these settings nor that traffic requirement are production
performance recommendations.

### Run and check

Use an isolated vcluster with Grove, the Engine Group CRD/controller, at least
three available B200 GPUs, shared RDMA and the manifest's populated RWX model PVC.
The cached model snapshot path and resource names are test inputs; verify them
against the target cluster before applying the manifest.

```bash
: "${TEST_CONTEXT:?Set the isolated vcluster context}"
: "${TEST_NAMESPACE:?Set the test namespace}"
kubectl --context "$TEST_CONTEXT" -n "$TEST_NAMESPACE" apply -f growth-test.yaml
kubectl --context "$TEST_CONTEXT" -n "$TEST_NAMESPACE" wait \
  dgd/sglang-enginegroup-retest --for=condition=Ready --timeout=20m
kubectl --context "$TEST_CONTEXT" -n "$TEST_NAMESPACE" get \
  dgdeg/sglang-enginegroup-retest-worker-0 -o yaml
kubectl --context "$TEST_CONTEXT" -n "$TEST_NAMESPACE" scale \
  dgdeg/sglang-enginegroup-retest-worker-0 --replicas=3
```

Keep sending real frontend completions while the joining allocation loads and
joins. The control/status poll alone does not progress this native protocol.
Then require:

```bash
kubectl --context "$TEST_CONTEXT" -n "$TEST_NAMESPACE" wait \
  dgdeg/sglang-enginegroup-retest-worker-0 \
  --for=jsonpath='{.status.activeNativeMemberCount}'=3 --timeout=20m
kubectl --context "$TEST_CONTEXT" -n "$TEST_NAMESPACE" wait \
  dgdeg/sglang-enginegroup-retest-worker-0 --for=condition=TargetReached --timeout=20m
kubectl --context "$TEST_CONTEXT" -n "$TEST_NAMESPACE" get \
  dgdeg/sglang-enginegroup-retest-worker-0 -o yaml
```

Capture before/after Pod UIDs, container IDs/restarts, native status, clique UID
and PodSpec, and the timestamped serving proof. Require desired, allocated,
available and active counts of three with current healthy conditions.
Check the current observed generation rather than accepting a condition that
was already True before scaling.
A frontend-only DGD replica update must not reset child capacity.

The test does not prove idle growth, restart-safe native replay, shrink, survivor
recovery, packed execution or production retirement. Cleanup is administrative:
prove old engine processes stopped before clearing safety finalizers. Deleting
a DGD with a live world is not a supported Retire test.
