---
name: adr-0033-organization-release-promotion-owner
version: "1.0.0"
description: >
  Make Organization-owned immutable release lineage and one generation-bound
  ContextLearning promotion transaction the only release publication path.
---

# 0033. Promote Organization releases through one Learning owner

- Status: accepted
- Date: 2026-07-22
- Refines: ADR-0007, ADR-0014, ADR-0015, ADR-0019

## Context

The active `ReleaseManifest` determines the content, index, Runtime, and
curation profiles observed online. Initial activation and rollback are the same
publication decision as an ordinary profile change. A migration seed, direct
pointer update, evaluator shortcut, or second Module owner would therefore be a
production publication authority even if it were described as bootstrap or
recovery.

The architecture permits release records to be tenant-owned or explicitly
global. Future curation compatibility is defined against Organization-owned
Revisions, while Issue #49 already requires wrong-scope operator authority to
produce no publication. A global pointer would introduce a second scope model
before there is evidence for cross-Organization release sharing.

Comparing only the expected manifest digest is insufficient for optimistic
concurrency. After `A -> B -> A`, a candidate evaluated against the first
activation of A would appear current again. A seeded sentinel pointer would
avoid a special initial case but would let migration or bootstrap create
publication state outside the release owner.

## Decision

Release lineage is Organization-owned. `release_manifest`,
`release_candidate`, `release_evaluation`, the active pointer, current
release-operator grants, and successful promotion audit all carry the exact
Organization key, same-Organization foreign keys where applicable, and FORCE
RLS. A fresh database and a newly created Organization have no active release
pointer. The first explicit `curation-off` manifest is an ordinary immutable
candidate with an absent expected base; no migration, trigger, fixture loader,
or bootstrap command activates it.

The only public Module operations are:

~~~text
ContextLearning.evaluate(ReleaseCandidateRef) -> ReleaseEvaluation
ContextLearning.promote(TrustedPromotionCall) -> PromotionReceipt
~~~

`evaluate` may persist immutable candidate and evaluation lineage but cannot
write the active pointer or success audit. Control, Supply, Runtime, Curation,
and application bootstrap expose no publication operation. Rollback constructs
a new candidate that selects a compatible historical immutable manifest and
uses the same `promote` operation; it appends a new activation event and never
rewrites history.

The M0 manifest always names immutable Content, Index, and Runtime profile refs
and their digests, and explicitly selects `curation-off`. “Empty” means that
this initial profile composition delivers no curated production content; it
does not mean that required profile identity or lineage is absent. Domain
contracts describe the future `curation-on` shape so incomplete or incompatible
snapshot and Revision references can fail closed, but successful production
`curation-on` remains inactive.

A candidate binds the selected manifest digest and the exact expected active
state: both the expected base manifest digest and the expected activation
generation. Initial activation expects no pointer and generation zero. Each
successful promotion increments the positive signed 64-bit generation. The
generation remains monotonic even when a historical manifest is selected, so
`A -> B -> A` cannot revive a stale candidate.

Canonical release documents encode signed 64-bit generation and key-version
values as base-10 JSON strings. This preserves the PostgreSQL `bigint` domain
without crossing RFC 8785's interoperable JSON-number range; domain and schema
contracts still expose and validate these values as integers.

The evaluation document binds the Organization, candidate and manifest
digests, expected base and generation, four independent closed
Security/Reliability/Quality/Budget gate results, compatibility evidence,
capability coverage, fixture references, command references, and evaluation
time. Security is a veto, not part of an aggregate score; every gate must pass.
The canonical document is protected by a separate versioned,
domain-separated HMAC-SHA256 evaluation signature. It has no default key and
does not reuse Package, query, ticket, or WorkerLease signing domains.

`TrustedPromotionCall` is a nominal, lifetime-bound trusted input. It binds one
Organization, candidate, signed evaluation, exact expected active state,
operator and authentication binding, durable grant reference, request,
expiry, and audit-reason digest. The caller cannot construct it from a wire
body. The retained non-owner Learning transaction revalidates the current
durable Organization-scoped operator grant using database-owned time, reloads
the immutable manifest/candidate/evaluation rows, recomputes their bindings,
verifies all gates and compatibility, and invokes one narrow promotion
function.

The PostgreSQL function is owned by a dedicated `NOLOGIN` definer, has a fixed
catalog-only search path and row security enabled, verifies the exact Learning
`SESSION_USER`, and is executable only by that dedicated non-owner login. The
Learning role has no direct pointer or audit DML. Other application roles and
`PUBLIC` have neither table DML nor function execution.

At M0, durable `release_operator_grant` administration belongs only to the
migrator-controlled deployment/security provisioning authority. It is not a
public operation of ContextLearning, ContextControl, or any other application
Module, and bootstrap creates no grant. The Learning login cannot insert,
update, revoke, or self-issue a grant; the definer may only read the exact
current grant while executing promotion. Introducing an application-facing
grant administrator requires a separately reviewed authority boundary and ADR.

Inside one transaction the function serializes the Organization release state,
compares the absent-or-exact generation-bound expected base, changes the
pointer, and appends one success audit row. A stale or concurrent loser raises
and the transaction exposes neither change. A receipt is constructed only
after commit. Failed authority, expiry, revocation, signature, digest, gate,
compatibility, or commit therefore produces zero pointer change and zero
success audit.

Schema downgrade takes a lock that serializes with promotion and refuses before
DDL when any release authority or lineage row exists. Once publication history
exists, preserving it requires a forward fix rather than destructive rollback.

## Rationale

Organization ownership reuses the repository's established tenant isolation
model and makes wrong-scope rejection executable without inventing a global
administrative plane. A generation-bound compare-and-swap covers both the
initial absent row and later ABA histories. Keeping pointer and audit mutation
behind one database function makes “sole owner” a credential and privilege
property, not merely a Python convention.

Separating nominal call authority, a current durable grant, and a signed
evaluation prevents any one stale or copied artifact from publishing. Exact
four-gate evidence preserves the security veto while leaving richer evaluation
and automatic Learning for later issues.

## Consequences

Operators must first create immutable profile, candidate, evaluation, and
current grant records through reviewed internal paths, then call `promote` with
their exact bindings. Initial M0 activation is explicit operational work and is
observable as the first success audit event. There is no preactivated fallback
if that work has not occurred.

The narrow Learning role and definer role add two database credential
contracts, but no process boundary. Tests must use the real non-owner Learning
login and prove fresh absence, direct-DML denial, current authority
revalidation, atomic concurrent CAS, immutable history, and rollback through
the same method.

## Revisit trigger

Revisit Organization ownership only if measured requirements establish a
single global release shared across tenants and define its operator scope,
isolation, and rollback semantics. Revisit the M0 evaluation signature when a
production evaluation service or hardware-backed signer is introduced. Any
replacement must preserve one publication owner, current authority
revalidation, generation-bound CAS, atomic success audit, no bootstrap seed,
and rollback as a new promotion.
