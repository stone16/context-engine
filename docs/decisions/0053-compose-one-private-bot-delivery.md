---
name: adr-0053-compose-one-private-bot-delivery
version: "1.0.0"
description: >
  Compose one independent private File-backed BotDelivery application from the
  generated SDK, controlled model boundary, and co-resident ActionPlane.
---

# 0053. Compose one private File-backed Bot delivery

- Status: accepted
- Date: 2026-07-24
- Refines: ADR-0002, ADR-0008, ADR-0011, ADR-0013, ADR-0017, ADR-0030, ADR-0045, ADR-0046, ADR-0048, ADR-0049, ADR-0050, ADR-0051, ADR-0052

## Context

The individual private delivery capabilities already have closed boundaries:
ingress redeems opaque delivery evidence, Runtime emits one authorized Package,
model egress consumes that Package and one grant, citation opening reauthorizes,
and ActionPlane prepares and performs one exact effect. M2 still needs to prove
that a real caller can compose those boundaries without acquiring a second
authorization path, manufacturing trusted facts, reusing effect authority, or
adding another process.

This first composed carrier must also make retries and partial outcomes explicit.
A placeholder can already exist when resolve or generation fails, and a Sender
timeout can be ambiguous after an effect. Reporting such states as success or
minting replacement authority would hide durable external state.

## Decision

One independent trusted TypeScript Bot application process contains
`BotDelivery` and the co-resident `ActionPlane`. The engine remains the API
process plus its independent Supply worker, so the activated topology has
exactly three application processes. BotDelivery reaches Runtime only through
the installed generated TypeScript SDK over HTTP. Its package and import gate
prohibit engine, repository, migration, AuthorizationKernel, server-internal,
and handwritten HTTP dependencies.

The process-private Feishu twin configuration binds an exact verified event to Organization, current
Membership/version, user, destination, consumer, purpose, Policy Epoch,
request, opaque `DeliveryEvidenceRef`, and either the exact question or exact
`CitationOpenRef`. Only the twin can mint nominal `VerifiedQuestionTurn` and
`VerifiedCitationOpen` values. Their private facts are held outside public
serialization and cannot be reconstructed from plain objects. The installed
package root does not export the twin constructor or fixture shapes; only the
Bot process composition root can load them.

The File source in the composed evidence fixture is acquired and published by
the existing independent `context-engine-worker` process through its bounded
one-job FileImport entrypoint. That entrypoint accepts only an exact signed
WorkerLease plus configured worker credential, registered ServicePrincipal and
logical File root bindings, then exits after one terminal outcome. It is the
already accepted Supply process, not a fourth application boundary; the default
unconfigured long-running worker remains fail-closed.

ActionPlane does not accept caller-authored Organization, Membership, user,
audience, epoch, service, consumer, authentication-binding, or purpose facts
from BotDelivery. Before it can mint a nominal `TrustedEffectIntent`, its
function-only action-role boundary hashes the opaque `DeliveryEvidenceRef` and
the expected service/consumer/request/destination/purpose bindings, then derives
all remaining trusted facts from current database-owned evidence, Membership,
and Policy Epoch. A forged, expired, or mismatched binding mints no intent,
ticket, attempt, or effect authority. The database-derived Organization must
also equal the process-owned Organization sealed into ActionPlane's trusted
prepare profile, so an otherwise valid evidence bearer from another tenant
cannot create even the placeholder effect.

`BotDelivery.answer` assigns one stable `DeliveryAttemptRef` to a verified turn.
It first asks ActionPlane's database-derived private-delivery seam to call `ActionPlane.prepare` and
`ActionPlane.perform` for `create_placeholder`. It then invokes the generated
SDK with the opaque evidence reference in authenticated metadata, accepts only
one current non-empty sufficient private File Package with the exact audience,
purpose and Policy Epoch plus a model grant, and invokes the controlled model
boundary from ADR-0052. The final edit or private follow-up is a second complete
prepare/perform pair. Placeholder and final effects use distinct operations,
payload digests, tickets, and idempotency keys; their only shared effect lineage
is the DeliveryAttemptRef.

A successful answer returns an immutable `DeliveryReceipt` containing only the
Package digest, DeliveryAttemptRef, two operation receipt refs, final status,
and a restricted audit ref. A function-only action-role PostgreSQL boundary
persists that exact linkage under FORCE RLS with a fixed digest-only retention
profile. It verifies that both immutable receipts belong to the same attempt
and have the required placeholder and final operation classes. No answer text,
Package body, identity, evidence bearer, grant, ticket, or denied detail is
retained in the delivery audit or returned in the receipt.

An identical successful turn returns the stored in-process receipt without new
SDK, model, Sender, or audit work. Deterministic refusal before an applied
effect returns one generic unavailable outcome. Once ActionPlane reports an
ambiguous provider attempt, or once both effects are applied but the final audit
cannot be established, BotDelivery returns reconciliation-required with the
original DeliveryAttemptRef and, when available, the original provider-attempt
reference. It does not create replacement effect authority.

`BotDelivery.openCitation` accepts only the twin-minted exact citation event,
uses its own current private evidence through the same generated SDK boundary,
and returns either the newly authorized citation Package/grant path or one
generic unavailable outcome. It never returns a source URL or treats the
locator as authority.

Issue #71 activates only the complete private File-backed deterministic-twin
carrier. Live Feishu APIs or event verification, real model and Sender
providers, group/public `AudienceSnapshot` and dual resolve, Continue,
compensation/delete, and MCP remain `NOT_ACTIVE`.

## Rationale

Composing the existing deep modules proves the deployable M2 call path while
preserving each authority owner. Nominal identity inputs and an installed SDK
make caller trust and engine authorization visibly separate. Distinct action
capabilities prevent a read, Package, model output, placeholder ticket, or
successful receipt from becoming general write authority. Digest-only final
audit gives operators durable delivery lineage without creating another content
store.

## Consequences

- The Bot application has its own credentials, readiness contract, and process
  lifecycle, while ActionPlane remains a module rather than a fourth process.
- The deterministic twin carrier consumes closed newline-delimited events on
  standard input. Every event is re-bound by the process-owned twin; citation
  output is reduced to status, purpose, and Package digest instead of
  serializing the Package or grant.
- A placeholder can remain after a later closed failure; compensation and
  deletion are deliberately deferred instead of being implied by retry logic.
- The deterministic twins prove contract composition, rejection, replay and
  reconciliation semantics but do not claim live-provider conformance.
- `ACCEPT-012` retains its historical M0 unavailable state while Issue #71 adds
  the exact current private M2 activation evidence.

## Revisit trigger

Revisit before enabling live Feishu ingress or Sender calls, a production model
provider, group/public delivery, compensation/delete, durable cross-process
successful-receipt lookup, another Bot instance concurrency strategy, or any
additional process boundary.
