---
title: "Observability Costs Are an Architecture Problem"
description: "Observability bills are driven by your architecture, not dashboards. Reshape volume, cardinality, and retention to cut spend without eroding the signals you need."
pubDate: 2026-11-20
author: Rahul Siddipeta
category: "Observability"
tags: ["observability", "architecture", "cost-optimization", "sre", "telemetry"]
readTime: "10 min read"
featured: false
draft: false
---
![Observability Costs Are an Architecture Problem: Your diagram sets the telemetry bill; fix topology, volume, cardinality, and retention.](/images/articles/observability-costs-architecture.svg)

## The bill comes from the diagram, not the vendor

Teams often treat observability cost as a negotiation problem. They comparison-shop platforms, tweak retention toggles, and haggle over per-gigabyte rates, only to discover that spend drifts back up months later. The uncomfortable truth is that telemetry cost is an architectural consequence: the way services fan out, the shape of identifiers, and where you place the data plane for ingest all precommit you to a bill, regardless of your chosen supplier. If your diagrams imply high fan-out, frequent retries, or unbounded identity spaces, your observability bill will hew to that structure.

Every hop you add creates a place where more events can be emitted and more labels can be attached. Scale a chatty control plane, or enable per-tenant dimensions on a heavily used route, and you will multiply series count and event size before anyone opens a query editor. Conversely, the biggest cost wins I have seen start with removing amplification in the call graph, eliminating needless edges, and bounding high-cardinality identifiers at their source.

If you are paying more than you expect, stare at the topology first. Ask where you turn one request into many, how many things you measure for each user action, and whether you ever allow an unbounded attribute to ride on hot-path telemetry. Most budget leaks are diagram leaks.

## The three levers: volume, cardinality, retention

Observability spend can be reasoned about through three first-order levers: event volume, label cardinality, and data retention. Volume is the total number of logs, metrics points, and spans you produce per unit time; cardinality is the count of distinct label combinations the backend must track; retention is the time horizon over which you keep the data in write-optimized and query-optimized tiers. Secondary effects—like query concurrency and cold storage rehydration—are downstream of these three.

Volume is the easiest to picture and the hardest to cap after the fact. If each request emits ten log lines, five metrics points with three labels, and a trace with twelve spans, your default per-request footprint is baked in. Multiplied across peak RPS and retry storms, the bill scales even faster. The cleanest control for volume is to relocate emission earlier or later in the path and to compress multiple events into a summarized one where you can prove the information loss is tolerable for operations.

Cardinality is a different beast. Two services that emit the same number of metrics points can have radically different costs if one attaches per-user, per-session, or per-resource identifiers as labels. Time series backends shard by label sets; when you attach an exploding dimension, you pay in indexing, memory, and cache churn, and you invite hot partitions that degrade query performance. The surest fix is not a downstream filter but an upstream rule: identifiers that grow without bound must not become labels on hot series.

Retention is your temporal lever. Shorter retention in the hot tier means smaller working sets, faster queries, and lower spend, provided you still meet your operational needs. Many teams keep traces for far longer than they ever meaningfully query because the system made it easy, not because the incident process requires it. A split-tier approach—short, strict retention for high-resolution data, with coarser rollups or sampled exemplars in a cheaper tier—preserves signal while containing cost.

## Failure modes of “collect everything”

The most visible failure of “collect everything” is budget overrun, but subtler and riskier failure modes show up in production. The first is backpressure and head-of-line blocking inside agents and collectors. When exporters block on network IO or remote limits, they can stall application threads, fill process queues, and spike latency in routes that are already under stress.

Another failure mode is cache and memory blowups due to unbounded cardinality. Introduce a label like user_email or request_hash into a hot metric, and a time series backend can see millions of short-lived series. The result is allocator churn, degraded compression ratios, elevated GC time, and slow queries with unpredictable p95s. You might not notice until a regional failover doubles traffic and the telemetry plane competes with the application for CPU.

Finally, there is the quiet failure: you keep everything but cannot retrieve what you need when you need it. Overly wide logs with inconsistent keys, traces with missing parent-child links on asynchronous edges, and counter metrics without a stable identity all lead to expensive data with weak operational value. You pay twice—once to ingest, again to reverse-engineer the story in the heat of an incident.

## Architectural patterns that shape telemetry

If you design the telemetry plane as a first-class system, you can choose patterns that keep cost bounded and signal strong. The first pattern is to separate the operational control plane from the hot data plane. Emit event summaries at the edge of request lifecycles, carry a request ID through all hops, and attach a small, consistent set of attributes that express the who and the what of the action. Avoid per-instance or per-pod labels unless you are debugging a deployment; prefer stable, semantically meaningful dimensions like feature flag cohort or API route template.

A second pattern is to move sampling decisions as close to the source as you can while keeping them aware of outcomes. Head sampling reduces cost but risks discarding the very traces you need. Tail-aware sampling—retain all error traces and a small, rotating slice of success traces—aligns cost with value. Applied at the per-service collector, not inside application threads, it prevents exporter stalls from touching the hot path while still reacting to observed outcomes.

A third pattern is event normalization at boundaries. Instead of sprinkling ad-hoc log formats across codebases, define a small envelope for audit-worthy events and operational spans. Record size, duration, outcome, and a bounded set of actors. If a downstream system needs a richer view, enrich at the collector where you can enforce quotas and strip prohibited attributes without recompiling applications.

A fourth pattern is to derive metrics from events and traces where possible rather than duplicating emission. SLI-grade counters and histograms are absolutely essential, but you do not need a bespoke metric for every new feature if a well-structured event already contains the signal. A small pipeline that aggregates a generic request_completed event into per-route latency and error rate can eliminate countless one-off custom metrics and the cardinality they invite.

## Budgets, SLAs, and guardrails for observability

To make observability costs operable, you need explicit budgets per service and SLAs for the telemetry pipeline itself. A budget is not a vendor limit; it is a contract with engineering about how much telemetry a service is allowed to emit at peak. Pair a simple envelope—events per request, average bytes per event, series count per metric family—with a playbook for exceeding it: which attributes to drop first, which spans to merge, and which routes to sample more aggressively under load.

The pipeline needs SLAs too. Define an ingestion success ratio, an end-to-end ingest-to-query freshness target, and exporter queue latency thresholds that must hold during a regional failover. If your telemetry plane cannot keep up exactly when you need it most, it is not reliable. Budget capacity for that failover explicitly, and prove that backpressure stays inside collectors and never bleeds into application threads.

Guardrails turn budgets into reality. Enforce a small allowlist of label keys for metrics, and reject or rewrite events that introduce new keys without approval. Cap event size at the edge and log the truncation so you can discover offending producers. Rate-limit low-value debug logs per route and tenant, and provide a well-documented override for short-lived incident debugging that self-expires.

## Verification: proving your pipeline works

Verification is not a dashboard glance; it is an adversarial test of the telemetry plane under the conditions that matter. Start by measuring the steady-state: per-service emission rate, average and p95 event size, series churn rate, and end-to-end freshness from edge to query. These establish the baseline you will defend when load and failure enter the picture.

Next, test failure behavior deliberately. Induce a network slowdown between collectors and the backend and confirm that queues grow where expected, backpressure does not propagate into application code, and the system applies the configured shedding policy. Kill a collector process and observe whether tail-aware sampling preserves error traces as the fleet recovers. If a regional failover doubles traffic, validate that freshness degrades gracefully but stays within your SLA envelope.

Finally, test guardrails. Attempt to emit a metric with a forbidden label and verify it is rejected or rewritten with an audit event attached. Send a series of oversized logs and watch the truncation path trigger. These are not chaos experiments for drama; they are routine verifications that your cost controls are live and effective.

## Scaling decisions: where to put compute and state

Where you place compute for shaping telemetry determines both cost and resilience. Putting exporters and buffers inside application processes is convenient but risky; a blocked exporter can stall a request thread. Sidecars isolate telemetry from request threads at the cost of more pods and higher baseline CPU and memory, which translate into cloud spend that may dwarf ingress costs if you are not careful. Node-level agents amortize overhead across many workloads, but you must account for noisy-neighbor dynamics when a single tenant explodes event volume.

Central collectors with autoscaling give you control and visibility, yet they create fan-in and potential single points of backpressure. If you choose this pattern, isolate the collectors’ compute plane from the application plane with dedicated nodes or quotas. Scale on leading indicators like exporter queue length and drop rate rather than CPU alone, and pre-scale for known traffic ramps such as batch windows or marketing events to avoid the cold-start tax.

State placement matters too. Tail sampling requires trace context to be buffered until a decision can be made, which consumes memory and introduces decision latency. If your services have long-running spans, set decision waits high enough to capture outcomes, but cap memory and define what gets shed first when buffers fill. The tradeoff is always the same: higher fidelity decisions cost more state; the key is to bear that cost in the telemetry plane, not in the application.

## An illustrative configuration and event design

The following examples are illustrative and intended to show mechanisms, not prescribe settings. They demonstrate tail-aware sampling in a collector and a normalized event shape that is easy to aggregate and budget.

```yaml
# Illustrative: OpenTelemetry Collector configuration with tail-aware sampling.
receivers:
  otlp:
    protocols:
      grpc: {}
      http: {}

processors:
  batch:
    timeout: 2s
    send_batch_size: 512
  tail_sampling:
    decision_wait: 5s
    num_traces: 100000
    expected_new_traces_per_sec: 1000
    policies:
      - name: always-error
        type: status_code
        status_code:
          status_codes: [ERROR]
      - name: ten-percent-default
        type: probabilistic
        probabilistic:
          sampling_percentage: 10

exporters:
  logging:
    loglevel: info

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [batch, tail_sampling]
      exporters: [logging]
```

In this shape, error traces are kept regardless of rate, and non-error traces are sampled probabilistically. The decision wait accommodates services with short lifecycles, and the batch processor reduces chattiness without pushing back into application threads. Your exact numbers will differ; the mechanism to copy is outcome-aware sampling applied in the telemetry plane, not in hot-path code.

```json
{
  "ts": "<rfc3339-timestamp>",
  "service": "<service-name>",
  "request_id": "<uuid>",
  "tenant_id": "<tenant>",
  "route": "POST /v1/checkout",
  "status": 500,
  "duration_ms": 237,
  "bytes_in": 834,
  "bytes_out": 112,
  "outcome": "error",
  "sample_hint": "keep",
  "error_kind": "db_timeout"
}
```

This normalized event is compact, bounded, and sufficient to drive SLI computation and ad-hoc debugging. It separates identity from labels by design: tenant and request identifiers live as fields for searchability, while only stable dimensions should be promoted to metric labels when aggregating. The sample hint allows a downstream sampler to prioritize retention during an incident without switching every producer into verbose mode.

## Operating the loop: decisions, measurement, and iteration

Treat observability like any other reliability loop: make a design decision, measure its effect, and iterate. Start by writing down how much signal you need to resolve the incidents you actually have—error class proportions, top routes by latency, slowest dependencies at peak, and the small number of user journeys that move your business. Then design a telemetry envelope that captures exactly those signals at the edges where they are cheapest to compute and safest to buffer.

Measure the outcome against a budget and an SLA. If queries stay fast and incidents are resolvable within your MTTD and MTTR targets, you have hit the right balance. If not, widen sampling on the routes that confuse you, not everywhere, and verify you can tighten it again post-incident. The architectural constraints you set—bounded label keys, tail-aware sampling, normalized events—let you turn these dials without breaking the bank.

The result is not austerity; it is clarity. When cost pressure returns, you have controls with known effects and a pipeline that fails gracefully. When a real incident hits, you have the error traces, the right rollups, and a queryable event stream that tells you what just happened. That is what it means to treat observability cost as an architecture problem: you solve it once in the design, and you operate it every day with confidence.
