---
name: adr-0082-recover-the-golden-corpus-and-refuse-stale-lineage
version: "1.0.0"
description: >
  Keep the private golden corpus recoverable through a second durable root and
  refuse stale expectation lineage instead of scoring it. Use when backing up,
  recovering, or evaluating the maintainer corpus. Not for changing golden
  schema, judges, thresholds, or the public-subset promotion authority.
---

# 0082. Recover the golden corpus and refuse stale lineage

- Status: accepted
- Date: 2026-07-30
- Refines: ADR-0080

## Context

ADR-0080 and `eval/README.md` place the maintainer corpus in one durable root
outside every Git worktree, configured as `CONTEXT_ENGINE_GOLDEN_ROOT`. That
rule keeps the corpus out of a disposable worktree, but a single durable copy
is still a single copy: an accidental delete, a bad edit, or a failing disk
destroys the project's only quality authority, and nothing in the repository
can reconstruct it.

The corpus also has a second, quieter failure. Golden expectations bind to
exact `source/resource/revision/fragment` refs. ADR-0018 publishes content as
immutable Revisions, and ADR-0033 promotes them through one Release owner, so
a promotion can leave an expectation pointing at a Revision that no longer
resolves. The retrieval judge compares expected refs against observed refs and
has no way to tell "the engine failed to retrieve this" from "this expectation
names a Revision that no longer exists". Both render as `evidence_recall = 0`.
A bookkeeping problem would then look like a quality regression and could
drive a wrong tuning decision.

## Decision

1. Backups live under a second configured durable root,
   `CONTEXT_ENGINE_GOLDEN_BACKUP_ROOT`, validated by the same contract as the
   corpus root: absolute, existing, not a symlink, outside every Git worktree,
   never under an ignored `.context-engine` directory. Neither root may contain
   the other, so one deletion cannot remove both copies. Neither root has a
   worktree-local default.
2. A backup is an immutable snapshot directory named by its recorded UTC
   instant. It is written into a staging directory, verified byte for byte
   against the digests recorded for its sources, fsynced, and only then renamed
   into place. An interrupted run leaves no snapshot and no staging directory,
   so a partial backup can never be mistaken for a complete one.
3. Each snapshot records a manifest of relative name, exact byte length, and
   SHA-256 per file, plus one content digest over that whole record. The same
   run is idempotent: identical content records no second snapshot. A backup
   that is not newer than the newest recorded snapshot is refused unless the
   operator passes an explicit flag, and even then the newer snapshot remains
   the one recovery restores.
4. Verification refuses truncation, corruption, missing content, unexpected
   content, a manifest that disagrees with its own record, and any file or
   directory readable beyond its owner. Recovery verifies first and refuses a
   non-empty destination, so it can neither restore corrupted content nor
   silently overwrite a working copy.
5. Expectation lineage resolves against one captured lineage map: exactly the
   Evidence lineage that resolves in one promoted Release. A case whose
   expected lineage no longer resolves is reported as `stale_lineage` with
   counts only, is excluded from every judge input, and refuses the whole
   report. It is never scored, and never counted as a retrieval miss.
6. No corpus path reaches a command line, a log line, or a refusal message. The
   backup commands take no path argument at all: roots come from the configured
   environment, and every message carries counts, snapshot instants, and
   digests only. Operating-system error text is replaced rather than echoed,
   because it names files.

## Rationale

Copy-then-rename is the smallest mechanism that makes "a snapshot exists"
equivalent to "a complete, verified snapshot exists", which is what recovery
needs to trust. Recording digests per file and one digest per snapshot lets a
recovery prove it restored the same bytes rather than asserting it.

Refusing a stale set is the conservative reading of a genuinely ambiguous
signal. Once refs stop resolving, no honest quality number is available for
those cases, and a partial number invites exactly the wrong conclusion. The
maintainer recaptures the lineage map and the refusal clears; nothing silently
degrades in between.

## Consequences

- A corpus loss is recoverable from the newest verified snapshot, and the
  recovery reproduces the same case count, set digest, and pilot digest.
- Backups accumulate as immutable snapshots. Retention is a maintainer
  decision; the tooling deletes nothing.
- After each Release promotion the maintainer must recapture the lineage map
  and rerun the check before evaluating, or the report refuses. That cost is
  deliberate.
- Under M1's single trusted local operator threat model these mechanisms
  prevent accident and detect corruption. They are not forgery-proof: an
  operator who deliberately rewrites both a snapshot and its manifest can
  produce a self-consistent backup, exactly as `eval/README.md` already states
  for the co-located lock chain. No signing or keyring boundary is added.
- The lineage map is captured, not derived from a live index, because M1 ships
  no evaluation run executor. Issue #160 owns that executor.

## Revisit trigger

Revisit when the run executor from issue #160 can resolve expectation lineage
against the live index, which would replace the captured map with a live check.
Revisit sooner if evaluation gains an untrusted caller or a remote runner, or
if a corpus large enough to make whole-root snapshots impractical arrives; any
revision must keep recovery verifiable and must not let unresolvable lineage
reach a judge.
