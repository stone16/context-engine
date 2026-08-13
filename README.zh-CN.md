# ContextEngine

[![CI](https://github.com/stone16/context-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/stone16/context-engine/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](./LICENSE)
[![Status](https://img.shields.io/badge/status-pre--release-orange.svg)](./STATUS.md)

**一个权限感知的上下文交付引擎。** 上游连接团队的知识源，下游把**经过授权、
带证据、有预算**的 ContextPackage 交付给 agent 应用与 IM bot。

[English](./README.md)

---

多数知识库产品回答的是「怎么存、怎么搜」，多数 RAG 工具链回答的是「怎么找到
最近的 chunk」。ContextEngine 存在的理由是：真正卡住「在公司内部上线一个可信
助手」的，是另外两个问题。

## 一、此刻这个 audience 有权知道什么？

单靠检索回答不了这个问题。在 ContextEngine 里，索引永远不返回可交付正文——它
只返回 `CandidateRef`。每一个候选都必须先经过 sealed `AuthorizationKernel` 完成
精确授权与字段投影，**任何承载内容的动作才被允许发生**。水合、精排、相关性
模型、装箱，全部只接受 `AuthorizedProjection`。每一次父级或邻居扩展都逐项
重新授权。

源 ACL 证据被明确分为 `Live`、`Mirrored`、`Weak` 三类。`Weak` 只用于源本身确实
缺乏细粒度 ACL 的场景，**它绝不是 `Live` / `Mirrored` 校验失败时的降级回退**
——那种情况一律 fail closed。

## 二、知识库由谁来组织？

组织成本是任何团队知识库最大的隐性成本。ContextEngine 把其中可自动化的部分
交给 agent——语义去重、过期标记、术语沉淀——而把 audit 留给人。所有 AI 产出的
标注都要先提案、经确认，再作为独立的不可变 `CurationSnapshot` 原子发布。已发布
的内容 Revision 永远不被就地修改。

## 项目状态

> **Pre-release。不可用于生产，也无意伪装成可以。**

ContextEngine 按里程碑逐步构建，每一项能力只在**可执行的证据**证明之后才被
激活。未经证明的能力一律标记为 `NOT_ACTIVE`，而不是悄悄留一个桩——包括在运行中
服务自己的 `/health` 响应里。

| 领域 | 状态 |
|---|---|
| 真实 PostgreSQL 17 + pgvector 底座、角色隔离、FORCE RLS | 已激活 |
| Organization / Membership / `UserActor` 租户事务 | 已激活 |
| Sealed `ContextRuntime.resolve` 返回 tenant-safe ContextPackage | 已激活 |
| 对抗性候选索引下的 exact-authorized Evidence tracer | 已激活 |
| OpenAPI v0 wire 契约 + 生成式 TypeScript SDK + breaking-change 门禁 | 已激活 |
| 私聊 File-backed bot 交付闭环（确定性 twin） | 已激活 |
| 自主 File import dispatch + 有界的过期 lease reclaim | 已激活 |
| Loopback single-Membership File dogfood `Acquire` | 显式配置后激活 |
| 一个 maintainer-local、spawn-per-session 的 stdio MCP `Acquire` translator | 显式配置后激活 |
| Evidence Console 内的 private File citation reopening | 显式配置后激活 |
| 生产认证（OAuth / JWT） | `NOT_ACTIVE` |
| 通用 Source ACL / 检索、`Continue`、Evidence Console 以外的 `OpenCitation` | `NOT_ACTIVE` |
| 飞书 / Slack / Google Docs 实连接器、群聊 | `NOT_ACTIVE` |

**[→ 完整能力台账与逐 Issue 证据边界（STATUS.md）](./STATUS.md)**

路线图与里程碑退出条件见 [PLAN.md](./PLAN.md)。

## 快速开始

### 前置依赖

| 依赖 | 版本来自哪里 | 用途 |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | — | 依赖解析，由 `uv.lock` 锁定 |
| [Python](https://www.python.org/) | [`pyproject.toml`](./pyproject.toml) 的 `requires-python`——`uv sync` 会自动装好匹配的解释器 | 引擎、adapters、worker |
| [Node.js](https://nodejs.org/) | [`sdk/typescript/.node-version`](./sdk/typescript/.node-version)——`nvm use`、`fnm use`、`asdf` 都会自动读取 | TypeScript SDK、ActionPlane、BotDelivery |
| Docker（含 Compose） | 服务版本固定在 [`compose.yaml`](./compose.yaml) | 真实 PostgreSQL + pgvector 测试底座 |

上表每个版本都声明在已提交的文件里，所以这里一个都不重复——装好工具，让它自己
读仓库。

### 安装与验证

```bash
make install
```

`make install` 除了同步锁定的 Python 环境，**还会对四个 TypeScript 工作区
（`sdk/typescript/`、`sdk/typescript-v1/`、`action_plane/typescript/`、
`bot_delivery/typescript/`）执行 `npm ci`**，并安装本地 MCP 证据套件需要的
`mcp` extra。Node 不是可选项。

从 clean checkout 运行与 CI 完全相同的门禁：

```bash
make install && make db-up && make check && make db-down
```

### 首次运行：从 File corpus 到公开查询

这是当前唯一 content-bearing served composition 的有序首跑：一个有界、
loopback-only、single-Membership 的 File carrier。migration、Control、release、
Runtime、scheduler、worker 使用彼此分离的 credential plane，绝不能拿一个 plane
的凭据替代另一个。请在版本控制之外准备 owner-only 的
`.context-engine/operators.env`；其中仅放下方详细操作文档列出的本地身份、secret、
profile、corpus root 与 reviewed release evidence 路径，不要把 secret 粘贴到命令中。

Promotion 前必须准备经审阅的 Security、Reliability、Quality、Budget 四门发布证据。
`M0 Security gate` 不会提供 Reliability、Quality 或 Budget PASS，也不能替代这些
独立门禁。

从 clean checkout 严格按以下顺序执行。`source` 只用于加载数据库底座生成的
role-isolated contract 和 operator 自己的 owner-only 配置：

```bash
make install
make db-up

set -a
source .context-engine/database.env
source .context-engine/operators.env
set +a

# 干净数据库上的首次全 plane 只读基线预期返回 not_ready。
uv run context-engine-control preflight

uv run context-engine-control migrate

# 任何依赖数据库的 setup 前，migration plane 必须单独 ready。
uv run context-engine-control preflight --plane migration

# 将 registry 中精确固定的模型物化到 Supply 与 Runtime 共用的新 durable path；
# 该操作可能下载已登记的模型 bytes。
uv run context-engine-model-materializer \
  --role primary \
  --destination "$CONTEXT_ENGINE_WORKER_EMBEDDING_MODEL_DIR"

# 继续 mutation 前，Control、Supply、Release 与 pinned local model 必须 ready。
uv run context-engine-control preflight \
  --plane control \
  --plane supply \
  --plane release

uv run context-engine-dogfood-seed \
  --organization-id "$CONTEXT_ENGINE_DOGFOOD_ORGANIZATION_ID" \
  --user-id "$CONTEXT_ENGINE_DOGFOOD_USER_ID" \
  --membership-id "$CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_ID" \
  --provision-release-operator-grant \
  --file-import-service-principal-id \
    "$CONTEXT_ENGINE_WORKER_SERVICE_PRINCIPAL_ID"

uv run context-engine-control register-file-source \
  --organization-id "$CONTEXT_ENGINE_OPERATOR_ORGANIZATION_ID" \
  --display-name "$CONTEXT_ENGINE_FILE_SOURCE_DISPLAY_NAME" \
  --root-ref "$CONTEXT_ENGINE_FILE_ROOT_REF" \
  --idempotency-key "$CONTEXT_ENGINE_FILE_SOURCE_IDEMPOTENCY_KEY"

# 用上一条命令返回的 sourceRef 替换这个 operator-supplied identifier。
export CONTEXT_ENGINE_FILE_SOURCE_REF='<returned sourceRef>'
uv run context-engine-control activate-change-feed \
  --organization-id "$CONTEXT_ENGINE_OPERATOR_ORGANIZATION_ID" \
  --source-ref "$CONTEXT_ENGINE_FILE_SOURCE_REF"
uv run context-engine-control activate-delete-observations \
  --organization-id "$CONTEXT_ENGINE_OPERATOR_ORGANIZATION_ID" \
  --source-ref "$CONTEXT_ENGINE_FILE_SOURCE_REF"
uv run context-engine-control scan \
  --organization-id "$CONTEXT_ENGINE_OPERATOR_ORGANIZATION_ID" \
  --source-ref "$CONTEXT_ENGINE_FILE_SOURCE_REF"

# 重复执行，直到 content-free JSON outcome 为 no_work。
uv run context-engine-worker --dispatch-file-once

uv run context-engine-control promote-release \
  --organization-id "$CONTEXT_ENGINE_OPERATOR_ORGANIZATION_ID" \
  --evidence-file "$CONTEXT_ENGINE_RELEASE_EVIDENCE_FILE"

# 启动 content service 前，完整 bounded composition 必须 ready。
uv run context-engine-control preflight

# 在独立 terminal 中保持这个前台进程运行。
uv run context-engine-api --host 127.0.0.1

# 在第二个、加载同一 owner-only environment 的 shell 中执行。
uv run context-engine-context query \
  "Which current evidence governs this question?"
```

公开 caller 会通过 loopback HTTP seam 发起一个新的 `Acquire`；成功结果必须是带
Evidence 的 `ContextPackage`，任何 closed refusal 都会中止首跑。当前 served
composition 只使用 pgvector 候选发现。Hybrid retrieval 已实现，但未在该 carrier
中激活；production/multi-user auth、remote exposure、group/public delivery、
`Continue`、external/network embeddings 与非 File provider 也都保持
`NOT_ACTIVE`。`preflight` 是时点性的只读 readiness 观测，不会迁移、seed、scan、
推理、promotion、启动进程或激活 carrier。

### 启动 API

显式指定监听地址，使下面的示例自成一体——默认值与完整参数集
（`--host`、`--port`、`--log-level`）见 `context-engine-api --help`：

```bash
uv run context-engine-api --host 127.0.0.1 --port 8137
```

```bash
curl http://127.0.0.1:8137/health
```

```json
{
  "status": "ready",
  "service": "context-engine-api",
  "version": "...",
  "runtime_delivery": "NOT_ACTIVE"
}
```

`runtime_delivery: NOT_ACTIVE` 是**预期且正确**的：默认应用拒绝一切 credential，
且不做任何内容 I/O。公开 wire 契约是 `POST /v0/resolve`，冻结在
[`openapi/v0/openapi.json`](./openapi/v0/openapi.json)。

### 启动 worker

Supply worker 是独立于 API 的进程，一个入口、四种模式：

```bash
uv run context-engine-worker --test-mode           # 确定性 no-op 生命周期
uv run context-engine-worker --run-file-job        # 一个精确签名的 File import job
uv run context-engine-worker --dispatch-file-once  # 一次确定性 dispatch 周期
uv run context-engine-worker --dispatch-files      # 长运行 dispatch 循环
```

`--test-mode` 输出 `job_behavior: NOT_ACTIVE`，表示默认 CLI 没有配置生产签名
密钥来源、queue loop 或真实 ingestion handler。

`--dispatch-files` 是生产长运行入口：无工作结果时按**服务端固定的一秒间隔**
轮询，并在 `SIGTERM` / `SIGINT` 时结束。

所有 dispatch 模式**只**读取 role-specific scheduler、worker URL、WorkerLease
签名密钥，以及服务端 JSON root registry（`CONTEXT_ENGINE_WORKER_FILE_ROOTS_JSON`）。
**调用方不得提供 Organization、Source、job 或 token**——这正是该边界的意义。
输出仅限 `dispatched` / `no_work` / `refused`。

已激活的本地 carrier 选择 `qwen-local`，并通过
`CONTEXT_ENGINE_WORKER_EMBEDDING_MODEL_DIR` 提供 hash-verified 模型目录。
Issue #217 已完成累计 public accounting contract，不再是 blocker。
External/network worker embeddings 仍为 `NOT_ACTIVE`，因为没有 accepted activation
decision 准入该 composition。

Lease 校验使用 worker 的 PostgreSQL 时钟，与数据库签发时间处于同一时间域，
不依赖 worker 宿主机时钟对齐。worker 基础设施不可用会**终止** dispatch，而不是
继续 claim 并滞留后续 job。文件/内容失败仅在该 job 已持久化为 terminal failed，
或当前 authority 拒绝该精确 failure transition 之后，才返回 `refused` 并继续调度。

File dispatch、reclaim 与 delete execution 的激活边界记录在
[STATUS.md](./STATUS.md)。

### 开发命令

```bash
make install        # 同步锁定 Python 环境 + 四个 TS 工作区 npm ci
make build          # 构建 wheel 与 sdist
make lint           # Ruff
make typecheck      # strict mypy + TS typecheck
make test-python    # 快速 Python-only unit lane（不属于 Definition-of-Done 证据）
make test           # 完整 unit contract（含必需的 TS build/test）
make catalog        # 安全目录静态测试与校验
make smoke          # API / worker 进程 smoke 套件
make db-up          # 启动固定版本的 PostgreSQL 17 + pgvector 底座
make db-down        # 停止底座，保留 disposable data volume
make db-reset       # 只销毁并重建该 disposable volume
make integration    # 真实 PostgreSQL integration/security harness
make security-gate  # 可执行的 M0 安全否决门（需先 make db-up）
make check          # 以上全部（需先 make db-up）
```

底座首次启动时，会在被 Git 忽略、权限 `0600` 的 `.context-engine/database.env`
生成随机凭据。该文件是本地 migration、runtime、worker 与安全测试连接配置的
唯一实时来源，并为每个 checkout 生成独有的 Compose project 身份，使并行的
worktree 之间永不共享容器、网络或数据卷。镜像与拓扑版本固定在
[`compose.yaml`](./compose.yaml)，PostgreSQL 只绑定一个动态选择的 `127.0.0.1`
端口。migration、runtime、worker 使用不同角色，且 runtime 绝不回退到 migration
或 bootstrap 凭据。

`make security-gate` 只发现并执行**已登记的** M0 安全证据，核对真实 PostgreSQL
的 RLS inventory，并把机器可读的原始证据与一份独立的 release-gate 报告写入
`.context-engine/security-gate/`。由于 Reliability、Quality、Budget 尚未进入 M0
范围，该报告只给出 `m0SecurityDecision`，并把其余三项明确记为
`not-evaluated`——安全门通过**永远不会**被写成整体可发布的 PASS。

## 架构

### 三个循环

| 循环 | 职责 | 关键对象 |
|---|---|---|
| **Supply** | 源 → 可信候选：采集、解析、切分、索引、原子发布 | `ContextSource` / `ContextResource` / `ContextRevision` / `ContextFragment` |
| **Runtime** | 认证调用 → ContextPackage：候选、授权投影、相关性、装箱 | `CandidateRef` / `AuthorizedProjection` / `ContextRun` / `ContextPackage` |
| **Learning** | authorized-only trace → 可发布的改进：评测集、切片门禁、版本化 profile | golden set / `ReleaseManifest` / `CurationSnapshot` |

### 唯一的在线公开契约

下方是架构契约，不代表其中每个可选 relevance stage 都已在 served carrier 中激活。
当前有界 composition 只使用 pgvector 候选发现；hybrid/FTS fusion 与 Runtime
rewrite/rerank/select 均为 `NOT_ACTIVE`。

```text
ContextRuntime.resolve(AuthenticatedInvocation, TrustedDeliveryContext,
                       Acquire | Continue | OpenCitation)

  → 查询理解 + candidate recall
  → CandidateRef                        ← 不携带任何可交付正文
  → AuthorizationKernel                 ← 精确授权 + 字段投影
  → AuthorizedProjection                ← 第一个承载内容的值
  → 授权后水合 / 可选 relevance stages
      + small-to-big 扩展，逐项重新授权
  → PackageBudget 装箱 + sufficiency 信号
  → ContextPackage                      ← citations / purpose / TTL / asOf
```

这是 Runtime **唯一**的公开能力。HTTP 是 V1 的服务端 ingress；TypeScript SDK 是
生成式 HTTP client，不是第二条 transport。ADR-0103 只激活一个 maintainer-local、
spawn-per-session 的 stdio MCP `Acquire` translator；所有 broader MCP carrier
仍为 `NOT_ACTIVE`。

`Continue` 使用 principal-bound、one-shot 且累计预算的 token。`OpenCitation`
使用本身不携带任何授权能力的 opaque `CitationOpenRef`——每次打开都重新认证并
重新授权。

### 仓库结构

```text
engine/            sealed 内核——不含 HTTP，不含厂商 SDK
  runtime/           resolve() 编排、AuthorizationKernel、ticket、
                     budget、provenance、ContextRun、policy epoch
  supply/            源 → revision → fragment 的摄取契约
  learning/          评测、候选，以及唯一的发布提升权限
  control/           面向 operator 的访问控制与 file-import 权限
  persistence/       PostgreSQL 连接、租户上下文、RLS 边界
adapters/          一切与外部世界接触的部分
  http/              FastAPI ingress、认证、传输限制、路由
  parsers/           格式解析器（PDF / Markdown / Office）
applications/      极薄的进程入口与运维 CLI（只做组装）
  api.py             `context-engine-api`
  worker.py          `context-engine-worker`
bot_delivery/      M2 受信 Bot 进程（TypeScript），generated-SDK 调用方
action_plane/      prepare() → 一次性票据 → 精确外部效果
sdk/typescript/    由 OpenAPI 生成的 HTTP client
eval/              golden set、切片门禁、裁判、安全目录
migrations/        Alembic 迁移
tests/             unit / integration / catalog / process 套件
docs/              实现权威、编号 ADR、威胁模型、PRD、研究
CONTEXT.md         领域术语表（只有术语，不含实现）
PLAN.md            愿景、原则、路线图、Non-goals
```

有两个结构事实值得注意：

- **薄入口，厚内核。** `applications/` 里的每个模块都只做参数解析与组装，不含任何
  授权、检索或投递行为；这些行为全部在 `engine/` 里。这正是「生产 composition
  root 不能替换、跳过或装配 no-op `AuthorizationKernel`」能成为一条**可强制执行的
  性质**、而不只是一句口号的原因。
- **测试量约为实现量的 3 倍。** `tests/` 的体量远大于 `engine/`。
  对一个核心主张是安全不变量的项目来说，**可执行的证据本身就是产品**。

### 什么可插拔，什么不可

| 层 | 可插拔（seam） | 不可插拔（kernel） |
|---|---|---|
| 解析 | PDF / Markdown / Office parser | — |
| 表示 | embedding、reranker、LLM | — |
| 存储 | V1 固定 PostgreSQL FTS + pgvector；仅保留 Runtime 内候选注入的测试 seam | 授权真相库（PostgreSQL） |
| 接入 | connector、HTTP server ingress、ADR-0103 的本地 MCP translator；generated SDK 属于 client 产物 | 认证调用与 `TrustedDeliveryContext` 构造；所有 broader MCP carrier |
| 治理 | 评测裁判模型 | sealed `ContextRuntime` 编排、`AuthorizationKernel`、`DecisionAudit`、budget、provenance |

在第二个真实存储后端出现之前，**可移植性是被刻意不承诺的**。

### 受信交付

IM 交付由 `BotDelivery` 这个受信深模块完成。它从 M2 起作为独立进程部署，且只
通过 generated HTTP SDK 访问引擎。它**不在 wire body 里自报 audience**，而是在
认证 transport metadata 中传递一个 opaque `DeliveryEvidenceRef`，由 ingress
兑换为 `TrustedDeliveryContext` / `AudienceSnapshot`。群成员的权限交集由
`AuthorizationKernel` 计算，绝不由 BotDelivery 计算。

群公开回答与提问者私有回答是**两次独立的、audience-bound 的 resolve**，绝不是
把一个 Package 事后切分。所有外部副作用都经 `ActionPlane.prepare` 再
`ActionPlane.perform`，每个效果使用各自 org-scoped、audience/payload-bound 的
一次性 `ActionTicket`。

## 三条硬底线

这些是 **release veto，不是分数**：

- 无授权证据泄漏 = **0**
- 跨租户影响 = **0**
- 缺失租户上下文 = **一律 fail closed**

任何功能收益都不能抵消其中任何一条的失败。每次发布按版本化 catalog 报告
`PASS / FAIL / NOT_ACTIVE / NOT_APPLICABLE`，并单独列出 capability coverage，
因此未激活的能力永远不可能冒充为通过。

## 文档

| 文档 | 提供什么 |
|---|---|
| [CONTEXT.md](./CONTEXT.md) | 领域术语表——身份、安全、内容与生命周期术语的仓库权威 |
| [PLAN.md](./PLAN.md) | 愿景、不可谈判的设计原则、路线图、明确的 Non-goals |
| [STATUS.md](./STATUS.md) | 逐 Issue 的能力激活台账与证据边界 |
| [ADR 索引](./docs/decisions/README.md) | 编号决策记录：边界、依赖方向、禁止捷径、重访触发器 |
| [实现设计](./docs/design/2026-07-18-context-engine-implementation-design.md) | 集成后的实现权威与里程碑边界 |
| [威胁模型](./docs/security/context-engine-threat-model.md) | 资产、信任边界、威胁与 hard oracles |
| [Program PRD](./docs/agents/prd-contextengine-implementation.md) · [Epic Tech Spec](./docs/specs/2026-07-19-context-engine-implementation-epic.md) | 需求、100 条 user story、contract shape、work package |
| [Daily-driver 部署](./docs/operations/daily-driver-deployment.md) | Durable loopback 部署、服务生命周期、备份与恢复 runbook |
| [Evidence Console](./docs/decisions/0090-admit-a-co-resident-local-evidence-console.md) | 已接受的本地 authenticated UI 边界及其封闭 authority limits |
| [本地 MCP](./docs/decisions/0103-activate-one-local-mcp-acquire-translation.md) | Spawn-per-session stdio `Acquire` translator 与明确 non-goals |
| [端到端 walkthrough](./docs/design/2026-07-28-end2end-dogfood-walkthrough.md) | 实测 File-to-ContextPackage dogfood journey 与保留的 evidence boundaries |
| [公开参照证据基线](./docs/research/2026-08-02-five-public-repositories-evidence.md) | 五个固定公开仓库的优势、局限、clean-room 拆解与证据缺口 |
| [D0 Baseline Candidate](./DESIGN-BASELINE.md) | 当前候选状态与尚未关闭的 evidence gate |

## 参照与致谢

设计吸收了对四个已准入固定公开开源项目——**Dify**、**RAGFlow**、**MaxKB**、**Onyx**——的
架构研究，且严格限于可观察行为、interface 形状、测试 oracle 与产品工作流。
版本化基线另含 **OpenViking** 候选包；#205 保持 open 时，本 README 不把 OpenViking
引作 authority。**OpenViking 零代码复制；其他复用仍由 ADR-0074 按精确源码区域治理。** 固定版本与一手链接记录在
[证据基线](./docs/research/2026-08-02-five-public-repositories-evidence.md)。

ContextEngine 的安全与多租户协议依据自身 requirement 与威胁模型独立设计。
仓库外的研究可以启发推理，但绝不作为公开 provenance 被引用。

## 参与贡献

本项目的证据门槛异常严格——安全不变量是否决门，能力未经可执行证明不得激活。
提 PR 前请先阅读 [CONTRIBUTING.md](./CONTRIBUTING.md)，其中说明了验证契约、
ADR 流程，以及在这里「完成」意味着什么。

Issue 与 PRD 追踪于
[GitHub Issues](https://github.com/stone16/context-engine/issues)。

## 许可证

Copyright 2026 stone16。基于 [Apache License 2.0](./LICENSE) 授权——包含明确的
专利授权条款。归属声明见 [NOTICE](./NOTICE)。
