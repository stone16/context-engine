---
name: adr-0001-adopt-doc-steward
version: "0.2.0"
description: >
  Record the decision to adopt the doc-steward standard for ContextEngine's
  agent-facing docs. Use when reviewing why AGENTS.md is canonical and CLAUDE.md is
  a compatibility bridge to it. Not for tracking day-to-day tasks or code changes.
---

# 0001. Adopt the doc-steward standard for agent-facing docs

- Status: accepted
- Date: 2026-07-18

## Context

ContextEngine is a new multi-process context-delivery system. Agent-facing documentation (the files an
AI coding agent loads before seeing a task) needs a consistent house standard from
day one so facts land at the right altitude, guardrails are always-resident, and
volatile values are never copied.

## Decision

We will adopt the **doc-steward** standard:

- `AGENTS.md` is the single canonical charter; `CLAUDE.md` is a thin compatibility
  bridge that imports it and contains only tool-specific routing deltas.
- Architectural decisions are recorded as MADR files under `docs/decisions/`.
- Only invariants and guardrails live in always-resident context; procedures and
  volatile values are shelved or point to a single live source.

## Considered Alternatives

- Separate duplicated `CLAUDE.md` and `AGENTS.md` — rejected because two copies drift.
- No formal doc standard — rejected because facts accrete at the wrong altitude and
  guardrails get buried.

## Consequences

Docs can be audited with doc-steward's read-only EVALUATE
(`doc_lint --target .`) and low-risk fixes applied via `/doc-steward-apply`.

~~TODO_DECISION: exact frontend/backend/database stack~~ — resolved 2026-07-18 by ADR-0005 (Python 3.13 + FastAPI + SQLAlchemy + PostgreSQL 17 + pgvector).
