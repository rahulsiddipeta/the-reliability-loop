---
title: "When Kubernetes HPA Makes Incidents Worse"
description: "HPA is meant to heal Kubernetes workloads under load. Yet during incidents it can amplify failure. Learn the mechanisms, tradeoffs, and safer patterns."
pubDate: 2026-11-20
author: Rahul Siddipeta
category: "Platform Engineering"
tags: ["Kubernetes", "HPA", "Platform Engineering", "Autoscaling", "SRE", "Incident Response"]
readTime: "11 min read"
featured: false
draft: false
---
![When Kubernetes HPA Makes Incidents Worse: HPA's loop can amplify incidents; choose sane signals, delays, and guardrails.](/images/articles/kubernetes-hpa-incidents.svg)

## The comfort of autoscaling—and its hidden loop
Autoscaling promises a simple story: demand rises, replicas rise; demand falls, replicas fall. The Horizontal Pod Autoscaler (HPA) operationalizes this with resource-based targets and a control loop that nudges your workload toward a desired state. That promise often delivers real resilience, but in a live incident the same feedback loop can intensify instability.

When capacity is tight or signals are noisy, the HPA can amplify oscillations, trigger cascading bottlenecks, or mask the true constraint. Platform engineers then confront an ironic problem: the mechanism intended to reduce toil and cost becomes a driver of alert storms and prolonged outages. Understanding where, why, and how that happens is essential before deciding whether and how to rely on HPA in the critical path.

What follows is not a rejection of HPA. It is a field-tested set of cautions for platform engineers who must make autoscaling safe when things are already going wrong. The theme is intentionality: make explicit choices about signals, timing, and limits that reflect your service’s real behavior.

## How HPA turns incidents into amplifiers
A common failure mode is scale-chasing latency. Requests slow down, CPU usage per pod rises, and the HPA adds replicas. New pods start cold and often perform expensive warmup work—cache fills, JIT, connection pooling, and adaptive compilers—so they initially run more slowly than warm pods. During that warmup window, overall latency can degrade further, backlogs deepen, and the HPA reads even higher utilization, resulting in another step up that prolongs the warmup storm. The service looks like it is “fighting back,” but it is really amplifying a delay.

Request/limit misalignment is another trap—especially under CPU-bound load. Low CPU requests inflate the utilization metric the HPA reads because utilization is measured against the request, not the node. That by itself does not cause Linux CFS throttling; throttling occurs when CPU limits (quota) are set too low relative to actual need. The harmful pattern in incidents is a combination: requests set too low make the HPA eager to scale up, while limits set too low cause pods to be throttled. The result is a horizontal expansion of throttled work that pressures the cluster without increasing effective throughput.

Memory-based scaling can backfire during leaks or footprint spikes. Approaching memory limits ends abruptly in termination, so scaling on memory utilization is inherently spiky. During incidents, if pods OOMKill at the same time (for example after a synchronized GC cycle), the deployment controller replaces them just as the HPA decides to scale up. That multiplies churn, increases page cache pressure across nodes, and disrupts colocated workloads, widening the blast radius from one service to many.

External contention interacts poorly with HPA, particularly via the cluster autoscaler. Extra pods cannot schedule immediately if the cluster is full, so they sit Pending while nodes are provisioned. During that delay, the HPA may continue to scale up on stale metrics, producing many unschedulable pods. When nodes finally arrive, they all start together and create a synchronized cold-start wave—exactly when the system is already stressed.

Queue-backed services show a distinct hazard: scaling to queue depth without consumption-rate awareness. If the HPA watches only backlog size (via external metrics) and not effective throughput per pod, a sudden influx can trigger a steep scale-up that overwhelms downstream dependencies. The queue drains, but databases, caches, or rate-limited APIs encounter a thundering herd, turning a controlled delay into cross-service timeouts and backoffs.

Timing magnifies all of the above. The HPA loop has delays: metric collection time, reconciliation interval, and pod startup and readiness time. If the total loop delay approaches the characteristic time of your traffic fluctuations, the control action arrives late and overshoots. Oscillation follows: replicas spike, cold starts worsen tail latency, the metric pushes further up, and the loop repeats. Scale-down oscillation is equally harmful: removing warm capacity too soon invites a second ramp if traffic bounces, producing a “too many cold pods” pattern that prolongs incidents even when average load is moderate.

## What your metrics really mean (and don’t)
HPA’s default resource signal for CPU is average utilization: measured CPU usage divided by CPU request for the pods in the target. If you set a low request to pack pods densely, the same absolute work will appear as higher utilization. The autoscaler is not wrong; it is following your declared target. But if the request does not reflect steady-state needs, the feedback loop chases a distortion. Aligning requests to observed baselines reduces noise and makes utilization targets meaningful. Separately, consider CPU limits carefully: low limits cause CFS throttling under load; avoid overly tight limits unless you intend to bound burstiness and accept the performance impact.

Memory tells a different story. Unlike CPU, memory has no throttle—when you hit the limit, the pod can be terminated. Scaling on memory utilization therefore tends to be lumpy and lagging. It can be effective for memory-bound workloads with predictable footprint per unit of work, but for applications with variable heap behavior, native caches, or large background tasks, the signal may not correlate tightly with throughput or latency. During incidents, prefer metrics that reflect the bottleneck you intend to relieve.

Custom and external metrics broaden the palette but bring semantics you must reason about explicitly. For example:
- Throughput-like signals (requests per second per pod, messages consumed per second) can map neatly to a utilization objective if you know a stable per-pod capacity. If per-pod capacity varies widely due to heterogeneous nodes or internal contention, averaging across all pods can make control sluggish or erratic.
- Backlog-like signals (queue depth) require a target that encodes acceptable latency. Scaling purely on absolute backlog can drive aggressive scale-ups that overload shared dependencies. Using backlog per replica relative to a documented target consumption rate better captures intent.

If your external or custom metrics come from Prometheus, a few clarifications help avoid confusion during incidents:
- Target selection vs label operations: drop/keep in the scrape configuration control which targets are scraped at all. labeldrop/labelkeep remove labels from scraped series, affecting series cardinality but not which targets are scraped.
- Cardinality vs ingestion rate: cardinality is the number of distinct time series; ingestion rate is samples per second. Both impact cost and reliability, but they are different levers.
- Recording rules: they create derived series; they do not delete or suppress the original source series.
- Metrics sampling vs trace sampling: scrape intervals and emit rates affect metric fidelity; they are unrelated to how tracing systems sample spans. Do not assume a tracing sampling change will fix metric sparsity.

These distinctions matter because sparse, high-cardinality, or mislabeled metrics can make HPA decisions laggy or misleading. If you are scaling on a Prometheus-based external metric, ensure the series is timely, bounded in cardinality, and stable in naming so that sudden incident-time relabels or churn do not disrupt autoscaling inputs.

## Guardrails in configuration and platform
Guardrails turn an eager feedback loop into a predictable one. The most important are minimum replicas, downscale stabilization, conservative downscale steps, and bounded upscale aggressiveness. Minimum replicas encode warm capacity; downscale stabilization prevents rapid removal of that warm capacity; conservative downscale steps avoid big swings when it finally becomes permissible to reduce replicas.

The following example captures these ideas for a CPU-based HPA. It is illustrative and focuses on behavior knobs. For compatibility: autoscaling/v2 requires Kubernetes 1.23 or later; on older clusters you may need autoscaling/v2beta2.

```yaml
# Illustrative: HorizontalPodAutoscaler with conservative downscale behavior
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: <workload-hpa>
  namespace: <namespace>
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: <workload-name>
  minReplicas: 4
  maxReplicas: 20
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 60
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 0
      selectPolicy: Max
      policies:
      - type: Percent
        value: 100
        periodSeconds: 60
      - type: Pods
        value: 10
        periodSeconds: 60
    scaleDown:
      stabilizationWindowSeconds: 300
      selectPolicy: Min
      policies:
      - type: Percent
        value: 20
        periodSeconds: 60
      - type: Pods
        value: 1
        periodSeconds: 60
```

Why these choices:
- minReplicas keeps some warm capacity online to absorb bursts without triggering cold starts.
- A 300s downscale stabilization window gives caches, JITs, and GC time to settle before removing warm pods.
- selectPolicy: Min with small step policies makes the first allowed downscale gentle, lowering the risk of flapping if traffic bounces.
- Aggressive but bounded scale-up helps avoid under-correction while protecting against explosive growth.

None of this fixes warmup cost by itself. Use readiness probes that reflect true service readiness—only route traffic when caches are primed and critical downstream connections are established—so new replicas join safely. Combine these HPA settings with pod disruption budgets and rollout strategies (surge and maxUnavailable) that pace changes rather than synchronize them.

## Verification and incident runbook when HPA is in the blast radius
Verification before changes and a clear runbook during incidents prevent well-intentioned tweaks from making things worse.

Before enabling or changing an HPA:
- Validate signal quality. Confirm the metrics provider returns timely, non-sparse data under moderate load. If you use external metrics, ensure naming and cardinality are stable.
- Check request/limit alignment. Requests should reflect steady-state needs so utilization traces meaningful headroom. Limits should not be so low that routine peaks cause CPU throttling; for memory, understand the headroom required to avoid OOM under bursty allocations.
- Measure startup and readiness. Time image pulls, init containers, application warmup, and the gap between readiness and actual good service performance. This determines stabilization windows and informs minReplicas.
- Observe per-pod capacity variation. If capacity varies widely across replicas, averaging hides extremes. Consider more conservative targets or alternative metrics that capture the real constraint.

During an incident, when HPA is changing replicas, stabilize the loop before changing application code or deep dependencies:
- Freeze downscaling. Temporarily set minReplicas to the current replica count or increase the downscale stabilization window. This prevents the removal of warm capacity while you investigate.
- Cap scale-up to protect shared backends. If a downstream is the limiting factor, temporarily lower maxReplicas so the autoscaler does not intensify pressure while you apply rate limits, enable degraded modes, or shed load.
- Prefer temporary HPA target adjustments over disruptive pod spec changes. If the signal is misleading, you may be tempted to alter requests immediately. Be cautious: changing requests can trigger pod restarts or rescheduling, adding churn during an outage. If you have ample capacity headroom and accept the rollout risk, proceed deliberately; otherwise, adjust HPA targets or minReplicas for temporary relief and schedule request alignment once the system is stable.
- Inspect HPA status to understand what the controller is doing and why before patching values. You can see current metrics, conditions, and last scale time; stabilization or scale limiting is indicated via conditions and reasons (for example, ScalingLimited and AbleToScale), not a standalone field.

```bash
# Illustrative: inspect autoscaler status to understand decisions and delays
# Replace <hpa-name> and <namespace> with real values.
kubectl describe hpa <hpa-name> -n <namespace>
```

- Coordinate with the cluster autoscaler view. If many pods are Pending, check node provisioning and scheduling constraints; unschedulable pods that later start together can cause a synchronized cold-start wave.
- Record your changes and observations. A brief note on the metric choice, stabilization settings, dependency constraints, and observed effects builds organizational memory for the next event.

## Safer patterns and circuit breakers for resilience
HPA is one control loop among many. Systems are calmer when other loops apply the right counter-pressure.

- Admission control and backpressure at the edge. Do not convert every arrival burst into internal concurrency. Per-client quotas, bounded queues, and overload protection turn the worst spikes into predictable latency instead of cascading failures. With these in place, HPA adds capacity into a controlled system rather than amplifying chaos.
- Real readiness signals. If a pod requires cache warmup or downstream priming, gate readiness on that state and ensure traffic is not routed early. This increases apparent startup time but reduces incident length by avoiding hot traffic on half-initialized pods.
- Jitter and pacing for startup. Small amounts of jitter in startup delays and cautious rollout surge settings prevent synchronized thundering herds when many pods start after node scale-up or restarts.
- Clear per-service caps and override procedures. Document maxReplicas and why it exists (e.g., protecting a database). During incidents, responders should have a known path to temporarily override caps when the protected dependency has been scaled or temporarily replaced.
- Know your concurrency model. If a pod has a hard concurrency ceiling (language runtime constraints, blocking I/O, or a cooperative worker model), horizontal scaling without parallel workers inside each pod multiplies contention rather than increasing throughput. Align worker counts and connection pools with expected replica counts.

These patterns work with HPA behavior settings to keep the system inside a stable operating region during load steps. They will not eliminate incidents, but they change the shape and duration of the ones you do have.

## Closing the reliability loop
HPA is not the villain of incident stories; it reflects and magnifies the assumptions you codify in requests, limits, metrics, and timing. In quiet weeks, those assumptions save money and operator toil; under stress, they either preserve headroom or turn frictions into feedback loops. Platform engineering’s task is to make those assumptions explicit, verify them in the face of timing and dependency realities, and encode guardrails that reflect the organization’s true priorities.

When the pager rings, the fastest path back to steady state is usually not to delete the autoscaler, but to steady its hand. Freeze downscaling if pods are warming, cap scaling if dependencies buckle, and correct the signal thoughtfully—prefer temporary autoscaler adjustments during the event and align requests and limits in a controlled follow-up. Done deliberately, HPA remains a powerful tool that shortens incidents rather than prolonging them.
