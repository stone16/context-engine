---
name: adr-0065-recurse-file-discovery-with-anchored-descriptors
version: "1.0.0"
description: >
  Expand File discovery to canonical nested Markdown paths through anchored
  no-follow descriptors and one server-owned bounded byte ceiling.
---

# 0065. Recurse File discovery with anchored descriptors

- Status: accepted
- Date: 2026-07-26
- Refines: ADR-0035, ADR-0037, ADR-0054, ADR-0058, ADR-0059

## Context

The active File provider observes only Markdown files directly beneath one
registered logical root, and the Supply worker fixes each read to 4 KiB. The
first dogfood corpus selected by ADR-0062 is organized into nested directories
and contains ordinary Markdown documents beyond that ceiling. Flattening or
splitting the source would make host layout part of the content contract and
would not exercise the provider shape needed by a real corpus.

Recursive traversal creates a security and consistency trade-off: path-string
recursion or symlink-following enumeration can escape the registered root, while
an unstable tree can mix observations from different filesystem states into one
signed change page. An unbounded byte setting would move memory authority into
deployment configuration instead of retaining a server-owned ceiling.

## Decision

`FileImportPath` accepts one canonical relative Markdown path with bounded,
nonempty components and no `.` / `..`, backslash, control, or surrogate values.
Discovery orders all canonical paths by UTF-8 bytes before existing baseline and
page logic runs. The existing 100-change page and 10,000-path baseline bounds do
not change; exceeding the baseline fails closed rather than truncating.

The File adapter requires platform `O_DIRECTORY` and `O_NOFOLLOW` support. It
opens every root and descendant directory relative to an already-open directory
descriptor, never follows a directory or final-file symlink, and reads regular
files relative to the current parent descriptor. Every relevant directory
snapshot and every opened file is revalidated with stable device, inode, type,
size, modification-time, and change-time identity. A path escape, non-regular
target, symlink, oversize file, or unstable traversal produces no signed mixed
snapshot.

The Supply worker receives one server-owned `max_file_bytes` setting. It defaults
to 1 MiB and is accepted only from 1 byte through 64 MiB. The same ceiling is
used for direct import, scheduled dispatch, and long-running dispatch. Only
Markdown files are discoverable at any depth.

The durable File path constraints and page-accept functions accept the same
canonical nested domain. Their migration holds the existing scheduling and
dispatch fences, and downgrade is refused while any retained File lineage has a
nested path. Prior flat baselines remain valid: newly visible nested paths are
upserts and still-present flat paths are not deletes.

## Rationale

Descriptor-relative traversal makes the registered root an operating-system
capability instead of trusting path normalization alone. Reusing the current
directory descriptor for reads avoids re-walking each ancestor while preserving
the same before/after proof. A bounded deployment setting admits real documents
without making the worker's memory exposure unbounded.

ADR-0064 classifies provider traversal and ingestion limits as product-lane work.
This decision is nevertheless recorded because the platform-capability veto,
filesystem snapshot model, and shared byte ceiling are hard to reverse and would
surprise a reader if they lived only in tests. It adds no security-catalog entry.

## Consequences

- Nested and flat Markdown files share deterministic change-page, replay,
  supersession, acknowledgement, and publication semantics.
- Directory symlinks and platforms without mandatory no-follow directory flags
  are unsupported rather than silently weakened.
- A complete recursive scan remains proportional to the retained tree and is
  capped at 10,000 relevant paths.
- The worker can ingest ordinary Markdown documents up to the configured bound,
  while every oversize read retains exact fail-closed behavior.
- Historical ADR-0054 shallow-scan statements remain true for that activation;
  this decision is the forward refinement.
- Runtime delivery, authorization, deletion authority, and the security catalog
  do not change.

## Revisit trigger

Revisit before adding filesystem watchers, provider polling changes, full resync,
checkpoint compaction, a higher or per-tenant byte ceiling, non-Markdown files,
or platforms without descriptor-relative no-follow traversal. Any revision must
preserve root confinement, stable whole-snapshot signing, bounded memory, prior
baseline compatibility, and the existing page/checkpoint authority split.
