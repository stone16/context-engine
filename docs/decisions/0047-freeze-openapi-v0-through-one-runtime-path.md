---
name: adr-0047-openapi-v0-runtime-bridge
version: "1.0.0"
description: >
  Freeze one public OpenAPI v0 resolve contract while keeping the provisional
  v1 route as a hidden transport bridge to the same sealed Runtime path.
---

# 0047. Freeze OpenAPI v0 through one sealed Runtime path

- Status: accepted
- Date: 2026-07-23
- Refines: ADR-0017, ADR-0022, ADR-0028, ADR-0033, ADR-0045, ADR-0046

## Context

M1 exposed the provisional `/v1/context:resolve` route before the public wire
contract was frozen. M2 requires one deterministic public `/v0/resolve`
operation, a complete ContextPackage, authenticated metadata, and an immutable
breaking-change gate. Publishing both paths in OpenAPI would create two client
contracts; implementing two authorization compositions would create a bypass
risk.

The complete Package also names active release and tokenizer lineage. Those
facts cannot be supplied by a caller or filled with server placeholders.
ContextLearning already owns the sole Organization release pointer, while the
Runtime role previously had no read access to that pointer.

## Decision

OpenAPI exposes exactly `POST /v0/resolve`. The provisional
`/v1/context:resolve` route remains temporarily callable but is hidden from the
schema. Both route registrations invoke the same HTTP handler, trusted ingress
construction, sealed Runtime, AuthorizationKernel, Package gates, and egress
gate. The compatibility bridge has no independent domain or authorization
composition.

The v0 request remains the discriminator-closed Acquire, Continue, and
OpenCitation union. Its authenticated carrier requires one bounded
`X-Context-Request-Id` and permits at most one bounded opaque
`X-Context-Delivery-Evidence-Ref`; duplicate metadata is invalid. The hidden v1
bridge may retain its temporary server-issued request reference while callers
migrate. Route-level application policy may return only generic 403 or 429
outcomes after authentication and before Organization, Membership, scope, or
content work. Protected-object authorization never selects those statuses.

ContextPackage v0 uses a request-scoped opaque `packageId`; it does not expose a
raw Organization identity. It carries the trusted audience digest, policy
epoch/snapshot, decision/run lineage, release manifest, digest-only retention
policy, tokenizer and Package schema refs. File Evidence declares locally
managed Mirrored SourceAclEvidence. Citation and continuation fields are
present but nullable until their owning capabilities activate.

Runtime receives read-only FORCE-RLS `SELECT` access to the active pointer and
immutable manifest for its exact Organization inside the retained current
UserActor transaction. Both release tables require the complete current
UserActor database context, not an Organization setting alone. A shared
Organization release lock serializes each online observation through delivery
commit with ContextLearning's exclusive promotion lock.

The observation binds the exact activation generation, manifest digest,
Runtime/Content/Index profile refs and digests, tokenizer, Package schema, and
the exact registered curation-off profile. The public Package release ref is a
domain-separated digest-derived opaque ref over both manifest digest and
activation generation; it never exposes the durable Organization-owned manifest
label and cannot collapse an `A -> B -> A` activation history. v0 recognizes
only its exact registered Content, Index, Runtime, and curation-off profile refs,
digests, schemas, tokenizer, and Package schema. Missing, malformed, or unknown
lineage makes Acquire unavailable before candidate discovery or content work.
Under curation-off, Supply's active Revision pointer remains independently
transactional: ordinary File replacement does not require a release promotion.
If curation-on activates later, its compatible Revision set must be handled in
the same database snapshot required by ADR-0014 rather than as an asynchronous
post-publication Runtime filter.

Runtime receives no insert, update, delete, promote-function, or audit
authority. It cannot publish, repair, reinterpret, or select a fallback
manifest.

The accepted `openapi/v0/openapi.json` and checksum are historical artifacts.
The check command regenerates deterministically, verifies recursive structural
equality, verifies its checksum, and requires exact server equality. CI also
loads both artifacts from the pull-request base or preceding push commit and
rejects any byte mutation after first publication, even when code and snapshot
are edited together. The generator refuses to overwrite an existing version
directory; a reviewed new version is required for any contract change.

## Consequences

- Generated clients see one public resolve operation and one closed schema.
- Existing internal M1 callers can migrate without a second Runtime path.
- Successful Acquire now proves the Package release/tokenizer lineage was
  published by ContextLearning for the exact Organization, while Supply retains
  atomic old-or-new active Revision visibility under curation-off.
- Adding fields or changing v0 deliberately requires a new reviewed contract
  version rather than rewriting historical evidence.

## Revisit trigger

Remove the hidden v1 bridge after all repository-owned callers use the generated
v0 SDK. Revisit release observation only if a measured deployment boundary
requires a separately authenticated read service; it must not add publication
authority or a Runtime fallback manifest.
