---
name: adr-0100-admit-session-derived-learning-only-as-governed-candidates
version: "1.0.0"
description: >
  Admit session-derived information only through consented provenance-bound
  Learning candidates with no direct publication authority. Use when designing
  session tape intake, memory extraction, or session-derived evaluation data.
  Not for treating transcripts as corpus, inferring user facts from model text,
  or mutating an active release from extraction output.
---

# 0100. Admit session-derived Learning only as governed candidates

- Status: accepted
- Date: 2026-08-02
- Refines: ADR-0014, ADR-0031, ADR-0033, ADR-0052, ADR-0062, ADR-0088

## Context

Agent applications retain ordered session entries containing user messages,
assistant output, tool calls and results, autonomous work, delivery events, and
feedback. Those sessions can reveal durable preferences, recurring work,
retrieval failures, and useful evaluation cases. ContextEngine currently has no
raw session intake. `ContextRun` is the authorized-only lineage of one resolve,
not a conversation tape, and its digest-only retention contract deliberately
excludes raw query and Package content.

Directly extracting facts into a mutable notebook would create an unreviewed
publication path. It would also turn assistant assertions, prompt injection,
tool output, secrets, or statements about absent people into apparent user
truth. Shared sessions add consent, audience, deletion, export, and retention
obligations that cannot be inferred from the fact that an agent application
already stores a transcript.

## Decision

1. Session-derived Learning is admitted as a product direction, but raw session
   intake remains inactive until its consent and data-lifecycle carrier is
   separately implemented and verified. A user session is external application
   state. It is not a `ContextRun`, ContextSource, ContextRevision, active corpus,
   authorization grant, or reusable Package authority.
2. Candidate admission requires a future trusted session adapter to project a
   bounded, integrity-bound intake envelope. The refining intake contract must
   bind Organization, ordered entry identity, authenticated speaker or system
   provenance, event time, autonomous-turn status, current consent/purpose, and
   exact Package/Evidence lineage for any entry derived from delivered context.
   Caller- or model-authored identity, role, consent, or audience claims are
   rejected.
3. Intake is purpose-minimized before any extractor sees content. Thinking,
   hidden prompts, credentials, capability values, raw model requests, denied
   content, and unsupported tool payloads are excluded. A verified human's own
   first-person statement may propose a preference or intent candidate about
   that speaker. Assistant, system, autonomous, tool, quoted, pasted, or
   third-party text cannot establish a person's preference, intent, consent, or
   instruction. Autonomous entries may propose only bounded operational
   candidates, never human intent.
4. Shared-session projection requires current intake consent for every included
   entry and affected data subject. An extractor may not use excluded surrounding
   entries to infer a candidate. Missing, withdrawn, expired, ambiguous, or
   non-enumerable consent yields zero candidate content and the same closed
   intake-unavailable outcome; it never falls back to another participant's
   authority.
5. Raw tape is transient by default and never stored in `ContextRun`,
   `DecisionAudit`, the active snapshot corpus, a retrieval index, or a model
   request-capture log. Durable raw-tape storage and model extraction each
   require refining decisions for their own retention/RLS/delete-export and
   Learning-egress boundaries. Until then no raw session carrier or extractor
   model is active.
6. The only eligible durable output of intake is an immutable,
   provenance-bound proposal in a future session-derived candidate family. The
   candidate contract and store must bind current consent, subject and
   Organization scope, source provenance, retention, authorized review, and
   deletion/export behavior. It may feed human review and frozen evaluation
   cases. It cannot enter the current `CurationSnapshot` or `ReleaseCandidate`
   path: the former annotates compatible Revisions and the latter has no governed
   Memory profile to select. The candidate has no active-corpus write, memory
   replace/merge, profile activation, or ReleaseManifest pointer operation.
7. Candidate review does not complete the Memory product loop. Before any
   reviewed session fact can be served, a separate ADR and glossary extension
   must define its governed persistent artifact, subject/Organization/purpose
   scope, merge and conflict semantics, expiry and consent withdrawal, removal
   and export, authorization atom and Evidence lineage, and how Runtime delivers
   it inside one audience-bound `ContextPackage`. That decision must also choose
   how activation and rollback remain under one publication owner; it cannot
   reuse `ContextRevision`, `CurationSnapshot`, or `ReleaseCandidate` by analogy
   and cannot create a second publisher beside `ContextLearning.promote`.
   Until that boundary is accepted and implemented, session-derived candidates
   cannot enter serving or release publication.

## Required evaluation slices

An activation issue must prove at least these zero-or-candidate outcomes:

- an explicit long-lived preference stated by its authenticated human speaker;
- the same preference asserted only by an assistant, tool, pasted document, or
  different speaker;
- autonomous work that contains both an operational outcome and an apparent
  human preference;
- a shared session with one missing, expired, or withdrawn consent binding;
- secret, credential, hidden-prompt, and denied-content canaries;
- duplicate/replayed bursts and out-of-order entry sequences;
- extractor/provider failure and uncertain output;
- consent withdrawal before review; post-publication behavior belongs to the
  required governed-memory serving ADR and cannot be claimed active here.

Security failures are vetoes. Candidate yield, answer quality, or memory recall
cannot offset an unauthorized byte, wrong-subject fact, missing consent, or
direct publication effect.

## Rationale

Session history is valuable precisely because it records real use, but it is a
mixed-trust, long-lived data source. Separating intake, candidate review, and
the later governed serving decision prevents that signal from creating a second
mutable memory authority. Speaker provenance and consent are structural inputs,
not instructions delegated to an extractor model.

## Consequences

- Session intake becomes an explicit later Learning capability, with an equally
  explicit block between reviewed candidates and online Memory serving.
- Existing feedback-bound curation remains the only active candidate source;
  this ADR does not claim that the current `CurationCandidate` schema accepts
  raw session facts.
- The maintainer-local
  [`QM evaluation`](../research/2026-08-02-qm-blueprint-evaluation.md) supplies
  useful test-oracle research but no public provenance or authorization claim.
- Session tape ingestion, model extraction, durable raw-tape storage,
  session-derived candidates, governed Memory artifacts, and Runtime serving
  remain `NOT_ACTIVE`.

## Revisit trigger

Revisit before the first raw session is read by ContextEngine, before any
durable tape storage or extractor-model egress exists, and before a reviewed
candidate can become online Memory. The refining decisions must preserve
consent-current intake, exact speaker provenance, zero direct publication
authority, one publication owner, and deletion/export obligations.
