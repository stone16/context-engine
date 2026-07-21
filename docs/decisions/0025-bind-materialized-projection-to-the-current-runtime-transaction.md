---
name: adr-0025-current-transaction-materialized-projection
version: "1.0.0"
description: >
  Bind the first exact-authorized PostgreSQL Fragment projection to the current
  UserActor transaction and keep content behind the sealed Kernel.
---

# 0025. Bind materialized projection to the current Runtime transaction

- Status: accepted
- Date: 2026-07-21

## Context

Issue #13 activates the first content-bearing Runtime slice. A hostile index may
nominate an authorized same-Organization Fragment, a denied
same-Organization Fragment, and a cross-Organization Fragment in any order.
`CandidateRef` is deliberately content-free and cannot authorize a read.

ADR-0023 already requires the current Membership check and Runtime work to share
one non-owner PostgreSQL transaction. Opening a second hydration connection
would split the authorization decision from that lifetime and its transaction-
local UserActor context. Reading every nominated body before exact scope
authorization would also let denied bytes enter Runtime before the sealed
Kernel decision, contrary to ADR-0012.

The full future publication, Source, PolicySnapshot, ContextRun, tokenizer, and
provider protocols are not yet active. This tracer must not fabricate them or
turn a seeded fixture into a production retrieval foundation.

## Decision

`PostgreSQLMembershipAuthority.current_user_actor` owns the request transaction
and yields a nominal proof that carries a private, narrow materialized-
projection capability bound to that same transaction lifetime. It never exposes
a raw SQLAlchemy `Connection` through the public Runtime or HTTP contract.

The only content-bearing order is:

~~~text
content-free CandidateRef
  -> same-transaction PostgreSQL/RLS lineage lookup
  -> current active Revision and non-tombstoned Resource check
  -> exact EffectiveScope target membership inside AuthorizationKernel
  -> same-transaction exact Fragment body projection
  -> Kernel-only AuthorizedProjection
  -> request-scoped Evidence and Package block assembly
~~~

Lineage lookup contains no Fragment body. Cross-Organization rows disappear
under FORCE RLS. A same-Organization row that is absent from the current
`EffectiveScope` is discarded before body projection. Candidate metadata,
candidate rank, and index Organization claims are never authorization inputs.
The body query repeats the exact Organization/Resource/Revision/Fragment active-
lineage predicates so a pointer or tombstone change cannot widen the result.

Persistence uses `context_resource`, `context_revision`, and
`context_fragment`. Keys and foreign keys include Organization. One
`context_resource.active_revision_id` selects the visible immutable Revision;
Fragment content retains its exact Revision lineage. Evidence is constructed
only for this request and is never persisted as a replacement for a
ContextFragment. Durable ContextRun and production publication transitions
remain owned by later issues.

The default Runtime composition keeps content discovery prohibited. This tracer
is activated only when an explicit content-free CandidateIndex is composed and
the current trusted Membership authority provides the same-transaction
projection capability. Missing, malformed, inactive, stale, tombstoned, or
unavailable projection state fails closed.

For the synthetic tracer, one selected UTF-8 block consumes exactly its UTF-8
byte length as the temporary deterministic `tokens` budget unit. Provider calls,
external cost, and elapsed usage remain zero because this path uses the internal
seeded index and the already-open PostgreSQL transaction. This rule is explicitly
not a production tokenizer claim and must be replaced when tokenizer selection
activates.

## Rationale

Keeping locator and body reads on one transaction makes the non-owner FORCE RLS
decision inseparable from the current UserActor operation. Separating the two
queries prevents denied body bytes from crossing into Runtime while still
allowing the Kernel to compare authoritative Source/Resource lineage with the
current EffectiveScope. Nominal lifetime-bound values make accidental reuse or
construction outside the Kernel fail closed.

Deterministic assembly independent of candidate order proves that rank cannot
grant access. Exact one-to-one block-to-Evidence reference closure prevents
dangling, duplicate, or orphan Evidence from becoming a second leakage path.

## Consequences

Issue #13 must prove the path through domain, real PostgreSQL/non-owner RLS, and
HTTP Runtime seams. Tests include arbitrary hostile candidate order, exact one
authorized Evidence/block, zero denied or cross-Organization bytes and fields,
zero wrong-Organization durable effects, and mutation controls for bypassing
exact authorization or trusting candidate metadata.

The first Evidence lineage may use request-scoped run and synthetic policy/
source-decision references because durable ContextRun and production policy
authorities are not active. Those refs are server-owned, non-authorizing lineage
and cannot be reused as capabilities.

No File ingestion, Source registration, FTS/vector retrieval, rerank,
continuation, citation-open, publication worker, or active-pointer transition
workflow is introduced here.

## Revisit trigger

Revisit when a production materialized repository, ContextProvider,
PolicySnapshot/Policy Epoch authority, tokenizer, durable ContextRun, or Supply
publication transition activates. Every replacement must preserve the same
request transaction, `CandidateRef -> AuthorizationKernel ->
AuthorizedProjection`, content-after-exact-authorization order, immutable active
Revision visibility, and Unauthorized Evidence = 0.
