# ContextEngine — 整体计划

> **Status**: D0 design closure (pre-M0) · **Updated**: 2026-07-18
>
> *English abstract*: ContextEngine is a multi-tenant context delivery engine. Upstream connectors ingest team knowledge (Feishu/Lark, Slack, Google Docs, WeCom); downstream it delivers authorized, evidence-backed, budget-bounded ContextPackages to agent applications and IM bots. Its differentiation is not another retrieval algorithm but the combination of permission-aware retrieval, revocable governance, agent-driven curation, and first-class support for the Chinese team-tooling ecosystem.

---

## 1. 愿景与定位

**一句话**:为团队工具栈提供权限感知、可撤销、可审计的 context 交付。

- **上游**:知识源 connector——File/笔记库起步,飞书(Docs/Wiki/Base)、Slack、Google Docs、企业微信分期接入。
- **下游**:统一的 `ContextRuntime.resolve(AuthenticatedInvocation, TrustedDeliveryContext, Acquire | Continue | OpenCitation)` 首先经 HTTP 交付 ContextPackage;TypeScript SDK 是由 OpenAPI 生成的 HTTP client,不是服务端 transport。MCP 在真实 caller 出现前保持 NOT_ACTIVE。其上的 **BotDelivery** 深模块编排受信身份/audience 证据、生成与 IM egress,但不计算授权 scope。
- **差异化**:
  1. **权限感知 + 可撤销治理三合一**——源 ACL 证据、逐条 provenance、受控的续取/引用重开。撤权在权威源或已声明 freshness 边界观测到变化后,对新 resolve 生效;已交付字节另由 egress 保留/撤回策略治理。
  2. **Agent-driven curation**——语义去重、过期标记、术语沉淀由 Agent 提案、人工确认、原子发布,把知识库最大的隐性成本(组织成本)从用户身上拿走。
  3. **中文与中国 IM 生态一等公民**——中文分词/术语库进检索内核,飞书/企微上下游打通。

**发行节奏**:先完成 M5 Engineering Gate E5(Security / Reliability / Quality / Budget 四份报告 + Ops readiness),再经过独立 Launch Gate L1(明确的 design-partner agreement、legal、命名与 commercial approval)开放受邀使用;公开服务另行裁决。

## 2. 设计原则(不可谈判)

1. **安全 Runtime 不可绕过**:`ContextRuntime.resolve(Acquire | Continue | OpenCitation)` 是 sealed 编排,固定经过 `AuthorizationKernel`、PackageBudget、provenance 与 audit gates;生产 composition root 不能替换、跳过或装配 no-op 实现。
2. **安全是 veto,不是分数**:三条硬底线(README)独立于任何功能评分,失败即不发布。
3. **授权先于任何承载内容的相关性处理**:索引只返回 `CandidateRef`;Runtime 内必须经 `AuthorizationKernel` 产生 `AuthorizedProjection`,才能进入水合、精排、相关性模型或 assembler。BotDelivery 的生成模型只接收由当前 audience-bound ContextPackage 派生的 `AuthorizedModelInput`。父子/邻居扩展逐项重新授权。
4. **ACL 证据分类,弱 ACL 绝不降级回退**:`Live` 是同请求的源生 subject-object check;`Mirrored` 是版本化本地投影且显式声明 `aclAsOf`/freshness;`Weak` 仅用于源本身没有细粒度 ACL 的场景。Live/Mirrored 故障必须 fail closed,不得改用 Weak。
5. **发布原子性**:内容以不可变 Revision 发布;治理结果组装为独立的不可变 `CurationSnapshot`。它的 active selection 与内容 Revision 解耦,但只能随 release-operator 授权的 `ContextLearning.promote` 激活,不修改已 active 的 Revision。
6. **Learning 有治理且发布权单一**:Learning 生成 candidate 与评测报告;只有经 release-operator 权限校验的 ContextLearning.promote 可激活或回滚 ReleaseManifest。ContextControl 不发布 profile,任何反馈/AI 产物不直改生产。
7. **完成判定不数 feature**:每个里程碑以安全不变量 + 垂直切片通过为完成标志。

## 3. 架构总览

### 3.1 三循环

| 循环 | 职责 | 关键对象 |
|---|---|---|
| **Supply** | 源 → 可信候选:采集、解析、切分、索引、原子发布 | Source / Resource / Revision / Fragment |
| **Runtime** | 认证调用 → ContextPackage:候选、授权投影、相关性、装箱 | CandidateRef / AuthorizedProjection / ContextRun / ContextPackage |
| **Learning** | authorized-only trace → 可发布的改进:评测集、切片门禁、版本化 profile | golden set / ReleaseManifest / CurationSnapshot |

### 3.2 Runtime 唯一公开入口与检索管线

```
ContextRuntime.resolve(AuthenticatedInvocation, TrustedDeliveryContext,
                       Acquire | Continue | OpenCitation)
  → 查询理解 + 双路召回(FTS + vector,RRF 融合)
  → CandidateRef(不携带可交付正文)
  → AuthorizationKernel(精确授权 + 字段投影 + 决策审计)
  → AuthorizedProjection
  → 授权后水合/精排 + 需逐项重授权的 small-to-big 扩展
  → PackageBudget 装箱 + sufficiency 信号
  → ContextPackage(citations / purpose / TTL / asOf,全带证据)
```

`Continue` 使用 principal-bound、one-shot 且累计预算的 ContinuationToken,返回 replacement Package。`OpenCitation` 使用不携带授权能力的 opaque `CitationOpenRef`;每次打开都重新认证当前 principal 并执行 exact authorization。

### 3.3 Curation Agent(C1 并行实验)

后台判断链:邻居圈定(引擎自检索)→ 廉价预过滤 → LLM 判断(带引用义务)→ 确定性证据校验(引用必须真实存在,反幻觉门)→ proposed 标注 → 人工 audit → 不可变 `CurationSnapshot` 原子发布。

- 首期三件套:**语义去重**(重复簇折叠)、**过期标记**(旧结论降权并提示)、**tag/术语沉淀**(反哺中文分词)。
- 标注只影响 ranking/assembly,永不触碰授权层。
- 质量门:每种 annotation kind 在实验前预注册样本量、误标阈值与不确定性口径;样本不足只能判 inconclusive,不得事后选阈值。

### 3.4 BotDelivery(引擎之外的受信交付深模块)

- M2 起作为独立 Bot application process 部署,与 ActionPlane 同驻,并只通过 generated HTTP SDK 调用 `resolve()`;回答生成和 IM 交付在 BotDelivery 内,引擎边界止于 ContextPackage。BotDelivery 处理明文 Package,因此是 TCB 和受控 egress,不是普通 caller。
- trusted Identity Adapter 解析 IM 身份与群成员事实,为每次 public/private resolve 分别签发或持久化 opaque `DeliveryEvidenceRef`;BotDelivery 只通过认证 transport metadata 传这个引用,由 Runtime ingress 兑换为 `TrustedDeliveryContext` / `AudienceSnapshot`。群 audience 的权限交集由 AuthorizationKernel 计算。未绑定、成员无法枚举或 snapshot 超出 freshness 时,公开回答 fail closed,只可尝试提问者私聊。
- 群公开 Package 与提问者私有 Package 必须是两次独立、audience-bound resolve,不得把提问者的完整 Package 在 BotDelivery 中二次分割。
- 所有外部副作用都先调 `ActionPlane.prepare(TrustedEffectIntent)` 获取单 effect 票据,再调 `ActionPlane.perform(EffectPayload, ActionTicket)`。每个效果(创建占位、编辑、发送私聊)使用独立的 org-scoped、audience/payload-bound、one-shot 票据;不得跨 effect 复用。
- 历史 IM 消息已是交付字节,Policy Epoch 无法召回;渠道保留、删除/编辑补偿与新成员历史可见性必须经 egress policy 明示定义。
- V1 不做流式:占位消息 + 编辑为完整回答;私聊/群聊 p95 预算在固定负载与 engine/generation 分段测量后预注册,未有证据前不硬编码承诺。

### 3.5 Kernel vs Seam

| 层 | 可插拔(seam) | 不可插拔(kernel) |
|---|---|---|
| 解析 | parser(PDF/Markdown/Office) | — |
| 表示 | embedding、reranker、LLM | — |
| 存储 | V1 固定 PostgreSQL FTS + pgvector;仅保留 Runtime 内候选注入 test seam,第二个真实后端出现前不承诺 portability | 授权真相库(Postgres) |
| 接入 | connector、HTTP server ingress;真实 caller 激活后的 MCP;generated SDK 是 client artifact | 认证调用与 TrustedDeliveryContext 构造 |
| 治理 | 评测裁判模型 | sealed ContextRuntime 编排 / AuthorizationKernel / DecisionAudit / budget / provenance |

## 4. 技术栈

Python 3.13 · FastAPI/Pydantic · SQLAlchemy/Alembic · PostgreSQL 17(RLS + composite FK,授权真相库)· pgvector · TypeScript SDK 由 OpenAPI codegen 生成。

单体模块化(modular monolith)+ 独立 worker 进程起步;微服务拆分由真实瓶颈触发,不预拆。

## 5. 路线图

| 里程碑 | 内容 | 退出条件(可观察) |
|---|---|---|
| **D0** | 设计闭环 | 设计/ADR/安全契约全部纳入可复现版本基线;Runtime 类型流、ACL 证据、BotDelivery/egress、token/ticket、撤权与历史消息语义无未决 P0;允许隔离且可丢弃的飞书、RLS transaction context 与过滤 ANN evidence spike,但不得把 spike 代码变成 production foundation |
| **M0** | 安全工程骨架 | 真实 PostgreSQL 17、migration owner/runtime role 分离、FORCE RLS 与 schema security manifest;两 Organization 对抗 fixture;sealed Runtime、AuthorizationKernel、DecisionAudit 与 invariant catalog 进 CI;初始空 ReleaseManifest 也经授权 `ContextLearning.promote`;未激活能力标为 `NOT_ACTIVE` |
| **M1** | File → authorized Package tracer bullet | 一个 Markdown 从 FileProvider 经 Revision/Fragment、lexical FTS、CandidateRef、AuthorizationKernel、AuthorizedProjection、PackageBudget 到 provisional internal HTTP `resolve(Acquire)` 端到端可演示;allowed/denied/cross-org 及单 Resource 的 revoke/tombstone/retry/active-flip crash 最小语义可验证 |
| **M2** | Wire contract + private BotDelivery PoC | 在 M1 semantic contract 上冻结 OpenAPI v0;TypeScript SDK 可 codegen/build/pack 并启用 breaking-change gate;BotDelivery 只经 generated client 消费 File Package;DeliveryEvidenceRef 兑换 TrustedDeliveryContext,私聊生成与 `ActionPlane.prepare/perform` 全链通过;MCP 在出现真实 caller 时再激活 |
| **M3** | File 可靠性 + Retrieval/Eval | 完整增量 corpus、删除、checkpoint replay、故障矩阵、lease/dead-letter/runbook 达生产硬化;冻结评测集在实验前按 failure-slice coverage 与 uncertainty/power 目标注册样本计划,负例覆盖全部 active refusal/security 类别;中文 tokenizer、FTS+pgvector+RRF、水合、PackageBudget、Continue 与 exact-vs-ANN 达门;reranker 仅由 ablation 激活 |
| **C1** | Curation 并行实验(不阻塞) | M3 后启动;CurationSnapshot、反幻觉门、人工 audit 与 on/off 实验产出可复现报告;每种 annotation kind 按预注册样本与不确定性计划验收,不足只判 inconclusive;不阻塞 M4/M5 |
| **M4** | 飞书上游 + 私聊闭环 | Docs/Wiki 实现 Materialized + 可证明的 Live/Mirrored SourceAclEvidence;Provider base runner/twin、sandbox fixture、live conformance 全绿;飞书私聊完成摄取→授权 Package→交付闭环;Base advanced/field ACL 只在租户版本与真实 API 取证成立时声明 |
| **M5** | 群聊 + private-cell engineering readiness | AudienceSnapshot 在 Kernel 内计算交集;群公开/提问者私聊双 resolve;成员未绑定/过期则 public fail closed;历史消息可见性与删改补偿有明确 policy;备份恢复、migration/rollback、密钥轮换、观测与 runbook 实演通过。**E5 = Security/Reliability/Quality/Budget 全绿 + Ops ready;不依赖先有 partner** |
| **M6** | Slack + contract-kit v1 | 一次只接入 Slack 一个新 connector;将 File/Feishu 已验证的 base runner、capability suite 和 twins 发布为有版本 contract-kit v1;Slack live conformance 全绿,证明复用性 |
| **M7** | Google Docs/Drive | 在 M6 经验稳定后单独实现 Google connector;domain-wide delegation/per-user OAuth 与 ACL 证据语义经 sandbox + live conformance 证明 |

M6 之后坚持 **one connector per milestone**,不把两个新权限模型压在同一个发布门内。企业微信先做 P3 feasibility(会话存档准入、微盘 ACL、delete/edit 事件、region/retention、成本),取证通过后再设独立里程碑;不预先承诺 M7 之前交付。

**Launch Gate L1** 在 E5 之后单独判断:需要明确的 design-partner agreement、legal review、产品命名和 commercial approval。只有 L1 通过才开放受邀使用;它不反向决定 M5 的工程完成度。

## 6. 评测与发布门禁

- **golden set 按失败模式切片**:精确指称 / 问答不对称 / 粒度完整性 / 冗余 / 负例拒答 / 安全。任一切片跌破阈值不放行;安全切片是 veto。
- **分层判分**:检索层用确定性指标(recall@k / MRR);LLM 裁判只判生成层;负例切片同时充当裁判的考场。
- **四条独立门**:Security / Reliability / Quality / Budget,互不平均,发布报告逐条列出。
- 达标之后的第二阶段:同语料对标业界产品盲测。

## 7. Non-goals(V1 明确不做)

- GraphRAG / 多级摘要索引(由评测数据触发,默认关)
- Connector marketplace / 第三方 connector 沙箱宿主
- 跨租户学习(默认关闭;未来仅以 opt-in + 聚合 + 匿名化形式设计)
- 个人微信接入(无官方 API,合规风险,显式出 scope)
- 流式交付(占位+编辑先行;由延迟评测触发)
- 答案生成进引擎(生成永远在 Gateway/上层应用)

## 8. 致谢与参照

设计吸收了对公开开源项目 **Dify、RAGFlow、MaxKB、Onyx** 的架构研究，仅学习可观察行为、Interface 形状、测试 oracle 与产品工作流，零代码复制。固定版本与一手链接见 `docs/research/2026-07-19-four-public-repositories-evidence.md`。安全与多租户协议由 ContextEngine 根据自身 requirement 与 threat model 独立设计；仓库外研究不是公开 authority 或 provenance。
