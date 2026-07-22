---
name: adr-0039-deduplicate-unchanged-file-acquisitions
version: "1.0.0"
description: >
  Classify unchanged File acquisitions before publication by a tenant-scoped
  canonical content identity and serialize concurrent decisions in PostgreSQL.
---

# 0039. Deduplicate unchanged File acquisitions before publication

- Status: accepted
- Date: 2026-07-22
- Refines: ADR-0018, ADR-0037, ADR-0038

## Context

An acquisition is durably known before its worker reads and compiles the File.
Before this decision, every successful compilation attempted first publication,
which is correct only while no Resource is active. A repeated observation of
the same canonical content must retain acquisition evidence without creating a
second immutable Revision, Fragment set, candidate set, publication-event
sequence, access row, or active-pointer write. Two workers may reach that
decision concurrently, so process-local comparison or locking is insufficient.

Content equality alone is also too broad. Equal customer bytes cannot become a
cross-Organization identity or authorization bridge, and the same canonical
text compiled under a meaning-affecting compiler or configuration version is a
different representation. A partial or failed publication must never supply
the active lineage required for a no-op.

## Decision

File content identity version 1 is SHA-256 over this length-unambiguous domain:

~~~text
UTF8("context-engine.file-content-identity.v1") || 0x00
|| UUID_BYTES(organization_id)
|| UUID_BYTES(source_id)
|| UTF8(resource_ref) || 0x00
|| ASCII(SHA256_HEX(UTF8(canonical_text))) || 0x00
|| UTF8(compiler_version) || 0x00
|| UTF8(config_version)
~~~

Every variable field in this domain is a PostgreSQL `text` value at the hashing
boundary. PostgreSQL rejects U+0000 before a `text` argument reaches the
classifier, so no variable field can contain the `0x00` delimiter. The two UUID
fields and the lowercase hexadecimal content hash have fixed lengths. These
input-type invariants make the displayed encoding length-unambiguous; a future
carrier that admits U+0000 must introduce a new length-prefixed identity
version rather than reuse version 1.

Each dimension is intentional. Organization prevents cross-tenant identity
sharing. Source and stable Resource restrict comparison to the same registered
source object rather than directory-wide or global deduplication. The content
hash names exact canonical UTF-8 bytes, not raw source bytes. Compiler and
configuration versions make representation changes observable. Compilation
digest is not a second identity dimension because the accepted deterministic
compiler derives it from those inputs; the version-specific publication wrapper
still compares the exact active compiled artifact and candidate set before it
accepts a no-op.

The acquisition, job, and exact WorkerLease become durable first. After File I/O
and pure compilation, one SECURITY DEFINER transaction validates the running
job and lease, inserts or finds a tenant/source/resource
`file_resource_ingestion_guard`, and locks that row. The lock serializes the
classification and publication decision for concurrent imports. The worker has
no direct table mutation privilege and cannot execute either the internal
classifier or the older publication functions; its only publication entrypoints
are the version-bound no-op-aware wrappers.

An acquisition is `unchanged` only when the same non-tombstoned Resource has an
active Revision whose immutable snapshot has the exact canonical content hash,
compiler version, and configuration version; its publication events are exactly
`prepared -> indexed -> active`; and its version-specific stored compilation,
Fragments, and exact-phrase candidates match the freshly compiled artifact. The
current acquisition's exact Principal and Membership version must also remain
active, be temporally valid at classification, and retain the published body
access; content equality never grants or repairs access. Missing or
inconsistent active artifacts or access fail closed. Initial publication and
classification remain in the same transaction and under the same guard lock.

Every unchanged observation creates one immutable, Organization-owned
`file_acquisition_result`. Initial publication retains its existing acquisition,
job, snapshot, and publication lineage without adding an Issue #25 outcome row.
A no-op result binds the acquisition, Source, Resource, current active Revision,
content identity, the fixed reason code `active-content-identity-match`, and a
reason digest:

~~~text
SHA256(
  UTF8("context-engine.file-no-op-reason.v1") || 0x00
  || UTF8("active-content-identity-match") || 0x00
  || CONTENT_IDENTITY_DIGEST_BYTES
  || UUID_BYTES(active_revision_id)
)
~~~

It retains no source content. The acquisition's durable import job completes
with the existing Resource/Revision/first-Fragment lineage and `effect_count =
0`; no Revision, Fragment, index candidate, publication event, access row, or
active pointer is created or changed. ACL changes remain their separate policy
domain and are not inferred from content equality.

Changed canonical content or compiler/configuration versions do not enter the
no-op path. Replacement publication and worker crash recovery remain unavailable
in this issue, so those attempts terminate generically without changing the
active publication. A partial active artifact is likewise not a no-op. Schema
downgrade refuses to discard any unchanged acquisition result.

## Rationale

The transaction lock gives one database linearization point for both
classification and first publication, including two imports that begin before a
Resource exists. Comparing only a complete active artifact makes visibility,
not merely the presence of partial rows, the reuse prerequisite. Tenant-scoped
digests support deterministic audit and testing without making equal content a
shared authorization object or retaining source text in the outcome.

## Consequences

- Repeated and concurrent imports still perform bounded File read and pure
  compilation, but emit no publication work after classification.
- Runtime receives the same active Revision and Fragment lineage through the
  existing `CandidateRef -> AuthorizationKernel -> AuthorizedProjection` path.
- One import job and one acquisition outcome remain per observation; these are
  audit/progress effects, not publication effects.
- Changed-content replacement, failed-job reclaim, and directory/global
  deduplication remain explicitly unavailable.

## Revisit trigger

Revisit before adding changed-content replacement, crash recovery after a
non-no-op publication begins, directory/global deduplication, another canonical
content carrier, or a compiler whose derived artifact is not deterministic from
the declared identity inputs. Any replacement must preserve tenant isolation,
database-linearized concurrency, immutable audit lineage, and the sealed Runtime
authorization path.
