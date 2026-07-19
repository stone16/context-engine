---
name: adr-0008-modular-monolith-plus-worker
version: "1.2.0"
description: >
  Record the process topology: a modular-monolith engine API plus an independent
  worker, and one justified Bot delivery application process from M2; no
  premature engine microservice split. Use when proposing service extraction or
  new processes.
---

# 0008. Modular monolith + independent worker

- Status: accepted
- Date: 2026-07-18

## Context

Every process boundary adds authenticated transport, tenant-context propagation,
retry/idempotency behavior, credential scope, and distributed failure modes. The
engine does require an independently schedulable Supply worker, but current
isolation and throughput requirements do not justify splitting its domain modules
into additional network services. Missing organization or job context must fail
closed at both the API and worker boundaries.

## Decision

Before M2 the engine has two processes: the API service and an independent
worker, sharing one domain package. Async work flows through a transactional outbox + job table
(SKIP LOCKED) with a server-minted signed WorkerLease (WORKER-LEASE-007) and
org-scoped idempotency keys. The lease binds Organization, job, operation,
source, optional resource/revision, ServiceActor/workload, policy epoch,
optional audience, idempotency key, lease generation, issued-at, expiry, and
nonce; redemption checks every claim against the durable job row.

M2 adds one trusted Bot application process containing BotDelivery and
ActionPlane. It is a justified caller and credential boundary, not an engine
microservice extraction: BotDelivery reaches Runtime only through generated
HTTP SDK contracts and cannot import engine internals. No further process or
service extraction occurs until a measured bottleneck or isolation requirement
triggers a revisit; seams are re-examined when the second Adapter of a kind
appears.

## Consequences

Trusted context propagates through explicit request evidence or the job/lease
protocol; the worker's Organization and ServiceActor context is
transaction-local and never ambient or borrowed from a triggering user. Engine
and Bot applications may ship from one repository/release while remaining
separate process and import boundaries.
