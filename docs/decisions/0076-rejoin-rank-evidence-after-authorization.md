---
name: adr-0076-rejoin-rank-evidence-after-authorization
version: "1.0.0"
description: >
  Add an authorized ranking stage that joins pre-Kernel rank evidence back onto
  successful projections by exact CandidateRef, while the Kernel itself stays
  rank-blind and rank never becomes part of an authorization grant. Use when
  reranking successful projections with pre-Kernel rank evidence. Not for
  rank-aware authorization or rank evidence that cannot rejoin exactly.
---

# 0076. Rejoin rank evidence after authorization

- Status: accepted
- Date: 2026-07-29
- Refines: ADR-0067, ADR-0075

## Context

ADR-0067 deliberately sorts `CandidateRef`s before authorization so that
candidate order can never influence, encode, or leak an authorization
decision. That protection also discards retrieval rank entirely, which blocks
every rank-sensitive post-authorization stage the ADR-0075 lifts require:
cross-encoder rerank ordering, budget-aware selection, and rank-informed
assembly all need to know how strongly each authorized Article matched the
query. Rank position computed over unauthorized candidates is itself a
side-channel if exposed, so the design must both preserve rank evidence and
keep it out of the authorization boundary.

## Decision

1. Candidate discovery emits, alongside each content-free `CandidateRef`, a
   content-free **rank evidence record** (per-ranker positions/scores and the
   fused rank). Rank evidence carries no content, no ACL claim, and no
   authority.
2. The `AuthorizationKernel` remains rank-blind: its inputs stay sorted
   exactly as ADR-0067 requires, and rank evidence is neither an input to nor
   an output of any authorization decision.
3. A new **authorized ranking stage** runs strictly after projection: it joins
   rank evidence onto successful `AuthorizedProjection`s by exact
   `CandidateRef` equality and orders only what authorization already
   admitted. Refused candidates' rank evidence is discarded unread by
   content-bearing stages and never surfaces in any tenant-visible output,
   audit category, or sufficiency signal.
4. Downstream consumers of order (rerank, selection, budget packing, assembly)
   read rank only from this stage. No stage reorders authorized items by any
   other retrieval-side signal.

## Consequences

- Rank-sensitive reuse from ADR-0075 becomes implementable without weakening
  the ADR-0067 protection; authorization decisions remain reproducible from
  policy state alone.
- The join is exact: a projection without rank evidence (for example, one
  admitted through a non-ranked path) receives a defined neutral rank rather
  than failing the resolve.
- Refusal patterns cannot be inferred from delivered order, because delivered
  order is computed only over admitted items.

## Revisit trigger

Revisit when relevance models begin producing per-Fragment scores inside one
Article, or if any audit shows delivered order correlating with refused
candidates.
