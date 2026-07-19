---
name: adr-0012-sealed-authorization-projection-pipeline
version: "1.0.2"
description: >
  Seal Runtime ordering so content-free CandidateRef values become
  AuthorizedProjection values before content-bearing relevance, assembly, or
  model work.
---

# 0012. Seal authorization and projection before content-bearing relevance

- Status: accepted
- Date: 2026-07-18
- Refines: ADR-0006, ADR-0007, ADR-0009

## Context

ContextEngine's threat model requires Unauthorized Evidence = 0, including in
intermediate content-bearing consumers. If rerank, hydration, tokenization,
debug tracing, or parent expansion runs before exact authorization, it can
observe content that is later removed from ContextPackage. Separating content
hydration from the ACL facts used to authorize that content also creates a
time-of-check/time-of-use gap. The Runtime contract therefore needs one
enforceable ordering rather than a convention shared by callers.

## Decision

ContextRuntime keeps one deep Interface:

~~~text
resolve(AuthenticatedInvocation, TrustedDeliveryContext,
        Acquire | Continue | OpenCitation) -> ResolutionOutcome
~~~

Before authorization, retrieval may expose only content-free CandidateRef
values. AuthorizationKernel is a sealed production Module and the only
constructor of AuthorizedProjection. Inside Runtime, content-bearing rerank,
dedupe, token accounting, Assembler, relevance-model calls, ordinary trace, and
ContextRun accept AuthorizedProjection only. Downstream answer generation is a
separate boundary: ModelGateway accepts AuthorizedModelInput derived only from
a current audience-bound ContextPackage and a matching EgressGrant.

The fixed order is CandidateRef retrieval and opaque RRF, exact authorization
and field projection, optional content-bearing relevance, expansion with
re-authorization, budget assembly, final egress decision, and ContextPackage.

ContextProvider is the only external Source seam with four operations:
describeCapabilities, readChanges, discover, and authorizeAndProject. Inside
Runtime, live source, PostgreSQL materialized, and declared weak membership
implementations satisfy one internal SourceProjection seam. Provider output is
source evidence; Kernel owns the final authorization decision.

The operations use closed ProviderOutcome values rather than empty-success
ambiguity. CapabilityDeclaration is versioned and declares ACL mode, Resource
kinds, projection fields, cursor/checkpoint semantics, deletion, batch,
freshness, and consistency. readChanges uses opaque monotonic cursors distinct
from the publish watermark; discover returns content-free CandidateRef; and
authorizeAndProject returns SourceProjectionBatch evidence.

CandidatePage and SourceProjectionBatch share a SourceConsistencyRef containing
provider, SourceVersion, authorization mode, decision/snapshot version, and
checkedAt/aclAsOf as applicable. Kernel rejects missing, mixed, changed, or stale
refs; live sources use same-operation projection or their declared
verify-before-and-after protocol.

FileProvider is the first implementation and uses a versioned, locally managed
FileSourceAccess projection as Mirrored SourceAclEvidence. It does not infer or
claim host operating-system file ACL. Missing explicit grants deny.

Source ACL evidence is a closed union:

- native live: authorization and projection in one request or documented
  verify-before-and-after protocol;
- native mirrored: versioned PostgreSQL SourceAclProjection with explicit
  aclAsOf and lag bound;
- declared weak membership: complete, fresh membership facts for a source that
  genuinely has no finer ACL.

SourcePolicy fixes the mode at SourceVersion activation. A live or mirrored
failure never downgrades to weak.

## Rationale

Security ordering is a domain invariant, not application wiring. Keeping
policy, audit, budget, provenance, and exact projection inside one sealed
Kernel makes bypass structurally unavailable while leaving infrastructure and
provider variation behind narrow seams.

## Consequences

The Interface has high depth: all callers get the same security order and cannot
rearrange it. Parent and neighbor expansion costs more authorization work.
Provider contract tests must prove content-free discovery and bounded
projection. Live outages may produce a typed partial/empty result instead of an
availability-oriented downgrade.

## Revisit trigger

Reopen the kernel-versus-seam allocation only when a second production
implementation demonstrates a variation that cannot satisfy the current deep
Interface, or a security finding proves the sealed order insufficient. A
replacement must preserve `CandidateRef -> AuthorizationKernel ->
AuthorizedProjection`, mandatory policy/audit/budget/provenance behavior, and
the absence of production disable flags or no-op dependencies.
