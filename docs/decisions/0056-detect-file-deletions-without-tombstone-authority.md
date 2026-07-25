---
name: adr-0056-detect-file-deletions-without-tombstone-authority
version: "1.0.0"
description: >
  Detect vanished shallow File paths from one exact complete baseline while
  keeping deletion execution, Runtime visibility, and audience authority closed.
---

# 0056. Detect File deletions without tombstone authority

- Status: accepted
- Date: 2026-07-25
- Refines: ADR-0037, ADR-0043, ADR-0054, ADR-0055

## Context

File v3 pages describe only files present in the current shallow snapshot. An
omitted path is ambiguous: it may be unchanged, never observed, or deleted.
Issue #85 needs durable deletion evidence, but Issue #28 already owns the only
authoritative Resource tombstone transition. Treating a provider omission,
cursor, or checkpoint as tombstone authority would bypass that transition and
its Policy Epoch, cleanup-intent, and Runtime visibility gates.

## Decision

An explicit Control operation advances an active v3 File SourceVersion to the
immutable `file-capabilities-v4` declaration. V4 adds
`deleteObservations=available`; `deletion`, `discover`,
`authorizeAndProject`, recursive discovery, freshness, and consistency remain
unavailable.

`read_file_source_progress` projects a bounded, content-free baseline only from
the latest complete accepted v4 scan for the exact active Organization,
ContextSource, and SourceVersion. It returns the terminal page/checkpoint
identity, its one-level comparison parent, and canonical change envelopes. The
progress row and complete baseline are read through two explicitly `STABLE`
database functions in one PostgreSQL statement snapshot. The migration owns
both volatility declarations and restores the predecessor progress function's
`VOLATILE` declaration on downgrade. The current scan head remains separate,
so an incomplete or concurrently committed scan never tears or replaces the
baseline paired with that progress observation.

`FileChangeProvider.read_changes` takes one stable shallow Markdown snapshot,
builds the whole canonical diff before pagination, and binds the exact baseline
reference into the scan digest and every page proof. Current paths are
`upsert`; a prior baseline `upsert` absent from the snapshot is `delete` with
only the prior path, raw digest, and length. With no complete baseline, no
delete can be emitted. Exact unchanged state replays the original scan and its
original comparison baseline.

One v4 SECURITY DEFINER acceptance function requires the referenced baseline to
be the latest complete page for the same active SourceVersion. Every delete
must exactly match a prior baseline `upsert(path, digest, length)`. It rejects
missing, incomplete, stale, cross-Organization, forged, out-of-order, or
over-limit lineage before a page/checkpoint commit. Accepted v4 pages bind their
baseline in a tenant-owned FORCE-RLS table; page, change, binding, and
checkpoint commit atomically. Its insert guard consumes the trusted transaction
tenant GUC and requires an exact Organization match; row data can neither mint
nor rewrite tenant context. Exact replay is identical.

A page containing any delete is ineligible for
`schedule_file_change_page`. Page acceptance creates no acquisition, import
job, Revision, candidate, ContextRun, tombstone, Policy Epoch update, cleanup
intent, or publish watermark. The previously published Resource remains
visible through the sealed Runtime and generated SDK until a later execution
owner revalidates current deletion lineage and calls the existing Issue #28
tombstone authority.

## Consequences

- Supply can durably distinguish a vanished path from an absent observation.
- Baseline size is server-bounded and cannot be caller-authored or recursively
  expanded.
- Delete observation and deletion execution remain separate capabilities.
- Existing v3 page acceptance and all-upsert scheduling stay unchanged.
- Downgrade restores each active v4 source to its immutable v3 predecessor only
  while no accepted v4 page, acquisition, cleanup, or retained ActionTicket
  lineage exists; otherwise it refuses and requires a forward fix.
- Autonomous polling, retry/reclaim, dead-letter handling, recursive scanning,
  full resync, restore/recreate, and rename correlation remain inactive.

## Revisit trigger

Revisit before any accepted delete can invoke tombstone execution. That owner
must revalidate the latest complete baseline and current source snapshot before
calling the Issue #28 authority; it may not infer audience, reuse an upsert
schedule, or make baseline metadata a Runtime authorization input.
