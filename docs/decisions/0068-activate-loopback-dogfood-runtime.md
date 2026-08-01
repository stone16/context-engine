---
name: adr-0068-activate-loopback-dogfood-runtime
version: "1.0.0"
description: >
  Activate one loopback-only, single-Membership File pgvector Acquire carrier
  with deterministic twin embeddings and EffectiveScope reduction before LIMIT.
  Use when running the explicit loopback dogfood Acquire carrier. Not for
  production authentication, remote ingress, group delivery, or external models.
---

# 0068. Activate the loopback dogfood Runtime

- Status: accepted
- Date: 2026-07-27
- Refines: ADR-0021, ADR-0025, ADR-0063, ADR-0067

## Context

ADR-0063 admits one explicitly configured local authentication composition but
does not activate content delivery. ADR-0067 admits the pgvector candidate seam
but leaves it out of the served composition until the Kernel-computed
`EffectiveScope` can remove out-of-policy rows before the bounded ANN result.
The maintainer dogfood loop needs both decisions to become one runnable,
auditable carrier without widening the default application or creating a
production-authentication ancestor.

Runtime cannot yet charge an external query embedding call to the effective
`PackageBudget`, and no external provider profile is active. Those gaps prohibit
an external provider in this carrier. The deterministic twin, by contrast, has
one Release-bound model/input profile identity.

## Decision

The served API may activate one bounded dogfood composition only when the exact
local composition selector and every required identity, secret, Runtime
database, and embedding setting are present and valid.

1. The module-level ASGI application is always the existing reject-all
   composition. The API CLI validates an explicit loopback host before it
   constructs the dogfood application; direct `uvicorn adapters.http.app:app`
   therefore cannot activate delivery. The default performs zero content I/O
   and reports `runtime_delivery: NOT_ACTIVE`.
2. A constant-time bearer check maps one environment-held secret to one fixed
   Organization, User, Membership/version, Principal, Agent, application, and
   authentication binding. The secret is excluded from representations and is
   never written to a response, log, `ContextRun`, or `DecisionAudit`. The
   query-digest key is domain-separated and derived from that secret; rotating
   the secret therefore also rotates the local digest-key material.
3. Only identity verification is simplified. Every accepted request opens the
   ordinary current-Membership `UserActor` transaction, redeems the active
   Release, computes trusted scope facts in that same transaction, traverses
   the non-pluggable `AuthorizationKernel`, projects an `AuthorizedProjection`,
   rechecks current Organization Policy Epoch, and persists the existing
   authorized-only lineage.
4. The dogfood scope authority carries the operands separately: Organization
   boundary and Mirrored File source lifecycle come from the current RLS-visible
   Release selection, Membership rights come from current field rights,
   Principal grant and Resource ACL come from current File access policy, and
   the exact configured Agent and `context.answer` purpose each receive only
   the Release-selected Organization ceiling. A missing or empty operand
   absorbs the intersection. This is a local File composition, not a general
   Principal, Agent, purpose, or source-native policy system.
5. The Kernel passes its computed `EffectiveScope` to candidate discovery.
   PostgreSQL matches exact Organization/source/resource triples before ANN
   ordering and `LIMIT`; the filter can only remove candidates. Every returned
   `CandidateRef` still undergoes exact Kernel reauthorization and field
   projection. Index, RLS, Release, and candidate rank never grant authority.
6. Query embedding is the deterministic network-free twin only. Its model and
   contextual-fragment input profile are bound by the active Release and are
   validated at composition activation and on every request. Its effective
   Package usage is zero external provider calls, zero cost, and zero provider
   elapsed time. Operators must freshly reimport the dogfood corpus with the
   worker's same twin before publishing that Release. Any external or unknown
   query-provider selection fails composition because exact external usage
   accounting is not active.
7. Identity seeding is an explicit idempotent local operation using only the
   configured migrator connection. It creates one Organization, one User, and
   one current Membership if absent; it does not run at API startup and never
   gives the Runtime process migration authority.

The activation records name only the loopback single-Membership authentication
and File pgvector Acquire carriers. Production authentication, a second human,
network exposure beyond the maintainer machine, group/public audience,
dogfood `OpenCitation`, `Continue`, hybrid retrieval, external query embedding,
and non-File providers remain `NOT_ACTIVE`.

## Rationale

Passing exact effective scope into discovery closes the selective-filter recall
gap without confusing an optimization with authorization. Keeping scope
derivation, discovery, authorization, projection, and final epoch validation in
one current `UserActor` transaction prevents trusted facts from being rebuilt
across sessions. A network-free twin makes the first real dogfood loop honest
about both cost and profile compatibility while durable external-provider
lineage remains unavailable.

## Consequences

- A configured maintainer can receive real Evidence-bearing File
  `ContextPackage`s over `POST /v0/resolve`; `/health` reports the carrier
  `ACTIVE` only for that explicit composition.
- Revoked or expired Membership and mid-resolve Policy Epoch change fail closed
  through the existing gates.
- Trusted scope materialization is exact but can grow linearly with the active
  dogfood Release. That is acceptable for this bounded local carrier; a larger
  corpus requires measurement and a durable policy representation.
- Empty or embedding-profile-mismatched active Release lineage, unavailable scope facts, malformed
  configuration, non-loopback binding, and external provider selection fail
  closed.
- This composition must be deleted or replaced, not widened, when production
  authentication or a second human becomes necessary.

## Revisit trigger

Revisit before a second human, remote ingress, group/public delivery,
dogfood `OpenCitation`, `Continue`, hybrid or non-File discovery, a general
source-native ACL authority, or an
external query embedding provider. External-provider activation additionally
requires immutable publication/query model plus input-profile identity and
Runtime-enforced provider-call, cost, and elapsed accounting.
