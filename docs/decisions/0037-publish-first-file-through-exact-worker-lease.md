---
name: adr-0037-publish-first-file-through-exact-worker-lease
version: "1.0.0"
description: >
  Activate one trusted Markdown File import through an exact durable WorkerLease,
  atomic immutable publication, and a content-free exact-phrase CandidateIndex.
---

# 0037. Publish the first File through an exact WorkerLease

- Status: accepted
- Date: 2026-07-22
- Refines: ADR-0018, ADR-0029, ADR-0035, ADR-0036

## Context

Issue #23 is the first end-to-end Supply-to-Runtime tracer. It must turn one
registered File Source and one trusted Markdown filename into an authorized
`ContextPackage` without promoting the Issue #17 no-op carrier into an implicit
content authority. The worker needs filesystem access, while Control and Runtime
must remain unable to accept a host path or caller-authored tenant facts. Initial
publication also needs a Resource row before its immutable Revision can satisfy
the deferred active pointer.

## Decision

File Source registration remains the unavailable version-1 declaration from
Issue #21. A trusted `prepare_file_import` Control call validates one basename
ending in `.md`, revalidates the current audience Membership and registered File
import ServicePrincipal, creates an immutable version-2 SourceVersion declaring
only `fileSourceAccess` and `ingestionJobs` available, atomically switches the
Source active pointer, and creates one immutable acquisition plus one durable
job. Control stores only a logical root reference and relative filename; it never
opens the filesystem.

The version-2 WorkerLease preserves the version-1 no-op token bytes and binds
Organization, exact durable job, Source, receiver, workload
`supply.file-import`, audience `context-engine-worker`, operation `file.import`,
database-owned issue/expiry times, key version, and nonce. Redemption and
publication each revalidate those exact current row values, database time, and
the enabled ServicePrincipal. The content-free terminal failure transition has
the same checks, so expiry or receiver revocation cannot retain even a failed
state mutation. The worker receives no user impersonation authority.

The File adapter opens every component of a server-owned logical root as a
no-follow directory capability, then opens exactly one validated filename
relative to that retained descriptor. It accepts only a regular file below an
explicit server-owned byte ceiling. Bytes pass unchanged into the Issue #22
compiler. One successful compilation is published in one database
transaction as Resource, immutable Revision, immutable compilation snapshot,
one paragraph Fragment, mirrored Resource access, exact Membership body right,
content-free exact-phrase candidate, ordered `prepared -> indexed -> active`
events, active pointer, and completed job. Any failure rolls the entire effect
back; a post-redemption acquisition or compilation failure leaves only a
content-free terminal failed-job marker so it cannot remain runnable. The
reversible migration removes only Issue #23-owned rows and schema
before restoring the Issue #21 and Issue #17 constraints.

The exact-phrase index stores a SHA-256 query digest and lineage references, not
content. Its Runtime SELECT requires the complete current UserActor transaction
context and runs inside the same retained projection transaction used by the
Kernel; returned `CandidateRef` values remain untrusted discovery output.
Every candidate still crosses the sealed AuthorizationKernel and
`AuthorizedProjection` gates before content-bearing assembly. Cross-Organization
or scope-denied resolution returns the canonical empty package.

## Rationale

This is the smallest production-shaped tracer that exercises real acquisition,
durable job authority, immutable publication, retrieval, authorization,
provenance, and HTTP delivery. Logical roots keep deployment paths out of
contracts. A content-free deterministic index proves that retrieval is not
authorization. Atomic publication prevents Runtime from observing prepared or
indexed content before the active pointer and access rights agree.

## Consequences

- One explicitly prepared Markdown basename can be imported and resolved by an
  exact phrase through the public HTTP seam.
- Direct worker table mutation remains unavailable; the shared definer role has
  only operation-specific policies, grants, and functions.
- Directory discovery, watchers, symlinks, traversal, multiple files, update,
  delete, hash no-op, retry/recovery, replacement, and approximate retrieval
  remain unavailable.
- The complete WorkerLease contract is still not proven for Policy Epoch,
  generation, outbox, or arbitrary Source operations.

## Revisit trigger

Revisit before accepting directory trees, links, remote files, repeat imports,
updates/deletes, checkpointing, recovery, approximate search, more compiler
shapes, or a broader ServiceActor operation set. Each expansion must retain
exact durable-job binding, server-owned roots, atomic immutable publication, and
the `CandidateRef -> AuthorizationKernel -> AuthorizedProjection` boundary.
