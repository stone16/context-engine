---
name: adr-0035-context-control-file-source-registration
version: "1.0.0"
description: >
  Register one Organization-owned File ContextSource and immutable first
  SourceVersion through a trusted ContextControl call without activating File
  acquisition or a new wire API.
---

# 0035. Register File sources through one trusted ContextControl transaction

- Status: accepted
- Date: 2026-07-22
- Refines: ADR-0015, ADR-0017, ADR-0018

## Context

M1 needs a stable Organization-owned `ContextSource` before File acquisition,
Markdown compilation, durable jobs, or publication can begin. Registration is
a Control-plane operation, but the current HTTP ingress is deliberately only
the Runtime `resolve` surface and the generated wire contract is not frozen
until M2. Treating the database Control login, a caller-supplied Organization
reference, or a host filesystem path as operator authority would create a new
trust bypass. Advertising future Provider operations merely because a File
source was registered would also make capability coverage false-green.

The first `SourceVersion` must be immutable and selected by one active pointer.
Registration retries need one Organization-local identity without allowing the
same key to alias different configuration, and another Organization must not
learn whether a source reference exists.

## Decision

`ContextControl` gains its first in-process public behavior:
`register_source(TrustedControlCall, RegisterFileSource) -> SourceManifest` and
`read_source(TrustedControlCall, SourceRef) -> SourceManifest`. A
`ControlOperatorAuthority` authenticates an opaque Control credential and
constructs a lifetime-bound, operation-bound, one-use trusted call. The
untrusted commands contain no Organization, source mode, ACL mode, or other
trusted selector. HTTP Control routes and SDK contracts remain inactive in this
slice.

`RegisterFileSource` accepts bounded display metadata, an Organization-local
idempotency key, and an opaque logical `FileRootRef`. A root reference is a
single identifier, not a path: separators, traversal segments, URI schemes,
drive prefixes, home expansion, and absolute host locations are rejected.
Registration never resolves, opens, stats, lists, or otherwise accesses it.

One PostgreSQL Control-role transaction binds the trusted Organization
transaction-locally and atomically creates:

1. one `context_source` row containing stable identity, display metadata,
   registration operation/key, and the active version pointer; and
2. one `source_version` row containing immutable File configuration and the
   exact capability declaration.

The two rows use Organization-inclusive primary and foreign keys. The active
pointer is a deferred same-Organization, same-source composite foreign key, and
the reverse SourceVersion ownership foreign key is also deferred so the first
pair can be inserted atomically. `source_version` updates are rejected by a
database trigger. The non-owner Control role receives only the required
Organization-RLS-scoped `SELECT` and `INSERT` privileges; Runtime, worker,
Learning, and PUBLIC receive none.

The registration key is unique over `(Organization,
register_file_source, idempotency key)`. An exact retry returns the original
manifest; reuse for a different request fails generically. Identical keys in
different Organizations are independent. Source read-back always derives its
Organization from the trusted call. Cross-Organization and unknown source
references therefore share one `SourceNotAvailable` result.

The File declaration fixes `materialized` source mode, Markdown content, the
`markdown_document` resource kind, and Mirrored ACL policy. It declares no
projectable fields and marks cursor semantics, checkpoint semantics, batch
limits, freshness, consistency guarantees, and every Provider carrier not
implemented in Issue #21 unavailable: capability-description dispatch, change
reading, discovery, authorization/projection, checkpoint, deletion, ingestion
jobs, and FileSourceAccess activation. A registered source is configuration
only and is not acquisition-ready.

## Rationale

The in-process deep Module is the highest stable Control seam today; inventing
an HTTP route before the M2 wire decision would create a premature second
contract. A sealed trusted call prevents source or Organization references from
becoming authority, while Control-role RLS remains an independent database
boundary. An exact immutable declaration lets later File issues activate one
capability at a time without rewriting the meaning of the first version.

## Consequences

Issue #21 creates no filesystem read, Provider call, job, outbox row,
`ContextResource`, `ContextRevision`, or `ContextFragment`. It does not activate
Runtime source selection or File authorization. Later source configuration
changes must create a new `SourceVersion`; later issues own FileSourceAccess,
acquisition, publication, update, disable, and offboarding transactions.

The active source pointer and both source tables join the schema security
manifest and the live RLS denominator. Any future HTTP Control ingress must
redeem its own authenticated operator evidence into the same trusted call; it
may not place Organization or mode authority in a request body.

## Revisit trigger

Revisit when an activated Control wire protocol, a second source kind, or a
measured source-registration workflow requires a broader command. Any
replacement must preserve trusted Organization derivation, exact retry
semantics, immutable versioning, same-Organization pointer integrity,
denied/not-found equivalence, and zero acquisition work during registration.
