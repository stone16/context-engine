---
name: adr-0066-embed-fragments-before-publication
version: "1.0.0"
description: >
  Persist fixed-dimension pgvector embeddings with newly prepared Fragments
  through an explicit external provider or deterministic CI twin. Use when
  embedding newly prepared Fragments before activation. Not for query-time
  retrieval, backfill authority, implicit twin fallback, or vector authorization.
---

# 0066. Embed Fragments before publication

- Status: accepted
- Date: 2026-07-26
- Refines: ADR-0009, ADR-0037, ADR-0041, ADR-0064

## Context

The first dogfood retrieval slice needs a real vector-bearing corpus before a
vector CandidateIndex can be activated. Fragment rows are immutable once
prepared, and publication recovery must never activate a partially enriched
Revision. Embedding calls are fallible and can be costly, while unchanged File
acquisitions already have an authoritative no-op classification that must not
repeat derived work.

The storage dimension, provider response shape, and migration compatibility are
hard to reverse. Allowing an implicit test provider in production composition
would also turn a network-free twin into accidental product behavior.

## Decision

Supply owns a small batch `EmbeddingProvider` seam. The current schema profile
pins vectors to 384 dimensions in one source constant shared by worker
composition, the deterministic twin, and response validation. The migration
pins the PostgreSQL column to the same value, with a mechanical equality test
preventing drift between the two declarations. Worker composition requires an
explicit provider mode and dimension. A dimension other than the schema profile
is rejected before work begins.

The external adapter sends contextual Fragment text to one environment-derived
HTTPS JSON endpoint. Endpoint, model, API key, timeout, and dimension enter only
through worker environment configuration. A required 1–256 batch-size setting
bounds each request while the adapter reassembles validated batches in original
Fragment order. Secret-bearing values are excluded from representations, and
transport, status, parsing, ordering, count,
dimension, non-finite, and float32-zero-vector failures collapse to one
content-free unavailability category. Provider values are normalized to the
same IEEE-754 float32 representation pgvector persists before the nonzero check.

The CI and integration twin is network-free. It derives a normalized vector
from a domain-separated SHAKE-256 stream over exact contextual text, giving
stable content-derived values without claiming semantic quality.

File publication first performs the existing acquisition and unchanged-content
classification. Only an `acquired` new or replacement Revision calls the
provider. Its complete validated embedding document enters the same transaction
that inserts immutable Fragment rows and advances `acquired -> prepared`.
Provider failure records the existing acquired-boundary interruption, leaving no
Revision or Fragment rows and allowing the bounded lease-reclaim path to retry.
Recovery from `prepared` or `ready` reuses stored vectors and does not call the
provider again. Indexing and activation both reject any current Revision with a
missing or wrong-dimension vector.

During a rolling schema change, the worker detects the installed prepare
signature: it keeps the pre-embedding publication contract while the database
is at the predecessor revision and uses the vector-bearing contract only after
0036 is active. The upgrade takes exclusive locks and rewinds any inactive,
pre-0036 `prepared` or `ready` Revision to `acquired`, removing only its staged
derived rows. The next bounded recovery lease therefore re-embeds through the
configured provider before activation; migration code never fabricates vectors.

`context_fragment.embedding` is nullable only for historical rows because this
slice deliberately adds no backfill authority. New publication requires a
vector. One partial HNSW cosine index covers embedded rows. It narrows future
candidate discovery only and never participates in authorization.

## Consequences

- Unchanged acquisitions perform zero embedding calls and keep their current
  active lineage.
- A newly active Revision always has one validated vector per Fragment.
- Historical rows remain readable and are embedded only by re-importing them as
  a new immutable Revision.
- CI has no embedding network egress; production has no implicit twin fallback.
- The migration downgrade takes an exclusive Fragment-table lock, removes only
  the derived vectors, and preserves immutable Fragment content and lineage.
- Runtime, AuthorizationKernel, projection, and served composition do not
  change in this slice.

## Revisit trigger

Revisit before changing the dimension or embedding input profile, adding model
version lineage, backfill authority, multiple embedding profiles, query-time
embedding, or a non-PostgreSQL vector store. Query-time candidate discovery and
its recall/latency evidence belong to the next vector CandidateIndex decision.
