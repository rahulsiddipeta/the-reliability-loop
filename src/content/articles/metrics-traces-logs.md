---
title: "Metrics Tell You It's Burning. Traces Tell You Where."
description: "Metrics, logs and traces answer different questions during an incident. The real power of observability appears when engineers can move between all three without losing context."
pubDate: 2026-10-07
author: "Rahul Siddipeta"
category: "Observability"
tags:
  - Observability
  - SRE
  - Metrics
  - Distributed Tracing
  - Logging
readTime: "6 min read"
featured: false
draft: false
---

The alert says API latency is rising.

Your dashboard confirms it. p95 latency has climbed from 300 milliseconds to more than two seconds, and the error rate is moving in the wrong direction.

You know something is broken.

But you still don't know **where**.

In a distributed system, that distinction matters. A dashboard can tell us the system is unhealthy without telling us which dependency, request path or operation is responsible.

That's where observability becomes more than collecting telemetry.

> **Metrics tell you there's a problem. Traces help tell you where it's happening. Logs help explain why.**

The real value appears when we can move between those signals as part of the same investigation.

![Metrics, traces and logs working together during an incident investigation](/images/articles/observability-metrics-traces-logs.png)

*Illustrative incident workflow: metrics expose the degradation, traces isolate the slow request path, and logs provide the context needed to understand the failure.*

## Start with the symptom

Imagine an API whose normal p95 latency is around 300 milliseconds.

At 12:15, latency starts climbing.

By 12:25:

```text
API p95 latency      2,400 ms
Error rate                7.8%
Request volume          normal
CPU utilization         normal
Memory utilization      normal
```

The first two signals clearly tell us something is wrong.

The last three make the incident more interesting.

There isn't an obvious traffic spike. CPU isn't saturated. Memory isn't exhausted.

Adding application capacity may accomplish nothing because we haven't established that application compute is the bottleneck.

At this stage, metrics have done their job extremely well.

They told us **when the system changed and how badly user-facing behavior degraded**.

Now we need another perspective.

## Find which service changed

Suppose the request path contains several services:

```text
Web Application
      ↓
API Gateway
      ↓
Order Service
   ↙    ↓     ↘
User  Inventory  Payment
```

Service-level metrics show:

```text
User Service        180 ms
Inventory Service   210 ms
Payment Service     340 ms
Order Service     2,400 ms
```

We've reduced the search space considerably.

Instead of investigating the entire platform, we now have evidence pointing toward the Order Service.

But even this doesn't fully answer the question.

The Order Service may perform several operations during one request. One might query inventory, another may retrieve customer data, another may call payment infrastructure, and another may write the final order.

A service-level latency metric tells us **which neighborhood to investigate**.

We still need the address.

## Traces expose the request path

Distributed tracing changes the investigation from looking at isolated services to looking at the lifecycle of an individual request.

A slow order request might reveal something like:

```text
POST /api/orders                 2,412 ms
│
├── API Gateway                    28 ms
│
├── Order Service               2,412 ms
│   │
│   ├── Get User                   42 ms
│   ├── Check Inventory            68 ms
│   ├── Reserve Item (DB)       1,892 ms
│   ├── Process Payment           310 ms
│   └── Write Order (DB)           72 ms
```

Now the investigation changes.

The Payment Service isn't the primary source of latency.

The API Gateway isn't the problem.

Most of the request time is being spent inside **Reserve Item**, specifically around a database operation.

We've moved from:

```text
The API is slow.
```

to:

```text
The Order Service is slow.
```

to:

```text
A specific database operation inside the
Order Service is consuming most of the request time.
```

That's a much more actionable hypothesis.

But a trace still doesn't necessarily tell us **why** that database operation became slow.

For that, we need more context.

## Logs explain the failure around the span

If the trace and logs share identifiers such as a trace ID, request ID or correlation ID, we can move directly from the slow span into the events surrounding it.

The relevant logs might show:

```text
12:23:14 INFO  Received order request
12:23:14 INFO  Checking inventory
12:23:15 WARN  DB query taking longer than expected
12:23:16 ERROR Connection pool exhausted
12:23:16 ERROR Failed to reserve inventory
12:23:16 INFO  Retrying request attempt=2
12:23:46 ERROR Request failed duration=2.4s
```

Now we have a much stronger explanation.

The database query is slow, but there's another important signal: **the connection pool is exhausted**.

That gives us several new questions to investigate.

Did database latency increase first and cause connections to remain occupied longer? Did application concurrency increase? Did a deployment change pool behavior? Are retries creating additional database pressure? Is one expensive query consuming disproportionate capacity?

This is where logs are particularly useful.

Metrics describe system behavior at scale.

Traces describe how a request traveled through the system.

Logs provide detailed events and application context around what happened.

None of those signals completely replaces the others.

## Correlation is more valuable than collection

It's possible to have excellent metrics, enormous log volumes and distributed tracing deployed across every service—and still have poor observability.

Why?

Because telemetry that can't be connected forces engineers to manually reconstruct the incident.

Imagine this workflow:

```text
Alert fires
   ↓
Open metrics dashboard
   ↓
Find suspicious service
   ↓
Open tracing platform
   ↓
Manually search time window
   ↓
Find slow trace
   ↓
Copy request ID
   ↓
Open logging platform
   ↓
Search request ID
   ↓
Reconstruct timeline
```

Technically, all the telemetry exists.

Operationally, the engineer is acting as the correlation engine.

A better experience preserves context as we move between signals:

```text
Metric anomaly
      ↓
Affected service
      ↓
Representative trace
      ↓
Slow span
      ↓
Correlated logs
      ↓
Deployment / dependency context
```

The difference isn't simply convenience.

During an incident, every manual search increases investigation time and creates another opportunity to follow the wrong signal.

> **The value of telemetry increases when context survives the transition from one signal to another.**

## High-cardinality context matters

This is also where dimensions become important.

A global latency graph may show degradation, but the incident could affect only:

```text
region = us-west
endpoint = POST /api/orders
version = v2.18.4
dependency = inventory-db
```

Without those dimensions, the aggregate signal can hide the pattern.

With them, we can ask much more useful questions:

Is the problem isolated to one deployment version? One region? One endpoint? One database cluster? One customer workflow?

This doesn't mean attaching unlimited metadata to every metric. High-cardinality telemetry has real storage and cost implications.

It means designing telemetry around the questions engineers will need to answer when the system behaves unexpectedly.

## Observability should shorten the path to a hypothesis

During an incident, we rarely begin with certainty.

We begin with evidence.

```text
Latency increased.
```

Then:

```text
The Order Service appears responsible.
```

Then:

```text
Reserve Item accounts for most of the latency.
```

Then:

```text
Database connections are exhausted.
```

Then perhaps:

```text
A deployment increased database concurrency,
and retries amplified the pressure.
```

Each signal should help reduce the number of plausible explanations.

That's how I think about observability during incident response: not as a collection of dashboards, but as a system for **progressively reducing uncertainty**.

The faster telemetry moves us from symptom to a testable hypothesis, the more useful the observability platform becomes.

## Verification needs the same signals

Suppose we determine that database connection pressure is the immediate cause and apply a safe remediation.

The incident isn't over simply because the change was deployed.

We need to verify the outcome.

Metrics should show latency and error rate recovering.

Traces should show the problematic span returning toward its normal duration.

Logs should stop reporting pool exhaustion or related failures.

Ideally, the verification signal is independent enough that we're not simply trusting the mechanism that performed the remediation.

The loop becomes:

```text
Detect
  ↓
Investigate
  ↓
Hypothesize
  ↓
Remediate
  ↓
Verify
```

Observability supports every stage.

Not just detection.

## More telemetry isn't automatically better observability

It's easy to measure observability maturity by volume.

More dashboards.

More logs.

More spans.

More retention.

More agents.

But telemetry volume and operational understanding aren't the same thing.

A system producing terabytes of logs can still leave engineers unable to explain why checkout latency doubled.

A smaller telemetry system with strong service ownership, meaningful SLIs, consistent correlation IDs, useful traces and well-designed dimensions may be far more effective.

The question isn't:

**How much telemetry are we collecting?**

The better question is:

> **How quickly can an engineer move from a user-visible symptom to evidence explaining what changed?**

That's the outcome observability should optimize for.

## Use all three together

Metrics, traces and logs each have strengths.

**Metrics** are excellent for detecting trends, alerting on aggregate behavior and understanding how the system changes over time.

**Traces** expose request paths, dependency relationships and where latency accumulates across distributed services.

**Logs** provide detailed application events and contextual evidence surrounding individual failures.

The most useful observability systems don't force engineers to choose between them.

They connect them.

Because during a production incident, knowing that something is burning is only the beginning.

Metrics can raise the alarm.

Traces can lead us toward the source.

Logs can help explain what happened.

And when those signals share enough context to work together, observability stops being a collection of tools.

**It becomes a way to understand the system.**