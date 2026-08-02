---
name: adr-0098-expose-current-agent-operations-without-granting-authority
version: "1.0.0"
description: >
  Expose a generated short-lived manifest of the operations an authenticated
  agent consumer can invoke without making discovery an authorization grant.
  Use when designing capability-aware agent operation discovery. Not for
  handwritten route catalogs, resource enumeration, or bypassing invocation-
  time authorization.
---

# 0098. Expose current agent operations without granting authority

- Status: accepted
- Date: 2026-08-02
- Refines: ADR-0017, ADR-0028, ADR-0047, ADR-0048, ADR-0088

## Context

The generated TypeScript SDK gives a caller one reliable compile-time contract,
but an agent consumer still has to know in advance which operations its current
authenticated application and the served deployment can actually invoke.
Supplying every possible operation in a prompt wastes context and encourages
attempts to call inactive carriers. A handwritten self-API catalog would improve
discovery while creating a second route and documentation truth beside OpenAPI
and the generated SDK.

Discovery is also easy to confuse with authorization. A list derived from one
token or deployment cannot prove that the Principal may read any Source,
Resource, field, citation, or delivery audience. Availability may change after
the list is produced, and every invocation must still reconstruct current
trusted inputs and traverse the sealed Runtime or effect boundary.

## Decision

1. A future versioned public contract exposes one server-authored
   `AgentOperationManifest` through the same authenticated HTTP ingress and
   generated SDK as the operation it describes. It is a short-lived discovery
   document, not a signed capability, bearer, grant, ticket, or authorization
   decision. OpenAPI v0 remains frozen; this decision activates no endpoint.
2. Operation metadata has one semantic source. Operation ids, generated request
   and outcome type references, and concise agent guidance come from the
   reviewed public contract. Runtime availability comes from the server-owned
   closed capability registry and exact deployment composition. The manifest is
   generated from those sources; no handwritten parallel route catalog or
   duplicated request schema is permitted.
3. The listed set is the intersection of contract-supported operations, active
   server carriers, and policy for the authenticated application identity at
   ingress. The manifest is also bound to the exact authenticated `AgentVersion`
   used by the request so it cannot be replayed across versions. This binding
   introduces no second application profile or delegation ceiling:
   `AgentVersion` retains its existing narrowing-only meaning. Unknown or
   inactive operations are omitted. The manifest never predicts per-Resource
   authorization and never widens the Principal, Membership, AgentVersion,
   purpose, audience, or EffectiveScope.
4. The public document contains only its schema/contract checksum, an opaque
   binding digest for the authenticated application and AgentVersion, issued-at
   and expiry times, and the closed operation entries required to select a
   generated SDK method. It contains no raw token, Organization or Principal
   claim, grant, Source/Resource identifier, audience membership, denied reason,
   carrier secret, provider configuration, internal route, score, or
   unavailable-operation inventory.
5. Consumers discard the manifest at expiry and refresh after a generic
   operation-unavailable result. They do not cache it as session authority.
   Invoking a listed operation still performs current authentication, trusted
   ingress construction, server capability gating, exact authorization,
   Package validation, and any required egress or effect authorization.
6. The generated SDK exposes a narrow typed discovery facade. Compile-negative
   and package tests continue to forbid raw generated-client imports, arbitrary
   headers, caller-authored trusted fields, and structurally invented manifest
   entries. An agent prompt may render the returned operation guidance, but the
   prompt is not the contract authority.
7. The first implementation slice, when separately activated, is read-only and
   reports only operations already active for one registered authenticated
   application. It does not activate `Continue`, public-group delivery, MCP,
   structured acquisition, Control, Learning publication, or any external
   effect.

## Rationale

Agents benefit from seeing the smallest current tool surface, while generated
contract ownership prevents the discovery UX from drifting away from the wire.
Treating the result as expiring descriptive data preserves the more important
property: possession or display of an operation name never authorizes its use
or any content reachable through it.

## Consequences

- Agent consumers can discover current operations without embedding the whole
  API surface or shipping consumer-specific handwritten catalogs.
- A deployment composition can remove an inactive operation from new manifests
  without changing the immutable meaning of an already generated method.
- Contract versioning, SDK generation, server registry parity, expiry behavior,
  generic unavailability, and zero resource enumeration become required tests
  for an activation issue.
- Until that issue publishes a new contract version and evidence, the operation
  manifest remains `NOT_ACTIVE`.

## Revisit trigger

Revisit when the first implementation proves that omission alone gives agents
insufficient recovery guidance, when operation parameters need dynamic
server-owned constraints that OpenAPI cannot express safely, or when a second
authenticated application demonstrates that operation exposure needs a
versioned policy model beyond existing ingress route policy. Any revision must
keep discovery non-authorizing, must not redefine AgentVersion, and must remain
generated from one contract plus the server-owned active registry.
