---
name: adr-0073-compose-explicit-release-candidates-from-current-corpus
version: "1.0.0"
description: >
  Let the explicit local release operator assemble a candidate from one exact
  Release-operator-owned observation of the current active File corpus and release
  base, while preserving ContextLearning as the sole publication owner.
---

# 0073. Compose explicit Release candidates from the current corpus

- Status: accepted
- Date: 2026-07-27
- Refines: ADR-0033, ADR-0068, ADR-0069

## Context

The dogfood Runtime accepts only a promoted manifest selecting the fixed
dogfood vector profile and a nonempty set of active Revisions. Supply owns the
active Resource-to-Revision pointers, while ContextLearning owns immutable
candidate evaluation and the only active Release pointer. The local operator
process needs both the current corpus set and the generation-bound active
Release base to construct an honest candidate.

Giving the Control process or Learning login any Resource-table observation
seam would widen either plane. Reading the corpus through the migrator would
also make a deployment credential an application dependency and permit candidate
assembly outside the Learning boundary. Guessing generation zero or accepting
Revision refs from CLI arguments would make retries stale or caller-authored.

## Decision

The dedicated release definer exposes one stable, tenant-scoped observation
function executable only by a distinct non-owner Release operator database
login. The local composition authenticates the release credential before it
opens that role-isolated connection. The function first revalidates the exact
current durable release-operator grant using database-owned time, then
returns exactly one row containing the current active generation, its manifest
digest if one exists, and the sorted unique active Revision refs of
non-tombstoned Resources whose exact File Source remains active. Disabled
sources retain lineage for audit but are not part of the current candidate
corpus. Migration 0040 acquires the existing File scheduling, dispatch, then
status fences before adding or removing the observation policies, preserving
the File subsystem's established global lock order. The function sets and
validates the Organization scope internally and exposes no content, ACL,
Fragment, Resource, or Source identity.

The local `promote-release` composition uses that snapshot to build one
immutable candidate selecting the already accepted dogfood Content, Index,
Runtime, and curation-off profiles. Four gate evidence, capability coverage,
fixture identity, and verification commands come from an explicit operator
evidence file; a separate explicit versioned signing key protects the durable
evaluation. The immutable candidate reference binds both the observed corpus
and the complete normalized evidence, so a corrected failed gate creates a
distinct candidate instead of colliding with the prior immutable failed
attempt. Empty corpus snapshots are refused before candidate persistence.

The command then calls `ContextLearning.evaluate` and authorizes exactly one
`promote` call with the release credential. It does not call the promotion
database function directly. Candidate, evaluation, active pointer, and success
audit persistence remain behind the existing Learning role and release
definer. A repeated command observes the new generation and promotes the same
immutable manifest as a new audited generation, preserving ADR-0033's existing
outcome rather than adding idempotency.

Durable release-operator grant provisioning remains deployment/security work.
The existing local dogfood seed command may explicitly provision only the
fixed ADR-0069 local release identity while already running under the migrator
role; it does not promote, create candidates, or write the active pointer. The
seeded durable grant uses the separately bounded `LOCAL_RELEASE_GRANT_TTL` and
can be refreshed by rerunning that explicit seed command. The authenticated
identity and each `PromotionAuthorizationRequest` remain bounded by the shorter
`LOCAL_OPERATOR_TTL`; a durable grant never extends a call identity.

## Consequences

- Candidate inputs are derived from current durable state without widening
  Control, Runtime, or Learning; a Learning database credential cannot invoke
  the observation function even with exact current release identity facts.
- The operator must retain an evaluation signing key across invocations and
  supply reviewed four-gate evidence; neither has a default.
- Corpus changes racing after observation are not silently included. The
  promoted manifest remains bound to the exact observed Revision set, and a
  later explicit promotion can advance it.
- Grant provisioning is explicit and bounded to the local release identity;
  production grant administration remains `NOT_ACTIVE`.

## Revisit trigger

Revisit when candidate generation becomes an autonomous Learning capability,
when production grant administration exists, or when a corpus-wide publication
snapshot must serialize with concurrent Supply pointer changes.
