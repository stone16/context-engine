---
name: adr-0058-pull-development-through-dogfood-workloads
version: "1.0.0"
description: >
  Order the roadmap by the maintainer's own real workloads instead of a fixed
  connector sequence, and require every capability investment to be pulled by
  an observed need rather than built breadth-first.
---

# 0058. Pull development through dogfood workloads

- Status: accepted
- Date: 2026-07-26
- Refines: ADR-0016, ADR-0057

## Context

The repository has no real query traffic. The release gate evaluates Security
as a veto while Reliability, Quality, and Budget report `not-evaluated`; the
`eval/` tree contains only security catalogs and no golden set. The served
default composition delivers empty ContextPackages because no candidate index
is wired into it. The prior roadmap ordered work as a fixed connector
sequence (File, then Feishu, then Slack, then Google Docs) aimed at a team
delivery scenario, while the founding thesis targets context infrastructure
for model callers generally. Security quality became excellent precisely
because it has a feedback source — the threat model and hard oracles. The
retrieval and ingestion surfaces have no equivalent feedback source today.

## Decision

The first caller of ContextEngine is the maintainer's own tooling over the
maintainer's real workloads: real notes, repositories, and recurring
questions. Roadmap ordering is pulled by these workloads:

1. A capability is scheduled when a real dogfood workload demonstrably needs
   it, and its first slice is the narrowest cut that serves that workload
   end to end.
2. The first vertical slice ("Slice A", recorded in the 2026-07-26 review
   document) makes the served composition — under the explicitly configured
   dogfood authentication composition of ADR-0059, while the module-level
   default composition remains reject-all — deliver real Evidence for the
   maintainer's Markdown corpus: File provider widening, one real vector
   candidate index behind the existing candidate-index seam, one real caller
   through the generated SDK or HTTP, and a golden set v0 seeded from real
   maintainer queries.
3. Connector breadth (Feishu, Slack, Google Docs), hybrid retrieval fusion,
   deep parsing beyond Markdown, and the structured acquisition family are
   backlog items ordered by observed pull, not fixed milestone positions.

The prohibited shortcut is breadth-first construction of "the complete
layer" — building connectors, parsers, or retrieval machinery ahead of any
workload that exercises them.

## Rationale

Completeness without a pulling workload has no falsifier; it optimizes area
instead of usefulness, which is the failure mode this decision is designed to
avoid. The maintainer's own workloads are the only immediately available,
zero-coordination feedback source, and they naturally span both context
families of ADR-0057. Dogfood pull decides ordering only; it does not weaken
any multi-tenant invariant — single-tenant dogfood traffic still runs the
full sealed AuthorizationKernel, FORCE RLS, and Policy Epoch path.

## Consequences

- `PLAN.md` milestone sequencing after the current File reliability work is
  subordinated to workload pull and requires a revision pass.
- Golden set v0 is seeded from real maintainer queries and grows with use;
  Quality gains its first measurable signal.
- Retrieval upgrades (hybrid fusion, reranking) are triggered by observed
  golden-set failures, not scheduled speculatively.
- The security posture is unchanged: hard oracles remain veto for every
  slice, and dogfood traffic exercises the same sealed path as future tenant
  Organizations.

## Revisit trigger

Revisit when the first external caller or design partner commits, or when a
dogfood-pulled ordering would defer a security-relevant capability that the
threat model shows is needed before wider exposure.
