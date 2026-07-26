---
name: adr-0059-dispatch-scheduled-file-imports-through-exact-leases
version: "1.0.0"
description: >
  Let the independent Supply worker select first-attempt scheduled File imports
  without caller tenant routing or broader Control authority.
---

# 0059. Dispatch scheduled File imports through exact leases

- Status: accepted
- Date: 2026-07-25
- Refines: ADR-0029, ADR-0037, ADR-0043, ADR-0055, ADR-0058

## Context

Accepted File pages can create exact `file_import_job` rows, but the Supply
process previously required an operator to provide Organization, Source, job,
receiver, and a pre-minted WorkerLease. Those values are an evidence seam, not
a safe autonomous selector. Giving the process the Control login would also
combine tenant choice, scheduling, and lease issuance authority.

## Decision

The existing Supply process may open two independently guarded pools: the
function-only `context_engine_scheduler` login and the existing worker login.
The scheduler login can execute only `context_scheduler_claim_file_import`; it
cannot read application tables or mutate Control/Runtime state. The function is
owned by a dedicated NOLOGIN File-dispatch definer so its cross-Organization
selection policies do not enlarge any existing definer function.

The function accepts only a fresh 32-byte nonce, current signing-key version,
and the distinct set of server-configured logical root references. It returns
typed no-work without leasing anything if the globally oldest eligible
candidate's exact root is absent from that capability set. The set is an
all-or-nothing capability assertion applied after global selection, never a
routing filter; host paths never enter PostgreSQL.
It selects the oldest current v3/v4 page-scheduled upsert by database acceptance
time, then checkpoint/page/change and stable Organization/Source/job identity.
It first locks the selected job, then holds the same per-Source progress advisory
lock as page acceptance, refreshes its database statement snapshot, and
refreshes database wall-clock time before revalidating the latest scan epoch.
It also requires the selected SourceVersion root to remain in the configured
registry and locks mutable Source, Membership, and
receiver authority rows with `FOR UPDATE SKIP LOCKED`, and changes only that job
from available generation zero to a database-timed leased generation one.
Current SourceVersion, Membership version, and exact enabled File import receiver
are thus revalidated and fenced in the claim transaction. Manual jobs, delete
observations, disabled sources, stale pages, and every later job state remain
invisible to the scheduler.

Python mints the existing versioned WorkerLease solely from returned claims and
fresh nonce, constructs the existing `FileImportLeaseRedemption`, and invokes
`PostgreSQLFileImportWorker`. Immediate verification reads the worker database
clock at whole-second protocol precision, matching the database-issued lease
timestamps and the redemption function's authoritative expiry check rather than
depending on host-clock alignment. Dispatch loads every served logical root from one
server-owned JSON registry, so cross-Organization selection cannot consume an
eligible job merely because another configured root was omitted. A claim is
marked internally for downgrade
fencing; process output contains only `dispatched`, `no_work`, or the closed
job-level `refused` outcome for exact lease rejection and never raw
claims, token, nonce, tenant identity, source bytes, or host path.
A file/content failure becomes job-level `refused` only after the existing
failure transaction durably seals that exact job or current authority rejects
that exact failure transition. Worker infrastructure or failure-recording
unavailability terminates dispatch after the already claimed lease instead of
claiming and stranding additional jobs; automatic retry/backoff remains inactive.

## Consequences

- Concurrent scheduler transactions claim different rows or typed no-work.
- A stop after claim retains one ordinary expiring generation-one lease and no
  publication effect; reclaim and retry remain inactive.
- Delete execution stays exclusively in the trusted #87 Control carrier.
- Stopping dispatch requires only stopping the loop or revoking function
  execution; retained jobs and publication lineage remain untouched.
- Downgrade is refused after this scheduler has claimed a job, because the prior
  schema cannot safely preserve the capability's provenance.

## Revisit trigger

Revisit before expired-lease reclaim, retry/backoff, dead-letter policy,
provider polling, automatic page acceptance, implicit audience selection, or
automatic upsert/delete ordering. Any revision must retain separate scheduler
and worker roles, database-selected tenant routing, exact WorkerLease fencing,
and content-free no-work/output contracts.
