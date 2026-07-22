---
name: adr-0022-stage-tenant-safe-empty-context-package
version: "1.1.0"
description: >
  Activate the first evidence-free Runtime outcome without manufacturing later
  Membership, policy-epoch, release, audit, or egress authority.
---

# 0022. Stage the tenant-safe empty ContextPackage

- Status: accepted
- Date: 2026-07-20
- Refines: ADR-0012, ADR-0017, ADR-0020, ADR-0021

## Context

The first public Runtime tracer bullet must replace ADR-0021's successful
invocation-observer response with an executable `ContextRuntime.resolve`
Acquire path. It precedes Membership and ActorContext validation, exact content
authorization, Policy Epoch persistence, ContextRun/PackageRecord persistence,
release selection, and signed per-hop egress grants.

The accepted security catalog describes the eventual complete resolved outcome,
including fields owned by those later capabilities. Emitting random values for
those fields now would turn absent authorization and publication behavior into
apparently valid authority. Conversely, accepting purpose or Organization in
the Acquire body would weaken the trusted-ingress boundary already established
by ADR-0017 and ADR-0021.

Issue #10 also calls the no-evidence condition a typed gap. The machine catalog
represents that condition as `coverage.reason = no_authorized_evidence` while
reserving the `gaps` collection for provider, capability, freshness, or budget
shortfalls. Adding denied-object details to either location would create an
existence side channel.

## Decision

`POST /v1/context:resolve` maps one authenticated request to exactly one sealed
Runtime `resolve` call and returns a `resolved` outcome containing the staged
ContextPackage. The closed Acquire body contains a nonblank context question,
an optional caller PackageBudget ceiling, and optional source/resource
narrowing. Purpose remains a server-owned direct-route policy fact and is
constructed as nominal trusted delivery context; Organization and all other
trusted identity, audience, ACL, and delivery facts remain forbidden in the
body.

The staged package contains a fresh server-authored opaque Organization
reference, trusted purpose, positive TTL and matching UTC expiry, UTC as-of
time, fresh opaque decision reference, empty blocks and Evidence, zero budget
usage, an empty gap list, and typed empty coverage with reason
`no_authorized_evidence`. The outbound Organization reference is package-scoped
and differs from the trusted internal Organization identifier. Because the
Acquire schema does not accept it and Runtime accepts only the trusted ingress
operand, it cannot be replayed as tenant authority.

Runtime owns a finite four-dimensional server PackageBudget profile. An omitted
caller ceiling inherits every server dimension; a partial ceiling inherits
omitted dimensions; every supplied dimension is intersected with `min`. The
effective ceiling remains Runtime decision data in this slice while the package
records actual zero usage.

The empty path performs no index, provider, or source-content operation. The
sealed Runtime nevertheless holds concrete fail-fast content ports so an
instrumented conformance composition can prove that the actual call graph used
none of them. It still crosses the non-pluggable AuthorizationKernel. With zero
CandidateRefs the Kernel has no content to project, but its fixed policy,
finite-budget, provenance, and safe in-memory audit gates each produce a
receipt consumed by package construction. Durable decision lineage remained
owned by its later issue at this decision's original activation.
[ADR-0031](0031-persist-authorized-context-run-lineage.md) now refines the
successful current Acquire path: its final ContextRun and the generic
delivered-empty DecisionAudit commit in the retained UserActor transaction
before the response.

Before nominal `AuthenticatedInvocation` construction, a trusted
Organization-authority sub-boundary must attest that the Organization exists.
Its nominal proof is bound to the authenticated Organization, authentication
binding, request identifier, and verification time; any absent, malformed, or
mismatched proof becomes the same generic authentication failure before
Runtime. The module-level application composes rejecting authentication and
Organization authorities. Deterministic and seeded-real-PostgreSQL authorities
are conformance twins only; they are not production identity or database
authority.

This issue does not expose ADR-0020's Organization-only database evidence
transaction through Runtime. Complete request database binding waits for Issue
#11's authenticated Membership and closed UserActor context. The real
PostgreSQL positive conformance seeds an Organization using the migration role,
then verifies the HTTP empty-package behavior; the inherited missing-GUC and
pool-reset tests independently preserve #8's fail-closed database evidence.

This staged outcome deliberately did not emit a placeholder EgressGrant,
Policy Epoch, audience digest, release/tokenizer reference, Package digest, or
persistent run/audit reference. ADR-0027 subsequently activated the real
Organization V0 Policy Epoch, while ADR-0031 adds the real Package digest and
digest-only persistent run/audit lineage. EgressGrant, audience digest,
release, tokenizer, and any full-Package retention remain unavailable until
their owning issues add real values and update the public contract and
canonical catalog carrier together.
The outbound references use closed prefix plus independent server entropy,
reject embedded trusted Organization identifiers, and reject reuse within a
Runtime instance. The opaque decision reference identifies the evidence-free
Runtime decision and, after ADR-0031, resolves through the exact
same-Organization durable `ContextRun` operator path.

## Rationale

The smallest honest package makes the online boundary executable while keeping
all unavailable authority visibly unavailable. Typed coverage communicates a
useful empty result without suggesting that a Provider failed or that a denied
resource exists. A nominal server-built delivery operand preserves the same
trust direction needed by future remote DeliveryEvidenceRef redemption.

Keeping the Issue #8 database transaction out of the activated Runtime also
preserves ADR-0020: Organization-only GUC binding is isolation evidence, not a
complete request ActorContext. This avoids granting the runtime role a new
global Organization enumeration capability just to validate a fact already
established by trusted authentication.

## Consequences

Contributors can execute and inspect the first canonical empty Package through
an actual listening conformance process, test bounded caller-only narrowing and
budget intersection, and prove zero content I/O. The default deployed process
remains fail closed because both trusted ingress authorities reject, even
though the conformance composition exercises the real Runtime.

The response contract will grow when owning security capabilities become real.
Those additions may not remove or reinterpret the fields established here, may
not convert the outbound Organization reference into input authority, and may
not populate `gaps` with denied counts, names, identifiers, or hints.

## Revisit trigger

Revisit when a real delivery caller requires a signed EgressGrant, or when the
owning release, PackageRecord body-retention, or later audit carriers activate
their fields. Membership/UserActor binding, Organization Policy Epoch, Package
digest, and current Acquire run lineage are now active only within their
bounded ADR-0023, ADR-0027, and ADR-0031 scopes. Preserve
the closed body, server-authored purpose and Organization reference, finite
budget intersection, zero-content-I/O empty path, and no-enumeration coverage
semantics.
