---
name: adr-0058-schedule-only-upserts-from-mixed-file-pages
version: "1.0.0"
description: >
  Reuse explicit File page scheduling for the upsert projection of one current
  mixed page without granting the scheduler delete or tombstone authority.
---

# 0058. Schedule only upserts from mixed File pages

- Status: accepted
- Date: 2026-07-25
- Refines: ADR-0055, ADR-0056, ADR-0057

## Context

ADR-0056 introduced deterministic v4 File pages containing `upsert` and
`delete` observations. Its safety boundary made ADR-0055 reject a whole page if
any delete was present. ADR-0057 subsequently gave a trusted operator a
separate exact operation for one current delete observation, but it did not
make the page scheduler a deletion authority.

Retaining the whole-page refusal now strands valid upserts that share a current
page with a delete. Splitting or rewriting the accepted page would break its
provider proof, checkpoint lineage, canonical ordering, and exact observation
foreign keys. Adding another queue or scheduler would duplicate the existing
File acquisition, WorkerLease, and publication protocol.

## Decision

`ContextControl.schedule_file_change_page` continues to accept the same exact
page locator and explicit `FileImportAudience`. Its SECURITY DEFINER transaction
first validates the complete stored shape: total row count, bounded contiguous
original ordinals, allowed closed change kinds, current Source/SourceVersion,
latest accepted scan epoch, current audience Membership, and exact enabled File
import receiver. A page containing any delete must also be terminal (`complete =
true`) before scheduling; the established v3/v4 all-upsert nonterminal behavior
remains compatible.

After whole-page validation, the transaction derives a nonempty `upsert`
projection. Its all-or-none existing-lineage count, insertion, exact replay
validation, and returned rows apply only to that projection. Each acquisition
retains the original page ordinal, even when intervening deletes make the result
ordinals non-contiguous. The closed result contract therefore requires a
nonempty sequence of strictly increasing unique ordinals rather than the
earlier `1..N` result-local contiguity. Page storage remains contiguous.

A delete-only page returns the same generic unavailable outcome and creates no
job. Delete observations never create acquisitions, never enter `file.import`,
and never invoke the tombstone function. They remain executable only through
ADR-0057's exact current-observation operation. Existing v3/v4 all-upsert
scheduling, raw-byte verification, WorkerLease redemption, and current-scan
publication fencing are unchanged.

Exact replay remains available for an already scheduled projection after the
page ceases to be current, matching ADR-0055. First scheduling still requires
the latest accepted scan epoch. A partial retained projection is generic
unavailable rather than completed or repaired, preserving all-or-none caller
semantics and making inconsistent lineage an operator-visible failure.

## Consequences

- One accepted page remains the immutable observation and checkpoint unit.
- One existing acquisition/job pair is created for every upsert and for no
  delete, preserving original ordinals and observation identities.
- The explicit audience, receiver, WorkerLease, byte-drift, and publication
  fences remain the only active import path.
- Scheduling has zero Policy Epoch, tombstone, cleanup, or Runtime authority;
  #87 remains the sole delete-execution carrier added after manual tombstone.
- Empty rollback restores whole mixed-page refusal only when no acquisition is
  retained from a mixed page. Scheduler calls take a shared transaction-scoped
  migration advisory fence before their per-Source lock; rollback takes the
  matching exclusive fence before table locks, checking that condition, and
  replacing the function. An old compiled call that waited behind rollback
  also exercises a definer-only generation-read capability before any Source
  work; rollback atomically revokes that capability while removing the restored
  prior function's need for it. Later forward migrations preserve the
  capability, so advancing the global Alembic revision does not stop this
  scheduler.
  Retained mixed scheduling lineage requires a forward fix and is never deleted
  or rewritten by downgrade.
- Autonomous polling, automatic upsert/delete ordering, batch deletion,
  retry/reclaim, dead-letter handling, full resync, and recursive discovery
  remain inactive.

## Revisit trigger

Revisit before autonomous scheduling or any policy that orders upsert
publication relative to delete execution. Such a change must define crash,
retry, restore/recreate, and partial-failure semantics without merging import
and tombstone authority or making Runtime trust Supply progress.
