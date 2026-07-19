# ContextEngine 四个公开参考仓证据基线

> 日期：2026-07-19
>
> 状态：研究基线；不是实现授权，也不是代码复用许可
>
> 范围：只核验公开材料允许出现的四个仓库；只总结行为、接口形状、测试 oracle 与产品工作流

## 1. 结论

ContextEngine 当前公开材料允许引用的参考仓只有以下四个：

| 仓库 | 固定研究版本 | 对 ContextEngine 最有价值的证据域 |
|---|---|---|
| [Dify](https://github.com/langgenius/dify) | [`120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5`](https://github.com/langgenius/dify/commit/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5) | 产品化的应用、工作流、Provider/Index seam 与检索运营面 |
| [RAGFlow](https://github.com/infiniflow/ragflow) | [`4391e03886b996201f3b8818f671b19eb24d0f7b`](https://github.com/infiniflow/ragflow/commit/4391e03886b996201f3b8818f671b19eb24d0f7b) | 复杂文档编译、结构化 chunk、hybrid retrieval、父级水合与差分验证 |
| [MaxKB](https://github.com/1Panel-dev/MaxKB) | [`32b2d885e47ad04639abd7a18490bf5937f9c072`](https://github.com/1Panel-dev/MaxKB/commit/32b2d885e47ad04639abd7a18490bf5937f9c072) | preview/confirm、Paragraph/Problem/Tag/Termbase、Hit Test 与人工运营闭环 |
| [Onyx](https://github.com/onyx-dot-app/onyx) | [`2fb3dd10493b3883870fa8adced5b1a0e114feff`](https://github.com/onyx-dot-app/onyx/commit/2fb3dd10493b3883870fa8adced5b1a0e114feff) | Connector、checkpoint、持续同步、权限感知检索、分阶段索引发布与真实依赖测试 |

公开框架不应选一个仓库作为 fork 基座。证据支持的路线是：按 deep module 拆解四仓优势，把 observable behavior 写成 contract 与 acceptance test；ContextEngine 自己实现 tenant ownership、AuthorizationKernel、publication transaction、ContextPackage、audit 与 learning release authority。索引、cache、connector 或 transport 都不能成为安全裁决者。

## 2. 证据纪律

本报告使用三种证据等级：

- **[一手静态]**：固定 commit 的官方仓库源码、测试、license 或官方文档；只证明该 checkout 中存在对应结构或路径。
- **[仓库综合]**：ContextEngine 当前设计对四仓一手材料的 clean-room 综合，必须能回引到固定 commit 的官方 permalink；它是分析，不替代上游源码。
- **[未取证]**：没有动态运行、故障注入、渗透测试或 benchmark 的事实，绝不写成已经验证。

本报告只记录固定 checkout 的静态源码研究，未运行四个上游系统的完整生产拓扑。ContextEngine 自身也仍处于 D0 设计闭环阶段；仓库中没有可执行 production implementation，计划中的隔离 evidence spikes 尚未完成。因此，下文不声称已有动态 Spike 验证。

“无 mock 内容”的公开口径应解释为：

1. 产品/验收场景不用凭空编造的业务接口或虚构能力冒充真实 Provider；最终 capability claim 必须由真实 source sandbox、真实 PostgreSQL、真实 wire 或真实依赖测试证明。
2. deterministic fake、fixture、property test 可以用于验证边界与失败语义，但不能替代 live conformance，也不能成为生产数据或产品能力证据。
3. 上游代码中的 mock/stub 只计入测试结构观察，不计入产品能力得分。

## 3. Dify

### 3.1 已核验优势

- **[一手静态] 产品化 orchestration。** 固定版本同时提供 Dataset/Document indexing 状态机、单库路由、多库 fan-out、hybrid/fusion/rerank、Workflow knowledge node、REST/SDK/MCP 产品面。关键路径见 [`api/core/indexing_runner.py`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/indexing_runner.py)、[`api/core/rag/retrieval/dataset_retrieval.py`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/rag/retrieval/dataset_retrieval.py) 与 [`api/core/workflow/nodes/knowledge_retrieval/retrieval.py`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/workflow/nodes/knowledge_retrieval/retrieval.py)。
- **[一手静态] Adapter/factory 变化轴清楚。** Index processor、vector backend、datasource 与 trace 都有独立入口，适合作为“能力如何产品化”的行为样本，而不是作为 ContextEngine 的安全内核。代表性 seam 见 [`api/core/rag/index_processor/index_processor_base.py`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/rag/index_processor/index_processor_base.py) 与 [`api/core/rag/datasource/vdb/vector_base.py`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/rag/datasource/vdb/vector_base.py)。
- **[仓库综合] 最值得借鉴的是 pipeline capability 作为可配置产品对象、变化轴进入 factory/adapter、执行保存 version/run。** 这支持 ContextEngine 后续把 Source、CompilationProfile、AssemblyProfile 和 ReleaseManifest 做成显式版本对象。

### 3.2 局限与边界

- **[一手静态] 普通 Chat 路径把 retrieval records 拼成 context string；Workflow 虽保留较丰富的 source metadata，也不是 ContextPackage。** 关键路径见 [`dataset_retrieval.py`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/rag/retrieval/dataset_retrieval.py) 与 [`api/core/app/apps/chat/app_runner.py`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/app/apps/chat/app_runner.py)。
- **[一手静态] candidate hydration 没有形成统一的 request-time resource/field exact-authorization seam。** 见 [`api/core/rag/datasource/retrieval_service.py`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/rag/datasource/retrieval_service.py)。因此，Dataset/tenant ownership 不能直接继承为 ContextEngine 的 audience authorization。
- **[一手静态] License 带有额外使用条件。** 公开设计必须只描述 clean-room pattern；复制与商业使用应单独做法律审查。来源：固定版本 [`LICENSE`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/LICENSE)。
- **[未取证]** disable 与异步 cleanup 间的可见性窗口、各 vector backend 的 filter/delete parity、外部企业授权服务的 enforcement，尚无动态证据。

### 3.3 Clean-room 输入

| 学习行为 | ContextEngine 独立实现 | 必须杀死的隐含前提 |
|---|---|---|
| 可观测 indexing 状态 | immutable Revision + outbox + WorkerLease + atomic active pointer | DB commit 后直接 dispatch 永不丢失 |
| Dataset routing、fan-out、fusion/rerank | planner 只能在 Authorized SourceCapability 中收窄 | App/Dataset 配置天然等于用户权限 |
| Workflow source metadata、Hit Test、REST/SDK/MCP | ContextPackage + ContextRun；transport 共用 sealed Runtime | 每个 transport 可自行实现 auth/filter |
| Provider/index factory | typed domain DTO + capability/contract suites | 同名 method 自动具有一致安全语义 |

## 4. RAGFlow

### 4.1 已核验优势

- **[一手静态] Document compiler 是最深模块。** OCR/layout/table/outline、结构化 chunk 与多格式 parser 证明“先编译、后检索”的工程价值。入口见 [`deepdoc/parser/pdf_parser.py`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/deepdoc/parser/pdf_parser.py) 和 [`rag/svr/task_executor_refactor/chunk_builder.py`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/rag/svr/task_executor_refactor/chunk_builder.py)。
- **[一手静态] Retrieval pipeline 覆盖 lexical+dense recall、fusion/rerank、稳定排序与 parent/TOC restoration。** 见 [`rag/nlp/search.py`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/rag/nlp/search.py)。
- **[一手静态] 运行迁移的差分思路可转成 Adapter parity oracle。** Recording context、write interception 与 comparator 可以启发同 fixture 的旧/新实现对比；这比按目录复制更适合 clean-room reconstruction。见 [`rag/svr/task_executor_refactor/comparator.py`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/rag/svr/task_executor_refactor/comparator.py)。

### 4.2 局限与边界

- **[仓库综合] parser 输出仍需 typed contract 包裹。** loose dict、不同 parser 与多语言 runtime 不能自动保证 deterministic parity；需要 PDF/DOCX/Markdown/table fixtures 固定结构、顺序、IDs、metadata 与 provenance。
- **[一手静态] index-side filtering 与 parent hydration 不能承担最终授权。** [`rag/nlp/search.py`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/rag/nlp/search.py) 提供检索/水合行为，但 ContextEngine 必须让每个 child、parent、neighbor、citation 在正文进入内容处理前通过 Kernel。
- **[一手静态] Arbitrary Text-to-SQL 不适合作为 V1 Provider contract。** 固定版本的 RDBMS connector 可以执行配置中的 raw query，缺省路径还可遍历 public tables；见 [`common/data_source/rdbms_connector.py`](https://github.com/infiniflow/ragflow/blob/4391e03886b996201f3b8818f671b19eb24d0f7b/common/data_source/rdbms_connector.py)。ContextEngine 的结构化源必须收口为 curated ContextView、allowed field/operator 与只读角色，而不是把 SQL 生成器当授权边界。
- **[未取证]** 当前固定版本的 Python/Go canonical parity、撤权与 connector secret 风险、queue/DB crash window、GraphRAG checkpoint tenant scope，尚无动态验证。

### 4.3 Clean-room 输入

| 学习行为 | ContextEngine 独立实现 | 必须杀死的隐含前提 |
|---|---|---|
| layout/OCR/table/outline 编译 | `DocumentCompiler.compile → CompiledRevision` typed seam | parser success 等于可以发布 |
| digest、fan-out、recovery、build-new-retire-old | `prepared → indexed → active` publication protocol | batch 中间态可在线可见 |
| hybrid/rerank/parent restoration | deterministic retrieval plan + every-expansion reauthorization | child 合法可推导 parent 合法 |
| runtime comparator | canonical fixtures + Adapter/implementation parity gate | test 数量本身等于行为等价 |

## 5. MaxKB

### 5.1 已核验优势

- **[一手静态] preview → confirm 把 ingestion 变成可审计的运营动作。** 用户可以在正式写入前查看分段结果，Document/Paragraph 保存与 embedding 工作由明确链路连接。见 [`apps/knowledge/serializers/document.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/knowledge/serializers/document.py)。
- **[一手静态] Paragraph、Problem、Tag、Termbase 构成可操作的知识单元。** 这比只暴露 vector chunk 更适合作为 ContextEngine curation/product UX 的参考。见 [`apps/knowledge/models/knowledge.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/knowledge/models/knowledge.py)。
- **[仓库综合] Hit Test、direct return 与 feedback improve 展示了人工闭环的产品价值。** 适合转成 preview、eval report、operator confirmation 和 versioned release，而不是直接改 active behavior。

### 5.2 局限与边界

- **[一手静态] Knowledge/Document/Paragraph 以 mutable product rows 为中心；它们不是 immutable source revision、authorized evidence 或 publication transaction。** ContextEngine 不能沿用 object ID 作为 provenance/authorization 的充分条件。
- **[一手静态] 固定 checkout 的 backend regression foundation 很薄。** 对其中 11 个 Django app 的 `tests.py` 进行静态审阅时观察到空桩；代表性证据是 [`apps/knowledge/tests.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/knowledge/tests.py)。现有 CI 也没有足以证明 backend/security contract 的默认门禁；产品手测不能替代自动回归。
- **[一手静态] Community 根仓为 GPLv3。** 公开 ContextEngine 只学习产品流程与行为，不引入其代码或依赖；来源：固定版本 [`LICENSE`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/LICENSE)。
- **[未取证]** 扩展版本的企业授权实现、部分 source acquisition、Workflow 重启恢复、生产规模与安全保证均不在可核验边界。

### 5.3 Clean-room 输入

| 学习行为 | ContextEngine 独立实现 | 必须杀死的隐含前提 |
|---|---|---|
| upload preview/confirm | candidate Revision → eval → operator decision → atomic publish | mutable Document row 同时是 source truth 与 release |
| Paragraph/Problem/Tag/Termbase | Fragment + CurationAnnotation + versioned profile | LLM enrichment 可直接进入生产 index |
| Hit Test/direct return/feedback | golden slices + report + canary/rollback | 人工测试可代替 regression/release gate |
| modular monolith product shape | Deep Modules + sealed Runtime + real integration seams | 大 serializer/Hook 适合承载 security policy |

## 6. Onyx

### 6.1 已核验优势

- **[一手静态] Connector + checkpoint + staged batch 是成熟的 supply deep module。** 固定版本的 registry、connector interfaces、document fetching 与 indexing pipeline 展示了持续 acquisition、恢复、限流和规范化。见 [`backend/onyx/connectors/registry.py`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/connectors/registry.py)、[`backend/onyx/connectors/interfaces.py`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/connectors/interfaces.py) 与 [`backend/onyx/background/indexing/run_docfetching.py`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/background/indexing/run_docfetching.py)。
- **[一手静态] Retrieval quality 来自完整 compilation chain。** Multi-query、hybrid/federated retrieval、weighted fusion、LLM selection、adjacent/full expansion 与 token budget 是一条显式链。见 [`backend/onyx/tools/tool_implementations/search/search_tool.py`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/tools/tool_implementations/search/search_tool.py)。
- **[一手静态] 测试分层包含真实依赖层。** 官方仓测试说明将 pure unit、外部依赖测试、完整部署 integration 与 Web E2E 分开，为 ContextEngine 的 PostgreSQL/Redis/object store/index contract tests 提供了可借鉴形状。见 [`backend/tests/README.md`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/tests/README.md)。
- **[一手静态] Index configuration 的 future/present rollout 与 readiness gate 值得保留为行为。** 状态与切换路径见 [`backend/onyx/db/models.py`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/db/models.py) 与 [`backend/onyx/db/swap_index.py`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/db/swap_index.py)。ContextEngine 应进一步收口为一次事务切换的 versioned ReleaseManifest/active pointer。

### 6.2 局限与边界

- **[一手静态] worker 缺 tenant context 时存在默认回落路径。** 见 [`backend/onyx/background/celery/apps/app_base.py`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/background/celery/apps/app_base.py)。ContextEngine 的 WorkerLease 必须在缺少 organization/job/policy/audience 绑定时 fail closed。
- **[一手静态] index ACL、source restriction 与 initial hit 不能替代 expansion/hydration 的 exact authorization。** 搜索编排见 [`search_tool.py`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/tools/tool_implementations/search/search_tool.py)；ContextEngine 必须在正文进入 selection、relevance model 与 assembler 前只传 `AuthorizedProjection`。
- **[一手静态] 社区与企业能力/许可边界必须分别报告。** 根 license 来源：固定版本 [`LICENSE`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/LICENSE)。公开文档不能把无法在社区 checkout 独立验证的企业能力写成开源事实，也不能复制受限实现。
- **[未取证]** permission refresh SLA、worker missing-tenant adversarial suite、index swap crash atomicity、所有 connector 的 ACL/freshness parity 与生产 benchmark 尚无动态证据。

### 6.3 Clean-room 输入

| 学习行为 | ContextEngine 独立实现 | 必须杀死的隐含前提 |
|---|---|---|
| Connector/checkpoint/staged batch | ContextProvider + outbox + signed WorkerLease + dual watermarks | 漏 tenant 可回默认 schema |
| versioned index readiness | ReleaseManifest + transactionally selected active version | 两次 status commit 天然原子 |
| multi-query/fusion/selection/expansion | QueryPlanner → candidates → Kernel → authorized relevance/assembly | first hit auth 永久覆盖扩展内容 |
| thin MCP/REST 与真实依赖 tests | canonical HTTP Runtime + generated SDK；MCP 后续 parity | transport 或测试数量本身证明安全 |

## 7. 合成后的 ContextEngine 拆解

四仓证据合并后，合理的职责分配是：

| ContextEngine area | 公开参考输入 | 必须自建的安全/发布边界 | 首个可信验证与证据层级 |
|---|---|---|---|
| Product/Control UX | Dify 的可配置 pipeline；MaxKB 的 preview/confirm/Hit Test | operator authorization、versioned candidate 与唯一 promote authority | contract：真实 Revision fixture 在 confirm 前不改变 active pointer |
| Document compilation | RAGFlow 的结构保真 parser/compiler | typed ParsedDocument/Revision/Fragment、provenance、determinism profile | contract：同一真实 Markdown/PDF fixture 可复现结构与 digest |
| Supply/freshness | Onyx checkpoint/staging；RAGFlow digest/recovery；Dify status visibility | transaction outbox、signed WorkerLease、双水位、atomic active pointer | sandbox：真实 PostgreSQL + worker fault-point crash test |
| Retrieval/assembly | RAGFlow hybrid/parent；Onyx multi-query/fusion/expansion；Dify routing | CandidateRef → Kernel → AuthorizedProjection；PackageBudget/provenance/audit sealed | sandbox：真实 PostgreSQL 授权状态下混入 denied candidate，正文进入 rerank/assembler 数为 0 |
| Curation/Learning | MaxKB operations；Dify trace/Hit Test；Onyx/RAGFlow eval形状 | authorized-only ContextRun、frozen eval、ReleaseManifest、canary/rollback | sandbox：真实 corpus slice report；失败 gate 无法 publish |
| Delivery | Dify/Onyx 的 thin transport/product surface | one Runtime contract、trusted invocation、audience-bound ContextPackage | sandbox：本地真实 HTTP server + generated SDK 对同请求给出等价 security fields |

这也给出实现顺序：先写 contract 与真实安全 seam，随后才接具体算法和广 connector。任何“先用 mock 跑通完整产品”的结果都只能是接口草图，不能记为 milestone capability；任何“先复制上游目录再补授权”的路线都会把上游隐含安全前提固化进核心。

## 8. 证据缺口与下一步研究门槛

### 8.1 当前缺口

1. **没有上游动态 Spike 证据。** 本报告是固定 commit 的静态源码重建；没有启动完整系统、渗透、故障注入、load test 或统一 benchmark。
2. **ContextEngine disposable spikes 尚未落盘。** 当前仓库只有 design/PRD/tech spec 对 spike deliverable 的定义，没有可运行报告、命令、原始输出和 digest。
3. **四仓不是同一 corpus 的横向 benchmark。** 不能把各仓的定性适配判断写成检索质量排名。
4. **Edition 能力不能跨边界继承。** 当前 checkout 看不到或无法依法复用的能力一律保持未取证。
5. **License 结论不是法律意见。** 上游固定版本的 license 文件是事实来源；公开发布前仍需逐仓 legal review。

### 8.2 下一步应形成的可复现包

每个 ContextEngine evidence spike 至少应提交：

- hypothesis 与 falsifier；
- 真实依赖/真实 source sandbox 与版本；
- 不含 secret 的 fixture provenance；
- 一条 setup 命令、一条 verify 命令；
- 原始 stdout/stderr 与机器可读 report；
- commit/config/schema digest；
- 明确的 PASS/FAIL/INCONCLUSIVE；
- spike code 的删除或 runtime-tree 外归档证明。

完成这些以前，公开材料可说“设计已拆解、上游静态证据已固定”，不能说“Spike 已证明实现可行”或“产品能力已通过”。

## 9. 仓库资料索引

- 公开四仓范围：`PLAN.md` 的“致谢与参照”。
- 实现权威：`docs/design/2026-07-18-context-engine-implementation-design.md`。
- PRD：`docs/agents/prd-contextengine-implementation.md`。
- Tech spec：`docs/specs/2026-07-19-context-engine-implementation-epic.md`。
- 四仓一手证据：本报告各仓章节固定的官方源码、测试、文档与 license permalink。本报告本身是公开 reference claim 的唯一聚合入口；仓库外研究笔记不属于公开 authority 或 provenance。
