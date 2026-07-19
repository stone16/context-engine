---
name: adr-0006-engine-delivers-context-not-answers
version: "1.1.0"
description: >
  Record the engine boundary: the only online deliverable is the ContextPackage;
  generation, planning, and write tools live in upper Agent Runtime applications.
  Use when tempted to add answer generation or action execution to the engine.
---

# 0006. The engine delivers context, not answers

- Status: accepted
- Date: 2026-07-18

## Context

Authorization and evidence assembly have deterministic security oracles:
unauthorized evidence must never reach a content-bearing consumer, and each
delivered item must retain provenance, purpose, freshness, and budget metadata.
Answer generation has different failure modes and evaluation methods, including
hallucination, tone, and model-provider behavior. External actions add a third
class of risk because their effects can be irreversible. Combining all three in
one online contract would make the authorization boundary harder to seal and
audit.

## Decision

The engine's online contract ends at the **ContextPackage** (citations, purpose,
TTL, asOf, decisionRef, budget). Generation, planning, and any write tool live in
upper-layer Agent Runtime applications (first instance: BotDelivery, ADR-0002).
Refunds, order changes, message sending stay in the separate Action Plane
(ADR-0011).

## Consequences

The engine can be evaluated with deterministic retrieval metrics and security
oracles; generation quality is the caller's concern. Revisit trigger: a caller
demonstrates that Package-level delivery structurally cannot serve a required
product surface.
