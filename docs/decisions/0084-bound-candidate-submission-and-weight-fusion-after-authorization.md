---
name: adr-0084-bound-candidate-submission-and-weight-fusion-after-authorization
version: "1.0.0"
description: >
  Bound both directions of the replaceable candidate-discovery seam with one
  server-owned ceiling, and place retrieval fusion weighting in the
  post-authorization ranking stage. Use when implementing or replacing a
  CandidateIndex or a multi-ranker fusion policy. Not for changing
  authorization, projection, or PackageBudget semantics.
---

# 0084. Bound candidate submission and weight fusion after authorization

- Status: accepted
- Date: 2026-07-30
- Refines: ADR-0067, ADR-0075, ADR-0076, ADR-0081

## Context

ADR-0081 sealed the replaceable `CandidateIndex` behind a data-only discovery
session, so pre-Kernel code can no longer reach a content-bearing port. It did
not bound the two list sizes that seam still controls.

An independent evaluation of that seam submitted 5001 `CandidateRef`s the
database had never discovered and observed 5001 `locate()` round trips inside a
single online resolve. Nothing was disclosed — `locate()` returns content-free
lineage, FORCE RLS still applied, every fabricated ref was refused, and the
response was byte-identical to the empty case — but one replaceable component
turned one request into unbounded database work. The symmetric hole is on the
inbound side: the index also authors the prepared discovery request, and its
`limit` was validated only as "positive", so the same seam could ask the trusted
transaction for an arbitrarily large result set.

The asymmetry is the tell. Every comparable list in the codebase is bounded by
the server at its own seam: vector discovery post-checks `request.limit`,
fragment expansion caps at 64 candidates, and a fragment window spans at most 32
in each direction. The one list authored by the replaceable seam had no cap.

Exact-phrase discovery is the deliberate exception. ADR-0067's implementation
returns every matching Fragment because hiding the sixty-fifth exact match is a
recall defect in a precision lookup, and an integration test added with the seam
itself pins that. A bound there would have to truncate, so the trusted
exact-phrase read stays complete and the bound sits at the seam instead.

A second, independent question blocks issue #148. ADR-0076 requires delivered
order to be computed only over candidates authorization already admitted, so the
authorized ranking stage recomputes ranker positions across admitted candidates
and discards the pre-Kernel fused rank. Pre-Kernel order is therefore provably
inert: ADR-0067 canonically sorts refs before authorization, and the Kernel
authorizes every submitted candidate, so no pre-Kernel ordering decision can
change which candidates are admitted or in what order they are delivered.
Weighted RRF placed before the Kernel would compute a number nothing reads.

## Decision

1. Candidate submission is bounded by a **server-owned seam-local ceiling**, not
   by a `PackageBudget` dimension. `MAX_CANDIDATE_SUBMISSION_CEILING` is the hard
   server ceiling; `Runtime` accepts a `candidate_submission_limit` at or below
   it, validated at configuration time, rejecting zero, negative, non-exact, and
   above-ceiling values.
2. The bound is enforced in both directions of the seam, before any content or
   lineage work. Runtime refuses a prepared discovery request whose limit exceeds
   the configured bound before the trusted transaction executes it, and refuses a
   submitted `CandidateQuery` that exceeds the configured candidate bound or the
   ranker-list bound before the first `locate()`. Both refusals are content-free,
   carry no candidate identity or count, and produce no Package.
3. Runtime re-derives the submitted query's own contract rather than trusting it.
   A hostile index that skips `__post_init__` through `object.__new__` is bounded
   by the same check.
4. A prepared discovery request that carries a limit is bounded by the same
   ceiling, and its result is post-checked against that limit. Exact-phrase
   discovery carries no limit and is never truncated; a corpus whose own exact
   matches exceed the configured bound refuses the resolve rather than silently
   delivering a subset.
5. Retrieval fusion **weighting lives in the post-authorization ranking stage**
   and nowhere earlier. `join_authorized_ranking` accepts server-owned
   `ranker_weights` covering the admitted rankers and fuses over positions
   compacted across admitted candidates only; omitted weights fuse uniformly.
   Weights are server configuration, never a request field and never caller
   supplied. The pre-Kernel stage keeps exact-ref dedupe and content-free
   per-ranker evidence carriage; its provisional fused rank remains inert.

## Rationale

`PackageBudget` is the wrong home for the bound on three counts. It is
caller-facing — `effective_package_budget` intersects it with a caller-supplied
`PackageBudgetRequest` — and a defense against an untrusted seam must not be a
value the other untrusted party tunes. Its dimensions all describe the delivered
Package's resource envelope and are reported back in `BudgetUsage`, so a
submission count would publish a retrieval-side quantity that ADR-0076
deliberately keeps out of tenant-visible output. And locality is the dominant
codebase pattern: every existing comparable bound is server-owned and enforced at
the seam that produces the list.

Weighting after authorization is the only placement that can see what it needs.
Only the post-Kernel stage knows which candidates were admitted, and only
positions recomputed over admitted candidates can order delivery without letting
refused candidates influence it. The Kernel stays rank-blind because weights are
neither an input to nor an output of any authorization decision, and because
server-owned constants are identical for every principal and therefore leak
nothing.

## Consequences

- One hostile or buggy ranker can no longer convert a single resolve into
  unbounded database round trips in either direction of the seam.
- Configuration errors surface at composition time rather than as a partial
  online failure.
- Issue #148 has exactly one legal home for weighted RRF, and a weight placed
  pre-Kernel is provably inert rather than subtly wrong.
- A tenant whose own exact-phrase query legitimately matches more Fragments
  than the configured bound gets a closed refusal rather than a silently
  truncated Package. `candidate_submission_limit` is the operator's knob for
  that tradeoff, and its default sits well above the largest result any single
  ranker produces today.
- Adding a ranker requires a weight and stays within the ranker-list bound.

## Revisit trigger

Revisit when a measured retrieval requirement needs more submitted candidates
than the hard ceiling admits, when a fusion policy needs a signal that only
exists before authorization, or if any audit shows delivered order correlating
with refused candidates. Any revision must keep the bound server-owned, keep
refusals content-free, and keep fusion weighting unable to observe anything
authorization has not already admitted.
