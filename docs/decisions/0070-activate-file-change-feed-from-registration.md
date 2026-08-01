---
name: adr-0070-activate-file-change-feed-from-registration
version: "1.0.0"
description: >
  Permit the existing change-feed Control operation to advance either a
  registered v1 or import-enabled v2 File source to the same immutable v3
  capability manifest. Use when advancing a registered File Source into the
  existing change-feed capability. Not for a second feed protocol, new provider
  semantics, or caller-authored capability manifests.
---

# 0070. Activate a File change feed from registration

- Status: accepted
- Date: 2026-07-27
- Refines: ADR-0035, ADR-0037, ADR-0054, ADR-0069

## Context

File source registration creates the immutable v1 capability manifest. The
first manual import operation can advance v1 to v2 while creating an exact
audience-bound import job, and change-feed activation historically accepted
only v2 before creating v3. That ordering reflected the implementation
sequence, not a security dependency: v3 contains the v2 import capabilities
plus the change-provider carriers, while activation itself creates no import,
audience, job, lease, or content.

The local operator workflow exposed the mismatch. A maintainer can register a
source, but cannot activate its change feed without first naming and scheduling
one manual file. An operator-invoked initial scan cannot provide that missing
step because the File provider correctly refuses `readChanges` until v3 is
active. Requiring a fabricated bootstrap import would create unrelated durable
work and make an empty registered root impossible to scan.

## Decision

`ACTIVATE_FILE_CHANGE_FEED` may atomically advance an active File source from
either the exact v1 registration manifest or the exact v2 import manifest to
the existing server-owned immutable v3 manifest. An already-active exact v3
remains an idempotent replay. Every other manifest, a disabled or foreign
source, and every non-Control database caller continue to receive the existing
generic refusal.

The transition remains inside the existing SECURITY DEFINER database function
and runs through `ControlOperatorAuthority`, one operation-bound
`TrustedControlCall`, `ContextControl`, the non-owner Control role, and FORCE
RLS. The command supplies only a `SourceRef`; it cannot construct a manifest or
version. Direct v1-to-v3 activation creates only one `SourceVersion` and updates
the active pointer. It does not read the filesystem or create an acquisition,
job, audience, WorkerLease, checkpoint, Resource, Revision, or Fragment.

V2 remains valid for manual-import-first sources. It is no longer a mandatory
ceremonial waypoint for sources whose first acquisition is a change scan.

## Consequences

- A registered empty or populated File root can become scan-capable without a
  fake manual import.
- Manual-import-first and scan-first sources converge on the same v3 manifest
  and retain the same downstream authorization and scheduling boundaries.
- Change-feed activation does not prove filesystem reachability. The provider's
  separately configured anchored root registry remains responsible for that
  check when a scan actually runs.
- Downgrade restores the v2-only precondition for future calls without
  rewriting retained immutable v3 source history.

## Revisit trigger

Revisit before change-feed activation creates durable work, accepts a manifest
other than exact v1/v2/v3, or derives filesystem or audience authority from a
logical `FileRootRef`.
