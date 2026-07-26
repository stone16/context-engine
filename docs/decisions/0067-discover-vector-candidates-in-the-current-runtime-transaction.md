---
name: adr-0067-discover-vector-candidates-in-the-current-runtime-transaction
version: "1.0.0"
description: >
  Embed Acquire queries with the publication-compatible profile and discover
  bounded content-free pgvector candidates in the retained UserActor transaction.
---

# 0067. Discover vector candidates in the current Runtime transaction

- Status: accepted
- Date: 2026-07-27
- Refines: ADR-0009, ADR-0025, ADR-0066

## Context

ADR-0066 published fixed-dimension Fragment vectors and reserved query-time
embedding, compatibility, and filtered-ANN evidence for the first vector
CandidateIndex decision. Runtime already owns one current UserActor transaction
whose transaction-local settings drive FORCE RLS and whose nominal projection
session remains live through the sealed AuthorizationKernel. Opening a second
database session for retrieval would detach discovery from that trusted context.

HNSW applies post-scan filters, including RLS and active-resource predicates.
With selective Organization, Membership, source, or policy filters, a bounded
one-pass approximate scan can underfill even when visible rows exist. Candidate
ordering cannot be authorization input, and ADR-0025 deliberately makes current
deterministic assembly independent of untrusted candidate order.

## Decision

`PostgreSQLVectorCandidateIndex` uses the explicit `EmbeddingProvider` seam and
requires exact compatibility with the stored 384-dimensional profile. Each
`Acquire` embeds only its exact query, validates the provider response through
the same float32 contract used by publication, and collapses provider failure
to one content-free availability category. HTTP maps that category to the
existing generic service-unavailable response.

The index calls a narrow vector-discovery operation on the current
`MaterializedProjectionSession`. Its PostgreSQL port executes on the retained
UserActor connection; it opens no connection and creates no transaction. The
query selects only Organization/source/resource/revision/fragment lineage,
orders by cosine distance with `<=>`, and applies an exact 1–64 limit. Body,
path, title, provider metadata, and distance score never leave persistence.
Every result remains an untrusted `CandidateRef` and still passes the unchanged
AuthorizationKernel before projection.

Before the ANN query, the port sets pgvector `hnsw.iterative_scan` to strict
order and a bounded maximum scan-tuple ceiling with transaction-local settings.
This allows pgvector to continue scanning after RLS and live-resource filters
remove approximate neighbors while retaining bounded work and exact distance
order at the index seam. The pinned harness uses the pgvector version recorded
by the served database topology.

Candidate rank is intentionally not preserved through the current Kernel.
ADR-0025 makes assembly independent of candidate order so index rank cannot
affect access or the deterministic Package. This slice therefore provides
bounded vector recall, not relevance-ranked budget selection. Changing that
behavior requires a separately designed authorized ranking stage and is not
smuggled into this product-lane index change.

The served composition does not activate the index here. Its default remains
reject-all and reports Runtime delivery as not active.

## Consequences

- Query embedding and vector discovery share one explicit profile with Supply.
- FORCE RLS narrows discovery as defense in depth; it never grants authority.
- Filtered HNSW scans can continue within a fixed work ceiling instead of
  silently stopping after the first filtered approximate batch.
- Provider diagnostics, distances, bodies, paths, titles, and denied lineage do
  not enter the candidate output or HTTP error response.
- Current PackageBudget selection remains deterministic and rank-independent;
  semantic ranking quality is not claimed by this slice.

## Revisit trigger

Revisit after the real golden set and dogfood corpus exist, or before activating
rank-sensitive budget selection. The exit evidence for tuning or replacing the
current strategy includes exact-search recall delta, underfilled-result rate,
`EXPLAIN ANALYZE`, corpus size, filter selectivity, hardware, iterative-scan or
oversampling settings, latency, and cost. Hybrid fusion and content-bearing
reranking remain separate evidence-triggered decisions.
