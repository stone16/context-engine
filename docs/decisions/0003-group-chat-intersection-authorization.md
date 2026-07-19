---
name: adr-0003-group-chat-intersection-authorization
version: "1.2.0"
description: >
  Record the group-chat authorization semantics for IM bots: public replies are
  authorized against the asker's scope intersected with all group members' scopes.
  Use when designing or reviewing any bot reply path or audience decision.
---

# 0003. Group-chat replies use asker ∩ all-members intersection

- Status: accepted
- Date: 2026-07-18

## Context

A bot answering publicly in a group makes the content visible to every member, not
just the asker. Authorizing by the asker alone lets a privileged asker leak content
to unprivileged members (confused deputy) — which would breach the hard oracle
"Unauthorized Evidence = 0". The authorization subject for a public reply must
therefore represent the full delivery audience, including membership uncertainty
and the possibility that membership changes between resolve and send.

## Decision

- The requesting Principal is the asker; an unbound asker fails closed. A trusted
  identity Adapter supplies complete `AudienceSnapshot` facts, never a calculated
  scope or caller-authored member list.
- AuthorizationKernel computes a public reply against the intersection of the
  asker and every current audience member. Unknown, external, unbound, stale, or
  non-enumerable membership makes the public path empty. BotDelivery cannot derive
  public content by trimming an asker-private Package.
- Public-group and asker-private content use distinct `DeliveryEvidenceRef` values,
  resolves, ContextPackages, EgressGrants, and delivery effects.
- A group reply is sent only through operation-specific
  `ActionPlane.prepare/perform`; send-time audience drift produces effect zero.
- `CitationOpenRef` is a reusable opaque locator, not a continuation or authority.
  Every opener is authenticated and exactly re-authorized; revoked, denied, and
  missing locators remain non-enumerating.
- If later members can read message history and the future audience cannot be
  bounded, protected cleartext is private-only or requires a proven compensating
  deletion/redaction policy.

## Considered Alternatives

- Asker-only authorization — rejected: structural confused-deputy leak.
- Permanent private-chat-only positioning — rejected. A private delivery slice is
  still implemented first; group delivery activates only at M5 after its audience
  and send-time gates pass.

## Consequences

Egress checks must model the group as the audience. New negative tests (unbound
user, mixed-permission group, revoked citation link) enter the security suite; the
minimum complexity of the IM MVP rises and is accepted.

ADR-0013 defines the concrete trusted types, delivery TCB, and capability
taxonomy used to implement this decision.
