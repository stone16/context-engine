---
name: adr-0075-reuse-onyx-capabilities-through-owned-runners-and-seams
version: "1.0.0"
description: >
  Reuse Onyx capabilities as vendored code behind ContextEngine-owned runners
  and three keystone seams, never as a deployed second product, keeping exactly
  one Tenant/ACL/storage/index/revocation truth.
---

# 0075. Reuse Onyx capabilities through owned runners and seams

- Status: accepted
- Date: 2026-07-29
- Refines: ADR-0067, ADR-0074

## Context

Onyx Community Edition contains a mature connector/checkpoint framework and
retrieval algorithms, but ships as a complete product with its own users,
tenants, document ACL mirror, and search index. Deploying it would create a
second data and authorization truth; its index bakes mirrored ACL markers into
documents, which produces false-positive exposure during revocation lag and
false-negative denial during grant lag. Module-level analysis at pinned commit
`2fb3dd10493b3883870fa8adced5b1a0e114feff` showed the valuable code is
separable: connector interfaces and implementations are MIT and cuttable at a
process boundary, while retrieval logic splits into content-free algorithms
and content-bearing stages.

## Decision

1. **No Onyx deployment.** No Onyx service, database, or index runs in any
   ContextEngine composition. ContextEngine's PostgreSQL (policy tables,
   Article/Revision/Fragment, pgvector, FTS) remains the only online corpus,
   index, and authorization truth.
2. **Connector-runner.** A ContextEngine-owned independent runner vendors the
   patched MIT connector framework (interfaces, registry, checkpoint
   generator, batching, per-connector permission observation). It is a network
   adapter sandbox: it receives one exact WorkerLease-bound job, emits Article
   content/metadata, ACL observations, deletes, and opaque checkpoints, and
   persists nothing independently. Checkpoints are proposals until the engine
   durably accepts the emitted change page.
3. **Three keystone seams** gate all further lifts: a Supply execution/
   checkpoint bridge; a content-free ranked-candidate and authorized-fragment
   access port; a governed model-inference port with explicit profiles, budget
   metering, and egress control.
4. **Function-level retrieval lifts, split at the Kernel.** Pre-Kernel code
   may touch only content-free refs and rank evidence (query rewrite,
   candidate generation contracts, fusion, dedupe). Content-bearing stages
   (rerank, selection, expansion hydration, assembly) consume
   `AuthorizedProjection`s only. Onyx's hybrid search SQL is not ported;
   PostgreSQL FTS/pgvector rankers are implemented natively behind the copied
   interface shape. ContextPackage assembly remains engine-native.
5. **Lift order** follows measured cut cost: weighted RRF/dedupe, hybrid
   retrieval adapter, same-Article expansion, query rewrite, authorized
   rerank, budgeting helpers. Onyx enterprise permission-sync orchestration is
   clean-room only per ADR-0074.

## Consequences

- The engine keeps its sealed
  `CandidateRef → AuthorizationKernel → AuthorizedProjection` ordering; no
  vendored code observes content before authorization.
- Onyx upgrades never propagate implicitly; each vendored subtree is pinned
  and re-admitted deliberately.
- The runner boundary adds serialization overhead per change page; that is the
  accepted price of one truth.
- Building the three seams (estimated tens of engineer-days) precedes broad
  retrieval reuse; connector capability arrives first.

## Revisit trigger

Revisit if a future source class requires request-time live authority the
runner contract cannot express, or if maintaining vendored connector patches
exceeds the cost of a clean-room connector framework.
