---
name: adr-0028-fail-closed-unavailable-runtime-capabilities
version: "1.0.0"
description: >
  Close the Runtime request union and activate deterministic M0 refusals for
  declared operations whose real production carriers do not yet exist.
---

# 0028. Unavailable Runtime capabilities fail closed before content I/O

- Status: accepted
- Date: 2026-07-21
- Refines: ADR-0012, ADR-0013, ADR-0017, ADR-0019, ADR-0027

## Context

The public Runtime contract deliberately names `Acquire`, `Continue`, and
`OpenCitation`, while M0 implements only the authorized `Acquire` carrier.
Later-carrier acceptance fixtures must still execute now. Skipping them would
leave release vetoes untested; treating them as empty success would imply an
authority or delivery capability that does not exist.

The same problem applies when a server-owned `Acquire` plan requires a
federated Provider or source-native ACL capability absent from the active
composition. A Provider credential, an index hit, or stored bytes cannot turn
that missing capability into authorization.

## Decision

The public request union is closed and version-compatible:

- `Acquire`, `Continue`, and `OpenCitation` are the only declared wire
  variants. Their request shapes are explicit and unknown fields are forbidden.
- A known declared variant whose carrier is unavailable reaches the server-
  owned `RuntimeCapabilityGate.require_available(RuntimeCapability)` before any
  Provider, index, or source-content I/O. `Continue` returns the generic
  non-retryable `request_not_available` outcome; `OpenCitation` returns the
  generic `citation_not_available` outcome. Both use HTTP 200 because they are
  valid requests with domain-level unavailable outcomes.
- An `Acquire` request never declares capabilities. Only trusted server-owned
  planning may require federated or source-native behavior. When that required
  capability is absent or undeclared by the active composition, the same gate
  returns generic non-retryable `request_not_available` before content I/O.
- An unknown wire variant, an undeclared caller-authored capability field, or
  any other closed-schema violation is malformed input and returns the generic
  HTTP 422 validation response. It is not normalized into a known capability
  outcome.

Authenticated HTTP ingress still establishes an existing Organization and a
current Membership before Runtime. For a server plan known to be unavailable,
it deliberately does not call the configured scope authority: that authority
may itself perform source-native I/O. Instead ingress constructs a request-
bound all-missing `TrustedScopeSnapshot`, and the sealed Runtime performs a
content-free Kernel preflight over trusted identity/delivery, empty policy,
finite budget, current Policy Epoch, provenance, and restricted audit. The
normal configured scope authority remains mandatory for an active Acquire
plan. Runtime independently repeats the capability check, so this ingress
optimization cannot mint an outcome or authorize content.

The resolve route accepts no query parameters. Any query string—including a
caller-authored source mode or capability request—is a closed-schema violation
and receives the same generic HTTP 422 response.

Internally, the gate records the safe typed category
`UNSUPPORTED_CAPABILITY` through a restricted audit seam. The public response
never serializes that category, the requested token/locator, a carrier name,
Provider/resource existence, or any protected detail. Durable
`DecisionAudit` remains `NOT_ACTIVE`; the M0 audit carrier is a restricted
in-process category counter and is absent from the public `ResolutionOutcome`.

This decision activates only refusal behavior. `ACCEPT-005`, `ACCEPT-009`, and
`ACCEPT-010` retain `statusAtM0: future` and `m0Expectation: fail_closed`.
Continuation issuance/redemption, citation redemption, federated/source-native
Provider behavior, and File publication are not real carriers. A later owner
may implement one without changing the public request union, but must replace
that fixture's generic refusal with its complete current-authorization oracle
and independently activate the carrier.

## Rationale

One mandatory server-owned gate makes absence of capability a security veto
instead of an adapter convention. Separating valid-but-unavailable domain
outcomes from malformed request validation keeps the wire contract predictable
without exposing which resource or carrier exists. Keeping all real carriers
future avoids turning executable M0 negative tests into false feature claims.

## Consequences

- Every unavailable Runtime path has deterministic Runtime and HTTP evidence,
  including zero scope-authority, Provider, index, and source-content calls.
- Existing authorized and empty `Acquire` behavior is unchanged when the
  server-owned plan requires only active capabilities.
- Caller input cannot select a Provider or manufacture a capability plan.
- Restricted diagnostics can count a stable internal cause without making it a
  public enum or tenant-visible denial record.
- Adding a real Continue, citation, or federated/source-native carrier does not
  require an incompatible request-union change.

## Revisit trigger

Revisit when a real continuation, citation, federated/source-native Provider,
or File publication carrier activates, or when durable `DecisionAudit` owns the
restricted category. The replacement must preserve the closed union, server-
owned capability planning, pre-I/O gate, generic non-enumerating outcomes, and
all zero hard oracles.
