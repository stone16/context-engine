# ContextEngine Domain Glossary

This repository-owned glossary is the canonical terminology contract for
ContextEngine. 中文解释与英文定义具有相同语义；实现细节以
[`Implementation Design v1.2`](docs/design/2026-07-18-context-engine-implementation-design.md)
和 accepted ADR 为准。公开参考事实须回引
[`four-repository evidence baseline`](docs/research/2026-07-19-four-public-repositories-evidence.md)
所固定的一手来源；仓库外研究不是公开 authority 或 provenance。

本文件只固定名称、scope、lifecycle 和 authorization role，不定义 executable
schema、Python type、threat fixture 或新的架构决策。

## How to read this glossary

- **Persistent**：对象具有跨请求的 durable identity 或 state。
- **Request-scoped**：对象的语义绑定一次 authenticated invocation 或
  `ContextRun`；保留审计 lineage 不会把它变成长期 source content。
- **Authorization authority**：参与当前授权决策的受信输入。任何一个输入都不能
  单独授权；必需约束取交集，缺失时 fail closed。
- 每个 tenant-owned object 只属于一个 `Organization`。Organization reference
  只是安全边界，不是 access grant。

## Required-term classification

| Canonical term | Persistence and lifecycle | Owner or scope | Authorization role |
|---|---|---|---|
| `Organization` | Persistent security root | Self; boundary for tenant-owned state | Required boundary, never a grant by itself |
| `User` | Persistent human identity | Global; joins an Organization only through Membership | None by itself |
| `Membership` | Persistent, versioned, revocable relationship | One Organization and one User | Current rights are one required authority input |
| `Principal` | Request-scoped authenticated actor designation | One Organization and authenticated invocation | Current grants are one required authority input |
| `Agent` | Persistent logical application/scenario identity | One Organization | None; owns versions that only narrow access |
| `AgentVersion` | Immutable version used by a request | One Organization and Agent | Delegation ceiling only; never an identity or grant |
| `ContextSource` | Persistent logical source registration | One Organization | Source policy boundary, never sufficient alone |
| `SourceVersion` | Immutable source-configuration snapshot | One Organization and ContextSource | Active configuration constrains access, never grants it |
| `ContextResource` | Persistent stable source-object identity | One Organization and ContextSource | Resource ACL/state constrain access, never grant it |
| `ContextRevision` | Persistent immutable canonical snapshot | One Organization and ContextResource | None; visibility is not authorization |
| `ContextFragment` | Persistent content derived from one ContextRevision | Same Organization/Resource/Revision lineage | None; an index hit cannot authorize it |
| `Evidence` | Request-scoped designation in one ContextRun | One Organization, run, Principal, purpose, and decision | Authorization result, never an authority |
| `ContextRun` | Persistent authorized-only resolution lineage | One Organization and invocation | Learning/audit lineage, not a grant |
| `PolicySnapshot` | Request-scoped decision snapshot; lineage may persist | One Organization and ContextRun | Records decision inputs; stale snapshots cannot authorize |
| `Policy Epoch` | Persistent monotonic revocation value | One Organization in V0; finer granularity is not active | Current value is a freshness check, not a grant |
| `ContextPackage` | Request-scoped, expiring online output | One Organization, ContextRun, Principal, purpose, and audience | Authorized output, not reusable authority |
| `ContextAccessTicket` | Short-lived signed source-read capability | One Organization, identity chain, provider audience, purpose, epoch, and expiry | Authority only for its declared read audience |
| `ActionTicket` | Short-lived signed one-effect capability | One Organization, identity chain, effect/audience/payload, epoch, and expiry | Authority only for its declared external effect |
| `WorkerLease` | Short-lived signed one-shot work capability | One Organization, durable job, registered service workload, expiry, and nonce | Authority only for the matching job attempt |
| `File acquisition outcome` | Persistent immutable completed-observation lineage | One Organization, ContextSource, acquisition, ContextResource, and active ContextRevision | None; deduplication evidence is not content or authority |
| `acquisition checkpoint` | Persistent monotonic acquisition progress | One Organization and ContextSource | None |
| `publish watermark` | Persistent monotonic visibility progress | One Organization and ContextSource | None |

Reviewer classification: `ContextRevision` and `ContextFragment` are persistent;
`Evidence` is request-scoped. None of the three is an authorization authority.

## Canonical definitions

### `Organization`

The tenant and security root. 中文：Organization 是租户隔离、归属和策略计算的根边界。

- **Owner/scope:** globally identified; every tenant-owned row, blob, index
  record, job, and trace belongs to exactly one Organization.
- **Lifecycle:** durable and distinct from Membership, ContextSource, or
  ContextResource lifecycle changes.
- **Invariant:** an Organization reference selects a boundary but never proves
  that a caller may read or act within it.
- **Do not confuse with:** workspace, source, deployment cell, or caller-supplied
  `tenant_id`. `tenant` is explanatory prose only.

### `User`

A persistent human identity that may participate in multiple Organizations.
中文：User 表示全局的人，不携带任何 Organization 内权限。

- **Owner/scope:** global; Organization association exists only through a
  Membership, as settled by issue #11.
- **Lifecycle:** independent of Memberships; removing one Membership does not
  redefine the User.
- **Invariant:** User ID alone is neither an authenticated Principal nor
  Organization authorization.
- **Do not confuse with:** Membership, Principal, IM account, or Agent.

### `Membership`

The durable, revocable relationship binding one User to one Organization.
中文：Membership 才承载 User 在某个 Organization 内的当前资格与权利。

- **Owner/scope:** tenant-owned by exactly one Organization and linked to one
  User.
- **Lifecycle:** active, versioned, expired, or revoked; inactive or stale
  Membership contributes no rights.
- **Invariant:** current Membership rights are required but are intersected with
  all other required authorization constraints.
- **Do not confuse with:** User, Principal, group, or delivery audience.

### `Principal`

The request-scoped designation of the authenticated human or service actor on
whose behalf an online invocation is evaluated. 中文：Principal 由可信认证链为本次调用
解析，不能由 request body 自报。

- **Owner/scope:** one Organization and authenticated invocation; this glossary
  does not choose how an identity provider stores the underlying account.
- **Lifecycle:** constructed from verified authentication context; missing,
  revoked, or invalid bindings fail closed.
- **Invariant:** Principal grants cannot bypass Membership, Agent ceiling,
  source/resource ACL, purpose policy, or delivery audience constraints.
- **Do not confuse with:** User, Membership, Agent, ServicePrincipal, or
  AudienceSnapshot.

### `Agent`

The persistent logical identity of a versioned application or scenario
configuration. 中文：Agent 是配置入口，不是代替 User/Principal 授权的身份。

- **Owner/scope:** tenant-owned by one Organization.
- **Lifecycle:** stable while configuration changes are published as immutable
  AgentVersions.
- **Invariant:** an Agent has no independent rights and never expands Principal
  scope.
- **Do not confuse with:** Principal, ServicePrincipal, worker, model, or
  BotDelivery.

### `AgentVersion`

An immutable Agent configuration version containing its delegation ceiling.
中文：AgentVersion 只给委托权限设上限，不能赋予 Principal 原本没有的权限。

- **Owner/scope:** same Organization and Agent; a request binds the exact version
  it used.
- **Lifecycle:** immutable and superseded by a later version rather than edited
  in place.
- **Invariant:** **Agent/AgentVersion is a delegation ceiling, not an independent
  authorization identity.** It only narrows effective scope.
- **Do not confuse with:** Principal grant, Membership right, model version, or
  WorkerLease.

### `ContextSource`

The persistent logical registration of an external content source.
中文：ContextSource 是 Organization 内 File、API 或其他知识来源的稳定注册身份。

- **Owner/scope:** tenant-owned by one Organization.
- **Lifecycle:** stable while configuration/capability snapshots are represented
  by SourceVersions; source offboarding is separate from content deletion.
- **Invariant:** owns ContextResources and declares source policy/capabilities;
  callers and Agents cannot choose a weaker source mode.
- **Do not confuse with:** `ContextProvider` adapter, credential,
  ContextResource, or SourceVersion.

### `SourceVersion`

An immutable snapshot of ContextSource configuration, capabilities, ACL evidence
mode, and source policy. 中文：SourceVersion 固定某次 source 配置，active-version
pointer 选择当前版本。

- **Owner/scope:** one Organization and ContextSource.
- **Lifecycle:** immutable and superseded rather than edited; creation,
  activation, and disable procedures belong to the implementation design and
  accepted ADRs.
- **Invariant:** work and source consistency evidence bind the exact version
  where required; inactive/stale configuration cannot silently substitute.
- **Do not confuse with:** ContextSource, ContextRevision, connector release, or
  source-native document version.

### `ContextResource`

The persistent stable identity of one source-owned object, such as one file or
document. 中文：ContextResource 表示 source 中具有独立 lifecycle 的对象，不是内容快照。

- **Owner/scope:** one Organization and ContextSource with stable source lineage.
- **Lifecycle:** points atomically to its active ContextRevision and may be
  tombstoned; content changes preserve Resource identity.
- **Invariant:** the active pointer changes only through publication; visibility
  and source/resource ACL still require exact authorization.
- **Do not confuse with:** raw bytes, ContextRevision, ContextFragment, or search
  result.

### `ContextRevision`

An immutable canonical content snapshot of one ContextResource.
中文：ContextRevision 是不可变内容版本；发布时读者只见完整旧版或完整新版。

- **Owner/scope:** persistent through exactly one Organization, ContextSource,
  and ContextResource lineage.
- **Lifecycle:** `prepared -> indexed -> active`; activation is an atomic
  ContextResource pointer change, and superseded revisions may remain retained.
- **Invariant:** content is never mutated in place; physical or active presence
  never authorizes delivery.
- **Do not confuse with:** SourceVersion, mutable document, ContextFragment, or
  Evidence.

### `ContextFragment`

A persistent unit derived from one ContextRevision for indexing, retrieval,
projection, ranking, and budget assembly. 中文：ContextFragment 是长期内容/检索单元，
不表示任何请求有权看到它。

- **Owner/scope:** same Organization, ContextResource, and ContextRevision
  lineage as its parent revision.
- **Lifecycle:** reproducibly compiled and visible only through its
  ContextRevision publication state.
- **Invariant:** a CandidateRef/index hit is only a candidate. Exact authorization
  and selection are required before the Fragment becomes Evidence.
- **Do not confuse with:** CandidateRef, AuthorizedProjection, Evidence,
  citation, or ContextPackage block. `chunk` is not a canonical object name.

### `Evidence`

The request-scoped designation given only to a ContextFragment that passed exact
authorization and was selected in one ContextRun. 中文：Evidence 不是长期存储内容，
而是某次 run 中“已精确授权且已选中”的 Fragment 身份。

- **Owner/scope:** one Organization, ContextRun, Principal, purpose, audience,
  as-of time, PolicySnapshot, Policy Epoch, and decision reference.
- **Lifecycle:** created after authorization and selection; expires with request
  authority while its safe lineage may persist.
- **Invariant:** **persistent content is ContextFragment; only an exact-authorized,
  selected Fragment in one ContextRun is Evidence.** Evidence is the outcome of
  authorization, not its authority.
- **Do not confuse with:** ContextFragment, candidate, AuthorizedProjection,
  citation, source quote, or generic supporting information.

### `ContextRun`

The durable, authorized-only lineage of one `ContextRuntime.resolve` execution.
中文：ContextRun 连接一次有效调用、已授权 Evidence、Package、budget/performance 和
允许的 feedback，是 Learning 数据。

- **Owner/scope:** persistent and tenant-owned by one Organization and
  authenticated invocation.
- **Lifecycle:** accepted and finalized with its outcome; later feedback may
  reference it without creating a new run.
- **Invariant:** denied content/object details never enter tenant-visible
  ContextRun; restricted denial lineage belongs to DecisionAudit.
- **Do not confuse with:** HTTP request, trace, DecisionAudit, PolicySnapshot,
  ContextPackage, or user session.

### `PolicySnapshot`

The request-bound snapshot of versioned policy and egress decision inputs used
for one ContextRun. 中文：PolicySnapshot 解释一次授权依据，不冻结未来授权。

- **Owner/scope:** one Organization and ContextRun; safe decision lineage may be
  persisted while authority remains request-scoped.
- **Lifecycle:** captured during authorization and bound to purpose, audience,
  Policy Epoch, as-of time, and decision reference.
- **Invariant:** a snapshot explains a past decision but stale state cannot
  authorize later delivery or reuse.
- **Do not confuse with:** current policy, Policy Epoch, policy version, grant,
  or ContextPackage.

### `Policy Epoch`

A tenant-owned, monotonically increasing local revocation value.
中文：Policy Epoch 在引擎 durable observation 后阻止旧授权决策被继续复用；物理
cleanup 可异步。

- **Owner/scope:** persistent tenant-owned state. V0 stores exactly one value
  per Organization; Source/Resource refinement remains a measured future
  decision rather than an active second authority.
- **Lifecycle:** only moves forward through a trusted access change. The active
  V0 Control transaction commits the access mutation and epoch advancement
  together; failure commits neither and a value is never reused.
- **Invariant:** an Acquire decision must bind the observed Organization epoch
  and pass a current-value check immediately before delivery. Stale decisions
  fail closed. Epoch cannot detect an upstream change not yet observed, grant
  access by itself, or recall bytes already delivered.
- **Do not confuse with:** PolicySnapshot, configuration version, acquisition
  checkpoint, publish watermark, or cleanup completion.

### `ContextPackage`

The sole online ContextEngine output: an expiring, authorized, evidence-backed,
budget-bounded context package. 中文：ContextPackage 交付 context/security lineage，
不生成答案，也不授权写操作。

- **Owner/scope:** one Organization, ContextRun, Principal, purpose, audience,
  as-of time, and decision; contains only authorized Evidence.
- **Lifecycle:** self-contained and expiring; any later use is evaluated under
  current authority rather than inheriting authority from an older package.
- **Invariant:** required security fields and Evidence lineage cannot be
  removed; expired/delivered bytes are not reusable authorization.
- **Do not confuse with:** answer/model output, HTTP envelope, ContextRun,
  EgressGrant, or ticket.

### `ContextAccessTicket`

A signed, short-lived capability for source-read operations against one declared
ContextProvider audience. 中文：ContextAccessTicket 只委托受限 source read，不能执行
外部写 effect。

- **Owner/scope:** one Organization, authenticated identity chain, declared
  source-read audience, purpose, revocation freshness, and expiry.
- **Lifecycle:** minted after trusted authorization and validated on use;
  issuance/redemption audit may persist while the ticket is ephemeral.
- **Invariant:** valid only for its exact read audience/restrictions; Provider
  projection evidence still requires Kernel validation.
- **Activation note:** Issue #18 proves only one Organization/Provider-bound
  signed synthetic-read carrier, including cross-plane rejection and final V0
  Policy Epoch validation. Production ContextProvider discovery/projection,
  source credentials, and source ACL evidence remain `NOT_ACTIVE`.
- **Do not confuse with:** credential, ActionTicket, ContinuationToken,
  WorkerLease, or generic bearer token.

### `ActionTicket`

A signed, short-lived, one-shot capability for exactly one external effect.
中文：ActionTicket 绑定一个 write effect；创建占位、编辑和私聊发送使用不同票据。

- **Owner/scope:** one Organization, authenticated identity chain, and exactly
  one declared effect, destination, audience, and payload.
- **Lifecycle:** short-lived and consumed once; issuance, redemption, and replay
  handling belong to the owning design and ADRs.
- **Invariant:** ContextAccessTicket and ActionTicket have different audiences
  and are never interchangeable; rejected use has business effect zero.
- **Activation note:** Issue #18 proves only a distinct signed
  Organization/channel-bound synthetic no-op and zero-effect rejection. It does
  not activate the canonical durable one-shot lifecycle, ActionPlane
  prepare/perform, Sender/IM delivery, payload/destination/approval/idempotency,
  DeliveryAttempt, replay, stored receipt, or reconciliation semantics.
- **Do not confuse with:** read ticket, EgressGrant, WorkerLease, credential, or
  proof that an effect succeeded.

### `WorkerLease`

A server-minted, signed, short-lived, one-shot capability for one durable job
attempt. 中文：WorkerLease 把 worker 权限限制到指定 Organization、job、operation 和
registered service workload，并防 cross-job/cross-tenant replay。

- **Owner/scope:** one Organization, one durable job attempt, its declared work,
  and the registered service workload that may perform it.
- **Lifecycle:** short-lived and one-shot; mismatch, expiry, staleness, or replay
  makes it invalid. Exact claim/redemption fields belong to the owning ADR.
- **Invariant:** no general tenant/read/action authority and no long-lived source
  credential; rejected lease produces zero business effect.
- **Activation note:** Issue #17 binds a registered ServicePrincipal to
  `supply.noop` + `context-engine-worker` + `noop.complete`; this bounded carrier
  is not the canonical ServiceActor until source/allowed-operation set, Policy
  Epoch, and the remaining ActorContext fields exist.
- **Do not confuse with:** queue message, durable job, lock,
  ContextAccessTicket, ActionTicket, or ServicePrincipal.

### `File acquisition outcome`

The immutable result of one completed File-content observation. 中文：File
acquisition outcome 记录一次 File acquisition 是首次发布还是命中 unchanged
no-op；它只保留安全 lineage 和 digest，不保存 source content。

- **Owner/scope:** one Organization, ContextSource, acquisition, stable
  ContextResource, and active ContextRevision.
- **Lifecycle:** created atomically when unchanged classification completes; it
  is append-only per deduplicated acquisition. Initial publication retains its
  existing publication lineage instead.
- **Invariant:** it may explain deduplication but cannot authorize content,
  replace current policy, or create cross-Organization content identity.
- **Do not confuse with:** ContextRevision, publication event, index job,
  acquisition checkpoint, publish watermark, or WorkerLease completion.

### `File replacement plan`

The immutable durable ready boundary for one changed File Revision. 中文：File
replacement plan 把完整但尚未 active 的新版与旧 active Revision、job 和
acquisition 精确绑定。

- **Owner/scope:** one Organization, ContextSource, ContextResource,
  acquisition, and File import job.
- **Lifecycle:** created only after the replacement snapshot, Fragments,
  candidates, and `prepared -> indexed` evidence are complete; retained as
  activation lineage.
- **Invariant:** does not make content visible or authorize delivery; activation
  must still revalidate authority and compare-and-swap the exact previous
  Revision.
- **Do not confuse with:** active ContextRevision, publish watermark,
  acquisition checkpoint, or recovery lease.

### `File revision supersession`

The immutable old-to-new Revision edge recorded by successful File activation.
中文：File revision supersession 记录哪个旧 Revision 被哪个新版替代，并保留清理状态。

- **Owner/scope:** one Organization and ContextResource, with exact acquisition
  and File import job lineage.
- **Lifecycle:** created in the active-pointer transaction and retained with
  `retained_until_explicit_cleanup` until a future cleanup policy exists.
- **Invariant:** neither the retained old Revision nor this edge authorizes
  Runtime delivery; only the current Resource pointer plus exact authorization
  can make Evidence visible.
- **Do not confuse with:** physical deletion, tombstone, active pointer, or
  rollback/recovery command.

### `acquisition checkpoint`

The durable, opaque, monotonic signal recording which source changes have been
accepted. 中文：acquisition checkpoint 只表示变化已 durable 接收，不表示内容可见。

- **Owner/scope:** one Organization and ContextSource; Provider contracts may
  further bind SourceVersion/stream/partition.
- **Lifecycle:** advances only after durable acceptance; replay is idempotent and
  older input cannot move it backward.
- **Invariant:** may advance while compilation/publication is incomplete or
  failed; it is progress metadata, never authorization.
- **Do not confuse with:** publish watermark, active ContextRevision, worker
  acknowledgement, or Policy Epoch. `acquisition cursor` maps here.

### `publish watermark`

The durable, monotonic signal recording which accepted changes completed
Runtime-visible ContextRevision activation or tombstone publication.
中文：publish watermark 只在相应 visibility transaction commit 后推进。

- **Owner/scope:** one Organization and ContextSource, correlated to accepted
  change/publication lineage.
- **Lifecycle:** may lag acquisition checkpoint during failure and catch up after
  recovery without regression.
- **Invariant:** **acquisition checkpoint and publish watermark are separate
  progress signals.** Runtime never trusts either instead of current policy,
  source consistency, and visibility checks.
- **Do not confuse with:** acquisition checkpoint, prepared/indexed state,
  WorkerLease completion, active pointer, Policy Epoch, or cleanup completion.

## Alias policy

Normative contracts, schemas, tests, and security discussion use the canonical
English names above. These short forms are accepted only after the canonical
term is established locally:

| Short form | Canonical mapping |
|---|---|
| tenant / Org | `Organization`; never a separate object or caller authority |
| Source | `ContextSource` |
| Resource | `ContextResource` |
| Revision | `ContextRevision` |
| Fragment | `ContextFragment` |
| Package | `ContextPackage` |
| acquisition cursor | `acquisition checkpoint` |
| read ticket | `ContextAccessTicket` (explanatory prose only) |
| write/effect ticket | `ActionTicket` (explanatory prose only) |

Forbidden conflations:

- User, Membership, Principal, Agent, and ServicePrincipal are not interchangeable.
- ContextSource, ContextProvider, credential, and SourceVersion are not interchangeable.
- ContextResource, ContextRevision, ContextFragment, CandidateRef,
  AuthorizedProjection, and Evidence are not interchangeable.
- PolicySnapshot, policy version, Policy Epoch, acquisition checkpoint, and
  publish watermark are not interchangeable.
- ContextAccessTicket, ActionTicket, WorkerLease, ContinuationToken,
  CitationOpenRef, EgressGrant, and credential are not interchangeable.

## Related established terms

The following terms are included only to preserve distinctions used above. Their
protocols, interfaces, field sets, and release rules are owned by the
[`Implementation Design v1.2`](docs/design/2026-07-18-context-engine-implementation-design.md)
and accepted ADRs, not by this glossary.

- **DecisionAudit:** restricted security lineage for authorization decisions;
  it is not tenant-visible Learning content.
- **RuntimeCapability:** a server-owned closed designation of one Runtime
  operation or required adapter behavior. It is never caller-authored authority,
  and availability is checked before Provider, index, or source-content I/O.
- **UNSUPPORTED_CAPABILITY:** the restricted internal refusal category emitted
  when a declared RuntimeCapability has no active carrier. It is not a public
  response code and carries no token, locator, Provider, Source, or Resource
  detail.
- **CandidateRef:** an opaque, content-free retrieval candidate; it is neither a
  ContextFragment nor Evidence.
- **AuthorizedProjection:** content projected only after exact authorization for
  use inside Runtime.
- **AuthorizedModelInput:** generation input derived from an authorized,
  audience-bound ContextPackage.
- **CurationAnnotation:** a proposed governance annotation that may affect
  ranking or assembly but never authorization.
- **CurationSnapshot:** an immutable collection of accepted curation state,
  distinct from ContextRevision publication.
- **ReleaseManifest:** an immutable composition of release-version references;
  it is not a ContextRevision or authorization grant.
- **ActorContext:** the trusted designation of the actor responsible for a
  transaction.
- **ServicePrincipal:** a registered service identity; it is not a User,
  Principal, Agent, or WorkerLease.
- **EffectiveScope:** the intersection of all required trusted authorization
  constraints plus any optional request narrowing; it can only stay equal or
  become narrower.
- **SourceAclEvidence:** the source-native access evidence considered during
  exact authorization.
- **TrustedDeliveryContext:** trusted delivery facts supplied by an authenticated
  ingress, not caller-authored narrowing.
- **AudienceSnapshot:** trusted audience-membership facts used when evaluating a
  shared delivery audience.
- **DeliveryEvidenceRef:** an opaque locator for trusted delivery evidence; it
  is not an authorization grant.
- **ContinuationToken:** an expiring capability for continuing one authorized
  context interaction; it is not a ContextAccessTicket.
- **CitationOpenRef:** an opaque citation locator that carries no authorization.
- **EgressGrant:** authority for one declared egress hop; it carries no external
  write authority.
- **AuthorizationKernel:** the trusted authorization boundary that turns
  candidates into AuthorizedProjection values.
- **ContextProvider:** the adapter boundary to a ContextSource; it is distinct
  from that source, its credentials, and its SourceVersions.
- **BotDelivery:** the trusted delivery orchestrator outside ContextEngine; it
  is not an Agent or Principal.
- **ActionPlane:** the owner of authorized external effects; it is distinct from
  ContextEngine's context-delivery boundary.
- **PackageBudget:** the size/selection budget enforced for ContextPackage.
- **PromptBudget:** the caller's broader prompt allocation; it is not enforced
  by ContextEngine.
