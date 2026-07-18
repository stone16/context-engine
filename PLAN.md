# ContextEngine — 整体计划

> **Status**: design phase (pre-M0) · **Updated**: 2026-07-18
>
> *English abstract*: ContextEngine is a multi-tenant context delivery engine. Upstream connectors ingest team knowledge (Feishu/Lark, Slack, Google Docs, WeCom); downstream it delivers authorized, evidence-backed, budget-bounded ContextPackages to agent applications and IM bots. Its differentiation is not another retrieval algorithm but the combination of permission-aware retrieval, revocable governance, agent-driven curation, and first-class support for the Chinese team-tooling ecosystem.

---

## 1. 愿景与定位

**一句话**:为团队工具栈提供权限感知、可撤销、可审计的 context 交付。

- **上游**:知识源 connector——File/笔记库起步,飞书(Docs/Wiki/Base)、Slack、Google Docs、企业微信分期接入。
- **下游**:统一的 `resolve()` 接口(HTTP / SDK / MCP)交付 ContextPackage;其上的 **Bot Gateway** 应用把 Package 组装成 IM 里的针对性回答(飞书群聊优先)。
- **差异化**:
  1. **权限感知 + 可撤销治理三合一**——源 ACL 镜像、逐条 provenance、离职/撤权在下一次查询即生效。市面产品最多做到其中一两样。
  2. **Agent-driven curation**——语义去重、过期标记、术语沉淀由 Agent 提案、人工确认、原子发布,把知识库最大的隐性成本(组织成本)从用户身上拿走。
  3. **中文与中国 IM 生态一等公民**——中文分词/术语库进检索内核,飞书/企微上下游打通。

**发行节奏**:先自用与受邀租户(私有部署形态),质量与安全双门槛通过后再考虑公开服务。

## 2. 设计原则(不可谈判)

1. **安全内核不可插拔**:policy / audit / budget / provenance 是核心构造的必填依赖,不存在「关掉安全跑得更快」的配置。可插拔的只有 seam(parser、embedding、index 后端、connector、transport)。
2. **安全是 veto,不是分数**:三条硬底线(README)独立于任何功能评分,失败即不发布。
3. **授权先于相关性**:索引/向量过滤只做候选收窄;每条进入 Package 的内容在交付前逐条做 exact authorization;缓存不做授权决定。
4. **撤权即时,清理异步**:撤权同步提升 Policy Epoch,下一次请求立即失效;索引/缓存清理异步收敛。
5. **发布原子性**:内容以不可变 Revision 发布,任何时刻查询看到全旧或全新,永不见混血。
6. **Learning 有治理**:任何反馈/AI 产物不直改生产;一律 candidate → 冻结评测 → 灰度 → 晋升/回滚。
7. **完成判定不数 feature**:每个里程碑以安全不变量 + 垂直切片通过为完成标志。

## 3. 架构总览

### 3.1 三循环

| 循环 | 职责 | 关键对象 |
|---|---|---|
| **Supply** | 源 → 可信候选:采集、解析、切分、索引、原子发布 | Source / Resource / Revision / Fragment |
| **Runtime** | 查询 → ContextPackage:混合检索、精排、水合、授权、装箱 | ContextRun / ContextPackage / continuation |
| **Learning** | trace → 可发布的改进:评测集、切片门禁、版本化 profile | golden set / versioned profile |

### 3.2 Runtime 检索管线

```
查询理解(意图路由;事务型请求出引擎)
  → 双路召回(全文检索 + 向量,RRF 融合;中文分词 + 术语库)
  → 精排(reranker,可插拔)
  → small-to-big 水合(交付完整逻辑段落)
  → 逐条 exact authorization(授权先于相关性)
  → token budget 装箱 + sufficiency 信号(不够可凭 continuation 再取,预算不追加)
  → ContextPackage(citations / purpose / TTL / asOf,全带证据)
```

### 3.3 Curation Agent(Supply 的延伸)

后台判断链:邻居圈定(引擎自检索)→ 廉价预过滤 → LLM 判断(带引用义务)→ 确定性证据校验(引用必须真实存在,反幻觉门)→ proposed 标注 → 人工 audit → 随 Revision 原子发布。

- 首期三件套:**语义去重**(重复簇折叠)、**过期标记**(旧结论降权并提示)、**tag/术语沉淀**(反哺中文分词)。
- 标注只影响 ranking/assembly,永不触碰授权层。
- 质量门:误标率 < 10% 才算验证通过(audit 记录免费产出该指标)。

### 3.4 Bot Gateway(引擎之外的第一个真实调用方)

- 独立应用,经 HTTP/SDK 调 `resolve()`;回答生成在 Gateway 内,引擎边界止于 ContextPackage。
- IM 用户须绑定到租户内身份,未绑定 fail closed。
- **群聊公开回答按「提问者授权 ∩ 全体群成员权限交集」交付**;超出交集自动降级为私聊/引导链接(点开按个人重新授权)。
- 发送消息走独立的写票据(ActionTicket),与读票据不可互换;引用链接在撤权后失效且不暴露存在性。
- V1 不做流式:占位消息 + 编辑为完整回答;延迟预算(私聊 p95 ≤ 6s / 群聊 ≤ 10s)进入发布门禁。

### 3.5 Kernel vs Seam

| 层 | 可插拔(seam) | 不可插拔(kernel) |
|---|---|---|
| 解析 | parser(PDF/Markdown/Office) | — |
| 表示 | embedding、reranker、LLM | — |
| 存储 | index/向量后端(pgvector 起步) | 授权真相库(Postgres) |
| 接入 | connector、transport(HTTP/SDK/MCP) | 认证调用构造 |
| 治理 | 评测裁判模型 | policy / audit / budget / provenance |

## 4. 技术栈

Python 3.13 · FastAPI/Pydantic · SQLAlchemy/Alembic · PostgreSQL 17(RLS + composite FK,授权真相库)· pgvector · TypeScript SDK 由 OpenAPI codegen 生成。

单体模块化(modular monolith)+ 独立 worker 进程起步;微服务拆分由真实瓶颈触发,不预拆。

## 5. 路线图

| 里程碑 | 内容 | 退出条件(可观察) |
|---|---|---|
| **M0** | 安全内核骨架 | 租户安全的 empty Package;全套对抗性安全 fixture 通过 |
| **M1** | File 源(Markdown 笔记库) | 结构感知切分、hash 增量、删除检测、Revision 原子发布在真实语料变更下成立 |
| **M2** | 检索栈 | hybrid + 精排 + 水合 + budget;中文分词定案;dense vs hybrid 在自有语料实测 |
| **M3** | Learning 基建 + Curation + Bot PoC | golden set ≥ 50 题入库;切片门禁报告;curation 三件套误标率数据;私聊 bot 端到端跑通 |
| **M4** | 飞书 connector + 团队租户 | 飞书 Docs/Wiki 摄取 + 权限映射 contract 测试全绿;群聊 bot 上线;**受邀使用阶段开启** |
| **M5** | Connector kit + Slack + Google Docs | 共享 contract 测试套件正式化;两个新 connector 用 kit 交付 |
| **M6** | 企业微信 | 弱 ACL 源降级语义(会话成员即 ACL + freshness 声明 + 敏感 fail closed)通过 contract 测试 |

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

设计吸收了对公开开源项目 **Dify、RAGFlow、MaxKB、Onyx** 的架构研究(pattern 层面,零代码复制)。安全与多租户设计为独立实现。
