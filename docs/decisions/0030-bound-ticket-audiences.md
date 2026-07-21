---
name: adr-0030-bound-ticket-audiences
version: "1.0.0"
description: >
  Activate distinct signed ContextAccessTicket and ActionTicket protocol
  boundaries only for one synthetic Provider read and one synthetic channel
  no-op while preserving the production carriers as deferred.
---

# 0030. Bound the first ticket audiences to synthetic effects

- Status: accepted
- Date: 2026-07-22
- Refines: ADR-0011, ADR-0013, ADR-0019, ADR-0027

## Context

[ADR-0011](0011-read-write-plane-separation.md) requires context reads and
external effects to use non-interchangeable capabilities.
[ADR-0013](0013-trusted-delivery-egress-and-capability-taxonomy.md) defines the
complete production `ActionTicket` as an
operation-, destination-, audience-, payload-, epoch-, expiry-, approval-, and
idempotency-bound one-shot authority owned by `ActionPlane.prepare` and
`ActionPlane.perform`. Issue #18 must prove the cryptographic and nominal
read/action separation before a real Provider, Sender, Bot application, or
durable ActionPlane carrier exists.

Treating one test Provider call or channel no-op as the complete production
contract would create a false-green security claim. Waiting for M2, however,
would leave the confused-deputy boundary unexecuted: a correctly signed read
ticket must never become write authority, and a correctly signed action ticket
must never become Provider-read authority.

## Decision

Issue #18 activates two bounded signed protocols over the same trusted identity
chain and signing-key configuration. Separation tests deliberately use the same
key material, so rejection cannot be attributed to unrelated keys.

The nominal execution identity is privately constructed only from one matching,
active `AuthenticatedInvocation` plus `TrustedDeliveryContext`. Their existing
trusted-input validator proves the current-Membership-backed `UserActor`,
Organization/request/authentication binding, active trusted scope snapshot,
authenticated Agent version, delivery application, and server-selected purpose.
Organization, subject User, Membership identity/version, actor Principal
reference, the Organization V0 Policy Epoch, and its retained verification
authority come from that actor; Agent version and purpose come only from the
validated invocation/delivery pair. Ticket claims never construct or replace
this trusted execution identity.

Each issuer is configured by trusted application composition with one exact
Organization and target. It selects signing-key version, issuance time, bounded
expiry, and nonce; callers cannot supply claims, operation, audience, epoch, or
target. Both protocols use canonical HMAC-SHA256 tokens with explicit versioned
keys, but their nominal token/claim/validation types and protected namespaces
remain distinct:

| Protocol | Protected domain | Protected type | Fixed operation | Derived audience |
|---|---|---|---|---|
| `ContextAccessTicket` | `context-engine.context-access-ticket` | `CE-ContextAccessTicket` | `synthetic.provider.read` | `context-read:<provider>` |
| `ActionTicket` | `context-engine.action-ticket` | `CE-ActionTicket` | `synthetic.channel.noop` | `im-send:<channel>` |

The two handlers are structurally separate. The read handler accepts only the
exact `ContextAccessTicket` nominal type and may invoke only its configured
read-only synthetic Provider. The action handler accepts only the exact
`ActionTicket` nominal type and may invoke only its configured synthetic channel
no-op. Neither ticket has a public value constructor. Each protocol has its own
type-aware `deserialize` boundary, which validates the signature, canonical
form, protected domain/type, fixed operation, and exact claim schema before it
can return that nominal type. There is no public generic ticket base, decoder,
or execute switch.

Before either synthetic effect, its handler validates the signature, canonical
form, protocol namespace, operation, trusted Organization and target, complete
identity chain, purpose, audience, issuance/expiry window, nonce shape, signing
key version, bounded protocol lifetime, and bound epoch. It then checks the
durable Organization V0 Policy Epoch through the retained authority immediately
before invoking the configured effect. Expected backend failures at that
authority port are normalized before the ticket boundary. A stale or unavailable
epoch, cross-plane type or deserialization attempt, wrong Organization or target,
wrong audience or identity fact, expiry, malformed token, unknown key, or
tampering returns the same non-enumerating unavailable failure and produces zero
effects.

This activation is intentionally synthetic. It does **not** activate production
`ContextProvider` discovery/projection, source credentials, source ACL evidence,
BotDelivery, Sender or IM delivery, or the M2
`ActionPlane.prepare`/`ActionPlane.perform` boundary. It also does not implement
payload or payload-digest binding, a real destination/effect taxonomy, approval
tier, idempotency key, DeliveryAttempt, durable consumption, one-shot/replay
handling, concurrency, stored receipts, or ambiguous-attempt reconciliation.
The complete `ACCEPT-012` carrier and its production ActionPlane oracles remain
`NOT_ACTIVE`; this bounded evidence cannot promote them by implication.

No schema-manifest version changes for this decision. The only persistent
authority reused by the integration proof is the already-active Organization V0
Policy Epoch and current `UserActor` transaction.

## Rationale

Distinct nominal types prevent accidental substitution at the Python boundary;
different signed domains, protected types, operations, and derived audiences
make the same separation cryptographically observable. Binding issuers and
handlers to trusted Organization/target configuration prevents two mutually
consistent attacker-selected identity and ticket values from redirecting the
configured effect. Reusing the Organization V0 epoch gives both bounded
protocols the same already-proven revocation boundary without inventing a
second freshness authority.

The synthetic effects keep Issue #18 focused on capability separation and zero-
effect rejection. Durable one-shot execution and reconciliation belong at the
transaction boundary of the future real ActionPlane, not in an in-memory test
carrier.

## Consequences

- Unit evidence proves canonical signing, domain/type separation with the same
  key, trusted invocation/delivery-derived Agent and purpose, type-aware
  deserialization, strict claim and audience binding, bounded-lifetime/tamper/
  expiry rejection, and generic non-enumerating failures with zero rejected
  effects.
- Real PostgreSQL evidence proves that one committed Organization epoch advance
  invalidates previously valid tickets in both bounded planes before either
  synthetic effect.
- A valid signature never overrides a wrong nominal type, operation, audience,
  target, Organization, identity chain, expiry, or current epoch.
- Runtime `resolve`, production Provider behavior, and the Bot application
  process remain unchanged by this decision.
- Security catalog evidence must label this exact bounded activation separately
  from the future complete `ACCEPT-012` carrier.

## Revisit trigger

Revisit before any real ContextProvider operation, source credential, Sender or
IM effect, ActionPlane prepare/perform path, payload/destination/approval policy,
durable audit, idempotency, one-shot consumption, replay, concurrency, stored
receipt, or reconciliation carrier is activated. That owner must add the
missing exact bindings at its durable authority/effect boundary and independently
run the complete applicable catalog oracles; this synthetic proof is not a
production shortcut.
