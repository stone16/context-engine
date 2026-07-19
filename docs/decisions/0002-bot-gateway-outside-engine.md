---
name: adr-0002-bot-delivery-outside-engine
version: "1.2.1"
description: >
  Record the decision that IM bot answering lives in a separate trusted Bot
  application containing the BotDelivery Module, not inside the engine and not
  as another transport.
---

# 0002. BotDelivery lives outside the engine

- Status: accepted
- Date: 2026-07-18

## Context

The product must answer questions in IM channels (Feishu first, with later
connectors gated independently). ContextEngine has two relevant trust boundaries:
its online read contract ends at an authorized ContextPackage, while any external
effect belongs to the Action Plane. Putting answer generation or IM protocol
handling inside the engine would enlarge the authorization kernel's trusted
computing base and couple retrieval safety to provider-specific delivery behavior.
HTTP is the V1 server ingress; the generated SDK is an HTTP client artifact, and
MCP remains inactive until a real caller justifies its canonical-parity and
enumeration-security review.

## Decision

Implement IM bots in a separate trusted Bot application process (same repo),
containing the deep `BotDelivery` Module. BotDelivery calls `resolve()` only over
HTTP through the generated SDK, performs controlled generation, and routes every
external effect through `ActionPlane.prepare` then `ActionPlane.perform`. IM is
not a server transport.

BotDelivery does not calculate authorization or author trusted audience claims.
It asks a trusted identity Adapter for a per-resolve opaque
`DeliveryEvidenceRef`, passes that reference in authenticated transport metadata,
and consumes the resulting audience-bound ContextPackage. Public-group and
asker-private deliveries use separate references and separate resolve calls.

## Rationale

Keeping generation and IM protocol handling in a caller preserves
`ContextPackage` as the engine's complete online boundary. It also keeps model
and channel-specific failures outside the Runtime authorization kernel while
still making their cleartext and effect boundaries explicit members of the
delivery trusted computing base.

## Considered Alternatives

- Generation inside the engine — rejected: re-draws every contract
  (Package → Answer) and mixes hallucination concerns into the security
  kernel's responsibility surface.
- IM as a fourth transport — rejected: triggers the full new-transport security
  revisit (canonical parity, closed wire schema, denied/not-found equivalence)
  for a surface that cannot carry the full Package contract anyway.

## Consequences

BotDelivery becomes the engine's first real caller, exercising the Package
contract (citations, TTL, gaps, model egress, and write effects) before any
external caller exists. ADR-0013 owns the trusted delivery evidence, egress, and
capability taxonomy; ADR-0003 owns group intersection semantics.

## Revisit trigger

Reopen this boundary only if an implemented caller demonstrates that an
audience-bound ContextPackage cannot support a required product surface, and a
new threat-model review shows how moving generation or delivery responsibility
would preserve the same authorization, egress, and effect oracles. Latency or
deployment convenience alone is not sufficient.
