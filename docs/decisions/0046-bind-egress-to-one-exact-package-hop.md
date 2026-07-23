---
name: adr-0046-bind-egress-to-one-exact-package-hop
version: "1.0.0"
description: >
  Issue and atomically redeem a digest-only EgressGrant for exactly one
  audience-bound ContextPackage model or channel boundary.
---

# 0046. Bind egress to one exact Package hop

- Status: accepted
- Date: 2026-07-23
- Refines: ADR-0002, ADR-0007, ADR-0009, ADR-0013, ADR-0015, ADR-0017, ADR-0045

## Context

An authorized `ContextPackage` is the only online deliverable, but authorization
to construct a Package is not authority to disclose it to a model provider or
channel. The boundary must prevent arbitrary text, raw `CandidateRef`,
`AuthorizedProjection`, a Package from another audience, or a grant for another
hop from reaching a content-bearing consumer. It must also distinguish channel
preflight from external write authority.

Public repositories may inform clean-room behavior, interface shapes, and test
oracles. Their implementations do not supply this boundary; ContextEngine's
design, accepted ADRs, threat model, and four-repository evidence report remain
the implementation and public-provenance authorities.

## Decision

Runtime has a mandatory final `EgressGate` after Package construction,
provenance, budget, and current-epoch validation. Its server-owned profile is a
closed union: internal-only, one exact model hop, or one exact channel hop. The
caller cannot select the profile, and Runtime issues at most one variant for a
resolve. Internal-only remains the default.

A grant binds the exact Organization, Package digest, canonical payload digest,
purpose, audience digest, Policy Epoch, hop variant, retention and sensitivity
profiles, issuer, consumer, provider and model or channel and destination,
region, issuance, expiry, and profile lineage. The random opaque locator is the
one-shot nonce. PostgreSQL retains only its SHA-256 digest, never the bearer.

Direct authenticated delivery derives its audience digest from trusted
Organization, current Membership/version, application, and delivery binding.
Private delivery reuses the audience digest established by the redeemed
`DeliveryEvidenceRef`. Channel issuance additionally requires that exact
redeemed private context and rejects unless its trusted destination and
consumer exactly match the server-owned channel profile. Direct delivery can
issue a model grant but cannot issue a channel grant. Neither path invents an
`AudienceSnapshot`.

Grant issuance stays in the retained current-UserActor transaction. A dedicated
non-owner egress login can execute only exact redemption; a separate NOLOGIN
definer owns the minimum table access and functions. Redemption uses
database-owned time, verifies current Organization Policy Epoch, performs one
atomic compare-and-set, and records only grant/payload digests plus restricted
issued, consumed, or not-available categories. Unknown and cross-Organization
locators remain non-enumerating.

BotDelivery exposes nominal `AuthorizedModelInput` and
`AuthorizedChannelPayload` constructors. They derive only from one exact
`ContextPackage` and the matching grant variant. The model boundary revalidates
the canonical payload digest, requires the gateway's immutable nominal identity
to match the grant profile's consumer/provider/model/region, and consumes the
exact model grant before the first gateway byte. The channel boundary requires
the Sender preflight identity to match consumer/channel/destination/region and
then does the same before preflight, but exposes no write operation. An external
effect still requires a distinct `ActionPlane.prepare` then
`ActionPlane.perform` ticket.

Issue #65 activates only opaque grant issuance, digest-only PostgreSQL
redemption/audit, and deterministic network-free model and channel boundary
spies. Real model/provider calls, a real Sender, ActionTicket effects, group
audience revalidation, the Bot application process, and generated SDK consumer
remain inactive.

## Rationale

The final gate makes egress authority a consequence of one already sealed
Package decision, while the independently privileged redemption boundary
prevents Runtime from treating its own locator as consumed. Exact variant and
payload bindings make grant swapping or mutation fail before bytes. One-shot
atomic state closes sequential and concurrent replay without using a cache as
authority.

Forking or copying a general-purpose open-source RAG implementation was rejected
for this boundary because its trust topology would become an unreviewed runtime
foundation. Clean-room observations remain useful, while these exact security
contracts and executable oracles remain ContextEngine-owned.

## Consequences

- Runtime returns no egress authority by default and never returns both grant
  variants for one resolve.
- A leaked database row cannot reconstruct or replay a bearer.
- Wrong Package, Organization, purpose, audience, epoch, hop, profile,
  destination, provider/model, lifetime, or replay emits zero boundary bytes.
- A channel grant permits preflight only and cannot create an external effect.
- Production BotDelivery and external-provider conformance remain gated by
  later issues.

## Revisit trigger

Revisit before grant delegation, multi-hop batches, provider token exchange,
group delivery, profile rotation, a remote grant store, production Sender use,
or ActionPlane activation. Any revision must preserve the sealed final gate,
exact one-hop binding, digest-only bearer persistence, independent least-
privilege redemption, atomic one-shot behavior, generic failure, and zero bytes
or effects on mismatch.
