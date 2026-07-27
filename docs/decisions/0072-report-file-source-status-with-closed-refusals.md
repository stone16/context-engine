---
name: adr-0072-report-file-source-status-with-closed-refusals
version: "1.0.0"
description: >
  Expose content-free File source operational status and retain only a closed
  compilation-refusal category for currently observed unpublished paths.
---

# 0072. Report File source status with closed refusals

- Status: accepted
- Date: 2026-07-27
- Refines: ADR-0036, ADR-0038, ADR-0043, ADR-0069, ADR-0071

## Context

The existing File progress read separates durable acquisition from publication,
but no supported operator command renders it. Compilation failures also make a
scheduled path disappear from operator view because the worker intentionally
returns only a generic refusal and previously retained no safe classification.
An operator therefore cannot distinguish an empty healthy source from one that
has never published, or identify a currently observed path that the active
compiler refuses.

The compiler's closed failure code is useful operational evidence. Source text,
parser diagnostics, exception details, and compiler internals are not required
for this status and would widen retained content and disclosure risk.

## Decision

`context-engine-control status` consumes one operation-exact
`READ_SOURCE_PROGRESS` call for one Organization and registered File Source. It
renders the acquisition checkpoint, contiguous publish watermark, current scan
head, complete-baseline size, active Resource count, last successful File
publication time and age, and canonically ordered current compilation refusals.
A source with no successful publication renders an explicit `never` state.

When compilation returns a closed `CompilationFailure`, the worker uses one
function-only transaction to perform the existing generic terminal failure and
retain exactly its category on the durable job. The allowed values are
`invalid_utf8`, `unsupported_construct`, and `unsupported_document_shape`.
The function accepts no source content or diagnostic string, and its failed
update rolls back the whole terminal transition. The caller continues to
receive the same generic `FileImportRefused`.

Category-bearing failure holds a shared File-status migration fence and
rechecks that its retained column still exists before the terminal transition.
Downgrade holds the matching exclusive fence before checking retained state,
so it either observes an earlier category and refuses or prevents an old
function body from writing after the column is removed.

Status derives refusals only for upsert paths in the latest complete scan of the
active SourceVersion and binds each result to that exact path, SHA-256, and byte
length. A failed changed observation remains visible even while the prior
published Revision stays active, and an unchanged refused observation retains
its prior failure without scheduling duplicate work. A refusal drops from
status after its path is deleted from the next complete scan. The retained job
category remains durable history; the status projection determines current
relevance.

The projection is Control-only, scoped by tenant and Source through FORCE RLS,
and content-free. Progress, baseline, pending schedules, and status are composed
in one PostgreSQL statement snapshot. It is operational evidence only. It
cannot authorize Runtime, emit `stale_evidence`, mutate work, retry a job,
repair a source, or change the Markdown grammar.

## Consequences

- Operators can compare accepted observations with published visibility and
  distinguish `never` from a healthy empty source.
- A refused current path becomes visible without retaining its note body or a
  compiler diagnostic.
- Last-success age is database-observed and naturally changes between calls;
  the JSON keys, ordering, UTC timestamps, and integer age representation are
  deterministic.
- One nullable closed category is added to the mutable terminal job rather than
  creating a content-bearing diagnostic record or another process.
- No HTTP, metrics, dashboard, watcher, repair, retry, or Runtime coverage
  surface is activated.

## Revisit trigger

Revisit before adding a refusal category, retaining any diagnostic payload,
reporting incomplete scans as current truth, aggregating across Organizations,
or allowing File progress or status to influence Runtime authorization.
