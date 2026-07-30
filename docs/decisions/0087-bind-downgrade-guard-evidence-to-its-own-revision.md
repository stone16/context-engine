---
name: adr-0085-bind-downgrade-guard-evidence-to-its-own-revision
version: "1.0.0"
description: >
  Bind registered downgrade-guard evidence to its own revision step and record
  the retained corpus the gate observed. Use when adding or changing evidence
  that asserts a migration refusal. Not for changing what any guard protects.
---

# 0085. Bind downgrade-guard evidence to its own revision

- Status: accepted
- Date: 2026-07-30
- Refines: ADR-0034

## Context

ADR-0034 makes `make security-gate` the executable M0 veto: every registered
selector must be collected and pass with no skip, xfail, or retry. Four
registered selectors assert that an Alembic downgrade refuses while a specific
retained fact exists, and each named the exact refusal message.

Each of those selectors reached its guard by asking Alembic to downgrade to a
target several revisions below the guard that owns the message. Alembic
downgrades newest-revision-first, so the chain evaluates every intervening
guard and stops at the first one that refuses. Revision `20260726_0035` counts
nested `relative_path` rows across the whole database with no Organization
filter, which is correct for a migration: ADR-0069 decision 6 gives schema
bootstrap no operator identity, no Organization context, and no
`ControlOperation`, so a migration cannot legitimately scope that count to a
tenant.

The consequence was measured in the PR #122 dogfood walkthrough. With real File
lineage retained, the recursive-path guard displaced the guard each selector
asserted and the gate reported four failures, so the runbook told operators to
run the veto only on a clean volume. A veto that goes red whenever real data is
present trains the operator to read red as normal, and every M1 connector adds
retained lineage, so the triggering population only grows.

The guards themselves are already deterministic; each is one counting query
whose result does not depend on scan or insertion order. What depended on the
retained corpus was the *shape of the evidence*: which guard a multi-revision
chain reaches first.

## Decision

1. Registered evidence that asserts a downgrade refusal exercises exactly the
   revision that owns the guard, through a shared test-support driver that runs
   that one revision's `downgrade` inside a real Alembic `MigrationContext` and
   rolls the transaction back unconditionally. It never drives a multi-revision
   chain to reach a guard.
2. No guard is deleted, tenant-scoped, relaxed, or narrowed to obtain this. The
   whole-database recursive-path guard keeps its exact semantics, and that it
   still refuses on retained nested lineage is proved by integration evidence
   under `make integration`, not by the M0 registry: no registered selector
   asserts that guard, and adding one is an ADR-0034 registry change with its
   own invariant and fixture mapping obligations rather than a side effect of
   this repair.
3. Where a guard selects among several whole-database blockers and names only
   the first, the assertion pairs the guard's refusal contract with the
   guard's own blocker predicate evaluated for the Organization under test.
   Naming a blocker that another tenant's retained rows can displace is not
   evidence about the property under test. Because that pairing can no longer
   detect a deleted branch, the guard's blocker branch structure is pinned at
   source level alongside it.
4. The gate records, before it executes any registered selector, the retained
   File lineage it observed: Organization, `file_acquisition`,
   `file_source_change`, `file_delete_observation_execution`, and nested-path
   counts, plus the derived `populatedVolume` and `retainedNestedLineage`
   facts. It is published as `provenance.retainedFileLineage`, and a missing or
   self-contradictory observation is a Security failure like any other missing
   provenance.
5. ADR-0034's report vocabulary is unchanged. No `PRECONDITION_NOT_MET` status
   is introduced: no registered assertion turned out to be inherently
   whole-database once bound to its own revision, so the fallback that would
   have moved the report contract is not used. Security remains `pass` or
   `fail`, the other three sections remain `not-evaluated`, and there is still
   no aggregate score.

## Rationale

Binding each assertion to its own revision makes the evidence a function of the
property it protects rather than of traversal order, which is the only repair
that keeps both the guard and the verdict intact. Rolling the step back leaves
the live revision at head, so the evidence costs no schema state.

Publishing the retained-lineage counts is what makes the verdict third-party
checkable. Without it a reader cannot distinguish a pass earned on a database
holding real lineage from a pass earned only after `make db-reset`, which is
exactly the ambiguity that made the previous runbook instruction necessary.
Counts and booleans carry no tenant content.

## Consequences

- `make security-gate` produces a trustworthy verdict on a populated volume, so
  M1's "7 consecutive days of real daily use; security-gate PASS" is reachable
  without resetting the database.
- New evidence that asserts a downgrade refusal must name the revision that owns
  the guard. Reaching a guard through a chain is a corpus-sensitivity defect.
- A guard whose refusal message is asserted by prefix rather than in full owes a
  source-level pin of its blocker branches, because the prefix alone cannot tell
  a deleted branch from a displaced one.
- Gate artifacts gain one provenance object. A run that cannot observe the
  retained lineage fails Security rather than reporting an unqualified pass.
- The synthetic multi-Organization corpus used by this evidence is tracked test
  support, never dogfood content, and its retained rows must stay unreadable by
  every non-owner role under FORCE RLS.

## Revisit trigger

Revisit if a registered assertion is found whose property genuinely cannot be
expressed against one revision's own retained facts; that case would reopen the
explicit-precondition status ADR-0034's report contract currently excludes.
Revisit also if Alembic stops exposing a single-revision downgrade through a
migration context. Any replacement must keep every guard's meaning, keep the
verdict independent of retained corpus, and keep the observed corpus condition
published beside the verdict.
