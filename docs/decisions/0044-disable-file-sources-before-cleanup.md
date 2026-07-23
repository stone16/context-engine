---
name: adr-0044-disable-file-sources-before-cleanup
version: "1.0.0"
description: >
  Disable one File ContextSource atomically with its Organization Policy Epoch,
  cancel outstanding work, and retain a durable asynchronous-cleanup intent.
---

# 0044. Disable File sources before cleanup

- Status: accepted
- Date: 2026-07-23
- Refines: ADR-0010, ADR-0027, ADR-0030, ADR-0035, ADR-0037, ADR-0042, ADR-0043

## Context

Offboarding a source is broader than tombstoning one Resource. It must stop new
acquisition and every outstanding worker attempt, make all of the source's
published Resources immediately ineligible for Runtime delivery, and prevent a
new source-bound read capability. Waiting for Revision, Fragment, candidate,
or blob deletion would make revocation depend on an unbounded maintenance job.
Deleting that lineage synchronously would erase the evidence required to audit
and eventually complete the cleanup.

Issue #30 needs one terminal manual offboarding carrier for a known registered
File source. File has no remote credential to revoke. Re-enable, physical
cleanup execution, retention expiry, and remote-provider token revocation are
separate capabilities.

## Decision

`ContextControl.offboard_file_source` accepts one operation-bound trusted
Control call and one exact Source reference. Organization, active SourceVersion,
trusted time, cleanup-intent identity, retained-artifact counts, and the next
Policy Epoch are database-owned facts.

One SECURITY DEFINER PostgreSQL transaction takes the Organization publication
advisory lock exclusively, locks the active ContextSource and current Policy
Epoch, and then atomically:

1. changes the stable ContextSource lifecycle from `active` to `disabled` and
   records the exact immutable active SourceVersion at that boundary;
2. advances the Organization Policy Epoch once;
3. appends one immutable pending `file_source_cleanup_intent`; and
4. cancels every nonterminal File import job for that Source with zero effects,
   while retaining its exact prior job and lease lineage.

The active-version pointer, SourceVersion, Resource, Revision, Fragment,
candidate, publication, and progress rows remain physically present. Runtime's
current-transaction source-lifecycle predicate and exact authorization
projection make all of those retained rows ineligible. A deliberately stale
`CandidateRef` therefore produces the canonical empty HTTP Package, not source
existence detail.

File job creation shares the same Organization publication lock and refuses a
disabled Source. Worker job/lease database policies require the exact Source to
remain active, so an available job cannot obtain a new lease and a previously
issued lease cannot redeem, recover, or publish after commit. The source check
is a database authorization predicate, not a process-local scheduling hint.

The existing `ContextAccessTicket` carrier validates current Policy Epoch
before signing. When issuance or redemption explicitly binds a File Source,
the same current UserActor transaction must also observe that Source as active.
The Issue #18 unbound synthetic ticket remains an explicitly synthetic carrier;
it is not authority to read an arbitrary File Source. Readers accept the
pre-extension ticket document as an unbound synthetic ticket so the optional
claim does not reinterpret old bytes as source authority.

The first committed offboarding is terminal for this slice. A repeat for the
same Organization and Source returns the original database-authored result
without another epoch bump, cancellation, or cleanup intent. Wrong-Organization,
missing, and already-ineligible sources expose the same generic refusal. The
cleanup intent is an obligation only: application roles cannot read it directly
and no cleanup consumer is activated by this decision.

Offboarding does not append an acquisition checkpoint or publish watermark.
Those streams describe accepted content changes and contiguous publication,
not source lifecycle or cleanup completion. Their retained history remains
operational evidence and never overrides the disabled lifecycle.

## Rationale

The smallest security-critical action is a lifecycle transition plus epoch
advance, not physical deletion. Keeping those writes, job cancellation, and the
durable cleanup obligation in one database transaction eliminates windows in
which a disabled source can still admit work or in which cleanup loses its
lineage. Reusing the publication lock serializes Runtime, publication, job
admission, tombstone, and source offboarding at their visibility boundaries
without turning that lock into authorization.

Mutating or deleting the active SourceVersion was rejected because versions are
immutable configuration evidence. Bulk-tombstoning every Resource was rejected
because transaction cost would scale with source size and duplicate a source-
level policy fact. Epoch-only and application-only checks were rejected because
stale candidates, direct worker functions, and fresh post-epoch tickets would
remain usable. Cleanup-first was rejected because maintenance latency cannot
define revocation.

## Consequences

- The first resolve beginning after commit returns zero Evidence for the Source,
  including when stale candidates and physical content remain.
- No new File import job, WorkerLease, source-bound ContextAccessTicket, recovery,
  or publication effect can cross the committed disabled lifecycle.
- A worker that already acquired source bytes may finish local computation, but
  its next database effect is refused; canceled jobs retain `effect_count = 0`.
- Other Organizations, including one with identical File bytes, source names,
  or Resource references, retain independent lifecycle and Policy Epoch state.
- A committed source cleanup obligation prevents schema downgrade until an
  explicit operator workflow has dealt with it; forward repair is required.
- File capability declarations still do not claim native watcher offboarding,
  cleanup execution, source re-enable, or remote credential revocation.

## Revisit trigger

Revisit before cleanup workers, re-enable/re-registration semantics, retention
expiry, native watcher offboarding, source replacement, or a remote provider
with revocable credentials. Any addition must preserve atomic tenant-owned
revocation, stale-candidate non-delivery, zero post-offboard worker effects,
generic non-enumeration, immutable lineage, and idempotent cleanup identity.
