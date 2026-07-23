---
name: adr-0042-tombstone-file-resources-before-cleanup
version: "1.0.0"
description: >
  Make one published File Resource synchronously ineligible, advance its
  Organization Policy Epoch, and retain a durable asynchronous-cleanup intent.
---

# 0042. Tombstone File Resources before cleanup

- Status: accepted
- Date: 2026-07-23
- Refines: ADR-0010, ADR-0018, ADR-0027, ADR-0037, ADR-0040

## Context

Deleting source bytes and index rows is a slow, fallible maintenance process.
If Runtime visibility depends on that cleanup, a removed File can remain
deliverable through stale candidates or retained Revision rows. Conversely,
physically deleting immutable lineage in the synchronous Control request would
couple authorization safety to an unbounded cleanup workload and erase the
evidence needed to finish or audit that work.

Issue #28 needs one manual deletion carrier for a known published File
Resource. Native File watching, restore, source offboarding, and physical
garbage collection are separate capabilities.

## Decision

`ContextControl.tombstone_file_resource` accepts one operation-bound trusted
Control call plus exact Source, stable File Resource, event reference, and
positive event sequence. Organization, cleanup-intent identity, trusted time,
and the next Policy Epoch are not caller supplied.

One SECURITY DEFINER PostgreSQL transaction takes the same Organization
publication advisory lock used by Runtime and activation. It locks the current
Organization epoch and active Resource, changes only `ContextResource.tombstoned`
to true, increments the epoch once, and inserts one immutable
`file_resource_cleanup_intent` bound to the still-retained active Revision.
Failure exposes none of those writes.

The Resource active pointer and every physical Revision, Fragment, snapshot,
candidate, and publication record remain present. Existing forced-RLS
predicates make the tombstoned Resource, active Revision, and Fragments
invisible to Runtime. A stale `CandidateRef` may still be discovered, but exact
authorization/projection returns no content. The HTTP result is therefore the
same canonical empty Package as an unknown Resource.

The first accepted event is terminal for this no-restore slice. A replay or an
older event for the same Organization/Source/Resource returns the original
database-authored tombstone result without another epoch bump or cleanup
intent. Event references are Organization-scoped: reusing one for any different
target, including a target under another Source in that Organization, is
refused. No event can make the Resource visible again.

The cleanup intent is pending and append-only. Control, Runtime, and Worker
roles have no direct table access; the Control role can create it only through
the tombstone function. This issue activates durable cleanup intent, not its
consumer or physical completion.

## Rationale

Visibility belongs to the small synchronous transaction because it is the
security-critical effect; bulk deletion belongs to a durable later workflow
because its latency and retries must not extend the authorization window. The
existing access-policy definer is reused narrowly because it already owns
Policy Epoch mutation and cannot log in. A new process or second publication
authority would add no isolation benefit for this single transaction.

## Consequences

- The first resolve beginning after commit delivers zero bytes and Evidence,
  even while all stale physical and index rows remain.
- Deletion and ordinary publication/replacement share one visibility lock, so
  neither can cross the other's commit boundary with a mixed view.
- Policy Epoch invalidates decisions made before deletion; the exact
  tombstone checks remain the authorization source for later reads.
- A committed cleanup obligation intentionally prevents schema downgrade until
  an explicit operator process has dealt with the retained lineage.
- File capability declarations still do not claim native delete observation:
  the active carrier is a trusted manual Control command.

## Revisit trigger

Revisit before adding File watchers, restore, source offboarding, cleanup
workers, retry/dead-letter policy, retention expiry, or provider-native delete
cursors. Those additions must preserve terminal monotonic visibility,
Organization ownership, non-enumeration, and exact durable cleanup lineage.
