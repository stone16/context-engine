---
name: adr-0099-select-live-surfaces-as-the-first-structured-acquisition-workload
version: "1.0.0"
description: >
  Select bounded live collaboration-surface reads as the first structured
  acquisition workload while retaining the sealed Runtime and Package
  boundary. Use when designing request-time surface context acquisition. Not
  for snapshotting live results, direct surface-to-model delivery, or activating
  a carrier before the structured family contract exists.
---

# 0099. Select live surfaces as the first structured acquisition workload

- Status: accepted
- Date: 2026-08-02
- Refines: ADR-0012, ADR-0013, ADR-0017, ADR-0031, ADR-0061, ADR-0062

## Context

ADR-0061 preserves a structured acquisition family for request-time database
and API context but intentionally leaves its terms and carrier undefined until
a real workload appears. Collaboration surfaces now provide that pull: an agent
needs a bounded view of current messages from an exact conversation while
answering one current question. The useful workflow is request, source-side
fulfilment, bounded result, timeout or refusal. It has no natural immutable
`ContextRevision`, active publication pointer, tombstone, or ingestion
checkpoint.

The knowledge-snapshot Runtime cannot simply accept surface message bodies as
`ContextFragment` values. Nor may a surface plugin hand trusted identity,
audience, or already-authorized content directly to the model. The structured
family needs its own source registration, acquisition evidence, freshness,
field projection, and Evidence terms before a carrier can join the existing
Package contract.

## Decision

1. The first structured acquisition design workload is a bounded read of text
   messages from one exact current collaboration conversation for one current
   `Acquire`. Attachment bytes, recursive history, background mirroring,
   cross-surface federation, write effects, and public-group delivery are
   deferred from the first slice.
2. The caller does not receive a separate surface-context bypass. It submits the
   existing closed `Acquire` shape through authenticated HTTP/generated SDK. In
   the first private-conversation slice, the exact current conversation comes
   only from the destination binding redeemed into `TrustedDeliveryContext`;
   the request body supplies only its existing `ContextNeed` and optional
   snapshot-family narrowing. Existing source/resource refs retain only their
   snapshot meaning and neither select nor widen the live target. The caller
   cannot select a conversation or supply trusted Organization, viewer,
   membership, audience, purpose, source ACL, provider epoch, or final scope. A
   missing or non-private trusted destination makes the live branch unavailable
   before source I/O. A server-owned plan selects the registered structured
   acquisition carrier.
3. Reading an explicit conversation other than that current trusted destination
   requires a reviewed new public contract version with a nominal structured-
   target narrowing field. Such a field may only narrow server-derived target
   capabilities and cannot establish viewer, audience, or source authority.
   OpenAPI v0 and its frozen `Acquire` bytes remain unchanged by this decision.
4. Behind Runtime, the carrier follows one bounded
   `request -> fulfil -> terminal outcome` protocol. The future profile fixes
   exact allowed target and query fields, message/byte/page ceilings, total
   latency, cancellation, ordering, duplicate handling, and freshness. A
   required live branch that times out, returns a partial page, loses current
   source authority, or cannot prove its terminal bounds yields one generic
   unavailable outcome; it does not silently deliver a partial Package or fall
   back to mutable memory.
5. Source access uses a distinct source-read authority and a current source-side
   check for the exact viewer and conversation. Results bind the registered
   source, acquisition request, exact target, source observation time and epoch,
   current access evidence, field projection, ordering, and expiry. A failed
   Live check never degrades to Mirrored or Weak evidence. Cache, message mirror,
   search index, target id, and source visibility filter are never final
   authorization.
6. Content stays unavailable to rerank, relevance, assembly, model egress, and
   tenant-visible tracing until the structured-family equivalent of the
   content-free candidate and exact authorized projection has passed the one
   sealed `AuthorizationKernel`. Both context families must then close into one
   evidence-backed, audience-bound, expiring `ContextPackage` with one shared
   PackageBudget and final Policy Epoch/egress veto.
7. Live results are request-scoped acquisition output. They are not stored as
   `ContextSource -> ContextResource -> ContextRevision -> ContextFragment`, do
   not enter the active snapshot corpus or index, and do not become QM or other
   consumer memory. `ContextRun` may retain only the future structured family's
   authorized digest lineage; raw messages, queries, denied rows, pre-auth
   counts, target names, and scores remain outside its current digest-only
   retention surface.
8. Before implementation, a refining ADR and `CONTEXT.md` update must define the
   structured source registration, acquisition request/result, authorization
   evidence, freshness, authorized projection, Evidence composition, retention,
   and closed failure vocabulary. Contract, deterministic provider twin, real
   source conformance, PostgreSQL/RLS evidence, and mixed-family Package tests
   must exist before a live carrier can be marked active.

## Rationale

A live collaboration surface is narrow enough to falsify the structured-family
design and common enough to justify it. Keeping the request behind `Acquire`
preserves one model-caller contract. Refusing to reuse snapshot lifecycle terms
protects the guarantees those terms already carry, while requiring the same
Kernel and Package termination preserves ContextEngine's differentiating
security boundary.

## Consequences

- ADR-0061's real-workload revisit trigger is satisfied for design ordering,
  not for implementation activation.
- The next structured-acquisition issue starts with terminology and an offline
  deterministic surface twin, then proves a real source. It does not begin by
  wiring a plugin response into model context.
- The maintainer-local
  [`QM evaluation`](../research/2026-08-02-qm-blueprint-evaluation.md) remains
  implementation research, not public provenance or a security authority.
- Structured acquisition, live surface reads, attachment retrieval, shared-room
  delivery, and any surface effect remain `NOT_ACTIVE`.

## Revisit trigger

Revisit the first-slice bounds when a real private text-message workload cannot
be served without attachments, paging, or a wider target; revisit the no-partial
rule only with a package-level semantics that cannot disguise missing required
context. Any revision must preserve acquisition-time source authorization,
non-enumeration, the sealed Kernel, and one final ContextPackage.
