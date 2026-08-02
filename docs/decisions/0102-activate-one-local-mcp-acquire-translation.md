---
name: adr-0102-activate-one-local-mcp-acquire-translation
version: "1.0.0"
description: >
  Activate one spawn-per-session local stdio MCP Acquire translator that calls
  only the existing loopback HTTP resolve carrier and returns its exact closed
  outcome. Use when a maintainer's local coding-agent host needs one authorized
  ContextPackage. Not for remote or multi-user MCP, trusted caller fields,
  generation, effects, citation opens, continuation, or egress-grant handling.
---

# 0102. Activate one local MCP Acquire translation

- Status: accepted
- Date: 2026-08-02
- Refines: ADR-0002, ADR-0008, ADR-0013, ADR-0045, ADR-0046, ADR-0047, ADR-0052,
  ADR-0063, ADR-0064, ADR-0068, ADR-0075

## Context

Issue #215 names the maintainer's local MCP-capable coding-agent host as the
first real MCP workload: ask one repository-context question and receive the
same authorized `ContextPackage` available through loopback dogfood HTTP. The
existing authorities already fix the security shape. `ContextRuntime.resolve`
is the sole read path; the local dogfood bearer maps to one complete fixed
`AuthenticatedInvocation`; an authentication context without a private binding
constructs `DirectDelivery`; and any model or channel disclosure remains
separately governed by a matching `EgressGrant` outside an ingress adapter.

They did not select an MCP transport, process lifetime, or public tool contract.
A remote carrier would require production authentication and
`DeliveryEvidenceRef`; embedding an MCP transport in the API would add protocol
state and dependencies to the authorization process without isolation or
performance evidence. The issue #215 process-boundary measurement shows that
the real host's required stdio child loads the MCP protocol dependency while
transitively loading zero engine, database-client, or Provider modules. A local
child can therefore translate stdio to the already served loopback seam while
owning no authorization or content lifecycle.

## Decision

1. The first active MCP caller is the maintainer's local MCP-capable
   coding-agent host. It spawns `context-engine-mcp` once per host session over
   stdio. The child exits with the session and owns no persistence, index,
   cache, Package reuse, session identity, or independent service endpoint.
2. The server exposes exactly one tool, `context_resolve`. Its input schema is
   the existing closed HTTP `AcquireWire`: `kind: acquire`, `ContextNeed`, an
   optional `PackageBudget`, and optional `RequestNarrowing`. It contains no
   Organization, User, Principal, Membership, Agent, application, purpose,
   audience, ACL, `AuthenticatedInvocation`, `TrustedDeliveryContext`,
   `DeliveryEvidenceRef`, `EgressGrant`, ticket, or caller credential.
3. Each tool call creates a fresh request id and delegates only to the loopback
   dogfood client's `POST /v0/resolve`. The MCP module cannot import `engine`,
   call a Provider, read PostgreSQL, hydrate a `CandidateRef`, construct trusted
   facts, assemble a Package, or reinterpret a refusal. Every content byte
   therefore remains on the existing
   `CandidateRef -> AuthorizationKernel -> AuthorizedProjection -> ContextPackage`
   path with PackageBudget, provenance, Policy Epoch, release and audit gates.
4. Tool structured content is the exact recursively closed HTTP
   `ResolutionOutcome` document with zero MCP-specific fields, wrapper values,
   renamed states, or omission. The MCP output-schema carrier adds only the MCP
   protocol's required top-level `type: object` marker around the unchanged
   existing outcome union. Both input and output are validated with the HTTP
   contract models. Denied, missing, unavailable and empty outcomes retain the
   HTTP carrier's generic non-enumerating behavior.
5. The server loads the existing loopback base URL and single-operator dogfood
   bearer only from its own environment. The host cannot pass either through a
   tool call. The caller disables environment proxies, refuses redirects,
   rejects secret material in requests and decoded responses, never represents
   or logs the secret, and maps configuration, authentication, transport and
   malformed-response failures to one content-free MCP tool error.
6. ADR-0063/0068 own identity mapping and rotation: the bearer establishes the
   exact configured Organization, User, current Membership/version, Principal,
   Agent, application and authentication binding, and secret rotation also
   rotates derived query-digest material. MCP adds no session-to-identity
   mapping. This remains one local human and is disabled for a second user or
   non-loopback exposure.
7. Delivery is `DirectDelivery` to the one authenticated local agent consumer.
   The exact HTTP document may carry its opaque existing `egressGrant` field,
   and the MCP adapter passes it only inside that unchanged outcome. The adapter
   does not accept a grant from the host, inspect, redeem, cache, extract, or
   forward it to another service or channel, and assigns it no MCP semantics.
   Any host-to-model or host-to-channel disclosure remains outside this adapter
   and must use the existing current-Package, matching-grant controlled egress
   seam. MCP activation grants no arbitrary host permission to forward
   cleartext.
8. `Continue`, `OpenCitation`, generation, ModelGateway, Sender,
   `ActionPlane.prepare`/`perform`, group/public dual resolve, remote HTTP MCP,
   multi-tenant or multi-user identity selection, `DeliveryEvidenceRef`, MCP
   egress-grant handling, resource browsing, prompts and resources are explicit
   `NOT_ACTIVE` capabilities. They are not inferred from the HTTP union or the
   MCP SDK and require separate callers, decisions and parity evidence.

## Rationale

Stdio matches the real local host and confines MCP protocol state to a
disposable child. Reusing the frozen HTTP models and public caller makes MCP a
transport translation, not a second delivery contract. Reusing the bounded
dogfood authenticator is the narrowest honest identity composition; treating it
as a general MCP credential would erase the constraint that makes it safe.

## Consequences

- A local MCP client can obtain one current expiring direct-delivery Package
  while the default API remains reject-all and MCP cannot reach Runtime
  internals.
- MCP and HTTP parity is structural: one model defines both schemas and the
  adapter returns the validated HTTP document without semantic projection. The
  frozen digest, nullable-field, ref-limit and projected-field primitives have
  one transport-neutral `context_engine_contracts` owner imported by both HTTP
  and Runtime, so the engine-independent MCP import graph creates no duplicate
  security contract.
- The MCP Python SDK is locked at `mcp==2.0.0` in `uv.lock` and used only for
  protocol framing and validation; it receives no database or engine authority.
  No upstream source was copied into this repository, and the runtime wheel
  remains subject to the normal build artifact license inventory outside
  ADR-0074's vendored-source register.
- A separately spawned process adds startup and local loopback overhead. That is
  bounded to one host session and buys protocol isolation without a deployed
  service, database, or index.

## Revisit trigger

Revisit before any second human, remote or shared host, non-loopback transport,
private/group audience, production authentication, Package cache, `Continue`,
`OpenCitation`, model/channel egress inside the adapter, or additional MCP tool,
prompt, or resource. Remote delivery additionally requires the ordered
production-authentication and `DeliveryEvidenceRef` gates rather than widening
the dogfood credential.
