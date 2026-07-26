---
name: adr-0060-reclaim-expired-file-imports-with-bounded-retries
version: "1.0.0"
description: >
  Reclaim expired scheduler-owned File imports with database-timed bounded
  backoff while preserving the existing exact WorkerLease recovery path.
---

# 0060. Reclaim expired File imports with bounded retries

- Status: accepted
- Date: 2026-07-26
- Refines: ADR-0029, ADR-0041, ADR-0043, ADR-0055, ADR-0059

## Context

Autonomous File dispatch can stop after leasing or between the acquired,
prepared, and indexed publication boundaries. The durable job retains enough
lineage to resume, but ADR-0059 deliberately leaves every expired scheduler
lease untouched. Without a bounded reclaim owner, one ordinary process stop can
leave an accepted upsert unpublished indefinitely. Letting the process choose a
tenant, job, retry time, or generation would weaken the function-only scheduler
boundary and duplicate the existing WorkerLease authority.

## Decision

The existing scheduler claim function first considers expired scheduler-owned
File imports, then delegates to its unchanged first-attempt selector. PostgreSQL
selects one globally eligible recovery with `FOR UPDATE SKIP LOCKED`, using the
database clock and a fixed exponential delay of 30, 60, then 120 seconds after
lease expiry. It may mint only generations two through four, so one first
attempt has at most three automatic reclaims. Generation four remains untouched
after expiry; this decision adds no dead-letter transition or operator requeue.

Recovery revalidates the same exact current page, upsert observation,
SourceVersion/root capability, Membership version, receiver, and scan epoch as
first dispatch. It also holds the existing per-Source progress lock and advances
only the locked job to the exact next generation. The transaction preserves the
durable recovery boundary, clears prior redemption, replaces the nonce and
database timestamps, and appends one immutable digest-only `reclaimed` event.
The scheduler still has no table access and receives only the existing closed
lease/no-work result. A missing configured root or failed current authority
check yields no work and no mutation.

The independent Supply worker consumes the replacement WorkerLease through the
existing publication-recovery state machine. Every older token is stale because
generation, nonce, and current job state are checked together. No new process,
queue, Runtime authority, or content-bearing retry path is introduced.

## Consequences

- Concurrent schedulers can advance an expired generation only once.
- Recovery work is preferred over fresh first attempts once its database-owned
  delay has elapsed; ineligible or exhausted work does not block fresh work.
- A crash at any durable publication boundary resumes toward one active Revision
  and one publication effect; the replaced lease has zero effect.
- Downgrade is refused while a scheduler-owned higher-generation lease remains,
  because the previous schema cannot preserve its automatic-reclaim provenance.
- Retry exhaustion remains durable but operationally unresolved.

## Revisit trigger

Revisit before adding terminal-failure retries, dead-letter state, exhaustion
transitions, operator remediation/requeue, configurable backoff, provider
polling, automatic page acceptance, or automatic upsert/delete ordering. Any
revision must preserve database-selected tenant routing, exact-generation lease
fencing, current authority revalidation, content-free scheduler results, and one
publication authority.
