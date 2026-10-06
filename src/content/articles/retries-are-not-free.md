---
title: "Retries Are Not Free: How Retry Storms Turn Small Failures Into Incidents"
description: "Retries improve resilience when failures are transient. Used carelessly, they multiply load, amplify dependency failures and turn small problems into cascading incidents."
pubDate: 2026-10-06
author: "Rahul Siddipeta"
category: "Distributed Systems"
tags:
  - Distributed Systems
  - SRE
  - Reliability Engineering
  - Resilience
  - Incident Response
readTime: "7 min read"
featured: false
draft: false
---

Retries are one of the simplest reliability patterns in distributed systems.

A request fails because of a temporary network interruption, a brief service restart or a transient timeout. The client tries again, the second attempt succeeds, and the user never notices the failure.

That's exactly what we want.

But retries have another property that's easy to underestimate:

> **Every retry is additional load applied to a system that may already be struggling.**

When one client retries, that additional request is insignificant. When thousands of clients retry simultaneously—and multiple layers of the system each have their own retry logic—the behavior changes completely.

A small dependency failure can become a **retry storm**.

And the mechanism intended to improve reliability can become the thing preventing the system from recovering.

![How retries amplify a small dependency failure into a retry storm](/images/articles/retry-storm-amplification.png)

*Illustrative example: a small downstream failure triggers additional requests, increasing load until retries themselves become part of the incident.*

## The retry that saves a request

Consider a simple request path:

```text
Client
  ↓
API Service
  ↓
Payment Service
```

Under normal conditions, the API receives a request, calls the payment service and returns the result.

Now imagine the payment service experiences a brief network interruption. The first call fails, but the client retries 200 milliseconds later. By then the network has recovered and the second attempt succeeds.

The retry converted a transient infrastructure failure into a successful user request.

This is why retries are everywhere.

HTTP clients retry. Service meshes retry. SDKs retry. Load balancers retry. Message consumers retry. Application code retries. Cloud services and database libraries may have their own retry behavior as well.

Individually, each decision can seem perfectly reasonable.

The problem appears when we look at the system rather than the individual request.

## A failure changes the economics of a retry

Suppose a service normally receives **1,000 requests per second**.

A downstream dependency begins failing 5% of requests.

Without retries, approximately 50 requests per second fail.

Now assume each failed request can retry up to three times.

At first, the additional traffic may appear manageable. But the dependency is already unhealthy. Some retry attempts fail too, generating more attempts. Meanwhile, new traffic continues arriving at the normal rate.

The system isn't replacing failed requests.

It's **adding new work on top of existing work**.

If the dependency becomes slower as load increases, requests remain in flight longer. Connection pools fill. Queues grow. Timeouts become more frequent. More timeouts generate more retries.

The feedback loop can look like this:

```text
Dependency slows down
        ↓
Requests time out
        ↓
Clients retry
        ↓
Dependency receives more load
        ↓
Latency increases further
        ↓
More requests time out
        ↓
More retries
        ↺
```

At this point, retries are no longer masking the failure.

They're amplifying it.

## Retry multiplication across layers

The situation becomes more dangerous when retries exist at multiple layers.

Imagine:

```text
Client
  ↓
API Gateway
  ↓
Order Service
  ↓
Payment Service
```

The client retries twice.

The gateway also retries failed upstream requests.

The order service has its own retry policy for payment calls.

A single user operation can now create far more backend attempts than anyone looking at one layer of the architecture might expect.

The exact amplification depends on how the policies interact, but the important principle is simple:

> **Retry budgets compose poorly when every layer assumes it is the only layer retrying.**

This is one reason retry behavior should be considered an architectural property rather than merely a client-library configuration.

During normal operation, duplicated retry policies may remain almost invisible. During a dependency failure, they can reveal themselves all at once.

## Timeouts and retries are coupled

Retries can't be designed independently from timeouts.

If a timeout is too aggressive, a client may abandon a request that was merely slow—not actually failed—and send another attempt while the original request continues executing.

Now the backend may be processing both requests.

For read operations, this wastes capacity.

For writes, the consequences can be more serious.

Imagine a payment request times out from the client's perspective, but the server successfully processes it just before the connection closes. The client assumes failure and retries.

Without appropriate idempotency controls, the system may perform the operation twice.

That's why retry design needs to consider at least three things together:

**Timeout policy** — How long do we wait before deciding an attempt has failed?

**Retry policy** — Which failures should actually be retried?

**Idempotency** — Can repeating the operation safely produce the same outcome?

Treating those as separate concerns is how resilience mechanisms begin interacting in surprising ways.

## Backoff gives the dependency room to recover

One of the simplest protections against retry storms is **exponential backoff**.

Instead of retrying immediately:

```text
Attempt 1 → fail
Attempt 2 → immediately
Attempt 3 → immediately
Attempt 4 → immediately
```

the client progressively waits longer:

```text
Attempt 1 → fail
Wait ~200 ms
Attempt 2 → fail
Wait ~400 ms
Attempt 3 → fail
Wait ~800 ms
Attempt 4
```

The exact values depend on the workload, but the objective is the same: stop clients from continuously hammering a dependency that may need time to recover.

Backoff also reduces synchronization.

But by itself, it isn't enough.

If ten thousand clients fail at approximately the same moment and all use the same deterministic backoff schedule, they may retry together 200 milliseconds later, then 400 milliseconds later, then 800 milliseconds later.

Instead of eliminating the traffic spike, we've simply scheduled the next one.

## Jitter prevents synchronized recovery

This is where **jitter** becomes important.

Rather than every client waiting exactly the same amount of time, we introduce randomness into the delay.

Conceptually:

```text
Client A → retry after 217 ms
Client B → retry after 341 ms
Client C → retry after 286 ms
Client D → retry after 403 ms
```

The retries spread across time instead of arriving as another synchronized wave.

This is especially important in large distributed systems where many clients may observe the same dependency failure simultaneously.

A retry strategy without jitter can accidentally coordinate thousands of independent clients into behaving like one enormous traffic generator.

## Not every failure deserves a retry

Another important protection is deciding **what is actually retryable**.

A timeout might be transient.

A connection reset might be transient.

A temporary `503 Service Unavailable` may be retryable.

But retrying a permanent validation error won't help.

```text
400 Bad Request
401 Unauthorized
403 Forbidden
```

Sending the same invalid request three more times doesn't improve reliability. It simply creates additional work.

Even server-side errors need context. Some failures indicate temporary capacity problems where immediate retries may make the situation worse.

The question shouldn't be:

**Did the request fail?**

It should be:

**Is there a reasonable probability that another attempt will succeed, and is the system healthy enough to accept that attempt?**

## Retry budgets put a ceiling on amplification

Retries should have limits.

That sounds obvious, but the useful limit isn't always simply "three attempts per request."

At scale, it's valuable to think in terms of a **retry budget**: how much additional traffic are we willing to generate because of failures?

If a service is healthy, a small number of retries may be harmless.

As failure rates increase, unrestricted retries become progressively more dangerous. At some point, protecting the dependency becomes more important than trying every possible request again.

A retry budget gives the system a way to say:

> We're already spending too much capacity on repeated work. Stop retrying and fail fast.

This helps prevent retries from consuming the capacity needed for healthy requests and recovery operations.

## Circuit breakers change the question

Retries ask:

> Should I try this request again?

A circuit breaker asks a different question:

> Should I send this request at all?

If a dependency is clearly unhealthy, continuing to send normal traffic plus retry traffic may serve no useful purpose.

A circuit breaker can temporarily stop requests, fail fast or route traffic toward a fallback while allowing the dependency time to recover.

That creates another useful feedback loop:

```text
Failures increase
      ↓
Circuit opens
      ↓
Traffic decreases
      ↓
Dependency gets recovery time
      ↓
Limited probes test health
      ↓
Circuit gradually closes
```

Retries and circuit breakers therefore solve different parts of the same resilience problem.

Retries help us survive **transient failures**.

Circuit breakers help us avoid **continuously attacking persistent failures**.

## Watch retries as a first-class reliability signal

One operational mistake is monitoring only the final success rate.

Suppose the user request succeeds, but only after three backend attempts.

From the outside, availability may still look healthy.

Inside the system, something is deteriorating.

Retry volume can act as an early warning signal because it often rises before user-visible failures become severe.

Useful signals can include retry rate, attempts per successful request, retry success rate, dependency latency, timeout rate and the amount of traffic generated by retries versus original requests.

A service that remains at 99.9% availability while retry traffic quietly triples is not necessarily healthy.

It may simply be spending increasingly more capacity to maintain the appearance of health.

## Resilience mechanisms need limits

Retries are valuable. I would not build a distributed system without considering them.

But reliability mechanisms aren't automatically safe just because they were designed to improve reliability.

Retries consume capacity.

Timeouts can create duplicate work.

Backoff without jitter can synchronize clients.

Multiple retry layers can multiply traffic.

Unlimited retry behavior can prevent a degraded dependency from recovering.

The safer pattern is to treat retries as a **bounded resource**: retry only failures that have a reasonable chance of succeeding, use backoff and jitter, coordinate retry behavior across layers, protect non-idempotent operations, establish retry limits, and observe retry traffic directly.

The important mental model is simple:

> **A retry is not free. It's another request sent to a system that just told you it was having trouble.**

Sometimes that second request is exactly what saves the user experience.

Sometimes ten thousand second requests are what turn a small failure into an incident.

Reliability engineering is knowing the difference.