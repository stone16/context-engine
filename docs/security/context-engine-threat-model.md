---
title: ContextEngine Threat Model
date: 2026-07-19
status: design-authority-support
scope: D0 through M5 controlled context delivery path
source: first-party product requirements and implementation design
---

# ContextEngine Threat Model

## 1. Purpose and current evidence state

This document turns ContextEngine's product boundary into explicit threats,
trust boundaries, and security oracles. It supports the implementation design;
it does not replace that design or an accepted ADR.

The repository is currently pre-M0. Everything here is **specified**, not an
implemented security claim. A threat is closed only when the applicable
invariant has real evidence at the tier required by the current milestone. The
claim tiers are defined by the implementation design: contract fixtures can
prove deterministic boundary behavior, while PostgreSQL, source ACL, wire,
model-egress, and external-effect claims require their corresponding real
dependencies.

## 2. Protected assets

The security model protects:

- tenant-owned source content, metadata, structure, and existence;
- Organization, Principal, Membership, purpose, audience, and ACL facts;
- immutable Resource, Revision, Fragment, CurationSnapshot, and ReleaseManifest
  lineage;
- ContextPackage, Evidence, citations, continuations, model input, and message
  bytes;
- WorkerLease, DeliveryEvidenceRef, EgressGrant, and ActionTicket capabilities;
- connector credentials, service identities, signing keys, and database roles;
- DecisionAudit and authorized-only ContextRun records;
- the active release pointer, publication state, and durable job/checkpoint
  state.

Disclosure of denied content is not the only loss. Revealing a resource's
existence, denied count, title, timing branch, source membership, or private
audience composition can also be an authorization failure.

## 3. Trust boundaries

| Boundary | Input posture | Trusted decision owner |
|---|---|---|
| Caller -> ingress | Request bodies, query text, object ids, narrowing, and transport payloads are untrusted even after caller authentication. | Ingress binds authenticated application/Principal facts and rejects caller-authored trusted fields. |
| Remote BotDelivery -> ingress | BotDelivery is authenticated but cannot manufacture Organization, purpose, or audience facts. | Ingress redeems one request-bound `DeliveryEvidenceRef` into `TrustedDeliveryContext`. |
| Retrieval/index/cache -> Runtime | Candidate ordering, identifiers, metadata, and cache hits may be stale, malicious, or cross-Organization. | `AuthorizationKernel` performs exact authorization and field projection. |
| ContextProvider -> Kernel | Provider discovery is not authorization; projection evidence may be unavailable, stale, incomplete, or inconsistent. | Kernel validates the closed `SourceAclEvidence` mode and `SourceConsistencyRef`. |
| Worker -> durable state | A worker process or message may be replayed, delayed, confused across jobs, or over-privileged. | Signed `WorkerLease`, current durable-job row, least-privilege ServiceActor, and database constraints. |
| Application -> PostgreSQL | Application filters can be omitted or malformed. Owners can bypass RLS. | Non-owner runtime/worker roles, composite ownership constraints, RLS, and `FORCE ROW LEVEL SECURITY`. |
| ContextPackage -> model | BotDelivery holds authorized cleartext but a model request can widen purpose, audience, or retention. | Matching `EgressGrant` and the `AuthorizedModelInput` constructor. |
| BotDelivery -> external effect | A sender call can target the wrong Organization, conversation, audience, or payload, or be ambiguously retried. | `ActionPlane.prepare` and `perform` with an effect-specific one-shot ticket. |
| Learning -> active release | Traces, evaluators, or operators may be poisoned, unauthorized, or bypass gates. | Release-operator-authorized `ContextLearning.promote` is the only active pointer writer. |

BotDelivery, ModelGateway, ActionPlane, Sender, activated providers, PostgreSQL,
and the Supply worker are within the trusted computing base only to the minimum
extent their roles require. Membership in the TCB is not an assumption of
correctness; each boundary is verified at its next trusted seam.

## 4. Threat actors and failure sources

The model covers:

1. an authenticated tenant member probing content outside their current grants;
2. a confused, compromised, or buggy first-party caller attempting to expand
   scope through body fields, identifiers, purpose, or audience claims;
3. a malicious or stale source/provider response, index candidate, cache entry,
   parent/neighbor expansion, or citation reference;
4. a replayed, delayed, mutated, or cross-job worker capability;
5. a delivery audience that is incomplete or changes between resolve and send;
6. a model or external sender receiving more cleartext or authority than the
   exact operation requires;
7. an operator, migration, bootstrap path, or learning job bypassing publication
   and release authority;
8. ordinary implementation defects, including missing tenant context, incorrect
   transaction scope, ambiguous retries, and partial publication.

Availability failures are expected. They may produce a typed unavailable result
or narrower delivery, but never a weaker authorization mode.

## 5. Hard oracles

These outcomes are release vetoes, not weighted scores:

1. **Unauthorized Evidence = 0.** Denied bytes cannot reach Runtime content work,
   ContextPackage, model input, tenant-visible trace, or citation output.
2. **Wrong-Organization effect = 0.** No external effect may target a different
   Organization, audience, destination, effect kind, or payload than prepared.
3. **Missing-context fallback = 0.** Missing trusted identity, tenant, audience,
   lease, or required ACL evidence fails before content work or business effect.

The versioned invariant catalog and negative-test catalog refine these oracles.
An inactive capability is `NOT_ACTIVE`; it is never reported as `PASS`.

## 6. Threat register

| ID | Threat | Required control and observable oracle |
|---|---|---|
| `TM-01` | Caller omits or injects tenant, Principal, purpose, audience, ACL, raw query, or bypass fields. | Closed wire schema plus trusted ingress binding; rejection occurs before provider, index, model, or effect calls. |
| `TM-02` | Guessed or cross-Organization identifiers reveal data or existence. | Composite ownership, non-owner `FORCE RLS`, exact Kernel authorization, and externally indistinguishable denied/missing outcomes. |
| `TM-03` | Candidate text reaches hydration, rerank, relevance models, assembly, or learning before authorization. | `CandidateRef -> AuthorizationKernel -> AuthorizedProjection` is a nominal type boundary; denied content-bearing calls and bytes equal zero. |
| `TM-04` | An authorized child causes unauthorized parent, neighbor, attachment, or citation expansion. | Every expansion produces a new CandidateRef and is independently authorized and projected. |
| `TM-05` | Live or Mirrored ACL failure silently downgrades to Weak or public access. | `Live | Mirrored | Weak` is a closed SourcePolicy choice; missing, stale, incomplete, changed, or failed strong evidence denies. |
| `TM-06` | Revoked access survives through an old epoch, cache, continuation, citation, or source snapshot. | New controlled operations validate current policy/source evidence; stale capabilities and cached decisions yield no Evidence. |
| `TM-07` | A worker impersonates a user or reuses authority across jobs, sources, generations, or Organizations. | Least-privilege ServiceActor plus signed exact-job WorkerLease checked against current durable state; mutation/replay changes no durable state. |
| `TM-08` | Crash or retry exposes mixed Revisions, skipped changes, duplicate effects, or an unapproved release. | Transactional outbox, dual watermarks, immutable versions, atomic active pointers, idempotency, and fault-point tests. |
| `TM-09` | Index or cache filtering is treated as authorization. | Index/cache outputs remain content-free candidates; deliberate cross-Organization hits are removed by Kernel before content work. |
| `TM-10` | Authenticated BotDelivery forges trusted delivery facts or replays another resolve's evidence. | Opaque `DeliveryEvidenceRef` is service/request/org/asker/destination/purpose/audience/expiry-bound and redeemed once at ingress. |
| `TM-11` | Group content is authorized only for the asker, an incomplete member set, or a stale audience. | Kernel computes the complete audience intersection; public and asker-private paths use separate resolves; unknown or stale audience means public bytes equal zero. |
| `TM-12` | A model receives raw provider data, denied content, multiple audiences, or retention/purpose beyond the Package. | ModelGateway accepts only `AuthorizedModelInput` derived from one current audience-bound Package and matching EgressGrant. |
| `TM-13` | Create, edit, send, or compensation authority is confused or replayed across effects. | Each effect uses a separate org/audience/payload-bound one-shot ActionTicket; ambiguous attempts reconcile under the original attempt. |
| `TM-14` | Continuation or citation references act as bearer authorization. | Continuation is principal/audience/epoch-bound, one-shot, and cumulative-budgeted; citation references are locators and reauthorize every open. |
| `TM-15` | Denied candidates leak through ContextRun, metrics, logs, evaluation, or Learning. | ContextRun is authorized-only; denied details stay in restricted DecisionAudit as categories/digests with no denied body or tenant-visible enumeration. |
| `TM-16` | Migration, bootstrap, Control, evaluator, or Curation activates production state outside release gates. | Only release-operator-authorized `ContextLearning.promote` changes the active ReleaseManifest, including bootstrap and rollback. |
| `TM-17` | Connector URL handling exposes internal networks or secrets. | Provider-specific URL allowlists, redirect/DNS rebinding defenses, egress controls, and external secret storage; blocked requests produce no network call. |
| `TM-18` | Timing, count, status, or error details distinguish denied objects from missing ones. | Frozen non-enumeration fixtures compare status/body and a preregistered timing distribution; underpowered evidence is inconclusive. |

## 7. Explicit assumptions and non-goals

- The engine controls future resolves, continuations, citation opens, model calls,
  and effects. It cannot recall bytes already observed or retained by an external
  system; deletion/redaction is an explicit best-effort ActionPlane compensation.
- A source cannot provide stronger ACL semantics than its official surface
  exposes. Weak sources must declare that limitation and may be restricted or
  disabled for sensitive content.
- Host file ownership or mode bits do not implicitly authorize FileProvider
  content. File access uses an explicit, active, versioned `FileSourceAccess`.
- Cross-Organization learning is not a V1 capability. No schema or executable
  path is created for it without a new opt-in/privacy ADR and raw reference
  oracle.
- Denial-of-service resistance, endpoint hardening, dependency patching, secret
  rotation, backup recovery, and regional/compliance controls are required for
  launch readiness, but none may weaken the three hard oracles.

## 8. Evidence closure

The detailed test architecture is in
`Test-Architecture-与可验证性设计.md`; stable adversarial cases are in
`安全负向测试清单.md`. Before a milestone closes, every applicable threat must map
to a versioned invariant, an activated capability, a reproducible command, and
an evidence artifact. The current D0 blockers include real PostgreSQL RLS
transaction-context evidence, filtered-ANN evidence, Feishu capability evidence,
and selection of executable install/test/lint/build/report commands.
