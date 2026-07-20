---
name: adr-0020-stage-organization-rls-before-actor-context
version: "1.0.0"
description: >
  Bound the first Organization RLS proof without activating an incomplete
  request or worker ActorContext protocol.
---

# 0020. Stage the Organization RLS proof before ActorContext activation

- Status: accepted
- Date: 2026-07-20
- Refines: ADR-0015

## Context

ADR-0015 requires a production request or worker operation to bind both its
Organization and a closed ActorContext before any content work. The first
database-isolation slice must prove Organization ownership, composite foreign
keys, non-owner FORCE RLS, transaction cleanup, and pool reuse. That slice
explicitly precedes the User, Membership, ServiceActor, WorkerLease, trusted
ingress, and sealed Runtime work needed to construct a valid ActorContext.

Inventing an opaque actor string or a temporary user table would create a
second identity protocol and make the database evidence look stronger than the
implemented trust boundary. Delaying all RLS evidence until those later domain
contracts exist would leave the selected PostgreSQL design untested.

## Decision

The first evidence slice may expose one engine-internal
`organization_transaction` boundary. It owns a fresh transaction, accepts only
an already trusted UUID value, sets `app.organization_id` transaction-locally,
verifies the readback, and only then exposes the connection. Its schema is
limited to the global Organization root and one representative tenant-owned
record.

This boundary is database evidence, not an activated request or worker
protocol. It is not exposed through HTTP or the sealed Runtime, performs no
provider, index, package, or external-effect work, and cannot raise Runtime or
worker capability status. A custom PostgreSQL setting is not authentication;
the owning ingress must establish the trusted Organization before calling this
boundary.

Before any production request or worker operation performs content work, the
owning implementation must extend the transaction protocol to bind and validate
the closed ADR-0015 ActorContext alongside Organization. UserActor construction
waits for authenticated Principal and Membership authority. ServiceActor
construction waits for the registered service identity, durable job, and signed
WorkerLease authority. No fallback actor or default Organization is permitted.

## Consequences

The repository can verify the selected Organization GUC, FORCE RLS, composite
ownership, missing-context denial, rollback, and pooled reuse against real
PostgreSQL without prematurely defining identity tables. Documentation and
capability reports must continue to mark complete ActorContext, Runtime
delivery, and worker behavior as inactive.

`DB-009`, which rejects Organization-only transactions in an activated request
or worker path, remains `NOT_ACTIVE` for this evidence-only boundary. The schema
manifest must not list that case as covered until the complete ActorContext
protocol exists and its owning negative test runs.

For the non-owner runtime role, a missing or reset Organization GUC produces the
same deterministic PostgreSQL invalid-text-representation error for reads and
writes. This prevents a missing-context write from being mistaken for an
authorized no-op. A present but different Organization remains an ordinary RLS
miss with zero visible rows and zero effects.

The representative record is an evidence carrier, not permission to generalize
an RLS framework before a second real tenant-owned domain table exists.

## Revisit trigger

Revisit when trusted ingress plus User/Membership activates the first Runtime
content transaction, or when ServiceActor plus WorkerLease activates the first
worker content transaction. That implementation must replace this staged entry
with the complete atomic Organization-and-ActorContext protocol required by
ADR-0015 and retain all existing fail-closed tests.
