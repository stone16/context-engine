---
name: adr-0055-schedule-accepted-file-observations-explicitly
version: "1.0.0"
description: >
  Bind accepted File observations to existing import jobs without inventing an
  audience or allowing later filesystem bytes to replace checkpoint lineage.
  Use when scheduling accepted File observations for import. Not for inventing
  audience authority or substituting later bytes for accepted lineage.
---

# 0055. Schedule accepted File observations explicitly

- Status: accepted
- Date: 2026-07-25
- Refines: ADR-0037, ADR-0043, ADR-0054

## Context

Issue #81 made File change pages durable but intentionally created no jobs: a
content-free filesystem observation has no delivery audience. Issue #83 needs a
narrow bridge from an already accepted page to the existing File import worker
without turning provider metadata, worker identity, or an earlier manual import
into delivery authority.

The filesystem can also change after Control accepts a page. Publishing the
later bytes under the earlier page checkpoint would make the accepted digest
decorative and corrupt acquisition provenance.

## Decision

`ContextControl.schedule_file_change_page` is an explicit trusted operation. It
requires the exact active v3 Organization, Source, SourceVersion, accepted page
reference, and an explicit existing `FileImportAudience`. Page acceptance
continues to create no job and never infers an audience.

One SECURITY DEFINER transaction acquires the existing per-Source progress
advisory lock before the active Source row lock, matching page acceptance and
checkpoint-trigger lock order. It validates the current Membership/version and
exact enabled File import receiver, requires the page to belong to the current
accepted scan epoch before creating its first lineage, checks a nonempty
contiguous stored `upsert` set, and creates one existing `file_acquisition` plus
one existing
`file_import_job` per change. Exact replay returns the same ordered jobs. Any
partial lineage or changed audience returns no rows and creates no new jobs.

Each scheduled acquisition has an Organization-inclusive foreign key to the
exact accepted observation: SourceVersion, page, ordinal, path, raw SHA-256, and
raw byte length. The existing immutable acquisition trigger prevents lineage
rewrites. Manual imports retain null observation fields and their existing
behavior.

Lease issuance and redemption still use only `file.import`. Redemption exposes
the optional expected raw identity to the worker. A non-locking read of immutable
acquisition lineage first distinguishes manual from scheduled work. Before a
scheduled lease can lock its job row or enter `running`, redemption takes the
same per-Source progress lock and requires its accepted page's scan epoch to
remain the latest accepted scan epoch. This preserves the progress-before-Source
and publication-before-Source-before-job ordering used by page acceptance and
offboarding; manual imports acquire no progress lock and retain their prior lock
path. After waiting for the progress fence, redemption refreshes trusted
database time and revalidates expiry before touching the job row. An expired or
superseded job therefore reads no content. After a successful stable
no-follow read and before Markdown compilation, the worker compares byte length
and SHA-256. A missing or changed file closes the redeemed job as failed when
its exact WorkerLease authority remains current, with no Revision, candidate,
policy, or publication watermark. A later scan may observe the new state; this
job never substitutes it for the accepted observation.

Supersession can still race after redemption. Every successful import outcome
therefore crosses a second database fence in the transaction that appends its
publish watermark. That fence takes the same progress lock and rechecks the
scheduled page epoch. If the epoch changed during read, compilation, or staged
publication, PostgreSQL rolls back the active pointer/result, active event, and
watermark together. Manual imports and tombstones retain their existing
behavior.

The failure marker remains subject to the existing exact current WorkerLease
authority. If the receiver is revoked after redemption, revocation vetoes even
that state mutation: the job remains content-free `running`, while the worker
still exposes only generic `FileImportUnavailable`. Retry/reclaim policy for
that fenced state remains inactive and must be introduced by a later issue.

## Consequences

- Explicit scheduling reuses the current queue, WorkerLease, publication, and
  acquisition-progress protocols.
- Scheduling a page is all-or-none and exact replay is idempotent.
- Superseded scheduled jobs are retained for audit/replay but can neither read
  content nor commit a visible publication.
- Provider checkpoints and worker identity remain non-authoritative for Runtime
  delivery.
- Automatic polling, filesystem watching, deletion execution, retry/reclaim,
  dead-letter handling, and full resync remain inactive.
- Downgrade is refused while any acquisition retains accepted-change lineage.
- Downgrade also refuses any retained manual acquisition whose path is valid in
  this revision but invalid under the preceding schema. The current boundary is
  the case-insensitive exact basename `.md`; refusal preserves the acquisition
  and requires a forward fix instead of deleting or rewriting an existing job.
- Downgrade serializes all `file_acquisition` writers before one database
  snapshot evaluates both rollback blockers, closing check-to-DDL races for
  accepted lineage and newly valid manual paths.

## Revisit trigger

Revisit before autonomous scheduling, deletion execution, retry/reclaim,
recursive discovery, or full resync. Any revision must preserve explicit
audience authority, exact observation binding, whole-page atomicity, and
pre-compiler byte verification.
