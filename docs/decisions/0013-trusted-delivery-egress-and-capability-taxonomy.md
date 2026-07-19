---
name: adr-0013-trusted-delivery-egress-and-capability-taxonomy
version: "1.0.1"
description: >
  Put group audience facts, egress, citation, continuation, and write effects
  into explicit trusted types and deep delivery/action Modules.
---

# 0013. Trusted delivery context, egress grants, and distinct capabilities

- Status: accepted
- Date: 2026-07-18
- Refines: ADR-0002, ADR-0003, ADR-0011

## Context

ContextEngine treats RequestNarrowing as caller-controlled input, so it cannot
carry trusted group scope. Group membership, destination binding, and history
exposure must instead arrive as trusted facts and be evaluated by the Kernel.
The threat model also requires reusable citation location, one-shot
continuation, model/channel egress, and each external write effect to have
different authority and replay semantics. Conflating those capabilities would
permit scope escalation, cross-hop reuse, or replay of an unintended effect.

## Decision

Trusted ingress Adapters construct TrustedDeliveryContext. Group delivery
contains an AudienceSnapshot with complete membership facts, provider epoch,
expiry, destination binding, audience digest, and platform history-exposure
semantics. The snapshot contains facts, not scope. AuthorizationKernel computes
the asker and all-members intersection; group audience never enters
RequestNarrowing.

Remote BotDelivery never serializes trusted identity or audience claims in the
request body. A trusted identity Adapter issues an opaque, short-lived
DeliveryEvidenceRef bound to the authenticated service, resolve request id,
Organization, asker, destination, purpose, audience digest, and expiry. The SDK
carries it in authenticated transport metadata; trusted ingress redeems it into
TrustedDeliveryContext. Public and private resolves use separate references.
Forgery or replay outside the bound request fails before content work.

Public and private replies use separate audience-bound resolve calls and
ContextRuns. An incomplete, stale, or unbound audience returns zero public
content. If future members can read history and the future audience cannot be
bounded, protected body text is private-only.

Trusted ingress, BotDelivery, ModelGateway, ActionPlane, and Sender form the
delivery TCB. Runtime issues a per-hop EgressGrant bound to Package digest,
provider, audience, purpose, region, retention, sensitivity, epoch, and expiry.
Wrong-hop use produces zero outbound bytes.

Capabilities are distinct:

- DeliveryEvidenceRef is an ingress attestation locator, not an authorization
  grant, and carries no raw audience claims.
- ContinuationToken is principal/audience-bound, short-lived, one-shot, and
  cumulative-budget; redemption returns a replacement Package.
- CitationOpenRef is a multi-use opaque locator, not authority. Every opener
  authenticates anew and receives current exact authorization.
- EgressGrant permits one Package hop but no write.
- ContextAccessTicket reads only.
- ActionTicket permits one write effect, payload digest, destination, audience,
  epoch, nonce, expiry, and idempotency key.

The trusted Bot application contains a deep BotDelivery Module with answer and openCitation
entry points. A separate ActionPlane Module owns `prepare(TrustedEffectIntent)`
and `perform(EffectPayload, ActionTicket)`, including write policy, ticket
issuance/validation, idempotency, execution, and audit. Placeholder creation and
reply finalization use two prepare/perform pairs, two tickets, and two
idempotency keys linked only by DeliveryAttemptRef.

prepare returns a closed Prepared, GenericDenied, AudienceChanged, or
RetryableUnavailable outcome. perform returns Applied, AlreadyApplied,
Rejected(effect zero), or ReconciliationRequired. Replaying an applied ticket
returns its stored receipt. An ambiguous provider attempt is reconciled under
the same idempotency identity and is never replayed with a new ticket.

ActionPlane revalidates group audience immediately before the final effect.
Audience change produces business effect zero and requires a new resolve.

## Consequences

The common private-chat caller remains simple while group, citation, egress, and
retry complexity gains locality. Group delivery may require two resolves and two
generations. Cleartext Package consumers remain trusted; supporting untrusted
consumers would require a sealed Package broker and a new decision.
