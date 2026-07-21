---
name: adr-0027-organization-policy-epoch
version: "1.0.0"
description: >
  Select an Organization-level Policy Epoch for the first Acquire revocation
  slice and freeze its atomic mutation and final-delivery validation boundary.
---

# 0027. Organization Policy Epoch is the V0 revocation boundary

- Status: accepted
- Date: 2026-07-21
- Refines: ADR-0010, ADR-0012, ADR-0015, ADR-0019, ADR-0025

## Context

[ADR-0010](0010-policy-epoch-revocation.md) fixes synchronous Policy Epoch
advancement as the revocation mechanism, but deliberately leaves Organization,
Source, and Resource granularity open. Issue #15 needs one concrete boundary for
the first `resolve(Acquire)` proof. It must revoke the next request even while
the same content-free Candidate and stored content remain present, and it must
not imply that future continuation, citation, lease, ticket, or cleanup
carriers exist.

The integrated design eventually requires access mutation, epoch advancement,
restricted `DecisionAudit`, and outbox publication to commit together. Durable
DecisionAudit and outbox carriers are not active in this slice, so claiming that
complete transaction now would be a false-green capability statement.

## Decision

V0 uses one persistent, positive, monotonically increasing Policy Epoch per
Organization. It is a freshness input, never an access grant. Finer Source or
Resource epochs are deferred until measured contention or invalidation evidence
justifies the added state and binding complexity.

The dedicated internal Control operation
`PostgreSQLAccessPolicyControl.change_access(ResourceAccessRevocation)` runs
through a dedicated least-privilege non-owner database role. It is separate
from Runtime and worker identities and cannot issue arbitrary table writes. In
PostgreSQL, the fixed-search-path `SECURITY DEFINER` function is owned by a
separate `NOLOGIN`, `NOINHERIT`, non-superuser role. That owner has schema
`USAGE`, exact Organization-RLS-constrained `SELECT` and `UPDATE` on only the
epoch and access-policy tables, and no schema creation privilege after the
migration. Only the Control login can execute the function; Control has no
direct table privilege and cannot assume the owner role. In one authoritative
transaction the function locks
the Organization epoch, applies the exact same-Organization access revocation,
and advances that Organization's epoch. A failed, missing, duplicate, overflow,
or fault-injected change commits neither half. Concurrent successful changes
serialize into distinct increasing values without a lost increment. Changing
Organization A cannot mutate Organization B's access or epoch.

This is an internal Module seam, not a UI, HTTP admin endpoint, or caller-
authorized external workflow. It does not activate production grant creation or
administrative authentication.

Every authorization decision that can produce Evidence for
`resolve(Acquire)` binds the Organization and epoch observed by its current
Runtime transaction. That retained transaction explicitly overrides and
verifies `READ COMMITTED` before its first statement; it never inherits a
session or database default that could pin the final read to a stale snapshot.
The sealed Runtime revalidates that binding against the current epoch
immediately before ContextPackage delivery. A pre-revocation decision injected
after the committed bump fails closed with zero Evidence, even when
CandidateIndex returns the same Candidate and the stored content is unchanged.
The first post-commit Acquire therefore observes the revoke without waiting for
cleanup, including when revocation commits after content projection but before
the final epoch read.

Only access mutation and epoch advancement are atomic in the active slice.
Durable DecisionAudit and outbox publication remain `NOT_ACTIVE`; their owning
issues must add them to the same authoritative transaction before claiming the
full design-level linearization contract.

## Rationale

Organization granularity gives the first implementation one unambiguous tenant-
isolated freshness value and makes rollback, concurrency, and cross-Organization
controls observable in real PostgreSQL. Revalidating at the final delivery gate
kills stale-decision reuse independently of candidate filtering, cache eviction,
or blob deletion. Keeping the Control seam internal avoids inventing an admin
authorization product before its identity and approval workflows are designed.

## Consequences

- A successful access revoke invalidates older Organization-bound Acquire
  decisions on the next request after commit.
- The before/after Runtime proof keeps query, CandidateRef, and content fixed;
  candidate removal is not evidence of revocation.
- An Organization-wide change may invalidate more cached decision work than a
  finer epoch would. V0 accepts that cost until measurements justify refinement.
- Physical index, cache, and blob cleanup may remain asynchronous and cannot
  authorize delivery.
- `ACCEPT-005` remains a future Continue fixture. OpenCitation, WorkerLease,
  ContextAccessTicket, ActionTicket, and historical Package-byte handling are
  not activated by this decision.

## Revisit trigger

Revisit when measured Organization-level contention or invalidation cost
justifies a finer epoch, or when DecisionAudit, outbox, Continue, OpenCitation,
WorkerLease, ContextAccessTicket, ActionTicket, or cleanup carriers activate.
Any refinement must preserve monotonic tenant isolation, atomic access-change
linearization, and final current-epoch validation before newly delivered bytes.
