---
name: adr-0018-immutable-revision-publication
version: "1.0.0"
description: >
  Fix the ContextResource to immutable ContextRevision to ContextFragment model
  and atomic active-pointer publication semantics.
---

# 0018. Publish immutable ContextRevisions through one atomic active pointer

- Status: accepted
- Date: 2026-07-19

## Context

Readers must never observe a mixture of old and new `ContextRevision` content
when compilation, indexing, retries, or worker recovery overlap. Mutating
content in place makes that guarantee impossible to audit and couples source
progress metadata to Runtime visibility. A tombstone also needs to stop
visibility without waiting for physical index or blob cleanup.

## Decision

Supply uses this ownership and publication chain:

~~~text
ContextSource -> ContextResource -> immutable ContextRevision -> ContextFragment
                                  prepared -> indexed -> active
~~~

A `ContextResource` is the stable source-object identity. Representation-
affecting content or metadata creates a new immutable `ContextRevision`;
`ContextFragment` values retain that ContextRevision lineage. Compilation and
indexing occur before activation. Publication changes the ContextResource's
active ContextRevision pointer in one PostgreSQL transaction so a reader sees
the complete old ContextRevision or the complete new ContextRevision, never a
hybrid.

Ordinary updates keep the old ContextRevision visible until the new
ContextRevision is fully ready. A ContextResource tombstone stops Runtime
visibility before asynchronous physical cleanup. Access revocation and source
offboarding ordering remain owned by
[ADR-0010](0010-policy-epoch-revocation.md). Acquisition checkpoint, publish
watermark, retry state, and operational metadata never mutate ContextRevision
semantics or substitute for the active pointer.

## Rationale

Immutable versions make retries reproducible and preserve provenance. A single
transactional pointer is the smallest visibility primitive that supports crash
recovery without exposing partially indexed content. Keeping acquisition and
publication progress separate makes operational acceptance distinct from
Runtime visibility.

## Consequences

Updates may temporarily serve an older complete ContextRevision while
compilation or indexing proceeds. Storage cleanup is asynchronous and cannot
define tombstone visibility. Publication and recovery tests must inject
failures at durable boundaries and prove idempotency, monotonic watermarks, and
complete-old-or-new reads.

## Revisit trigger

Reopen only when an implemented source or measured publication workload cannot
meet its requirements with per-ContextResource immutable ContextRevisions and a
transactional active pointer. Any alternative must still prove immutable
provenance, complete-old-or-new reader visibility, immediate ContextResource
tombstone visibility, and idempotent recovery under every durable fault point.
