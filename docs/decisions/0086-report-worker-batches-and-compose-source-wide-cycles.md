---
name: adr-0086-report-worker-batches-and-compose-source-wide-cycles
version: "1.0.1"
description: >
  Report privacy-shaped File dispatch batches and let the local operator scan
  or inspect every active File source without caller-copied Source references.
---

# 0086. Report worker batches and compose source-wide cycles

- Status: accepted
- Date: 2026-07-30
- Refines: ADR-0059, ADR-0069, ADR-0071, ADR-0072, ADR-0073

## Context

The autonomous File dispatcher amortizes process startup, but its existing
per-poll output does not answer how many jobs a contiguous drain attempted or
which safe job observation failed. Rendering durable job UUIDs, source paths,
titles, exception text, or trusted routing facts would turn operator
observability into a content and identity disclosure surface. Rendering every
one-second no-work poll also creates unbounded idle chatter.

The local operator can scan and inspect one Source only after a caller copies
its returned `SourceRef`. Repeating that command per Source does not add an
authorization fence: the authenticated Organization and exact Control calls
already determine the boundary. Convenience must nevertheless not create a
new kernel-adjacent operation, reuse a trusted call, widen WorkerLease scope, or
attach Release publication to Supply work.

## Decision

The long-running worker treats the contiguous jobs between two observed
no-work results as one reporting batch. The first claim starts the batch; each
claim increases the observed total, each terminal job increases processed, and
a durable job refusal increases failed. Therefore an in-flight total is the
number of jobs observed so far, while the terminal summary is the exact drained
batch total. No-work before a job emits nothing, and the first no-work after an
active batch emits exactly one summary and resets the reporter.

The worker emits an immediate `dispatching` observation before content work
and repeats it at the server-fixed progress interval until that job returns.
It then emits the terminal job outcome. Every record validates against
`docs/contracts/worker-batch-progress-v1.schema.json`. The contract contains
only aggregate counters, phase, an opaque batch reference, an opaque one-way
job reference, and either `file_import_refused` or `worker_lease_refused`.
File paths, Source or Organization identity, principal facts, lease material,
credentials, exception messages, titles, excerpts, and content have no schema
field. Worker infrastructure failure remains fatal under ADR-0059 and is not
misreported as a durable job refusal.

The existing `FileDispatchLease` remains the only input to execution. The
dispatcher derives reporting attribution from the claimed job UUID, then
passes the claim's unmodified exact redemption to the existing worker. No
lease is cached, generalized, or reused.

`ContextControl.list_sources` consumes one exact `READ_SOURCE` call and returns
the canonically ordered active File manifests visible through the existing
Organization-scoped Control store and FORCE RLS. It introduces no
`ControlOperation`. `context-engine-control scan-all` consumes that discovery
call, then the existing operation-exact read, progress, accept, and schedule
calls independently for each Source. `context-engine-control status` without a
`--source-ref` consumes discovery followed by one independently authorized
`READ_SOURCE_PROGRESS` call per Source. The source-specific forms remain
available. Source-wide status aggregates current refusal categories per Source
and never expands the path-bearing single-Source status projection.

Neither source-wide command calls ContextLearning, constructs a release
candidate, or promotes a Release. Promotion remains the separate explicit
ADR-0073 command under its distinct credential and database planes. The six
variable all-or-nothing ADR-0069 operator opt-in and every credential plane are
unchanged.

## Consequences

- A caller can distinguish active progress, one safe per-job refusal, batch
  completion, and true idle without receiving source content or trusted
  routing facts.
- Progress is bounded by a server-owned interval, while an idle worker emits
  only its existing readiness record.
- Adding another registered active File source changes neither the recurring
  scan command nor the recurring status command and requires no copied
  `SourceRef`.
- Source discovery adds enumeration: an operator with `READ_SOURCE` can list
  every active manifest without already knowing any `SourceRef`, whereas the
  source-specific read requires one. This wider discovery capability is
  intentional for the local source-wide commands; disabled Source history
  remains absent.
- Multi-source scan is a sequence of independently durable bounded cycles, not
  one cross-Source transaction. A source-local generic refusal is reported
  with its already-discovered `SourceRef` and the closed
  `operation_refused` category, and later Sources continue. This prevents a
  later refusal from hiding earlier completed Source cycles. Totals cover only
  completed cycles. Only a source-scoped capability, provider, or file-identity
  refusal is continued; discovery, authorization, configuration, database, or
  process setup failure still refuses the whole command.
- Metrics, HTTP progress, dead-letter transitions, automatic promotion, and
  production operator administration remain inactive.

## Revisit trigger

Revisit before promising a precomputed queue total, exposing progress outside
the local process stream, reporting another failure category, adding retry or
repair policy after a source-wide refusal, listing disabled Source history, or
adding any automatic Release action.
