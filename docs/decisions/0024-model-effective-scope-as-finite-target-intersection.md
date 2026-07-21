---
name: adr-0024-model-effective-scope-as-finite-target-intersection
version: "1.0.0"
description: >
  Activate the first EffectiveScope oracle as a finite intersection over typed
  Organization/Source/Resource targets, with seven required trusted operands
  and optional RequestNarrowing as an explicit identity-or-filter input.
---

# 0024. Model the first EffectiveScope as finite target intersection

- Status: accepted
- Date: 2026-07-21
- Refines: ADR-0012, ADR-0013, ADR-0017, ADR-0023

## Context

Issue #12 activates the first EffectiveScope decision before ContextEngine has
content candidates, durable Principal grants, Source ACL evidence, Resource ACL
rows, field projection, or a permission-language hierarchy. The implementation
authority fixes the security algebra: seven trusted constraints are mandatory,
optional RequestNarrowing can only reduce their intersection, and a missing or
empty trusted constraint fails closed. It does not yet fix the concrete atom
over which synthetic M0 scopes intersect.

A plain string set would conflate Organization, Source, Resource, and future
field identifiers. Treating an omitted RequestNarrowing as missing trusted
authority would also contradict the accepted identity-element semantics.
Conversely, adding roles, wildcards, inheritance, sensitivity lattices, or a
policy DSL now would pre-empt the exact-authorization and source-schema issues
that own those contracts.

## Decision

### Finite typed target domain

The M0 oracle operates on immutable finite `ScopeTarget` values. Each target
contains one exact Organization identifier, one exact opaque Source reference,
and either one exact opaque Resource reference or no Resource reference. The
Organization is part of the atom, so equal Source or Resource spellings in two
Organizations are never the same authorization target.

This target is only the minimum synthetic authorization domain for Issue #12.
It has no wildcard, prefix, hierarchy, role, action, sensitivity, field, or
implicit parent semantics. A Source-level target and a Resource-level target
are distinct exact atoms; neither grants the other. Issue #13 and later may
refine the target language when real CandidateRef, SourceAclEvidence,
ResourceACL, and projected-field contracts exist, but must preserve monotonic
intersection.

### One explicit intersection oracle

The sole pure oracle receives seven explicitly named trusted operands:

1. Organization boundary;
2. Membership rights;
3. Principal grants;
4. AgentVersion delegation ceiling;
5. Source-native ACL;
6. Resource ACL;
7. purpose policy.

Every operand is either an exact finite ScopeSet or the explicit
`MissingTrustedScope` value. Python `None` never means unrestricted. Missing or
empty in any required operand yields an empty EffectiveScope. Otherwise the
oracle intersects all seven sets. AgentVersion is a ceiling, never an identity
or independent grant, so widening it cannot exceed any other operand.

RequestNarrowing is a separate closed input. Omission maps to the explicit
`OmittedRequestNarrowing` identity value only after all seven trusted operands
have been established. A supplied `sourceRefs` set filters targets by exact
Source reference, a supplied `resourceRefs` set filters targets by exact
Resource reference, and supplying both applies both filters. Well-formed
unknown, denied, over-broad, or cross-Organization references merely intersect
to the same or a smaller result and reveal no existence detail. Malformed
shapes, empty lists, duplicates, unknown fields, or attempted union/bypass
operators are rejected by the closed RequestNarrowing contract before Runtime.

The immutable result has a canonical order-independent digest for restricted
decision observation. The HTTP response, ContextPackage, public error, and
OpenAPI response graph never serialize the target set or digest.

### Trusted binding and Runtime seam

Trusted scope state is carried by a nominal request-bound proof constructed by
a trusted authority, not by the Acquire body. The proof binds the seven
operands to the exact Organization, Membership/version, Principal,
AgentVersion, purpose, request identifier, authentication binding, and trusted
decision time. Runtime validates that tuple at its public seam while the
current Membership authority transaction remains active.

Issue #12 uses deterministic trusted-state fixtures rather than inventing
durable grant/ACL tables. The production-safe default supplies explicit missing
trusted scope and therefore an empty decision. The sealed AuthorizationKernel
always invokes the same oracle; transports and databases do not implement
parallel scope logic. A test-only decision observer may receive only the
effective digest, target count, and empty state. It cannot alter the decision
or receive concrete identifiers.

### External behavior and exclusions

The Runtime output remains the tenant-safe evidence-free empty ContextPackage.
A narrower, empty, or missing-scope decision performs zero index, Provider, or
source-content I/O. Agent, Principal, Membership-rights, ACL, purpose-policy,
or precomputed-scope fields remain forbidden in the body; RequestNarrowing is
the only caller-authored scope input.

This decision does not activate durable grant or Agent schemas, role
hierarchies, real SourceNativeACL/ResourceACL evidence, Candidate retrieval,
AuthorizedProjection, field projection, ranking, Policy Epoch, audience scope,
or Evidence delivery. Those remain owned by later issues.

## Rationale

An exact typed finite set is the smallest model that can prove intersection,
missing-input absorption, Agent ceiling, and request monotonicity without
inventing the later permission language. Explicit missing and omitted values
make the trust distinction executable. Binding a nominal proof to the complete
request tuple prevents a trusted-state snapshot from becoming reusable under a
different actor, AgentVersion, purpose, or authentication.

## Consequences

Property tests can generate arbitrary finite target graphs and prove
commutativity, idempotence, empty absorption, and monotonic non-expansion. HTTP
tests can prove that only closed RequestNarrowing reaches the oracle and that
scope identifiers remain absent from every public shape. The first active
scope decision is deliberately synthetic and content-free; a green result does
not claim exact content authorization.

## Revisit trigger

Revisit when Issue #13 defines CandidateRef and the first exact-authorized
synthetic Evidence, when a durable Principal/Membership/Agent grant schema is
introduced, or when field/sensitivity/purpose semantics require a structure
beyond exact finite targets. Any refinement must keep Organization in the atom,
seven trusted operands mandatory, omitted RequestNarrowing as identity, and all
caller/Agent changes monotonic non-expanding.
