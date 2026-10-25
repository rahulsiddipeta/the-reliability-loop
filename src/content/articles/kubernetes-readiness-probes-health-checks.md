---
title: "Kubernetes Readiness Probes Are Not Health Checks"
description: "Why Kubernetes readiness probes are routing controls rather than comprehensive health checks, with precise mechanics (EndpointSlice conditions, kube-proxy behavior, DNS..."
pubDate: 2026-10-25
author: Rahul Siddipeta
category: "Platform Engineering"
tags: ["Kubernetes", "Platform Engineering", "SRE", "Reliability", "Operations"]
readTime: "11 min read"
featured: false
draft: false
---
![Conceptual engineering control loop for Kubernetes Readiness Probes Are Not Health Checks](/images/articles/kubernetes-readiness-probes-health-checks.svg)

## Readiness is routing, not health

Teams frequently equate Kubernetes readiness probes with application health checks. They are not the same. Readiness exists to make one narrow decision: should this Pod be eligible to receive new Service traffic right now? That is a routing control, not a verdict on whether users are meeting their SLOs.

SRE-grade health is multi-dimensional: it spans dependencies, regions, request paths, authentication, and business logic. By contrast, readiness is a local, binary signal consumed by Kubernetes controllers and data plane components. A Pod turning unready does not stop or restart the process; it only changes endpoint eligibility for Services that select the Pod.

Keeping this distinction crisp avoids two classes of outages: overreach (readiness tied to distant dependencies, yanking capacity during transient blips) and blind spots (readiness satisfied while the service cannot complete useful work). Use readiness to control admission of new work. Measure health with external observation aligned to user journeys.

## How Kubernetes actually implements readiness

Under the hood, kubelet runs probes against containers and updates Pod conditions. Some mechanics that matter operationally:

- Pod readiness is a Pod-level condition. A Pod becomes Ready only when all containers defining readiness are Ready. A single container passing its readiness probe does not, by itself, make the Pod Ready.
- When readiness for a Pod is true, the EndpointSlice controller marks that Pod’s entry as ready in EndpointSlice objects associated with Services that select the Pod. When readiness later fails, the EndpointSlice entry typically remains present but with its ready condition set to false. Endpoints (the legacy resource) represents this as addresses and notReadyAddresses.
- kube-proxy programs iptables or IPVS to steer new Service traffic only to endpoints whose ready condition is true. Backend choice occurs at selection time: for TCP this is at connection establishment; for UDP and other connectionless traffic, selection is per packet or per flow-hash. In all cases, flipping to unready stops new selections but does not retroactively move existing flows.
- DNS behavior differs by Service type. For normal (ClusterIP) Services, DNS resolves to the Service ClusterIP; readiness does not change the record set. kube-proxy then routes only to ready endpoints behind that ClusterIP. For headless Services (clusterIP: None), DNS returns Pod IPs for ready endpoints by default; setting publishNotReadyAddresses=true includes not-ready Pods in Endpoints and in headless Service DNS records.
- Readiness does not restart containers and does not preemptively cut existing connections; it affects only eligibility for new traffic. Liveness is the probe type that triggers restarts when a process is believed wedged. Startup probes postpone both readiness and liveness evaluation during initialization.
- Rollouts consume the Ready signal, not generic “health.” A Deployment counts a Pod as available when it is Ready for at least minReadySeconds (default 0), and surge/unavailable rollout budgets are enforced using that signal.

These mechanics underpin safe deployments and traffic gating. If readiness is too permissive, bad Pods receive traffic; if too strict or slow, you can strand capacity and stall releases. Understanding the precise scope of the ready bit and how it is propagated into EndpointSlices, DNS, and kube-proxy is key to making deliberate tradeoffs.

## Production failure modes to expect

- Over-scoped checks that magnify blips. If your readiness endpoint verifies a primary database each time, a brief failover can flip an entire fleet to unready exactly when you need headroom. Keep the check local and bounded; include only strictly gating dependencies and prefer quick, resilient tests over full end-to-end validations.

- Superficial checks that admit broken instances. A TCP port-open or “process responds to ping” check can report Ready while the instance cannot complete work due to cold caches, empty credential stores, or paused worker pools. This creates latency black holes while the platform believes capacity is healthy.

- Probe flapping under resource pressure. Tight timeouts plus CPU throttling or GC can produce intermittent timeouts. After a few misses, EndpointSlice ready bits flip, kube-proxy updates rules, and load migrates—often cascading pressure to neighbors. Without damping, this oscillation can snowball into a fleet-wide brownout.

- Mismatch between probe type and protocol. TCP probes confirm only socket establishment. HTTP probes are more expressive for HTTP services, but the chosen path must correlate with ability to accept new work. Exec probes run a program directly (no shell by default); assumptions about quoting or globbing can silently fail. Pick the type that reflects useful readiness and behaves predictably during stress.

- Topology blind spots. If local-zone communication is fine but cross-node routing is impaired, a local probe may claim Ready while remote clients fail. Conversely, a readiness path that calls across the WAN can drop local availability for a remote hiccup that does not actually affect same-zone users. Align the check with the true data-path and failure domains.

- Sidecar coupling surprises. In multi-container Pods, any container that declares readiness can gate the Pod. If a non-critical sidecar fails, the application may be fully capable yet withdrawn from endpoints. Make that shared fate an explicit design choice.

- Stateful clusters and peer discovery. Some clients discover peers via headless Service DNS. If you ever enable publishNotReadyAddresses=true (for bootstrap or specialty protocols), be aware you are deliberately including not-ready Pods in discovery. Clients then bear responsibility for avoiding or handling not-ready peers.

## Tuning probes: policy, damping, and stability

Probe fields encode tradeoffs among detection speed, tolerance to blips, and stability:

- periodSeconds and timeoutSeconds define cadence and patience per attempt.
- failureThreshold sets the number of consecutive misses before marking the Pod unready.
- successThreshold sets consecutive successes required before marking the Pod ready again. Only readiness supports successThreshold values greater than 1; liveness and startup require successThreshold=1.

Together, these define the minimum/maximum time to remove and to reintroduce capacity. For example, periodSeconds=5, timeoutSeconds=1, failureThreshold=3 removes a Pod after roughly 10–15 seconds of sustained trouble; a readiness successThreshold of 2 requires two consecutive passes to re-enter, helping to prevent flapping immediately after a transient blip. Be cautious with very small timeouts: under CPU throttling, GC pauses, or transient I/O, a 1-second HTTP timeout is frequently too tight and can cause avoidable oscillation. Slightly larger timeouts (e.g., 2–3 seconds) and/or tuned thresholds often yield a more stable fleet without significantly delaying detection.

Startup probes isolate long initialization from liveness/readiness. Without startup, a slow-to-warm service risks liveness kills or premature readiness. Use a generous startup failure window; once it passes, leaner readiness and liveness policies can enforce steady-state behavior.

The check content matters most. Readiness should assert the instance can accept and complete new work quickly under normal load, not that every optional dependency is perfect. Good inputs include listener bind and responsiveness, worker/queue headroom, rate limiter state, and feature flags enabling degraded but useful operation. If a dependency is optional, avoid flipping readiness solely due to its failure; serve a degraded mode instead.

Workload scale complicates tuning. Unready Pods still consume resources. Autoscalers that average metrics across all Pods can overreact (diluting metrics with unready capacity) or underreact (ignoring unready Pods incorrectly). Align probe policy with scaling inputs to avoid oscillations that amplify load. Where possible, prefer metrics that reflect effective capacity (e.g., throughput of Ready Pods) rather than raw CPU across all Pods.

Finally, do not encode global health in readiness. Cross-region reachability, authentication end-to-end tests, and third-party API checks belong in telemetry and external synthetic monitoring. Binding them to readiness converts distant hiccups into capacity withdrawal.

## An illustrative probe design for a stateful HTTP service

Suppose a service serves most reads from a local cache but writes through to a replicated datastore. During datastore maintenance it should keep serving cached reads and reject or queue writes. Readiness must track whether the instance can safely take new work, not whether every dependency is perfect.

A practical split uses separate admin endpoints: liveness to confirm the process loop responds; readiness to assert listener state, worker/queue headroom, and whether the instance can either shed writes gracefully or has confirmed write-path availability. Startup absorbs long cache warm-ups so steady-state probes can be strict without false positives.

Restrict these admin endpoints (/live, /ready, /startup) to cluster-internal access; avoid exposing them via public ingress. If you publish such paths by mistake, they can leak operational state. Gate them using controls appropriate to your environment (for example, keep them off public Ingress and consider NetworkPolicy and authentication for internal-only access).

The following manifest is illustrative and intentionally not deployable as-is. It uses placeholder image coordinates and does not define a Service. To observe readiness-based endpoint gating, you must create a Service (ClusterIP or headless) that selects Pods with app=placeholder-app. The small timeouts shown are intentionally aggressive for illustration; in most systems you should use slightly larger timeouts and/or adjust thresholds to avoid flapping.

```yaml
# Illustrative example (placeholders; not deployable)
apiVersion: apps/v1
kind: Deployment
metadata:
  name: placeholder-app
spec:
  replicas: 3
  selector:
    matchLabels:
      app: placeholder-app
  template:
    metadata:
      labels:
        app: placeholder-app
    spec:
      containers:
      - name: app
        image: placeholder-registry/placeholder-app:placeholder-tag
        ports:
        - containerPort: 8080
        startupProbe:
          httpGet:
            path: /startup
            port: 8080
          periodSeconds: 5
          failureThreshold: 60
          timeoutSeconds: 1
        readinessProbe:
          httpGet:
            path: /ready
            port: 8080
          periodSeconds: 5
          timeoutSeconds: 1
          failureThreshold: 3
          successThreshold: 2
        livenessProbe:
          httpGet:
            path: /live
            port: 8080
          periodSeconds: 10
          timeoutSeconds: 1
          failureThreshold: 3
        env:
        - name: READINESS_MAX_QUEUE_DEPTH
          value: "1000"
        - name: READINESS_ALLOW_READS_ON_DEGRADED_WRITES
          value: "true"
```

This design keeps the readiness path local and fast, dampens re-entry with a small successThreshold, and provides a long startup window for cache warm-up. If you ever enable discovery of not-ready peers for stateful systems, ensure your clients can identify and avoid not-ready members; otherwise you effectively route to uninitialized instances.

## Verifying behavior safely (without relying on a single probe)

You can validate three core truths using your normal observability and Kubernetes introspection workflows: readiness changes Service endpoint eligibility, readiness does not restart containers, and established connections are not severed solely by a readiness change.

What to observe in practice:

- Pod conditions and probe events show readiness transitions. When a readiness probe fails, the Pod remains Running; no automatic restart occurs from readiness changes alone.
- EndpointSlices maintain entries for selected Pods while toggling conditions.ready. The older Endpoints resource lists addresses and notReadyAddresses. This means unready Pods do not disappear from discovery data structures; they are marked as not ready so data plane components can exclude them from new traffic.
- For normal (ClusterIP) Services, DNS responds with the ClusterIP; kube-proxy routes only to endpoints where conditions.ready=true. For headless Services (clusterIP: None), DNS returns Pod IPs for ready endpoints by default; if publishNotReadyAddresses=true is set on the Service, not-ready Pods also appear in Endpoints and headless DNS records.
- During a rollout, the Deployment advances only when new Pods become Ready for at least minReadySeconds, honoring surge/unavailable budgets. If your readiness gate is overly strict or slow, rollouts will stall or take much longer than expected.
- For TCP traffic, backend selection happens at connection establishment; for UDP or other connectionless protocols, selection is per packet or per flow-hash. Flipping to unready stops new selections; existing TCP connections are not torn down by readiness flips.

If you need graceful maintenance, pair readiness withdrawal with in-application draining and a suitable terminationGracePeriodSeconds so you complete in-flight work cleanly. When a Pod is terminating, Kubernetes withdraws it from Service endpoints before the container receives its final termination signal; that ordering helps stop new traffic while the application drains.

## Integrating probes with rollouts, scaling, and protection

Readiness is most effective when coordinated with other controllers and policies:

- Deployment strategy and disruption. Ensure your rollouts use values that make sense for your probe timing. minReadySeconds provides a post-Ready soak period before a Pod counts as available, protecting you from immediate re-entry after a transient blip. During voluntary disruptions (e.g., node drain), Kubernetes removes Pods from Service endpoints before termination to stop new traffic; your application should still drain gracefully to avoid user-visible errors.

- Autoscaling and metrics integrity. Autoscalers act on time-averaged metrics. Unready Pods may consume CPU without providing throughput. Choose metrics that represent effective capacity (e.g., successful requests per second from Ready Pods) and avoid averages that blend Ready and unready work indiscriminately. Consider smoothing windows that are compatible with your probe cadence to avoid feedback loops.

- Backpressure and overload protection. If readiness flips to unready under deep queues, remaining Pods see a load spike. Coordinate fleet-wide rate limits, adaptive concurrency, and admission control so that load is shed coherently rather than oscillating from Pod to Pod.

- Stateful membership and discovery. For headless Services, readiness directly shapes which Pod IPs appear in DNS. Some stateful systems expect a stable membership list and perform their own health checks. If you change readiness semantics or toggle publishNotReadyAddresses, confirm the client’s behavior (e.g., quorum calculations, replica lag handling) remains correct under those discovery changes.

- Security of admin endpoints. Liveness/readiness URLs often reveal operational details. Keep them internal-only and avoid binding them to public Ingress. If your platform supports it, add authentication or network policy around these paths. Treat the responses as operational signals, not public health pages.

- ReadinessGates and external conditions. Kubernetes supports Pod readiness gates backed by custom conditions. This can be useful when readiness depends on external controllers (for example, sidecar readiness that is not visible via an in-Pod probe). If you adopt readiness gates, document ownership and failure modes carefully so one controller cannot accidentally withdraw large portions of capacity.

## Conclusion: use readiness as a routing valve, not a health oracle

Kubernetes readiness is a precise lever for steering new traffic; it is not a comprehensive measure of service health. Under normal operation, a readiness change flips a ready bit in EndpointSlices and updates kube-proxy routing, without restarting processes or cutting existing connections. That makes readiness essential for safe rollouts and graceful disruption—provided the probe measures the ability to accept new work, is bounded and local, and is tuned to avoid flapping.

Build real health from the outside-in using synthetic checks and golden signals tied to error budgets. Align readiness with rollout, autoscaling, draining, and protective controls so the system moves coherently under stress. Be explicit about DNS and data plane behavior: ClusterIP DNS does not vary by readiness; kube-proxy selection does. Headless Service DNS reflects Pod readiness unless you deliberately publish not-ready addresses. When you separate routing from health—and design both deliberately—you reduce risk during change and failure while keeping capacity where it helps users most.
