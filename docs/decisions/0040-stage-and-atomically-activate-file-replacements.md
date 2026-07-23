---
name: adr-0040-stage-and-atomically-activate-file-replacements
version: "1.0.0"
description: >
  Stage one complete changed File Revision behind the old active pointer, then
  atomically activate it with immutable supersession lineage.
---

# 0040. Stage and atomically activate File replacements

- Status: accepted
- Date: 2026-07-23
- Refines: ADR-0018, ADR-0037, ADR-0038, ADR-0039

## Context

ADR-0039 classifies changed canonical content or a meaning-affecting compiler
contract separately from an unchanged no-op, but did not publish that change.
A replacement cannot remove or hide the current Revision while the new
snapshot, Fragments, candidate representation, and publication evidence are
still being built. Runtime also resolves a Package through multiple SQL
statements at `READ COMMITTED`; an active-pointer swap between candidate
discovery and authorized projection would otherwise create a spurious empty
Package even when both Revisions are complete.

The first replacement slice must retain the previous immutable Revision for
replay and future cleanup without inventing a deletion policy. Worker crash
recovery after the durable ready boundary is owned by the next issue and must
not expand this success-path authority.

## Decision

The existing tenant/source/resource ingestion-guard row remains the
classification and writer-serialization authority. The unchanged classifier
runs first. Only its `changed` result may enter replacement.

Replacement has two database transactions under version-bound SECURITY DEFINER
entrypoints:

1. **Stage.** Revalidate the exact running File job, WorkerLease,
   ServicePrincipal, current acquisition Membership version, body access, and
   the complete old active artifact. Insert a new immutable Revision, snapshot,
   complete source-ordered Fragment set, exact-phrase candidates, and exactly
   `prepared -> indexed` events. Persist an immutable
   `file_revision_replacement_plan` binding the old and new Revision, Source,
   Resource, acquisition, job, and content identity. Move the job to `ready`.
   The Resource active pointer is not changed.
2. **Activate.** Revalidate the exact still-live WorkerLease and all current
   authority, lock the ingestion guard and replacement plan, and prove the new
   snapshot, Fragments, candidates, and exact `prepared -> indexed` history are
   complete. Compare-and-swap the Resource pointer from the plan's previous
   Revision to its replacement. In the same transaction append `active`, write
   one immutable `file_revision_supersession`, and complete the job with one
   publication effect.

The replacement plan is the durable ready boundary; it is not Runtime-visible
content or authorization. `file_revision_supersession` records the exact old to
new edge with retention state `retained_until_explicit_cleanup`. The old
Revision, snapshot, Fragments, candidates, and events remain immutable and
physically retained. No cleanup duration or delete authority is active in this
slice.

Runtime UserActor transactions take an Organization-scoped shared transaction
advisory lock after binding the trusted database role/context. Activation takes
the matching exclusive transaction advisory lock immediately before validation
and pointer swap. This lock is only a visibility barrier across Runtime's
multi-statement transaction; it neither grants content nor replaces
Membership, scope, policy, field-right, or `AuthorizationKernel` decisions.
The ingestion-guard row remains the writer serialization boundary. Thus an
in-flight public HTTP resolve finishes entirely against the old pointer, while
the next resolve sees the new pointer; there is no mixed or empty window.

The worker retains no direct table mutation privilege. V1 and structural V2
staging have separate closed entrypoints, followed by the same activation
entrypoint. Initial publication and unchanged no-op behavior remain unchanged.
Any failed pre-ready attempt rolls back its staging transaction. Reclaim or
replay of a committed `ready` job is explicitly deferred to recovery work.

## Rationale

Building all content before the pointer swap keeps activation small and gives
PostgreSQL one clear visibility linearization point. The compare-and-swap binds
activation to the old Revision that was actually inspected, preventing a stale
replacement from overwriting a newer publication. Retained immutable lineage
supports audit and future recovery without granting readers access to an
inactive Revision.

The Organization-scoped reader/writer barrier is required because the current
public Runtime seam deliberately uses `READ COMMITTED` and several statements.
It prevents a resolve from discovering old candidates and projecting after the
pointer has moved. Per-Resource locking alone cannot protect that reader
transaction.

## Consequences

- Before activation, Runtime returns only the complete old Revision; after it,
  only the complete new Revision.
- Concurrent public HTTP resolves are all-old or all-new. Candidate discovery
  still feeds `CandidateRef -> AuthorizationKernel -> AuthorizedProjection`.
- Other Organizations do not share the visibility barrier; other Resources are
  not mutated by the exact Resource compare-and-swap.
- Superseded storage grows until an explicit cleanup policy and authority are
  accepted. This issue performs no garbage collection.
- A crash after `ready` can leave a complete inactive replacement and ready job;
  recovery/replay is deliberately deferred rather than silently improvised.

## Revisit trigger

Revisit before adding ready-job reclaim, batch activation, delete/tombstone,
physical Revision cleanup, another Runtime isolation level, or a publication
process that spans Organizations. Any change must preserve immutable lineage,
exact WorkerLease binding, all-old/all-new public visibility, tenant isolation,
and the sealed Runtime authorization path.
