---
name: adr-0041-recover-file-publication-by-durable-boundary
version: "1.0.0"
description: >
  Recover one interrupted File publication from stable acquired, prepared, or
  ready state using a higher-generation lease and immutable transition audit.
---

# 0041. Recover File publication by durable boundary

- Status: accepted
- Date: 2026-07-23
- Refines: ADR-0029, ADR-0037, ADR-0039, ADR-0040

## Context

File publication now spans classification, immutable Revision construction,
candidate/index preparation, and the active-pointer transaction. A worker may
stop after any committed step. Deleting partial state and creating a new job
would lose the exact attempt identity, can duplicate immutable lineage, and can
create an empty visibility window during replacement.

Recovery must reuse the File job and WorkerLease protocol. It must also make an
expired lease permanently stale once a new worker owns the job, while keeping
the old active Revision available until activation.

## Decision

One File job has four durable checkpoints: `acquired`, `prepared`, `ready`, and
`completed`. `file_publication_recovery` binds the job to Organization, Source,
stable Resource, one generated Revision, optional previous Revision, versioned
content identity, compiler contract, and a digest of the exact compilation and
write artifacts. Structural-v2 artifacts must exactly project the validated
compilation document; v1 artifacts must exactly project the canonical paragraph.
Each step is a separate PostgreSQL transaction and advances only from its exact
predecessor.

Lease issue may reclaim an expired `leased`, `running`, `prepared`, or `ready`
job. It increments `lease_generation`, replaces the nonce/time binding, and
records the state to resume. The generation is a signed WorkerLease claim;
redemption restores that state and clears the transient resume marker. Every
later mutation, including compatibility publication seams, rechecks the current
exact generation and lease; an old nonce or generation can perform no work.

`file_import_job_event` is immutable and records acquired, prepared, indexed,
explicit interruption, reclaim, unchanged, and active transitions. Failure and
reclaim evidence uses fixed categories plus digests; it stores no source
content. Deterministic test interruption is injected only after a committed
boundary and is never auto-retried.

The Organization/Source/Resource ingestion guard serializes initial and changed
classification. A concurrent equivalent winner is re-observed as an auditable
zero-effect no-op. A second worker cannot redeem the same lease. Recovery
reuses the stable Revision and existing rows rather than inserting them again.

Activation revalidates the current ServicePrincipal, acquisition Membership,
body access, complete Fragment/candidate representation, and ordered
publication evidence. Initial activation changes a null active pointer once;
replacement delegates to ADR-0040's previous-to-new compare-and-swap. Therefore
Runtime sees the complete old Revision until the complete recovered Revision is
activated.

## Consequences

- The three named interruption boundaries resume to one active Revision without
  duplicate Resource, Revision, Fragment, candidate, job, or Package evidence.
- A durable checkpoint is retained for later reclaim; pre-checkpoint authority
  or compilation failures remain terminal and content-free.
- Recovery rows and job history are tenant-owned, FORCE-RLS protected, and
  accessible to the worker only through SECURITY DEFINER functions.
- A recovery schema with any non-completed checkpoint or job event attached to
  a non-completed job is intentionally non-downgradable because removing it
  would erase the only resume contract or audit. Completed checkpoint/audit
  metadata may be removed by an explicit schema downgrade because the immutable
  publication lineage remains in the Issue #26 tables.
- Arbitrary instruction-level chaos, batch recovery, delete/tombstone recovery,
  dead-letter handling, and operator remediation remain inactive.

## Revisit trigger

Revisit before automatic retry scheduling, explicit lease release, batch or
delete recovery, checkpoint compaction, dead-letter processing, or physical
cleanup. Any change must preserve exact Organization/job/Revision identity,
higher-generation lease fencing, audit continuity, and all-old/all-new Runtime
visibility.
