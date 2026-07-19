# CONTEXT.md — ContextEngine 术语表

> 本文件只是 glossary，不是 spec。实现细节以
> `docs/design/2026-07-18-context-engine-implementation-design.md` 与 accepted ADR
> 为准。公开参考事实须回引
> `docs/research/2026-07-19-four-public-repositories-evidence.md` 所固定的一手来源；
> 仓库外研究可以作为独立思考输入，但不是公开 authority 或 provenance。

## 核心对象

- **ContextPackage**:引擎唯一的 online 交付物——经过授权、带证据、有预算的上下文包(citations、purpose、TTL、asOf、decisionRef)。引擎边界止于此,不产出答案。
- **Evidence**:窄定义——仅指某次 ContextRun 中通过 exact authorization 且被选中的 Fragment。长期存储对象只能叫 ContextFragment,不得叫 Evidence。
- **ContextRun**:一次 resolve 的 authorized-only 可学习记录(Package digest、已授权 Evidence refs、性能/预算、feedback)。不存未授权正文或可枚举的 denied candidate。
- **DecisionAudit**:受限安全审计记录,保存授权/拒绝的原因类别、摘要与必需身份链;不是租户可见的 Learning 语料,也不保存 denied 正文。
- **Resource / Revision / Fragment**:Supply 三层——外部内容源对象 → 不可变内容版本(原子发布,查询永不见混血)→ 索引/交付单元。
- **CandidateRef**:召回阶段的不透明候选引用;不是 Evidence,不携带可向 reranker/模型/调用方暴露的正文。
- **AuthorizedProjection**:`AuthorizationKernel` 对 CandidateRef 执行 exact authorization 与字段投影后的 Runtime 内部唯一内容承载类型;水合、精排、相关性模型与 assembler 只接收此类型。
- **AuthorizedModelInput**:BotDelivery 只能从一个当前、audience-bound ContextPackage 及其 purpose/retention policy 派生的生成输入;ModelGateway 不接收 CandidateRef、SourceProjectionBatch、源裸内容或 denied data。
- **CurationAnnotation**:Curation Agent 产出的带证据标注(去重簇/过期标记/tag-termbase),以 proposed 状态入库,人工 audit 后编入 CurationSnapshot;只影响 ranking/assembly,不触碰授权。
- **CurationSnapshot**:独立于内容 Revision 的不可变治理发布物,引用兼容的 content revisions。active selection 与 Revision 解耦,但只能由 ContextLearning.promote 随 ReleaseManifest 切换;curation pipeline 无直接激活权。
- **ReleaseManifest**:将 ContentProfile / IndexProfile / RuntimeProfile / CurationProfile 等不可变版本引用组合为一次可评测发布;CurationProfile 显式携带可选 CurationSnapshotRef、兼容 Revision 集与评测摘要,因此 manifest 唯一选择某个 snapshot 或 curation-off。Learning 产出 candidate 与评测报告;经 release-operator 权限校验的 ContextLearning.promote 是唯一激活/回滚入口。
- **CrossOrgLearningArtifact**:跨租户学习的 revisit-only 概念标签。V1 是 non-goal,不创建该对象的 schema、interface 或可执行路径;未来重访必须先有 opt-in、聚合、匿名化与 raw refs=0 的新 ADR。

## 身份与授权

- **Organization**:安全根。所有租户拥有的行/块/索引/任务/trace 显式归属。
- **ActorContext / ServicePrincipal**:事务 actor 为 UserActor 或 ServiceActor。在线请求使用认证 Principal/Membership;后台 worker 使用注册且最小权限的 ServicePrincipal,绑定 workload、Organization、job/lease、source/operation、epoch 与 expiry,不得伪装触发用户,也不替代最终用户授权。
- **WorkerLease**:server-minted 且签名,绑定 Organization、job、operation、source、可选 resource/revision、ServiceActor/workload、policy epoch、可选 audience、idempotency、lease generation、iat/exp 与 nonce;兑换时逐项核对 durable job row,变异、过期、旧 generation 或 replay 均拒绝。
- **EffectiveScope**:七项必需 trusted 授权约束(OrgBoundary ∩ MembershipRights ∩ PrincipalGrants ∩ AgentCeiling ∩ SourceNativeACL ∩ ResourceACL ∩ PurposePolicy),再与可选 RequestNarrowing 取交集。任一必需 trusted 项缺失 fail closed;RequestNarrowing 缺省表示「不额外收窄」,绝不扩大已建立 scope。
- **Policy Epoch**:引擎观测到权限变化后的本地失效机制。它使新 resolve、Continue 和 OpenCitation 不再复用旧决策,但不能召回已交付给 IM/模型的字节。
- **SourceAclEvidence**:某次 exact authorization 所使用的源权限证据。`Live`=同请求源生检查;`Mirrored`=版本化本地投影+明确 `aclAsOf`/freshness;`Weak`=源天生无细粒度 ACL 时的受限语义。Weak 永不是 Live/Mirrored 读取失败时的 fallback。
- **TrustedDeliveryContext**:受信入口构造的 nominal 交付事实(channel、conversation、purpose、audience binding、freshness),不属于 caller 可随意提交的 RequestNarrowing。
- **AudienceSnapshot**:签名/受信的群成员事实快照;由 identity adapter 解析,由 AuthorizationKernel 计算群交集。任一成员未绑定、无法枚举或快照过期时,公开回答 fail closed。
- **DeliveryEvidenceRef**:远程 BotDelivery 到 Runtime ingress 的 opaque 证据引用,绑定 authenticated service、resolve request id、Organization、asker、destination、purpose、audience digest 与 expiry;通过认证 transport metadata 传递,不携带 raw audience claims,由 ingress 兑换 TrustedDeliveryContext。
- **ContextAccessTicket / ActionTicket**:读票据 / 外部效果票据,同一 identity chain、不同 audience、不可互换。ActionTicket 与单一 effect 绑定且 one-shot;创建占位、编辑、发私聊分别使用不同票据。
- **ContinuationToken / CitationOpenRef**:Continue 使用 principal-bound、one-shot、累计预算的 token;CitationOpenRef 是可多次使用但不授权的 opaque locator,每次 OpenCitation 都重新认证与授权。
- **群聊交集授权**:群公开 Package 按受信 AudienceSnapshot 中全体可见人的权限交集生成;提问者私有 Package 必须另行 resolve,不得从公开或提问者全量 Package 二次分割。

## 模块与面

- **ContextControl / ContextRuntime / ContextLearning**:三个 engine Module。Runtime 收口为 sealed `resolve(AuthenticatedInvocation, TrustedDeliveryContext, Acquire | Continue | OpenCitation)`,固定经过 AuthorizationKernel、PackageBudget、provenance 与 audit gates;Control 只管 source/access/policy;Learning 提供 evaluate/promote,通过唯一的 promote 入口激活或回滚 ReleaseManifest。加上外部受信 BotDelivery 与 ActionPlane,系统共有五个 deep Module。
- **AuthorizationKernel**:不可插拔的 deep Module,固定执行 identity / Organization / policy / SourceAclEvidence / exact authorization / field projection 并记录 DecisionAudit;生产路径无 no-op 或 bypass 构造。
- **BotDelivery**:引擎之外的受信交付编排 deep Module——向 trusted identity adapter 请求 DeliveryEvidenceRef,消费 Package,经 ModelGateway 生成,再通过 ActionPlane 交付。BotDelivery、ModelGateway、ActionPlane 与 Sender 都在 delivery TCB 内;受控明文网络端点是 ModelGateway 与 Sender。BotDelivery 不自行计算群 scope,也不是 transport。
- **ActionPlane**:所有外部副作用的唯一所有者;prepare 返回 Prepared/GenericDenied/AudienceChanged/RetryableUnavailable closed outcome,perform 返回 Applied/AlreadyApplied/Rejected(effect=0)/ReconciliationRequired。每个 effect 独立签发并校验 org/audience/payload digest/expiry/idempotency;已成功 replay 只回存量 receipt,外部结果不确定时用同一 attempt 对账,不得换新 ticket 重放。
- **ContextProvider**:唯一 Source seam,V1 只有四个 typed 只读操作(describeCapabilities/readChanges/discover/authorizeAndProject),统一返回 closed ProviderOutcome。discover 的 CandidatePage 与 authorizeAndProject 的 SourceProjectionBatch 共享 SourceConsistencyRef(provider/SourceVersion/ACL mode/decision-or-snapshot/asOf),Kernel 拒绝缺失、混合、变化或过期引用;只有 Kernel 能构造 AuthorizedProjection。FileProvider 使用显式、版本化且 active 的 FileSourceAccess 作为 Mirrored ACL,不声称继承宿主 OS 文件权限;grant 缺失、不完整或未知时严格 deny,不推断 owner/public。
- **Server ingress / generated client**:HTTP 是 V1 server ingress;MCP 是只在真实 caller 出现后激活的可选 server ingress;TypeScript SDK 是 OpenAPI 生成的 HTTP client artifact,不是 transport。所有入口参数均不可直接伪造 AuthenticatedInvocation 或 TrustedDeliveryContext。
- **Kernel vs Seam**:AuthorizationKernel 不可插拔;parser/embedding/connector/HTTP 与激活后的 MCP adapter 可插拔。V1 retrieval 固定 PostgreSQL FTS + pgvector,只有内部候选注入 test seam;第二个真实 backend 出现前不抽外部 Index portability contract。
- **PackageBudget / PromptBudget**:ContextEngine 只强制 PackageBudget(服务端 ceiling 与 caller request 取小);system prompt、历史与答案预留属于上层 PromptBudget。

## 节奏与门

- **Engineering Gate E5 / Launch Gate L1**:M5 以 Security/Reliability/Quality/Budget 四份报告 + Ops readiness 判工程完成,不依赖先有 partner。其后 L1 需明确的 design-partner agreement、legal review、命名与 commercial approval 才开放受邀使用;公开 SaaS 另行裁决。
- **并行 C1**:M3 后启动的 CurationSnapshot 实验轨,不是 M4/M5 或 design partner 的前置条件。
- **Connector 节奏**:M6 Slack、M7 Google Docs/Drive,每个里程碑只引入一个新权限模型;企微先 P3 feasibility,通过后再独立排期。
- **三硬 oracle**:Unauthorized Evidence = 0、wrong-Organization effect = 0、missing-context fallback = 0。veto,不与功能分互换。
- **Invariant catalog**:版本化预注册 applicability/applicableFrom 与 required gate;capability activation/coverage 独立报告。展示态仅 PASS/FAIL/NOT_ACTIVE/NOT_APPLICABLE;active 但未执行或未映射 = FAIL,required exit 只能由 PASS 满足。
- **切片门禁**:golden set 按失败模式切片(指称/不对称/粒度/冗余/负例/安全),任一切片跌破阈值不放行。
- **双水位**:acquisition cursor(变化已 durable 接收)≠ publish watermark(Revision 已 active)。
