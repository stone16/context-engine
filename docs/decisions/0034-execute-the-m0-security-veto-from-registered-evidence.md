---
name: adr-0034-executable-m0-security-veto
version: "1.0.0"
description: >
  Join the frozen security authority to exact executable evidence, live RLS
  inventory, and provenance-bearing release artifacts without inventing
  product behavior or aggregate scoring.
---

# 0034. Execute the M0 security veto from registered evidence

- Status: accepted
- Date: 2026-07-22
- Refines: ADR-0015, ADR-0019

## Context

The normalized security catalog fixes fifteen invariant families and twelve
acceptance fixtures, including the expected fail-closed outcome for carriers
that are not active at M0. Its evidence identifiers intentionally describe the
full delivery roadmap. They are design authority, but a string in that catalog
does not prove that a current test exists, was collected, ran without a skip,
or passed against the required seam.

The schema security manifest is likewise the declared table-classification
authority. Reading only its `rls.enabled` fields would let drift in the live
database, a newly unclassified table, or removed `FORCE ROW LEVEL SECURITY`
escape a release report. Finally, reducing Security, Reliability, Quality, and
Budget to one score would allow an unrelated result to offset a failed
authorization invariant.

## Decision

The static security catalog remains the semantic authority. A separate,
versioned M0 evidence registry joins current evidence identifiers to exact
pytest selectors, one evidence class (`property`, `postgres`, or `runtime`),
the invariant families genuinely proved, and the acceptance fixtures whose
explicit observations they emit. The registry must cover every invariant in
all three evidence classes and every canonical fixture. Duplicate identifiers,
unknown references, empty or missing mappings, uncollected selectors, skips,
xfails, xpasses, and failed setup, call, or teardown outcomes are deterministic
Security failures. Registered selectors are de-duplicated and executed once;
the gate has no retry path.

Fixture tests publish the three hard-oracle observations rather than relying
on pytest success as a substitute for measurement: unauthorized Evidence,
wrong-Organization effect, and missing-context fallback. Every required value
is zero. A missing or non-zero observation fails Security and the command.
Inactive M0 carriers are tested at their current public fail-closed boundary;
the gate does not add their future product semantics.

The schema security manifest is the complete public-table denominator. Each
table is exactly one of `tenant_owned` or explicitly allowlisted `global`, and
each global entry carries a non-empty rationale. For every tenant-owned table,
the gate verifies a machine-declared Organization ownership path, enabled and
forced live RLS, at least one live policy, and registered non-owner PostgreSQL
evidence. It first requires the live public-table set to equal the manifest, so
an unclassified table cannot disappear from the denominator. Coverage is
reported per table and as numerator over denominator; M0 requires complete
coverage.

One local and CI command performs static validation, exact evidence execution,
the live PostgreSQL audit, and report generation. It writes canonical raw
evidence plus a release-gate report. Provenance includes the Git commit and
tracked-worktree state, catalog/registry/schema/configuration digests, Alembic
head and live revision, exact execution command, and a deterministic digest of
normalized test outcomes and observations. Wall-clock durations remain in raw
evidence but are excluded from that normalized result digest. Provenance never
records database URLs or credentials.

The release report has four independent sections: Security, Reliability,
Quality, and Budget. Security is `pass` or `fail` and is a veto. The other
three are explicitly `not-evaluated` at M0 until their owning gates exist;
`not-evaluated` is never converted to pass. The document has no aggregate
score, weighted score, or averaging field. A passing Security section sets only
`m0SecurityDecision: pass`; `releaseDecision` and `promotionReadiness` remain
`not-evaluated`, so this integration report is not a promotion-ready
`ReleaseEvaluation`.

## Rationale

Separating expected roadmap evidence from the executable registry preserves
stable design identifiers while making the current proof discoverable and
strict. Exact pytest collection closes the gap between a referenced test name
and an executed assertion. Explicit observations prevent the runner from
manufacturing hard-oracle zeros from a green process exit.

Using the manifest as the denominator and PostgreSQL catalogs as live facts
makes complete RLS coverage measurable without guessing from column names.
Keeping the four gates independent preserves the Security veto and represents
unevaluated work honestly.

## Consequences

Adding or renaming a registered test, invariant, fixture, or application table
requires an intentional registry or manifest update. A deterministic skip is a
red gate, not a temporary green build. Registered evidence may support multiple
families only when its registry references and assertions name each proof.

The executable gate re-runs a focused subset after the broad unit and
integration suites so it can retain exact release evidence. That extra runtime
is accepted for M0. Generated artifacts remain ignored locally and are retained
by CI; they are evidence, not source authority.

## Revisit trigger

Revisit the registry transport if pytest is replaced or signed remote evidence
becomes necessary. Revisit the three unevaluated sections only when their
independent deterministic commands and provenance contracts exist. Any
replacement must still execute all canonical mappings without skips or retries,
derive hard-oracle results from explicit observations, audit the complete live
RLS denominator, retain raw evidence, and preserve Security as a non-aggregate
veto.
