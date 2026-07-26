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

Caller-supplied source and resource narrowing is passed through only as an ANN
pre-filter before the candidate limit. It can remove candidates but cannot
grant access; the Kernel independently recomputes the authoritative
`EffectiveScope` intersection and exactly reauthorizes every surviving
`CandidateRef`.

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

An external query-embedding provider is also not eligible for served
composition while Runtime cannot meter and enforce that call against the
effective PackageBudget. The follow-up activation must either add that usage
propagation or explicitly compose only the deterministic network-free twin,
classified as zero external provider calls and zero provider cost. External
provider configuration must fail closed until the metered path exists.

The follow-up served activation must also pass the Kernel-computed
`EffectiveScope` to discovery as a removal-only pre-filter before the ANN
limit. Caller narrowing alone is insufficient when trusted policy operands are
stricter than the RLS-visible set. Every returned candidate must still pass the
Kernel's exact authorization and projection; the pre-filter never grants
authority. Until that propagation exists, the served vector carrier remains
`NOT_ACTIVE`.

## Rationale

Keeping discovery on the retained UserActor transaction preserves the exact
FORCE-RLS and Membership lifetime already established for resolve; a second
connection would require reconstructing trusted context and create a drift-prone
authorization boundary. Strict-order iterative HNSW scanning bounds work while
continuing past rows removed by tenant, lifecycle, ACL, and narrowing filters.
Keeping rank outside the current Kernel preserves ADR-0025's deterministic,
rank-independent assembly until a separately measured authorized-ranking stage
can justify changing the sealed path.

## Consequences

- Query embedding and vector discovery share one explicit profile with Supply.
- FORCE RLS narrows discovery as defense in depth; it never grants authority.
- Filtered HNSW scans can continue within a fixed work ceiling instead of
  silently stopping after the first filtered approximate batch.
- Provider diagnostics, distances, bodies, paths, titles, and denied lineage do
  not enter the candidate output or HTTP error response.
- Current PackageBudget selection remains deterministic and rank-independent;
  semantic ranking quality is not claimed by this slice.
- Served external query embedding remains `NOT_ACTIVE` until its provider call,
  cost, and elapsed usage can be enforced and recorded by Runtime.
- Served vector discovery remains `NOT_ACTIVE` until the Kernel-computed
  EffectiveScope can restrict candidates before the ANN limit without becoming
  an authorization decision at the index.

## Revisit trigger

Revisit after the real golden set and dogfood corpus exist, or before activating
rank-sensitive budget selection. The exit evidence for tuning or replacing the
current strategy includes exact-search recall delta, underfilled-result rate,
`EXPLAIN ANALYZE`, corpus size, filter selectivity, hardware, iterative-scan or
oversampling settings, latency, and cost. Hybrid fusion and content-bearing
reranking remain separate evidence-triggered decisions.

Revisit before any served composition admits an external query-embedding
provider without exact PackageBudget usage propagation.

Revisit before any served composition can limit ANN candidates without first
applying the current Kernel-computed EffectiveScope as a removal-only filter.
