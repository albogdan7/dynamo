/*
 * SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

package dynamo

import (
	api "github.com/ai-dynamo/dynamo/deploy/operator/api/v1beta1"
	"github.com/ai-dynamo/dynamo/deploy/operator/internal/consts"
	"github.com/ai-dynamo/dynamo/deploy/operator/internal/controller_common"
	grove "github.com/ai-dynamo/grove/operator/api/core/v1alpha1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/utils/ptr"
)

func (r groveEngineGroupRenderer) renderSGLang(
	dgd *api.DynamoGraphDeployment,
	component *api.DynamoComponentDeploymentSharedSpec,
	queueName string,
	topologyDomains []api.TopologyDomain,
	restart *RestartState,
	restartAnnotations map[string]string,
) (*grove.PodCliqueTemplateSpec, grove.PodCliqueScalingGroupConfig, error) {
	// Resolve SGLang's immutable template before injecting common Pod defaults.
	profile, err := ResolveSGLangComponentEngineGroup(component)
	if err != nil {
		return nil, grove.PodCliqueScalingGroupConfig{}, err
	}
	component = component.DeepCopy()
	propagateDGDAnnotations(dgd.Annotations, component)
	podTemplate := ensurePodTemplate(component)
	podTemplate.Labels[consts.KubeLabelDynamoNamespace] = GetDynamoNamespace(dgd, component)
	discovery := controller_common.GetDiscoveryBackend(r.operatorConfig.Discovery.Backend, dgd.Annotations)
	if discovery != "" {
		podTemplate.Annotations[consts.KubeAnnotationDynamoDiscoveryBackend] = string(discovery)
	}

	// Keep backend launch/probe conventions separate from shared Grove assembly.
	role := ServiceRole{Name: component.ComponentName, Role: RoleMain, Replicas: profile.InitialReplicas}
	clique, err := buildCliqueForRole(cliqueParams{
		r: role, component: component, backendFramework: BackendFrameworkSGLang,
		secretsRetriever: r.secretsRetriever, dynamoDeployment: dgd, numberOfNodes: 1,
		operatorConfig: r.operatorConfig, runtimeConfig: r.runtimeConfig, componentName: component.ComponentName,
		usesPCSG: true, discoveryBackend: discovery,
		discoveryContext: NewDiscoveryContext(r.operatorConfig.Discovery.Backend, dgd.Annotations),
		restartState:     restart, existingRestartAnnotations: restartAnnotations,
		validatedQueueName: queueName, groveClusterTopologyDomains: topologyDomains,
		containerGPUs: func() (int64, error) { return profile.Geometry.GPUsPerReplica, nil },
	})
	if err != nil {
		return nil, grove.PodCliqueScalingGroupConfig{}, err
	}
	clique.Spec.PodSpec.RestartPolicy = corev1.RestartPolicyNever
	clique.Spec.MinAvailable = ptr.To(profile.InitialReplicas)
	clique.Labels[consts.KubeLabelDynamoEngineGroup] = EngineGroupNameForComponent(dgd.Name, component.ComponentName, 0)
	clique.Labels[consts.KubeLabelDynamoScaleRepresentative] = consts.KubeLabelDynamoScaleRepresentativeYes

	// PCSG replicas count worlds, independently of this clique's allocation count.
	world := buildGroveScalingGroupConfig(component.ComponentName, component, dgd.Spec.TopologyConstraint,
		[]ServiceRole{role}, []string{clique.Name}, nil, false)
	return clique, world, nil
}
