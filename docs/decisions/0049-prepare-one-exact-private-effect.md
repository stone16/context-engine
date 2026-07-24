---
name: adr-0049-prepare-one-exact-private-effect
version: "1.0.0"
description: >
  Prepare one operation-specific private ActionTicket through a digest-only,
  FORCE-RLS, function-only PostgreSQL authority without invoking Sender.
---

# 0049. Prepare one exact private effect before Sender

- Status: accepted
- Date: 2026-07-23
- Refines: ADR-0011, ADR-0013, ADR-0015, ADR-0030, ADR-0044, ADR-0045,
  ADR-0046

## Context

Issue #18 proved only that synthetic read and action capabilities have distinct
audiences. A private Bot delivery needs a production preparation boundary that
cannot derive effect authority from model output, `action_required`, a
`ContextPackage`, an SDK body, or caller-authored destination/audience values.
It must also make exact retry safe before the external provider is introduced.

The engine's Python ActionTicket remains the explicitly synthetic Issue #18
carrier. Reusing or extending it would blur the process boundary and could
false-green production payload, destination, approval, idempotency, and durable
authority claims.

## Decision

The co-resident Bot application owns a private TypeScript ActionPlane module.
Its public `prepare(TrustedEffectIntent)` accepts only request-lived nominal
values constructed by trusted in-process orchestration. The package export map
does not expose those constructors, and exported ticket nominal classes require
an internal construction authority at runtime.

The operation is a closed union of `create_placeholder`, `finalize_reply`, and
`send_private_followup`. Each maps to a distinct ticket audience and type. The
payload digest is SHA-256 over a domain-separated RFC 8785 document containing
the exact operation, `application/json` media type, and closed payload object.
Approval and idempotency are separately domain-bound.

ActionPlane uses a dedicated `context_engine_action` NOINHERIT/NOBYPASSRLS
login. That login has no table privileges and can execute only
`context_action_prepare_private_effect`. A distinct NOLOGIN definer owns the
function and has only the RLS-gated reference reads plus delivery-attempt and
ticket reads/inserts and restricted-audit inserts needed by prepare; it has no
Action-table update or delete authority. The function fixes
`search_path`, enables row security, uses database-owned time, and atomically
revalidates:

- exact private DeliveryEvidence digest, authenticated service and binding;
- Organization, User, current Membership/version, destination, consumer,
  purpose, private audience digest, and Policy Epoch;
- exact closed operation/audience, payload digest, approval tier, profile,
  expiry, signing version, and idempotency digest;
- when supplied, the exact active Source and active SourceVersion.

Delivery-attempt, ticket, and audit rows are Organization-owned and FORCE-RLS.
They retain only digests for evidence/service/binding/destination/consumer/
purpose/audience/identity, payload, approval, idempotency, and decisions. The
serialized ticket bearer and payload body are never stored. The active
`action-digest-audit-retention-v1` profile fixes a bounded `retain_until` for
every row.

Exact retry under one Organization/idempotency digest returns the stored ticket
reference, delivery attempt, issuance, and expiry. Conflicting reuse is a
generic zero-effect denial. Placeholder and finalize can share only an exact
DeliveryAttemptRef and still require separate idempotency and ticket rows.

`prepare` contains no Sender/provider seam. Issue #67 activates only ticket
preparation. `perform`, ticket consumption, external writes, applied-receipt
replay, ambiguous-attempt reconciliation, group audience, compensation, and
real Feishu remain `NOT_ACTIVE`.

Public repositories may inform clean-room behavior, interface shapes, and test
oracles. No repository implementation supplies this module or database
boundary; ContextEngine design, ADRs, threat model, and executable evidence
remain authoritative.

## Rationale

Keeping the login powerless outside one function makes the PostgreSQL
transaction the atomic authority for current audience, source lifecycle,
idempotency, and durable issuance. Digest-only rows limit retained sensitive
data while preserving exact conflict, audit, and future reconciliation
lineage. Separate operation types prevent a generic action string or create
ticket from acquiring edit/send authority.

## Consequences

- Wrong Organization, service, binding, destination, audience, payload, epoch,
  operation, approval, evidence lifetime, or source lifecycle produces only a
  closed zero-effect outcome.
- An identical prepare does not mint a second logical ticket authority.
- The TypeScript module and database role become M2 production foundations;
  the Issue #18 Python carrier remains synthetic and separate.
- Prepared tickets cannot cause an effect until the later `perform` boundary
  validates and consumes them.

## Revisit trigger

Revisit before adding group delivery, another effect kind or approval tier,
rotating signing profiles, splitting ActionPlane into another process, remote
ticket storage, compensation, or any Sender. Any revision must preserve trusted
intent provenance, closed operation-specific tickets, exact current audience
and source validation, digest-only bearer/payload persistence, function-only
least privilege, generic zero-effect refusal, and atomic idempotency.
