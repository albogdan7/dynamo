/*
 * SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

package enginegroup

import (
	"context"
	"fmt"
	"slices"
	"strconv"
	"strings"

	domain "github.com/ai-dynamo/dynamo/deploy/operator/internal/enginegroup"
	"github.com/ai-dynamo/dynamo/deploy/operator/internal/enginegroup/grovecapacity"
	corev1 "k8s.io/api/core/v1"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

// SGLangGroveCapacity binds the growth-only profile's process identity to Grove capacity.
// The legacy engine API has no process lifetime ID, so this profile prohibits container restarts.
type SGLangGroveCapacity struct {
	Grove *grovecapacity.Adapter
}

// Observe adds the profile's one native member to each physical allocation.
func (a *SGLangGroveCapacity) Observe(ctx context.Context, groupID domain.GroupID) (domain.CapacityObservation, error) {
	// Grove owns Pod identity and placement; the engine integration owns process lifetime and rank assignment.
	physical, err := a.Grove.Observe(ctx, groupID)
	if err != nil {
		return domain.CapacityObservation{}, err
	}
	observation := domain.CapacityObservation{AppliedRevision: physical.AppliedRevision, ReleaseFences: physical.ReleaseFences}
	for _, allocation := range physical.Allocations {
		pod := &corev1.Pod{}
		if err := a.Grove.Client.Get(ctx, client.ObjectKey{Namespace: a.Grove.Clique.Namespace, Name: allocation.Pod.Name}, pod); err != nil {
			return domain.CapacityObservation{}, err
		}
		if domain.PodUID(pod.UID) != allocation.Pod.UID {
			return domain.CapacityObservation{}, fmt.Errorf("%w: SGLang Pod changed during process observation", domain.ErrRecoveryRequired)
		}
		for _, container := range pod.Status.ContainerStatuses {
			if container.RestartCount > 0 {
				return domain.CapacityObservation{}, fmt.Errorf("%w: SGLang Pod %s restarted outside membership recovery", domain.ErrRecoveryRequired, pod.Name)
			}
		}
		member, err := sglangSlotMember(allocation.SlotID)
		if err != nil {
			return domain.CapacityObservation{}, err
		}
		observation.Allocations = append(observation.Allocations, domain.CapacityAllocation{
			Incarnation: domain.ReplicaIncarnation{
				ReplicaID: allocation.ReplicaID, SlotID: allocation.SlotID, CapacityRefs: []domain.CapacityRef{allocation.Pod},
				Members: []domain.NativeMemberIncarnation{{ID: member, RuntimeIncarnation: domain.RuntimeIncarnationID(pod.UID)}},
			},
			Available: allocation.Ready, Health: allocation.Health,
		})
	}
	return observation, nil
}

// Apply validates native bootstrap and frozen processes before any physical capacity effect.
func (a *SGLangGroveCapacity) Apply(ctx context.Context, groupID domain.GroupID, target domain.CapacityTarget) (domain.ApplyResult, error) {
	// The supported profile appends one native member per allocation; recovery and packed launches are separate capabilities.
	for _, replica := range target.Replicas {
		if replica.Bootstrap == nil {
			continue
		}
		member, err := sglangSlotMember(replica.SlotID)
		bootstrap := replica.Bootstrap
		if err != nil || replica.SlotID == "slot-0" || bootstrap.Mode != domain.BootstrapModeJoin ||
			bootstrap.BaseTopologyGeneration <= 0 || len(bootstrap.NativeMembers) != 1 || bootstrap.NativeMembers[0] != member {
			return domain.ApplyResult{Rejection: &domain.Failure{
				Classification: domain.FailureClassificationTerminal, Reason: "UnsupportedBootstrap",
				Message: "SGLang growth requires one dp-N joining member in allocation slot N",
			}}, nil
		}
	}

	// A matching Pod UID cannot hide a different or restarted native process.
	observed, err := a.Observe(ctx, groupID)
	if err != nil {
		return domain.ApplyResult{}, err
	}
	for _, replica := range target.Replicas {
		if replica.Incarnation == nil {
			continue
		}
		if !slices.ContainsFunc(observed.Allocations, func(allocation domain.CapacityAllocation) bool {
			return domain.SameIncarnation(allocation.Incarnation, *replica.Incarnation)
		}) {
			return domain.ApplyResult{}, fmt.Errorf("%w: SGLang capacity target lost an exact process binding", domain.ErrRecoveryRequired)
		}
	}
	return a.Grove.Apply(ctx, groupID, target)
}

func sglangSlotMember(slot domain.CapacitySlotID) (domain.NativeMemberID, error) {
	// Stable Grove slots map to the compatibility launcher's one-member rank assignment.
	literal := strings.TrimPrefix(string(slot), "slot-")
	index, err := strconv.ParseInt(literal, 10, 32)
	if err != nil || index < 0 || string(slot) != "slot-"+strconv.FormatInt(index, 10) {
		return "", fmt.Errorf("invalid SGLang allocation slot %q", slot)
	}
	return domain.NativeMemberID("dp-" + literal), nil
}
