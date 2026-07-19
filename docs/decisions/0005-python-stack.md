---
name: adr-0005-python-stack
version: "1.1.0"
description: >
  Record the language/stack decision: Python 3.13 + FastAPI/Pydantic +
  SQLAlchemy/Alembic + PostgreSQL 17 + pgvector, with evidence-based revisit
  triggers. Use when questioning the stack choice or proposing a replacement.
---

# 0005. Python 3.13 stack; revisit on falsifiable stack risk

- Status: accepted
- Date: 2026-07-18
- Resolves: `TODO_DECISION` in ADR-0001

## Context

The first release needs typed HTTP contracts, OpenAPI generation, document
parsing, asynchronous supply work, PostgreSQL transactions/RLS, and explicit
schema migrations. Python 3.13 with FastAPI/Pydantic and SQLAlchemy/Alembic
covers those needs in one runtime, while the public client contract can remain
language-neutral through generated OpenAPI SDKs. The current implementation
design identifies database security, retrieval, and source-capability semantics
as D0 evidence risks. It does not currently identify a concrete stack risk that
a dual-language prototype would falsify.

## Decision

Adopt **Python 3.13 + FastAPI/Pydantic + SQLAlchemy/Alembic + PostgreSQL 17 +
pgvector**. The TypeScript SDK is generated via OpenAPI codegen. D0 does not run
a dual-language prototype without a preregistered, falsifiable stack risk. A
comparison is reopened when an implementation, performance, library,
operability, or team constraint challenges the selected stack.

## Considered Alternatives

- Run the spike without a falsifiable language-risk hypothesis — rejected: it
  would consume the D0 evidence window without reducing a known risk.
- TypeScript full stack — not selected for V1 because no current requirement
  demonstrates enough benefit to justify two server prototypes; revisit through
  the falsifiable triggers above rather than through preference.

## Consequences

Authorization truth stays in Postgres (RLS + composite FK) per ADR-0007;
retrieval stays PostgreSQL-specific in V1 with only an internal test
seam, and a portability Interface waits for a second real backend.
If a future team is TS-heavy, this ADR is the revisit anchor.
