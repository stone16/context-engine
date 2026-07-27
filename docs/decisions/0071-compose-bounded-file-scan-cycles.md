---
name: adr-0071-compose-bounded-file-scan-cycles
version: "1.0.0"
description: >
  Compose one operator-invoked File acquisition cycle from separate exact
  read, accept, and schedule Control calls while preserving checkpoint
  idempotence.
---

# 0071. Compose bounded File scan cycles

- Status: accepted
- Date: 2026-07-27
- Refines: ADR-0054, ADR-0055, ADR-0058, ADR-0059, ADR-0068, ADR-0069

## Context

The shipped local operator can register and activate a File source, while the
provider, durable page acceptance, explicit audience-bound scheduling, and
autonomous worker dispatch already exist as separate proven modules. A scan
caller must compose them without collapsing their authority boundaries.

One scan may require several provider pages and cannot hold one
`TrustedControlCall` across the cycle. Acceptance and scheduling are distinct
operations by design: accepting content-free provider observations does not
infer a delivery audience. Process interruption can occur after either durable
step. Their transactions remain independently idempotent. This first command
needs narrow reconciliation for accepted current-scan pages missing jobs, while
broader workflow recovery remains outside its activation.

The existing page scheduler is all-or-none. The File provider emits a complete
snapshot of upserts plus baseline-derived deletes, so scheduling a larger page
would re-import unchanged paths whenever one path changed.

## Decision

`context-engine-control scan` is one bounded, operator-invoked acquisition
cycle. It obtains short-lived, operation-exact trusted calls separately for
`READ_SOURCE`, `READ_SOURCE_PROGRESS`, every
`ACCEPT_FILE_CHANGE_PAGE`, and every required
`SCHEDULE_FILE_CHANGE_PAGE`. The configured Control identity must enumerate
those operations, but no call carries more than one. The process adds no HTTP
surface and does not hold an ambient Control call.

This explicitly refines ADR-0069's “one operation per invocation” rule for a
bounded workflow command: leaf Control commands still map one invocation to
one call, while a workflow invocation may obtain a sequence of independently
consumed calls. The invariant is one operation per `TrustedControlCall`; the
workflow cannot request the full operation set, reuse a call, or invoke an
operation absent from its configured allowlist.

The application fixes provider pages to one observation. This preserves the
existing all-or-none scheduler while allowing it to schedule only new or
content-changed upserts. Deletes are accepted and counted but never scheduled
or executed. Scan requires the exact v4 delete-observation manifest because
that carrier provides the complete durable comparison baseline; v1-v3 sources
fail closed. A complete changed scan advances that baseline.

An exact unchanged scan is recognized only when the complete baseline is also
the durable head and the provider reproduces that scan identity. The report
retains the already accepted durable checkpoint and counts zero accepted
changes and deletes. Before returning, the same `READ_SOURCE_PROGRESS` call
also projects accepted upsert pages in the current scan epoch with no durable
acquisition. Scan schedules those missing jobs idempotently and includes them
in the scheduled-import and compilation-refusal counts. It does not create
another scan epoch or duplicate an existing job.

Scan and worker share one server-owned anchored root registry, byte ceiling,
and exact active Markdown configuration pin. Scan preflights newly scheduled
paths only to produce the aggregate compilation-refusal count. It rechecks the
accepted byte length and SHA-256 before compiling; filesystem drift fails the
cycle generically instead of reporting on different bytes. Scan cannot mark a
job terminal or publish. The autonomous worker remains the sole compiler and
publisher and independently rechecks the accepted raw identity before content
work.

The provider-page and checkpoint proof keys are explicit persistent Ed25519
secrets, distinct from each other and from the Control, release, dogfood, and
worker secrets. The worker secret is already part of ADR-0069's complete local
operator configuration; scan reads it only for local cross-plane collision
checking, never for lease issuance or redemption. Scan output contains only
deterministic content-free counts, the `SourceRef`, and the accepted opaque
checkpoint reference.

This decision explicitly refines ADR-0068 decision 7's local migrator seed
boundary. In addition to the existing Organization/User/current-Membership
identity tuple, that command may optionally create the one exact enabled
File-import ServicePrincipal required by this composition. The optional
identifier is explicit and idempotent only for the fixed `supply.file-import`,
`context-engine-worker`, `file.import` binding; a conflicting or disabled row
refuses the whole seed transaction. The seed remains a pre-process bootstrap
operation. Neither scan nor worker receives migration authority, and the
Runtime process remains unable to create identities or receivers.

## Consequences

- One-note additions create exactly one durable import without widening the
  page scheduler or Markdown grammar.
- Exact unchanged scans create no scan epoch or Revision. They create no job
  when the accepted scan is already fully scheduled; recovery may create only
  the jobs missing from an accepted current-scan page.
- A source with more observations performs more short-lived Control calls, but
  each authorization and durable transaction remains independently bounded.
- Because the provider revalidates the full root for each continuation and the
  existing scheduler cannot select a subset of one accepted page, singleton
  pages make this first local composition quadratic in observed path count.
  It is suitable for the initial measured maintainer corpus, not a general
  large-root synchronization loop. Larger rollout requires an exact durable
  selected-upsert scheduling contract or a restart-safe provider snapshot; it
  must not silently batch unchanged upserts.
- The cycle is not atomic as a whole. Existing accepted-page and scheduled-job
  transactions remain its durable boundaries. A later scan reconciles an
  accepted current-scan upsert page that has no acquisition only when its
  durable `page_limit` is the composition's exact singleton limit. Foreign or
  future larger pages are not adopted because their all-or-none scheduling
  could re-import baseline-identical upserts. Broader workflow recovery remains
  inactive.
- Polling, watching, full resync, delete execution, alternate publication,
  non-File providers, and network operator access remain inactive.

## Revisit trigger

Revisit before batching more than one observation into a scheduling decision,
adding a daemon, changing the provider from complete snapshots to deltas, or
making compilation refusal a durable pre-worker state transition. Measurement
showing that singleton-page root revalidation is operationally material is also
an immediate revisit trigger.
