# Room-A 研究产物 — 维护者本地研究，非公开 provenance；Room-B 实现者只读规格与 oracle，不读 MaxKB 源码

> **决策状态**：本文开放问题已由维护者于 2026-07-31 全部决定（D7/D11），结果见 [`five-repository-implementation-blueprint.md`](./2026-07-31-five-repository-implementation-blueprint.md) §5；正文推荐项为评估时刻的状态。

> 本文是 GPLv3 clean-room 两室协议中的 Room-A 规格。它只记录固定版本的可观察行为、接口形状与测试 oracle，不授权复制 MaxKB 代码、依赖、schema、SQL、提示词、UI 实现或命名。除本仓 accepted ADR 与一手 ContextEngine 要求外，本文不得作为公开 provenance。

## 1. 固定 commit 与许可证核验

### 固定输入与结论

| 项目 | 静态核验结果 | 对 Room-B 的约束 |
|---|---|---|
| 上游仓库 | `1Panel-dev/MaxKB` | 只允许从本文规格与 oracle 独立实现 |
| 固定 commit | [`32b2d885e47ad04639abd7a18490bf5937f9c072`](https://github.com/1Panel-dev/MaxKB/commit/32b2d885e47ad04639abd7a18490bf5937f9c072) | 所有上游事实均只针对此 commit；后续版本不得自动外推 |
| 根许可证 | 固定 commit 的 [`LICENSE`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/LICENSE) 是 GNU GPL version 3；其中 §5(c) 要求以 GPLv3 许可整个被传递的组合作品 | 根据 ADR-0074，MaxKB 全部实现区为 **clean-room only**；零代码、零依赖、零 schema/SQL/UI 复制 |
| 单独许可子树 | 对 checkout 中 `LICENSE*`、`COPYING*`、`NOTICE*`、`.gitmodules`、常见 `vendor`/`third_party` 目录及 SPDX/Apache/MIT 授权头的静态扫描，没有发现可从 MaxKB 仓内单独取用的许可子树 | **可用子树：无**。依赖自身许可证不等于 MaxKB 对其粘合代码、配置或产品实现作出了单独授权 |
| 研究方式 | 只读源码和 UI 静态链路；未启动 MaxKB、未跑动态请求、故障注入、并发测试或 benchmark | 动态效果、性能、恢复可靠性与安全保证一律保持 `[未取证]` |

核验边界：仓内仅发现根 `LICENSE` 这一份许可证文本；`ui/src/api/system/license.ts` 是业务文件名，不是许可授予。没有发现单独许可子树是本次固定 checkout 的有限静态结论，不是法律意见；隐藏在生成物、依赖包或仓外分发物中的 notice 完整性仍为 `[未取证]`。即使未来发现 permissive dependency，也只能独立从其原始上游、固定版本和许可边界评估，不能从 MaxKB checkout 拷贝。

本报告遵守 [ADR-0074](../decisions/0074-adopt-controlled-third-party-code-reuse.md)：Room-A 可观察行为，Room-B 不读 MaxKB 源码。公开材料如需描述 MaxKB，只能回引本仓已批准的[四仓证据基线](2026-07-19-four-public-repositories-evidence.md)，不能引用本文新增的维护者本地研究作为公开 authority。

## 2. 能力盘点 → ContextEngine 区域映射表

| MaxKB 可观察能力或产品形状 | 固定 commit 一手路径 | ContextEngine 区域 / 所属 seam | 判定 | 独立实现结论 |
|---|---|---|---|---|
| 上传 → 分段规则 → 预览 → 编辑/删除 → 导入 | [`UploadDocument.vue`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/ui/src/views/document/UploadDocument.vue)、[`SetRules.vue`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/ui/src/views/document/upload/SetRules.vue)、[`ParagraphList.vue`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/ui/src/views/knowledge/component/ParagraphList.vue) | Supply candidate Revision + Control UX adapter；ADR-0018；随后由 ReleaseCandidate / ContextLearning 决定在线 release | **clean-room Room-A spec** | 保留“先看再确认”的操作心智；改成 digest-bound 候选、分离内容审阅与 release 门禁、全程不可变 lineage |
| `/document/split` 接收文件、规则、长度、过滤开关并返回可编辑分段 | [`views/document.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/knowledge/views/document.py)、[`serializers/document.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/knowledge/serializers/document.py) | versioned CompilationProfile → deterministic CompiledRevision preview | **clean-room Room-A spec** | 输入 profile 必须版本化；重复 preview 产生同 digest；preview 不改 active pointer、不进入生产 index |
| preview 时把源文件持久化并用 `source_file_id` 串联 confirm | [`serializers/document.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/knowledge/serializers/document.py) | tenant-owned preview blob / candidate lineage | **do-not-take / anti-pattern** | 临时内容必须有 Organization、actor、purpose、TTL、digest 和清理状态；临时文件 ID 不是发布或授权能力 |
| Paragraph 作为可编辑检索单元 | [`models/knowledge.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/knowledge/models/knowledge.py) | immutable ContextFragment | **clean-room Room-A spec**（产品心智） | UX 可以显示“段落/内容块”；持久层不得原地改 Fragment，任何表示变化产生新 ContextRevision |
| Problem 与 Paragraph 多对多关联、手工或 LLM 生成相关问题 | 同上；[`serializers/paragraph.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/knowledge/serializers/paragraph.py) | proposed CurationAnnotation `alternate_query` / golden-case candidate | **clean-room Room-A spec** | 关联问题可进入候选召回或评测覆盖；先审计，再进入 CurationSnapshot；绝不直接写生产索引 |
| document-scoped key/value Tag 及筛选 UX | [`models/knowledge.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/knowledge/models/knowledge.py)、[`TagDrawer.vue`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/ui/src/views/document/tag/TagDrawer.vue) | source metadata 或 CurationAnnotation `facet_tag`，二者必须明确分型 | **clean-room Room-A spec** | 源事实随新 Revision；人工/模型治理标签随 CurationSnapshot。禁止同一 mutable Tag row 同时承担两种真相 |
| Termbase 参与分词向量保存时的搜索向量构造 | [`models/knowledge.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/knowledge/models/knowledge.py)、[`vector/pg_vector.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/knowledge/vector/pg_vector.py) | audited `term` annotation → immutable CompilationProfile / IndexProfile rebuild | **clean-room Room-A spec** | 术语表值得进入 curation UX；变化必须创建新 profile 并重编译/重索引，不得让 mutable row 改写现存索引语义 |
| Hit Test：query、top N、相似度、embedding/keywords/blend，返回命中内容和分数 | [`serializers/knowledge.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/knowledge/serializers/knowledge.py)、[`views/knowledge.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/knowledge/views/knowledge.py) | ContextRuntime tracked resolve + exploration report；golden intake | **clean-room Room-A spec** | 保留可解释调试面；命中内容必须来自 ContextPackage，手测结果不构成 release gate |
| direct return：document mutable flag + threshold，命中后跳过生成 | [`base_search_knowledge_node.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/application/flow/step_node/search_knowledge_node/impl/base_search_knowledge_node.py)、[`base_chat_step.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/application/chat_pipeline/step/chat_step/impl/base_chat_step.py) | versioned AssemblyProfile / RuntimeProfile；仍只消费 AuthorizedProjection / ContextPackage | **clean-room Room-A spec**（需收紧） | “verbatim authorized block”可作为显式 release 候选；阈值和策略必须版本化、评测、canary，不能由 document row 即时改生产行为 |
| 点赞/点踩与原因；把回答标注为改进内容 | [`chat_record.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/chat/serializers/chat_record.py)、[`application_chat_record.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/application/serializers/application_chat_record.py) | authorized-only ContextRun feedback → triage → golden candidate / CurationAnnotation candidate | **clean-room Room-A spec** | 保留反馈采集和人工归因；反馈只生成候选，必须绑定 Package、release generation 与 citations；不能直接造 Fragment |
| Document/Paragraph 落库后由 decorator/Celery 触发 embedding，删除旧向量再建新向量 | [`serializers/document.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/knowledge/serializers/document.py)、[`task/embedding.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/knowledge/task/embedding.py)、[`listener_manage.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/common/event/listener_manage.py) | WorkerLease-bound Supply + ADR-0066 embed-before-publication | **do-not-take / anti-pattern** | 采用完整 embedding readiness 的行为目标；拒绝“先可见内容、后补向量”、delete/recreate 活跃索引和 ambient Celery authority |
| 11 个 Django app 的 `tests.py` 是三行空桩 | [`apps/knowledge/tests.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/knowledge/tests.py) | highest public seam + real PG17/invariant catalog | **do-not-take / anti-pattern** | UI 手测、文件存在和测试数量均不能证明行为；active invariant 未执行即 FAIL |
| serializer/view/decorator 同时承载校验、权限、事务、状态、任务派发和向量副作用 | [`serializers/document.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/knowledge/serializers/document.py) | Deep Modules；sealed Runtime；narrow Supply/Learning ports | **do-not-take / anti-pattern** | transport 只做受信输入构造和 DTO 映射；策略与发布只能在所属 Module 的封闭事务里执行 |

全表的总约束是：**candidate 永远不等于 active behavior**。Supply 的原子 `ContextResource.active_revision` 只发布完整不可变内容；在线 Runtime 采用哪个 corpus/profile/curation 仍由唯一的、release-operator-authorized `ContextLearning.promote` 激活 `ReleaseManifest`。`ContextControl` 只治理 source/access/policy，不能发布 profile；评测 executor 只产报告，也不能发布。

## 3. 逐能力蓝图

### 3.1 preview → confirm ingestion：从候选 Revision 到两段原子可见性

**上游路径 + permalink**

- 三步 UI（上传、规则/预览、成功）和最终批量导入：[`ui/src/views/document/UploadDocument.vue`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/ui/src/views/document/UploadDocument.vue)。
- intelligent/advanced 分段，advanced 可选 pattern、长度、特殊字符过滤；右侧按文件展示分段：[`ui/src/views/document/upload/SetRules.vue`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/ui/src/views/document/upload/SetRules.vue)。
- 每个分段展示 title、content、字符数，并可编辑/删除：[`ui/src/views/knowledge/component/ParagraphList.vue`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/ui/src/views/knowledge/component/ParagraphList.vue)。
- preview API 与 confirm API 是两条路径：[`apps/knowledge/views/document.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/knowledge/views/document.py)、[`apps/knowledge/serializers/document.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/knowledge/serializers/document.py)。

**映射的本仓 seam / ADR**

- `ContextSource → ContextResource → ContextRevision → ContextFragment`，`prepared → indexed → active`：[ADR-0018](../decisions/0018-immutable-revision-publication.md)。
- embedding 完整才允许 publication：[ADR-0066](../decisions/0066-embed-fragments-before-publication.md)。
- post-Revision curation 单独形成 `CurationSnapshot`：[ADR-0014](../decisions/0014-curation-snapshot-and-release-ownership.md)。
- 当前 corpus 形成 generation-bound `ReleaseCandidate`：[ADR-0073](../decisions/0073-compose-explicit-release-candidates-from-current-corpus.md)。
- 只有 `ContextLearning.promote` 改 active Release pointer：[ADR-0033](../decisions/0033-promote-organization-releases-through-one-learning-owner.md)。

**Room-A 行为规格**

状态机分成三个不可混写的状态族：

```text
Preview: uploaded -> compiled_preview -> reviewed -> confirmed | abandoned | expired
Supply:  accepted_job -> acquired -> prepared(embeddings complete) -> indexed -> resource_active
Release: candidate -> executor_evaluated(PASS|FAIL|REFUSED) -> operator_authorized -> promoted
```

1. `uploaded` 接受受信 Organization/actor/source binding、一个或多个 source blob refs、不可变 `CompilationProfileRef` 和 preview TTL；不接受 caller 自报 Organization、active Revision 或 release generation。
2. `compiled_preview` 必须固定 source digest、compiler/profile digest、规范化 Revision digest、Fragment 有序清单 digest、warning/refusal category；相同输入必须得到相同 digest。preview 失败时没有 ImportJob、Revision、Fragment、index candidate 或 active-pointer 效果。
3. reviewer 可以对候选执行 `keep | edit_as_new_candidate | split | merge | remove | attach_alternate_query | propose_tag | propose_term`。任何内容编辑都生成新的 candidate digest，不原地改已审候选；旧候选留审计 lineage。
4. `confirm` 使用 optimistic compare：`PreviewRef + expected_candidate_digest + decision_reason_digest`。digest 已变、preview 过期、actor 权限撤销、SourceVersion 变化或 Resource tombstoned 时拒绝，业务效果为零。confirm 只创建 durable acquisition/import job；它不直接写 active pointer。
5. Supply worker 只能用与 exact durable job、Organization、Source、Resource、operation、generation、nonce 完全绑定的 `WorkerLease` 继续。编译、embedding、indexing 任一步不完整时旧 Revision 保持在线。
6. `resource_active` 是 ADR-0018 的一次 PostgreSQL pointer transaction；读者只见完整旧版或完整新版。它使完整 Revision 成为当前 corpus 事实，但**不会自动改变 promoted ReleaseManifest**。
7. ContextLearning 从当前 corpus 观察生成 generation-bound `ReleaseCandidate`，由 ADR-0080 executor 运行权威评测。报告 `REFUSED`/`FAIL` 或四门任一不通过时不可 promote。
8. release operator 看到精确 base generation、manifest/corpus/profile digests、四门状态、compatibility、capability coverage、commands、报告时间和 executor seam 后，才授权 `ContextLearning.promote`。promotion 原子更新 Release pointer 并追加 success audit；并发 loser 或 stale base 的效果为零。
9. curation 不阻塞 Revision publication。审计通过的 annotation 另组 `CurationSnapshot`；curation-on 只有作为兼容 `ReleaseManifest` 的 `CurationProfileRef` 经同一 promote 路径才生效。缺失/失败 curation 退回正常非 curation retrieval，而不是弱化授权。

**operator-visible signals**

| 面 | 可见内容 | 不能显示 / 不能代表 |
|---|---|---|
| candidate inspection（受权 reviewer） | **content-bearing**：文件名的安全显示名、每个候选 Fragment 的 source-ordered title/body、字符/token 估算、structural path、source span、拟关联问题/tag/term、before/after diff、compiler warning；默认分页且不把内容写入普通日志 | denied source 内容、其他 Organization 内容；preview 内容不代表已授权 Runtime Evidence |
| queue/status 列表 | **content-free**：PreviewRef/JobRef 的 opaque 或 digest 形式、source/resource refs、candidate digest、Fragment count、byte/token totals、profile refs、状态、generic failure category、created/expiry time | 正文、原始路径、secret、provider error、denied candidate rank |
| release decision | **content-free 为主**：expected base generation、manifest/corpus/profile/eval digests、PASS/FAIL/REFUSED、四门、slice counts/uncertainty、stale-lineage count、executor seam、commands、candidate diff counts | “手测通过”、caller 上传 counters、单一 aggregate score；任何内容摘要都不能替代受权 inspection |

**审计链**

追加且不可变地记录 `PreviewCreated → CandidateRecompiled → ReviewerDecision → ImportJobAccepted → LeaseIssued/Reclaimed → acquired/prepared/indexed/active events → ReleaseCandidateObserved → ExecutorReport → PromotionAuthorized → PromotionCommitted`。每条至少绑定 Organization、actor/service、source/resource、old/new digest、profile refs、base generation、UTC instant、reason/refusal category；restricted audit 可持 decision detail，普通运营审计只持 content-free digest。失败 promotion 不写 success audit；denied details 不进入 ContextRun 或 Learning corpus。

**接口形状草图（独立设计，不是 MaxKB API 翻译）**

```python
class RevisionPreviewPort(Protocol):
    def create(
        self,
        call: AuthorizedCurationCall,
        source_blobs: tuple[SourceBlobRef, ...],
        compilation_profile: CompilationProfileRef,
    ) -> RevisionPreviewRef: ...

    def inspect(
        self, call: AuthorizedCurationCall, preview: RevisionPreviewRef, page: PageRequest
    ) -> RevisionPreviewPage: ...  # content-bearing, separately authorized

    def revise(
        self, call: AuthorizedCurationCall, preview: RevisionPreviewRef,
        expected_digest: Sha256, edits: tuple[CandidateEdit, ...]
    ) -> RevisionPreviewRef: ...  # new digest, immutable predecessor

    def confirm(
        self, call: AuthorizedCurationCall, preview: RevisionPreviewRef,
        expected_digest: Sha256, reason_digest: Sha256
    ) -> ImportJobRef: ...  # no publication effect

class RevisionReadinessEvaluator(Protocol):
    def evaluate(self, candidate: CandidateRevisionRef) -> RevisionReadinessReport: ...

class ContextLearning:
    def evaluate(self, candidate: ReleaseCandidateRef) -> ReleaseEvaluation: ...
    def promote(self, call: TrustedPromotionCall) -> PromotionReceipt: ...
```

`RevisionReadinessReport` 只证明 deterministic compilation、embedding/index completeness 和 compatibility；它不是 ADR-0080 的权威 release evaluation，也没有 publication authority。

**测试 oracle 清单**

- 相同 bytes + profile 产生相同 candidate/Fragment ordering/digest；任一 edit 产生不同 digest，旧 preview 不变。
- preview、revise、abandon、expire 的 active Revision pointer、active Release pointer、生产 index row 数变化均为 0。
- unauthorized/cross-Organization preview inspect 返回同类 generic refusal；正文、路径和 item count 不泄漏。
- confirm 时 actor/source/profile/digest 任一 stale：ImportJob=0、pointer=0、success audit=0。
- 在 acquired/prepared/indexed/pointer-before-commit 每个 fault point 终止：旧 Revision 完整可读；恢复只由新 lease generation 继续；旧 nonce 效果为 0。
- missing/wrong-dimension embedding 不能进入 `prepared/indexed/active`。
- resource activation 后、Release promotion 前，Runtime 仍按旧 promoted manifest 行为；候选不等于 active behavior。
- `evaluate` 的 PASS 也不改 pointer；非 release operator、过期 grant、stale generation、四门任一失败或 REFUSED 的 promote 效果为 0。
- curation pipeline、ContextControl、migration、bootstrap、evaluator 均不能直接写 Release pointer；rollback 走新的 candidate + 同一个 promote。
- UI content-bearing inspect 只走受权服务 seam；列表/日志/metrics 不含 source bytes、denied detail 或 corpus path。

**验证命令**

```bash
make lint
make typecheck
make test
make ui-test
make catalog
make smoke
make integration       # 真实 PG17：fault points、RLS、CAS、publication visibility
make security-gate     # 需先 make db-up；hard-oracle 与 release evidence
```

**工作量与依赖**

- 估算：**18–27 engineer-days**（1 名工程师；preview contract/storage 5–7，受权 inspection/UX 4–6，confirm/job/audit 4–6，release wiring 与 tests 5–8；不含新 parser、真实 provider procurement 和 UX research）。
- 依赖：现有 File acquisition/WorkerLease/recovery、ADR-0066 embedding、release candidate/evaluation/promotion、trusted operator authentication、owner-only preview blob store、local Evidence Console 或等价受权 UI。若要激活 curation-on，另依赖 C1 annotation audit 与 CurationSnapshot 实现。

### 3.2 Paragraph / Problem / Tag / Termbase：知识单元与 curation UX

**上游路径 + permalink**

- `Paragraph(content,title,status,hit_num,is_active,position,chunks)`、`Problem(content,hit_num)`、mapping、`Tag(key,value)`、`Termbase(content)`：[`apps/knowledge/models/knowledge.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/knowledge/models/knowledge.py)。
- Paragraph 关联/解除 Problem、创建/编辑/删除以及 embedding 副作用：[`apps/knowledge/serializers/paragraph.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/knowledge/serializers/paragraph.py)。
- document tag 的 key/value 与关联文档 UX：[`ui/src/views/document/tag/TagDrawer.vue`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/ui/src/views/document/tag/TagDrawer.vue)。
- Termbase 在向量保存时参与全文 `search_vector`：[`apps/knowledge/vector/pg_vector.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/knowledge/vector/pg_vector.py)。

**映射的本仓 seam / ADR**

- `Paragraph` 产品概念 → `ContextFragment`，继承唯一 Article/ContextResource 与 Revision lineage；不能独立授权。
- `Problem` → `CurationAnnotation(kind=alternate_query)` 或 golden-case candidate。
- `Tag` → 必须二选一：source-declared metadata 进入新 Revision，或 `CurationAnnotation(kind=facet_tag)` 进入 snapshot。
- `Termbase` → `CurationAnnotation(kind=term)`；接受后组成不可变术语快照并进入 versioned `CompilationProfile`/`IndexProfile`。它若影响 assembly 展示，另由 versioned `AssemblyProfile`/`RuntimeProfile` 引用，不能一个 profile 包办所有变化域。
- [ADR-0014](../decisions/0014-curation-snapshot-and-release-ownership.md)、[ADR-0033](../decisions/0033-promote-organization-releases-through-one-learning-owner.md)；Article 是内容授权原子的约束见 ADR-0077/本仓 threat model。

**哪些概念值得进入 curation UX**

| UX 概念 | 保留方式 | operator 看到什么 | 生效条件 |
|---|---|---|---|
| Fragment inspector | 显示 immutable Fragment 与 Revision lineage，不称 mutable paragraph | content-bearing title/body、structural path、span、source/revision/profile refs、active/superseded 标记 | 内容只能通过新 Revision 改；reviewer 不能直接 patch active Fragment |
| Alternate query / related problem | 一对多 annotation candidate，来源可为 human、feedback、LLM | query 文本、目标 Fragment、生成来源、citation、confidence、duplicate warning | human audit + frozen on/off eval + CurationSnapshot + promote |
| Facet tag | typed key/value annotation；明确 source_fact 或 curated | tag、scope、目标 Article/Fragment、proposer、evidence、冲突/覆盖关系 | source_fact 随 Revision；curated 随 snapshot；均不参与授权 |
| Term | term、aliases、language、match mode、作用域和 tokenizer impact | term 内容、命中样本、预计 recompile/reindex 数、profile diff | audit 后创建新 term snapshot/profile；完整 rebuild 和 release gate |
| Dedup/stale | C1 计划中的 cluster / stale annotation | representative/member refs、理由、置信区间、Revision compatibility | 按 kind 预注册样本和误标阈值；不足只能 inconclusive |

**必须拒绝的 mutable-row 语义**

- `Paragraph.content/title/chunks/is_active/position` 不能在同一个 stable row 上既做 source truth、检索载体又做 active release；表示变化创建全新 Revision/Fragments。
- `hit_num` 不能写回 Fragment 作为学习权威；聚合使用带 release/profile/run lineage 的派生 telemetry，且不得影响授权。
- `Problem.hit_num`、Tag、Termbase 的当前行不能即时改变 recall、tokenization、ranking 或 assembly；它们只能生成不可变候选/profile。
- Problem 与 Fragment 的 mapping 不能赋予可见性；跨 Article/Resource 的任何 expansion 都要重新授权。
- 模型生成 `Problem/Tag/Term` 只处于 `proposed`，不能写 production index；失败、低置信度或无 citation 只能拒绝/待审。

**Room-A 行为规格**

```text
annotation: proposed -> evidence_validated -> human_accepted | rejected | expired
snapshot:   assembling -> compatibility_checked -> evaluated -> release_candidate
profile:    draft -> built -> evaluated -> promoted | superseded
```

- 输入：exact Organization、target Revision/Fragment refs、kind-specific payload、proposer identity/model ref、source ContextRun/feedback refs、citations、profile base digest、proposed_at。
- 输出：content-addressed `CurationAnnotationRef`；accepted 集合组成 immutable `CurationSnapshotRef`，固定 compatible Revision set、member ordering、evaluation digest。
- `evidence_validated` 要求引用真实且属于目标 Revision；模型 assertion 无 citation、citation 跨目标或 lineage stale 一律拒绝。
- reviewer 的 accept/reject 必须记 reason category/digest；任何 edit 生成新 annotation candidate。
- snapshot 构建时 target Revision 已 superseded、缺失、跨 Organization、重复/冲突 annotation、profile base stale 时 fail closed。
- Runtime 只能在一个数据库 snapshot 中读取 promoted ReleaseManifest 指定的 active Revision 和 compatible CurationSnapshot。snapshot 缺失/失败时 curation-off，不回退到“最新 mutable annotation”。
- annotation 只能影响 ranking/assembly，不能改变 AuthorizationKernel 的 allow/deny、field projection 或 SourceAclEvidence。

**operator-visible signals**

- annotation inbox 列表保持 content-free：kind、target lineage digest、proposer kind、citation count、base profile/release refs、state、confidence/uncertainty、created/expiry time；不显示正文、denied Evidence 或跨 Organization existence。
- 单条受权 inspection 才显示 Fragment 正文、proposed query/tag/term、citations、before/after profile diff、conflict 和 deterministic validation；accept/reject 必须要求 reason category/digest。
- snapshot/release 面显示 accepted/rejected/expired counts、compatible Revision count/digest、per-kind sample/threshold/uncertainty、rebuild impact 和 evaluation digest；`proposed`、无 citation 或 inconclusive 均不得渲染为 active。

**接口形状草图**

```python
@dataclass(frozen=True)
class CurationAnnotationCandidate:
    organization_ref: OrganizationRef
    target: FragmentLineage
    kind: Literal["alternate_query", "facet_tag", "term", "dedup", "stale"]
    payload_digest: Sha256
    evidence_refs: tuple[EvidenceRef, ...]
    proposer_ref: ActorOrModelRef
    base_profile_digest: Sha256

class CurationReviewPort(Protocol):
    def propose(self, call: AuthorizedCurationCall, candidate: CurationAnnotationCandidate) -> CurationAnnotationRef: ...
    def inspect(self, call: AuthorizedCurationCall, ref: CurationAnnotationRef) -> AnnotationInspection: ...
    def decide(self, call: AuthorizedCurationCall, ref: CurationAnnotationRef,
               expected_digest: Sha256, decision: AuditDecision) -> AnnotationAuditReceipt: ...

class CurationSnapshotBuilder(Protocol):
    def assemble(self, call: LearningBuildCall,
                 annotations: tuple[CurationAnnotationRef, ...],
                 compatible_revisions: tuple[ContextRevisionRef, ...]) -> CurationSnapshotRef: ...

class ProfileCompiler(Protocol):
    def compile_terms(self, snapshot: CurationSnapshotRef,
                      base: CompilationProfileRef) -> CompilationProfileRef: ...
    def compose_assembly(self, snapshot: CurationSnapshotRef,
                         base: AssemblyProfileRef) -> AssemblyProfileRef: ...
```

最后两种 profile 必须落到 ReleaseManifest 已拥有的 `ContentProfileRef`/`IndexProfileRef`/`RuntimeProfileRef`/`CurationProfileRef` 兼容关系中；不得新增第二个 active pointer 或让 `ContextControl` 发布。

**测试 oracle 清单**

- 修改 Fragment 内容只能产生新 Revision；旧 Revision/Fragment digest 和 citations 永不变化。
- annotation target 跨 Organization、跨 Revision、不存在或 stale：accepted=0、snapshot member=0、index effect=0。
- LLM alternate query/tag/term 缺 citation、引用不在 authorized feedback binding 内：保持 proposed/rejected，生产 index effect=0。
- 同一组 accepted annotations 不论输入顺序都得到同一 canonical snapshot digest；duplicate/conflict 规则确定性。
- term 增删导致新 Compilation/Index profile digest；旧 active profile 与 vectors/search_vector 不原地变化；全量 readiness 前不能 promote。
- curation-on 缺 snapshot ref、compatible Revision set 或 evaluation digest：evaluate/promote 拒绝，active manifest 不变（`tests/unit/test_release_owner_architecture.py` 对应结构 oracle）。
- Runtime 只把 annotation 应用于 AuthorizedProjection；denied bytes 进入 curation/rerank/assembly 计数为 0。
- snapshot 不兼容时 Runtime 使用 curation-off 的正常检索或 release candidate 被拒绝，不读取 latest mutable row。
- ContextControl、review UI、snapshot builder 均无 active pointer DML；只有 release-operator-authorized promote 生效。

**验证命令**

```bash
make lint
make typecheck
make test             # release contracts、curation workflow、feedback authority
make ui-test          # UI 只用 public seam；feedback 无 publication authority
make catalog
make integration      # FORCE RLS、cross-org、release compatibility
make security-gate
```

**工作量与依赖**

- 估算：**24–36 engineer-days**（annotation contract/audit 7–10，Fragment/alternate-query UX 5–7，tag/term profile build 6–10，snapshot/release/tests 6–9）。若只做 `alternate_query` 最小切片：**10–15 days**。
- 依赖：M3 frozen retrieval/eval baseline、authorized feedback binding、CurationSnapshot persistence、profile compatibility、C1 预注册样本与阈值、受权内容 inspection。Termbase 激活还依赖 tokenizer/index rebuild cost evidence。

### 3.3 Hit Test / direct return / feedback-improve：人工质量运营但非门禁

**上游路径 + permalink**

- Hit Test 输入 `query_text/top_number/similarity/search_mode`，返回 Paragraph 与 similarity/comprehensive score：[`apps/knowledge/serializers/knowledge.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/knowledge/serializers/knowledge.py)。
- direct return 由 document method/threshold 决定，并把匹配内容直接作为回答：[`apps/application/flow/step_node/search_knowledge_node/impl/base_search_knowledge_node.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/application/flow/step_node/search_knowledge_node/impl/base_search_knowledge_node.py)、[`apps/application/chat_pipeline/step/chat_step/impl/base_chat_step.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/application/chat_pipeline/step/chat_step/impl/base_chat_step.py)。
- 点赞/点踩、原因：[`apps/chat/serializers/chat_record.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/chat/serializers/chat_record.py)。
- improve 会把 chat answer 复制为新 Paragraph、关联 Problem、再 embedding：[`apps/application/serializers/application_chat_record.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/application/serializers/application_chat_record.py)。

**映射的本仓 seam / ADR**

- 手动 explorer 必须调用最高已激活的 `ContextRuntime.resolve`/HTTP-generated SDK seam；不能直查 index。
- 权威 evaluation 只能由 ADR-0080 私有 run executor 自行组合 `dogfood-loopback-resolve-acquire-v1` 并从它亲自获取的 ContextPackage 构造 security observation：[ADR-0080](../decisions/0080-refuse-authoritative-evaluation-without-an-executor.md)。
- golden expectation 绑定 exact Evidence lineage，stale lineage 整份报告 REFUSED 而非记 recall miss：[ADR-0082](../decisions/0082-recover-the-golden-corpus-and-refuse-stale-lineage.md)。
- canary/rollback 都是新的 generation-bound ReleaseCandidate，通过 [ADR-0033](../decisions/0033-promote-organization-releases-through-one-learning-owner.md) 同一 promote。

**Room-A 行为规格**

#### Manual Hit Explorer

- 输入：authenticated invocation、trusted delivery context、query、PackageBudget 和可选的**只收窄**请求；operator 不能覆盖 server active profile、Organization、principal、audience、threshold 或 security counter。
- 输出：一个真实 `ResolutionOutcome` 的 content-bearing inspection：authorized Evidence title/snippet（限 projection）、citation/lineage、排名与可公开 score、budget usage、coverage/refusal、active release/profile refs；denied candidate 内容/score/count 不显示。
- 可以把本次 query 提交为 `GoldenCaseCandidate`，但必须由 maintainer 后续补 expectation、partition/slice、hard negatives 和 exact lineage，再走 lock/intake。
- manual result 状态仅为 `exploratory_success | exploratory_refusal | unavailable`。UI 禁止展示 `release_pass` 或把单次命中标成 regression gate。

#### Direct return 独立实现

- 行为名称建议改为 `verbatim_authorized_block`，明确它仍是交付一种已经 exact-authorized 的 ContextPackage 内容，不是 index 直出。
- 策略属于 immutable `AssemblyProfile`/`RuntimeProfile`：eligible Evidence kind、minimum calibrated score、tie behavior、最大 blocks/tokens、citation rendering、abstention、audience/egress policy。document/Fragment mutable flag 无权覆盖。
- 输出必须保留 Package/Evidence provenance、purpose、TTL、audience 和 citations；没有匹配 Evidence 时只给 canonical refusal，不允许 designated fallback 生成无 Evidence 内容。
- 新策略先成为 ReleaseCandidate，在 frozen golden slices 上比较 baseline/candidate。Security/Reliability/Quality/Budget 四门独立；security 一票否决。

#### Feedback → improvement

```text
feedback_received -> bound_to_authorized_run -> triaged
  -> golden_case_candidate | curation_annotation_candidate | no_action
  -> separately evaluated -> release candidate -> promoted
```

- feedback 必须绑定 exact Organization、ContextRun、Package digest、active release ref/generation、Evidence citations、actor、category、created_at；只有 authorized-only ContextRun 内容可以进入 Learning。
- thumbs-up/down、closed reason 和自由文本是信号，不是真值。自由文本按敏感内容处理；不得携带 denied candidate 或 DecisionAudit detail。
- “把回答加入知识”只生成 candidate。模型回答不是 source truth；没有 source Evidence 的 claim 不得自动成为 Fragment。需要新 source content 时，走 3.1 preview/confirm；需要 alternate query/tag/term 时，走 3.2 annotation audit。
- candidate admission、golden lock、executor evaluation、release promotion 四个权限分离。ContextControl 不发布，feedback handler/triager/evaluator 不发布。

#### Golden slices、报告、canary 与 rollback

- 最小 slices：精确指称、问答不对称、单文档/跨文档、时序、粒度完整性、冗余、负例拒答、安全、direct-return eligible/ineligible、feedback-derived candidate。
- retrieval judge 用 deterministic recall@k/MRR/claim support；LLM blind judge 只提供 blind score/critical contradiction/produced claims，不能携 observed/security 字段。
- executor seam unreachable、response malformed、没有 executor observation、lineage stale → `REFUSED`；观测到任一 unauthorized evidence/missing-context fallback/wrong-audience binding → `FAIL`；绝不靠 quality score 抵消。
- canary manifest 固定 corpus/profile/snapshot 和 traffic/audience boundary，报告 generation 与 baseline digest。canary 不新增 publication owner。
- rollback 选择兼容历史 immutable profiles 创建**新 candidate**，重新授权 promote，generation 继续递增；不回写历史 pointer，不绕过当前 authority/freshness。

**operator-visible signals**

- Explorer：content-bearing authorized Evidence、query、release/profile refs、budget、coverage/refusal；普通导出默认 content-free，显式授权才能导出正文。
- Golden report：case/slice counts、set/pilot/lineage map digests、release ref、executor seam、PASS/FAIL/REFUSED、四门、阈值来源、uncertainty、stale count、commands；不打印 corpus path或具体 stale refs。
- Feedback inbox：category、run/package/release digests、citation count、triage state；内容 inspection 单独授权。任何“已改进”只表示 candidate recorded，不表示 production changed。
- Canary：baseline/candidate generation、exposure count、slice delta、security events、budget/latency、stop/rollback decision；不得展示 aggregate win 替代各门。

**接口形状草图**

```python
class ManualHitExplorer:
    def run(self, invocation: AuthenticatedInvocation,
            delivery: TrustedDeliveryContext,
            request: Acquire) -> ResolutionOutcome: ...
    def propose_case(self, call: AuthorizedGoldenIntakeCall,
                     run_ref: ContextRunRef, reason: TriageCategory) -> GoldenCaseCandidateRef: ...

@dataclass(frozen=True)
class FeedbackBinding:
    organization_ref: OrganizationRef
    run_ref: ContextRunRef
    package_ref: ContextPackageRef
    package_digest: Sha256
    release_ref: ReleaseManifestRef
    release_generation: int
    citations: tuple[EvidenceRef, ...]

class FeedbackIntake:
    def record(self, call: AuthorizedFeedbackCall,
               binding: FeedbackBinding, signal: FeedbackSignal) -> FeedbackRef: ...
    def triage(self, call: AuthorizedTriageCall,
               feedback: FeedbackRef, decision: TriageDecision) -> CandidateRef | NoAction: ...

class EvaluationRunExecutor:
    def execute(self, golden_set: GoldenSet, blind_judgments: BlindJudgeDocument,
                tracked_thresholds: TrackedThresholds, report_at: datetime) -> GoldenReport: ...
    # 不接受 callback/client/transport/counters/security_result
```

**测试 oracle 清单**

- Manual Hit Explorer 与 canonical HTTP/generated SDK 对同请求给出相同 Package security fields；直查 index 的 UI/import 依赖测试失败。
- 注入 hostile denied CandidateRef：denied bytes 进入 explorer/rerank/assembly/direct-return 计数为 0；public UI 不泄漏 denied score/count。
- 单次/百次 manual test、手工标“通过”均不能构造 `ReleaseEvaluation` 或调用 promote。
- file-only run 或 caller 自报 zero counters：报告必须 `REFUSED(no_run_executor_security_observation)`；executor 不接受 callback/client/transport。
- blind judge 含 observed/security/refusal 字段：整份运行拒绝。
- stale expected Revision/Fragment：case 不进入 judge、报告 REFUSED，不计 retrieval miss；不打印 ref/path。
- direct-return candidate 对 authorized Evidence 保留 Package lineage/citations；无 Evidence、过阈值但 lineage 不完整、wrong audience 时返回 canonical refusal。
- feedback 缺 run/package/release/citation binding、跨 Organization 或 generation stale：candidate=0、golden change=0、index effect=0。
- triaged feedback 只能构造 candidate；`tests/unit/test_feedback_has_no_publication_authority.py` 与 `tests/integration/test_feedback_has_no_publication_authority.py` 必须证明 pointer DML=0。
- canary 任一 security event 立即 FAIL/stop；rollback 产生新 generation 和 success audit，历史不可变。

**验证命令**

```bash
make test
make ui-test
make smoke
make eval-v1-execute  # 需配置 durable golden/backup roots、lock、judgments、lineage map
make integration
make security-gate
```

`make eval-v1` 的 file-only report 可以计算分层指标，但按 ADR-0080 不能产出权威非 REFUSED 结论；release gate 必须使用 executor-owned `make eval-v1-execute`。

**工作量与依赖**

- 估算：**20–31 engineer-days**（manual explorer 收口 3–5，direct-return profile/ablation 6–9，feedback UX/triage 4–6，golden slices/executor/canary/rollback tests 7–11）。不含收集足够真实 golden cases 的日历时间。
- 依赖：bounded Runtime HTTP seam、private golden/backup roots 与 lineage recapture、tracked thresholds、release operator、authorized feedback intake。direct return 只有在 frozen dataset 上证明收益且不破坏四门后才可激活。

### 3.4 Embedding / document-save chain：改成 lease-bound embed-before-publication

**上游路径 + permalink**

- document save 在事务中写 Document/Paragraph/Problem/mapping，`@post` 返回后调用 refresh 触发 embedding：[`apps/knowledge/serializers/document.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/knowledge/serializers/document.py)、[`apps/common/utils/common.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/common/utils/common.py)。
- Celery task 以 document/paragraph ID 调 listener：[`apps/knowledge/task/embedding.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/knowledge/task/embedding.py)。
- listener 修改 mutable status，删除旧 embeddings、batch save、finally 汇总状态：[`apps/common/event/listener_manage.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/common/event/listener_manage.py)。
- vector store 对 normalized text 调模型并直接写 Embedding rows：[`apps/knowledge/vector/pg_vector.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/knowledge/vector/pg_vector.py)。

静态观察只证明调用和状态形状；事务提交与 Celery enqueue 的 crash window、delete/recreate 期间在线可见性、exception 后状态正确性和并发覆盖均为 `[未取证]`。

**映射的本仓 seam / ADR**

- [ADR-0066](../decisions/0066-embed-fragments-before-publication.md)：新 Fragment 在 activation 前必须具有完整、固定维度、有限、非零且顺序正确的 embedding；provider failure 不留可发布 Revision/Fragment。
- ADR-0037/0040/0041、Implementation Design §6.1：durable job + exact WorkerLease、outbox/检查点、`acquired → prepared → ready → active` 恢复。
- ADR-0018：新旧 Revision 用 pointer swap 隔离；index 只做候选发现，不是 authorization。

**Room-A 行为规格**

```text
accepted job
  -> acquired(canonical bytes + exact profile identity)
  -> embedding_requested(exact Fragment document)
  -> prepared(Fragments + validated vectors atomically persisted)
  -> indexed(all candidates complete)
  -> ready(authority/readiness revalidated)
  -> active(single Resource pointer CAS)
```

- `EmbeddingProvider.embed_batch` 输入 source-ordered contextual Fragment texts 和显式 provider/model/dimension/batch profile；provider endpoint/key/timeout 只来自 worker environment，不进入 record/repr/log。
- Worker 每个 durable effect 前核对 WorkerLease 的 Organization/job/source/resource/revision/workload/operation/generation/expiry/nonce 与当前 durable row；不能仅凭 job ID 或 Celery process identity。
- dimension 在 schema profile 中唯一固定。transport/status/parse/count/order/dimension/non-finite/float32-zero-vector 任一失败折叠为 content-free unavailability。
- 只有新或 replacement acquisition 调 provider；unchanged classification embedding calls=0。恢复自 `prepared/ready` 只复用已存 vectors，不重复收费调用。
- complete embedding document 与 immutable Fragment rows 在同一 transaction 写入并推进到 `prepared`。任一向量缺失时全部不写；不允许逐 Paragraph success 就在线可见。
- indexing 和 activation 都再次拒绝 missing/wrong-dimension vector。旧 Revision 在整个准备期间保持 active。
- external provider 和 deterministic CI twin 都需显式配置；生产无 twin fallback。test twin 只证明协议/确定性，不声称语义质量。
- 任何 LLM enrichment（related query/tag/term/summary）与 embeddings 分开：embedding 是已批准 representation 的派生值；LLM enrichment 必须先走 proposed/audit/snapshot/profile，绝不借 embedding job 直入 production index。
- active 后 candidate 仍是 content-free `CandidateRef`；Runtime 必须走 `AuthorizationKernel → AuthorizedProjection`，vector score 不授权。

**operator-visible signals**

- content-free：JobRef、Resource/Revision refs、lease generation、state、Fragment/vector counts、profile/provider/model refs（非 secret）、dimension、batch count、attempt count、generic failure category、checkpoint times、old/new active refs。
- content-bearing：只有受权 candidate inspection 能查看 contextual Fragment text；worker logs、queue、metrics、failure report 不打印 source text/provider response/secret。
- readiness 明确区分 `provider_unavailable`、`invalid_embedding_document`、`lease_stale`、`authority_revoked`、`profile_incompatible`、`ready`，但 public refusal 不泄露对象是否存在。

**接口形状草图**

```python
class EmbeddingProvider(Protocol):
    def embed_batch(self, request: EmbeddingBatchRequest) -> EmbeddingBatchDocument: ...

@dataclass(frozen=True)
class EmbeddingBatchRequest:
    profile_ref: EmbeddingProfileRef
    revision_ref: ContextRevisionRef
    fragments: tuple[ContextualFragmentInput, ...]  # source order

class SupplyPublicationPort(Protocol):
    def prepare(self, lease: WorkerLease, acquired: AcquiredCompilation,
                embeddings: EmbeddingBatchDocument) -> PreparedRevisionRef: ...
    def index(self, lease: WorkerLease, prepared: PreparedRevisionRef) -> ReadyRevisionRef: ...
    def activate(self, lease: WorkerLease, ready: ReadyRevisionRef,
                 expected_active: ContextRevisionRef | None) -> PublicationReceipt: ...
```

**测试 oracle 清单**

- unchanged acquisition → provider calls=0，active lineage 和 index rows 不变。
- N Fragments 必须返回 N 个同序、固定维度、float32-normalized、finite、nonzero vectors；count/order/dimension/NaN/Inf/zero 任一异常 → Fragment/Revision/pointer publication effects=0。
- provider timeout/status/invalid JSON 不泄漏 endpoint/key/body；记录统一 content-free category；旧 Revision 可读。
- crash after provider、during prepare、after prepared、after ready、before/after CAS：reclaim 仅发更高 generation；旧 lease/nonce mutation=0；prepared/ready recovery provider calls=0。
- revoked Membership/source、disabled Source、tombstoned Resource、expired lease 在 activation 前重检后 pointer=0。
- production missing explicit provider mode、选择 CI twin 或 dimension drift：启动/工作开始前拒绝。
- new active Revision 的每个 Fragment 恰有一个有效 vector；historical nullable rows不能被新发布路径复用为 active。
- vector hit 注入 cross-org/denied candidate：正文进入 rerank/assembly=0；index filters 不成为授权证据。
- LLM generated Problem/Tag/Term 未 audited/promoted 时，embedding/index writes=0。

**验证命令**

```bash
make lint
make typecheck
make test             # provider validation、deterministic twin、worker lease
make smoke
make integration      # tests/integration/test_fragment_embeddings.py + publication recovery
make security-gate
```

**工作量与依赖**

- 现有 ADR-0066 主链已实现时，补齐 preview/curation carrier 的增量估算：**7–12 engineer-days**（candidate embedding document 2–3，lease/recovery integration 2–4，status/UX 1–2，fault/security tests 2–3）。若从零实现完整链：**18–26 days**。
- 依赖：固定 schema dimension、explicit external provider config、deterministic CI twin、File job/checkpoint/reclaim、real PG17 + pgvector harness、profile/release compatibility。

### 3.5 产品形状反蓝图：serializer policy、mutable truth 与空测试桩

**上游路径 + permalink**

- 大 serializer 同时写业务行、做事务、状态、task dispatch 和 embedding：[`apps/knowledge/serializers/document.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/knowledge/serializers/document.py)。
- mutable `Document/Paragraph/Problem/Tag/Termbase/Embedding`：[`apps/knowledge/models/knowledge.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/knowledge/models/knowledge.py)。
- backend test stub：[`apps/knowledge/tests.py`](https://github.com/1Panel-dev/MaxKB/blob/32b2d885e47ad04639abd7a18490bf5937f9c072/apps/knowledge/tests.py)；固定 checkout 中 11 个 Django app 的 `tests.py` 均为三行桩。

**映射的本仓 seam / ADR**

- Deep Modules：ContextControl / ContextRuntime / ContextLearning；Runtime sealed，发布权单一。
- ADR-0018 immutable publication、ADR-0033 release owner、ADR-0080 executor-owned evaluation、Implementation Design §8.3 highest test seams。

**Room-A 行为规格**

- 状态：`untrusted_request → trusted_command → module_outcome → presented_response`；任何一步失败都终止，不能以 serializer fallback、默认 Organization、latest mutable row 或 direct DML 继续。
- 输入：router 只收 untrusted DTO 与 trusted ingress 已构造的 invocation ref；Module 自己加载当前 Organization、authority、profile、job/release generation。输出只为 typed outcome/receipt/refusal，不返回可供 caller 再写 pointer 的内部 store 对象。
- failure modes：认证/租户/authority 缺失、DTO 非 canonical、base generation stale、Module unavailable、transaction rollback、evidence selector 未执行，分别折叠为 closed refusal 或 gate FAIL；不得由 view/decorator catch 后标 success。
- HTTP serializer/router 只能：解析非受信 DTO、调用 trusted ingress 构造的 authority、映射 Module outcome；不得直接读写 release pointer、授权表、index、CurationSnapshot 或 security audit。
- 每个 effect 只有一个 owner 和一个 durable transaction boundary：Supply publish Revision；Learning promote ReleaseManifest；Control mutate source/access/policy；Runtime resolve；UI/feedback/evaluator 都无替代路径。
- 所有 active capability 必须有 catalog applicability、activation coverage 和 executed PASS/FAIL 三维证据。存在测试文件、manual click、mock、green process 或 serializer validation 不能代替。
- Room-B 不按 MaxKB class/table/API 逐一翻译；先实现本文 contract/property，再选本仓术语与结构。

**operator-visible signals**

- 正常操作面只显示 operation ref、Module、状态、generic refusal category、base/new generation、digest、commit/audit receipt 与时间；内容页面继续服从各自单独的 content-bearing inspection authority。
- release/evidence 面显示实际 collected selectors、skip/xfail/xpass/failed counts、capability tier、真实 seam 和 hard-oracle counters；“tests.py exists”“manual passed”“mock green”不能渲染为 PASS。
- restricted security detail 只进 DecisionAudit；public error、ordinary log 和 Learning report 不显示 raw denied content、SQL/provider exception、secret、内部 row existence 或跨 Organization count。

**接口形状草图**

```python
@router.post("/revision-previews")
def create_preview(body: UntrustedPreviewBody,
                   invocation: TrustedOperatorInvocation) -> PreviewResponse:
    command = preview_ingress.authorize_and_map(invocation, body)
    return presenter.render(revision_preview.create(**command))

# 路由层没有 store/session/vector/provider/release-pointer 参数。
```

**测试 oracle 清单**

- architecture/import test 禁止 UI/router/serializer 引用 release store、Kernel internals、vector adapter 或 direct DML。
- privilege test 证明 Control/Runtime/Supply/UI/evaluator credentials 无 release pointer DML；Learning login 也只能经专用 definer promote。
- 每个 active invariant selector 必须实际 collected 且无 skip/xfail/xpass；active-but-unexecuted=FAIL。
- deterministic fake 只提升 domain/contract 层，不能标 sandbox/live；真实 PG17、wire、provider 分层报告。
- candidate/feedback/manual test/LLM result 的 active pointer delta 恒为 0，直到 valid TrustedPromotionCall commit。

**验证命令**

```bash
make lint
make typecheck
make test
make catalog
make smoke
make integration
make security-gate
make check            # release 前最终合集；先启动真实 DB harness
```

**工作量与依赖**

- 估算：**6–10 engineer-days**，用于 architecture guards、privilege tests、catalog mappings 和 empty-stub replacement；不含前四项业务实现。
- 依赖：schema security manifest、evidence registry、真实非 owner roles、明确的 module import boundaries。

## 4. 不可借鉴清单与必须杀死的隐含前提

| 学习行为 | ContextEngine 独立实现 | 必须杀死的隐含前提 |
|---|---|---|
| preview 后 confirm | digest-bound immutable candidate；confirm 只创建 WorkerLease-bound job；ADR-0018 完整 pointer swap；ADR-0033 再 promote release | mutable Document row 同时是 source truth、current index 和 active release；用户点导入就立刻改变在线行为 |
| preview 暂存 source file | Organization/actor/purpose/TTL-bound preview blob + immutable audit + explicit expiry cleanup | 拥有临时 file ID 就有读取、confirm 或发布权；临时路径可以进日志 |
| Paragraph 可编辑 | 新 Revision + immutable Fragments；`tests/integration/test_zz_file_publication_recovery.py` 证明 old-or-new | 原地编辑内容还能保留可靠 provenance/citation；Paragraph 可独立授权 |
| Problem/related query | proposed `alternate_query` annotation，citation validation、human audit、CurationSnapshot、on/off eval | LLM 生成的问题天然正确，创建 row 后可立即提高 recall；命中次数是真值 |
| Tag | source metadata 与 curated annotation 分型、分别版本化 | 一个 mutable key/value row 可同时代表来源事实、人工治理、权限和 release behavior |
| Termbase | accepted term snapshot → 新 Compilation/Index profile → rebuild/eval/promote | 改一个术语 row 可以无版本、无重建地改变 tokenizer 与生产检索；索引派生状态是 source truth |
| Hit Test | canonical Runtime/HTTP seam 的 exploration；可提交 golden candidate | 手工 query 命中等于 regression/release gate；直查 index 与 Runtime 行为等价 |
| direct return | versioned Assembly/Runtime profile，只直出 ContextPackage 内完整 authorized Evidence，保留 citation | similarity threshold 是授权；document flag 可绕过 Kernel/egress；无命中时可输出无 Evidence fallback |
| 点赞/点踩 | binding-complete feedback signal → triage candidate | 用户反馈是真值、可跨 release 复用；自由文本可以直接进 Learning 或日志 |
| feedback improve | source candidate 或 CurationAnnotation candidate；`test_feedback_has_no_publication_authority` 证明零发布权 | 把模型 answer 复制为 Paragraph 并 embedding 就完成知识改进；模型内容等于 source evidence |
| document save 后 async embedding | ADR-0066 embedding document 与 Fragment 同事务进入 prepared；exact WorkerLease；旧 Revision 保持 active | DB commit 后 task dispatch 永不丢；job ID/Celery worker 是权限；部分 embeddings 可在线 |
| delete old embedding then rebuild | build-new → validate → index → CAS active；superseded artifact retained | 删除/重建窗口对读者天然原子；finally 状态等于真实成功 |
| mutable status string | append-only job events + durable checkpoints + separate publish watermark | 一个 row 的多位状态同时是操作进度、readiness、release truth 和审计历史 |
| index/filter | content-free CandidateRef → AuthorizationKernel → AuthorizedProjection；`test_candidate_security_regressions.py` 等 hostile oracle | knowledge/workspace filter、is_active 或 vector hit 已完成最终授权 |
| serializer/view 权限与 policy | trusted ingress + Deep Module interface + DB privileges/definer | transport decorator/serializer 校验就是不可绕过 security boundary；换 transport 可复制一套策略 |
| ContextControl 产品配置 | Control 只管 source/access/policy；profile 只由 Learning promotion | 配置后台天然拥有 profile publication；初始化/migration 可 seed active pointer |
| evaluator PASS | ADR-0080 executor-owned observation；报告无 publication authority | caller counters、空 events、callback/no-op client 可证明 clean；高 quality 可抵消 security |
| golden retrieval miss | ADR-0082 lineage map check；stale case 排除且整报 REFUSED | unresolvable expectation 等于模型 miss；可以用部分非 stale cases 出 release 分数 |
| canary/rollback | generation-bound candidate + same `ContextLearning.promote` + append-only audit | rollback 可直接改历史 pointer；A→B→A 可重用旧 candidate/base digest |
| 空 tests.py 与 UI 手测 | highest public seam + real PG17/non-owner/FORCE RLS + explicit hard-oracle counters | 测试文件存在、功能可点或 mock 通过就是 backend/security evidence |
| GPLv3 实现形状 | Room-A 本文 + Room-B 独立命名/代码/schema/tests；ADR-0074 | “只改一点”“只复制接口/SQL/UI”不算复制；依赖许可证能覆盖 MaxKB 粘合实现 |

落实时，以下 repo tests/ADR 是最小“杀前提”集合：ADR-0014/0018/0033/0066/0080/0082；`tests/unit/test_release_owner_architecture.py`、`tests/unit/test_feedback_has_no_publication_authority.py`、`tests/unit/test_eval_run_executor.py`、`tests/unit/test_stale_lineage_detector.py`、`tests/integration/test_fragment_embeddings.py`、`tests/integration/test_release_promotion.py`、`tests/integration/test_worker_lease.py`、`tests/integration/test_zz_file_publication_recovery.py`。新增实现应扩展这些最高 seam，而不是另建只测 serializer 的平行套件。

## 5. 推荐实现顺序 + 给 coordinator 的开放问题

### 推荐顺序

1. **先固化 Room-B 输入边界。** 将本文作为唯一 MaxKB 深挖输入；Room-B 人员不读 checkout。先写 nominal contracts、state machine、closed refusals、architecture/privilege tests，确认没有 MaxKB 名称/schema/API/SQL/UI 逐字迁移。
2. **先做最小 preview 候选，不接 publication。** 完成 deterministic `RevisionPreviewRef`、content-free 列表、受权 content inspection、edit-as-new-digest、expire/abandon。以 `active Revision delta=0`、`active Release delta=0` 验收。
3. **接现有 Supply publication。** confirm 只建 durable job；复用 exact WorkerLease、ADR-0066 embedding、publication recovery、old-or-new CAS。不要创建第二个 queue/process 或另一套 publication state machine。
4. **把 current corpus 观察与 release gate 接通。** Revision resource-active 后生成 ADR-0073 candidate；使用 ADR-0080 executor 和 ADR-0082 lineage map；由 release operator 调唯一 promote。此时才可声称候选影响在线行为。
5. **上线 manual Hit Explorer，但显式标“探索”。** 只走 canonical Runtime HTTP/generated SDK；支持一键生成 golden candidate，不支持手工 PASS。它既给后续 UX 反馈，也扩大真实 golden corpus。
6. **先做一个 `alternate_query` annotation 的 C1 薄切片。** feedback binding → propose → citation validation → human audit → immutable CurationSnapshot → frozen on/off eval → curation-on ReleaseCandidate。先证明 authority/compatibility，再做 Tag/Termbase。
7. **Tag 分型后再做 Termbase。** 先禁止 source_fact/curated 混用；Termbase 由于改变 tokenizer/index，必须最后接 profile rebuild、cost evidence 与 release gate。
8. **direct return 最后、默认关闭。** 使用 `verbatim_authorized_block` profile，通过专门 golden slices、ablation、canary 和 rollback 才激活；失败保持普通 ContextPackage 或 canonical refusal。
9. **最终以全门禁收口。** `make check`、executor-owned golden run、raw evidence/report、真实 PG17 security gate；任何 `[未取证]` 不升级 capability tier。

按最小可交付波次估算：Wave A（preview-only）8–12 days；Wave B（confirm + Supply/release）10–15 days；Wave C（explorer + feedback candidate）8–12 days；Wave D（alternate-query C1）10–15 days；Wave E（tag/term/direct-return）16–25 days。单人串行总量约 **52–79 engineer-days**，可在 contract 固定后由 UI、Supply、Learning 测试工作并行，但 release ownership 和 shared schema 仍需单一集成人负责。

### 给 coordinator 的开放问题

1. **谁拥有 preview draft 的 public application seam？** 建议它是 API process 内的窄 Curation/Intake application service，而不是扩大 `ContextControl` 的 canonical Interface；需 coordinator 确认是否写新 ADR，还是只作为内部 seam。
2. **confirm 与 promote 是否由不同角色执行？** 建议 curation reviewer 可 confirm import，只有 release operator 可 promote；若产品要求一人完成，仍应产生两份 nominal authority 和两次审计，不能合并能力。
3. **content-bearing preview 的保留与导出策略是什么？** 需要确定 TTL、owner-only storage、是否允许下载、离职/撤权后的清理、普通 operate log 是否只留 digest；在答案前不能实现持久 preview 默认值。
4. **`CompilationProfile` / `AssemblyProfile` 是新增一等合同，还是现有 profile 的内部组成？** 建议 Compilation 落入 Content/Index compatibility，Assembly 落入 Runtime/Curation compatibility；不要新增 active pointer。需要架构 owner确认命名与迁移边界。
5. **direct return 的产品语义是否允许绕过生成但不绕过 BotDelivery？** 建议仍由 BotDelivery/egress 消费 audience-bound ContextPackage，并带 citation；若要求引擎直接生成回答，则与“ContextPackage 是唯一输出、答案生成不进引擎”的边界冲突，应拒绝或新 ADR。
6. **第一种 curation kind 是否锁定 `alternate_query`？** 它最贴近 Problem/feedback 且 rebuild 成本低于 Termbase；若先做 tag/term，需要先补 profile rebuild 和 tokenizer impact evidence。
7. **canary 的可用 carrier 当前是什么？** 若没有受信 traffic allocation/exposure observation，报告必须保持 `[未取证]`，先做 offline release comparison + operator-controlled promote/rollback，不虚构在线 canary。
8. **preview 前后的内容评测是否需要独立 evaluator？** 本报告建议 deterministic `RevisionReadinessEvaluator` 只做编译/readiness，权威 release evaluation 仍唯一由 ADR-0080 executor 完成；若要让 preview evaluator判“质量通过”，必须先定义 tracked seam、threat model、golden lineage 与无 publication authority。

在这些问题未决时，Room-B 仍可安全实施 Wave A 的 preview-only contract 和零发布权 oracles；不得提前激活 curation-on、direct return 或新的 canary claim。
