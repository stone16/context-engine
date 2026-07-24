---
name: adr-0051-reauthorize-opaque-citation-opens
version: "1.0.0"
description: >
  Issue digest-only multi-use citation locators after authorized projection and
  reauthorize every open through the sealed Runtime Kernel.
---

# 0051. Reauthorize every opaque citation open

- Status: accepted
- Date: 2026-07-24
- Refines: ADR-0012, ADR-0013, ADR-0023, ADR-0025, ADR-0028, ADR-0031, ADR-0045, ADR-0046, ADR-0048

## Context

A citation must let a later caller request the exact prior Evidence target, but
the reference cannot preserve the prior caller's authorization. Membership,
Resource access, Source lifecycle, field rights, Policy Epoch, delivery
audience, and egress policy may all differ at open time. Treating the reference
as a bearer capability would bypass those current facts; returning a source URL
would expose both location and authority-sensitive metadata.

The locator is also useful across retries. A denied open must therefore neither
consume it nor extend its lifetime, and denial must not reveal whether the
target once existed.

## Decision

An authorized File `Evidence` receives a server-issued `CitationOpenRef` only
after `CandidateRef -> AuthorizationKernel -> AuthorizedProjection` has
completed. The reference is opaque, type-separated from continuation and egress
capabilities, and included in Evidence integrity and the public Package digest.

PostgreSQL stores only the SHA-256 locator digest, digest/profile and retention
metadata, prior Package/Evidence refs, and exact Resource/Revision/Fragment
location lineage. It stores no source URL, prior principal, Membership,
audience, purpose, Policy Epoch, authorization decision, or bearer. A dedicated
NOLOGIN definer owns three function-only operations. The Runtime login may
issue and redeem through two of them but has no locator-table privilege. A
restricted security-operator login may invoke only exact-Organization cleanup,
which uses database time and deletes digest lineage only after the fixed
profile `retain_until`. FORCE RLS and exact same-Organization foreign keys
remain mandatory.

Redemption is multi-use and content-free. It returns at most one `CandidateRef`
plus prior Package/Evidence location lineage; it does not return content or an
authorization receipt. Database time decides issuance and expiry. Missing,
expired, forged, cross-kind, cross-Organization, disabled, tombstoned, or stale
location lineage maps to the same internal not-available condition without
mutation.

Every active `OpenCitation` obtains a new current `UserActor` transaction and a
new trusted direct or private delivery context. For private delivery, the HTTP
metadata carries a new request-bound `DeliveryEvidenceRef` whose purpose is
`citation.open`; trusted audience facts never enter the body. Runtime computes
the current full trusted scope, feeds the redeemed `CandidateRef` through the
same sealed Kernel locator, scope, field projection, budget, provenance, final
epoch, and audit gates, and never calls candidate discovery. A successful open
produces a replacement audience-bound `ContextPackage`, fresh citation locator,
matching `EgressGrant`, and authorized `ContextRun`. The retained query digest
uses the fixed semantic value `citation.open`, never the locator bearer.

If the locator or current authorization yields no Evidence, Runtime persists
only the existing generic delivered-empty ContextRun/DecisionAudit lineage and
returns `citation_not_available`. It issues no egress grant and exposes no
existence detail. A denied open does not consume, refresh, or otherwise mutate
the original locator, so a later authorized opener can succeed.

Issue #69 activates only private/direct File citation issuance and opening over
the public HTTP v0 contract and generated TypeScript SDK. Group/public
`AudienceSnapshot`, non-File provider citation semantics, and Continue remain
`NOT_ACTIVE`.

## Rationale

Location is sufficient to restart authorization; carrying any previous
decision would create a second authorization system. Multi-use locators make
retries deterministic while current-transaction reauthorization makes every
open independently revocable. Including the locator in Package integrity
prevents substitution without turning it into authority.

## Consequences

- Reader A may issue and reopen a locator; reader B receives only the generic
  unavailable outcome; reader A may still reopen it afterward.
- Every content-bearing open crosses `CandidateRef`, `AuthorizationKernel`, and
  `AuthorizedProjection` before a replacement Package or grant exists.
- Locator database outages are service unavailability, while validly decided
  misses and denials are `citation_not_available`.
- Rollback refuses while locator lineage remains; the dedicated security
  operator cleans it only after the versioned citation retention window.
- Ordinary traces and public responses contain neither the locator bearer nor
  prior trusted authorization facts.

## Revisit trigger

Revisit before activating group/public citation delivery, a non-File provider's
locator semantics, a different retention profile, or a public citation contract
that cannot preserve the same generic denial and sealed Kernel path.
