---
title: 2026-07-26 Repo State Review and Course Correction
date: 2026-07-26
status: review record; decision boundaries owned by ADR-0061..ADR-0064
---

# 2026-07-26 仓库状态评审与航向修正

> 评审基线:工作树 `4471ee7`。方法:三路并行代码勘探(engine 核心 /
> tests 与安全门 / delivery 与 adapters/SDK),关键断言经维护者会话逐条
> 亲验后采信。所有代码事实均附 file:line 出处。本文档记录评审结论与
> 修正决策;边界本身由 ADR-0061 至 ADR-0064 固定。

## 一句话结论

授权宪法已经立宪、机器尚未通电:安全与治理工程的完成度罕见地高,而创始
论题中的核心能力——真实检索、结构化数据、adapter 广度、深度清洗——在
生产组合中当前为零。这不是执行力缺陷(执行纪律恰是顶级),而是投资方向
与论题之间未经显式裁决的分叉。本次评审将该裁决补上并沉淀为 ADR。

## 四视角评估

### 1. 代码视角

**上限——安全工程是真材实料。**

- 约 950 个测试函数(577 unit / 260 integration / 约 110 catalog / 6
  process,按测试函数计),测试:引擎代码 ≈ 68k:21k 行。
- Integration 全部运行于 digest 固定的真实 PostgreSQL 17 + pgvector
  (`compose.yaml:3`);session 级 autouse role guard 在会话开始对应用角色
  engine 断言非 owner、非 BYPASSRLS(`tests/integration/conftest.py:130`,
  `engine/persistence/role_guard.py:20`)。
- 53 张 tenant-owned 表 100% FORCE RLS,由 security gate 现场审计
  (`scripts/security_gate/rls.py:106`)。
- 门禁对自身做变异测试:真实执行 `NO FORCE ROW LEVEL SECURITY` 后断言
  审计降级为 52/53,再以同名 `USING (true)` policy 证明语义摘要不被名字
  欺骗(`tests/integration/test_m0_security_gate_rls.py:56,183`)。
- 真实锁竞争下的 Policy Epoch 并发测试
  (`tests/integration/test_access_policy_revocation.py:593`)。
- 30 个 migration 中除空 baseline 外均含带守卫的真实 `downgrade()`;全仓
  无 `pytest.skip`、无 TODO/FIXME。

**下限——机械债存在但不致命。**

- `_require_utc` 一类校验函数约 34 份拷贝散布于 11+ 模块。
- God module:`engine/runtime/construction.py`(1397 行,六个 gate 类 +
  kernel + Runtime + reference issuer 同居)、
  `engine/persistence/control_sources.py`(1084)、
  `engine/persistence/membership_context.py`(1072)。
- 77 个 `SECURITY DEFINER` 函数把大量业务逻辑压进 PostgreSQL——RLS 强制
  力的代价是逻辑演进走 migration、可调试性下降。此为双刃剑而非错误。

**中间是空的——生产组合当前交付零内容。**

- 默认 Runtime 构造不带 `candidate_index`(`adapters/http/app.py:224`),
  线上 `/v0/resolve` 恒返回空 ContextPackage(下用别名 Package);
  `/health` 自报 `runtime_delivery: NOT_ACTIVE`。
- 唯一检索实现是 SHA-256 精确短语匹配
  (`adapters/exact_phrase.py:26`,query 须与存储的 ContextFragment(下用
  别名 Fragment)正文或其派生 search phrase——列表项、表格单元格、代码体、
  标题文本——逐字节一致),且仅测试引用。代码中
  `tsvector|pgvector|embedding|rrf|bm25` 全仓零命中;pgvector 扩展已建
  (`infra/postgres/init/10-security-roles.sh:163`)但无使用者。
- File provider:仅单层平铺目录(`adapters/file_source.py:138` 拒绝
  子目录)、仅 `.md`、worker 硬编码 4096 字节上限
  (`applications/worker.py:84`)。

### 2. 结构视角

- **sealed AuthorizationKernel 与可插拔 seam 的边界(下称 Kernel-vs-seam)
  在代码中真实存在**,这是全仓结构上最值得肯定的
  部分:授权路径以 `type(x) is not T` 类型同一性检查封死(连子类都不收,
  `engine/runtime/construction.py:556`),`AuthorizedProjection` 只能在
  kernel 的 nominal scope 内构造(`engine/runtime/evidence.py:160`);而
  检索是干净的 Protocol seam,可整体替换。"安全封死、检索可换"的不对称
  是刻意的、正确的、且已兑现。
- 弱点:engine 内部模块边界有渗漏(`engine/supply/__init__.py` 直接转口
  `engine.control.file_imports` 符号;File 细节贯穿
  control/persistence/adapters 三层)。
- `applications/` 仅 212 行;worker 单 job 即退出,无调度器/队列/重试
  循环——"API + 独立 worker"的进程拓扑当前是形状而非能力。

### 3. 目标视角

- **以仓库自身计划为准绳:优等生。** 46 个 issue 关闭,M0/M1/M2 实质
  完成,处于 M3 早期;56 条 ADR;每个未激活能力都在 catalog 显式记为
  `NOT_ACTIVE`,无虚报。
- **以创始论题为准绳:分叉。** 四支柱现状——真实检索:占位符;结构化
  DB/API 族:路线图与领域模型零出现;adapter 群:一个 provider 且 seam
  为名义;深度清洗:仅确定性 Markdown 编译。
- 与四个公开参考仓(见
  `docs/research/2026-07-19-four-public-repositories-evidence.md`)呈镜像:
  证据基线显示它们集体薄弱的授权/撤销/租户隔离恰是本仓已建成者;它们各自
  擅长的摄取与检索广度恰是本仓未建者。此镜像可成为差异化,但必须出自
  自觉选择——本次评审即为该选择补办手续(ADR-0061)。
- **质量门倒挂:** 安全门达变异测试级,而 `eval/` 无 golden set,release
  report 中 Reliability/Quality/Budget 均 `not-evaluated`
  (`scripts/security_gate/report.py:606`)。检索质量当前零测量。

### 4. 可扩展性视角

- **一条干净的路:** 真实检索可只经 `candidate_index` seam 进入,授权层
  零改动。
- **一笔递延的债:** 第二个 provider 无可实现接口——`SourceKind` 单成员
  枚举(`engine/control/contracts.py:59`)、`FileCapabilityManifest` 直接
  作为 `SourceVersion` 字段类型(`engine/control/contracts.py:342`)、
  parser 直接 import 而非注入(`engine/persistence/file_imports.py:19`)、
  持久层 exact-type 检查禁止替换
  (`engine/persistence/file_imports.py:220`)。符合"第二用例前不抽象"
  纪律,但接入任何第二源前必有一次 seam 提炼,成本递延而非消失。
- **一个装不下的族:** 结构化 DB/API 活数据与
  `ContextResource -> ContextRevision -> ContextFragment` 的不可变快照发布
  语义冲突——无 Revision、无 tombstone、授权须下推至源。硬塞会腐蚀模型
  (裁决:ADR-0061)。

## 修正决策(grilling 收敛结果)

| # | 问题 | 裁决 | 固定于 |
|---|---|---|---|
| 1 | 仓库定位 | 完整 context 层:知识快照族 + 结构化获取族两族一约;结构化族 deferred-by-design,禁止伪装成 Revision 语义接入 | ADR-0061 |
| 2 | 牵引负载 | 维护者本人的真实工作负载 dogfood 牵引全部排期;禁止广度优先建"完整层" | ADR-0062 |
| 3 | 第一切片 | Slice A(下节);向量先行,混合检索为同 seam 的后置升级,由 golden set 失效证据触发 | ADR-0062 |
| 4 | 认证入口 | 显式配置的 dogfood 认证组合;默认组合维持 reject-all;仅简化"你是谁",不触碰授权链 | ADR-0063 |
| 5 | 流程重量 | 双车道:kernel 车道全仪式,product 车道轻流程;车道线 = 既有 sealed-vs-seam 线 | ADR-0064 |

## Slice A——第一次通电

目标:被服务的生产进程在显式配置的 dogfood 认证组合(ADR-0063)下,对
维护者的真实 Markdown 语料交付真实 Evidence,并被一个真实 caller 消费。
模块级默认组合按 ADR-0063 维持 reject-all 不变。

| # | 内容 | 车道 |
|---|---|---|
| 1 | File provider 扩容:递归目录、文件上限改为可配置(约 1MB),仍仅 `.md` | product |
| 2 | pgvector 单路检索:Supply 侧新增 embedding seam,Fragment 入库计算向量;query 向量近邻产出 CandidateRef;授权层零改动 | product |
| 3 | 生产组合通电:candidate_index 接入被服务的 Runtime 组合(首次内容载体激活);dogfood 认证组合按 ADR-0063 边界实现 | kernel(载体激活与认证组合均全仪式,见 ADR-0064) |
| 4 | 真实 caller:维护者工具经生成 SDK 或 HTTP 调 `resolve`,替代一个真实的手工翻找场景 | product |
| 5 | Golden set v0:自真实查询积累最初 20–50 条,为 Quality 门装上第一块电表 | product |

**显式不入切片:** Continue(维持 NOT_ACTIVE)与 OpenCitation 的任何新
carrier(ADR-0051 已激活的私有 File-backed carrier 维持不变)、结构化
获取族实现(仅按 ADR-0061 设计沉淀)、深度清洗/PDF、FTS+RRF 混合融合、
第二 connector。

## 标准调整清单(按优先级)

1. **执行 Slice A**(上节)——所有其他调整都以第一次通电为前提。
2. **PLAN.md 与实现设计的修订过**:以两族论题与 dogfood 牵引重述 M3 之后
   的排期(ADR-0061/0058 为权威;修订完成前,旧 roadmap 文本在此二 ADR
   之下阅读)。同批将 AGENTS.md Definition of Done 的 ADR 条目对齐
   ADR-0064 双车道规则(AGENTS.md 修订须走 doc-steward 工作流)。
3. **README 状态台账外移**:已由 #97 完成——`STATUS.md` 承接 capability
   ledger,README 重写并链接之。保留此条仅作记录;后续激活记账继续写入
   `STATUS.md`。
4. **结构化获取族设计**:当 dogfood 出现第一个真实 DB/API 需求时启动
   (ADR-0061 revisit trigger),先术语与 ADR,后代码。
5. **第二 provider 前的 seam 提炼**:接入任何第二源前,将
   SourceKind/capability manifest/parser 注入点提炼为真实接口;在此之前
   不做投机抽象。
6. **机械债(低优先级,product 车道顺手清理)**:校验函数去重;
   `construction.py` 按 gate 拆分;巨型 barrel 文件收敛。

## 本文档的地位

本文档是评审记录与执行参考,不是边界权威。边界权威是 ADR-0061 至
ADR-0064 与既有 accepted ADR;术语权威是 `CONTEXT.md`;冲突时以权威
文档为准。
