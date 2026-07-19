---
name: adr-0009-pgvector-first-index
version: "1.1.0"
description: >
  Record the V1 retrieval index choice: Postgres FTS + pgvector + deterministic
  fusion, external index engines only when a real corpus violates latency or
  recall SLOs. Use when proposing Elasticsearch/dedicated vector DBs.
---

# 0009. pgvector-first retrieval index

- Status: accepted
- Date: 2026-07-18

## Context

V1 requires lexical and vector candidate recall, deterministic fusion, and exact
authorization before content-bearing rerank, hydration, or assembly. Keeping
candidate indexes beside tenant metadata and authorization truth reduces the
number of publication, backup, deletion, and consistency boundaries. A dedicated
index service would add operational state before a measured corpus demonstrates
that PostgreSQL cannot meet the frozen latency or recall gates.

## Decision

V1 retrieval runs on Postgres FTS + pgvector with deterministic RRF/weighted
fusion. Chinese tokenization and termbase injection are solved inside the
PostgreSQL retrieval implementation and chosen by frozen evaluation. External
index/vector backends are evaluated only when the real corpus measurably
violates latency or recall SLOs. Until a second real implementation exists, the
retrieval boundary is an internal candidate-injection test seam rather than a
public portability contract; a future Interface is extracted from observed
variation instead of forcing PostgreSQL behind a lowest-common-denominator API.

## Consequences

One database to operate, back up, and secure; index rebuilds ride the Revision
publication protocol. The internal test seam preserves an evidence-backed
escape hatch, but V1 does not claim backend interchangeability.
