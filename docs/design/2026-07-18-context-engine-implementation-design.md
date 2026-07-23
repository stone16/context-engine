---
title: ContextEngine Implementation Design v1.2
date: 2026-07-19
status: implementation authority
public-summary: PLAN.md
public-evidence: docs/research/2026-07-19-four-public-repositories-evidence.md
threat-model: docs/security/context-engine-threat-model.md
---

# ContextEngine Implementation Design v1.2

## 0. Authority and completion meaning

This document is the implementation authority for ContextEngine. Earlier drafts
are non-authoritative history and cannot settle an implementation question.
CONTEXT.md owns vocabulary; accepted ADRs under docs/decisions own their scoped
decisions; `docs/security/context-engine-threat-model.md` owns the explicit
assets, trust boundaries, threats, and hard oracles; this document owns their
integrated implementation shape. These are scoped responsibilities rather than
a total precedence order. A contradiction must be reconciled explicitly before
implementation; one document cannot silently override another's owned scope.

Production milestone implementation may start only after D0 closes every
blocking decision in this document and records one immutable, self-contained
repository baseline. D0 may run isolated, disposable evidence spikes for
Feishu, PostgreSQL RLS, and filtered ANN; spike code is not a production
foundation and is deleted or archived outside the runtime tree after its
reproducible report/digest is recorded.

### 0.1 Public provenance and independent design ownership

Public prior-art claims are allowlisted to Dify, RAGFlow, MaxKB, and Onyx at the
fixed revisions recorded in
`docs/research/2026-07-19-four-public-repositories-evidence.md`. Those projects
provide observable patterns for product orchestration, document compilation,
knowledge operations, connectors, checkpointing, retrieval, and test layering.
They do not establish ContextEngine's security guarantees.

ContextEngine independently owns the tenant model, AuthorizationKernel,
AuthorizedProjection type flow, SourceAclEvidence semantics, WorkerLease,
audience-bound delivery, ActionTicket protocol, atomic publication, audit data
separation, and single ReleaseManifest promotion authority. Each of these is
justified by this product's threat model and hard oracles, not by an assertion
that a reference repository already implements or validates it. No reference
code is copied; only observable behavior, Interface shape, test oracle, and
product workflow may inform a clean-room implementation.

The repository is sufficient authority for a clean clone. Unpublished notes,
local paths, private implementations, and unattributed research conclusions are
not normative dependencies. An idea from any exploratory input enters this
design only after it is restated as a ContextEngine requirement, threat,
decision, or evidence-gated hypothesis that can be reviewed from repository
materials alone.

### 0.2 Claim and evidence discipline

Every implementation claim belongs to one of these states:

- **specified**: the required behavior is defined but not implemented;
- **contract-verified**: observable behavior passes through the public Module
  Interface with deterministic fixtures or a protocol twin;
- **sandbox-verified**: the behavior passes against an isolated real dependency;
- **live-verified**: the declared production path and dependency pass the frozen
  conformance suite;
- **inconclusive**: evidence is missing, underpowered, or contradictory.

Contract fixtures, deterministic fakes, and provider twins are legitimate test
tools at internal seams. They cannot activate a capability or support a sandbox
or live claim. Security properties tied to PostgreSQL, authenticated wire
metadata, source ACLs, model egress, or external effects require the
corresponding real dependency. "No mock capability" means no fabricated source,
response, identity, ACL, or effect may be presented as product evidence; it does
not ban deterministic test doubles from lower evidence tiers.

Completion is observable behavior, not feature count. Every milestone has:

- a demoable vertical slice;
- explicit security, reliability, quality, and budget gates;
- versioned fixtures, commands, thresholds, and report digests;
- capability coverage reported separately from pass/fail.

An unimplemented path is NOT_ACTIVE, never PASS.

## 1. Product boundary and non-negotiable invariants

ContextEngine is a multi-tenant context delivery system. Its only online content
deliverable is ContextPackage: authorized, evidence-backed, budget-bounded
context for an agent application. The engine does not generate answers and does
not execute writes.

The primary threat actors and failure sources are an authenticated but
overreaching tenant member, a confused or compromised caller, malicious/stale
retrieval candidates, a provider or worker carrying incomplete tenant context,
replayed capabilities, audience changes between resolve and send, and ordinary
implementation defects. PostgreSQL, activated source providers, BotDelivery,
ModelGateway, and Sender are not assumed infallible; their authority and
cleartext access are minimized and verified at the next trusted seam.

The three hard oracles are vetoes:

1. Unauthorized Evidence = 0.
2. Wrong-Organization effect = 0.
3. Missing-context fallback = 0.

Additional fixed invariants:

- Organization is the security root.
- Missing tenant or principal context fails before provider, index, model, or
  side-effect calls.
- Index and cache narrow candidates; they never authorize.
- Content-bearing relevance work happens only after exact authorization and
  field projection.
- Revocation blocks future controlled operations after the engine observes and
  commits the policy change; physical cleanup is asynchronous.
- Bytes already delivered to an external system cannot be recalled by Policy
  Epoch. Message redaction or deletion is an Action Plane compensation.
- RequestNarrowing is untrusted caller input and can only shrink scope.
- RequestNarrowing is optional. When absent it is the identity element relative
  to the already established trusted scope; every trusted authorization operand
  remains mandatory and missing trusted context still fails closed.
- Weak ACL is a declared source type, never an outage fallback for a stronger
  source.
- Learning and curation never modify authorization.

## 2. Deep Module map

The system exposes five deep Modules: three engine Modules plus two trusted
delivery/effect Modules. Each has one caller-facing Interface; internal seams
exist only where an implementation genuinely varies or a true external
dependency must be adapted. Callers and behavioral tests use the same
Interface, keeping security ordering local rather than distributing it across
transports and adapters.

The engine deployment is a modular monolith with an API process and an
independent Supply worker. M2 activates one additional trusted Bot application
process containing BotDelivery and ActionPlane. This is a deliberate caller and
credential boundary rather than an engine microservice split: BotDelivery calls
ContextRuntime only over HTTP through the generated SDK, and the Bot process may
share delivery contracts but cannot import engine internals. No further process
is added without measured isolation or performance evidence.

### 2.1 ContextControl

Interface:

- registerSource
- changeAccess
- changePolicy

ContextControl owns source enrollment, access governance, SourcePolicy, and
policy mutation. It does not publish runtime profiles and does not perform
online retrieval.

### 2.2 ContextRuntime

Interface:

~~~text
resolve(
  AuthenticatedInvocation,
  TrustedDeliveryContext,
  Acquire | Continue | OpenCitation
) -> ResolutionOutcome
~~~

This is the only read path. The Interface hides identity validation, source mode,
retrieval, authorization, field projection, rerank, expansion, budget,
provenance, audit, continuation, citation redemption, and egress decisions.

Requests to any activated server ingress do not contain trusted Organization,
Principal, Membership, audience, or ACL fields. HTTP is the V1 ingress; MCP
remains NOT_ACTIVE until a real caller and parity suite justify it. Trusted
ingress Adapters construct
AuthenticatedInvocation and TrustedDeliveryContext from session, token, mTLS,
OAuth binding, and platform event evidence. For a remote BotDelivery caller, a
trusted identity Adapter persists or attests that evidence and issues a
short-lived DeliveryEvidenceRef bound to the authenticated service, resolve
request id, Organization, asker, destination, purpose, audience digest, and
expiry. The SDK carries only that opaque reference in authenticated transport
metadata; trusted ingress redeems it to construct TrustedDeliveryContext. Raw
audience claims never appear in the request body. Public and private resolves
use separate references. The generated TypeScript SDK is an HTTP client
artifact, not a server transport.

The closed request union is:

- Acquire: ContextNeed plus PackageBudget and optional RequestNarrowing. Purpose
  is a trusted ingress/DeliveryEvidenceRef fact and is not accepted in the body.
- Continue: a principal-bound, audience-bound ContinuationToken and an optional
  smaller PackageBudget.
- OpenCitation: a CitationOpenRef; the opener identity always comes from the new
  AuthenticatedInvocation.

### 2.3 ContextLearning

Interface:

~~~text
evaluate(ReleaseCandidateRef) -> ReleaseEvaluation
promote(TrustedPromotionCall) -> PromotionReceipt
~~~

ContextLearning owns candidate evaluation and the only active ReleaseManifest
pointer. promote validates release-operator authority, immutable lineage,
compatibility, and Security/Reliability/Quality/Budget gates. A new deployment's
empty initial manifest is a normal ReleaseCandidate and must pass through the
same promote path; migrations, ContextControl, and bootstrap scripts have no
alternate activation write.

### 2.4 BotDelivery

The trusted Bot application contains one deep delivery Module:

~~~text
answer(VerifiedQuestionTurn) -> DeliveryReceipt
openCitation(VerifiedCitationOpen) -> CitationOpenOutcome
~~~

BotDelivery owns IM workflow orchestration: binding, private/group delivery
choice, public/private dual resolve, controlled model egress, citation
reconciliation, placeholder/final effects, retries, and delivery audit.

BotDelivery does not calculate authorization and cannot submit caller-authored
audience claims. It asks the trusted identity Adapter for a DeliveryEvidenceRef,
passes that opaque reference through the generated client, and consumes the
resulting audience-bound Package.

### 2.5 ActionPlane

Interface:

~~~text
prepare(TrustedEffectIntent) -> ActionPreparationOutcome
perform(EffectPayload, ActionTicket) -> ActionExecutionOutcome
~~~

ActionPlane is the only owner of write policy, operation-specific ActionTicket
issuance/validation, idempotency, effect execution, and write audit. prepare
binds one operation, destination, audience, payload digest, policy epoch,
expiry, approval tier, and idempotency key. perform verifies that exact binding
before Sender, an Adapter behind this Module, can execute. ContextRuntime returns
ACTION_REQUIRED for transactional intent and never routes or executes the
action.

ActionPreparationOutcome is a closed union: Prepared(ActionTicket),
GenericDenied, AudienceChanged, or RetryableUnavailable with business effect
zero. ActionExecutionOutcome is Applied(ActionReceipt),
AlreadyApplied(ActionReceipt), Rejected(effectZero, reasonCategory), or
ReconciliationRequired(providerAttemptRef). Payload mismatch, wrong audience,
wrong effect, expiry, and invalid ticket are Rejected with effect zero. A replay
of an already applied ticket returns its stored receipt. An ambiguous provider
outcome enters reconciliation; it is never retried under a new ticket or allowed
to duplicate the effect.

Each effect has one ticket and one idempotency key. Creating a placeholder and
finalizing a reply are separate effects linked by DeliveryAttemptRef.

## 3. Runtime security pipeline

The only valid Runtime order is:

~~~text
trusted invocation and delivery validation
  -> real PostgreSQL transaction and transaction-local security context
  -> Membership, AgentVersion, Policy Epoch, SourcePolicy validation
  -> delivery audience and egress preflight
  -> query planning
  -> content-free CandidateRef retrieval and RRF
  -> AuthorizationKernel.authorizeAndProject
  -> AuthorizedProjection
  -> content-bearing rerank and dedupe
  -> parent or neighbor expansion back to CandidateRef
  -> re-authorization of every expanded ref
  -> deterministic PackageBudget assembly
  -> final egress decision
  -> ContextPackage plus per-hop EgressGrant
  -> authorized-only ContextRun and restricted DecisionAudit
~~~

### 3.1 CandidateRef

CandidateRef may contain opaque organization, source, resource, revision,
fragment, index-build, structure, and ranking references. It must not contain
body text, snippets, title, path, source field values, or provider metadata.

RRF and structural reference expansion may operate on CandidateRef before exact
authorization because they do not see content. Inside ContextRuntime, any
content-bearing reranker, LLM, tokenizer accounting, debug trace, or Assembler
accepts AuthorizedProjection only. The downstream generation boundary instead
accepts AuthorizedModelInput derived only from a current audience-bound
ContextPackage.

### 3.2 AuthorizationKernel

AuthorizationKernel is a sealed deep Module inside ContextRuntime. Production
composition cannot substitute or disable it. It owns the fixed order:

~~~text
policy snapshot
  -> tenant and membership enforcement
  -> source ACL evidence validation
  -> ResourceACL and field projection
  -> delivery audience constraint
  -> budget ceiling
  -> provenance and decision receipt
  -> audit
~~~

Infrastructure dependencies such as PostgreSQL, clock, keyring, and source
projection are internal seams. No-op policy, audit, budget, or provenance
implementations are invalid production compositions.

Only AuthorizationKernel can construct AuthorizedProjection. The type carries
projected fields, classification, source ACL evidence, policy snapshot,
decision reference, policy epoch, authorization as-of time, and provenance.

### 3.3 ContextProvider and source projection

ContextProvider remains the only external Source seam. Its V1 typed Interface
has four operations:

~~~text
describeCapabilities(SourceRef) -> ProviderOutcome<CapabilityDeclaration>
readChanges(SourceRef, ChangeCursor | InitialScan, ChangeLimit)
  -> ProviderOutcome<ChangePage<SourceChange, NextCursor>>
discover(ContextAccessTicket, RetrievalPlan, CandidateLimit)
  -> ProviderOutcome<CandidatePage<CandidateRef, SourceConsistencyRef>>
authorizeAndProject(ContextAccessTicket, CandidateRef[], ProjectionCeiling)
  -> ProviderOutcome<SourceProjectionBatch<SourceConsistencyRef>>
~~~

ProviderOutcome is a closed union: Ok, Unsupported(capability),
RetryableUnavailable(retryAfter), InvalidCheckpoint, or GenericDenied. It does
not turn unsupported, denied, or unavailable into an empty success. Batch
authorization records per-ref authorized projection evidence, generic denial,
or source-unavailable without exposing denied object details to ordinary trace.

CapabilityDeclaration is versioned per SourceVersion and declares Resource
kinds, ACL evidence mode, projection fields, cursor/checkpoint semantics,
deletion support, batch limits, freshness, and consistency guarantees. Change
cursors are opaque and monotonic within that SourceVersion. A cursor advances
only after its SourceChange page is durably accepted; it is distinct from the
publish watermark.

SourceConsistencyRef binds discovery to projection evidence: provider,
SourceVersion, authorization mode, source decision/snapshot version, and
checkedAt/aclAsOf as applicable. Kernel rejects a missing, mixed, stale, or
changed ref; native live providers either project in the same operation or use
their declared verify-before-and-after protocol.

readChanges carries content, ACL, and tombstone changes for Supply. discover is
used only by declared federated or hybrid modes and returns content-free
CandidateRef values. authorizeAndProject performs source-native authorization
and bounded field projection as one operation where the source supports it.

Inside ContextRuntime, live providers, PostgreSQL materialized storage, and weak
membership sources satisfy one internal SourceProjection seam:

~~~text
authorizeAndProject(
  ContextAccessTicket,
  CandidateRef[],
  ProjectionCeiling
) -> SourceProjectionBatch
~~~

The returned batch is evidence for Kernel validation, not an authorization
decision callers can trust directly.

FileProvider uses locally managed Mirrored SourceAclEvidence. It does not claim
to mirror host operating-system file permissions. An explicit versioned
FileSourceAccess manifest is stored in PostgreSQL and activated with the File
source. Missing, incomplete, or unknown grants deny: there is no implicit owner,
filesystem-owner, or public fallback. Its aclAsOf is the local commit and it is
evaluated together with Membership, ResourceACL, and purpose policy.

## 4. Source ACL evidence and revocation

SourcePolicy fixes one authorization evidence mode when a SourceVersion becomes
active. A request, agent, failure handler, or provider cannot select or downgrade
the mode.

### 4.1 Native live

- Source authorization and field projection occur in the same request or in a
  documented verify-before-and-after protocol.
- Evidence records source decision/version and checkedAt.
- Authorization timeout, inconsistency, or outage fails closed for that source.
- The result may be an explicitly partial Package, but never a weak-ACL fallback.

### 4.2 Native mirrored

- PostgreSQL stores a versioned SourceAclProjection with aclAsOf, observedAt,
  source version, and declared lag bound.
- Exact authorization means exact relative to that explicit snapshot.
- An upstream change is covered by next-request revocation only after an event or
  poll is durably observed and its Policy Epoch update commits.
- Package Evidence exposes the relevant as-of and freshness information.

### 4.3 Declared weak membership

- Allowed only when the source truthfully lacks finer native ACL.
- A complete conversation membership snapshot substitutes for SourceNativeACL.
- The source declares a freshness bound and sensitivity policy at enrollment.
- Incomplete membership, expired snapshot, unknown sensitivity, or unsupported
  history semantics denies delivery.
- Weak mode cannot be entered because a live or mirrored provider is unavailable.

### 4.4 Revocation linearization

Local access mutation, Policy Epoch bump, DecisionAudit, and outbox publication
commit atomically. The authorization linearization point is inside the request
transaction after the current epoch and source evidence are validated.

V1 does not cache final ContextPackage or AuthorizedProjection as an
authorization authority. Any future cache hit must revalidate Membership,
Policy Epoch, source evidence freshness, tombstone, audience digest, and field
projection.

Old Package bytes cannot be invalidated after an external caller has received
them. Trusted callers must discard expired Package values and may not reuse them
for new operations. Historical IM body retention belongs to Delivery policy and
Action Plane compensation.

## 5. Delivery audience, egress, and capability taxonomy

### 5.1 TrustedDeliveryContext

TrustedDeliveryContext is a nominal server-authored type:

- DirectDelivery for an agent consumer.
- PrivateDelivery for one bound Membership and destination.
- GroupDelivery for an asker, destination, AudienceSnapshot, and platform
  history-exposure semantics.

AudienceSnapshot contains member references, completeness, observed time,
expiry, provider membership epoch, destination binding, and audience digest. It
contains facts, not precomputed scope.

DeliveryEvidenceRef is the remote ingress bridge, not caller-authored delivery
data. It reveals no member list or trusted claims, is bound to the authenticated
BotDelivery service and one resolve request id, and is idempotently redeemable
only for that same request. Forgery, cross-request replay, expiry, wrong service,
or wrong destination fails before provider, index, model, or Package work.

The Kernel computes:

~~~text
EffectiveDeliveryScope =
  asker EffectiveScope
  intersect every audience member EffectiveScope
  intersect EgressPolicy
~~~

Group audience facts never enter RequestNarrowing.

An incomplete, expired, or partially unbound audience produces zero public
content bytes. A private asker resolve may still run independently.

If future members can read history and that future audience cannot be bounded,
protected body text is not posted publicly. The group may receive only a generic
non-enumerating notice or a per-opener CitationOpenRef; the asker receives a
separate private resolve.

Public and private Package values are always separate ContextRuns. BotDelivery
must not fetch an asker-wide Package and partition it after the fact.

Immediately before final group send, ActionPlane revalidates the audience
digest. A change returns AUDIENCE_CHANGED with business effect zero and requires
a new resolve.

### 5.2 Trusted egress path

Trusted ingress, BotDelivery, ModelGateway, ActionPlane, and Sender form the
delivery TCB. Ordinary application code cannot call model or IM network Adapters
with Package body text.

ContextRuntime emits a per-hop EgressGrant bound to Package digest,
Organization, purpose, policy snapshot/epoch, audience digest, provider, model
or channel kind, region, retention class, sensitivity ceiling, and expiry.

AuthorizedModelInput is constructed inside BotDelivery only from one current
audience-bound ContextPackage and its purpose/retention policy; it cannot accept
CandidateRef, SourceProjectionBatch, raw provider content, or denied data.
ModelGateway accepts AuthorizedModelInput plus a matching model EgressGrant.
Sender accepts an operation-specific ActionTicket plus a matching payload
digest. Wrong provider, region, channel, audience, purpose, or digest results in
zero outbound bytes or effects.

### 5.3 Tokens and locators

The following types are not interchangeable:

- DeliveryEvidenceRef: opaque trusted-ingress attestation locator, bound to one
  authenticated service and resolve request id; it is not an authorization
  grant and carries no raw audience claims.
- ContinuationToken: signed, principal-bound, audience-bound, short-lived,
  one-shot, cumulative-budget capability. It returns a complete replacement
  Package.
- CitationOpenRef: random opaque locator, multi-use, not authorization. Each
  redemption authenticates the current opener and performs current exact
  authorization. Denied, missing, deleted, revoked, and expired all map to
  CITATION_NOT_AVAILABLE.
- EgressGrant: permits one Package digest to one provider/audience hop. It does
  not permit a write.
- ActionTicket: one operation, destination, audience, payload digest, epoch,
  expiry, nonce, approval tier, and idempotency key.
- ContextAccessTicket: source-read capability only.

CreatePlaceholder and FinalizeReply use different ActionTicket values and
different idempotency keys. They share only DeliveryAttemptRef and identity
lineage.

## 6. Supply, immutable publication, and curation

### 6.1 Resource publication

The Supply chain remains:

~~~text
Source -> Resource -> immutable Revision -> Fragment
       -> prepared -> indexed -> active
~~~

The per-Resource active pointer changes in one PostgreSQL transaction. A query
sees the full old Revision or the full new Revision.

Three change domains are separate:

- representation-affecting content or metadata creates a new Revision; unchanged
  blobs or embeddings may be reused only when their inputs are identical;
- ACL changes create policy/ACL versions and bump Policy Epoch as required;
- sync cursor, retry, and operational metadata never mutate Revision semantics.

There is no generic metadata-only mutation of immutable Revision data.

For File ingestion, content identity is versioned and scoped to Organization,
Source, and stable Resource. It binds the SHA-256 of exact canonical UTF-8 text
plus compiler and configuration versions. After an acquisition and exact
WorkerLease-backed job are durable, the worker reads and compiles the File, then
one PostgreSQL transaction locks the Resource's ingestion-guard row before it
classifies or publishes. Only a complete non-tombstoned active artifact with
the exact snapshot, `prepared -> indexed -> active` events, compiled Fragments,
and index candidates can be `unchanged`. The immutable acquisition outcome then
records only active lineage, a tenant-scoped content-identity digest, and the
fixed no-op reason/digest; it retains no source content. The job completes with
zero publication effects while Revision, Fragment, access, candidate, event,
and active-pointer state remain unchanged. The acquisition's exact Principal
and Membership version must still be active, temporally valid, and retain body
access; content equality never creates or repairs authorization. Equal content
in another Organization is independently owned. Changed bytes,
compiler/config changes, revoked/different access, and partial publication
never take this path. ADR-0039 owns the byte domain and no-op concurrency
boundary.

Changed File content enters the separate ADR-0040 replacement path. One staging
transaction revalidates the exact WorkerLease, current acquisition authority,
and complete old active artifact, then persists the complete immutable new
Revision, snapshot, Fragments, candidates, `prepared -> indexed` events, and a
durable replacement plan before marking the job `ready`. The old Revision stays
active throughout staging. A second transaction revalidates authority and
readiness, compare-and-swaps the Resource active pointer, appends `active`,
records immutable supersession lineage, and completes the job. Superseded
artifacts remain `retained_until_explicit_cleanup`; ADR-0042 activates only
manual deletion of the current File Resource, not superseded-revision cleanup.
When an equivalent concurrent
replacement activates between the initial publish attempt and replacement
staging, the guarded stage classification completes the later job as
`unchanged` and returns that durable zero-effect result. V1 and V2 each reprove
that the supplied compilation exactly matches the now-active snapshot,
Fragments, and candidates before reporting success.

ADR-0041 makes publication recoverable after exactly the committed `acquired`,
`prepared`, and `ready` boundaries. One tenant-owned checkpoint binds the
existing File job to its stable Resource/Revision/content identity, while an
immutable job-event stream records interruption, reclaim, and completion.
Reclaim issues a higher signed lease generation only after expiry; the replaced
generation and nonce cannot resume or mutate, including through compatibility
publication seams. Each idempotent step revalidates the exact current lease and
advances one checkpoint. Initial and replacement activation recheck
current audience authority and complete Fragment/candidate evidence before the
single pointer transaction, so a recovered replacement leaves old active
content visible until the new Revision is complete. Automatic retry scheduling,
arbitrary chaos, delete recovery, and dead-letter handling remain inactive.

ADR-0042 activates the manual trusted-Control tombstone carrier for one known
published File Resource. One transaction shares the Organization publication
visibility lock with Runtime and activation, flips only the Resource tombstone,
advances the Organization Policy Epoch, and appends an immutable pending cleanup
intent bound to the retained active Revision. Runtime then exposes neither the
Resource nor its physical Revision/Fragments; a deliberately stale candidate
still produces the same canonical empty HTTP Package as an unknown Resource.
Duplicate and older deletion observations return the original result without
another epoch bump. Physical cleanup, restore, native watcher detection, source
offboarding, and cleanup-job execution remain inactive.

Because Runtime resolves through multiple SQL statements at `READ COMMITTED`,
each UserActor transaction takes an Organization-scoped shared publication
barrier and activation takes the matching exclusive transaction barrier around
validation and pointer swap. This barrier supplies all-old/all-new transaction
visibility; it is not authorization and does not replace the
`CandidateRef -> AuthorizationKernel -> AuthorizedProjection` path. Other
Organizations do not share the barrier, and activation mutates only the exact
Resource named by the replacement plan.

Markdown compilation is representation-versioned. The frozen v1 contract keeps
its original heading-plus-paragraph bytes. Version 2 emits one source-ordered
Fragment for each heading, paragraph, flat list, fenced code block, or table,
with a stable structural path and exact canonical UTF-8 source span. Lists,
code blocks, and tables remain coherent units rather than item, line, or cell
Fragments. Parent heading ancestry is copied into the same Fragment during pure
compilation, so Runtime performs no parent expansion: the complete contextual
block crosses one `CandidateRef -> AuthorizationKernel -> AuthorizedProjection`
decision and consumes Package budget as one content-bearing value. Unsupported
syntax fails all-or-nothing. The immutable Revision snapshot binds the complete
versioned compilation document; publication of every Fragment and index alias
remains one transaction. ADR-0038 owns the detailed grammar and compatibility
boundary.

### 6.2 CurationSnapshot

Curation runs after Revision activation and is not on the publication critical
path. Audited CurationAnnotation values are assembled into a separate immutable
CurationSnapshot with compatibility references to Revision ids. Its active
selection is independent of the content Revision pointer, but it changes only
as part of a release-operator-authorized ContextLearning.promote operation; the
curation pipeline cannot activate a snapshot directly.

Runtime reads active Revision and the compatible CurationSnapshot selected by
the active ReleaseManifest in one database snapshot. Missing or failed curation
means normal retrieval without curation, not a Supply failure.

Curation is an experimental C1 track after the retrieval/eval baseline. It does
not block the design-partner opening gate. Promotion requires registered sample
sizes, per-kind thresholds, confidence reporting, and a frozen curation-on/off
comparison.

### 6.3 Release ownership

Profiles are internally split into immutable ContentProfile, IndexProfile,
RuntimeProfile, and CurationProfile references composed by ReleaseManifest.
CurationProfile contains an optional CurationSnapshotRef plus its compatible
Revision set and evaluation digest; therefore the active ReleaseManifest
unambiguously selects either one compatible snapshot or curation-off.

ContextLearning evaluates a ReleaseCandidate and is the only Module that can
promote its ReleaseManifest. ContextControl does not publish profiles. Supply,
Runtime, and Learning do not import each other cyclically; they exchange
persisted records and outbox events.

## 7. Persistence, RLS, audit, and privacy

### 7.1 PostgreSQL transaction context

Schema owner/migrator and runtime/worker roles are separate. Runtime and worker
roles are NOSUPERUSER, NOBYPASSRLS, NOINHERIT, and non-owner.

ActorContext is a closed union:

- UserActor contains the authenticated Principal and Membership used for an
  online request;
- ServiceActor contains a registered ServicePrincipal, workload identity,
  Organization, allowed source/operation set, policy epoch, and expiry.

A Supply worker always uses a least-privilege ServiceActor bound to its durable
job and WorkerLease. It never invents or borrows the user who triggered a source
change, and service ingestion authority never substitutes for end-user Runtime
authorization.

WorkerLease is server-minted and signed. It binds Organization, job, operation, source and optional
resource/revision, ServiceActor/workload, policy epoch, optional delivery
audience, idempotency key, lease generation, issued-at, expiry, and nonce.
Redemption checks every claim against the current durable job row. A mutated,
cross-job, cross-resource, expired, stale-generation, or replayed lease denies.

Each request or worker operation:

1. begins a transaction;
2. sets Organization and ActorContext transaction-locally;
3. validates that context;
4. only then permits ORM autoflush, query, or provider/index work;
5. rolls back on cancellation or error and returns a clean pooled connection.

SECURITY DEFINER is denied by default. An approved function fixes search_path,
has a narrow owner, and receives dedicated negative tests.

### 7.2 Schema security manifest

Every table and partition appears in a versioned schema security manifest as
global or tenant-owned. A tenant-owned table must have Organization ownership,
composite foreign keys, USING and WITH CHECK policies, FORCE RLS, and policy
tests. Coverage is computed over the manifest, not over tables that already
happen to contain organization_id.

### 7.3 ContextRun and DecisionAudit

ContextRun is tenant-visible Learning data and contains only authorized
projections, selected Evidence references, PackageRecord, version references,
budget, timing, and permitted feedback. PackageRecord stores digest,
authorized-evidence references, and reconstruction metadata; body persistence
requires an explicit retention policy.

DecisionAudit is append-only restricted security data. Pre-authorization denied
or cross-Organization candidates are recorded only as reason categories,
aggregate counts where non-enumerating, or irreversible digests. They never
enter tenant-visible ContextRun, prompt debug, or ordinary trace.

Query text and any retained authorized payload require an explicit retention,
encryption, access, export, and deletion policy before M1 records real usage.

## 8. Retrieval, budget, evaluation, and tests

### 8.1 Retrieval

V1 starts with PostgreSQL FTS and later adds pgvector plus deterministic RRF.
Chinese tokenization and multilingual embedding are selected through a frozen
evaluation, not hard-coded as product truth.

Approximate vector retrieval is benchmarked against exact search under selective
Organization, Membership, source, and policy filters. Exit evidence includes
recall delta, underfilled result rate, EXPLAIN ANALYZE, corpus size, hardware,
iterative scan or oversampling settings, latency, and cost.

Rerank is a disabled seam until an ablation on the frozen dataset proves
slice-level benefit without breaking latency, cost, security, or PackageBudget.

### 8.2 Budget ownership

ContextRuntime owns PackageBudget: the intersection of server profile ceiling
and a caller-requested smaller cap, with tokenizer/version and usage recorded.
The upper Agent Runtime owns PromptBudget because it knows system prompt,
conversation history, query, model window, and answer reserve.

PackageBudget may include tokens, provider calls, cost, and wall-time limits.

### 8.3 Highest test seams

Behavior tests use the same Interfaces as callers:

- ContextRuntime.resolve
- BotDelivery.answer and openCitation
- ActionPlane.prepare and perform
- ContextProvider shared contract

Real PostgreSQL 17 tests prove RLS, composite FK, transaction context, pool
cleanup, outbox, revocation, and filtered retrieval. Deterministic fakes may
prove domain behavior through the same Interface, but do not count toward a
database, sandbox, live-provider, wire-egress, or external-effect gate. A twin
may prove protocol conformance and failure mapping; only the real source sandbox
and declared live path can raise the corresponding capability tier.

The invariant system keeps three dimensions separate:

- applicability is preregistered in a versioned catalog as required,
  conditional with applicableFrom, or NOT_APPLICABLE with an approved rationale;
- capability activation/coverage is reported independently as unavailable,
  implemented, contract-verified, sandbox-verified, or live-verified;
- an applicable active invariant result is PASS or FAIL.

The rendered release status remains exactly PASS, FAIL, NOT_ACTIVE, or
NOT_APPLICABLE. NOT_APPLICABLE is legal only when the frozen catalog excluded it
before the run. NOT_ACTIVE means the applicable capability is declared inactive
and its boundary was proven unreachable. An active but unexecuted, missing, or
unmapped invariant is FAIL. Every milestone names required entries; each must be
PASS, so neither NOT_ACTIVE nor NOT_APPLICABLE can satisfy a required exit gate.
Every entry maps to domain property, database, and runtime or delivery cases.

Required adversarial cases include:

- malicious cross-Organization CandidateRef and denied bytes to rerank/model = 0;
- live source outage does not enter weak mode;
- mirrored ACL freshness and upstream-observation semantics;
- group join before send, join after send/history, unbound/external member,
  audience cache collision, and destination reuse;
- A-authorized, B-denied, A-authorized repeated CitationOpenRef redemption;
- ContinuationToken replay, stale epoch, wrong principal, and wrong audience;
- wrong model, region, channel, purpose, payload digest, and token audience;
- forged, expired, cross-request, wrong-service, and wrong-destination
  DeliveryEvidenceRef;
- revocation or audience change between placeholder and final effect;
- ContextRun and trace contain no denied content or secret token;
- missing tenant and connection-pool cross-Organization stress.
- ServiceActor mutation, wrong workload/job/source/operation, expired lease,
  per-claim WorkerLease mutation/replay, and attempted user impersonation.

## 9. Milestone plan

### D0 - Design closure and evidence retirement

Exit:

- this authority, glossary, ADRs, security contracts, public PLAN, and PRD agree;
- an immutable, self-contained repository design baseline is pinned;
- no open P0 architecture or security decision;
- Feishu sandbox report covers Docs/Wiki/Base permissions, group membership,
  bot send/edit, events, rate limits, credentials, editions, and go/no-go;
- PostgreSQL RLS transaction-context and filtered pgvector spikes are recorded;
- installation, test, lint, build, and report commands are chosen.

### M0 - Secure engineering skeleton

Exit:

- clean checkout builds API and worker and migrates empty PostgreSQL 17;
- schema owner, migrator, runtime, and worker roles are separated;
- schema security manifest and real RLS harness are green;
- two Organizations and multiple Memberships prove missing-context,
  cross-Organization, and same-Organization isolation;
- sealed AuthorizationKernel returns a tenant-safe empty Package;
- an authorized release operator promotes the empty initial ReleaseManifest
  through ContextLearning.promote, while direct bootstrap-pointer writes fail;
- invariant catalog reports capability coverage without false green.

### M1 - File to authorized Package tracer bullet

Exit:

- one Markdown document travels through FileProvider, outbox/worker, immutable
  Revision activation, lexical FTS, HTTP resolve, AuthorizedProjection,
  ContextPackage, PackageRecord, ContextRun, and DecisionAudit;
- allowed caller receives one cited block;
- denied, missing-tenant, and cross-Organization callers receive Evidence zero;
- revoke, tombstone, retry, and publication crash cases show only complete old or
  complete new state;
- query/audit retention policy is active before real logs accumulate.

M1 proves the minimum single-Resource safety semantics with one fixture per
revoke, tombstone, retry, and active-pointer crash. M3 owns the production
incremental corpus, exhaustive fault matrix, checkpoint replay, leases,
dead-letter handling, and operational runbooks.

FileProvider is production Adapter number one and establishes the Provider base
contract runner.

### M2 - Wire contract and private BotDelivery caller

Exit:

- resolve and ContextPackage OpenAPI v0 are frozen;
- trusted identity and delivery fields are absent from the wire body, while an
  opaque DeliveryEvidenceRef in authenticated transport metadata is redeemed by
  ingress into TrustedDeliveryContext;
- generated TypeScript SDK builds, typechecks, packs, and calls a real local API;
- breaking-change diff gate is active;
- the Bot application cannot import engine internals and calls only HTTP/SDK;
- a private Feishu delivery twin performs placeholder, File-backed resolve,
  controlled generation, final edit/follow-up, and delivery audit;
- citation redemption, wrong egress, wrong token audience, and per-effect action
  tests are green.

M1's minimal HTTP endpoint is provisional and internal: it proves the semantic
resolve contract but makes no public compatibility promise. M2 freezes the
versioned public wire and activates breaking-change checks.

### M3 - File reliability and retrieval/eval baseline

Exit:

- Markdown AST, hash incrementality, delete detection, checkpoint replay,
  broader crash recovery, dead-letter, and full-resync runbook are complete;
- before experiment execution, the frozen dataset registers a sample plan based
  on failure-slice coverage and a declared uncertainty/power target; negative
  cases cover every active refusal/security category, and an underpowered set is
  reported only as pilot or inconclusive;
- lexical baseline, Chinese tokenizer, multilingual embedding, hybrid RRF,
  hydration, PackageBudget, and continuation are evaluated;
- exact-versus-ANN filtered benchmark is recorded;
- rerank remains off unless preregistered ablation proves benefit;
- queue age, publish lag, retry, failure, and retrieval quality are observable.

### C1 - Curation experiment, parallel and non-blocking

Starts after M3 and may run beside M4/M5. Exit for promotion:

- immutable CurationSnapshot path works;
- each annotation kind has registered sample size and threshold;
- each annotation kind meets its preregistered audit sample plan and reports
  uncertainty; underpowered results remain inconclusive;
- frozen curation-on/off comparison proves benefit;
- failure remains normal retrieval without curation.

### M4 - Feishu upstream and private-chat closed loop

Exit:

- Provider capability suite and twin harness are frozen before implementation;
- FileProvider and FeishuProvider pass the same base suite;
- Docs/Wiki create, update, delete, revoke, duplicate, out-of-order, 429, and 5xx
  cases pass in sandbox and live conformance;
- Feishu content answers through the real private BotDelivery path;
- Base field ACL activates only when edition and live capability evidence pass;
- a missing strong ACL path excludes or fails the source and never becomes weak.

FeishuProvider is production Adapter number two; its experience freezes
contract-kit v1.

### M5 - Group delivery and private-cell launch readiness

Exit:

- group public/private dual resolve and current/future audience semantics pass;
- send-time audience revalidation, historical visibility, non-enumerating
  fallback, and citation per-opener cases pass;
- fixed-load private and group latency reports define engine and generation
  boundaries;
- immutable image, independent migration job, N-1 to N rehearsal, rollback
  rules, backup restore, declared RPO/RTO, secret rotation, dashboard, alerts,
  and operator runbooks are complete;
- Security, Reliability, Quality, and Budget reports are all green.

Engineering Gate E5 ends here when Security, Reliability, Quality, and Budget
reports are green and Ops readiness is demonstrated; E5 does not require a
partner to exist first. Design-partner agreement, legal review, product naming,
and commercial approval are the subsequent, separate Launch Gate L1. Invited
use starts only after L1; public SaaS remains a later decision.

### M6 - Slack and contract-kit reuse

One connector only. Slack validates contract-kit v1 without destructive
Interface changes and includes live conformance, message retention, delete/edit,
rate-limit, and source ACL behavior.

### M7 - Google Docs reuse

Google Docs/Drive is a separate milestone so failures can be attributed. It must
reuse the same kit and prove OAuth or delegation, ACL, update/delete, and live
conformance behavior.

### P3 - WeCom feasibility before scheduling

WeCom is not an implementation milestone until paid archive access, legal
boundary, delete/edit reconciliation, region/retention, and weak-ACL evidence
are proven. If admitted, it receives its own milestone and explicitly weaker
freshness claim; it never inherits a next-request real-time claim.

## 10. Explicitly out of scope for V1

- public connector marketplace or untrusted Connector Host;
- microservice split before a measured boundary requires it;
- CrossOrgLearningArtifact schema or Interface;
- external index portability before a second real implementation;
- GraphRAG, RAPTOR, or relationship inference without eval-triggered need;
- streaming before latency evidence requires it;
- untrusted consumers receiving cleartext Package;
- claiming revocation of bytes already delivered to external systems;
- Base field ACL where the tenant edition or API cannot prove it;
- implementing two new connectors in one milestone.

## 11. Residual evidence gates

The following are evidence gates, not design ambiguity:

- exact Feishu editions, permission endpoints, event coverage, rate limits, and
  bot edit semantics;
- Chinese tokenizer and embedding selection;
- filtered ANN parameters and partition strategy;
- group history exposure semantics by platform;
- private/group latency budgets after measured decomposition;
- RPO/RTO and retention values for the first design partner;
- WeCom access, cost, legal, deletion, and residency facts.

No evidence failure may silently weaken authorization. It narrows capability,
delays the relevant milestone, or produces a typed unavailable result.
