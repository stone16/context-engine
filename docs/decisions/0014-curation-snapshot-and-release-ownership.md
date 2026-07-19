---
name: adr-0014-curation-snapshot-and-release-ownership
version: "1.0.1"
description: >
  Publish post-Revision curation through its own immutable snapshot and give
  ReleaseManifest promotion one Module owner.
---

# 0014. Curation has an independent snapshot and release owner

- Status: accepted
- Date: 2026-07-18

## Context

Curation consumes an already active immutable Revision, so accepted annotations
cannot be published atomically as a mutation of that Revision. Giving both
ContextControl and ContextLearning an activation operation would create two
production publication authorities. Bundling content, index, runtime, and
curation parameters into one profile would also couple changes that have
different rebuild and rollback costs.

## Decision

Curation runs after Revision activation and assembles audited annotations into
an immutable CurationSnapshot with explicit compatibility references to
Revision ids. Its active selection is independent of the content Revision
pointer but changes only through ContextLearning.promote as part of an active
ReleaseManifest; the curation pipeline has no direct activation operation.
Runtime reads active Revision and the compatible CurationSnapshot selected by
that manifest in one database snapshot. Missing or failed curation is normal
retrieval without curation.

Profiles are internally split into immutable ContentProfile, IndexProfile,
RuntimeProfile, and CurationProfile references composed by ReleaseManifest.
The CurationProfile contains an optional CurationSnapshotRef, compatible
Revision set, and evaluation digest, so the active manifest selects one
snapshot or explicitly selects curation-off.
ContextLearning evaluates ReleaseCandidate and is the only Module that can
promote ReleaseManifest. ContextControl owns source, access, and policy
governance but cannot publish a profile.

Supply, Runtime, and Learning exchange persisted records and outbox events; they
do not import each other cyclically.

## Consequences

Curation no longer mutates immutable Revision or blocks content publication. It
runs as the parallel C1 experiment after retrieval/eval baseline and does not
block the design-partner opening gate. Promotion needs registered sample size,
per-kind thresholds, uncertainty reporting, and frozen on/off evidence.
