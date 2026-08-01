---
name: adr-0057-execute-current-file-deletes-through-tombstone-authority
version: "1.0.0"
description: >
  Execute one exact current File delete observation through the existing
  tombstone authority while preserving provider and Runtime trust boundaries.
  Use when executing a deletion observed in the current complete File scan. Not
  for provider deletion authority, caller-authored effects, or stale scans.
---

# 0057. Execute current File deletes through tombstone authority

- Status: accepted
- Date: 2026-07-25
- Refines: ADR-0042, ADR-0043, ADR-0054, ADR-0055, ADR-0056

## Context

File v4 can durably prove that one path disappeared from a bounded complete
scan, but ADR-0056 deliberately gives that observation no visibility authority.
The existing tombstone transaction from ADR-0042 is the sole File Resource
visibility transition. Asking an operator to copy the observed path into the
manual tombstone command would turn content-free Supply metadata into
caller-authored effect identity and permit stale scans to delete current state.

## Decision

`ContextControl.execute_file_delete_observation` accepts one non-serializable
trusted command containing only Source, immutable SourceVersion, accepted page
reference, and change ordinal. Organization comes from `TrustedControlCall`.
Path, ResourceRef, event reference, event sequence, cleanup identity, trusted
time, and Policy Epoch are database-derived.

One SECURITY DEFINER PostgreSQL transaction first returns an existing immutable
exact execution binding for replay. Otherwise it takes the Organization File
publication advisory lock and then the per-Source progress advisory lock. This
order matches the existing manual tombstone transaction, whose cleanup trigger
takes the progress lock while the publication lock is still held. The
transaction then revalidates:

1. the exact source remains active on the requested v4 SourceVersion;
2. the ordinal is a persisted `delete` with an exact prior complete-baseline
   binding;
3. the selected page and current durable head share one scan epoch; and
4. the current durable head is the complete terminal page for that scan.

The Resource identity uses the established File identity digest over the exact
Source and persisted canonical path. The event reference is deterministic over
the Organization, Source, SourceVersion, page, and ordinal; its positive event
sequence is the selected accepted-page checkpoint sequence. The wrapper calls
`context_control_tombstone_file_resource` rather than updating Resource or
Policy Epoch state itself. It then inserts one tenant-owned immutable execution
binding with exact Organization-inclusive foreign keys to both the accepted
change and the resulting cleanup intent. Tombstone and binding share the same
transaction, so a binding failure rolls the entire visibility effect back.

Exact replay remains valid after a newer scan or source disablement because the
immutable binding is the already-committed result authority. An unexecuted
incomplete, superseded, stale-version, upsert, missing, unpublished, disabled,
or cross-Organization locator returns generic `SourceNotAvailable` with no
effect.

Provider v4 remains observation-only: `deleteObservations=available` and
`deletion=unavailable`. Execution metadata, scan lineage, and the binding are
never Runtime authorization inputs. Runtime continues to decide delivery only
from current Resource/Revision/policy/audience facts through the sealed
AuthorizationKernel. Consequently the next HTTP/generated-SDK resolve returns
zero deleted Evidence even though stale Revision, Fragment, snapshot, and
candidate rows remain physically present.

## Consequences

- There is one explicit delete per trusted Control operation; there is no
  implicit audience, page batch, polling loop, watcher, or worker operation.
- Existing manual tombstones remain supported and retain sole effect authority.
- Failed exact binding, stale current-head validation, and unavailable Resource
  paths commit no epoch, cleanup, tombstone, or binding mutation.
- Downgrade is permitted only while the execution table is empty. A committed
  tombstone or execution binding is never removed, reversed, or renumbered.
- Autonomous scheduling, retry/reclaim, dead-letter handling, physical cleanup,
  restore/recreate, rename correlation, and full resync remain inactive.

## Revisit trigger

Revisit before batch or autonomous deletion execution, a cleanup consumer,
restore/recreate semantics, or a different lock graph is activated. Any such
change must retain one visibility authority, current-scan revalidation, exact
tenant lineage, and the provider-versus-Runtime trust separation.
