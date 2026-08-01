# Onyx → ContextEngine 可复刻蓝图评估（ADR-0075 lift plan）

> **决策状态**：本文开放问题已由维护者于 2026-07-31 全部决定（D2/D3/D4），结果见 [`five-repository-implementation-blueprint.md`](./2026-07-31-five-repository-implementation-blueprint.md) §5；正文推荐项为评估时刻的状态。

# 1. 固定 commit 与许可证核验

本评估只针对 Onyx commit [`2fb3dd10493b3883870fa8adced5b1a0e114feff`](https://github.com/onyx-dot-app/onyx/commit/2fb3dd10493b3883870fa8adced5b1a0e114feff)。已在 `/tmp/onyx-bp` 以 detached HEAD 核验 `git rev-parse HEAD` 等于该值；以下 permalink 均固定到该 commit，而不是浮动分支。

根 [`LICENSE`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/LICENSE) 明确规定：所有 `ee` 目录内内容受 Onyx Enterprise License 约束，目录外内容适用 MIT Expat。固定 checkout 中存在的 `ee` 根为 `backend/ee`、`backend/tests/external_dependency_unit/ee`、`backend/tests/unit/ee`、`web/src/app/ee`、`web/src/ee`；六个 lift 的拟议源文件均不位于这些目录。对拟议目录做嵌套 `LICENSE`/`NOTICE`/`COPYING` 静态扫描，未发现会改变这些文件许可证边界的嵌套文本。[一手静态] 这证明固定 checkout 的路径边界；不把它扩大为对未来 commit 或未扫描依赖的许可证结论。

| 拟议源路径 | 本次用途 | 固定 checkout SHA-256 | 边界结论 |
|---|---|---|---|
| `backend/onyx/tools/tool_implementations/search/search_utils.py` | weighted RRF、邻接/全量扩展、重叠合并 | `393b5909f1b40784eac1babd6a8836a003d18f272bf0a35d4bb4970211b28943` | 非 `ee/`，MIT；只有切割后的函数可考虑 copy+patch |
| `backend/onyx/tools/tool_implementations/search/search_tool.py` | query 去重、budget trim、检索编排 | `00d486a15705f1855f2d06ea898bb22c6d5eae16f09b2433b2376c5c3d329c43` | 非 `ee/`，MIT；整文件不得复制，依赖与正文面过宽 |
| `backend/onyx/context/search/retrieval/search_runner.py` | hybrid runner 接口行为 | `846e4f16e142355ae3f37544de33196e5f8e20fc3dfbee6eebf2e54587534587` | 非 `ee/`，MIT；实现绑定 Onyx index，不能移植 |
| `backend/onyx/document_index/interfaces_new.py` | `HybridCapable` 接口形状 | `2285d0cbedf91b109f9484325a769872dc520529c81ab50f65d8630bc1339576` | 非 `ee/`，MIT；可只复制并收窄 ABC 形状 |
| `backend/onyx/context/search/models.py` | hybrid request/expansion enum 形状 | `5732486e4e44a6b337cf5b732934666b946b96d2cf2cc8ffa81d992dc2dab4c0` | 非 `ee/`，MIT；含 `bypass_acl` 等禁用语义，不整文件复制 |
| `backend/onyx/context/search/pipeline.py` | 相邻 section 合并的行为 oracle | `d662115ed3c3fcc6256a58ea8e03f3bf692342d3ca0e7f88953dfcdc9adad584` | 非 `ee/`，MIT；函数会动态调用企业后处理，故只取无 `ee` 的局部行为 oracle |
| `backend/onyx/secondary_llm_flows/query_expansion.py` | semantic/keyword rewrite | `3639371ee18301ab209fa73682ddc227466a20ee4ceaa84f10a3a49c4b828a06` | 非 `ee/`，MIT；模型调用必须改接治理端口 |
| `backend/onyx/secondary_llm_flows/document_filter.py` | relevance selection/expansion classification | `9634888840574ac8d0c264dcf308b7d73ef0c489730b3090008526da27a2961d` | 非 `ee/`，MIT；正文消费者只能位于 Kernel 后 |
| `backend/onyx/natural_language_processing/search_nlp_models.py` | cross-encoder/API rerank 行为 | `539441523f90e0c8c5be2a7be833c8c9d0f2c65273548002640dea99914e19c9` | 非 `ee/`，MIT；provider/credential/client 实现不复制 |
| `backend/onyx/natural_language_processing/utils.py` | token count/trim helpers | `8df3950e61c41023992d9c149220c69b918454771588b29b13293fb6cbcc2bb4` | 非 `ee/`，MIT；best-effort token 语义不能成为硬预算实现 |

企业权限同步编排不进入任何 `source_paths`。它只允许经 ADR-0074 的两室流程形成行为规格和测试 oracle；本报告不提供、复述或建议复制 `backend/ee/**` 实现。现有 `third_party/onyx/UPSTREAM.toml` 已注册 connector framework 的四个文件，且 `excluded_paths` 已含 `backend/ee`、`web/src/app/ee`、`web/src/ee`；本报告不重新提议该框架，也不把它误计为六个 retrieval lift 的新增工作。

# 2. 六个 lift 的状态盘点

三条 keystone seam 不是绿地：Supply execution/checkpoint bridge 已由 `engine/persistence/supply_execution.py`、`adapters/connectors/file.py` 和 connector-runner 落地；content-free candidate/authorized-fragment access port 已由 `CandidateDiscoverySession`、`CandidateIndex`、`AuthorizationKernel`、`FragmentWindowSession` 落地；governed model-inference port 已由 `engine/runtime/model_inference.py` 与共享 `PackageBudgetMeter` 落地。第三条 seam 只有端口和单元/HTTP-PG 证明，`STATUS.md` 明确所有 Runtime `rewrite/rerank/select` carrier 仍为 `NOT_ACTIVE`。

| 固定顺序 | lift | 仓内状态 | 已有证据/实现 | 仍缺的可交付工作 |
|---:|---|---|---|---|
| 1 | weighted RRF/dedupe | **核心已完成** | `fuse_candidate_evidence` 做 exact-ref 去重并携带 content-free rank evidence；`join_authorized_ranking` 在授权后压紧位置并应用 server-owned weights；ADR-0083 已禁止 pre-Kernel weighting | 用固定 Onyx oracle 做差分用例；不要再复制一个第二 fusion authority |
| 2 | hybrid retrieval adapter | **实现与真实 PG/HTTP 测试已完成，通用 served activation 仍受限** | `PostgreSQLFtsCandidateIndex` + `PostgreSQLVectorCandidateIndex` + `PostgreSQLHybridCandidateIndex`；FTS/pgvector SQL 原生执行于 retained UserActor transaction；`tests/integration/test_rank_blind_kernel_hybrid.py`、`test_hybrid_path_sealed_http.py` | 只需补接口 provenance、基准与明确的 production activation；外部 query embedding 仍需计量/不可隐式启用 |
| 3 | same-Article expansion | **安全 seam 已完成，resolve 编排未接通** | `AuthorizationKernel.expand_fragment_window`、`PostgreSQLFragmentWindowReader`、同 Article/current Revision 检查、跨 Article `reauthorization_refs`；unit + real PG tests 已有 | 将 expansion plan 接入 sealed Runtime 顺序、把跨 Article refs 回送 Kernel、再做预算与 HTTP 最高 seam 验证 |
| 4 | query rewrite | **端口能力已完成，carrier 编排绿地** | `RewriteModelRequest`/`ModelInferencePort.rewrite` 已有闭合 output、grant、timeout、usage；无 Runtime 调用方 | 固定 profile、输入来源、rewrite 数量/长度、失败策略、共享 meter、grant issuance 与最终 package usage |
| 5 | authorized rerank | **类型/治理端口已完成，正式 carrier 绿地且有 ADR 类型冲突待决** | `RerankModelRequest` 只接 `AuthorizedProjection`，输出必须是精确 permutation；HTTP/PG seam 已证明 raw CandidateRef 不到达 port | 接入 resolve、决定 ADR-0052 `AuthorizedModelInput` 与 Runtime `AuthorizedProjection` 的合法桥接、共享 meter、最终 package usage 和安全 gate |
| 6 | budgeting helpers | **基础 meter 已完成，端到端累计发布未完成** | `PackageBudget`/`PackageBudgetMeter` 原子 reserve/commit/cancel；当前 Package 构造仍直接计算 block UTF-8 bytes，并把 provider/cost/elapsed 写成 0 | 建立每 resolve 单一 meter，rewrite/rerank/expansion/assembly 共用并把累计 usage 写入最终 Package；替换 stage-local/ad-hoc 计数 |

因此“照 lift 顺序实现”在当前分支上的含义不是重写 1→6，而是按同一顺序关闭每个 lift 的剩余 gate。特别是 lift 1、2 不应因为评估发现 Onyx 函数而退回上游数据模型或索引。

# 3. 逐 lift 蓝图

## 3.1 Lift 1 — weighted RRF/dedupe

### 上游函数路径与 permalink

- [`weighted_reciprocal_rank_fusion`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/tools/tool_implementations/search/search_utils.py#L28-L111)：按 `id_extractor` 去重，计算 `weight / (k + rank)`，以 score、首次 source rank、source index 稳定排序。
- [`deduplicate_queries`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/tools/tool_implementations/search/search_tool.py#L159-L180)：大小写不敏感合并 query，保留首次 casing 并累加 weight；这是 lift 4 的输入 oracle，也能固定 lift 1 的重复列表语义。
- [`test_search_utils.py`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/tests/unit/onyx/tools/test_search_utils.py#L25-L337)：空列表、重复、权重、tie-break 等上游 oracle。

### Kernel 切割线

上游函数 28–96 的 exact ID 去重、rank position 采集是 content-free，可映射到 pre-Kernel `CandidateRef` + `RankerEvidence`；不得携带上游 `InferenceChunk` 本体、title、content、metadata 或 score explanation。上游 83–111 的 **weighted** fused order 不能成为 pre-Kernel 有效决策：ADR-0083 已固定权重只在 Kernel 后按已授权集合压紧 rank position 后计算。Runtime 在 `fuse_candidate_evidence` 后必须把 refs 按 canonical `_candidate_sort_key` 排序送入 Kernel；只有 `join_authorized_ranking(AuthorizedProjection[], rank_evidence, server_weights)` 能形成下游顺序。denied ref 的 evidence 在 join 时丢弃，且不得影响 position compact、budget selection 或可见 trace。

### copy+patch vs 原生重写决定与理由

**决定：不新增 vendored runtime code；保留本仓原生实现，并把上游测试作为差分 oracle。** 上游 generic function 本身是 MIT、可复制，但它把 weight 放在授权前集合上，且 tie-break 受 denied candidates 的初始位置影响；复制后再把权重移到 Kernel 后会只剩算法名字相同。当前 `fuse_candidate_evidence` + `join_authorized_ranking` 已直接编码 ADR-0076/0083，新增 vendored helper 只会制造第二 fusion authority。

### 复刻配方

1. 保留 `CandidateQuery(ranked_lists)`，在 seam 入口重新运行 `require_bounded_candidate_submission`，拒绝超过 server-owned candidate/list ceiling 的 hostile object。
2. 对每个 ranker 内以 exact `CandidateRef` 去重，保存第一次 position 与 optional finite score；输出 `FusedCandidates(candidate_refs, rank_evidence)`，不输出正文或 ACL。
3. 将 `candidate_refs` canonical sort 后送入 sealed Kernel；rank evidence 不进 Kernel。
4. Kernel 返回 `AuthorizationDecision.projections` 后，按 exact CandidateRef join；只对 admitted refs 为每个 ranker 重排连续 `1..N` position。
5. 从 server-owned `RankerWeights` 读取权重，绝不接受 request 字段；计算 authorized-only fusion，稳定 tie-break 使用 `_candidate_sort_key`。
6. 后续 selector、rerank、budget pack、assembler 只消费 `AuthorizedRerankItem` 顺序。没有 evidence 的授权 projection 使用 neutral rank；denied evidence 不进入 tenant-visible output。

### 测试 oracle

- 功能：同 ref 跨 ranker 只输出一次；同 ranker 重复只计首次；输入 permutation 在同 rank evidence 下给稳定 exact-ref tie-break；缺 rank evidence 的 projection 得 neutral rank；权重缺项、非有限、非正数拒绝。
- 安全：向两个合法 ranker 列表插入任意数量/位置的 denied 与 cross-Organization refs，授权后的合法顺序、选择和 Package bytes 必须与未插入时一致；Kernel 输入不含 rank；denied content 到 rerank/assembler 为 0。
- 资源：在第一次 `locate()` 前拒绝超 bound submission 和伪造 `object.__new__` DTO。

### third_party 注册计划

无新增 `UPSTREAM.toml` 条目。若仅导入上游 oracle fixture，也必须作为测试派生物登记 `backend/tests/unit/onyx/tools/test_search_utils.py` 的 source hash、vendored fixture hash 与修改说明；推荐直接重写等价 fixture，避免把 Onyx `InferenceChunk` test model 引入本仓。

### 工作量与依赖

增量 **1–2 engineer-days**（oracle 补齐、provenance 注释与回归），原始绿地约 4–6 天。依赖 content-free ranked-candidate/authorized-fragment port；不依赖 Supply bridge 或 governed model port。

## 3.2 Lift 2 — hybrid retrieval adapter

### 上游函数路径与 permalink

- [`HybridCapable.hybrid_retrieval` / `keyword_retrieval`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/document_index/interfaces_new.py#L365-L425)：hybrid/keyword adapter 的输入、bounded top-N 与 score-ranked 输出形状。
- [`_embed_and_hybrid_search`、`_keyword_search`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/context/search/retrieval/search_runner.py#L52-L87)：query embedding、hybrid/keyword dispatch 的编排行为。
- [`search_chunks`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/context/search/retrieval/search_runner.py#L90-L166) 与 [`combine_retrieval_results`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/context/search/retrieval/search_runner.py#L28-L49)：多 retrieval function 的并发/合并形状。
- [`BasicChunkRequest` / `ChunkIndexRequest`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/context/search/models.py#L142-L169)：query、alpha、limit、filters 的上游 request 形状；其中 `bypass_acl` 语义明确禁止带入。

### Kernel 切割线

只保留以下 pre-Kernel 数据：bounded query plan、query embedding、trusted removal-only scope、每个 native ranker 返回的 `CandidateRef` 和 rank position/score。`InferenceChunk` 的 content/title/metadata、Onyx ACL list、tenant ID、highlight 和 score explanation 均不得跨 candidate seam。Runtime 自己在 retained UserActor transaction 上执行 prepared request，把 primitive refs 放进一次性 `CandidateDiscoverySession`；replaceable index 永远拿不到 connection、projection session、locator 或 projector。Kernel 后才 exact join rank evidence 与 `AuthorizedProjection`。

### copy+patch vs 原生重写决定与理由

**copy+patch：**只抽取 `HybridCapable` 的接口形状，压缩成 dependency-free、content-free 的 runner-side ABC/DTO；删除 `InferenceChunk`、ACL filter、tenant、score-bearing content 和 index management 方法。

**原生重写：**FTS、pgvector、同 transaction execution、scope filter、HNSW settings、ranker composition 全部留在 ContextEngine。绝不复制 Onyx Vespa/OpenSearch 查询、schema、SQL、client 或 deployment。当前 `PostgreSQLFtsCandidateIndex`、`PostgreSQLVectorCandidateIndex`、`PostgreSQLHybridCandidateIndex` 已实现正确方向：FTS 使用 PostgreSQL `websearch_to_tsquery/ts_rank_cd`，vector 使用 pgvector `<=>`，两者只返回 lineage refs。

### 复刻配方

1. 在 `third_party/onyx/retrieval/interfaces.py` 放入经裁剪的 `HybridRetrievalShape.prepare(query, limit, filters)` / `shape_results(content_free_rows)`；其 wire DTO 只允许 opaque refs、position 和有限 score。
2. CE adapter 将 `Acquire` + `CandidateDiscoveryScope` 转为 `HybridDiscoveryRequest(fts, vector)`；每个子 limit 与总 limit 必须小于等于 server submission bound。
3. Runtime 对 request 做 hostile revalidation，然后在 retained `MaterializedProjectionSession` 内执行。FTS SQL只选择 organization/source/resource/revision/fragment；vector SQL同样只选择 lineage，query embedding profile 必须与已发布向量 profile 匹配。
4. FTS/ANN 都先应用 Kernel-computed EffectiveScope 的 removal-only projection及 caller narrowing，再 `LIMIT`；这不是授权，返回 ref 仍逐个过 Kernel。
5. 把两个结果集标成独立 `fts`/`vector` ranked lists，交 lift 1 携带 evidence；禁止在 adapter 内返回融合正文或做授权判断。
6. external query embedding 只有在同一个 resolve-owned budget meter 上计 provider call/cost/elapsed 且 profile identity 被 ReleaseManifest 固定后才能 served；否则只允许 network-free twin 并报告 `NOT_ACTIVE`。

原生实现 skeleton：

```text
prepare_discovery(Acquire, CandidateDiscoveryScope) -> HybridDiscoveryRequest
Runtime.execute(retained_user_actor_tx, request) -> CandidateDiscoverySession
discover(Acquire, data_only_session, scope) -> CandidateQuery[
  RankedCandidateList("fts", CandidateRef...),
  RankedCandidateList("vector", CandidateRef...)
]
canonical refs -> AuthorizationKernel -> AuthorizedProjection
```

### 测试 oracle

- 功能：FTS lexical 命中与 pgvector neighbor 各自稳定；同 ref 双路命中只在 lift 1 合并；limit/total bound 生效；embedding dimension/profile mismatch 在 SQL 前拒绝；deterministic tie-break。
- real dependency：PostgreSQL 17 + pgvector 下验证 FORCE RLS、active Revision、tombstone、source/resource narrowing、selective filter 下 iterative scan；记录 `EXPLAIN ANALYZE`、underfill、exact-vs-ANN recall，不将静态分析写成 benchmark 通过。
- 最高 seam：HTTP `resolve(Acquire)` 证明 `CandidateRef → Kernel → AuthorizedProjection`；在 authorized corpus 混入 same-Org denied 与 cross-Org candidates，denied content 到 rerank/assembler=0，响应与不混入时的合法内容一致。
- capability：关闭 external embedding 时零 network bytes；provider failure 映射统一 content-free unavailable，不回显 query/vector/locator。

### third_party 注册计划

若采用接口形状 copy+patch，在现有 `UPSTREAM.toml.source_paths` 增加：

- `backend/onyx/document_index/interfaces_new.py`，upstream SHA-256 `2285d0cbedf91b109f9484325a769872dc520529c81ab50f65d8630bc1339576`；vendored target `third_party/onyx/retrieval/interfaces.py`。
- 如 DTO 确需来源追踪，再加 `backend/onyx/context/search/models.py`，upstream SHA-256 `5732486e4e44a6b337cf5b732934666b946b96d2cf2cc8ffa81d992dc2dab4c0`；target `third_party/onyx/retrieval/models.py`。

`files[].sha256` 必须记录 **post-patch vendored hash**，原始 hash 写入 `MODIFICATIONS.md`；修改说明列明删除的 index/ACL/content/tenant 方法。`excluded_paths` 保持全部 `ee` 根，并新增明确“不包含 `backend/onyx/document_index/vespa/**`、`opensearch/**`”。若最终继续使用当前 CE-native Protocol，不为“灵感”伪造 vendored 注册。

### 工作量与依赖

现有实现上的增量 **3–5 engineer-days**（接口 provenance 1、benchmark 1–2、activation gate 1–2）；外部 embedding served 另计 3–5 天。依赖 ranked-candidate/authorized-fragment port；governed model port只在 external query embedding 被归入模型推理时依赖；Supply bridge不依赖。

## 3.3 Lift 3 — same-Article expansion

### 上游函数路径与 permalink

- [`_retrieve_adjacent_chunks`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/tools/tool_implementations/search/search_utils.py#L129-L205)：按 document ID 和 chunk range 取邻居。第 149–151 行显式假设初始权限已检查、扩展无需再检查，是必须删除的 anti-pattern。
- [`merge_overlapping_sections`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/tools/tool_implementations/search/search_utils.py#L208-L348)：同 document 相邻/重叠 section 合并并保留首次顺序。
- [`expand_section_with_context`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/tools/tool_implementations/search/search_utils.py#L351-L494)：main/adjacent/full-document 四类 expansion。
- [`merge_individual_chunks`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/context/search/pipeline.py#L130-L244)：按 document/chunk ordinal 合并相邻结果的补充 oracle。

### Kernel 切割线

expansion plan（anchor exact ref、before/after、候选邻居 refs）可 content-free；任何邻居正文读取都必须在 Kernel 内或 Kernel 授予的窄 `FragmentWindowSession` 后。已授权 Article（`ContextResource`）的 **current active Revision** 内，邻居经 organization/source/resource/revision lineage 和 source ACL projection freshness 验证后可继承 Article decision并构造 `AuthorizedProjection`。一旦候选跨 source/resource（即跨 Article），只能输出 content-free `CandidateRef`，回到完整 Kernel 重授权；Fragment 永远没有独立 ACL。revision 不匹配不是跨 Article fallback，而是 stale lineage，拒绝。

### copy+patch vs 原生重写决定与理由

**决定：原生实现；上游只作 behavior oracle，不 vendoring。** 上游函数直接从 `DocumentIndex` 取 content，显式关闭 ACL filter，并以 `document_id` 作为长期权限继承依据；这与 ADR-0077 的 Article/current Revision atom、retained transaction 和 hostile port snapshot verification不兼容。纯 merge 算法虽可复制，但当前 CE window 是 ordinal-sorted exact projections，原生十余行 merge 更易审计且不需要 Onyx content model。

### 复刻配方

1. post-Kernel ranking/selection 先选择 anchor `AuthorizedProjection`；构造 `FragmentWindowRequest(anchor, before<=32, after<=32, expansion_candidates<=64)`。
2. `AuthorizationKernel.expand_fragment_window` 用 anchor locator 在同一 retained UserActor transaction 读取权威 window；SQL join `ContextResource.active_revision_id`、`tombstoned=false`，按 Fragment ordinal 取窗口。
3. 对每一 item 验证 organization/source/resource/revision 全等、source ACL projection ref/as-of 全等、field ceiling 不扩张；从 anchor decision 继承并构造 active `AuthorizedProjection`。
4. replaceable `FragmentWindowReader` 只见一次性 data session；Kernel 对其返回与 authoritative snapshot 做 primitive exact comparison，防内容/lineage mutation。
5. `expansion_candidates` 中 source/resource 不同的 refs 去重后进入 `reauthorization_refs`；同 Article ref 若被错误送去 reauthorize，直接拒绝 seam contract。
6. Runtime 对 `reauthorization_refs` 调用完整 `authorizeAndProject`；成功 projection 才能与 inherited projection 合并。跨 Article denied 详情只进 restricted DecisionAudit category/digest。
7. 最终按 Article+Revision+ordinal 合并重叠窗口，保持首次 authorized rank；然后交共享 budget meter 和 assembler。

### 测试 oracle

- 功能：window 上下界、anchor 在首尾、重叠窗口去重、ordinal 排序、current Revision replacement 后旧 anchor 拒绝、tombstone/disabled source 拒绝。
- lineage：同 Article+same Revision 继承；same Article+old Revision 不继承；cross Article 一定进入 Kernel；伪造 source ACL ref/as-of、field ceiling、body 或 locator 时 authoritative comparison 拒绝。
- 安全：authorized anchor 周围放 denied other-Article candidate，窗口 reader/merge/assembler 看到的 denied body=0；跨 Article reauthorization 拒绝后不能用 anchor Article decision补位。
- 最高 seam：real PG + HTTP resolve 覆盖 expansion 后 Package Evidence 一一闭合，revocation/Revision activation 与 window read 并发时只见完整旧或完整新，绝不混版。

### third_party 注册计划

无新增 vendored 条目。若未来复制纯 `merge_overlapping_sections`，必须单独登记 `search_utils.py` 原始 hash `393b...943`，target 只能是 `third_party/onyx/retrieval/merge_sections.py`，并在 `MODIFICATIONS.md` 证明输入类型已限制为已授权 projection view；但当前建议是不复制。

### 工作量与依赖

剩余 **4–6 engineer-days**：resolve 编排 2、cross-Article loop 1、budget/merge 1、real PG+HTTP/security gate 1–2。依赖 ranked-candidate/authorized-fragment port；若 expansion classification 使用模型则再依赖 governed model port；不依赖 Supply bridge。

## 3.4 Lift 4 — query rewrite

### 上游函数路径与 permalink

- [`_build_additional_context` / `_build_message_history`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/secondary_llm_flows/query_expansion.py#L23-L65)：上下文和 history 规范化。
- [`semantic_query_rephrase`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/secondary_llm_flows/query_expansion.py#L68-L147)：生成 standalone semantic query。
- [`keyword_query_expansion`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/secondary_llm_flows/query_expansion.py#L150-L227)：生成最多三条 keyword query 的上游意图（实现本身未强制三条，CE 必须强制）。
- [`SearchTool._expand_queries_and_decide_scope`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/tools/tool_implementations/search/search_tool.py#L588-L633) 及 [`query mix/dedupe`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/tools/tool_implementations/search/search_tool.py#L865-L910)：并行 rewrite、缓存、query 权重与去重行为。

### Kernel 切割线

rewrite 输出是 query text，不是 source content，可在 Kernel 前作为 retrieval input；但它仍是模型 egress，不能绕过 profile、EgressGrant、timeout、trace 和 shared `PackageBudgetMeter`。输入只允许当前 closed `Acquire.need.query` 及已明确授权进入该 hop 的对话上下文；不得注入 candidate body、Provider metadata、denied details、raw `TrustedDeliveryContext` 或 arbitrary memory。rewrite 只产生 bounded strings，随后作为多个 `CandidateQuery` 的 query plan；它不能扩大 `EffectiveScope` 或选择 source authorization。

### copy+patch vs 原生重写决定与理由

**copy+patch：**可抽取“semantic + keyword 两类输出、case-insensitive query dedupe、保留 original query”的纯编排形状。

**原生重写：**prompt、history DTO、模型调用、trace、cache、日期注入全部由 CE profile/port 实现。Onyx 函数接受通用 `LLM`、任意 user_info/memories，并在空输出时采用不一致 fallback；直接复制会形成第二未治理模型口。现有 `ModelInferencePort.rewrite` 已是合法 gateway，应成为唯一调用点。

### 复刻配方

1. 定义 server-owned `RewriteProfile`：exact model/provider/region/retention、1 input、最多 1 semantic + 3 keyword outputs、每条字符/UTF-8/token bound、one-shot/no retry、timeout/cost ceiling。
2. Runtime 创建每 resolve 唯一 `PackageBudgetMeter`，在任何模型 bytes 前 reserve；构造 `RewriteModelRequest(profile, original_query)`，禁止传 content-bearing object。
3. 通过 exact model EgressGrant 和 `ModelInferenceEgressBinding` 调 `ModelInferencePort.rewrite`；grant mismatch/replay、budget 不足、timeout、malformed output 均返回同一 content-free unavailable。
4. 对输出做 Unicode/whitespace canonicalization，拒绝空串、超长、超数量、duplicate JSON key；casefold exact 去重，保留 first spelling；将 original query 作为固定 ranker 输入，不允许模型删掉唯一 retrieval path。
5. 为每个 rewrite 生成独立 bounded FTS/vector request；总 candidate submission仍受 ADR-0083 seam-local bound，模型不能控制 limit、weights 或 scope。
6. 不做跨请求/跨 audience cache。若做 request-local reuse，key 必须包含 profile digest、original query digest、purpose/audience/epoch，并只保存 content-free output。
7. final Package 发布同一个 meter 的累计 usage；不得像当前构造那样把 provider calls/cost/elapsed固定为 0。

### 测试 oracle

- 功能：semantic/keyword/original query 去重；大小写重复合并；输出顺序稳定；最多三条 keyword；malformed/empty/oversize model output拒绝；rewrite 不能改变 source/resource narrowing。
- 模型治理：wrong model/provider/region/audience/purpose/package digest、expired/replayed grant、timeout、第二 provider call均为 provider bytes=0或不再增加；trace只含 digest/category/usage。
- 安全：尝试把 CandidateRef、AuthorizedProjection、denied detail 或 caller-authored tenant/audience传入 rewrite DTO均在 gateway 前拒绝；rewrite output不得进入 audit 作为 raw query。
- budget：rewrite + hybrid query embedding +后续 rerank共用同一 meter；任一 reserve超限不允许 stage-local meter重试，最终 Package usage等于累计值。

### third_party 注册计划

推荐不复制上游 prompt/LLM code。若仅复制 query dedupe helper，则登记 `search_tool.py` 原始 hash `00d486...29c43`，target `third_party/onyx/retrieval/query_dedupe.py`，`MODIFICATIONS.md` 写明删除 `SearchTool`、LLM、scope、logging、cache 与 content DTO。`query_expansion.py` 仅在确实复制解析/编排代码时加入 source path，hash `363937...8a06`；不能为了引用 permalink提前登记空文件。

### 工作量与依赖

**5–8 engineer-days**：profile/grant composition 2、Runtime plan 1–2、shared meter/final usage 1–2、tests/gate 1–2。依赖 governed model-inference port和 ranked-candidate port；不依赖 Supply bridge。

## 3.5 Lift 5 — authorized rerank

### 上游函数路径与 permalink

- [`cohere_rerank_api`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/natural_language_processing/search_nlp_models.py#L680-L699)、[`cohere_rerank_aws`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/natural_language_processing/search_nlp_models.py#L702-L738)、[`litellm_rerank`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/natural_language_processing/search_nlp_models.py#L741-L760)：query+passages → per-input relevance score 的 provider shape。
- [`RerankingModel._make_direct_rerank_call` / `predict`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/natural_language_processing/search_nlp_models.py#L1207-L1312)：provider/local-server 路由和 score order。
- [`select_sections_for_expansion`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/secondary_llm_flows/document_filter.py#L187-L360)：content-bearing relevance selection 的 LLM形状；不是可直接复用的授权实现。

### Kernel 切割线

绝对切线是 `AuthorizationKernel` 输出。query、CandidateRef、rank evidence 可在前；passage/body/title/metadata/relevance prompt 一律只能由 `AuthorizedProjection` 或一个从 **单一、当前、audience-bound ContextPackage** 构造的 nominal `AuthorizedModelInput` 提供。denied candidates 的 body、field、rank evidence不得进入 rerank request，模型输出只能重排或选择输入集合的精确成员，不能发明 ref。rerank 后仍由 PackageBudget、provenance、final egress 和 audit gate封口。

### copy+patch vs 原生重写决定与理由

**决定：provider client与模型路由全部不复制；原生接到 governed model port。** Onyx functions直接持有 API key、URL、provider fallback和普通 `list[str]`，没有 audience/package/grant/budget nominal boundary。可借鉴的只有“输入顺序与 score index一一对应”oracle。当前 CE `RerankModelRequest` 已强制 `AuthorizedProjection`、结果是 exact permutation，比复制 provider wrapper更接近目标。

存在一个必须先决议的类型冲突：ADR-0052 把 `AuthorizedModelInput` constructor固定在 BotDelivery，并从一个完整 ContextPackage构造；当前 Runtime rerank port则固定接 `AuthorizedProjection`，发生在最终 Package之前。任务约束要求 lift 5 使用前者。实现不得暗中创造第二种同名 nominal type；推荐在新 ADR 中选择并证明以下桥接：Kernel先形成一个内部、不交付、audience-bound的 pre-rerank ContextPackage，使用独立 model EgressGrant构造唯一 `AuthorizedModelInput`；模型只能返回其 Evidence的 permutation/subset；随后 Runtime用同一 shared meter形成最终 Package与独立 final-hop grant。若 maintainers 不接受内部 Package，该 lift 必须保持 `NOT_ACTIVE`，并由 ADR 明确 Runtime专用 projection model input，而不是声称已满足 ADR-0052。

### 复刻配方

1. `join_authorized_ranking` 先丢弃 denied evidence，只保留 active `AuthorizedProjection`；在此之前禁止任何 tokenizer/model访问正文。
2. 对已授权集合做 deterministic pre-pack，形成单一当前 audience/purpose/epoch绑定的 pre-rerank Package；Block/Evidence一一闭合、expiry和digest完整，不向 caller交付。
3. 用 ADR-0052 constructor从该 Package + exact model EgressGrant + closed query envelope + versioned rerank profile生成 nominal `AuthorizedModelInput`；同 resolve 的 `PackageBudgetMeter`先 reserve最大 input/output/call/cost/elapsed。
4. governed gateway只序列化该 input 的 authorized Block text/Evidence refs；不含 raw identity、grant、denied detail、arbitrary context。one-shot grant redemption成功后才发 provider bytes。
5. 输出解析为输入 Evidence index的 exact permutation（或 profile允许的 unique subset）；长度、indices、duplicates、invented citation/ref任一异常都拒绝。不得接受上游 float scores直接作为授权或公开字段。
6. reranked projections/blocks进入 deterministic PackageBudget assembly；final Package只能是 pre-rerank Package Evidence的子集/重排，且 `budgetUsage`发布 shared meter累计值。
7. final egress重新使用与最终 Package digest匹配的独立 grant；model grant与final channel/model hop grant不可复用。
8. 模型 unavailable 的策略必须在 profile中显式冻结。安全默认是该 lift unavailable而非静默调用未治理 fallback；若允许 deterministic retrieval-order fallback，必须零 provider bytes且记录 closed category，不能隐藏预算或授权失败。

### 测试 oracle

- 核心安全 oracle：authorized set中混入 same-Org denied、cross-Org和不存在 candidates，实际进入 rerank gateway和assembler的 denied content bytes **= 0**；合法 request payload与未混入时相同。
- type：raw CandidateRef、duck-typed projection、expired projection、两个 Packages、wrong audience/purpose/epoch Package均不能构造 `AuthorizedModelInput`。
- egress：wrong/replayed/expired grant、wrong provider/model/region/retention、database failure均 provider bytes=0；audit failure不释放最终 Package。
- output：exact permutation、stable tie、no invented refs；duplicate/out-of-range/missing index、NaN score、oversize output拒绝。
- budget：reservation发生在 redemption/provider之前；timeout或parse失败按固定最大用量charge；最终 Package usage等于 meter累计，不能重置为 0。
- highest seam：real PG + HTTP/generated SDK完成 `CandidateRef → Kernel → AuthorizedProjection → AuthorizedModelInput → final ContextPackage` tracer，security-gate注册上述混入场景。

### third_party 注册计划

无新增 provider代码。若只复制 score/index normalization oracle，登记 `search_nlp_models.py` 原始 hash `539441...e19c9` 并将极窄纯函数放入 `third_party/onyx/retrieval/rerank_normalization.py`；明确删除 Cohere/AWS/LiteLLM credentials、HTTP clients、fallback与logging。推荐继续使用当前 CE exact-permutation parser，不产生该 vendored文件。

### 工作量与依赖

**7–10 engineer-days**，另加类型桥接 ADR/评审 **1–2 天**。依赖 governed model-inference port、ranked-candidate/authorized-fragment port；不依赖 Supply bridge。若内部 Package方案被否决，此工作量不可视为可开工承诺，carrier保持 `NOT_ACTIVE`。

## 3.6 Lift 6 — budgeting helpers

### 上游函数路径与 permalink

- [`_estimate_section_tokens`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/tools/tool_implementations/search/search_tool.py#L183-L210) 与 [`_trim_sections_by_tokens`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/tools/tool_implementations/search/search_tool.py#L213-L255)：按顺序装箱，遇首个不适配 section即停止；metadata用固定 75 token估算。
- [`count_tokens`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/natural_language_processing/utils.py#L191-L210)：大文本分片计数并可 early exit。
- [`split_text_by_tokens` / `tokenizer_trim_content`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/natural_language_processing/utils.py#L213-L247)：best-effort split/trim；上游注释承认重新 tokenize不保证硬上限和可能产生 replacement character。
- [`test_projects_file_utils.py`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/tests/unit/onyx/server/test_projects_file_utils.py#L284-L362)：chunked count/early-exit oracle。

### Kernel 切割线

预算 ceiling和模型调用 reserve可在正文前；对某 projection的 token/byte计数、trim和packing是 content-bearing，只能消费 `AuthorizedProjection`/authorized Package Block。pre-Kernel不得通过 token length、oversize或trim result观察 denied content。最终 Package必须发布所有 active stages共用的一个 `PackageBudgetMeter.usage`，不是只发布 block bytes；调用方请求只能缩小 server ceiling。

### copy+patch vs 原生重写决定与理由

**决定：保留 CE-native PackageBudget/Meter，最多把上游 early-exit行为写成测试，不复制 helper。** 固定 `METADATA_TOKEN_ESTIMATE=75`、`max_tokens<=0`返回原 sections、BPE边界漂移和replacement character均不满足安全硬预算。CE当前 tokenizer profile是 versioned contract，必须精确计数；reserve/commit/cancel还要覆盖 provider call、cost、elapsed，Onyx helper没有这些维度。

### 复刻配方

1. Runtime在完成有效 budget intersection后创建唯一 `PackageBudgetMeter(effective_budget)`，由 resolve拥有，不暴露给 caller或replaceable candidate port。
2. rewrite/query embedding/rerank/selection分别在外部调用前原子 reserve其profile maximum；成功commit actual，调用已发生但结果异常则按冻结策略charge maximum，调用前拒绝则cancel。
3. content packing接 `AuthorizedProjection[]`，用 ReleaseManifest固定的 tokenizer/version精确计算每个 block和provenance overhead；不得估算 private metadata或读取 denied item长度。
4. deterministic first-fit策略必须明示是 skip-oversize继续还是首个不适配即stop；建议继续扫描，避免一个大 authorized block饿死后续小block，并以 canonical authorized rank保持确定性。
5. trim只在结构允许的边界进行；不可切断 citation/provenance/UTF-8 scalar。不可切的 block超限则跳过并记录 authorized-only gap category，而非best-effort破坏文本。
6. Package构造从 `meter.usage`取累计值，并验证usage不超过effective ceiling；ContextRun持久化同一值。当前 `construction.py` 直接重建 `BudgetUsage(tokens=block bytes, others=0)` 必须被替换。
7. final package digest覆盖 usage；任何 stage-local meter、usage重置、provider call未计量均由construction/type test拒绝。

### 测试 oracle

- 功能：精确边界 `== limit`通过、`+1`拒绝/跳过；Unicode、长文本chunk、结构不可切 block；deterministic packing；caller cap只缩小。
- concurrency：两个 stage并发 reserve总和超限时只有一个成功；cancel不泄漏reservation；double commit/cancel拒绝。
- security：denied大正文与不存在ref不能改变usage、coverage、packing顺序或timing claim；content consumer bytes=0。
- failure accounting：grant拒绝前零usage，provider已调用后parse/timeout按策略charge；最终 Package/ContextRun usage一致，digest mutation被拒绝。
- real seam：HTTP Package `budgetUsage`与模型 trace receipts、assembly bytes可独立重算；security-gate把budget绕过作为veto，不以quality抵消。

### third_party 注册计划

无新增条目。若将 `count_tokens` 的大输入 early-exit纯算法复制为非权威优化，登记 `natural_language_processing/utils.py` 原始 hash `8df395...cc2bb4`，target `third_party/onyx/retrieval/token_count.py`，并由权威 tokenizer在边界附近重新精确计数；它不能决定硬预算。当前建议完全原生。

### 工作量与依赖

剩余 **4–6 engineer-days**：resolve-owned meter wiring 2、assembler/Package contract 1–2、failure/concurrency/HTTP tests 1–2。依赖 governed model port和 authorized-fragment access port；Supply bridge不依赖。

# 4. 次要评估

## 4.1 `tests/README.md` layering 到本仓 test pyramid

Onyx [`backend/tests/README.md`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/tests/README.md#L3-L59) 定义四层：pure unit、real external-dependency unit、full deployment integration、Playwright E2E。映射如下：

| Onyx 层 | ContextEngine 合法对应 | 命令归属 | 六 lift 用法 |
|---|---|---|---|
| Unit，无外部服务 | `tests/unit/` domain、DTO、算法、hostile object、import/type边界 | `make test` | RRF、bounds、exact permutation、meter concurrency、no-content capability graph |
| External dependency unit，真实 PG/Redis/MinIO/Vespa、无 app process | `tests/integration/` 中真实 PostgreSQL 17 + pgvector、non-owner roles、FORCE RLS；可直接调用最高公开 Python seam | `make integration`（需先 `make db-up`） | FTS/pgvector、same-transaction discovery、window lineage、release CAS、WorkerLease |
| Full deployment integration，优先 HTTP、无 mock | 本仓 real PG + API/worker process/HTTP/generated SDK vertical slice；数据层仍在 `tests/integration/`，process boot在 `tests/process/` | `make integration` + `make smoke`；完整组合由 `make check` | hybrid resolve、rewrite/rerank final Package、worker runner、revocation并发 |
| Web E2E | 当前没有独立通用 `make e2e`；UI/SDK/Bot/Action tests与未来浏览器E2E分别管理 | `make ui-test`、`make sdk-test`、`make bot-test`、`make action-test`；未来应加显式E2E target | Evidence Console/browser只验证产品流，不替代 Kernel/PG oracle |

`make security-gate` **不是第五个scope层**，而是跨层的release veto：只执行catalog注册的精确security evidence并产出独立报告，通常需要真实数据库已经up。一个unit/integration/E2E绿灯只有被catalog注册且hard oracle被观测时才构成对应安全证据；skip、未执行和`NOT_ACTIVE`不能算PASS。`make test`不应暗含数据库，`make integration`不应被fake替代，`make smoke`只证明boot/readiness。

## 4.2 `db/swap_index.py` future/present readiness → ReleaseManifest 行为规格

Onyx静态行为：`SearchSettings.status`有 `FUTURE/PRESENT/PAST`；[`_port_swap_ready`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/db/swap_index.py#L196-L219) 等待required ports全部成功且metadata backlog清零；[`check_and_perform_index_swap`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/db/swap_index.py#L222-L336)按INSTANT/REINDEX/ACTIVE_ONLY决定切换；[`_perform_index_swap`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/db/swap_index.py#L40-L171)更新旧/新状态。其 `update_search_settings_status` 每次调用都会独立 `commit`，所以旧→PAST与新→PRESENT不是一个事务；而index verify还发生在状态切换之后。这只能作为clean-room行为输入，不能复制。

ContextEngine readiness/promotion行为规格：

1. Candidate必须引用一个immutable `ReleaseManifest`，完整绑定content/index/runtime/curation profile refs+digests、tokenizer/package schema、compatible/active Revision refs；没有“默认 PRESENT”。
2. `ContextLearning.evaluate`只生成immutable evaluation，检查四个独立gate、所有active Revision的FTS/vector/profile readiness、无pending publication gap、schema compatibility和security veto；它无pointer权限。
3. `promote`输入绑定exact Organization、manifest/candidate/evaluation digests、expected base digest与expected active generation；fresh first activation期待generation 0和不存在pointer。
4. non-owner Learning transaction锁定Organization release state，重新读取并重算immutable lineage与readiness，验证current operator grant、expiry、signature、四gate和compatibility。
5. **一个数据库事务**内做generation-bound CAS active pointer、generation+1、success audit append；任一失败提交0 pointer change与0 success audit。不存在先把旧版设PAST再激活新版的窗口。
6. 旧Manifest/activation event保持immutable；rollback创建选择历史manifest的新candidate，重新evaluate/promote，不能反向改status。`A→B→A`仍有新generation，旧candidate不能ABA复活。
7. Runtime只读exact active pointer与完整manifest；missing/mixed/stale lineage fail closed，绝不fallback到上一版或“future”。cleanup与readiness不是authorization。

必须测试：fresh absence；not-ready不切换；wrong Org/operator/direct DML=0；并发两个promote只有一个成功；事务故障pointer/audit同回滚；A→B→A拒绝stale generation；Runtime并发只见完整old或new；rollback走同一promote；任何安全gate失败不可被quality分抵消。

## 4.3 missing-tenant fallback anti-pattern checklist

Onyx [`TenantAwareTask.__call__`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/background/celery/apps/app_base.py#L100-L118) 在`tenant_id`缺失/falsey时回落 `POSTGRES_DEFAULT_SCHEMA`；[`on_task_postrun`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/background/celery/apps/app_base.py#L150-L204) 在kwargs缺失时也回落默认schema；[`on_task_revoked`](https://github.com/onyx-dot-app/onyx/blob/2fb3dd10493b3883870fa8adced5b1a0e114feff/backend/onyx/background/celery/apps/app_base.py#L255-L276) 同样从task kwargs回落。ContextEngine suite要断言的不是“默认值不同”，而是以下 exact anti-behavior 全部不可能：

- 缺失/空/错误类型Organization、job、operation、source、registered service workload、Policy Epoch、lease generation、nonce、issued-at/expiry中的任一项，**在source root、connector、database business function、index、checkpoint store前拒绝**。
- task payload或环境中的 tenant/Organization不能替代server-minted、签名且exact job绑定的 `WorkerLease`；不能用触发用户身份、默认Organization、默认schema或上一次context。
- wrong workload/job/source/operation/Organization、expired/stale/superseded/replayed lease业务效果=0：无文件scan、无Provider call、无DB row变更、无checkpoint/publish watermark推进、无job完成、无Redis/default-schema cleanup。
- postrun/revoke/cleanup也必须携带可验证exact binding；缺kwargs不得对默认tenant taskset做`srem`或其他“善后”。cleanup failure不能创造另一个authority。
- worker复用进程和connection pool前后都清空context；下一任务缺context时拒绝，不能继承前一Organization。并发A/B任务与pool checkout/checkin stress必须证明cross-Organization read/write=0。
- rejection audit只保留lease/job digest与closed category，不记录raw token、path、denied object或tenant枚举细节；caller看到统一`WorkNotAvailable`。
- runner不得独立持久化checkpoint/index/cache；checkpoint只是proposal，只有engine在exact lease transaction中durably accept整页后才推进。

现有 `test_supply_bridge_lease.py`、`test_connector_runner_lease.py`、`test_connector_runner_isolation.py` 已覆盖其中大部；新增lift或connector必须复用同一suite，而非把“有 tenant_id 参数”当合格。

# 5. 不可借鉴清单与必须杀死的隐含前提

| Onyx形状/隐含前提 | ContextEngine必须采取的相反约束 |
|---|---|
| 部署Onyx service、DB、Redis、Vespa/OpenSearch作为第二产品 | 不部署；ContextEngine PostgreSQL 17 + pgvector/FTS是唯一corpus/index/policy/revocation truth |
| 非 `ee` 就可以整目录复制 | 仍须逐路径license/nested notice/依赖扫描、固定commit、hash、MODIFICATIONS、SBOM；依赖纠缠和安全不兼容仍可否决复制 |
| `ee/` permission-sync可因行为有价值而复制 | 绝不复制；只允许两室clean-room行为规格/test oracle |
| `IndexFilters.access_control_list`、`tenant_id`、`bypass_acl`是最终授权 | index filter只做removal/defense-in-depth；caller永远不能bypass；每个ref过sealed Kernel |
| `InferenceChunk`可作为pre-Kernel search result | pre-Kernel只有opaque `CandidateRef`与content-free rank evidence；正文/title/path/metadata/score explanation禁止 |
| first hit做过权限检查，所以同document expansion无需再检查 | 只有same Article+current Revision+fresh lineage可继承；跨Article一律新CandidateRef重授权，stale Revision拒绝 |
| document/chunk ID本身足以继承权限 | Article/ContextResource是唯一atom；Fragment无ACL，ID/index presence不授予任何权限 |
| pre-Kernel weighted RRF order可直接交付 | Kernel rank-blind；授权后对admitted positions压紧并用server weights重新fusion；denied positions不影响输出 |
| hybrid alpha、limit、filter、weight可由request/model任意给出 | server-owned profile和seam bounds；request只能收窄scope/budget，不能选择authority或放大work |
| Vespa/OpenSearch hybrid实现或SQL可换壳移植 | 只复用interface shape；FTS/pgvector query、HNSW、scope filter原生实现并在retained transaction执行 |
| 模型wrapper持API key/URL并可直接调用或fallback | 唯一governed port；exact profile、one-shot EgressGrant、timeout、shared meter、digest-only trace；失败不降级到未治理模型 |
| arbitrary user_info/memory/history可进入rewrite | closed input envelope；无candidate/provider/denied content，无caller-authored trusted audience/tenant |
| rerank普通`list[str]`输入且float score可信 | 只从单一audience-bound Package构造`AuthorizedModelInput`；输出只能是输入Evidence exact permutation/subset；score不授权也不公开 |
| 固定75 metadata token、BPE best-effort split足以做hard budget | Release-bound tokenizer精确计数；结构边界trim；provider/cost/elapsed与tokens共用atomic meter |
| 每stage各自budget或最后只数block bytes | 每resolve一个meter，所有stage累计，最终Package和ContextRun发布同一usage |
| FUTURE/PRESENT用两次commit切换，切换后再verify index | readiness先验证；一个Learning-owned transaction做generation CAS pointer+audit；失败0可见变化 |
| missing tenant落默认schema，postrun/revoke也可默认 | 任一exact WorkerLease/ActorContext字段缺失即fail closed，source/DB/effect=0；绝无默认Organization/schema |
| index/cursor/checkpoint推进等于内容已发布/授权 | acquisition checkpoint、publish watermark、active Revision、Policy Epoch完全分离；任何一个都不单独授权 |
| 日志可打印query、filter、document IDs、provider异常 | denied/object/query内容不进入ordinary trace；只保留closed category/digest和bounded usage |
| 并行query和重试天然安全 | 总candidate/model call有server bound；one-shot grant无透明retry；并发reserve与job replay由durable authority裁决 |
| 上游unit/integration数量等于能力已验证 | 每项claim绑定本仓unit/real PG/HTTP/E2E层和catalog security oracle；[未取证]保持[未取证] |
| Onyx search tool可负责最终assembly/answer | ContextPackage assembly保持engine-native、sealed budget/provenance/audit；answer generation在BotDelivery且另过ADR-0052边界 |

# 6. 推荐实现顺序 + 给 coordinator 的开放问题

顺序保持ADR-0075不变，并按当前完成度执行：

1. **weighted RRF/dedupe**：不重写，先用Onyx固定oracle补足差分与provenance，冻结“pre-Kernel只携带、post-Kernel才weight”。退出条件是denied candidate位置无法改变合法顺序。
2. **hybrid retrieval adapter**：登记或明确拒绝接口形状vendoring，运行native PG FTS+pgvector真实依赖/HTTP/security验证与benchmark；不碰Vespa/OpenSearch。退出条件是bounded data-only session和mixed-denied oracle通过。
3. **same-Article expansion**：把现有Kernel window接入resolve，完成cross-Article reauthorization loop、merge和budget。退出条件是same Article/current Revision继承、cross Article重授权、old Revision/tombstone拒绝均由real PG+HTTP证明。
4. **query rewrite**：在governed port上固定profile/grant/共享meter与bounded multi-query plan；保持`NOT_ACTIVE`直到final Package累计usage可验证。
5. **authorized rerank**：先解决ADR-0052 `AuthorizedModelInput`与Runtime projection port的类型/时序冲突，再接carrier；混入denied candidate→rerank/assembler content=0是security veto。
6. **budgeting helpers**：最后统一替换ad-hoc Package usage，令1–5所有调用共享一个resolve meter，并把累计usage写入final Package/ContextRun；完成后才能声称model-backed lift active。

按现有分支的增量估算合计 **24–37 engineer-days**（不含真实provider认证、生产credential、长期benchmark/corpus标注）；三条seam的已投入成本不重复计算。每一lift独立PR/ADR gate，且只有完成`make test`、适用的`make integration`、最高HTTP/generated SDK proof与catalog `make security-gate`后才能改变`STATUS.md`。

给 coordinator 的开放问题：

1. **rerank类型桥接（阻塞lift 5 activation）：**是否接受“内部、不交付的audience-bound pre-rerank ContextPackage → ADR-0052 AuthorizedModelInput → final Package”的两Package时序？若不接受，需要新ADR明确Runtime专用nominal input，且必须解释为何任务约束中的AuthorizedModelInput不适用；不能维持当前两种叙述同时声称active。
2. **lift 2 provenance：**要不要实际vendor极窄`HybridCapable` ABC形状，还是认可当前CE-native Protocol加permalink/hash为“behind copied interface shape”的等价实现？前者增加升级/attribution成本但更贴ADR字面，后者代码更浅。
3. **budget tokenizer：**最终 Package的`tokens`是否继续定义为`utf8-byte-token-v1`，还是在model-backed lift前引入ReleaseManifest绑定的真实tokenizer？这决定lift 6是简单统一meter还是包含schema/OpenAPI migration。
4. **rewrite failure策略：**governed rewrite unavailable时，是整次resolve closed unavailable，还是允许profile显式声明“零provider bytes、original-query-only”的deterministic fallback？两者都可安全实现，但必须在activation前冻结并进入golden slices。
5. **same-Article扩展位置：**先做deterministic fixed window（复用已完成seam）还是同时引入governed relevance classification？建议先fixed window，避免lift 3暗中依赖lift 5并破坏固定顺序。

