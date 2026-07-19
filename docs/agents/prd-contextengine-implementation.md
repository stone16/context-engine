## Problem Statement

ContextEngine 已经形成了较完整的产品定位、Domain Model、安全不变量和技术选型，但当前仍处于 D0 设计闭环阶段。仓库还没有可运行实现，若直接按旧的 feature 顺序进入编码，会把尚未关闭的边界问题固化为高成本返工：

- 设计权威、ADR、安全清单、术语与公开 roadmap 需要形成一个可由干净 clone 复现的版本基线。
- Runtime 必须保证 exact authorization 与字段投影发生在正文进入 reranker、模型、普通日志或 egress 之前，而不是只在装箱前做最终过滤。
- PostgreSQL、本地策略、Source-native ACL 与 Provider live check 之间需要明确、可声明、可测试的授权证据语义。
- BotDelivery 会处理明文 ContextPackage、模型调用和 IM 写入，因此必须被当作受控 egress 与 TCB，而不是普通 SDK caller。
- `ContinuationToken`、`CitationOpenRef`、`ContextAccessTicket` 与 `ActionTicket` 分别具有不同的授权能力、audience、重放和生命周期语义，不能继续共用含混的 continuation/action 抽象。
- 群聊公开 audience、成员快照、发送前 TOCTOU 与历史消息可见性需要独立闭环；提问者私有内容不能先取回再由 BotDelivery 二次切分。
- Supply、Runtime、Learning 与 Curation 的发布职责需要单一所有者。ContextLearning 的 `promote` 必须成为唯一 ReleaseManifest 激活/回滚入口，ContextControl 不再拥有 `publishProfile`。
- ContextRun、DecisionAudit 与 PackageRecord 的数据边界需要避免把 denied 或 cross-Organization 内容带入普通 Learning、模型日志或租户可见 trace。
- 旧 roadmap 把可靠性、wire、bot、retrieval、curation 和多个 connector 压在少数里程碑中，无法形成适合单人或小团队领取的 tracer-bullet 节奏。
- 现有安全与质量结论仍是设计承诺；只有经过真实 PostgreSQL、真实 wire contract、Provider contract 与 live conformance 后，才能成为可对外出示的产品能力。

从用户视角看，需要的是一条从版本化设计到私有部署的可信落地路径：先让一个 Markdown 通过 FileProvider、sealed Runtime 和 AuthorizationKernel 变成 authorized ContextPackage，再依次证明 wire/SDK、private BotDelivery、可靠检索、飞书上游、群聊 audience 和后续 connector。每个 milestone 都应独立可演示、可验收，并且任何质量收益都不能抵消租户隔离、授权、撤权或 egress 失败。

## Solution

把 ContextEngine 收口为一个版本化、可验证、按纵向切片推进的多租户 context 交付系统：

1. 先完成 D0 设计闭环，修订并版本化设计权威、ADR、安全模型、术语与 roadmap，关闭 implementation-blocking 冲突并完成关键 spike。
2. 以 `ContextPackage` 作为引擎唯一 online deliverable；引擎不生成最终答案，也不执行写操作。
3. 将 production `ContextRuntime.resolve(Acquire | Continue | OpenCitation)` 固定为 sealed 编排，任何 variant 都必须经过不可插拔的 AuthorizationKernel、PackageBudget、provenance 与 audit gates。
4. 通过 `AuthenticatedInvocation + TrustedDeliveryContext → CandidateRef → AuthorizedProjection → Evidence → ContextPackage` 建立唯一安全数据流；未授权正文不得进入内容型 rerank、模型、普通 ContextRun 或 egress。
5. 使用 PostgreSQL 17 作为租户归属与本地授权状态的权威，并显式区分 live、mirrored-as-of 和 weak 三类 Source ACL 证据。
6. 将 Continue 与 OpenCitation 建模为 `resolve` 的不同 request variant：Continue 使用 principal-bound、one-shot、累计预算的 ContinuationToken；OpenCitation 使用不携带授权能力、可多次兑换但每次重新认证和授权的 CitationOpenRef。
7. 将 BotDelivery 明确为引擎之外的受信交付编排 deep Module；trusted identity adapter 为每次 resolve 签发 opaque DeliveryEvidenceRef，由 ingress 兑换 TrustedDeliveryContext/AudienceSnapshot，群 audience 的权限交集仍由 Kernel 计算。BotDelivery、ModelGateway、ActionPlane 与 Sender 共同构成 delivery TCB，受控明文网络端点只有 ModelGateway 与 Sender。
8. 将公开群 Package 和提问者私有 Package 作为两次独立、audience-bound resolve；无法证明完整 audience 时只允许私有路径。
9. 使用 immutable Revision、双水位、transactional outbox、原子 active pointer 和 crash-safe publication 实现 Supply；内容、ACL 与同步运行元数据使用不同变化域。
10. 以 PostgreSQL FTS + pgvector + deterministic fusion 建立检索基线，在真实 RLS 高选择性过滤下验证 exact-vs-ANN recall，再按 ablation 证据决定是否启用 reranker。
11. 由 ContextLearning 产出候选 profile、评测报告和可签名 ReleaseManifest，并通过唯一的 `promote` 入口完成激活或回滚；该入口必须校验 release operator 授权，ContextControl 不拥有第二个发布入口。
12. 将 Curation 作为并行 C1 实验，通过独立 immutable CurationSnapshot 生效，不修改已 active Revision，也不阻塞飞书主路径。
13. 采用 D0、M0–M7 与并行 C1 roadmap：安全骨架 → File authorized Package → wire/SDK/private BotDelivery → File 可靠性与检索评测 → 飞书上游私聊闭环 → 群聊与 private-cell launch readiness → Slack → Google；WeCom 先进入 P3 feasibility，不预先承诺交付 milestone。
14. 每个 milestone 均以外部可观察行为、安全不变量、真实数据库测试、contract conformance 和 release report 作为退出条件，而不是以内部 class 或 feature 数量判断完成。

### Public provenance and design ownership

- 公开 prior art 只允许引用四个固定版本的公开仓：Dify、RAGFlow、MaxKB、Onyx；证据索引统一见 `docs/research/2026-07-19-four-public-repositories-evidence.md`。
- 四仓只提供可观察的产品流程、Interface 形状、测试 oracle 与工程 pattern。ContextEngine 的 tenant model、AuthorizationKernel、SourceAclEvidence、WorkerLease、audience-bound delivery、ActionTicket、publication transaction 与 release authority 均是本项目根据自身威胁模型独立设计。
- 仓库之外的笔记、私有实现、本机路径或未署名综合结论不构成实现权威。任何探索性想法必须先改写成仓库内可独立审查的 requirement、threat、decision 或 evidence-gated hypothesis，才能进入 Design、ADR、PRD 或 Tech Spec。
- 不复制参考仓代码。公开设计不得通过删去来源名称来保留不可验证归因，也不得把 ContextEngine 自有协议描述成参考仓已经证明的能力。

### Evidence and no-mock policy

- `specified` 只代表行为已经定义；它不代表实现存在。
- deterministic fixture、fake、property test 与 provider twin 可以穿过同一个 Module Interface 验证 domain behavior、失败映射和 contract conformance，但不能支撑 sandbox/live capability claim。
- PostgreSQL 隔离、认证 wire metadata、source-native ACL、model egress 与外部 effect 必须分别由真实 PostgreSQL、真实 ingress、真实 source sandbox/live path 和真实依赖证明。
- 禁止用虚构 Provider、伪造 API response、虚构身份/ACL 或模拟 effect 充当产品证据。未取证、样本不足或结果矛盾时只能标记为 `INCONCLUSIVE` 或 `NOT_ACTIVE`。

## User Stories

1. As a system owner, I want the authoritative design, ADRs, glossary, security model, and roadmap versioned together, so that every implementation can be traced to one reproducible baseline.
2. As a system owner, I want implementation-blocking design conflicts closed before M0, so that engineering does not silently invent security behavior.
3. As a maintainer, I want every non-obvious architectural change to refine or supersede an ADR explicitly, so that accepted decisions cannot drift through prose edits.
4. As a maintainer, I want every milestone to deliver a narrow end-to-end behavior, so that progress is independently demoable and reversible.
5. As a maintainer, I want experimental Curation work separated from the launch critical path, so that uncertain research cannot delay the core product.
6. As a tenant administrator, I want every tenant-owned object to belong explicitly to one Organization, so that cross-Organization ownership is structurally impossible.
7. As a tenant administrator, I want missing Organization context to fail closed, so that configuration mistakes never fall back to a default or public tenant.
8. As a tenant administrator, I want Membership, Principal grants, Agent ceilings, Resource policy, Source ACL, purpose, and request narrowing intersected on every delivery, so that no individual input can expand access.
9. As a tenant administrator, I want a source’s authorization evidence mode declared as live, mirrored-as-of, or weak, so that I know what freshness and revocation guarantee applies.
10. As a tenant administrator, I want weak ACL semantics used only for sources that genuinely lack stronger ACLs, so that an outage cannot silently weaken a document source’s policy.
11. As a tenant administrator, I want sensitive content from a weak-ACL source denied when classification or membership evidence is missing, so that uncertainty always narrows access.
12. As a tenant administrator, I want an observed permission change to invalidate later resolve, Continue, and OpenCitation operations, so that stale caches and indexes cannot preserve authority.
13. As a tenant administrator, I want Package metadata to disclose `asOf`, TTL, trust, and authorization freshness, so that consumers can evaluate whether a Package meets policy.
14. As a tenant administrator, I want source offboarding to disable acquisition, revoke credentials, invalidate logical access, and schedule cleanup, so that removing a source has complete semantics.
15. As a security auditor, I want a schema security manifest classifying every table as global or tenant-owned, so that a tenant table cannot evade review by accidentally omitting Organization ownership.
16. As a security auditor, I want every tenant table protected by composite ownership constraints, FORCE RLS, and a non-owner runtime role, so that application mistakes do not become cross-tenant reads.
17. As a security auditor, I want tenant database context scoped to one transaction, so that connection pooling cannot leak a previous Organization into a later request.
18. As a security auditor, I want Unauthorized Evidence, wrong-Organization effect, and missing-context fallback to remain release vetoes, so that quality gains cannot compensate for a security failure.
19. As a security auditor, I want denied and not-found outcomes to be externally equivalent, so that callers cannot enumerate protected Resources.
20. As a security auditor, I want release reports tied to immutable code, schema, Provider, model, index, and dataset versions, so that every claim is reproducible.
21. As a security auditor, I want invariant status reported only as PASS, FAIL, NOT_ACTIVE, or NOT_APPLICABLE, so that unimplemented capabilities cannot be described as successful security tests.
22. As a security auditor, I want capability coverage reported separately from invariant outcomes, so that I can distinguish unsupported behavior from tested active behavior.
23. As an agent developer, I want one stable `resolve(Acquire | Continue | OpenCitation)` entry point, so that I do not compose security-sensitive retrieval stages myself.
24. As an agent developer, I want ContextPackage to be self-contained, authorized, cited, budgeted, and time-bounded, so that I can consume it without engine internals.
25. As an agent developer, I want the engine to stop at ContextPackage, so that answer generation, tone, and action policy remain in my Agent Runtime.
26. As an agent developer, I want a TypeScript SDK generated from the HTTP contract, so that client types cannot drift from the deployed API.
27. As an agent developer, I want a Continue success to return a complete replacement Package, so that I never perform a security-sensitive delta merge.
28. As an agent developer, I want continuation scope and cumulative budget to be non-increasing, so that repeated Continue cannot amplify authority or cost.
29. As an agent developer, I want expired, revoked, replayed, or principal-mismatched continuations rejected before Provider or index work, so that they cannot become enumeration or resource-exhaustion tools.
30. As an agent developer, I want CitationOpenRef to carry no authorization authority, so that sharing a citation does not share the original reader’s permissions.
31. As an agent developer, I want every OpenCitation to authenticate the current opener and run exact authorization, so that a reusable locator cannot bypass revocation.
32. As an agent developer, I want PackageBudget separated from my PromptBudget, so that the engine can enforce its ceiling while I reserve space for system prompt, history, and answer generation.
33. As an agent developer, I want Package budget to cover tokens, Provider/model calls, cost, and wall time, so that satisfying one limit cannot exhaust another resource.
34. As a principal, I want only exact-authorized, selected Fragments called Evidence, so that provenance has a precise security meaning.
35. As a principal, I want citations to identify their Resource, Revision, as-of time, and decision reference, so that I can inspect why content was included.
36. As a principal, I want gaps and partial coverage represented explicitly, so that missing context is not presented as complete knowledge.
37. As a principal, I want revoked or expired OpenCitation to return a non-enumerating result, so that losing access does not reveal protected object existence.
38. As a principal, I want callers to discard a Package after its TTL, so that an old decision does not become indefinite authority.
39. As a vault owner, I want one Markdown file to become an authorized ContextPackage end to end, so that the earliest implementation proves the real product boundary.
40. As a vault owner, I want Markdown parsed with structural awareness, so that headings, tables, and argument boundaries survive retrieval.
41. As a vault owner, I want Resource, immutable Revision, and Fragment modeled separately, so that content identity, history, and retrieval units remain stable.
42. As a vault owner, I want index units separated from answer units, so that precise matching can hydrate a coherent passage without mixing revisions.
43. As a vault owner, I want a content edit to create a new immutable Revision, so that readers see either the complete old state or complete new state.
44. As a vault owner, I want changes classified as content, policy, or operational sync state, so that generic metadata updates cannot mutate immutable content invisibly.
45. As a vault owner, I want deletion represented as a tombstone, so that deleted content becomes unavailable before physical cleanup completes.
46. As a vault owner, I want hash-based incremental acquisition and separate acquisition/publish watermarks, so that accepted and query-visible changes are not confused.
47. As a vault owner, I want a crash at every publication stage to leave either the complete old Revision or complete new Revision visible, so that queries never observe mixed state.
48. As an operator, I want accepted asynchronous work persisted through a transactional outbox and durable job table, so that process restarts do not lose changes.
49. As an operator, I want job idempotency scoped by Organization and source operation, so that retries cannot duplicate work or cross tenants.
50. As an operator, I want publication identifiers and read fences, so that a lagging future index may miss temporarily but cannot hydrate stale unauthorized content.
51. As a search user, I want lexical and vector retrieval fused deterministically, so that exact terminology and semantic similarity both contribute to recall.
52. As a search user, I want Chinese tokenization and a versioned termbase evaluated on real queries, so that Chinese references and domain terms remain retrievable.
53. As a search user, I want CandidateRef authorized before any content-bearing reranker or model call, so that final non-selection is not mistaken for non-disclosure.
54. As a search user, I want every parent or neighboring Fragment expansion re-authorized, so that small-to-big hydration cannot cross an ACL boundary.
55. As a search user, I want reranking enabled only after a frozen ablation proves slice-level benefit, so that complexity is evidence-driven.
56. As a search user, I want highly selective RLS filters benchmarked against exact vector results, so that approximate index underfill is detected.
57. As an evaluation owner, I want a frozen golden set with positive, negative, reference-resolution, granularity, redundancy, and security slices, so that an aggregate score cannot hide a failed scenario.
58. As an evaluation owner, I want per-slice minimums and uncertainty reported, so that a small pilot is not presented as statistically conclusive.
59. As an evaluation owner, I want every retrieval-sensitive change compared against an immutable baseline, so that score changes are attributable.
60. As an evaluation owner, I want Security, Reliability, Quality, and Budget reported as independent gates, so that no blended score hides a veto.
61. As a Learning operator, I want ContextRun recorded from day one without raw unauthorized content, so that Learning has useful evidence without becoming a disclosure channel.
62. As a Learning operator, I want tenant-visible run records limited to authorized Evidence and authorized candidate metadata, so that denied and cross-Organization identifiers or bytes never enter ordinary analytics.
63. As a security investigator, I want restricted DecisionAudit records to retain safe reason categories and digests, so that incidents can be investigated without exposing protected content to Learning consumers.
64. As a Learning operator, I want candidate profiles and evaluation reports produced before promotion, so that feedback cannot mutate production directly.
65. As a release operator, I want `ContextLearning.promote` to be the only ReleaseManifest activation and rollback path, so that production publication authority has one owner.
66. As a release operator, I want `ContextLearning.promote` to verify my authorization and the candidate’s gate results, so that model or user feedback cannot self-promote.
67. As a release operator, I want ContextControl to manage control-plane configuration without a `publishProfile` operation, so that no second production publication path exists.
68. As a release operator, I want ReleaseManifest to compose immutable ContentProfile, IndexProfile, RuntimeProfile, and optional CurationProfile references, so that a release and rollback identify exactly what changed.
69. As a curation reviewer, I want CurationAnnotations to enter proposed state and require audit, so that model-generated metadata cannot silently affect production ranking.
70. As a curation reviewer, I want approved annotations assembled into an immutable CurationSnapshot whose activation is independent of Revision publication but owned by ContextLearning.promote, so that curation can evolve without mutating content history or creating a second release path.
71. As a product owner, I want Curation evaluated in parallel with an on/off experiment, so that it earns a place in positioning without blocking Feishu delivery.
72. As a connector developer, I want one ContextProvider capability contract with typed unsupported behavior, so that every source declares what it can actually prove.
73. As a connector developer, I want FileProvider to establish the first shared behavior runner and FeishuProvider to validate the second implementation, so that abstractions emerge from real uses.
74. As a connector developer, I want every production Provider paired with a deterministic twin and shared contract suite, so that retries, checkpoints, ACL failures, and deletion semantics can be tested without live APIs.
75. As a connector developer, I want live conformance in a controlled source tenant, so that platform documentation claims are verified against actual behavior.
76. As a Feishu administrator, I want Docs and Wiki object permissions mapped and checked, so that mirrored content follows source access.
77. As a Feishu administrator, I want Base field-level ACL exposed only when the active product edition and API capability are proven, so that unsupported controls are never advertised.
78. As a Feishu administrator, I want a capability matrix covering token mode, permission objects, collaborator expansion, events, rate limits, and edition, so that deployment behavior is explicit.
79. As a Feishu administrator, I want a strong-ACL Resource excluded or live-checked when ACL evidence is unavailable, so that operational failure cannot downgrade it to weak ACL.
80. As a Slack administrator, I want Slack delivered as its own post-launch milestone with sandbox and live conformance, so that its permission model is not hidden inside another connector’s gate.
81. As a Google Workspace administrator, I want Google Docs/Drive delivered after Slack with explicit delegation and per-user ACL evidence, so that its identity model is proven independently.
82. As a WeCom stakeholder, I want archive access, drive ACL, event, regional, retention, and cost feasibility investigated before a delivery promise, so that roadmap commitments are evidence-based.
83. As an IM user, I want an unbound platform identity or forged/replayed DeliveryEvidenceRef rejected before retrieval, so that platform identifiers and caller-authored audience claims cannot impersonate a Membership.
84. As an IM user, I want a private bot answer generated only from my authorized File or Feishu ContextPackage, so that BotDelivery cannot expand authority through its ServicePrincipal.
85. As a group member, I want public replies authorized for the asker and every current audience member, so that a privileged asker cannot leak content to the group.
86. As a group member, I want an unresolved, hidden, external, unbound, or stale audience member to force private-only delivery, so that audience uncertainty fails closed.
87. As an asker, I want content outside the group intersection offered through a separately authorized private response, so that useful private context is not leaked publicly.
88. As an asker, I want public and private responses produced by separate audience-bound resolves, so that BotDelivery never partitions one over-privileged Package.
89. As an IM user, I want group membership revalidated near send time, so that membership changes between resolution and delivery are not ignored.
90. As an IM user, I want historical-message visibility and deletion/redaction behavior declared by deployment policy, so that revocation is not misrepresented as recalling delivered bytes.
91. As an IM user, I want message creation and message editing authorized by distinct operation-specific tickets, so that one ticket cannot be replayed for another side effect.
92. As a security auditor, I want ContextAccessTicket, ContinuationToken, CitationOpenRef, and ActionTicket to use distinct wire variants and validators, so that they cannot be confused or exchanged.
93. As an ActionPlane operator, I want each write ticket single-use, audience-bound, effect-bound, and linked to one delivery attempt, so that sends and edits are independently auditable.
94. As a platform operator, I want secrets stored outside code and rotated through a documented procedure, so that connector credentials do not become repository state.
95. As a platform operator, I want backup, point-in-time recovery, and restore drills completed before private-cell launch, so that durability is demonstrated rather than assumed.
96. As a platform operator, I want forward migration, rollback, and compatibility tested against packaged artifacts, so that schema evolution does not break workers or callers.
97. As a platform operator, I want metrics and alerts for ingestion lag, publish lag, authorization failures, Provider errors, queue age, and Runtime latency, so that operational failure is diagnosable.
98. As a platform operator, I want audit retention and redaction policy fixed before production data arrives, so that observability does not accumulate uncontrolled sensitive data.
99. As a platform operator, I want a private-cell deployment runbook and explicit recovery objectives before invited use, so that the first external deployment can be operated responsibly.
100. As a design partner, I want a release report, capability declaration, and known-limitations statement, so that I can evaluate security and operational fit before relying on the system.

## Implementation Decisions

1. **Design authority and governance**
   - D0 creates one versioned implementation baseline containing the canonical design, glossary, accepted ADRs, security invariants, and public roadmap.
   - Exploratory research may remain outside the repository, but it is never normative. The implementation repository pins a self-contained immutable baseline with enough behavioral contract and public provenance for a clean clone to build and verify the system.
   - Existing decisions that conflict with this PRD are explicitly refined or superseded; prose does not silently override an accepted ADR.
   - This PRD is the parent program item; implementation work is decomposed into dependency-ordered tracer-bullet issues.
   - D0 permits isolated disposable evidence spikes, but no spike code becomes a production runtime foundation; production milestone implementation starts only after the baseline closes.

2. **Product boundary**
   - ContextPackage remains the engine’s only online deliverable.
   - Final answer generation, planning, and write tools remain in upper Agent Runtime applications.
   - Evidence retains its narrow meaning: an exact-authorized Fragment selected for one ContextRun.
   - Already delivered bytes cannot be recalled. Revocation governs later engine operations, cache reuse, Continue, OpenCitation, and egress compensation supported by the target platform.

3. **Module and process topology**
   - The engine is a modular monolith with one API process and one independent worker process sharing one domain package.
   - M2 activates one additional trusted Bot application process containing BotDelivery and ActionPlane. It is the justified network caller boundary: BotDelivery consumes Runtime only through the generated HTTP SDK, while BotDelivery and ActionPlane may share delivery contracts but cannot import engine internals.
   - The three engine Modules remain ContextControl, ContextRuntime, and ContextLearning; trusted BotDelivery and ActionPlane bring the system total to five deep Modules.
   - ContextRuntime and ContextLearning remain in-process engine Modules; the Supply pipeline, Curation, and AuthorizationKernel stay internal behind the five system Interfaces rather than becoming new network services.
   - No further process or microservice split occurs until an observed performance or isolation requirement justifies another network trust boundary.

4. **Sealed Runtime and AuthorizationKernel**
   - Production `ContextRuntime.resolve(AuthenticatedInvocation, TrustedDeliveryContext, Acquire | Continue | OpenCitation)` is sealed and has no feature flag, alternate composition, no-op security dependency, or retriever-to-assembler bypass.
   - AuthorizationKernel, PackageBudget, provenance, and audit are mandatory gates for all three variants.
   - `AuthorizationKernel.authorizeAndProject` is the single semantic boundary that converts opaque CandidateRef into AuthorizedProjection.
   - Inside Runtime, only AuthorizedProjection may enter content-bearing reranking, deduplication, relevance-model calls, assembly, or ordinary ContextRun payloads.
   - BotDelivery constructs AuthorizedModelInput only from one current audience-bound ContextPackage and a matching EgressGrant; the generation ModelGateway accepts no raw candidate or Provider content.
   - Parent, neighbor, and Resource expansion returns to the same authorization boundary; authorization is not inherited solely from a child Fragment.

5. **Authenticated input and audience context**
   - Runtime consumes AuthenticatedInvocation plus TrustedDeliveryContext.
   - Organization, Principal, Membership, AgentVersion, and audience facts are derived from verified identity/platform material; purpose is selected by authenticated route/application policy and bound into trusted delivery evidence. None is asserted by BotDelivery or the request body.
   - A remote BotDelivery sends only an opaque DeliveryEvidenceRef in authenticated transport metadata. The reference is bound to its service identity, resolve request id, Organization, asker, destination, purpose, audience digest, and expiry; ingress redeems it into TrustedDeliveryContext.
   - Public and private resolves use separate DeliveryEvidenceRef values; raw trusted identity and audience fields never appear in the request body.
   - RequestNarrowing remains caller-controlled and may only reduce authority. Its absence means no additional narrowing, not missing trusted authorization evidence.
   - AudienceSnapshot is a trusted fact input; AuthorizationKernel, not BotDelivery, computes group EffectiveScope.

6. **EffectiveScope**
   - EffectiveScope is the intersection of seven required trusted constraints—Organization boundary, Membership rights, Principal grants, Agent delegation ceiling, Source-native ACL, Resource ACL, and purpose policy—plus RequestNarrowing when supplied.
   - Any missing required trusted component produces an empty scope or typed generic denial; omitted RequestNarrowing is the identity element relative to that established scope.
   - Index filters, caches, model scores, and Provider discovery results are candidate optimizations, never authorization authorities.

7. **Authorization evidence modes**
   - Source ACL behavior is declared as one of three modes:
     - **live**: authorization is checked against the source during the same operation;
     - **mirrored-as-of**: a versioned SourceAclProjection is exact relative to a declared source snapshot and exposes `aclAsOf` plus its freshness contract;
     - **weak**: a documented substitute such as conversation membership is used with bounded freshness and sensitive-default-deny.
   - A strong-ACL source that cannot produce its required evidence fails closed, uses an explicitly supported live check, or excludes the Resource. It never falls back to weak ACL because of an outage.
   - Provider capability declarations state which evidence modes and Resource types they support.

8. **PostgreSQL authorization truth**
   - PostgreSQL 17 stores Organization ownership, Membership, local policy, Resource policy, Policy Epoch, SourceAclProjection, and tenant-owned operational state.
   - Every schema object is classified as global or tenant-owned in a security manifest.
   - Every tenant-owned table has explicit Organization ownership, required composite references, FORCE RLS, and a real-database negative test.
   - API and worker use non-owner, non-BYPASSRLS runtime roles; migration ownership remains separate.
   - Tenant and ActorContext are transaction-local for every request and job; online work uses UserActor, while workers use a registered least-privilege ServiceActor.
   - Every WorkerLease binds Organization, job, operation, source, optional resource/revision, ServiceActor/workload, policy epoch, optional audience, idempotency key, lease generation, issued-at, expiry, and nonce; redemption checks every claim against the durable job row.
   - A worker never impersonates the triggering user, and service ingestion authority never substitutes for end-user delivery authorization.
   - Externally exposed database errors are normalized to avoid enumeration.

9. **Revocation semantics**
   - Policy Epoch invalidates later resolve, Continue, and OpenCitation operations after the relevant permission change reaches trusted engine authorization state.
   - Upstream changes not yet observed are bounded by the declared live or mirrored freshness contract; the product does not claim impossible instantaneous upstream revocation.
   - Index, cache, and stored-byte cleanup remains asynchronous after logical invalidation.
   - Policy Epoch granularity may begin broadly and become narrower only after measurement demonstrates the need.

10. **Supply object and change model**
    - Supply uses Resource, immutable Revision, and Fragment.
    - Content and canonical representation changes create a new Revision.
    - ACL changes create a new policy/ACL projection and bump the relevant Policy Epoch without mutating content history.
    - Sync cursors, retry state, and observed timestamps remain operational metadata outside immutable Revision.
    - Index units may point to larger answer units, but both belong to the same Revision.
    - Content deletion, access revocation, and source offboarding retain distinct transitions.

11. **Publication and background work**
    - Acquisition cursor and publish watermark remain separate.
    - Accepted changes are persisted before background execution through a transactional outbox and durable job table.
    - Jobs use Organization-scoped idempotency and a mandatory server-minted, signed WorkerLease with the exact claim binding above; crossing a new network trust boundary must preserve the same claims and durable-row verification rather than inventing a weaker lease.
    - Publication uses staged build/publication identifiers and an atomic active pointer.
    - Queries may temporarily miss a lagging result but must never hydrate a non-active or unauthorized Revision.
    - Fault injection at every publication transition proves old-complete-or-new-complete visibility.

12. **ContextProvider seam**
    - ContextProvider remains the sole Source seam and advertises a machine-verifiable capability declaration.
    - The V1 Interface has exactly four read-only operations: `describeCapabilities`, `readChanges`, `discover`, and `authorizeAndProject`.
    - `discover` returns content-free CandidateRef values. `authorizeAndProject` returns SourceProjectionBatch evidence for Kernel validation; only AuthorizationKernel constructs AuthorizedProjection.
    - Every operation returns a closed ProviderOutcome: Ok, typed Unsupported, RetryableUnavailable, InvalidCheckpoint, or GenericDenied; failure never masquerades as an empty success.
    - CapabilityDeclaration versions Resource kinds, ACL mode, projection, cursor/checkpoint, deletion, batch, freshness, and consistency semantics; change cursor and publish watermark remain distinct.
    - CandidatePage and SourceProjectionBatch share a SourceConsistencyRef; Kernel rejects missing, mixed, changed, or stale provider/snapshot evidence.
    - FileProvider uses a versioned local FileSourceAccess projection as Mirrored SourceAclEvidence and does not claim host operating-system ACL behavior; missing, incomplete, unknown, or inactive grants strictly deny, with no implicit owner or public fallback.
    - Unsupported capability is a typed result, not implicit empty success.
    - FileProvider establishes the first shared behavior runner. The contract suite is frozen before FeishuProvider, the second implementation, becomes active.
    - Every production Provider has a deterministic twin; live conformance supplements but does not replace twin and contract tests.

13. **Runtime request union**
    - Runtime exposes one `resolve` operation with closed Acquire, Continue, and OpenCitation variants.
    - Acquire's body accepts only ContextNeed, optional RequestNarrowing, and bounded caller PackageBudget. Purpose is a trusted ingress/DeliveryEvidenceRef fact and cannot be supplied or overridden in the body.
    - Continue accepts only a server-authored ContinuationToken and an optional budget no greater than the remaining server-authorized ceiling.
    - OpenCitation's body accepts a CitationOpenRef; ingress supplies current authenticated invocation and redeems any remote DeliveryEvidenceRef, while the locator itself grants no access.
    - Runtime does not expose caller-authored relation, target, Organization, Principal, or scope fields for continuation or citation opening.

14. **Distinct capability types**
    - ContextAccessTicket authorizes context reads.
    - ContinuationToken is principal-bound, short-lived, one-shot, scope-non-increasing, and cumulatively budgeted.
    - CitationOpenRef is a non-authorizing opaque locator that may be used repeatedly; each OpenCitation independently authenticates and exact-authorizes the opener.
    - ActionTicket authorizes one write effect for one audience and delivery attempt.
    - The four types use separate wire variants, validators, audiences, audit events, and replay rules.

15. **ContextPackage**
    - ContextPackage contains only authorized Evidence plus citations, purpose, TTL, as-of data, freshness/trust declarations, budget accounting, gaps, and decision reference.
    - Continuation offers are emitted only for currently authorized targets and never disclose denied candidate counts.
    - CitationOpenRef contains a safe locator, not a source credential or reusable authorization decision.
    - Package serialization is canonical across active server transports.

16. **Retrieval**
    - V1 uses PostgreSQL FTS, pgvector, and deterministic reciprocal-rank or weighted fusion.
    - Chinese tokenization is implemented inside the PostgreSQL retrieval implementation and evaluated on the real File corpus; the internal candidate-injection seam creates no public Index portability contract.
    - Authorization and field projection happen before any content-bearing external or model reranker.
    - Approximate vector retrieval is benchmarked against exact results under selective Organization and Membership filters.
    - Oversampling, iterative scans, or partitioning are adopted only when measurement demonstrates recall loss.
    - Rerank remains disabled until a frozen ablation demonstrates slice-level improvement without new security, latency, or budget failures.

17. **Budget**
    - Runtime enforces the minimum of its server ceiling and the caller’s requested PackageBudget.
    - PackageBudget remains independent of the upper Agent Runtime’s PromptBudget.
    - Budget accounting covers delivered tokens, Provider/model calls, cost, and wall time.
    - Tokenizer identity is versioned with RuntimeProfile so replay and evaluation remain comparable.

18. **ContextRun, PackageRecord, and DecisionAudit**
    - ContextRun records query, authorized candidate references/digests, selected Evidence, PackageRecord, feedback, and version lineage from day one.
    - Denied or cross-Organization identifiers and bytes never enter ordinary tenant-visible ContextRun or Learning data.
    - PackageRecord stores a digest, authorized Evidence references, and reconstruction metadata by default; complete payload retention requires an explicit policy decision.
    - Restricted DecisionAudit stores safe reason categories and digests needed for incident analysis.
    - PII redaction, model-provider payload logging, and retention are fixed before M1 accepts persistent user data.

19. **Learning and ReleaseManifest authority**
    - ContextLearning evaluates ContextRuns and creates candidate profiles, evaluation reports, and candidate ReleaseManifests.
    - `ContextLearning.promote` is the only operation allowed to activate or roll back an active ReleaseManifest.
    - `promote` validates the authenticated release operator, candidate lineage, compatibility, Security/Reliability/Quality/Budget gates, and audit reason.
    - ContextControl has no `publishProfile` operation and cannot mutate active release state through another path.
    - ReleaseManifest composes immutable ContentProfile, IndexProfile, RuntimeProfile, and optional CurationProfile references; CurationProfile names an optional CurationSnapshotRef, compatible Revision set, and evaluation digest, so the manifest selects one snapshot or curation-off.
    - A rollback is represented as a new audited promotion to a previously compatible manifest, not mutation of history.
    - The initial empty ReleaseManifest is promoted through the same authorized path; migration or bootstrap code has no direct active-pointer write.

20. **Curation**
    - Curation remains a long-term differentiator but runs as parallel C1 after the M3 retrieval/eval foundation is available.
    - CurationAnnotation begins proposed and requires audit.
    - Audited annotations are assembled into an immutable CurationSnapshot with compatibility references to content Revisions.
    - Its active selection is independent of content Revision publication but can change only through `ContextLearning.promote` as part of ReleaseManifest activation.
    - Runtime reads the active Revision and the compatible CurationSnapshot selected by the active ReleaseManifest without mutating either.
    - Initial experiments cover deduplication, deprecation, and tag/termbase assistance; relationship inference remains deferred.
    - Curation promotion uses ContextLearning.promote through ReleaseManifest and does not block M4 or M5.

21. **BotDelivery and egress**
    - BotDelivery is outside the engine but is an egress-facing trusted deep Module because it processes cleartext ContextPackage, invokes a generator, and performs IM delivery.
    - A trusted identity adapter authenticates IM identities and supplies evidence for TrustedDeliveryContext/AudienceSnapshot; BotDelivery passes only DeliveryEvidenceRef through the generated HTTP client and invokes ActionPlane.prepare/perform.
    - AuthorizationKernel, not BotDelivery or the identity adapter, computes effective group scope from those audience facts.
    - It cannot import or invoke engine internals.
    - Generator and Sender adapters are the only authorized cleartext egress points and enforce provider, region, retention, and audience policy.
    - Public group and private asker packages come from separate audience-bound resolves.
    - Unknown, unbound, hidden, external, or stale audience members force private-only delivery.
    - Audience membership is revalidated near send time.
    - Historical message visibility is an egress retention concern; supported deletion/redaction is compensation, not an engine revocation guarantee.

22. **ActionPlane**
    - ActionPlane is an explicit dependency with one policy and audit owner.
    - `prepare(TrustedEffectIntent)` returns Prepared(ticket), GenericDenied, AudienceChanged, or RetryableUnavailable(effect zero); `perform(EffectPayload, ActionTicket)` returns Applied, AlreadyApplied, Rejected(effect zero), or ReconciliationRequired.
    - Replaying an applied ticket returns its stored receipt. An ambiguous provider attempt is reconciled under the same idempotency identity and is never retried with a new ticket.
    - ContextRuntime does not execute or route writes; unsupported transactional requests receive a typed read-only rejection.
    - Message creation and message editing use distinct single-use ActionTickets linked to one delivery attempt.
    - Wrong-audience, wrong-effect, expired, or replayed tickets produce zero business effect.

23. **Transport and SDK**
    - HTTP is the initial server ingress, and OpenAPI is its wire authority.
    - The TypeScript SDK is a generated HTTP client artifact, not a separate server transport.
    - MCP remains NOT_ACTIVE until a real caller requires it and must then demonstrate canonical parity with HTTP.
    - IM remains an application integration through BotDelivery, not an engine transport.

24. **Feishu**
    - Feishu capability discovery is completed in D0 using an owned sandbox and recorded as an executable capability matrix.
    - M2 may use Feishu only as a private downstream BotDelivery channel while all context still comes from FileProvider.
    - M4 adds Feishu as an upstream ContextProvider for the private loop, starting with Docs/Wiki object ACL.
    - Base advanced or field ACL is an optional capability gated by product edition, token mode, and live conformance.
    - User-token and tenant-token behavior, collaborator expansion, events, and rate limits are modeled explicitly.
    - If required ACL evidence is unavailable, affected Resources fail closed or are excluded.

25. **Group and launch readiness**
    - Group-chat delivery is intentionally deferred to M5, after the Feishu upstream/private loop is stable.
    - Kernel computes asker scope intersected with the full trusted audience snapshot.
    - Public and private content are resolved independently; BotDelivery cannot split one broad Package.
    - M5 also closes secret rotation, backup/PITR, restore drill, migrations/rollback, audit retention, observability, incident runbook, and private-cell deployment readiness.
    - M5 exits at Engineering Gate E5 when Security/Reliability/Quality/Budget reports and Ops readiness pass; it does not require a partner to exist first.
    - Invited use begins only at the subsequent Launch Gate L1, after a real partner agreement, legal review, naming, and commercial approval.

26. **Subsequent connectors and WeCom**
    - M6 implements Slack as one independently gated connector with twin, sandbox, contract suite, and live conformance.
    - M7 implements Google Docs/Drive after Slack, with explicit domain-wide delegation and per-user OAuth authorization evidence.
    - Each milestone introduces at most one new permission model.
    - WeCom remains P3 feasibility covering archive eligibility, drive ACL, change events, regional/retention policy, and cost.
    - A WeCom delivery milestone is created only after feasibility produces sufficient evidence; personal WeChat remains out of scope.

27. **Operational readiness**
    - Private-cell deployment precedes public SaaS.
    - Secrets, backups, restore, migrations, audit retention, observability, release artifacts, recovery objectives, and incident response are demonstrated before M5 Engineering Gate E5.
    - SLOs cover resolve latency, publish lag, Provider errors, queue age, and declared revocation freshness without embedding environment-specific endpoints or credentials in prose.
    - Public SaaS remains gated by demonstrated Kernel security, retrieval quality, operational readiness, legal review, and actual partner demand.

28. **Roadmap**
    - **D0 — Design closure and evidence spikes:** version authority; close Runtime type flow, ACL evidence, BotDelivery/egress, token/ticket, release authority, revocation, and historical-message decisions; complete Feishu, transaction-local RLS, and filtered-ANN spikes.
    - **M0 — Secure engineering skeleton:** real PostgreSQL roles/RLS/security manifest, two-Organization fixture, sealed Runtime/Kernel gates, DecisionAudit, executable invariant catalog, and honest invariant/capability reporting.
    - **M1 — File → authorized Package tracer bullet:** one Markdown crosses FileProvider, Revision/Fragment, lexical FTS, CandidateRef, AuthorizationKernel, AuthorizedProjection, PackageBudget, minimal HTTP Acquire, authorized-only ContextRun, and ContextPackage.
    - M1's HTTP endpoint is provisional and internal: it proves resolve semantics plus one-fixture revoke/tombstone/retry/active-flip behavior without a public compatibility promise.
    - **M2 — Wire/SDK + private BotDelivery PoC on File:** freeze OpenAPI v0 and its breaking-change gate, generated TypeScript SDK, private identity binding, TrustedDeliveryContext, OpenCitation, answer generation, ActionPlane.prepare/perform, and private IM delivery using File context only.
    - **M3 — File reliability + Retrieval/Eval:** production-hardening corpus and fault matrix, checkpoint/lease/dead-letter/runbooks, hybrid retrieval, Chinese tokenizer, hydration, budget, Continue, a preregistered slice-coverage/uncertainty sample plan, exact-vs-ANN gate, and evidence-gated rerank.
    - **C1 — Parallel Curation experiment:** CurationSnapshot, audited annotations, and on/off hypothesis testing; no dependency from M4/M5.
    - **M4 — Feishu upstream/private loop:** Feishu Docs/Wiki ingestion and ACL evidence plus private BotDelivery end to end; no group launch yet.
    - **M5 — Group + private-cell engineering readiness:** audience intersection, dual resolve, send-time recheck, historical-message policy, operational readiness, and Engineering Gate E5; invited use remains the separate L1 business gate.
    - **M6 — Slack:** one connector milestone with full contract and live conformance.
    - **M7 — Google Docs/Drive:** one connector milestone with delegation and ACL conformance.
    - **P3 — WeCom feasibility:** evidence gathering only until an independent delivery milestone is justified.

## Testing Decisions

1. **Primary highest seam**
   - The primary product seam is externally visible `ContextRuntime.resolve(Acquire | Continue | OpenCitation)` over HTTP.
   - Tests assert ContextPackage, PackageRecord/DecisionAudit, Provider/model payloads, and observable side effects; they do not assert internal class calls or incidental pipeline structure.
   - Supply publication, retrieval, authorization, continuation, citation opening, and revocation are proven through this seam wherever practical.

2. **Necessary secondary seams**
   - ContextProvider contract tests remain because external source behavior cannot be diagnosed solely through Runtime.
   - ActionPlane.prepare/perform and Sender contract tests remain because writes are deliberately outside ContextRuntime.
   - `ContextLearning.promote` is tested as the sole ReleaseManifest activation/rollback seam.
   - ContextControl behavior tests prove that it cannot publish or mutate active release state.
   - No lower seam is introduced merely to make an implementation detail easier to assert.

3. **Security test architecture**
   - Every active security invariant has three forms: domain property oracle, real PostgreSQL enforcement test, and Runtime negative test.
   - All database security tests use real PostgreSQL 17 with pgvector, non-owner runtime roles, and FORCE RLS.
   - In-memory databases and twins may accelerate non-security feedback but never satisfy tenant-isolation or RLS gates.
   - Generated adversarial fixtures include at least two Organizations, multiple Memberships, cross-Organization IDs, revoked grants, stale epochs, malicious retrieval candidates, and missing tenant context.

4. **Invariant status and capability coverage**
   - Applicability is preregistered in a versioned catalog as required, conditional with `applicableFrom`, or NOT_APPLICABLE with an approved rationale; it cannot be chosen after seeing a run.
   - Every invariant result is exactly one of:
     - **PASS:** the active behavior was exercised and satisfied the invariant;
     - **FAIL:** the exercised behavior violated the invariant;
     - **NOT_ACTIVE:** the relevant capability is not active in this release, and its boundary is proven to deny or remain unreachable;
     - **NOT_APPLICABLE:** the invariant is structurally irrelevant to this release, with a recorded rationale.
   - `NOT_ACTIVE` and `NOT_APPLICABLE` are never counted or displayed as PASS.
   - An active but unexecuted, missing, or unmapped invariant is FAIL; every required milestone entry must be PASS.
   - Capability coverage is tracked separately as unavailable, implemented, contract-verified, sandbox-verified, or live-verified.
   - Milestone exits declare both required invariant statuses and required capability coverage; an inactive feature cannot make a release appear more secure by reducing exercised surface.

5. **Schema tests**
   - CI checks that every table appears in the schema security manifest.
   - Tenant tables must have Organization ownership, required composite relationships, RLS policy, FORCE RLS, and a negative cross-tenant test.
   - Tests cover transaction-local Organization+ActorContext, connection reuse, migration-owner separation, normalized constraint errors, ServiceActor non-impersonation, and per-claim WorkerLease mutation against the durable job row.

6. **M0 secure skeleton tests**
   - Synthetic Provider and internal candidate-injection fixtures return allowed, denied, cross-Organization, stale, and malformed CandidateRefs through the sealed Runtime path.
   - Tests prove there is no alternate production composition that reaches Package assembly without Kernel, budget, provenance, and audit.
   - Invariant catalog generation fails when an invariant lacks an explicit status or evidence reference.
   - An authorized release operator promotes the initial empty ReleaseManifest through ContextLearning.promote; direct bootstrap/migration pointer writes and unauthorized promotion fail.
   - Security claims for future File, citation, bot, group, and connector capabilities remain NOT_ACTIVE until their real paths are exercised.

7. **M1 File tracer-bullet tests**
   - A real temporary Markdown file is ingested and queried through minimal HTTP Acquire.
   - Allowed, denied, cross-Organization, missing-context, and no-match requests assert ContextPackage, authorized-only ContextRun, and DecisionAudit behavior.
   - The test observes external Resource/Revision/Fragment and Package behavior without coupling to parser or repository implementation details.
   - One minimal fixture each proves revoke, tombstone, retry, and active-pointer crash semantics; M3 owns the exhaustive production fault matrix.

8. **Supply and reliability tests**
   - File behavior is tested from a real temporary corpus through active ContextPackage output.
   - Property and fault-injection tests cover create, update, rename, delete, revoke, offboard, duplicate events, replay, and every publication crash point.
   - Queries observe either complete old Revision or complete new Revision.
   - Worker retries prove idempotency, cursor monotonicity, and zero wrong-Organization effect.

9. **Provider tests**
   - The same contract suite runs against each deterministic twin and production Provider.
   - Contracts cover capability declaration, checkpoints, deletion, ACL evidence mode, freshness, collaborator expansion, typed unsupported behavior, outage behavior, and non-enumeration.
   - Live conformance runs separately in controlled accounts and records platform edition, token mode, and verified capabilities.
   - External transient retries retain the first failure; deterministic failures are never auto-rerun into green.

10. **Runtime union and capability tests**
   - Malicious retrieval injection may return denied and cross-Organization CandidateRefs; no unauthorized bytes may reach Runtime hydration/rerank/assembly, AuthorizedModelInput, ContextPackage, or ordinary ContextRun.
    - Continue tests cover one-shot redemption, replay, expiry, principal mismatch, Policy Epoch mismatch, cumulative budget, and complete replacement Package.
    - OpenCitation tests cover different openers, repeated authorized opens, revoked access, hidden Resources, and locator tampering.
   - Action tests cover read/write ticket confusion, all closed prepare/perform outcomes, wrong channel/effect/payload/audience, create/edit separation, stored-receipt replay, ambiguous-attempt reconciliation under the original id, and zero extra business effect.

11. **Wire and SDK tests**
    - OpenAPI schema validates the closed Acquire/Continue/OpenCitation union and distinct capability wire variants.
    - Acquire rejects caller-authored purpose along with Organization, Principal, Membership, audience, ACL, and bypass fields; ingress supplies purpose only from authenticated application policy or redeemed DeliveryEvidenceRef.
    - The generated TypeScript SDK builds and packages from the checked contract without handwritten drift.
    - BotDelivery is exercised only through the generated client; tests fail if it imports engine internals.
    - MCP remains capability-unavailable and its related invariants NOT_ACTIVE until a real caller activates it.

12. **BotDelivery and egress tests**
    - M2 private tests traverse identity binding, DeliveryEvidenceRef redemption, TrustedDeliveryContext, SDK resolve on File content, generator policy, ActionTicket, ActionPlane.prepare/perform, and message delivery.
    - Egress assertions inspect generator and Sender payloads, not merely final generated text.
    - M5 group tests cover mixed permissions, unknown/hidden members, membership changes between resolution and send, public/private dual resolve, and no denied-count leakage.
    - Historical message deletion/redaction is tested only where the platform declares the capability; otherwise the release report states the limitation.

13. **Retrieval and evaluation tests**
    - M1 begins with a small pilot set sufficient to validate the File tracer bullet.
    - M3 freezes a sample plan before execution, sized from failure-slice coverage and a declared uncertainty/power target; negative cases cover every active refusal/security category, and an underpowered set is labeled pilot or inconclusive.
    - PostgreSQL exact vector search is the recall reference for approximate search under selective Organization and Membership filters.
    - Evaluation compares lexical, dense, and hybrid configurations on identical authorized corpora and immutable profiles.
    - Reranker and Curation experiments use explicit on/off ablations and cannot promote when any security slice regresses.
    - Small samples are labeled pilots; broader claims include confidence or equivalent uncertainty reporting.

14. **Learning and promotion tests**
    - ContextLearning creates candidate profiles and reports without altering active state.
    - Only an authenticated, authorized release operator may call `promote` successfully.
    - Failed security, reliability, quality, budget, compatibility, or lineage gates prevent activation with no active-manifest change.
    - ContextControl attempts to publish or mutate active manifests are structurally unavailable and covered by architecture and behavior tests.
    - Rollback creates an audited promotion event to a compatible historical manifest.

15. **Release and operational tests**
    - Every commit runs fast properties, real PostgreSQL security integration, Module behavior, and active contract suites.
    - Retrieval-sensitive changes run the frozen evaluation plus the full active security family.
    - Nightly runs fault injection, live Provider conformance, timing non-enumeration, and larger-corpus performance tests.
    - Release candidates test packaged artifacts, clean migrations, rollback compatibility, backup restore, non-owner roles, HTTP/SDK, BotDelivery, and active connector paths.
    - Release reports bind raw result digests to exact code, schema, Provider, model, index, configuration, and dataset versions.

16. **Prior art**
    - The repository begins without a runnable implementation or executable test suite.
    - Initial prior art is the accepted security invariant catalog, test architecture, glossary, and ADR behavior, not reusable test code.
    - M0 establishes the first executable harness and becomes the prior art for subsequent slices.
    - External reference repositories may inform clean-room patterns only; no code is copied from them.

## Out of Scope

- Final answer generation inside ContextEngine.
- General-purpose planning, agent orchestration, or arbitrary tool execution inside the engine.
- General business writes beyond the minimum ActionPlane IM-delivery integration required to prove read/write separation.
- Recalling or erasing cleartext already seen by a human or retained by an external IM platform; only future access and supported compensating actions are governed.
- Public multi-tenant SaaS before security, quality, operational, and legal gates are demonstrated.
- Additional microservices, multi-region active-active deployment, or distributed consensus in V1.
- A dedicated vector database or external search engine before real corpus measurements violate the PostgreSQL baseline.
- Streaming partial ContextPackage content in V1.
- Personal WeChat or unofficial WeChat protocols.
- A committed WeCom delivery milestone before P3 feasibility is complete.
- Cross-Organization Learning or raw cross-tenant Learning artifacts.
- Any second ReleaseManifest publication entry point in ContextControl or another Module.
- Autonomous promotion of Learning or Curation results without release-operator authorization.
- Curation relationship inference such as `supersedes`, `contradicts`, or general knowledge-graph construction in C1.
- A universal promise of Feishu Base field-level ACL where product edition or API evidence cannot prove it.
- Group-chat launch before M5.
- Simultaneous implementation of Slack and Google Docs/Drive in one milestone.
- A general UI or administration console before the core API, BotDelivery, and operational workflows stabilize.
- Premature BlobStore, queue, or external index portability abstractions without demonstrated need.
- Public procurement or quality claims based only on the initial small golden set.

## Further Notes

### Delivery sequence

| Phase | Demonstrable outcome | Required gate |
|---|---|---|
| D0 | A clean implementation agent can recover one coherent architecture and reproduce the Feishu, RLS, and filtered-ANN evidence decisions | No open implementation-blocking security contradiction |
| M0 | A synthetic adversarial request crosses real PostgreSQL isolation and the sealed Runtime/Kernel gates with honest invariant reporting | Three hard oracles pass; schema manifest complete; future capabilities remain NOT_ACTIVE |
| M1 | One Markdown becomes an atomically identifiable, authorized ContextPackage through minimal HTTP Acquire | Allowed/denied/cross-Organization and authorized-only ContextRun behavior pass |
| M2 | A private user receives an answer through generated SDK, BotDelivery, OpenCitation, and ActionPlane using File content | Wire contract, identity, egress payload, citation, and write-ticket tests pass |
| M3 | File synchronization is crash-safe and a Chinese query returns a cited hybrid Package with bounded Continue | Preregistered slice/uncertainty plan, exact-vs-ANN, reliability, budget, and rerank activation gates pass |
| C1 | Audited Curation signals can be enabled or disabled through ReleaseManifest without modifying Revision | Preregistered per-kind coverage/uncertainty sample plan and ablation report; underpowered results remain inconclusive; no M4/M5 dependency |
| M4 | Feishu Docs/Wiki act as an upstream source and private BotDelivery completes the loop | Provider twin, sandbox, ACL evidence, live conformance, and private egress pass |
| M5 | Group and private flows run from an engineering-ready private cell | Audience intersection, dual resolve, send-time recheck, four engineering reports, and Ops readiness pass E5; partner approval remains L1 |
| M6 | Slack works through the established Provider contract | No source-specific Kernel bypass; contract and live conformance pass |
| M7 | Google Docs/Drive works through the same contract with explicit delegation semantics | Delegation, ACL evidence, contract, and live conformance pass |
| P3 | WeCom feasibility evidence supports or rejects a future milestone | Archive/ACL/event/region/retention/cost report; no delivery promise implied |

### Open evidence gates

- Feishu permission APIs, collaborator expansion, token modes, events, rate limits, and edition-specific Base controls require an executable sandbox capability report before related capabilities become active.
- Filtered pgvector approximate behavior must be measured with selective Organization and Membership filters rather than inferred from unfiltered benchmarks.
- Policy Epoch granularity, complete Package payload retention, and historical-message compensation remain evidence-gated; defaults minimize retained data and fail closed.
- Slack and Google remain separate milestones even when a shared contract kit exists.
- WeCom remains feasibility-only until official access, compliance, technical, regional, and cost evidence justifies a delivery milestone.

### Product opening points

- Internal engine use begins after M1.
- Private BotDelivery use on File content begins after M2.
- Internal Feishu upstream/private-loop use begins after M4.
- Invited team/design-partner use begins only after M5 Engineering Gate E5 and the separate partner/legal/naming/commercial Launch Gate L1 both pass.
- Public SaaS has no fixed milestone in this PRD; it requires demonstrated Kernel security, quality stability, operational readiness, legal review, and a justified tenancy/deployment model.

### Issue-tracker readiness

This PRD is the parent program item. Child implementation issues should be tracer-bullet vertical slices, published in dependency order, and labeled `ready-for-agent` only when their inputs, externally observable behavior, acceptance criteria, and blockers are independently verifiable. Human evidence gathering or product-choice work should remain separate from AFK-agent implementation work.
