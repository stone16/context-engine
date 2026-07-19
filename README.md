# ContextEngine

> A multi-tenant context delivery engine: connect your team's knowledge sources
> upstream, deliver **authorized, evidence-backed, budget-bounded**
> ContextPackages to agents and IM bots downstream.

多租户上下文交付引擎——上游连接团队知识源(飞书 / Slack / Google Docs /
企业微信),下游把「经过授权、带证据、有预算」的 ContextPackage 交付给 agent
应用与 IM bot(飞书群聊问答优先)。

**当前状态**:D0 设计闭环阶段(pre-M0),尚无可运行代码。整体计划见
[PLAN.md](./PLAN.md)。

本次公开候选 bundle 包含实现权威、ADR、安全契约、PRD、Tech Spec
与四个公开参考仓的证据基线；经维护者批准并提交后，它们将与实现一同
版本化。公开 prior art 仅限 Dify、RAGFlow、MaxKB、Onyx 的固定版本；
ContextEngine 的安全协议依据自身需求与威胁模型独立设计，零代码复制。

## 文档入口

- [Domain glossary](./CONTEXT.md)：身份、安全、内容与生命周期术语的仓库
  权威。
- [Architecture Decision Record index](./docs/decisions/README.md)：实现
  边界、依赖方向、禁止捷径与重访触发器。
- [Implementation Design v1.2](./docs/design/2026-07-18-context-engine-implementation-design.md)：
  集成后的实现权威与里程碑边界。
- [四个公开参考仓证据基线](./docs/research/2026-07-19-four-public-repositories-evidence.md)：
  四仓优势、局限、clean-room 拆解与证据缺口。
- [Threat Model](./docs/security/context-engine-threat-model.md)：自有资产、
  信任边界、威胁与 hard oracles。
- [Program PRD](./docs/agents/prd-contextengine-implementation.md) 与
  [Implementation Epic Tech Spec](./docs/specs/2026-07-19-context-engine-implementation-epic.md)：
  需求、100 条 user stories、contract shapes 与 work packages。
- [D0 Baseline Candidate](./DESIGN-BASELINE.md)：当前候选状态与尚未关闭的
  evidence gates。

当前只有固定 commit 的四仓静态证据与仓库内设计拆解；PostgreSQL RLS、
filtered ANN 和飞书 capability 的 disposable evidence spikes 尚未完成，
因此不声称已有动态可行性或产品能力验证。

## 为什么做这个

现有知识库产品回答的是「怎么存、怎么搜」;RAG 工具链回答的是「怎么找到最近的
chunk」。都没有回答两个更难的问题:

1. **这个 audience 此刻有权知道什么?** —— 索引只产生
   `CandidateRef`;sealed `ContextRuntime.resolve` 必须经
   `AuthorizationKernel` 执行 exact authorization 和字段投影,得到
   `AuthorizedProjection` 后,才能进入 Runtime 内的水合、精排、相关性模型和
   装箱。BotDelivery 的生成模型只接收由当前 audience-bound ContextPackage
   派生的 `AuthorizedModelInput`。Live/Mirrored/Weak 三类
   SourceAclEvidence 各有明确语义,Weak 绝不是强 ACL 故障时的 fallback。
2. **知识库由谁来组织?** —— Agent 承担可自动化的组织工作(语义去重、过期
   标记、术语沉淀),用户负责 audit;所有 AI 产物先提案、经确认、再以独立的
   不可变 `CurationSnapshot` 原子发布,绝不修改已发布的内容 Revision。

## 核心在线契约

`ContextRuntime.resolve(AuthenticatedInvocation, TrustedDeliveryContext,
Acquire | Continue | OpenCitation)` 是 Runtime 唯一公开能力,HTTP 是 V1 服务端
ingress,TypeScript SDK 是 generated HTTP client;MCP 只在真实 caller 出现后
激活。Continue 的 token 绑定 principal、one-shot 且累计预算;CitationOpenRef
本身不授权,每次打开都重新认证与授权。

IM 交付由受信 `BotDelivery` 深模块完成。它不在 wire body 自报 trusted
audience,而是通过认证 metadata 传递 opaque `DeliveryEvidenceRef`,由 ingress
兑换 `TrustedDeliveryContext` / `AudienceSnapshot`;群公开和提问者私有内容分别
resolve,外部效果均通过 `ActionPlane.prepare` + `perform`。

## 三条硬底线(release veto,不是分数)

- 无授权证据泄漏 = 0(Unauthorized Evidence = 0)
- 跨租户影响 = 0(wrong-Organization effect = 0)
- 缺失租户上下文一律 fail closed

任何功能收益不能抵消其中任何一条的失败。每次发布按版本化 catalog 报告
`PASS / FAIL / NOT_ACTIVE / NOT_APPLICABLE`,并把 capability coverage
单独列出;未激活能力不能冒充通过。

## License

TBD(设计阶段;在首个可运行版本前确定)。
