---
name: adr-0078-narrow-the-contract-kit-gate-to-per-connector-twins
version: "1.0.0"
description: >
  Dissolve the standalone contract-kit milestone: connector contract ownership
  moves to the Supply execution seam, deterministic twins become a per-connector
  test obligation, and the Slack-first proving order is abolished.
---

# 0078. Narrow the contract-kit gate to per-connector twins

- Status: accepted
- Date: 2026-07-29
- Refines: ADR-0075; supersedes the resident `contract_kit/` sequencing
  ("base runner + twins before Feishu; versioned kit v1 proven by Slack")

## Context

The pre-pivot architecture reserved a standalone `contract_kit/`: a versioned
connector contract plus deterministic twin sources, to be proven on Slack
before any Feishu connector. ADR-0075 changed the landscape: the vendored MIT
connector framework plus the Supply execution seam's ChangePage/WorkerLease
contract now own connector semantics, and ADR-0062 orders work by real
pulling workloads — which Feishu has and Slack does not. The 2026-07-29
coverage audit (G10) flagged that the resident sequencing and the Feishu
connector issue contradict each other, blocking implementation.

## Decision

1. **Contract ownership moves to the seam.** The versioned connector contract
   is the Supply execution seam's ChangePage/SupplyDocumentEnvelope/
   checkpoint interface; no separate contract-kit artifact is built.
2. **Twins survive as a per-connector test obligation.** Every external
   connector ships with a deterministic twin: a fixture-driven fake of the
   source API (documents, identities, groups, ACL responses, deletes) that
   makes the connector's behavior — including permission observation and
   revocation-lag oracles — executable offline. The Feishu connector's twin
   must make the Room-A permission test oracles runnable without a live
   tenant.
3. **The Slack-first proving order is abolished.** Connector admission order
   follows real pulling workloads per ADR-0062.

## Consequences

- The Feishu connector is unblocked; its definition of done absorbs the twin
  and the permission oracles.
- No `contract_kit/` directory is created; the resident architecture map is
  corrected accordingly.
- Each future connector pays the twin cost at admission time instead of the
  project paying a speculative kit milestone up front.

## Revisit trigger

Revisit if connector count grows enough that twin scaffolding is duplicated
across connectors and a shared fixture framework would pay for itself.
