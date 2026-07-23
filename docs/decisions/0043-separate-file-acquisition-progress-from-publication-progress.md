---
name: adr-0043-separate-file-acquisition-progress-from-publication-progress
version: "1.0.0"
description: >
  Persist source-scoped accepted-change checkpoints separately from contiguous
  Runtime-visibility publication watermarks.
---

# 0043. Separate File acquisition progress from publication progress

- Status: accepted
- Date: 2026-07-23
- Refines: ADR-0037, ADR-0039, ADR-0040, ADR-0041, ADR-0042

## Context

A durable File import request proves that a source change was accepted, but it
does not prove that compilation, indexing, or publication completed. Treating
the import job state as one generic checkpoint makes operators unable to tell a
recoverable publication pause from completed Runtime visibility. It also makes
an out-of-order later completion capable of hiding an earlier failed change if
the source reports only its greatest completed ordinal.

Issue #29 needs two source-level progress signals without turning either signal
into Runtime authorization or claiming the deferred provider `checkpoint`
operation from the standard ProviderPort.

## Decision

Each accepted File import job and trusted File tombstone receives one immutable,
database-ordered `file_source_acquisition_checkpoint`, scoped by Organization
and Source. The import checkpoint is appended in the same transaction that
creates its acquisition/job; the tombstone checkpoint is appended in the same
transaction as tombstone visibility and cleanup intent. Opaque references are
deterministic SHA-256-derived tokens, while positive sequence numbers remain
database-owned ordering facts. Idempotent replay does not append another row.

Each unchanged result, active Revision event, and tombstone appends one distinct
immutable `file_source_publish_watermark` row at its existing visibility
transaction boundary. Prepared, indexed, claimed, interrupted, or failed state
cannot create that row. Exact foreign keys bind each completion to its accepted
checkpoint, durable job or cleanup intent, Resource, and Revision.

The read-only `ContextControl.read_file_source_progress` seam returns the latest
accepted checkpoint and the highest *contiguous* completed sequence. If change
3 completes while change 2 is paused, completion 3 remains durably recorded but
the exposed watermark remains 1. When recovery completes change 2, the exposed
watermark becomes 3 without rewriting either append-only stream. Calls are
operation-bound and Organization-scoped through a SECURITY DEFINER function;
all application roles are denied direct table access.

Runtime receives no table privilege or dependency on this status. Current
Resource state, active Revision, policy, audience, and AuthorizationKernel
checks remain the only delivery authority. The File capability manifest also
continues to report the standard provider cursor/checkpoint operations as
unavailable: this issue activates operational progress for the manual File
carrier, not provider `readChanges` acknowledgement.

## Rationale

Acceptance and visibility have different transaction boundaries and different
failure meanings, so one mutable cursor cannot truthfully represent both. Two
append-only streams retain the causal history needed to diagnose a pause,
while exposing only the contiguous publication prefix prevents an
out-of-order completion from overstating Runtime freshness. Database-owned
ordering, deterministic opaque references, and exact durable-lineage foreign
keys make each accepted ordering fact stable without promoting progress
metadata into delivery authority. Existing durable lineage is ordered once
during the initial upgrade; after any progress exists, downgrade is refused so
concurrent online lock order can never be reconstructed differently or accept
new changes through an older migration boundary.

Keeping the operator read seam in ContextControl also preserves the existing
ProviderPort contract: File progress can be observed now without pretending a
provider-native change cursor has been acknowledged or making Runtime depend
on an operational status surface.

## Consequences

- An accepted change remains observable through compilation, indexing,
  interruption, failure, and recovery without falsely claiming visibility.
- First publication, replacement, unchanged classification, and tombstone each
  advance publication progress only inside their committed visibility outcome.
- Concurrent or out-of-order completion cannot skip an earlier gap; recovery
  closes the gap and deterministically catches the watermark up.
- Both streams are append-only, forced-RLS tenant tables with no source content.
  Downgrade is allowed only while both streams are empty; after any accepted
  change, a forward fix is required so sequence numbers and opaque references
  cannot be renumbered from historical timestamps.
- Cross-source aggregation, UI, provider cursor formats, resync, and watermark-
  based authorization remain inactive.

## Revisit trigger

Revisit before provider-native `readChanges`/checkpoint activation, source
offboarding, resync, progress compaction, cross-source aggregation, or deletion
recovery. Any change must preserve separate signals, contiguous watermarks,
exact tenant lineage, replay idempotency, and Runtime independence.
