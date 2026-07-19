---
name: adr-0016-implementation-authority-and-vertical-slice-roadmap
version: "1.1.0"
description: >
  Make the concise current implementation design authoritative and deliver the
  program as testable vertical slices with a separate launch gate.
---

# 0016. One implementation authority and a tracer-bullet roadmap

- Status: accepted
- Date: 2026-07-18

## Context

ContextEngine needs one reproducible implementation contract whose claims can
be reviewed from a clean checkout. Mixing research evidence, unresolved
questions, and implementation decisions weakens that contract. A horizontal
capability roadmap can also let unimplemented security paths appear green,
couple optional curation to the opening critical path, and bundle multiple
connector permission models into one milestone.

## Decision

`docs/design/2026-07-18-context-engine-implementation-design.md` is the
implementation authority. `CONTEXT.md` owns terms, accepted ADRs own individual
decisions, and `docs/security/context-engine-threat-model.md` owns explicit
assets, trust boundaries, threats, and hard oracles. These responsibilities are
scoped, not a total precedence order; a contradiction blocks implementation
until the owning documents are reconciled explicitly. `PLAN.md` plus the program
PRD form the implementation-facing summary.
`docs/research/2026-07-19-four-public-repositories-evidence.md` fixes
the allowlisted public evidence behind reference-repository claims; it informs
design but does not override ContextEngine requirements, threat model, design,
or ADRs. Repository-external research inputs are neither public authority nor
public provenance.

D0 must record an immutable commit and digest for the publication-ready
authority bundle after its allowlist and provenance scans pass. Implementation
proceeds as vertical slices:

- M0 secure engineering skeleton;
- M1 File to authorized Package tracer bullet;
- M2 wire contract, generated SDK, and private BotDelivery caller;
- M3 File reliability and retrieval/eval baseline;
- C1 curation in parallel, non-blocking;
- M4 Feishu upstream and private-chat closed loop;
- M5 group delivery and private-cell launch readiness;
- M6 Slack as one connector;
- M7 Google Docs as one connector;
- WeCom only after a separate feasibility gate.

Invariant applicability is preregistered in a versioned catalog; capability
activation/coverage is reported separately; and active results are PASS or
FAIL. The rendered four states are PASS, FAIL, NOT_ACTIVE, and NOT_APPLICABLE,
but an active unexecuted entry is FAIL and a required milestone entry must PASS.
Unimplemented capability is NOT_ACTIVE, not PASS. Design-partner agreement,
legal review, naming, and commercial approval form Launch Gate L1 rather than an
engineering milestone exit.

## Consequences

Each milestone is independently demoable and attributable. The first real
caller arrives before advanced retrieval. Curation and connector breadth cannot
block the security/reliability spine. Promoting an authority baseline requires
a fresh manifest generated only after the public-source allowlist and
provenance closure pass.
