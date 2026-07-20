---
name: adr-0021-stage-http-authentication-before-provider-selection
version: "1.0.0"
description: >
  Automate the HTTP trusted-invocation boundary without selecting or implying
  a production identity provider or activating Runtime delivery.
---

# 0021. Stage HTTP authentication before identity-provider selection

- Status: accepted
- Date: 2026-07-21
- Refines: ADR-0017

## Context

ADR-0017 requires authenticated ingress to construct
`AuthenticatedInvocation` from verified transport or session facts while the
ordinary request body remains untrusted and closed. The first executable slice
must prove this trust boundary before a production OAuth, JWT, mTLS, or session
provider has been selected. It also precedes Membership validation,
`TrustedDeliveryContext`, Runtime authorization, and ContextPackage delivery.

Accepting identity-shaped headers or optional internal body fields would let a
caller manufacture trusted facts. Treating a deterministic test credential as
production authentication would instead claim a capability that the repository
does not have.

## Decision

`POST /v1/context:resolve` first activates a bounded trust-boundary seam. An
injected `Authenticator` maps one opaque credential to a nominal, immutable
`VerifiedAuthenticationContext`; the HTTP adapter then constructs the nominal,
immutable `AuthenticatedInvocation`. Neither type is a request-body model. The
module-level production composition uses a rejecting authenticator until a
later issue selects and verifies an owning identity provider.

The request body is a recursively closed acquire shape containing only
`kind = acquire` and `need.query`. Unknown fields and duplicate JSON object keys
fail before the invocation observer. Duplicate singleton authentication,
content-type, or correlation headers also fail closed. Transport syntax errors
use a generic `400 invalid_request`; authentication failures use a generic
`401 authentication_failed`; closed-schema failures use a generic
`422 invalid_request`. These bodies and statuses are part of OpenAPI.

A successful test-only request stops immediately after an injected observer has
received the trusted invocation and returns no content. The correlation header
contributes a request identifier only; it supplies no Organization, Principal,
Membership, Agent, ACL, purpose, audience, or authorization fact.

## Rationale

A nominal adapter boundary makes the trusted construction point observable
without mixing caller-controlled fields into the same Pydantic model. A
reject-all production composition keeps capability reporting honest while the
deterministic test composition provides executable evidence for failure
equivalence and zero downstream work.

## Consequences

Tests can prove exactly one trusted invocation for valid authentication and zero
domain calls for rejected requests, including conflicting identity injection
and unknown-field smuggling. The default application remains fail closed even
if it receives a syntactically valid opaque credential.

The observer success response is evidence for this implementation slice, not a
ContextPackage or an activated `ContextRuntime.resolve` contract. Production
authentication, Membership lookup, trusted delivery construction,
authorization, provider/index work, and ContextPackage delivery remain
`NOT_ACTIVE` and cannot be inferred from this seam.

## Revisit trigger

Revisit when an owning production authentication mechanism is selected or when
the first Runtime delivery issue replaces the observer. Preserve the closed
body, generic failure, duplicate-input, OpenAPI, and zero-downstream-call
oracles when either boundary advances.
