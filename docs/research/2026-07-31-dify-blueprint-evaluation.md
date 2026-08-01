# Dify → ContextEngine 可复刻蓝图评估（clean-room）

> **决策状态**：本文开放问题已由维护者于 2026-07-31 全部决定（D5），结果见 [`five-repository-implementation-blueprint.md`](./2026-07-31-five-repository-implementation-blueprint.md) §5；正文推荐项为评估时刻的状态。

> **Room-A 研究产物 — 维护者本地研究，非公开 provenance；Room-B 实现者只读本报告的规格与 oracle，不读 Dify 源码**
>
> 本报告是固定 checkout 的静态源码观察与 ContextEngine 独立设计规格，不是公开引用权威，也不是法律意见。公开 prior-art 主张仍只回引
> [`2026-07-19 four-repository evidence baseline`](./2026-07-19-four-public-repositories-evidence.md)。除明确标为本仓已激活的能力外，上游动态正确性、性能、故障恢复与生产安全均为 **[未取证]**。

## 1. 固定 commit 与许可证核验

### 1.1 固定范围与取证方法

- 上游仓库：`https://github.com/langgenius/dify.git`。
- 固定 commit：[`120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5`](https://github.com/langgenius/dify/tree/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5)。Room-A 在 `/tmp/dify-bp` checkout 后以 `git rev-parse HEAD` 核对为该值。
- 证据类型仅为固定 commit 的 **[一手静态]** source/license/tree 观察；未启动 Dify 服务、数据库、队列、向量后端或 tracing provider，未做故障注入与 benchmark。
- 本报告不得成为 Room-B 对 Dify root code 的间接逐行翻译。Room-B 只实现下文 ContextEngine 自有 DTO、状态机、失败语义和 oracle；不得打开上游 root-licensed 实现来补足含糊处。

### 1.2 root license：全部 root-licensed code 只允许 clean-room

Dify 固定 commit 的根 [`LICENSE`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/LICENSE#L1-L19) 声明“modified Apache License 2.0”，并对未经书面授权使用源码运营 multi-tenant environment 增加商业许可条件。ContextEngine 的产品目的正是 multi-tenant context delivery，因此按
[`ADR-0074`](../decisions/0074-adopt-controlled-third-party-code-reuse.md) 的已接受裁决：

1. `api/`、`web/` 及其他未被路径内独立许可证覆盖的代码全部是 **clean-room only**；
2. 本报告可保留可观察行为、接口形状和测试 oracle，不得复制实现、常量表、控制流或测试代码；
3. root license 结论不因单个文件缺少版权头而改变，也不能以“Apache-2.0 部分”绕过附加条件；
4. public provenance 不引用本报告新增的仓库外研究结论，只引用四仓 evidence baseline 或可复核的一手 permalink。

### 1.3 SDK 子树逐路径核验

| 固定路径 | 路径内证据 | 结论 | 本次处置 |
|---|---|---|---|
| `sdks/nodejs-client/**` | 子树有独立 [`LICENSE`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/sdks/nodejs-client/LICENSE#L1-L21)，正文为 MIT；[`package.json`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/sdks/nodejs-client/package.json#L13-L42) 同时声明 `license: MIT`、仓库子目录和 root-only export。 | 在该固定 commit、该路径边界内具备 ADR-0074 所要求的独立 MIT 路径证据；若复制仍须逐文件 hash、nested dependency/notice scan、审批、SBOM 和 artifact inclusion。 | **法律层可进入 copy+patch 审查，架构层候选为 none。** 不复制。 |
| `sdks/php-client/**` | [`README`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/sdks/php-client/README.md#L91-L95) 自述 MIT；但该子树 tree 中没有路径内 `LICENSE`，[`composer.json`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/sdks/php-client/composer.json#L1-L9) 也没有 `license` 字段。 | **[未取证]**。README 文字不足以替代 ADR-0074 要求的 exact path license-region verification。 | do-not-take；且 ContextEngine 没有 PHP SDK 需求。 |
| `sdks/` 其余说明 | 固定 [`sdks/README.md`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/sdks/README.md#L1-L25) 将 Java/Go/Ruby 指向外仓，并把 Python/PHP/Node 标为待迁移说明。 | 外仓不在本任务固定 commit 的许可证核验范围，不能继承 Dify 子树结论。 | do-not-take；若未来需要，重新固定各自 repo/commit/path。 |

### 1.4 copy+patch 结论：none；生成式 SDK 路径严格更优

`sdks/nodejs-client` 虽有 MIT 路径证据，但不适合 ContextEngine：

- 它手写 routes、wire types 和 client methods，而 ADR-0047/0048 要求 OpenAPI v0 是唯一语义源；
- 它从 root 导出 [`HttpClient`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/sdks/nodejs-client/src/index.ts#L88-L103)，并且 base client 暴露任意 endpoint/header 的
  [`sendRequest`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/sdks/nodejs-client/src/client/base.ts#L33-L68)；这与 ContextEngine “只导出 closed facade，调用者不能制造任意 trusted metadata/header”的要求冲突；
- 它的 KnowledgeBase client 面向 mutable Dataset/Document/Segment CRUD 与手写 indexing-status route，例如
  [`getDocumentIndexingStatus`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/sdks/nodejs-client/src/client/knowledge-base.ts#L319-L329)，不是 `ContextPackage` consumer；
- 本仓已经按 [`ADR-0048`](../decisions/0048-generate-typescript-sdk-behind-a-closed-facade.md) 激活 pinned OpenAPI codegen、generated-tree digest、closed export map、clean consumer install 与 compile-negative fixtures。复制会引入第二份 wire truth 和不必要的补丁负担。

因此本任务的 **copy+patch candidates = none**，无需创建 `third_party/dify/`。若未来架构决策被重开，下面仅是治理登记的最小模板，**不是复制批准**；每个 `[[files]]` 必须列举实际选中的文件与固定 commit 内容 hash，`approval` 在批准前不得填占位值后落库：

```toml
repository = "https://github.com/langgenius/dify.git"
commit = "120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5"
source_paths = ["sdks/nodejs-client/<exact-approved-file>"]
excluded_paths = ["api", "web", "sdks/php-client"]
reuse_mode = "copy-patch"
approval = "<required-review-record>"
license = "MIT"

[[files]]
upstream_path = "sdks/nodejs-client/<exact-approved-file>"
vendored_path = "third_party/dify-node-sdk/<exact-approved-file>"
sha256 = "<sha256-of-pinned-upstream-bytes>"
```

实际重开还必须同时加入 `LICENSE.upstream`、`MODIFICATIONS.md`、nested notices、CycloneDX SBOM、`THIRD_PARTY_NOTICES.md` 聚合与 wheel/sdist/npm/container artifact completeness；仅有 `UPSTREAM.toml` 不构成合规。

## 2. 能力盘点 → ContextEngine 区域映射表

| Dify 可观察能力 | 固定上游入口 | ContextEngine 区域 / seam | 复用分类 | 决策摘要 |
|---|---|---|---|---|
| Document indexing lifecycle、pause/error/retry | [`api/core/indexing_runner.py`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/indexing_runner.py)；[`IndexingStatus`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/models/enums.py#L129-L139) | Supply：immutable ContextRevision、File publication checkpoint/job events、outbox、WorkerLease、dual watermarks、Control status | **clean-room Room-A spec** | 学习可观察 phase；不拿 mutable Document/Segment、先删旧 index 再重建或 exception text。 |
| 单库 LLM routing | [`single_retrieve`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/rag/retrieval/dataset_retrieval.py#L602-L738) | Runtime `QueryPlanner` + server-derived `AuthorizedSourceCapabilitySet` | **clean-room Room-A spec** | router 只能在已授权 capability 集中收窄；source 描述必须 content-free，route 不是授权。 |
| 多库 fan-out、hybrid、weighted/model rerank | [`multiple_retrieve`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/rag/retrieval/dataset_retrieval.py#L740-L887)；[`RetrievalService._retrieve`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/rag/datasource/retrieval_service.py#L796-L931) | Candidate discovery、rank evidence、RRF、AuthorizationKernel、authorized ranking/rerank、PackageBudget | **clean-room Room-A spec** | 仅 content-free fusion 可在 Kernel 前；content-bearing rerank/dedupe/assembly 必须在 `AuthorizedProjection` 后。 |
| Candidate hydration、parent/child/summary restoration | [`format_retrieval_documents`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/rag/datasource/retrieval_service.py#L488-L791) | CandidateRef → Article-level Kernel → lineage-verified same-Article expansion / cross-Article reauthorization | **do-not-take implementation；仅负面 oracle** | 上游 formatter 直接读取 segment/child content；这是必须杀死的授权前水合 premise。 |
| 普通 app 的 context string concat | [`DatasetRetrieval.retrieve`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/rag/retrieval/dataset_retrieval.py#L455-L600) | `ContextPackage` 是唯一 online deliverable；生成在 BotDelivery/上层 | **do-not-take** | 不返回裸字符串，不丢 Evidence、audience、policy、budget、provenance、TTL。 |
| Index processor factory | [`BaseIndexProcessor`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/rag/index_processor/index_processor_base.py#L46-L108)；[`IndexProcessorFactory`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/rag/index_processor/index_processor_factory.py#L10-L29) | DocumentCompiler + Supply execution seam + typed immutable DTO | **clean-room Room-A spec** | 学习 variation isolation；不拿混合 I/O、mutable ORM Dataset/Document 或“大而全”base class。 |
| Vector backend factory/entry point | [`BaseVector`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/rag/datasource/vdb/vector_base.py#L18-L76)；[`vector_backend_registry.py`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/rag/datasource/vdb/vector_backend_registry.py#L28-L87) | V1 native PostgreSQL FTS + pgvector；data-only CandidateIndex seam | **do-not-take portability；clean-room contract oracle only** | 在第二个真实 backend 前不承诺 portability；同名 search/filter/delete 不代表 tenant、filter 或原子语义一致。 |
| Datasource runtime/factory | [`datasource_manager.py`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/datasource/datasource_manager.py) | ContextProvider / connector-runner、SupplyDocumentEnvelope/ChangePage/checkpoint、per-connector twin | **clean-room Room-A spec** | connector 只提议 observation；engine durable acceptance 与 source authorization 仍是唯一真相。 |
| Workflow knowledge-retrieval node | [`KnowledgeRetrievalNode`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/workflow/nodes/knowledge_retrieval/knowledge_retrieval_node.py#L67-L182)；[`Source`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/workflow/nodes/knowledge_retrieval/retrieval.py#L12-L82) | Agent/Bot consumer 经 generated SDK 调一个 sealed Runtime；ContextPackage/ContextRun | **clean-room Room-A spec** | 学习“retrieval 是版本化产品对象”；不拿 caller-authored tenant/user/dataset IDs 或 raw source-rich output。 |
| REST retrieval/hit-test | [`service_api/dataset/hit_testing.py`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/controllers/service_api/dataset/hit_testing.py#L17-L68) | 公开 `POST /v0/resolve`；Control/UI hit test 也走最高 public seam | **clean-room Room-A spec** | 不建第二条“test retrieval”授权路径；同一 Runtime 返回同一 Package/error 语义。 |
| SDK | `sdks/nodejs-client/**` | immutable OpenAPI v0 → generated client → closed TypeScript facade | **copy+patch (MIT) eligibility，candidate none** | 路径许可可核验，但 generated path 严格更符合 ADR-0048。 |
| MCP product surface | [`MCPAppApi`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/controllers/mcp/mcp.py#L44-L96) | 可选 MCP ingress → 同一 `ContextRuntime.resolve` | **do-not-take now** | 本仓真实 caller 出现且 parity/security suite 完成前保持 `NOT_ACTIVE`，不复制 App MCP server。 |
| Trace provider selection 与 retrieval trace | [`OpsTraceProviderConfigMap`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/ops/ops_trace_manager.py#L216-L300)；[`dataset_retrieval_trace`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/ops/ops_trace_manager.py#L1094-L1180) | authorized-only digest-only ContextRun + restricted DecisionAudit + content-free metrics | **clean-room Room-A spec；raw trace do-not-take** | 保留 provider variation lesson；不保留 raw query、full documents、denied refs/counts、workspace/user display fields或 credential metadata。 |

## 3. 逐能力蓝图

以下工期均为**相对当前 `STATUS.md` 激活面上的增量 engineer-days**，包括实现、单元/contract test、真实 PostgreSQL 测试与文档，不含 production identity、live connector tenant、外部法务或大规模 benchmark 等外部等待时间。

### 3.1 Indexing 状态机 → immutable Revision publication

**上游路径与观察。** Dify 固定枚举公开 `waiting → parsing → cleaning → splitting → indexing → completed`，另有 `paused`/`error`（[`models/enums.py`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/models/enums.py#L129-L139)）。runner 在 extract 后写 splitting、保存 segment 后写 indexing、index worker 完成后写 completed，并把 exception text 写回 Document（[`indexing_runner.py`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/indexing_runner.py#L58-L67)、[`#L483-L491`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/indexing_runner.py#L483-L491)、[`#L676-L687`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/indexing_runner.py#L676-L687)）。retry/sync 可先删除旧 segment/vector 再重新处理（[`retry_document_indexing_task.py`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/tasks/retry_document_indexing_task.py#L81-L118)、[`document_indexing_sync_task.py`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/tasks/document_indexing_sync_task.py#L123-L160)）。这些只证明 UI/运营 phase 的价值；不会被继承为 publication semantics。

**本仓 seam / ADR。** `CONTEXT.md` 的 `ContextResource → immutable ContextRevision → ContextFragment`；ADR-0018（old-or-new pointer）；ADR-0040/0041（replacement plan + durable recovery boundary）；ADR-0043（acquisition checkpoint / publish watermark 分离）；ADR-0059/0060（exact WorkerLease dispatch/reclaim）；ADR-0072（content-free status）。

**Room-A 行为规格。** Room-B 实现下列独立状态域，不把它们压成一个 mutable `status`：

1. **Durable acceptance / outbox。** 一个 accepted `SourceChange` 事务必须同时写入 immutable change record、按 source sequence 的 acquisition checkpoint、唯一 durable import job 和可 claim outbox/queue row。事务失败则四者都不存在；事务成功但 worker 未收到通知时，scheduler 仍能从 durable row claim，禁止“commit 后直接 `.delay()` 是唯一交付”。
2. **Job/recovery checkpoint。** job 内部 checkpoint 只允许 `acquired → prepared → ready → completed` 单调前进；`interrupted`/`reclaimed` 是 immutable job events，不倒退 checkpoint。每次执行绑定 `(organization, sourceVersion, job, operation, resource?, revision?, leaseGeneration, nonce, expiry)` exact WorkerLease；高 generation reclaim 后旧 lease 任何 effect 为 0。
3. **Revision lifecycle。** `ContextRevision` 只允许 `prepared → indexed → active`。`error`、`paused`、`retrying` 不是 Revision state：失败属于 job/refusal，暂停属于调度/Control，旧 active Revision 不被修改。prepared 必须固定 content/compilation/profile digest 与完整 Fragment lineage；indexed 必须证明所有 required index artifacts 完整且与该 Revision 一致。
4. **Activation。** replacement `ready` 后，在同一 Organization publication exclusive barrier 下重新验证 current Source active、SourceVersion、job/lease generation、current acquisition authority、完整 artifact digest 与 expected previous active Revision；随后单事务 compare-and-swap `ContextResource.active_revision_ref`、append `active` publication event、supersession edge、publish completion并完成 job。读事务持 shared barrier，始终看完整旧版或完整新版。
5. **CAS conflict。** 若另一并发任务已激活 bit-identical complete artifact，当前 job可在 guard lock 下分类为 `unchanged` 并零 publication effect 完成；若 active lineage 不同则保留 `ready`/产生 closed conflict category，重新取权威状态，不得覆盖。
6. **失败与取消。** compile/index/embedding 失败：新 Revision永不 active，旧 Revision继续服务；仅持久化 closed refusal category，不存 source bytes、parser diagnostic 或 exception text。Source disable：同事务禁用 source、推进 Policy Epoch、取消 nonterminal jobs、写 cleanup intent；此后旧 lease effect 为 0。崩溃发生在任一已提交 boundary 后：新 generation 从 checkpoint 继续，不重复已证明的 deterministic work；未提交 work 视为未发生。
7. **Operational status projection。** Control 可显示 `accepted/claimed/preparing/indexing/ready/reclaiming` 的 content-free aggregate、acquisition checkpoint、contiguous publish watermark、active Resource count、`never | last_success_at/age`、in-flight count、closed refusal category及 ADR-0072 已允许的当前 canonical path/digest/length。不要表面化 raw parsing text、exception、Fragment/denied identity或非连续“最新完成”水位。terminal `activated/unchanged/refused/cancelled` 可按 opaque job ref查看；它们不进入 Runtime authorization。

**ContextEngine 接口形状草图（不是 Dify API）。**

```python
@dataclass(frozen=True, slots=True)
class PublicationWork:
    organization_ref: OrganizationRef
    source_ref: ContextSourceRef
    source_version_ref: SourceVersionRef
    job_ref: FileImportJobRef
    acquisition_ref: AcquisitionRef
    expected_resource_ref: ContextResourceRef | None
    expected_previous_revision_ref: ContextRevisionRef | None
    content_identity_digest: Digest

class SupplyPublication:
    def accept(change_page: AcceptedChangePage, actor: TrustedControlCall) -> AcceptanceReceipt: ...
    def prepare(work: PublicationWork, compiled: CompiledRevision, lease: WorkerLease) -> PreparedReceipt: ...
    def mark_indexed(work: PublicationWork, artifacts: IndexArtifactSet, lease: WorkerLease) -> ReadyReceipt: ...
    def activate(work: PublicationWork, lease: WorkerLease) -> ActivationOutcome: ...
    def status(call: TrustedSourceStatusCall) -> SourceOperationalStatus: ...

ActivationOutcome = Activated | Unchanged | LeaseRejected | AuthorityChanged | Conflict | RetryableUnavailable
```

`CompiledRevision` 和 `IndexArtifactSet` 必须是 versioned、immutable、无 ORM/session/callback 的 domain DTO；只有 Supply module 能构造 publication transaction。`status` DTO 无 Runtime capability。

**测试 oracle。**

- 在 accept commit 后、dispatch 前 kill；scheduler 最终 claim 同一 job且只产生一个 target Revision。
- 在 `acquired`、`prepared`、`ready` 后逐点 kill；reclaim generation `n+1` 完成，同一 checkpoint不回退，generation `n` 重放 effect=0。
- index/embedding 第 N 个 Fragment 失败：active pointer 仍指旧版；候选、Fragment 与旧 Package无混合。
- activation commit 前/后 kill：读者观测集合只能是 all-old 或 all-new，不能出现 hybrid；publish watermark只在 visibility commit 后跨过该 sequence。
- 两个相同与两个不同 replacement 竞争：相同产生一个 active + 一个 unchanged；不同只有 CAS winner，loser 不覆盖。
- Source disable 与 activation/lease redemption 竞争：线性化后若 disable 先提交则 publication=0；若 activation先提交，随后的 disable 仍使 Runtime不可见并推进 epoch。
- refusal status 只含 closed category；fixture 的 source substring、exception、Fragment id、credential在 DB row、JSON、log capture 中出现次数为 0。
- acquisition checkpoint可领先 publish watermark；后续 sequence completion不能越过前序 gap；recovery闭合 gap后 contiguous watermark推进。

**验证命令。** 纯状态/DTO/治理：`make lint && make typecheck && make test && make catalog`。publication、WorkerLease、RLS、crash-boundary和 old-or-new 证据：先 `make db-up`，再 `make integration && make security-gate`，最后 `make db-down`；本报告任务未执行这些数据库命令。

**工作量与依赖。** 6–9 engineer-days。依赖现有 ADR-0040/0041/0059/0060 实现、PostgreSQL publication barrier、File import job/outbox ownership、ADR-0072 status DTO；若新增通用非 File outbox，另加 3–5 天并先写 ADR，不能把 File carrier泛化成已激活 ProviderPort。

### 3.2 Dataset retrieval orchestration → authorized planner

**上游路径与观察。** 单库模式先为 available datasets 生成工具描述，由 LLM router选择一个 id，再按该库 retrieval config执行（[`dataset_retrieval.py#L602-L738`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/rag/retrieval/dataset_retrieval.py#L602-L738)）。多库模式并行每个 dataset / text-or-attachment branch，检查 indexing technique/embedding model compatibility，最后进行跨库 rerank或 score排序（[`#L740-L887`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/rag/retrieval/dataset_retrieval.py#L740-L887)、[`#L1798-L1909`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/rag/retrieval/dataset_retrieval.py#L1798-L1909)）。单 dataset 内 hybrid 同时跑 keyword/full-text/vector，dedupe 后 weighted/model rerank（[`retrieval_service.py#L796-L931`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/rag/datasource/retrieval_service.py#L796-L931)）。

**必须明确的 hydration anti-pattern。** Dify vector/search 结果携带 `page_content`；formatter 随后读取 `DocumentSegment`、`ChildChunk.content`、summary、attachments并构造内容对象（[`retrieval_service.py#L488-L791`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/rag/datasource/retrieval_service.py#L488-L791)）。普通 app 路径再把这些正文排序后以 newline concat 返回（[`dataset_retrieval.py#L474-L600`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/rag/retrieval/dataset_retrieval.py#L474-L600)）。固定 inner service 甚至明确只验证 tenant ownership、不检查 user-level dataset permission（[`knowledge_retrieval_inner_service.py#L1-L12`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/services/knowledge_retrieval_inner_service.py#L1-L12)）。ContextEngine 必须把“tenant/dataset ownership = audience authorization”和“hit 后可直接 hydrate”作为 must-kill premises。

**本仓 seam / ADR。** ADR-0012/0024/0025（sealed projection + EffectiveScope + same-transaction projection）、ADR-0067/0068（content-free vector discovery）、ADR-0076（rank evidence在授权后 exact rejoin）、ADR-0077（Article authorization atom）、ADR-0081（data-only discovery session）、Implementation Design §3 Runtime order / §8 retrieval。

**Room-A 行为规格。** planner严格分成 plan、discover、authorize、rank/assemble 四层：

1. `AuthorizedSourceCapabilitySet` 由当前 `UserActor` transaction 内的 mandatory EffectiveScope、active SourceVersion、ReleaseManifest/RuntimeProfile、Source capability declaration 和 optional RequestNarrowing交集产生。它只说明“哪些 source/ref + retrieval mode 可以被尝试”，不是 Resource grant。caller 不能直接传 dataset/source set；RequestNarrowing只能删除已有 ref/mode。
2. `QueryPlanner.plan` 输入 ContextNeed、上述 capability set、ReleaseObservation 和 effective PackageBudget，输出 frozen `RetrievalPlan`。source descriptor仅含 opaque source ref、closed resource kinds、mode、language/embedding/profile compatibility和content-free operator label；不得把 source description正文、title/path、ACL或credential交给 router/model。
3. `ROUTE_ONE` 模式只允许从 capability set 选零或一个 source。router返回未知/越界 source、无结果或模型不可用时，分别映射 `InvalidPlan`、合法 empty plan或 profile-declared unavailable；绝不能扩大为全部 source。router model调用必须经 governed model-inference port、EgressGrant与 shared PackageBudgetMeter。
4. `FAN_OUT` 模式按 plan固定的 `max_sources × per_source_k × max_rankers` 有界并发。每个 source只能使用声明 capability；不支持的 mode在 candidate I/O 前返回 `UnsupportedCapability`。V0 对任何 required branch failure采用 fail-closed `RetryableUnavailable`，不静默降级到 weaker source/ACL/mode；将来若允许 optional ranker，必须由 versioned profile预声明且在 Package记录 gap。
5. candidate discovery query由 CandidateIndex准备、Runtime在 retained UserActor transaction执行；replaceable index只收到 `CandidateDiscoverySession` 的 primitive results。每个 hit是 `CandidateRef + RankEvidence`，二者均无 body/title/path/source metadata。lexical/vector/hybrid fusion（建议 deterministic RRF）只操作这些值。
6. Runtime以 deterministic canonical CandidateRef order调用 sealed `AuthorizationKernel`；Kernel看不到 rank。每个 ref验证 Organization、active Source/Resource/Revision、ArticleAccessPolicy、SourceAclEvidence、Membership/Agent/purpose/audience/field ceiling并只对成功项构造 `AuthorizedProjection`。missing/denied/cross-org均进入同一个 empty-compatible路径；denied detail不进 ContextRun。
7. `AuthorizedRanker` 按 exact CandidateRef 把 rank evidence rejoin到成功 projection；denied rank立即丢弃。weighted fusion仍可使用 authorized rank；任何看正文的 reranker、dedupe、tokenizer、summary/parent/neighbor hydration、selection和assembler只接受 `AuthorizedProjection`。
8. same-Article/current-Revision expansion可在核验 lineage后继承已作出的 Article decision；跨 Article expansion必须回到新的 CandidateRef并重新走 Kernel。任何 summary/attachment/child/parent只要触及另一 Article都不得继承 first hit。
9. assembly按 effective PackageBudget确定性选择；final Policy Epoch和egress veto在 delivery 前重验。输出只可能是 authorized `ContextPackage`、canonical empty Package、closed unavailable/refusal；永不输出 context string或 raw ranked documents。

**ContextEngine 接口形状草图。**

```python
@dataclass(frozen=True, slots=True)
class AuthorizedSourceCapability:
    source_ref: ContextSourceRef
    source_version_ref: SourceVersionRef
    allowed_modes: frozenset[DiscoveryMode]
    allowed_resource_kinds: frozenset[ResourceKind]
    index_profile_ref: IndexProfileRef
    projection_ceiling: ProjectionCeiling

class QueryPlanner(Protocol):
    def plan(
        self,
        need: ContextNeed,
        capabilities: tuple[AuthorizedSourceCapability, ...],
        release: ReleaseObservation,
        budget: PackageBudget,
    ) -> RetrievalPlan: ...

class CandidateIndex(Protocol):
    def prepare(self, plan: RetrievalPlan, scope: ContentFreeDiscoveryScope) -> PreparedDiscovery: ...
    def shape(self, result: CandidateDiscoverySession) -> tuple[RankedCandidateList, ...]: ...

class AuthorizedRanker(Protocol):
    def rank(
        self,
        query: AuthorizedRelevanceQuery,
        items: tuple[AuthorizedProjectionWithRank, ...],
        meter: PackageBudgetMeter,
    ) -> tuple[AuthorizedProjection, ...]: ...
```

`AuthorizedRelevanceQuery` 是经 retention/egress policy允许的 request-scoped值；它不允许 ranker恢复数据库/session/locator。`RankedCandidateList` 的 type constructor拒绝任何 `content/title/path/metadata` 字段。

**测试 oracle。**

- capability set `{S1}` + caller narrowing `{S1,S2}`：plan仍只能含 `S1`；未知/empty/malformed router output不会触发 S2 call。
- malicious CandidateIndex尝试通过对象图获取 projection session/connection/locator：静态 capability-graph test和运行时 probe都不可达。
- 每个 ranker混入 same-org denied、cross-org、stale revision、tombstoned resource；正文进入 pre-Kernel fusion可为 0（它本就无正文），进入 rerank/tokenizer/assembler/model的 denied bytes严格为 0。
- rank evidence map含 denied candidate；authorized output排序只由 admitted candidate的 exact join决定，删除 denied ref不改变 delivered order/shape。
- hybrid三个 branch乱序完成：相同 inputs/profile产生 byte-stable fused order；相同 rank/tie以 opaque canonical ref稳定打破。
- required branch exception、timeout、unsupported、live ACL unavailable各映射 closed unavailable，且不会退回 Weak；零 hit则为 canonical empty Package，不伪装 unavailable。
- parent/child fixture：same Article/current Revision expansion成功；stale Revision或cross Article必须重授权；cross Article denied内容进入 consumer=0。
- final epoch在 authorization 与 delivery间推进：Package不交付，ContextRun不宣称 delivered；已消耗的模型/provider budget按失败 lineage安全记录，不含 denied内容。
- top-k/score不产生 protected-object enumeration：unknown、denied、cross-org probes经允许归一化字段后 Package相等；不声明 timing equivalence。

**验证命令。** `make lint && make typecheck && make test && make catalog`；real pgvector/FORCE-RLS/cross-org/epoch/hydration证据需 `make db-up && make integration && make security-gate`（结束 `make db-down`）；HTTP/generated SDK最高 seam另跑 `make smoke && make openapi-check && make sdk-check && make sdk-test`。

**工作量与依赖。** 12–18 engineer-days（ROUTE_ONE 3–4，bounded FAN_OUT + deterministic RRF 4–5，authorized rank/rerank 3–5，failure/budget/trace gates 2–4）。依赖 ADR-0081 data-only CandidateIndex、ADR-0076 rank rejoin、complete Article policy/field projection、shared PackageBudgetMeter、frozen eval slices；real model rerank carrier保持 `NOT_ACTIVE` 直到 ablation与egress证据独立通过。

### 3.3 Factory / adapter 轴 → typed Provider contract 与 conformance suite

**上游路径与观察。** Dify 将 paragraph/QA/parent-child processors经 factory选择（[`index_processor_factory.py`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/rag/index_processor/index_processor_factory.py#L10-L29)），`BaseIndexProcessor` 同时拥有 extract/transform/summary/load/clean/index/preview（[`index_processor_base.py`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/rag/index_processor/index_processor_base.py#L46-L108)）。vector backend 以同一 abstract create/add/search/delete面和 entry points隔离（[`vector_base.py`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/rag/datasource/vdb/vector_base.py#L18-L76)、[`vector_backend_registry.py`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/rag/datasource/vdb/vector_backend_registry.py#L39-L87)）。Datasource manager和 trace config map分别选择插件 runtime与 trace provider。可学习的是“变化轴有独立入口、unsupported显式”；不可继承的是 broad ORM/session接口和“方法同名即语义等价”。

**本仓 seam / ADR。** Implementation Design §3.3 `ContextProvider` typed interface；ADR-0075 connector-runner + three keystone seams；ADR-0078 将 contract ownership 放回 Supply execution seam并要求每 connector自带 deterministic twin；ADR-0081固定 CandidateIndex data-only能力；V1 vector implementation固定 PostgreSQL，不建 backend marketplace。

**Room-A 行为规格。** 将变化轴拆成三个互不相认的端口：

1. **Pure compiler axis：** `DocumentCompiler.compile(CanonicalSourceBytes, CompilationProfile) -> CompiledRevision | CompilationFailure`。无网络、DB、clock、Organization lookup、credential或publication authority；同 bytes/profile输出完全相同的 typed structures、source spans和digests。
2. **Connector-runner axis：** runner接收 exact WorkerLease-bound `ConnectorJobEnvelope`，调用一个 connector读取 source，输出 bounded `ChangePage[SupplyDocumentEnvelope, DeleteObservation, AclObservation, OpaqueCheckpointProposal]`，独立持久化为 0。checkpoint仅是 proposal；engine全页 durable accept 后才签发 continuation。connector不得发布 Revision、推进 watermarks、tombstone Resource或构造 Runtime authority。
3. **Runtime discovery/projection axis：** Materialized File走 native PostgreSQL candidate discovery；未来 live/federated provider使用 `describeCapabilities/discover/authorizeAndProject` 的 closed outcomes。candidate discovery返回 content-free CandidateRef；projection evidence交给 Kernel，不让 adapter直接构造 AuthorizedProjection。V1不抽象多个 vector store；PG FTS/pgvector是一个固定实现，filter/delete parity问题不被 factory掩盖。

`ProviderOutcome` 只能是 `Ok | Unsupported | RetryableUnavailable | InvalidCheckpoint | GenericDenied`。Unsupported/denied/unavailable不能变成 empty success；SourceAclEvidence mode是 SourceVersion声明，Live/Mirrored失败不可退成 Weak。

**ContextEngine 接口形状草图。**

```python
class DocumentCompiler(Protocol):
    def compile(self, source: CanonicalSourceBytes, profile: CompilationProfile) -> CompilationOutcome: ...

class ConnectorRunner(Protocol):
    def execute(self, job: ConnectorJobEnvelope, lease: WorkerLease) -> ConnectorRunOutcome: ...

class ContextProvider(Protocol):
    def describe_capabilities(self, source: ContextSourceRef) -> ProviderOutcome[CapabilityDeclaration]: ...
    def read_changes(
        self, source: ContextSourceRef, cursor: ChangeCursor | InitialScan, limit: ChangeLimit
    ) -> ProviderOutcome[ChangePage]: ...
    def discover(
        self, ticket: ContextAccessTicket, plan: RetrievalPlan, limit: CandidateLimit
    ) -> ProviderOutcome[CandidatePage]: ...
    def authorize_and_project(
        self, ticket: ContextAccessTicket, refs: tuple[CandidateRef, ...], ceiling: ProjectionCeiling
    ) -> ProviderOutcome[SourceProjectionBatch]: ...
```

DTO全部 `frozen + slots + closed enum/discriminated union`，只携带 domain refs/values；禁止 `Session`、ORM row、untyped dict、callback、raw credential和ambient tenant。connector registry只从 server-owned SourceVersion解析实现，不接受 caller plugin path。

**每 connector 必过的 conformance suite。**

| Suite | 硬 oracle |
|---|---|
| Capability honesty | 未声明 operation返回 `Unsupported` 且 provider/source I/O计数符合声明；声明的 ACL mode、batch/field/cursor limits与实际相同。 |
| Identity/tenant binding | 缺 Organization/SourceVersion、wrong source/job/workload/operation、expired/replayed lease的 source call或durable effect为 0；无 default tenant/schema。 |
| Cursor/checkpoint | 同 cursor同 fixture得到同 ordered page；未全页 durable accept不发 next cursor；old cursor不后退；SourceVersion变化令旧 cursor `InvalidCheckpoint`。 |
| DTO/provenance | 同 source bytes/metadata/profile产生相同 canonical path、Resource key、content digest、ACL observation、delete observation和ordered envelope；不含 credential/provider internal object。 |
| ACL semantics | Live同请求检查或声明 verify-before/after；Mirrored带 exact aclAsOf/version/freshness；Weak仅在源确无更强语义；Live/Mirrored outage永不回退。 |
| Projection consistency | discovery/projection的 SourceConsistencyRef exact match；missing/mixed/stale/changed ref被Kernel拒绝；batch部分 denial不泄露denied object detail。 |
| Delete semantics | connector只产 content-free delete observation；接受 observation不会 tombstone/advance epoch/cleanup，唯一 Control tombstone authority另行重验 current scan。 |
| Retry/replay | page、job和provider response重放不重复 Resource/Revision/effect；reclaim generation替换旧 generation；ambiguous external read映射 closed retryable，不能伪造 empty。 |
| Boundedness | path count、page size、bytes、fields、depth、wall time都由 server profile限制；越界 all-or-nothing closed refusal，无 partial publish。 |
| Twin/live tiers | deterministic twin跑完全套但只得 contract-verified；sandbox/live同一 suite通过后才分别升 tier，不能由 mock数量替代。 |
| Capability graph | connector/compiler/CandidateIndex对象图均不能到达publication pointer、AuthorizationKernel constructor、projection session、ActionPlane或release promotion。 |
| Trace redaction | provider error、credential、source body、denied ID在ordinary logs/metrics/ContextRun=0；只保留closed category/digest与authorized lineage。 |

**测试 oracle。** 除表中套件外，做 metamorphic tests：重排 provider page input仍按 canonical order输出；同名 backend的 `delete_by_metadata_field` 不算 conformance，必须对“删除后Runtime立即不可见”证明由 tombstone/epoch而非index delete完成；filter支持必须用 cross-org混入与 underfilled ANN fixtures证明候选仍过Kernel，不能以 adapter self-report验收。

**验证命令。** DTO/compiler/registry/twin：`make lint && make typecheck && make test && make catalog`。third-party governance（若实际复制任何 permissive subtree）：`make third-party-check && make third-party-artifacts`。真实 connector sandbox另有其 owning make target后才能升 tier；当前数据库 seam用 `make db-up && make integration && make security-gate`，结束 `make db-down`。

**工作量与依赖。** shared contract/conformance harness 8–12 engineer-days；每个新 connector的 twin + mapping + failure matrix另计 5–10天（不含 source API本身）。依赖 ADR-0075 runner serialization、ADR-0078 per-connector ownership、WorkerLease/ChangePage DTO、SourceVersion CapabilityDeclaration和credential broker。不要创建已被 ADR-0078 dissolved 的 `contract_kit/`。

### 3.4 Workflow / REST / SDK / MCP → 一个 sealed Runtime 产品对象

**上游路径与观察。** Workflow node将 dataset ids、single/multiple config、metadata filters与query/attachments组合成 retrieval request，输出含 dataset/document/segment名字、score、hit count、hash、child content、raw doc metadata的 `Source[]`（[`knowledge_retrieval_node.py#L184-L294`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/workflow/nodes/knowledge_retrieval/knowledge_retrieval_node.py#L184-L294)、[`retrieval.py#L12-L82`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/workflow/nodes/knowledge_retrieval/retrieval.py#L12-L82)）。Service API同时暴露 hit-test/retrieve；SDK把 KnowledgeBase CRUD做成产品面；MCP以 App server/JSON-RPC提供另一入口（[`mcp.py#L44-L96`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/controllers/mcp/mcp.py#L44-L96)）。可学习的是“retrieval config/node/run是用户可见产品对象”；不可继承多套auth/输出语义。

**本仓 seam / ADR。** ADR-0017 closed access set；ADR-0047唯一公开 `POST /v0/resolve` + hidden bridge同handler；ADR-0048 generated SDK facade；ADR-0031 ContextRun；engine output固定 ContextPackage；MCP目前 `NOT_ACTIVE`。

**Room-A 行为规格。** 产品对象分三层，只有一条 domain执行：

1. **Configuration object。** AgentVersion/RuntimeProfile持有 server-validated retrieval strategy、source delegation ceiling、profile refs和PackageBudget ceiling。它可被Control/Release UI展示和version，但不是Principal或grant；发布仅由ContextLearning promote active ReleaseManifest。
2. **Invocation object。** 所有 activated ingress只构造 `AuthenticatedInvocation + TrustedDeliveryContext + ResolveWire`。body是 closed `Acquire | Continue | OpenCitation`：Acquire只含need、optional smaller budget、optional narrowing；Organization/Principal/Membership/purpose/audience/ACL/source mode不得在body。远程Bot只携带authenticated metadata中的opaque `DeliveryEvidenceRef`。
3. **Result object。** 唯一内容输出是 `ResolutionOutcome.Resolved(ContextPackage)`；Package含opaque package/run/decision refs、audience digest、policy snapshot/epoch、release/profile/tokenizer/schema lineage、asOf/expiry、budget usage、authorized Blocks ↔ Evidence一一闭合、citations/coverage/gaps和package digest。它不返回raw dataset/source/document names、pre-auth score/hit count、denied details或plain context string。ContextRun在同UserActor transaction、response前以digest-only授权 lineage提交。

HTTP `/v0/resolve`、generated TS SDK、loopback consumer、BotDelivery和未来 MCP都调用同一个 `ContextRuntime.resolve` composition。Control hit-test若存在也必须用HTTP/generated SDK最高public seam获取Package，不能直接调CandidateIndex/formatter。MCP只有在真实caller、trusted-context construction、Package/error parity、unknown/denied convergence、metadata-injection negative tests完成后激活；在此前 server能力报告和状态继续为 `NOT_ACTIVE`。

**ContextEngine 接口形状草图。**

```python
class ContextRuntime:
    def resolve(
        self,
        invocation: AuthenticatedInvocation,
        delivery: TrustedDeliveryContext,
        request: Acquire | Continue | OpenCitation,
    ) -> ResolutionOutcome: ...

# generated semantic types come from frozen OpenAPI; facade does not handwrite ResolveWire
export interface ContextEngineClient {
  resolve(args: {
    requestId: string
    deliveryEvidenceRef?: string
    request: ResolveWire
  }): Promise<ResolutionOutcome>
}
```

SDK facade constructor只接受base URL与transport authentication；不导出generated implementation subpath、generic fetch client、arbitrary headers、raw request method或handwritten Package schema。Future MCP adapter只做 protocol decode/encode + trusted ingress redemption，然后调用同一 `resolve`；不得生成MCP特有授权或source-rich result。

**失败模式。** malformed/unknown union/duplicate singleton headers在trusted context前以frozen generic wire error失败且content I/O=0；auth/evidence redemption失败generic且I/O=0；known but inactive capability在candidate/source I/O前closed unavailable；policy denied/missing/cross-org candidate收敛为canonical empty Package；ContextRun commit失败则不返回成功/decisionRef；SDK transport failure与closed HTTP domain outcome是不同union；MCP notification/protocol错误未来不得绕过Runtime。

**测试 oracle。**

- server OpenAPI只有一个 public resolve operation；hidden bridge与v0 handler/composition对象identity相同，不能注入第二Kernel。
- HTTP、installed npm tarball client、loopback consumer对同seeded Acquire返回相同Package security fields/digest；transport失败与domain unavailable可区分。
- compile-negative：body加organization/principal/purpose/audience/ACL/unknown variant失败；SDK加raw header、deep import generated client、generic request method编译失败。
- valid empty/authorized Package在response前都有同org durable ContextRun；commit fault返回generic unavailable且没有optimistic decisionRef。
- workflow/Bot consumer只能导入SDK package root；repo scan禁止engine internal/CandidateIndex/ORM imports；每个Block保留exact Evidence ref。
- future MCP parity gate逐字段比较Package/error；wrong service/destination/request DeliveryEvidenceRef与caller-authoredtrusted fields均在content I/O前失败。

**验证命令。** `make openapi-check && make openapi-breaking-check && make sdk-check && make sdk-build && make sdk-test && make sdk-pack && make smoke`；consumer/Bot：`make bot-build && make bot-test && make ui-build && make ui-test`；真实authorized wire需 `make db-up && make integration && make security-gate` 后 `make db-down`。

**工作量与依赖。** 当前HTTP/OpenAPI/SDK已激活，补 retrieval product object与consumer parity约4–6 engineer-days；MCP不计入当前实现，未来activation 8–12天并需独立ADR/threat-model/evidence。依赖 production authentication另行激活、DeliveryEvidenceRef redemption、frozen Package schema、ContextRun commit-before-response和PackageBudget。

### 3.5 MIT SDK 子树 → 不复制，守住 generated client

**上游路径与观察。** 许可证据见第1节。产品形状上，Node client手写routes/types，root导出generic `HttpClient`和base `sendRequest`；KnowledgeBase client把Dataset/Document/Segment CRUD与retrieval status捆成宽SDK。固定 commit 的PHP子树许可边界未充分核验。以上不形成可复制架构价值。

**本仓 seam / ADR。** ADR-0047 immutable OpenAPI v0；ADR-0048 pinned generator + closed facade + generated-tree digest + package consumer test；ADR-0074 third-party registration仅在实际copy时适用。

**Room-A 行为规格。** Room-B不实现Dify SDK，不创建`third_party/dify`。保持以下 generation contract：

1. `openapi/v0/openapi.json` 是historical immutable semantic source，已有目录不能覆盖；server schema与snapshot structural equality/checksum必须相等。
2. pinned Node/npm/generator/TypeScript versions从clean temp dir生成；file set、每字节、tree digest和bundled OpenAPI checksum任一漂移即失败。
3. public package export map只开放facade与checksum；generated implementation不可deep import。
4. facade只接收base URL、transport auth、request id、optional opaque DeliveryEvidenceRef和generated `ResolveWire`；任意header/endpoint/request-option通道不存在。
5. npm tarball在clean temp consumer安装、compile、调用real local HTTP；tarball包含所需contract/license/SBOM材料且cache不入包。
6. Continue/OpenCitation可被generated类型表达但未激活carrier时只返回closed unavailable；codegen存在不等于capability active。

**接口形状草图。** 延续3.4的 `ContextEngineClient.resolve`；语义类型必须 `export type { ResolveWire, ResolutionOutcome, ContextPackage } from <generated-internal>` 的受控再导出，facade不得重新声明字段。

**测试 oracle。** 修改snapshot/server/generated任一侧、generator version、export map、tree digest、checksum都使对应gate红；tarball consumer无法import internal generated path或构造trusted body/header；packed client对authorized seeded Package recompute digest成功；`npm pack --dry-run` file list不含credential/cache/source map（除非明确批准）且包含contract/license notices。

**验证命令。** `make openapi-check && make openapi-breaking-check && make sdk-generate && make sdk-check && make sdk-build && make sdk-test && make sdk-pack`。若未来真的copy，额外 `make third-party-check && make third-party-artifacts && make build`；当前结论none，因此不要创建登记。

**工作量与依赖。** 0 engineer-days copy工作；1–2天用于将本报告的negative oracle补进现有SDK tests（若当前未覆盖generic header/client export）。依赖ADR-0048与pinned toolchain；不依赖Dify代码或package。

### 3.6 Trace / observability → authorized-only digest lineage

**上游路径与观察。** Dify retrieval完成后可更新hit count并将完整`Document`交给trace task（[`dataset_retrieval.py#L889-L1009`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/rag/retrieval/dataset_retrieval.py#L889-L1009)）；query audit在独立事务中保存raw query/attachment id（[`#L1029-L1082`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/rag/retrieval/dataset_retrieval.py#L1029-L1082)）。trace manager将inputs、full documents、tenant/app/user/display names、embedding/rerank model写入trace info（[`ops_trace_manager.py#L1094-L1180`](https://github.com/langgenius/dify/blob/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5/api/core/ops/ops_trace_manager.py#L1094-L1180)），再由provider map适配多种trace backend。它证明operator想看latency/model/profile/result的产品价值，但其payload不适合ContextEngine。

**本仓 seam / ADR。** ADR-0031 authorized-only ContextRun + seven-field restricted DecisionAudit；ADR-0076 refused rank evidence不外露；Implementation Design §7.3；ContextRun query只存organization-bound keyed digest，Package retention固定digest-only。

**Room-A 行为规格。** 观测拆为三个不同可见面：

1. **Tenant-visible / Learning-safe ContextRun（只在成功形成并交付Package时写）。** 可存：opaque run/decision/auth/request refs；trusted purpose与audience digest；PolicySnapshot ref/epoch/asOf；effective-scope digest；active ReleaseManifest generation/digest和Content/Index/Runtime/Curation/tokenizer/Package schema refs；effective/used PackageBudget；stage timing/call/cost aggregate；terminal `delivered_authorized | delivered_empty`；selected authorized Evidence refs；Package digest/retention mode；accepted/finalized/expiry。raw query只存versioned Organization-bound HMAC digest和key version。
2. **Restricted DecisionAudit。** 对 `delivered_empty` 当前只存 Organization/run/decision、PolicySnapshot/epoch、closed `no_authorized_evidence`、recorded_at；不存query/digest、Candidate/Fragment/Resource ref/body/name/score、denial reason或candidate/denied count。其他pre-auth transport失败不伪造ContextRun。
3. **Ordinary operator metrics/logs。** 只允许content-free aggregate：resolve outcome class、stage latency buckets、budget usage、provider call outcome category、lease/retry phase、active profile/ref digest、source status watermarks/refusal category。labels不能含Organization/user/source/resource/fragment raw id、query、title/path/body、credential、opaque token或high-cardinality denied detail。跨Organization aggregate必须经过另一个明确privacy/retention decision，当前不默认激活。

**operator-visible surviving signals。** `runRef/decisionRef`（需exact authorized read seam）、time range/latency、delivered authorized或canonical empty、authorized Evidence count/refs、Package digest、budget effective/used、release/profile/tokenizer lineage、policy epoch/snapshot、audience digest、closed provider/Runtime outcome、source acquisition/publish watermarks和closed current compilation refusal。**不 survive**：raw query、prompt、Package/body副本、pre-auth scores/order、denied/missing object identity/count/reason差异、source/document display name、workspace/user name、credential/model secret、DeliveryEvidenceRef/ContextAccessTicket/ActionTicket/WorkerLease bearer。

**接口形状草图。**

```python
@dataclass(frozen=True, slots=True)
class AuthorizedRunObservation:
    run_ref: ContextRunRef
    decision_ref: DecisionRef
    terminal_outcome: Literal["delivered_authorized", "delivered_empty"]
    policy_snapshot_ref: PolicySnapshotRef
    policy_epoch: int
    audience_digest: Digest
    release: ReleaseObservation
    budget: BudgetObservation
    stage_metrics: tuple[ContentFreeStageMetric, ...]
    authorized_evidence_refs: tuple[EvidenceRef, ...]
    package_digest: Digest
    query_digest: KeyedDigest

class ContextRunWriter(Protocol):
    def append_before_delivery(
        self, observation: AuthorizedRunObservation, tx: CurrentUserActorTransaction
    ) -> DurableRunReceipt: ...
```

只有sealed Runtime构造该DTO；candidate index、provider、trace adapter不能调用writer。External telemetry exporter若未来存在，只能接受另一个经过allowlist projection的content-free DTO，不能接收ContextRun ORM row或Package。

**测试 oracle。**

- canary strings放入query、authorized body、denied body、path/title、credential/token；ContextRun只命中query HMAC与authorized Evidence refs，ordinary log/metric/trace sink命中正文/secret=0，DecisionAudit全部canary=0。
- same-org denied/cross-org/missing candidate三种empty运行的public Package和restricted audit category相同（只归一化server refs/times/digest）；不做timing equivalence宣称。
- authorized run的Evidence refs与Package Blocks一一闭合；Package digest可重算；digest-only retention中不存在Package JSON/body副本。
- query相同但Organization不同，HMAC digest不同；key version rotation改变comparison domain；无key/default secret时fail closed。
- ContextRun insert/commit fault：HTTP成功响应=0；unauthenticated/malformed request ContextRun=0。
- refused candidate的rank/score加入hostile fixture后，tenant-visible/operator output中该rank/score=0；authorized排序仍正确。
- metric cardinality test拒绝raw ids/query/path作为labels；provider exporter异常不影响authorization，也不能导致raw fallback logging。

**验证命令。** `make lint && make typecheck && make test && make catalog && make smoke`；真实RLS、commit-before-response、redaction和security operator exact-read需 `make db-up && make integration && make security-gate`，结束 `make db-down`。如新增telemetry artifact，必须将它纳入catalog与secret/redaction tests后再声明active。

**工作量与依赖。** 5–8 engineer-days（safe stage metrics 2–3，export projection/cardinality guard 1–2，redaction/adversarial suite 2–3）。依赖现有ADR-0031 schema/writer、retained UserActor transaction、Package digest、release observation、PackageBudgetMeter；production operator identity、retention/export/delete policy仍是独立前置，不可由trace provider配置代替。

## 4. 不可借鉴清单与必须杀死的隐含前提

| 学习行为 | ContextEngine 独立实现 | 必须杀死的隐含前提 |
|---|---|---|
| 可观察 parsing/splitting/indexing phase | Revision三态 + job checkpoint/events + dual watermarks + content-free Control status | 一个mutable Document status同时能表达execution、visibility、retry和authorization。 |
| commit后异步dispatch | acceptance/outbox/job同事务，scheduler从durable state claim | DB commit后直接queue dispatch永不丢；broker ack等于business commit。 |
| retry前clean旧segments/vector | build immutable new Revision，旧active保留到CAS activation；cleanup异步 | 重建中短暂无正文/混合索引对在线读者可接受。 |
| exception text写Document.error | closed refusal category + restricted diagnostics另行决策 | parser/provider异常适合tenant/operator普遍展示或长期保留。 |
| Dataset tenant ownership过滤 | current Membership + Agent ceiling + ArticleAccessPolicy + SourceAclEvidence + purpose + audience + field projection交集 | tenant/dataset同属天然等于每个Principal/audience可读。 |
| LLM router选择dataset | router只在server-derived AuthorizedSourceCapabilitySet内收窄 | model输出是可信source authority；未知选择可回退到全库。 |
| metadata filter先缩小documents | optional request narrowing只缩小EffectiveScope；exact auth仍逐Article执行 | filter命中/索引ACL足以授权，missing filter可视为unrestricted。 |
| keyword/vector/full-text并行 | content-free CandidateRef + RankEvidence，deterministic RRF | 检索返回的page_content/title/path可在授权前供fusion/debug。 |
| score threshold / top-k | rank与auth隔离，授权后exact rejoin和budget selection | 分数高可抵消授权失败；top-k之外无需考虑side channel或underfill。 |
| retrieval formatter水合Segment/Child/Summary | CandidateRef先过Kernel；same Article/current Revision lineage核验，cross Article重授权 | 首个child hit的许可自动覆盖parent、neighbor、attachment、summary或另一Article。 |
| rerank/fusion读取Document body | pre-Kernel仅ref fusion；正文rerank只接AuthorizedProjection | 相关性模型是“内部服务”所以可以先看denied bytes。 |
| newline concat context | expiring、audience-bound、budgeted ContextPackage + Evidence closure | 裸context string足以表达授权、provenance、TTL、revocation和citation。 |
| Workflow输出dataset/document/segment metadata | Package只给opaque、安全必需、authorized lineage；ContextRun digest-only | source metadata越丰富越可观测，不会泄露existence、names、ranks或ACL。 |
| REST hit-test另调retriever | Control/UI也通过唯一public resolve seam | “测试”路径可以弱化auth、budget、audit或输出更多denied详情。 |
| 每transport单独封装retrieval | HTTP/generated SDK/future MCP都映射同一sealed Runtime | SDK或MCP是新的domain implementation，可各自filter/auth。 |
| caller body携带tenant/user/dataset | ingress构造AuthenticatedInvocation/TrustedDeliveryContext；body closed | trusted identity/audience/purpose可以由caller自报并在service层校验。 |
| MCP App server直接激活 | 保持NOT_ACTIVE直到真实caller与完整parity/security gate | protocol支持存在即等于安全产品能力存在。 |
| broad BaseIndexProcessor | pure compiler + runner + publication分别typed | extract/transform/load/clean/index同一base class仍是deep module，session/ORM不会泄漏authority。 |
| vector backend同名CRUD/search | V1固定PG；每个真实adapter跑semantic conformance | 同名`filter/delete/search`具有相同tenant、atomicity、score、consistency和failure semantics。 |
| provider返回empty表示各种失败 | closed ProviderOutcome；Unsupported/Unavailable/Denied不变empty success | 空列表可安全吞掉credential、ACL、checkpoint、backend故障。 |
| connector返回checkpoint | engine整页durable accept后才签发continuation | provider观察/返回cursor等于engine已接受或内容已发布。 |
| connector delete | content-free observation → sole Control tombstone authority重验 | source adapter/index delete本身能决定Runtime不可见与epoch。 |
| trace记录raw query/full documents | Org-bound keyed query digest + authorized Evidence refs + digest-only Package | observability天然是受信面，保存输入/输出不会成为第二内容库。 |
| trace记录dataset/user/workspace/model字段 | allowlisted content-free metrics与profile digests | display name/raw id是低风险label；credential lookup失败可fallback raw。 |
| hit_count副作用 | 若需要只对authorized selected Evidence、同transaction/明确retention记录aggregate | candidate命中即是可见/可学习事件；异步副事务失败不影响lineage真实性。 |
| root code看似Apache | Dify root永远Room-A clean-room；只看exact separately licensed regions | 去掉附加条件后可当普通Apache复制；产品级license可替代路径级核验。 |
| MIT Node SDK可复制 | 继续OpenAPI-generated closed facade；copy candidate none | permissive许可自动意味着架构适配且维护成本更低。 |
| PHP README写MIT | 保持[未取证]并不复制 | README一句license声明等于exact path license region与完整notice链。 |

## 5. 推荐实现顺序 + 给 coordinator 的开放问题

### 推荐顺序

1. **先冻结 oracle 与 capability graph（2–3天）。** 把第3.2的“pre-Kernel正文=0”、第3.3 per-connector conformance、第3.6 redaction canary写入catalog/contract tests；保持所有新carrier `NOT_ACTIVE`。这是Room-B唯一允许的Dify观察输入。
2. **补齐 Supply publication/status 投影（6–9天）。** 在现有File replacement/recovery/lease基础上确认accept+outbox原子性、closed operational phases和每durable boundary crash tests；不增加Revision状态，不泛化ProviderPort claim。
3. **固定 planner DTO和AuthorizedSourceCapabilitySet（3–4天）。** 只实现server-derived capability narrowing、ROUTE_ONE deterministic twin、unsupported/failure semantics；router model默认无carrier。
4. **实现bounded FAN_OUT + content-free RRF（4–5天）。** 使用ADR-0081 data-only session；保留rank evidence但Kernel rank-blind；先用FTS/pgvector现有PG实现，不引入vector factory。
5. **实现授权后rank/rerank/expansion（5–8天）。** exact rank rejoin、same-Article/current-Revision lineage、cross-Article reauth、PackageBudgetMeter；rerank twin先contract-verified，真实model须ablation/egress后另激活。
6. **收口产品面（4–6天）。** Workflow/UI/Bot只经installed generated SDK消费Package；补compile-negative/generic-header禁止与transport parity；不做Dify SDK迁移。
7. **最后补safe observability（5–8天）。** 先ContextRun allowlist/stage aggregate，再external exporter projection；redaction/retention/operator identity未闭合前不输出raw trace。
8. **MCP继续不排期。** 只有真实caller证明HTTP/SDK不能满足protocol需要时，另立ADR和8–12天activation slice；不得把Dify App MCP结构当捷径。

总增量约 **29–43 engineer-days**（不含MCP、live connector、production auth和外部等待）；可按 `Supply status` 与 `Runtime planner` 两条不共享文件的工作流并行，但共同合入前必须跑一次最高public seam + real PostgreSQL security gate。

### 给 coordinator 的开放问题

1. **Planner的第一个真实pulling workload是什么？** 当前STATUS只激活loopback single-Membership File pgvector Acquire；是否先交付“单Source lexical+vector hybrid”，还是直接要求多Source fan-out？后者会把AuthorizedSourceCapabilitySet、cross-source budget和failure policy一起提前。
2. **ROUTE_ONE是否允许模型router？** 建议首版只用deterministic rule/twin并把model router `NOT_ACTIVE`；若确有自然语言source routing需求，需要确定ModelGateway/EgressGrant、profile、budget和fallback语义的owning ADR。
3. **Required branch失败是整次unavailable，还是允许profile-declared partial Package gap？** 本报告默认V0整次fail closed，避免silent quality downgrade。若业务必须partial，需预先冻结哪些branch可选、gap wire字段、Quality/Budget gate和不泄露source existence的归一化。
4. **Source operational status要不要增加per-job opaque view？** ADR-0072已允许source aggregate与current refused paths；本报告允许terminal phase按opaque job ref读取。若UI只需source-level视图，应删掉per-job surface以减少枚举面。
5. **Authorized rank/timing保留到什么层级？** 建议ContextRun只存selected authorized refs与stage aggregate，不存逐Evidence raw score；若evaluation要逐项rank，需独立retention/privacy schema，且refused rank永不进入。
6. **是否要把“Dify SDK copy candidate none”记录为长期决策？** ADR-0048已实质覆盖；建议不新增ADR，只在未来有人提议迁移Dify SDK时以本报告和ADR-0048拒绝，避免为“未做的copy”制造normative artifact。
7. **PHP subtree是否值得进一步legal取证？** 当前产品无PHP SDK需求，建议保持 `[未取证]` 且不投入；只有出现明确caller后再向upstream/license owner核实exact path grant并固定独立repo/commit。
8. **公开材料如何引用本报告？** 建议答案是“不引用”。任何可公开的Dify结构主张回到四仓evidence baseline；本报告留在maintainer-local research边界，Room-B提交只引用本仓requirements/ADR/tests，不把Dify当安全guarantee或public provenance。
