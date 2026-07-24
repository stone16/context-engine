---
name: adr-0050-perform-one-exact-private-effect
version: "1.0.0"
description: >
  Perform one prepared private effect through an exact one-shot PostgreSQL
  state machine, immutable receipt replay, and original-attempt reconciliation.
---

# 0050. Perform one exact private effect under one provider attempt

- Status: accepted
- Date: 2026-07-24
- Refines: ADR-0011, ADR-0013, ADR-0015, ADR-0030, ADR-0046, ADR-0049

## Context

Preparation proves current private delivery authority and mints an
operation-specific `ActionTicket`, but it deliberately has no Sender seam.
Perform must prevent two dangerous shortcuts: invoking a provider before every
binding is revalidated, and treating an ambiguous provider response as
permission to retry under a new effect identity.

Create-placeholder, finalize-reply, and private follow-up also have different
effect semantics. A generic ticket or provider retry key would let one prepared
capability drift into another operation or produce an untraceable duplicate.

## Decision

`ActionPlane.perform(EffectPayload, ActionTicket)` first verifies the nominal
operation-specific ticket, signature, closed canonical payload, active profile,
and approval binding. A configured private Sender is mandatory before durable
consumption begins. It then calls a dedicated function-only PostgreSQL
authority with the exact signed and stored bindings.

The begin function locks one Organization/ticket identity, matches the stored
delivery attempt and ticket, and immediately revalidates current Membership,
Policy Epoch, optional Source lifecycle, private DeliveryEvidence lifetime,
destination, and audience. Only its `sender_required` result contains the raw
private destination, which returns to the co-resident trusted ActionPlane and
is never persisted in the execution tables. Every mismatch returns the same
closed zero-effect result before Sender.

One `provider_attempt_ref` is durably inserted before Sender and remains the
only provider identity for that ticket. Applied completion atomically writes a
terminal attempt, consumes the ticket, and inserts one immutable digest-only
`ActionReceipt`. Exact replay reads that stored receipt and does not invoke
Sender. Deterministic rejection becomes terminal with no receipt.

Timeout, thrown Sender calls, ambiguous provider results, or failure to commit
completion return `ReconciliationRequired` under the original provider attempt.
Retry observes that attempt and cannot call Sender or mint a replacement.
Trusted reconciliation changes `in_flight` or `ambiguous` exactly once to
applied or rejected; applied reconciliation creates the same receipt shape,
while rejected reconciliation creates none. Exact terminal replay is stable,
and a conflicting decision is rejected.

For this issue's single co-resident process and deterministic twin, a
module-private active-attempt fence covers the complete Sender-through-
completion interval. Reconciliation is refused before database work while that
exact Organization/provider attempt remains active. Process loss clears the
fence and deliberately leaves the durable `in_flight` attempt reconcilable.
This process-local fence is not sufficient authority for a future real or
multi-process Sender; that carrier remains inactive and requires a new design.

The application login owns no table privilege and can execute only prepare,
begin, complete, and reconcile functions. A distinct NOLOGIN execution definer
has only the exact RLS-gated reads/inserts/updates needed by those functions,
with no delete or receipt-update authority. Provider attempts, receipts,
reconciliation, and perform audit are Organization-owned and FORCE-RLS. They
retain opaque refs, bounded categories, timestamps, and digests only; no
payload, destination, service, DeliveryEvidence bearer, ActionTicket bearer,
or denied detail is retained.

Issue #68 activates only the deterministic private Sender twin. Real provider
or channel network effects, group AudienceSnapshot, compensation/delete,
BotDelivery orchestration, and the full `ACCEPT-012` carrier remain
`NOT_ACTIVE`.

Public repositories may inform clean-room behavior, interface shapes, and test
oracles. No repository implementation supplies this state machine or its
database boundary.

## Rationale

Committing the provider attempt before Sender makes ambiguity explicit and
durable. Separating begin from completion is necessary because an external
effect cannot share the PostgreSQL transaction; reconciliation closes that
gap without granting a second effect identity. Immutable receipt replay gives
callers a stable success result without another provider call.

## Consequences

- Each prepared ticket can cause at most one deterministic Sender-twin call.
- Create, finalize, and private-send require distinct prepare/perform pairs.
- Wrong Organization, service, operation, destination, audience, payload,
  epoch, approval, delivery attempt, idempotency, expiry, nominal kind,
  signature, or current audience has business effect zero.
- Process loss after Sender is never reported as safely retryable; an operator
  must reconcile the original attempt.
- Retained execution state supports release stop and later reconciliation
  without retaining raw content or bearers.

## Revisit trigger

Revisit before a real Sender, provider-specific idempotency contract, group
delivery, compensation, delete/redaction, human approval, new action kind,
remote ActionPlane process, or automatic reconciliation. Any revision must
preserve one original attempt identity, pre-Sender current-authority checks,
operation-specific tickets, immutable terminal replay, digest-only retention,
function-only least privilege, and zero effect for every confused input.
