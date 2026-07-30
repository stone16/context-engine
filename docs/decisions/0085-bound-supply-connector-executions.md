---
name: adr-0085-bound-supply-connector-executions
version: "1.0.0"
description: >
  Bound each Supply connector execution by validated page, cumulative-byte,
  and no-progress limits before accepting additional source observations.
---

# 0085. Bound Supply connector executions before acceptance

- Status: accepted
- Date: 2026-07-30
- Refines: ADR-0075

## Context

The Supply execution bridge polls connector-runner code until a terminal page
arrives. Empty non-terminal pages are valid, so a connector that neither emits
observations nor advances its opaque checkpoint can otherwise poll a customer
source indefinitely. Page count alone is also insufficient because a page may
reach the staged-page byte ceiling. After ACL freshness was added to delete
observations, every delete element may include an evidence payload up to the
same one-MiB evidence ceiling as an upsert.

## Decision

1. Every bridge construction owns an immutable `SupplyExecutionConfiguration`.
   Page count, cumulative serialized bytes, and consecutive no-progress pages
   are positive values validated against server-owned ceilings when that
   configuration is constructed.
2. The cumulative-byte default is sixteen times `_MAX_STAGED_PAGE_BYTES`; its
   server-owned ceiling is 256 times `_MAX_STAGED_PAGE_BYTES`. This sizing
   remains valid for post-ACL-freshness delete observations because it budgets
   exact canonical staged bytes rather than assuming the former small delete
   reference shape or an element count.
3. Page and byte budgets apply per `execute` call. A page consumes its exact
   `serialize_supply_change_page` byte length. Once the page count is exhausted,
   the bridge makes no further connector call. A page that would exceed the
   cumulative-byte budget is refused before its acceptance transaction.
4. No progress means a non-terminal page with no upserts, no delete
   observations, and a checkpoint proposal byte-equal to the checkpoint loaded
   for that poll. Consecutive no-progress pages consume a separately validated
   allowance; the next such page is refused before acceptance. A terminal page
   is always a genuine terminal outcome, including when empty.
5. Exhaustion raises `SupplyExecutionBoundExceeded` with exactly one closed
   `SupplyExecutionBoundReason`: `page_count`, `page_bytes`,
   `cumulative_bytes`, or `no_progress`. `page_bytes` identifies the existing
   staged-page ceiling separately from the configurable cumulative allowance.
   The exception retains no source content, page reference, checkpoint, size,
   or count.
6. Bounds are checked before the offending page's acceptance transaction.
   Previously committed pages and their checkpoints remain durable; the
   checkpoint never advances to the refused page.

## Consequences

- Broken or untrusted connector code cannot poll a customer source forever in
  one execution.
- Realistic executions keep a generous default while even worst-case staged
  pages have a finite cumulative allowance.
- Operators can distinguish a connector-declared terminal page from each
  closed execution refusal without receiving source-derived diagnostics.
- A later execution may resume from the last durably accepted checkpoint under
  the existing exact WorkerLease rules.

## Revisit trigger

Revisit the configured defaults or ceilings only with measured connector page
distributions, a changed staged-page/evidence ceiling, or a source whose valid
polling protocol requires a different explicit progress signal.
