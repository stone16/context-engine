---
name: adr-0015-rls-transaction-context-and-schema-manifest
version: "1.0.0"
description: >
  Define transaction-local tenant context, runtime role constraints, and an
  all-table schema security manifest for PostgreSQL RLS.
---

# 0015. RLS uses transaction-local context and an all-table security manifest

- Status: accepted
- Date: 2026-07-18
- Refines: ADR-0007

## Context

FORCE RLS and a non-owner role are insufficient without a request transaction
protocol. Connection pools, ORM autoflush, cancellation, worker reuse, owner
bypass, or an accidentally unclassified table can escape the intended gate.
Computing RLS coverage only over tables that already contain organization_id
also excludes the exact error the audit must detect.

## Decision

Schema owner/migrator and runtime/worker roles are separate. Runtime and worker
roles are non-owner, NOSUPERUSER, NOBYPASSRLS, and NOINHERIT.

ActorContext is a closed UserActor or ServiceActor union. UserActor carries the
authenticated Principal and Membership. ServiceActor carries a registered
ServicePrincipal, workload identity, Organization, allowed source/operation
set, policy epoch, and expiry. Supply workers use a least-privilege ServiceActor
bound to the durable job and WorkerLease; they never impersonate a triggering
user, and ingestion authority never grants Runtime delivery authority.

WorkerLease is server-minted and signed. It binds Organization, job, operation,
source and optional resource/revision, ServiceActor/workload, policy epoch,
optional audience, idempotency key, lease generation, issued-at, expiry, and
nonce. Redemption checks every claim against the current job row; mutation,
stale generation, and replay deny.

Every request and worker operation begins a transaction, sets Organization and
ActorContext transaction-locally, validates it, and only then permits ORM
autoflush, query, provider, index, or effect work. Cancellation and error roll
back the transaction; pooled connections are returned clean.

SECURITY DEFINER is denied by default. Any approved function fixes search_path,
uses a narrow owner, and receives dedicated negative tests.

A versioned schema security manifest classifies every table and partition as
global or tenant-owned. Tenant-owned entries require Organization ownership,
composite foreign keys, USING and WITH CHECK policies, FORCE RLS, and policy
tests. Coverage is computed over the manifest.

## Consequences

Real PostgreSQL 17 with the runtime role is a per-commit security gate.
In-memory implementations can accelerate ordinary behavior but cannot prove
RLS, pool isolation, composite FK, revocation, or migration safety. Migration
review gains an explicit classification obligation for every new table.
