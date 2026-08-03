# 五仓借鉴总蓝图 — ContextEngine 可复刻实施路线

> 日期：2026-07-31 · 作者：coordinator（Claude）融会贯通五份独立 codex worker 评估
>
> 输入报告（同目录，全部固定 commit 静态取证，未运行上游系统）：
> - [`2026-07-31-ragflow-blueprint-evaluation.md`](./2026-07-31-ragflow-blueprint-evaluation.md)（Apache-2.0，copy+patch 可选区）
> - [`2026-07-31-dify-blueprint-evaluation.md`](./2026-07-31-dify-blueprint-evaluation.md)（Room-A）
> - [`2026-07-31-maxkb-blueprint-evaluation.md`](./2026-07-31-maxkb-blueprint-evaluation.md)（Room-A）
> - [`2026-07-31-onyx-blueprint-evaluation.md`](./2026-07-31-onyx-blueprint-evaluation.md)（MIT 非 ee 区，copy+patch 可选）
> - [`2026-07-31-openviking-blueprint-evaluation.md`](./2026-07-31-openviking-blueprint-evaluation.md)（Room-A，AGPLv3）
>
> **证据纪律**：公开 reference claim 只回引版本化的 [`2026-08-02 五仓证据基线`](./2026-08-02-five-public-repositories-evidence.md) 或其固定一手 permalink。Dify / MaxKB / OpenViking 三份仍是 **Room-A 维护者本地研究**，不能作为公开 provenance；Room-B 实现者只读其中规格与 oracle、不读上游源码。OpenViking 已形成候选五仓准入文档，但 legal sign-off 仍由 #205 的维护者决策关闭。本文不是法律意见，不替代逐项 legal review。

## 1. 五仓许可证矩阵与复用模式总账

| 仓库 | 固定 commit | 许可证事实 | 复用模式 | 本次 copy+patch 结论 |
|---|---|---|---|---|
| RAGFlow | `4391e03` | 根 Apache-2.0；**模型资产不在 Git 树内，许可未取证** | 源码区 copy+patch 可选；资产门未闭合 | **DOCX + PDF outline 两文件可进入注册流程**；PDF/OCR 九文件 blocked（模型/依赖审计） |
| Onyx | `2fb3dd1` | 非 `ee/` 全 MIT；`ee/` 企业许可 | MIT 区 copy+patch 可选；ee 仅 clean-room | **至多一个极窄 ABC 形状**（`interfaces_new.py`）待决策；推荐原生 Protocol + permalink 等价 |
| Dify | `120c38b` | 根 Apache-2.0 + 多租户附加限制，与本产品目的冲突 | 全部 root 代码 clean-room | **none**（`sdks/nodejs-client` MIT 可核验，但 generated SDK 严格更优） |
| MaxKB | `32b2d88` | GPLv3 §5(c) | 纯 clean-room，零代码/零依赖 | **none** |
| OpenViking | `49b1820` | 主项目 **AGPLv3**（§13 覆盖网络服务）；`crates/ov_cli` MIT↔Apache **冲突未消歧**；examples 逐路径混合（openwebui-plugin 明确 AGPL） | 主项目严格 clean-room | **none**（当前；generated SDK 路径更优，冲突消除前不复制） |

**已 vendored（不重复提议）**：`third_party/ragflow/deepdoc/parser/markdown_parser.py`（ADR-0079）、`third_party/onyx/connectors/` 四文件（ADR-0075）。

**一句话总账**：真正可能进入 `third_party/` 的新增代码只有 RAGFlow 的 `docx_parser.py` + `utils.py`（outline）两个文件，且要先过 format-neutral representation ADR 与依赖许可证门；其余全部价值以 clean-room 行为规格 + 接口形状 + 测试 oracle 的形式吸收。这与 ADR-0074/0075 的"按区域不按产品、Kernel 切割、唯一真相"完全一致。

## 2. 能力 → 主参考源归属表（融会贯通版）

每个 ContextEngine 区域只指定**一个主参考源**避免多源污染，负 oracle（反例）同样有价值。

| ContextEngine 区域 | 主参考源 | 辅助输入 | 负 oracle（必须杀死的形状） |
|---|---|---|---|
| Supply 编译：PDF/DOCX/结构保真 | **RAGFlow** deepdoc（copy+patch 候选） | — | loose dict/HTML 输出、parser 成功=发布、cell/word 成 Fragment、runner 联网下模型 |
| Supply 编译：Markdown v1/v2/v3 | 已冻结（ADR-0036/0038/0079） | — | 新 factory 静默重解释旧 Revision |
| Supply 执行/checkpoint/lease 生命周期 | **Onyx** connector framework（已 vendored）+ RAGFlow task_executor 行为 oracle | Dify indexing 状态可见性（Room-A） | Redis unacked=lease、heartbeat 续权、missing-tenant 落默认 schema（Onyx app_base 反例） |
| Supply 状态可见性（运营 UX） | **Dify** indexing lifecycle（Room-A） | MaxKB preview 状态 | mutable Document.status 同时是执行/可见性/授权真相 |
| Candidate discovery：单源 hybrid | **Onyx** lift 2（接口形状；原生 PG FTS+pgvector） | — | Vespa/OpenSearch SQL 移植、`bypass_acl`、index filter 授权 |
| Pre-Kernel fusion/dedupe | **Onyx** lift 1 差分 oracle | RAGFlow weighted_sum 对照 fixture | pre-Kernel 加权（ADR-0083 已禁止）、denied 位置影响合法顺序 |
| Runtime 多源编排（planner） | **Dify** dataset_retrieval（Room-A） | — | LLM router 输出是 source authority、caller 传 dataset set、hydration 前授权 |
| Same-Article expansion | **Onyx** lift 3 行为 oracle | RAGFlow TOC/parent（clean-room） | first-hit 授权继承 parent/neighbor（`search_utils.py:149` 显式反例）、document_id 继承权限 |
| Authorized rerank | **Onyx** lift 5 行为 oracle | RAGFlow rerank（clean-room） | provider wrapper 持 key 直调、float score 授权、`list[str]` 输入 |
| Query rewrite | **Onyx** lift 4 编排形状 | — | 任意 memory/history 进 rewrite、模型删掉 original query |
| PackageBudget | **Onyx** lift 6 行为 oracle | OpenViking 逐 hop 预算 | 固定 75-token 估算、stage-local meter、usage 写 0 |
| Progressive disclosure（密度分层） | **OpenViking** L0/L1/L2（Room-A） | — | depth/tier 产生权限、目录已搜尽声明 |
| Curation 运营 UX：preview/confirm | **MaxKB**（Room-A） | Dify 状态机 | mutable row 同时 source+index+release、临时 file ID=发布权 |
| Curation 知识单元：annotation/snapshot | **MaxKB** Paragraph/Problem/Tag/Termbase（Room-A） | — | mutable Tag row 双真相、LLM enrichment 直入索引、hit_num 回写 |
| Learning：session→候选 | **OpenViking** session extraction（Room-A） | MaxKB feedback improve | 模型 create/merge/delete active memory、从 ContextRun digest 反推正文 |
| Release：evaluate/promote/canary/rollback | 已固定（ADR-0033/0073/0080/0082）；**Onyx** swap_index 行为 oracle | MaxKB Hit Test/direct return | 两次 commit 切换、手测=门禁、quality 抵消 security |
| 交付：HTTP/SDK/MCP 产品面 | **Dify** workflow node 产品化（Room-A） | OpenViking MCP 13 tools 生命周期 | 每 transport 一套 auth、context string concat、宽 MCP 写面（remember/forget） |
| Observability/trajectory | **Dify** trace 变体轴 + **OpenViking** trajectory（二者收敛到同一红线） | — | raw query/full document/denied count 进 trace（Dify ops_trace_manager 反例） |
| Operator console UX | **OpenViking** Studio/Helper 映射到 ADR-0090 七类 job | MaxKB Hit Explorer | 同进程直调 DB、匿名浏览器继承 dogfood principal、score 显示 |
| Eval/parity 方法 | **RAGFlow** comparator 迁移方法（clean-room） | Onyx tests/README 四层 → 本仓 make 分层 | 任意 Python object 差异少=等价、回放生产写返回值 |
| 战略定位 | OpenViking = **产品层相邻竞品、协议层条件互补、安全架构仅战略参考** | — | 把 agent memory FS 当 runtime foundation |

## 3. 统一实施路线图（Wave 0–6）

五份报告各自的推荐顺序存在依赖与重叠，合并为七波。**退出条件（exit gate）全部是本仓已注册的 make 证据，不是上游一致性。** 工作量为增量 engineer-days（单人；已扣除三 seam 与两个 vendored subtree 的沉没成本）。

### Wave 0 — 治理与架构决策（3–6d，阻塞后续开工）

D1–D12 已于 2026-07-31 全部决策完毕（§5）。本 Wave = 把决定落盘为 ADR / issue / legal 动作，不写产品代码：

1. **D1 落盘：format-neutral representation ADR**（按 RAGFlow 报告 §3.1 配方）：族契约 + nominal locator 子类型（`TextByteSpan` / `DocxXmlLocator` / `PdfRegionLocator`）、FIGURE kind、hard bounds、refusal vocabulary。**ADR 落盘前不写 DOCX/PDF 产品代码。**
2. **D2 落盘：rerank-bridging ADR**：两 Package 时序（内部 pre-rerank Package → ADR-0052 `AuthorizedModelInput` → final Package），解除 lift 5 阻塞。
3. **D4 落盘：tokenizer-profile ADR + migration 计划**：ReleaseManifest 绑定真实 tokenizer，替换 `utf8-byte-token-v1` 的时机与 OpenAPI/schema 迁移步骤。
4. **D9 落盘：OpenViking 有条件准入流程**：legal 复核（AGPL §13 + `ov_cli` 消歧请求）→ 逐 claim permalink 固定 → 四仓基线修订为版本化五仓基线（更新 PLAN/STATUS attribution）。
5. **D6 落盘：新开 DOCX/outline 批准 issue**（不复用 #124）；**Room-A/B 边界登记**：三份 clean-room 报告标注为维护者本地研究，Room-B 提交只引本仓 requirements/ADR/tests。

### Wave 1 — 零代码风险的证据增量（5–9d，可与 Wave 0 并行）

1. RRF provenance 纠偏 + 差分 oracle（Onyx lift 1 + RAGFlow §3.2）：固定"上游 `search.py` 是 weighted_sum 不是 RRF"的文档事实；补 Onyx `test_search_utils` 等价 fixture（重写而非 vendoring 上游 test model）。退出：denied candidate 任意插入不改变合法顺序（`make test` + catalog）。
2. Adapter parity gate 通用骨架（RAGFlow §3.4）：canonical serializer + mutation-complete comparator + `ParityFixtureManifest`；首批覆盖 Markdown v3、SupplyChangePage、candidate fusion。退出：mutation matrix 全红/全绿符合预期（`make test`）。
3. Redaction canary 进 catalog（Dify §3.6 + OpenViking §3.4）：query/body/denied/credential canary 扫描 ContextRun/log/metric/DecisionAudit。退出：canary 命中=0（`make catalog`）。

### Wave 2 — Runtime 检索链收口（Onyx 固定顺序，19–31d）

严格按 ADR-0075 lift 顺序关闭**剩余 gate**（不是重写）：

1. **lift 2 hybrid**（3–5d）：接口 provenance 决策（vendoring 极窄 ABC vs 原生 Protocol——推荐后者，见决策 D2）；真实 PG17 benchmark（EXPLAIN、underfill、recall）；production activation 记录。external query embedding 保持 `NOT_ACTIVE` 直到计量闭合。
2. **lift 3 expansion**（4–6d）：把已完成的 `expand_fragment_window` 接入 resolve 编排；cross-Article refs 回送 Kernel；合并重叠窗口。退出：real PG + HTTP 证明 same-Article/current-Revision 继承、cross-Article 重授权、old-Revision/tombstone 拒绝。
3. **lift 4 rewrite**（5–8d）：`RewriteProfile` + one-shot EgressGrant + 共享 meter；最多 1 semantic + 3 keyword，original query 固定保留。carrier 在 final usage 可验证前保持 `NOT_ACTIVE`。
4. **lift 5 rerank**（7–10d + ADR 1–2d）：依赖 Wave 0 决策 D2 通过；混入 denied → gateway/assembler content bytes = 0 是 release veto。
5. **lift 6 budget**（4–6d）：每 resolve 单一 meter，替换 `construction.py` 中 usage=0 的 ad-hoc 计数；final Package + ContextRun 发布同一累计 usage。**这是前四个 model-backed lift 转 active 的总开关。**

### Wave 3 — DOCX + PDF outline copy+patch（10–15d，依赖 Wave 0 决策 D1）

按 RAGFlow §3.1 配方：复制 `docx_parser.py` + `utils.py::extract_pdf_outlines` → `third_party/ragflow/deepdoc/parser/`；patch 移除 rag_tokenizer/LazyImage/Pandas/吞异常；OOXML block order 遍历；typed locator；双进程 digest 确定性证明；依赖（python-docx、pypdf）逐包许可证文本补齐；SBOM + 制品内 notice。PDF/OCR 九文件**保持 blocked**：只做 `/tmp` source-only spike（8–12d，可并行），模型资产（layout/det/rec/tsr.onnx、xgb.model）许可/hash 任一门失败即永久停止，不把 sunk cost 当准入理由。产品化另需 15–22d 且不在本蓝图承诺内。

### Wave 4 — 多源 planner + 运营/curation UX（22–36d）

1. **Dify planner**（12–17d）：`AuthorizedSourceCapabilitySet`（server-derived，caller 只收窄）→ `QueryPlanner`（ROUTE_ONE 首版用 deterministic twin，model router `NOT_ACTIVE`）→ bounded FAN_OUT（required branch 失败整次 fail closed）。依赖 Wave 2 的单源 hybrid。
2. **MaxKB preview/confirm Wave A**（8–12d）：digest-bound `RevisionPreviewRef`、受权 content inspection、edit-as-new-digest、confirm 只建 durable job（接现有 Supply，不建第二 queue）。退出：active Revision/Release pointer delta = 0。
3. **alternate_query annotation 薄切片**（10–15d，C1）：propose → citation validation → human audit → immutable CurationSnapshot → frozen on/off eval。Tag/Termbase/direct-return 明确排后（Termbase 改 tokenizer，必须最后）。

### Wave 5 — Progressive disclosure + console + thin MCP（21–32d，依赖 Wave 2/4）

1. **tiered AssemblyProfile**（6–9d，OpenViking §3.1）：`abstract/overview/detail` 只是**信息密度**不是存储层/ACL；每 hop 预留/结算同一 meter；同 Article lineage 继承、跨 Article 重授权。
2. **Evidence Console 七类 job**（7–11d，OpenViking §3.5 + ADR-0090）+ Manual Hit Explorer（MaxKB §3.3，3–5d）：只走 `/v0/resolve`；score/rank 明示 unavailable；Control 一次一操作。
3. **Authorized browse**（8–12d，OpenViking §3.2）：**等待 OpenCitation carrier 正式激活后才做**；在此之前 console 只对当前 Package 纯投影。
4. **thin MCP parity**（6–9d，OpenViking §3.6 + Dify §3.4）：初版恰好一个 `resolve_context`，首个 consumer 锁定 **Codex**（D10）；HTTP/SDK/MCP 对同请求 Package digest 等价；不复制 read/list/remember/forget。

### Wave 6 — 长期/条件项（不承诺排期）

- PDF/OCR 产品化（27–42d）：受模型资产门 + 单一 runtime target 决策双重否决。
- session-derived Learning input（10–15d + retention 系统另估）：须先接受独立 consent/加密/retention ADR（D8），否则永久 `NOT_ACTIVE`。
- Onyx ee 权限同步编排：永远 clean-room，仅作行为规格。

**总量**：Wave 0–5 约 **80–129 engineer-days**（不含 Wave 6、production auth、live connector、外部 legal 等待）。Wave 1/2 与 Wave 0 部分并行；Wave 3 与 Wave 2 不共享文件可并行；但每次合入共享 schema 前必须跑一次 `make integration` + `make security-gate` 最高 seam 证据。

## 4. 跨报告一致性核对（agent 互证结果）

五份独立报告在以下点**互相印证或纠正**，可信度高：

1. **RRF 归属纠正**（RAGFlow 发现，Onyx 印证）：RAGFlow `search.py` 用后端 `weighted_sum(0.05,0.95)`，**不是 RRF**；真正的 weighted RRF 函数在 Onyx `search_utils.py`。本仓 owned RRF 的 provenance 文档不应错误归因 RAGFlow。
2. **pre-Kernel 加权禁令**（Onyx ADR-0083 + RAGFlow cut line + Dify §3.2 三方一致）：fusion 只携 content-free evidence，权重只在授权后 admitted positions 上计算。
3. **hydration 反例收敛**（Dify formatter + Onyx `_retrieve_adjacent_chunks:149` + RAGFlow `retrieval_by_children`）：三个上游都显式存在"首 hit 授权覆盖扩展"的隐含前提，全部列为 must-kill。
4. **observability 红线收敛**（Dify §3.6 + OpenViking §3.4）：两份独立报告给出逐字段相同的 ContextRun 存活/禁止信号表 → 直接作为 redaction catalog 输入。
5. **copy+patch=none 收敛**（Dify SDK + OpenViking CLI/examples + Onyx 大多数 lift）：generated SDK / 原生实现 + permalink 在四个独立评估中都胜出，只有 RAGFlow 两个解析文件值得注册。
6. **现状感知**（Onyx 独有，已代码核对属实）：三 keystone seam 与 lift 1/2 已在 `engine/runtime/{prekernel_fusion,authorized_ranking,budget,model_inference}.py`、`engine/persistence/supply_execution.py`、`adapters/{hybrid,fts,pgvector}.py` 落地；路线图因此是"关 gate"而非"重写"。
7. **新发现的许可证事实**：OpenViking `ov_cli` Cargo.toml 自报 MIT 与父目录/README Apache-2.0 冲突；Dify `sdks/php-client` 仅 README 称 MIT（未取证）；RAGFlow ONNX/XGBoost 资产不在 Git 树（未取证）。三者都按"冲突/未知即停止"处理。

## 5. 决策记录（2026-07-31 stometa 全部确认）

12 项决策已全部作出（均采纳推荐方案）。Wave 0 的剩余工作 = 把这些决定**落盘为 ADR / issue / legal 动作**，而不是继续讨论。

| # | 决策结果 | 落盘动作 | 来源 |
|---|---|---|---|
| D1 | **format-neutral 族契约 + nominal locator 子类型**：`ParsedDocument` 提升为格式中立族（共享 publication interface、canonical serialization、hard bounds），locator 分 `TextByteSpan`/`DocxXmlLocator`/`PdfRegionLocator`；**新增 FIGURE structural kind**，image bytes 走独立 bounded artifact policy | 写 representation ADR（4–6d），未决前不写 DOCX/PDF 产品代码 | RAGFlow Q1/Q3 |
| D2 | **两 Package 时序**：Kernel → 内部不交付的 pre-rerank Package → ADR-0052 `AuthorizedModelInput`（+ one-shot model EgressGrant）→ rerank = Evidence exact permutation → final Package（独立 final-hop grant）。不发明第二种同名 nominal type。**协调者诠释（2026-07-31，review round 2 分歧裁决，claude 认同 / codex 保留）**："单一 nominal contract" 指单一契约定义（构造规则 + canonical serialization + digest），跨进程以 digest-equivalent twin 实例化（ADR-0052 双语言 digest 权威先例）；D2 禁止的是同名不同构造规则的第二契约（如 projection-fed），不禁止同一契约的 twin。已写入 ADR-0095 clause 2 | 写 rerank-bridging ADR，解除 lift 5 阻塞 | Onyx Q1 |
| D3 | **原生 Protocol + permalink**：不 vendor Onyx ABC；在 ADR/MODIFICATIONS 记录 `adapters/hybrid.py` 是 Onyx `interfaces_new.py@2fb3dd1`（sha256 `2285d0cb…`）HybridCapable 形状的等价独立实现 | ADR 补 provenance 段落；无 third_party 注册 | Onyx Q2 |
| D4 | **model-backed lift 激活前引入 ReleaseManifest 绑定的真实 tokenizer**（pinned 制品 + digest），lift 6 一步做成完整 meter（tokens + calls/cost/elapsed 累计），含一次 schema/OpenAPI migration | tokenizer-profile ADR + migration 计划 | Onyx Q3 |
| D5 | **推荐组合**：先单源 hybrid 收尾（Wave 2）；ROUTE_ONE 只用 deterministic rule/twin，model router `NOT_ACTIVE`；required branch 失败整次 fail closed，无 partial Package | planner 规格按此冻结；model router 重开需新 ADR | Dify Q1/Q2/Q3 |
| D6 | **新开 DOCX/outline 专用批准 issue**（不复用 #124）：列 source_paths、固定 commit、双 hash、依赖许可证文本 owner、SBOM checklist | 开 issue；python-docx/pypdf 许可文本随注册 PR 落盘 | RAGFlow Q6 |
| D7 | **窄 Curation/Intake application service**（不扩 ContextControl canonical Interface）；**双角色分离**：reviewer 可 confirm import，只有 release operator 可 promote；一人流程也产生两份 nominal authority + 两次审计 | preview seam 设计文档；privilege test 证明无越权 | MaxKB Q1/Q2 |
| D8 | **立项但排 Wave 6**：写独立 consent/最小字段/加密/retention/删除-export/RLS ADR；此前 Learning 候选只从 authorized Package evidence + explicit feedback 产生，原始 transcript extraction 保持 `NOT_ACTIVE` | retention ADR 骨架进 Wave 6 排期 | OpenViking Q4 |
| D9 | **有条件准入 OpenViking 为第五仓**：白名单 = context FS/L0–L2 tiering、session→candidate UX、observable trajectory、agent exposure；黑名单 = 多租户授权/安全证明、runtime foundation、任何 copy+patch。流程：legal 复核 → 逐 claim permalink 固定 → 四仓基线修订为版本化五仓基线；同流程向 upstream/legal 发 `ov_cli` MIT↔Apache 消歧（即使消歧也不复制） | legal 复核任务 + 基线修订 PR | OpenViking Q1/Q7 |
| D10 | **首个 thin MCP consumer = Codex**：工具面恰好一个 `resolve_context`；成功标准 = HTTP/generated SDK/MCP 对同请求 digest 等价或同一 closed refusal | 更新 exposure-spike-mcp-vs-api 研究文档 | OpenViking Q6 |
| D11 | **首切片锁 `alternate_query`**（propose → citation validation → human audit → CurationSnapshot → frozen on/off eval）；**direct return = `verbatim_authorized_block`**，versioned AssemblyProfile、BotDelivery 消费 audience-bound Package 带 citation、默认关闭经 canary 激活；Tag/Termbase 排后 | C1 切片 issue；引擎不生成答案边界不变 | MaxKB Q5/Q6 |
| D12 | **离线安装 + 单 runtime target**：模型 bundle 经独立受控安装取得，逐项固定 revision/sha256/license/model card，只读 digest-bound；runner 零网络；首版只支持一个 CPU/ONNX Runtime/arch 组合；资产门任一失败 PDF/OCR 永久停止 | 资产门 checklist 进 D6 父级跟踪 | RAGFlow Q4/Q5 |

## 6. 合规与公开口径清单（Definition of Done 附加项）

- [ ] 任何新增 copy+patch：`third_party/<upstream>/` 具备 `LICENSE.upstream`、`UPSTREAM.toml`（repo/commit/paths/excluded/hashes/mode/approval）、`MODIFICATIONS.md`、`patches/`、`sbom.cyclonedx.json`，且 wheel/sdist/npm/container **物理包含** notice + SBOM（Git-only attribution 不合规）。
- [x] Dify/MaxKB/OpenViking 相关 PR 描述不引用三份 Room-A 报告为公开 provenance；只引本仓 ADR/tests/版本化五仓基线。
- [ ] OpenViking 准入前，README/PLAN/STATUS/设计文档不出现 OpenViking 作为 authority 的引用。此项只在 #205 maintainer/legal sign-off 记录且候选准入 PR 可合并时闭合；当前 draft 不把候选基线视为已准入 authority。
- [x] OpenViking 公开 authority 只通过版本化五仓基线的固定四类白名单；Room-A 报告不作为 provenance。
- [ ] 每个 Wave 的 STATUS.md 更新区分 Active / `NOT_ACTIVE`；bounded proof 不升级为 general claim；`make smoke` 绿不升级为 publication/security 证明。
- [ ] RAGFlow PDF/OCR：资产门（ONNX/XGBoost 逐项 revision+hash+license+model card）未闭合前，profile 报 `NOT_ACTIVE`，spike 代码留在 `/tmp` 或 runtime-tree 外并留删除证明。
- [ ] 全局不变量逐 release 复核：Unauthorized Evidence = 0、wrong-Organization effect = 0、missing tenant context = fail closed。

## 7. 如果只做一件事

每个仓库对 ContextEngine 的**单一最高价值贡献**，按可开工顺序：

1. **Onyx**：六 lift 的剩余 gate（尤其 lift 6 统一 meter）——把已建成的检索链从 "NOT_ACTIVE" 变成可证明 active 的最后一公里（24–37d）。
2. **RAGFlow**：DOCX + PDF outline 两文件 copy+patch——用最小法律风险打开第一个非 Markdown 格式（10–15d，依赖 D1）。
3. **MaxKB**：preview→confirm 运营心智——把 ingestion 变成可审计运营动作，且全程 pointer delta=0（Wave A 8–12d）。
4. **Dify**：AuthorizedSourceCapabilitySet + bounded fan-out——多源编排的唯一干净形状（12–17d，依赖 Wave 2）。
5. **OpenViking**：tiered AssemblyProfile + 单工具 thin MCP——progressive disclosure 与 agent exposure 的产品化，但安全上仅战略参考（12–18d 首批）。
