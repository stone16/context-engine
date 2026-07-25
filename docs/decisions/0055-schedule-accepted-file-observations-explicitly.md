---
name: adr-0055-schedule-accepted-file-observations-explicitly
version: "1.0.0"
description: >
  Bind accepted File observations to existing import jobs without inventing an
  audience or allowing later filesystem bytes to replace checkpoint lineage.
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
exact enabled File import receiver, checks a nonempty contiguous stored `upsert`
set, and creates one existing `file_acquisition` plus one existing
`file_import_job` per change. Exact replay returns the same ordered jobs. Any
partial lineage or changed audience returns no rows and creates no new jobs.

Each scheduled acquisition has an Organization-inclusive foreign key to the
exact accepted observation: SourceVersion, page, ordinal, path, raw SHA-256, and
raw byte length. The existing immutable acquisition trigger prevents lineage
rewrites. Manual imports retain null observation fields and their existing
behavior.

Lease issuance and redemption still use only `file.import`. Redemption exposes
the optional expected raw identity to the worker. After its stable no-follow
read and before Markdown compilation, the worker compares byte length and
SHA-256. A missing or changed file closes the redeemed job as failed when its
exact WorkerLease authority remains current, with no Revision, candidate,
policy, or publication watermark. A later scan may observe the new state; this
job never substitutes it for the accepted observation.

The failure marker remains subject to the existing exact current WorkerLease
authority. If the receiver is revoked after redemption, revocation vetoes even
that state mutation: the job remains content-free `running`, while the worker
still exposes only generic `FileImportUnavailable`. Retry/reclaim policy for
that fenced state remains inactive and must be introduced by a later issue.

## Consequences

- Explicit scheduling reuses the current queue, WorkerLease, publication, and
  acquisition-progress protocols.
- Scheduling a page is all-or-none and exact replay is idempotent.
- Provider checkpoints and worker identity remain non-authoritative for Runtime
  delivery.
- Automatic polling, filesystem watching, deletion execution, retry/reclaim,
  dead-letter handling, and full resync remain inactive.
- Downgrade is refused while any acquisition retains accepted-change lineage.

## Revisit trigger

Revisit before autonomous scheduling, deletion execution, retry/reclaim,
recursive discovery, or full resync. Any revision must preserve explicit
audience authority, exact observation binding, whole-page atomicity, and
pre-compiler byte verification.
