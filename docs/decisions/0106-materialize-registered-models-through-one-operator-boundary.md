---
name: adr-0106-materialize-registered-models-through-one-operator-boundary
version: "1.0.0"
description: >
  Materialize the exact registered primary local model through one bounded,
  no-clobber operator process. Use when provisioning pinned local model bytes.
  Not for inference, preflight, Supply, Runtime, or Release activation.
---

# 0106. Materialize registered models through one operator boundary

- Status: accepted
- Date: 2026-08-13
- Refines: ADR-0069, ADR-0102, ADR-0105
- Related: [issue #241](https://github.com/stone16/context-engine/issues/241)

## Context

The active local Qwen carrier is network-free and verifies one complete tracked
snapshot. Operators still need a bounded way to obtain those exact bytes.
Allowing ordinary Runtime, Supply, or preflight composition to fetch them would
turn a missing deployment artifact into undeclared network authority and could
mix provisioning with inference or publication.

## Decision

`context-engine-model-materializer` is the only repository-owned model-fetch
composition. Its public input is one durable destination plus the closed
`primary` role. The tracked embedding model registry alone supplies repository
identity, immutable revision, allowed relative paths, artifact hashes, and the
canonical artifact digest. Caller-supplied identity, URL, revision, path, hash,
credential, fallback role, and overwrite controls do not exist.

The process uses HTTPS only in production. It starts at the fixed registered
model host and accepts only same-host redirects or the host's closed CDN suffix;
test composition may substitute one loopback HTTP twin. It disables ambient
proxy and credential use. Every artifact is streamed into a new sibling staging
directory with bounded size, exclusive non-following creation, exact length
when declared, and durable writes.

The same production verifier used by the local embedding carrier then rejects
missing, extra, changed, linked, special, or escaping entries and validates the
canonical artifact digest. Publication is a same-filesystem kernel atomic rename
with no-replace semantics. The verified source name lives beneath a private
capability-retained directory whose read/list permission is removed before the
verifier runs and remains removed through rename; only the materializer retains
the parent capability and randomized source name required for publication.
Existing destinations and destinations introduced by a race are never removed,
traversed, or overwritten. Failed staging trees are discarded through retained
directory descriptors; successfully published trees are immutable by convention
and are never promoted or activated by this process.

After a successful no-replace rename, the process fsyncs the retained
destination-parent descriptor before reporting `ready`. A refused durability
barrier returns the closed `publication_refused` outcome; because the atomic
rename may already be visible, operators must inspect the chosen destination and
never retry with overwrite semantics. The command never reports durable success
without that barrier.

Output is the versioned `context-engine-model-materializer-v1` content-free
contract. It exposes only service, status, and a closed category. Model identity,
revision, digest, artifact name, URL, destination, credentials, byte counts, and
exception text never enter either output channel.

## Consequences

- Runtime and Supply retain `local_files_only`; preflight remains read-only and
  network-free.
- Materialization loads no model, touches no database, embeds no content, and
  holds no Release or carrier-activation authority.
- CI uses only tiny synthetic loopback bytes. `make
  model-materialize-acceptance MODEL_DESTINATION=...` is an explicit maintainer
  action over the real registered bytes and is not a CI or Release gate.
- Platforms without a kernel atomic no-replace directory rename refuse
  publication instead of emulating it with a check-then-rename race.

## Revisit trigger

Revisit before registering another materializable role, changing the upstream
host or redirect boundary, supporting resumable transfers, or provisioning a
platform without a kernel no-replace rename primitive.
