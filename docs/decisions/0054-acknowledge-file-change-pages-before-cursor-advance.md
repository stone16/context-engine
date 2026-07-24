---
name: adr-0054-acknowledge-file-change-pages-before-cursor-advance
version: "1.0.0"
description: >
  Activate bounded File readChanges while keeping provider proposals distinct
  from Control-accepted acquisition checkpoints.
---

# 0054. Acknowledge File change pages before cursor advance

- Status: accepted
- Date: 2026-07-25
- Refines: ADR-0012, ADR-0043, ADR-0044

## Context

Issue #81 activates the first File `readChanges` carrier. A shallow filesystem
scan can deterministically propose an ordered page, but observing files does not
prove that ContextControl durably accepted every envelope. Returning the same
cursor type before and after commit would let callers continue a scan while the
acquisition checkpoint still points at the previous accepted page.

The provider page also has no delivery audience. Creating publishable import
jobs during page acceptance would invent FileSourceAccess authority and conflate
acquisition progress with Runtime visibility.

## Decision

The active File capability advances from immutable v2 to immutable v3 only
through ContextControl. V3 activates bounded `describeCapabilities`,
`readChanges`, cursor semantics, checkpoint semantics, and the provider
checkpoint operation. `discover`, `authorizeAndProject`, deletion execution,
freshness, and consistency guarantees remain unavailable.

`InitialScan` observes only shallow regular Markdown files under the anchored
logical root. It orders names by UTF-8 bytes, does not follow symlinks, and emits
content-free change envelopes containing canonical path, content digest, byte
length, change kind, Organization, Source, SourceVersion, and scan binding.
Before returning success, the Provider re-lists the anchored root and
revalidates every observed file identity; a changed directory membership or
file identity closes as retryable-unavailable rather than signing a mixed
snapshot. The database path constraint accepts the exact shallow Markdown
filename domain owned by `FileImportPath`, including the minimal `.md` name.

The provider signs the canonical whole page and returns a
`PendingChangeCursor`. ContextControl verifies that page proof, then a narrow
SECURITY DEFINER transaction requires the same active v3 SourceVersion, exact
predecessor page, and complete bounded change array. That transaction appends
the immutable page, every immutable change envelope, and one entry in the
existing acquisition-checkpoint sequence. Exact replay returns the same receipt;
out-of-order, changed, foreign, disabled, or stale acceptance returns nothing.

Only after the transaction commits does the PostgreSQL Control adapter wrap the
pending provider cursor in a separately authenticated `ChangeCursor` bound to
the accepted page digest, checkpoint reference, and global acquisition sequence.
Provider pages and Control checkpoints use opposite Ed25519 key directions: each
consumer holds only the other boundary's public verification key, never its
private signing authority. The provider accepts only this post-commit cursor
form for continuation. Keys are injected composition contracts; page and cursor
values remain opaque and redacted from representations.

Every initial source state receives an opaque scan epoch derived from the
canonical state plus the current Control-read durable scan head. It is stable
across Provider restarts while bytes and durable head remain unchanged, but
advances whenever the observed state changes relative to that head. Every
continuation page carries that epoch plus its preceding accepted page,
checkpoint, and sequence. A new epoch also carries the immediately superseded
epoch so Control can atomically replace the current scan; it cannot name any
older epoch. Control exposes this head only through the trusted Source progress
read model, and the caller must recompose `FileChangeSource` from that value
before Provider I/O. The acceptance transaction compares these bindings with
the latest accepted File change page for the Source, not merely the latest page
within one content-derived scan. Re-presenting the same durable state is exact
replay rather than a new checkpoint; a stale initial page cannot regress a
newer completed scan. This globally monotonic rule remains crash-safe across
Provider restart and rejects stale continuation cursors, including ABA source
state reversion.

Provider-page checkpoints carry no publish outcome and therefore do not create
gaps in the publication-bearing watermark prefix. Runtime has no privileges on
page, change, or checkpoint tables and does not consult them as authorization.
Page acceptance creates no acquisition job, WorkerLease, Revision, candidate,
Resource policy, or publish watermark. Later scheduling must establish an
existing File import audience and WorkerLease lineage explicitly.

## Rationale

Acknowledging a signed whole page before issuing its continuation cursor makes
the durable checkpoint—not Provider process memory—the source of truth. A full
shallow rescan on each request deliberately preserves restart-safe snapshot
validation; caching or incremental watching would introduce new invalidation
and recovery semantics that this fixed point has not proven. Separate signing
directions and a private cursor-payload encoder also keep page proposal and
checkpoint minting authority from becoming a shared public API.

## Consequences

- A filesystem observation cannot advance the durable cursor by itself.
- Provider verification cannot mint a Control checkpoint, and Control
  verification cannot mint a Provider page.
- An old scan cannot resume after a newer scan becomes current, including ABA
  source-state reversion.
- Whole-page replay is idempotent, while partial insertion cannot commit.
- Cross-Organization, SourceVersion change, and source disable fail closed.
- Manual v3 File imports remain available and keep their existing audience/job
  path; provider changes do not inherit that audience implicitly.
- Recursive scanning, watching, deletion execution, automatic scheduling,
  retries, dead-letter handling, and full resync remain inactive.
- Downgrade is refused after any accepted provider page because deleting or
  renumbering acquisition history would violate opaque monotonic checkpoints.

## Revisit trigger

Revisit before automatic scheduling, deletion execution, checkpoint compaction,
key rotation, recursive discovery, or full resync. Any revision must preserve
post-commit cursor issuance, exact tenant/source/version lineage, whole-page
atomicity, replay idempotency, and Runtime independence.
