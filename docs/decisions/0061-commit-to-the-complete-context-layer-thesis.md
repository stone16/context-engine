---
name: adr-0061-commit-to-the-complete-context-layer-thesis
version: "1.0.0"
description: >
  Fix the product thesis as one complete context layer with two context
  families, and defer the structured acquisition family behind an explicit
  boundary instead of forcing it into snapshot publication semantics.
---

# 0061. Commit to the complete context layer thesis

- Status: accepted
- Date: 2026-07-26
- Refines: ADR-0006, ADR-0016

## Context

The 2026-07-26 repository review confirmed a divergence between the founding
thesis and the implemented shape. The founding thesis is a complete context
layer for model callers: knowledge-base context plus structured data acquired
at request time from databases and APIs. The implemented domain model —
`ContextSource -> ContextResource -> ContextRevision -> ContextFragment` —
carries immutable snapshot publication semantics only. Request-time structured
acquisition has no natural ContextRevision, no tombstone, no publication
pipeline, and requires source-side authorization evaluated at acquisition
time. The roadmap and glossary were silent about this second family, leaving
two failure modes open: force-fitting live structured results into Revision
semantics, or reading the repository as if structured context were abandoned
rather than deferred.

## Decision

ContextEngine's product thesis is one complete context layer that serves two
context families behind the same sealed Runtime contract and the same
authorization invariants:

1. **The knowledge-snapshot family.** Everything the current glossary defines:
   snapshot-published sources whose content becomes immutable ContextRevision
   and ContextFragment lineage. This family is active and remains the only
   implemented family.
2. **The structured acquisition family.** Request-time acquisition of
   structured data from databases and APIs. This family is deferred by
   design, not abandoned. It enters only through its own canonical glossary
   terms and its own accepted ADRs, with acquisition-time authorization
   semantics designed before any carrier is implemented.

Both families terminate in the same deliverable: an authorized,
evidence-backed, budget-bounded ContextPackage produced through the sealed
AuthorizationKernel. Neither family may bypass the three hard oracles. The
glossary currently defines `Evidence` only over exact-authorized
ContextFragments, and ContextPackage composition over that Evidence; the
structured family's future ADRs must explicitly extend those definitions
before any carrier exists, not silently reuse them.

The prohibited shortcut is registering a database or API source as a
ContextSource whose query results are stored as ContextRevisions, or
otherwise reusing snapshot publication lifecycle or Fragment-bound
request-scoped terms for request-time structured results without an explicit
glossary extension.

## Rationale

The built differentiation — exact authorization, revocation, evidence lineage
under FORCE RLS — is family-independent and is exactly the capability the
four-repository evidence baseline
(`docs/research/2026-07-19-four-public-repositories-evidence.md`) shows to be
collectively absent across the audited references. The
snapshot lifecycle, however, is family-specific. Extending it by analogy to
live structured data would corrupt the glossary's lifecycle guarantees
(immutability, atomic pointer activation, tombstone-before-cleanup) that the
security suite currently proves. An explicit deferred boundary preserves both
the thesis and the model.

## Consequences

- `CONTEXT.md` records that its current lifecycle terms cover the
  knowledge-snapshot family only; structured-family terms are added when that
  family is designed.
- The roadmap's connector-only sequencing is no longer a complete statement of
  intent; ordering is governed by ADR-0062.
- The structured acquisition family requires at least: a source registration
  shape, an acquisition-time authorization contract, freshness semantics, and
  an explicit glossary extension of `Evidence` and ContextPackage composition
  — each fixed by future ADRs before code.
- `PLAN.md` and the implementation design require a revision pass to state the
  two-family thesis; until then their roadmap text is read under this ADR.

## Revisit trigger

Design the structured acquisition family before implementing its first
carrier. The trigger is the first real dogfood workload under ADR-0062 that
needs request-time structured data; reopen this boundary then, not before.
