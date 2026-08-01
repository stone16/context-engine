---
name: adr-0097-require-routing-clauses-in-agent-facing-frontmatter
version: "1.0.0"
description: >
  Require action-led descriptions with explicit positive and negative routing
  clauses across every agent-facing document class. Use when authoring or
  reviewing frontmatter for an ADR, charter, routing shelf, skill, or design
  system. Not for changing the decision or procedure described by the document.
---

# 0097. Require routing clauses in agent-facing frontmatter

- Status: accepted
- Date: 2026-08-02
- Refines: ADR-0001

## Context

ADR-0001 adopted doc-steward for ContextEngine's agent-facing documentation but
did not settle whether its description-quality rule applied uniformly to
shelved ADRs. The repository therefore accumulated descriptions that summarized
their documents accurately but omitted positive or negative routing clauses.
The decision index still routed ADRs well for a reader already in that index,
while deterministic audit findings grew and made the default house standard an
unreliable gate.

Two approaches remained defensible: scope routing clauses only to always-resident
and routing surfaces, or backfill every document class so one default rule governs
the repository. The maintainer selected the uniform full backfill. Description
rewrites remain judgment-bearing because their routing language must stay inside
the scope of each document's own decision.

## Decision

1. Doc-steward's description-quality rule applies uniformly to every
   agent-facing document with frontmatter. This includes `AGENTS.md`,
   `CLAUDE.md`, routing shelves under `docs/agents/`, every `SKILL.md`,
   architecture decisions, and `DESIGN.md`. A new agent-facing document class
   inherits the same rule unless a later accepted ADR records and configures an
   explicit exception.
2. Every description is English-only, contains 40–500 English characters,
   begins with an action verb, and includes both an affirmative `Use when ...`
   clause and a negative `Not for ...` clause. Document types that carry a
   version use semantic `X.Y.Z` form. The clauses name distinct routing
   situations rather than repeating a generic repository-wide trigger.
3. An ADR description stays subordinate to that ADR's Decision. Its summary,
   positive trigger, and negative boundary must be derivable from the Decision's
   fixed choice, exclusions, or explicitly deferred capabilities. A frontmatter
   cleanup may not broaden, narrow, or otherwise rewrite the architectural
   decision it routes to.
4. The ADR authoring skeleton lives in `docs/decisions/README.md` and already
   conforms. Authors copy that skeleton for new ADRs and replace every
   placeholder with decision-specific language before review.
5. The repository uses doc-steward's default uniform rule without a scoping
   configuration. Deterministic EVALUATE is the reproducible gate. Because
   description rewrites are escalate-class, historical backfills are manually
   reviewed rather than presented as mechanical autofixes.

## Rationale

One uniform style is cheaper to remember, review, and audit than a class-specific
exception. Explicit negative routing also makes an ADR's boundary visible before
an agent loads its full body. Requiring the clauses to derive from the Decision
controls the principal cost of a backfill: frontmatter that drifts into a second,
less-reviewed source of architecture.

The README index remains the primary map for implementation boundaries. Routing
frontmatter complements that map for direct document discovery; it does not
replace the index or acquire architectural authority of its own.

## Considered alternatives

- Apply the routing clauses only to always-resident charters, routing shelves,
  skills, and design guidance, while requiring ADRs only to carry a bounded
  action-led summary. Rejected because the committed exception and the audit
  configuration would make document-class membership part of every future
  author's decision.
- Leave historical descriptions unchanged and enforce the rule only on new
  documents. Rejected because the deterministic audit would retain a permanent
  accepted-red baseline and would not distinguish known debt from regression.

## Consequences

- Existing agent-facing descriptions are backfilled without changing their
  document bodies or architectural decisions.
- Positive and negative triggers for closely related ADRs must describe distinct
  situations; in particular, one-Resource tombstoning and whole-Source
  offboarding cannot share the same routing trigger.
- A clean default EVALUATE report is the forward quality gate, so adding a
  nonconforming document reintroduces a visible finding immediately.
- Any future document-class exception requires an accepted refining ADR and
  reproducible committed configuration; an informal convention is insufficient.

## Revisit trigger

Revisit if doc-steward replaces the rule with a spec-required equivalent, if a
new agent-facing document class cannot express truthful positive and negative
routing within the bound, or if measured retrieval evidence shows that the
clauses reduce rather than improve document selection.
