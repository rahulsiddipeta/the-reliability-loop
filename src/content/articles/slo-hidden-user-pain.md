---
title: "Your SLO Is Green. Your Users Are Still Hurting."
description: "A healthy aggregate SLO can hide regional failures, broken user journeys and terrible tail latency. Reliability is only as good as what your SLI actually measures."
pubDate: 2026-10-06
author: "Rahul Siddipeta"
category: "SRE"
tags:
  - SRE
  - SLO
  - SLI
  - Observability
  - Reliability Engineering
readTime: "5 min read"
featured: false
draft: false
---

Your reliability dashboard is green. Availability over the last 30 days is **99.95%**. The SLO is being met, error-budget consumption looks normal, and there are no major service-wide alerts.

From the dashboard, the system looks healthy. But customers in one region are reporting failures. Checkout latency has climbed above a second, some requests are timing out, and support tickets are starting to appear.

Both things can be true at the same time:

> **Your SLO can be green while some of your users are having a terrible experience.**

The problem isn't necessarily the SLO calculation. The problem may be what we've chosen to aggregate.

![A green aggregate SLO hiding degraded regional performance and a failing checkout journey](/images/articles/slo-hidden-user-pain.png)

*Illustrative example: the overall service satisfies its availability objective while a region and a critical user journey are significantly degraded.*

## Aggregation can hide concentrated failure

Imagine an API serving traffic across several regions. Most requests are succeeding. US East, US West and Asia Pacific are operating normally, while Europe is experiencing elevated failures.

If Europe represents a relatively small percentage of total request volume, the healthy regions can dominate the aggregate availability calculation. At the service level, the number may still look excellent. For users in the affected region, however, the service isn't **99.95% available**. It's simply unreliable.

The same problem can happen inside an application. Suppose browsing, search, profiles and several background APIs are all healthy, but checkout is failing. If checkout represents only a small fraction of total requests, millions of successful low-value requests can statistically overwhelm a much smaller number of failures on the workflow users actually care about.

At the service level, the dashboard says **healthy**. For the affected customer, the reality is much simpler: **I can't complete my purchase.**

Neither observation is mathematically inconsistent. They're measuring different things.

## Averages hide tails too

Availability isn't the only place aggregation causes problems. Latency can be even more deceptive.

Suppose an API reports:

```text
Average latency: 180 ms
```

That sounds healthy. But the distribution might look very different:

```text
p50     110 ms
p95     420 ms
p99   1,900 ms
```

Most requests are fast enough to keep the average looking reasonable, while a smaller percentage of users repeatedly experience multi-second responses. For a high-volume system, even 1% of requests can represent a significant number of real interactions.

This is why reliability engineering usually cares much more about **distributions and percentiles** than averages alone. The average describes the center of the traffic; the tail often describes where users start getting angry.

## Service health is not always user-journey health

Another common problem is defining reliability entirely around infrastructure or service boundaries.

Users don't experience our architecture the way we diagram it. They experience journeys.

A checkout flow might depend on:

```text
Frontend
   ↓
Cart Service
   ↓
Checkout API
   ↓
Inventory
   ↓
Payment Provider
   ↓
Order Service
```

Every individual service could technically satisfy its own availability objective while the complete transaction still fails more often than the business considers acceptable.

A dependency might be slow rather than unavailable. Retries may eventually succeed but push the transaction beyond an acceptable latency threshold. A failure might occur only for one payment method, client type or geographic region.

From the perspective of individual services, everything may remain mostly green. From the user's perspective, **checkout doesn't work**.

That's why some of the most useful SLIs are tied not only to infrastructure components but to **critical user outcomes**: can the user authenticate, search, submit the transaction or retrieve the data they came for?

The closer an SLI gets to what the user is actually trying to accomplish, the more meaningful the corresponding SLO becomes.

## Segmentation changes what you see

An aggregate SLO is still useful. It gives us a concise view of overall reliability and a common language for error budgets. The mistake is treating it as the only view of system health.

When something looks suspicious, reliability data becomes much more useful when we can segment it across dimensions that matter. Depending on the system, that might include:

- region or availability zone,
- endpoint,
- customer tier,
- device or client type,
- dependency,
- response-code family,
- workload type,
- or critical user journey.

The goal isn't to create an SLO for every possible combination. That creates a different problem: hundreds of reliability signals that nobody can realistically operate.

Instead, segmentation should help answer a practical question:

> **Can a meaningful group of users be suffering while the aggregate still looks healthy?**

If the answer is yes, we need enough visibility to find them.

## SLOs are models, not reality

This is the part that's easy to forget: an SLO is not the user experience itself. It's a **model of the user experience**.

We choose an indicator. We define what success means. We select a measurement window. We choose a target and decide which traffic belongs in the calculation. Those decisions compress a complicated production system into something engineers can reason about.

That's incredibly useful, but every model leaves information out.

A green SLO therefore doesn't prove that every user is receiving a reliable experience. It tells us that the system satisfied the reliability objective **as we defined and measured it**.

When reality and the model begin to disagree, the right response isn't to defend the dashboard. It's to improve the model.

## What I would look for

When users report problems but the primary SLO remains healthy, I'd start by asking whether the failure is concentrated in one region, endpoint or customer journey. I'd check whether averages are hiding poor tail latency, whether the SLI measures technical success while missing the actual user outcome, and whether a low-volume but business-critical workflow is being statistically overwhelmed by high-volume healthy traffic.

Then I'd ask whether the reliability model needs another perspective. That might mean a regional breakdown, a journey-level SLI, better latency percentiles or an additional indicator for a critical transaction.

Not every problem requires another SLO. Sometimes you simply need better observability underneath the one you already have.

## The dashboard is not the customer

SLOs are one of the most useful tools reliability engineering has given us. They help teams define acceptable reliability, reason about risk and make better decisions about engineering investment.

But **the number is not the objective. The user experience is the objective.**

The SLO is our attempt to measure it.

So when the dashboard says **99.95% — SLO met**, but users are still telling us the system is broken, both signals deserve attention.

A green SLO doesn't necessarily mean users are happy. It means the system satisfied the reliability model we gave it.

And when users are hurting while that model remains green, the first thing to question may not be the system.

> **It may be the model.**