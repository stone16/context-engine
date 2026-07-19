---
name: adr-0011-read-write-plane-separation
version: "1.2.1"
description: >
  Record the separation of context reads from action writes: ContextAccessTicket
  and ActionTicket share an identity chain but different audiences and are never
  interchangeable. Use when adding any side-effectful capability.
---

# 0011. Context read / Action write plane separation

- Status: accepted
- Date: 2026-07-18

## Context

"Send a message", "issue a refund", and "modify an order" have irreversible
side effects and different approval/audit needs than reading context. If
ContextEngine allowed a read credential or resolved content package to
authorize a write, it would turn the caller into a confused deputy and make
least-privilege review impossible.

## Decision

The engine issues **ContextAccessTicket** for reads only. Writes go through a
separate Action Plane with **ActionTicket** — same authenticated identity chain,
different audience claim, never interchangeable (ACTION-SEPARATION-014). Low-risk
actions (e.g. plain-text group replies) may use a pre-approved policy tier:
the approval flow lightens, ticket separation and audit never do.

## Rationale

Read authority proves only that a caller may receive declared context. It says
nothing about whether that caller may mutate an external destination. Distinct
ticket audiences make this boundary cryptographically checkable and prevent a
read-capable component from becoming a confused deputy for writes.

## Consequences

The engine's attack surface stays read-only; every side effect has its own
ticket, policy, and audit trail. IM reply sending (BotDelivery) is the first
consumer of this plane.

ADR-0013 names `ActionPlane.prepare` and `ActionPlane.perform` as the deep write
Interface and freezes their closed outcomes. Each effect receives one
operation-specific ActionTicket. Placeholder creation and final edit/follow-up
use distinct tickets and idempotency keys linked only by DeliveryAttemptRef;
successful replay returns the stored receipt, while an ambiguous provider
attempt is reconciled under the original id rather than retried with a new
ticket.

## Revisit trigger

This separation may be refined when a real external effect requires a new
capability class or approval tier. It must not be reopened merely to reuse a
read ticket: any proposal still has to prove different read and effect
audiences, one-effect payload/destination binding, and zero business effect for
wrong-audience use.
