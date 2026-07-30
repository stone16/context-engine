---
name: adr-0087-bound-live-feishu-to-the-existing-private-bot-composition
version: "1.0.0"
description: >
  Bound live private-audience Feishu event ingress and Sender calls to the
  existing BotDelivery composition without adding trust, effect, or ingestion
  authority.
---

# 0087. Bound live Feishu to the existing private Bot composition

- Status: accepted
- Date: 2026-07-30
- Refines: ADR-0013, ADR-0045, ADR-0046, ADR-0049, ADR-0050, ADR-0052,
  ADR-0053, ADR-0077

## Context

ADR-0053 composes one private File-backed delivery carrier in the existing
trusted TypeScript Bot application process. Its Feishu identity Adapter and
Sender are deterministic, network-free twins. They already prove the installed
generated-SDK call into the engine, one audience-bound Package, controlled model
generation, distinct placeholder and final effects, and reconciliation without
adding a fourth process.

A live Feishu bot replaces those two provider-facing twins with event ingress
and Sender calls. That replacement handles untrusted provider events, process
credentials, cleartext generated output, and real external effects, so it must
not turn Feishu fields into trusted facts or turn delivery retry into new effect
authority. The first live audience remains one asker's private conversation in
one process-owned tenant.

This is the downstream delivery surface of issue #133. Issue #127 owns the
separate ingestion-side Feishu connector that acquires source content and ACL
evidence through Supply. This decision grants no connector, source-read,
publication, or ingestion authority and keeps the delivered corpus File-backed.

## Decision

Live Feishu delivery is a bounded replacement inside the ADR-0053 composition,
not a new composition or process. The engine remains the API process plus its
independent Supply worker, and one existing trusted Bot application process
continues to contain BotDelivery and the co-resident ActionPlane. BotDelivery
reaches Runtime only through the installed generated TypeScript SDK over the
frozen `POST /v0/resolve` operation.

### Verified private event ingress

The Bot process owns one versioned Feishu verification profile bound to its
configured application, provider tenant, ContextEngine Organization, consumer,
private destination kind, and server-fixed purpose. A trusted identity Adapter
accepts a bounded provider envelope and verifies its authenticity, integrity,
event kind, provider application and tenant binding, unique event identity,
trusted-time freshness and expiry, private destination, asker mapping, and
replay state before it constructs any nominal turn or citation-open value.
Provider fields remain untrusted input until all checks succeed.

Only that verified nominal event may request issuance of one exact opaque
`DeliveryEvidenceRef`. BotDelivery carries the ref only in authenticated SDK
transport metadata; it never places raw Feishu identity, destination, audience,
or trusted delivery facts in the resolve body. Engine ingress redeems and
revalidates the reference in the current UserActor transaction before it alone
constructs `TrustedDeliveryContext`. The audience is asker-private only; this
carrier creates no `AudienceSnapshot`, group intersection, public Package, or
fallback from an invalid public event.

A forged signature or envelope, expired or stale event, replay, wrong provider
application or tenant, wrong Organization, mismatched asker or Membership,
non-private destination, wrong consumer, purpose, request, or event kind returns
one generic unavailable outcome before evidence issuance or content work. It
mints no verified turn, `DeliveryEvidenceRef`, `TrustedDeliveryContext`,
`DeliveryAttemptRef`, trusted effect intent, `ActionTicket`, provider attempt,
or external effect.

### Exact Package and egress boundaries

Each verified question uses one current private resolve. The returned Package
must match the redeemed audience, purpose, Policy Epoch, lifetime, and complete
Block/Evidence closure before the controlled model boundary can consume its
exact model `EgressGrant`. The Feishu event or Sender cannot manufacture a
Package, grant, trusted audience, or model input.

The File Article (`ContextResource`) remains the only content authorization
atom. Feishu events, destinations, message fragments, citation locators, and
channel metadata cannot add a Fragment ACL, widen an Article decision, or turn
delivery identity into source-content authority.

A channel `EgressGrant` governs only a preflight hop when exact Package bytes
are prepared for a Feishu channel. It must match the Package, private audience,
consumer, channel, destination, region, purpose, epoch, payload, profile, and
lifetime and is consumed before any preflight byte. It grants no send or edit
authority. The ADR-0053 generated-answer flow sends no raw Package bytes to
Feishu: it uses the model grant for Package-to-model disclosure and a distinct
`ActionTicket` for each generated-answer effect. A future Package-bearing
channel payload must use the channel grant boundary and cannot substitute that
grant for either the model grant or an ActionTicket.

### One exact authority per Feishu effect

The live Sender remains reachable only from `ActionPlane.perform`; BotDelivery
cannot invoke it directly. Placeholder creation and final edit or private
follow-up each traverse their own `ActionPlane.prepare` and
`ActionPlane.perform` pair with distinct operations, payload digests,
operation-specific one-shot tickets, provider idempotency keys, and receipts.
Their only shared effect lineage is the stable `DeliveryAttemptRef`.

Immediately before Sender, ActionPlane revalidates the exact Organization,
current Membership and Policy Epoch, private destination and audience, consumer,
purpose, delivery-evidence lifetime, operation, payload, ticket, approval, and
idempotency bindings. Any mismatch, staleness, expiry, replay, or unavailable
verification has business effect zero. One durable `ProviderAttemptRef` is
created before a Sender call. A timeout or ambiguous provider outcome returns
reconciliation-required under that original attempt; BotDelivery mints no
replacement ticket, idempotency identity, provider attempt, or effect authority.

Feishu verification and Sender credentials come only from the Bot process's
configured secret source. Their values are absent from repository content,
command arguments, event-derived state, HTTP bodies, Package and effect payloads,
receipts, audit rows, logs, errors, and representations. Accepting this ADR
alone activates no credential or network call. Until the live implementation
and its provider-conformance evidence land, live credentials and Feishu network
access remain `NOT_ACTIVE`, and all verification is twin-bounded.

The first replacement keeps real model providers, group/public delivery,
`Continue`, compensation/delete, automatic ambiguous-attempt reconciliation,
multiple Feishu tenants or Bot instances, MCP, and the issue #127 ingestion-side
Feishu connector `NOT_ACTIVE`.

## Rationale

Replacing provider-facing twins in place preserves the already-proven process,
SDK, Runtime, model, action, and audit boundaries. Exact event verification
keeps provider payloads outside the trusted-fact boundary, while opaque evidence
redemption lets the engine construct private delivery context without receiving
raw identity or audience claims.

Keeping channel preflight authority distinct from each write ticket prevents a
Package disclosure grant from becoming a Feishu mutation capability. Durable
single-attempt reconciliation preserves at-most-one external effect when the
provider result is ambiguous.

## Consequences

- A private Feishu carrier can replace the identity and Sender twins without a
  fourth process, a handwritten Runtime client, or another authorization path.
- Provider verification failure is closed before trusted context, Package,
  ticket, or effect creation; no private fallback widens the audience.
- A valid question may leave an applied placeholder when a later stage fails;
  compensation remains deliberately inactive.
- Live-provider readiness requires executable event-authenticity, expiry,
  replay, wrong-binding, zero-effect, idempotency, and reconciliation evidence
  before credentials or network calls can be marked active.
- Feishu delivery does not activate Feishu ingestion. The source remains File
  until issue #127 independently satisfies its Supply-side contract.

## Revisit trigger

Revisit before group/public Feishu delivery, a second tenant or Bot instance,
provider credential rotation with overlapping validity, automatic
reconciliation, compensation/delete, a real model provider, a Package-bearing
channel response, or any new process boundary. Any revision must preserve
pre-trust event verification, opaque metadata-only delivery evidence, the
asker-private audience, exact per-hop grants, operation-specific one-shot effect
authority, and zero effect for every invalid or ambiguous input.
