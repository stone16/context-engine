---
name: adr-0017-trusted-invocation-and-closed-runtime-access
version: "1.0.0"
description: >
  Fix trusted invocation construction and the closed HTTP, generated SDK, and
  optionally activated MCP access set over one Runtime contract.
---

# 0017. Trusted invocation and closed Runtime access surfaces

- Status: accepted
- Date: 2026-07-19
- Refines: ADR-0002, ADR-0012, ADR-0013

## Context

Organization, Principal, Membership, purpose, delivery audience, and ACL facts
are authorization inputs. Accepting them from an ordinary request body lets a
caller choose the security context under which its own request is evaluated.
Implementing policy separately in HTTP, an SDK, MCP, or IM would also create
multiple security contracts with different rejection and disclosure behavior.

## Decision

Every activated public access surface maps to the same sealed
`ContextRuntime.resolve(AuthenticatedInvocation, TrustedDeliveryContext,
Acquire | Continue | OpenCitation)` contract and returns the same closed
`ResolutionOutcome` semantics.

The access set is closed:

- HTTP is the V1 server ingress.
- The generated TypeScript SDK is a typed HTTP client artifact, not another
  server transport or domain implementation.
- MCP is an optional server ingress that remains `NOT_ACTIVE` until a real
  caller and the full parity/security suite justify activation.
- IM is not a fourth transport. BotDelivery is an external Runtime caller and
  uses the generated HTTP SDK.

Request bodies contain only the closed untrusted request union. They cannot
supply Organization, Principal, Membership, trusted purpose/audience facts,
ACLs, raw SQL, placement, source mode, `AuthenticatedInvocation`,
`TrustedDeliveryContext`, or bypass controls.

A trusted ingress Adapter constructs `AuthenticatedInvocation` from verified
session, token, mTLS, or OAuth bindings. It constructs
`TrustedDeliveryContext` from authenticated route/application evidence or
redeems a short-lived opaque `DeliveryEvidenceRef` from authenticated transport
metadata. Remote callers never serialize raw trusted identity or audience
claims into the body. Failure to construct or redeem all required trusted
context stops before provider, index, model, Package, or effect work.

## Rationale

One Runtime contract keeps authorization, error equivalence, security fields,
and audit behavior independent of transport syntax. Server-side construction
of trusted inputs prevents untrusted callers from selecting their tenant or
authority. Distinguishing the SDK client from a server ingress also prevents a
second domain implementation from emerging in TypeScript.

## Consequences

HTTP and the generated SDK must share canonical schema and behavior tests. MCP,
if activated, joins that same suite before it can claim capability. Adding a
transport is a security-boundary change, not an Adapter-only convenience.
Authentication and evidence-redemption failures are generic and perform no
content work.

## Revisit trigger

Reopen the closed set only when a real caller cannot use an existing activated
surface and provides a concrete protocol requirement. Activation requires a new
ADR or explicit refinement, threat-model update, trusted-context construction,
canonical Package/error parity, denied-versus-missing equivalence, and negative
fixtures proving that untrusted fields cannot become authority.
