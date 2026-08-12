---
name: adr-0106-bind-daily-driver-launch-to-exact-schema-state
version: "1.0.0"
description: >
  Bind daily-driver manifest publication and content-process startup to one
  exact code revision and database schema state. Use for durable local setup,
  update, and API/worker/scan launch. Not for migration execution or launchd
  installation and lifecycle ownership.
---

# 0106. Bind daily-driver launch to exact schema state

- Status: accepted
- Date: 2026-08-13
- Refines: ADR-0069, ADR-0105
- Related: [issue #242](https://github.com/stone16/context-engine/issues/242)

## Context

The daily-driver setup can fast-forward code, install the runtime, start the
database harness, and render launchd plists without proving that the retained
database is at the updated code's unique migration head. Preflight provides a
point-in-time diagnosis, but ADR-0105 intentionally makes it non-authoritative;
API and worker startup therefore cannot rely on an earlier successful run.

## Decision

After the database harness is available, setup reuses ADR-0105's migration-plane
read-only schema classifier. It publishes a ready renderer manifest only when
the database has exactly the one packaged Alembic head. The owner-only manifest
binds the clean Git revision and a content-free digest of that exact schema
state. It contains no URL, credential, path, role, identifier, migration output,
or exception text.

API, worker, and scheduled scan wrappers verify the owner-only ready manifest,
the current clean Git revision, and a fresh result from the same read-only
schema classifier before content I/O. Missing, stale, malformed, unsafe,
unreachable, behind, ahead, divergent, multiple-head, or interrupted-migration
state refuses closed. There is no override.

Setup never imports or invokes Alembic execution. Refusal provides only the
separately authorized `context-engine-control migrate` followed by an explicit
setup rerun. Setup does not delete, stop, install, reload, or overwrite running
services or unknown artifacts; launchd lifecycle remains maintainer-owned.

## Consequences

- Re-running setup at unchanged code and exact schema head is idempotent.
- A code update cannot publish a new ready manifest over a mismatched database.
- A previously ready manifest is not authority after either code or schema
  changes; every content-process start revalidates both.
- Database bootstrap and backup remain non-content operations and do not require
  the ready binding.
