---
name: adr-0105-bind-local-preflight-to-read-only-observation
version: "1.0.0"
description: >
  Bind the local daily-driver preflight to capability-minimal, read-only
  observation. Use when implementing or operating preflight. Not for migration,
  Runtime delivery, Control, Supply execution, or Release publication.
---

# 0105. Bind local preflight to read-only observation

- Status: accepted
- Date: 2026-08-13
- Refines: ADR-0015, ADR-0033, ADR-0068, ADR-0069, ADR-0102
- Related: [issue #240](https://github.com/stone16/context-engine/issues/240)

## Context

The bounded local daily-driver has separate migration, Control, Supply, release,
Runtime, and caller planes. Before starting or mutating them, an operator needs a
single content-free readiness result. Reusing the normal Control composition
would construct mutation and publication capabilities merely to diagnose them;
adding a database role would also broaden the accepted role topology.

## Decision

`context-engine-control preflight` is a short-lived local CLI selected before
the mutation-bearing Control module is imported. Its production dependency
closure excludes Control, Learning, migration execution, worker execution,
Runtime construction, HTTP, BotDelivery, and ActionPlane.

The command accepts only the closed local plane selection and environment-held
configuration. It emits the versioned
`context-engine-preflight-v1` contract and closed categories; values, paths,
URLs, identifiers, model artifact names, digests, exception text, and content
never enter either output channel.

Schema observation uses the existing migration-plane credential only. Before
reading `alembic_version`, the same PostgreSQL transaction is configured
`READ ONLY`, verifies that state, and verifies the exact non-superuser,
non-BYPASSRLS migrator login. The object graph exposes no Alembic upgrade or
migration callable.

Active Release observation uses the existing Runtime role and binds the exact
configured current UserActor inside one database-enforced read-only
transaction. FORCE-RLS selects only that Organization's active immutable
Release. The probe never constructs Runtime, writes ContextRun or DecisionAudit,
performs inference, or acquires publication/effect authority.

The tracked environment template contains names and empty values only and is
mechanically checked against the implementation inventory. A `ready` result is
point-in-time readiness, not Security-gate evidence or production
certification.

## Consequences

- No new role, grant, process boundary, HTTP route, or active carrier is added.
- Older or unreachable schemas fail closed; preflight never falls back to a
  migration effect.
- Model readiness verifies exact registered bytes without loading the backend;
  actual process startup still revalidates and may refuse.
- Preflight is deliberately non-authoritative: state may change immediately
  after it exits and every actual plane retains its existing fail-closed checks.
