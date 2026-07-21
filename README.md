# ContextEngine

> A multi-tenant context delivery engine: connect your team's knowledge sources
> upstream, deliver **authorized, evidence-backed, budget-bounded**
> ContextPackages to agents and IM bots downstream.

多租户上下文交付引擎——上游连接团队知识源(飞书 / Slack / Google Docs /
企业微信),下游把「经过授权、带证据、有预算」的 ContextPackage 交付给 agent
应用与 IM bot(飞书群聊问答优先)。

**当前状态**:M0 工程骨架已启动。API 和独立 Supply worker 可运行，
[`compose.yaml`](./compose.yaml) 固定的真实 PostgreSQL + pgvector 测试底座可复现；
Organization 安全根、全局 User、Organization-scoped Membership 与一张代表性
tenant-owned 表的非 owner FORCE RLS 隔离已验证；HTTP 已能把确定性测试认证解析成
当前 Membership-backed `UserActor`，构造 nominal `AuthenticatedInvocation`，并用
closed body 与通用错误证明 caller 不能注入 trusted identity；该测试组合已通过唯一
`ContextRuntime.resolve` 返回 tenant-safe ContextPackage。默认应用仍拒绝全部
credential 并保持空包、零内容 I/O；显式 conformance 组合已证明 hostile
CandidateIndex 只能经同一 PostgreSQL 事务的 FORCE RLS、exact EffectiveScope 与 sealed
AuthorizationKernel 交付一个 synthetic exact-authorized Evidence/block。生产认证、durable
Principal/Agent grants、真实 Source ACL 与通用内容检索仍为 `NOT_ACTIVE`。Issue #17
已激活唯一的 persistent no-op WorkerLease 子载体：server-minted lease 精确绑定
Organization、job、registered ServicePrincipal binding、固定 workload/worker audience、
过期时间与 nonce，并在 non-owner FORCE RLS 下只允许一次原子完成；该 bounded binding
不是完整 canonical `ServiceActor`，真实 ingestion、outbox 与 publication job 仍为
`NOT_ACTIVE`。[Issue #18 的 ADR-0030](./docs/decisions/0030-bound-ticket-audiences.md)
也只激活 bounded signed-ticket separation proof：一个 synthetic
Provider read 与一个 synthetic channel no-op 共享 current `UserActor` identity chain 和
key configuration，但使用不同 nominal types、signed domains、fixed operations 和
provider/channel audiences。Agent/purpose 只从 matching
`AuthenticatedInvocation`/`TrustedDeliveryContext` 派生；各自的 type-aware deserializer
在创建 nominal ticket 前验证 signed namespace。两者均绑定 trusted
Organization/target、bounded expiry 与 V0 Policy Epoch；所有 mismatch 使用 generic
rejection 且 effect 为零。Production Provider、
Sender/IM 与完整 M2 ActionPlane 仍为 `NOT_ACTIVE`。整体计划见 [PLAN.md](./PLAN.md)。

## 开发命令

要求 Python 3.13 和 [uv](https://docs.astral.sh/uv/)。依赖版本由
`uv.lock` 固定，仓库命令统一由 `make` 暴露：

```bash
make install   # uv sync --frozen
make build     # 构建 wheel 和 sdist
make lint      # Ruff
make typecheck # strict mypy
make test      # 单元测试
make catalog   # 安全目录静态测试与校验
make smoke     # API / worker 进程 smoke
make db-up     # 启动 compose.yaml 固定的 PostgreSQL + pgvector 测试底座
make db-down   # 停止测试底座并保留 disposable data volume
make db-reset  # 删除并重建该测试底座的 disposable data volume
make integration # 真实 PostgreSQL integration/security harness
make check     # 全部门禁；要求先执行 make db-up
```

数据库底座首次启动时会在被 Git 忽略的
`.context-engine/database.env` 生成随机凭据并将文件权限设为 `0600`；该文件是
本地 migration、API Runtime、worker、security test 连接配置和该 checkout
独有 Compose project 身份的唯一实时来源，避免多个 worktree 或 checkout 共享
容器、网络与数据卷。
镜像及服务拓扑的版本真相位于 [`compose.yaml`](./compose.yaml)，PostgreSQL 只绑定
一个动态选择的 `127.0.0.1` host port。migration、runtime 与 worker 使用不同
角色；runtime/security test 不会回退到 migration 或 bootstrap 凭据。

从 clean checkout 运行与 CI 相同的数据库门禁：

```bash
make install
make db-up
make check
make db-down
```

`make db-reset` 只删除当前 checkout 的 generated Compose project 所属的
disposable PostgreSQL volume，然后从初始化脚本重建。它不会删除仓库内容，但会
清除该本地测试数据库中的全部数据。

本地启动 API：

```bash
uv run context-engine-api
```

监听地址和端口可通过 `context-engine-api --help` 中记录的参数覆盖；进程启动后
在所配置地址请求 `/health`。

确定性运行 worker 的 no-op 测试生命周期：

```bash
uv run context-engine-worker --test-mode
```

健康响应中的 `runtime_delivery: NOT_ACTIVE` 表示默认进程没有生产认证入口。worker
输出中的 `job_behavior: NOT_ACTIVE` 特指默认 CLI 尚未配置生产签名密钥来源、queue/job
loop 或真实 ingestion/publication handler；Issue #17 的 persistent no-op 应用 seam 与
PostgreSQL authority 已激活并由 integration suite 调用。当前数据库测试证明 `compose.yaml` 固定的
PostgreSQL/pgvector、
角色隔离、迁移、连接池清理，以及 Organization + current Membership-backed
`UserActor` + `organization_record` 的事务级租户上下文、复合所有权和 FORCE RLS。
它不声明 durable Principal/Agent grants、真实 ACL、生产级内容授权或生产
ContextPackage 交付已经实现；注入的 conformance 组合证明当前 Membership 门禁、
Issue #12 synthetic EffectiveScope 的 fail-closed 单调不扩张路径，以及 Issue #13
hostile CandidateIndex 的 synthetic exact-authorized Evidence 路径；Issue #14 的
paired Runtime/HTTP gate 进一步证明 cross-Organization、same-Organization denied
与 nonexistent Candidate 收敛为同一个 tenant-safe empty Package（不声明 timing
等价）；Issue #15 进一步激活 Organization-level V0 Policy Epoch：内部专用最小权限
non-owner Control 事务原子撤销 seeded access 并推进 epoch，sealed Acquire 在交付前复核当前
epoch，因此相同 query、CandidateRef 与持久 Fragment 在第一次 post-revoke 请求中返回
零 Evidence，且 Org B 不受影响。该测试能力不等于生产 grant/admin workflow。
Issue #16 已把公开 Runtime wire 固定为 closed `Acquire | Continue | OpenCitation`
union，并在 server-owned `RuntimeCapabilityGate` 激活 M0 拒绝路径：已知但尚无真实
carrier 的 Continue、OpenCitation、federated discovery 与 source-native authorization
在任何 Provider/index/source-content I/O 前分别返回通用 domain-level
`request_not_available` 或 `citation_not_available`；unknown variant 或 caller 自报
capability 仍为通用 422。该激活只证明 deterministic refusal，不表示 continuation、
citation、federated/source-native Provider 或 File publication 已实现。
Issue #17 进一步加入 Organization-owned `service_principal` 与 `worker_noop_job`，以及
显式 versioned keyring 的 canonical HMAC-SHA256 WorkerLease。Control issuer 使用数据库
事务时间和 server-owned bounded TTL 签出租约；若旧 lease 已按数据库时间过期，可用新
时间与 nonce 原子 takeover，恢复“事务已提交但 token 未交付”的 crash window，且旧 token
随后 effect 为零。worker 应用 seam 必须先以自身配置的 registered ServicePrincipal identity
与时钟验证签名、Organization、job 和时效，再打开数据库事务；durable receiver 固定为
`supply.noop` + `context-engine-worker` + `noop.complete`，不接受 worker call 覆盖。
worker 无直接两张 tenant table 的 `SELECT` 或 job `UPDATE` 权限；专用 non-login definer
function 是唯一 durable 读写边界，并在 FORCE RLS 下以数据库当前时间、key version、nonce
digest、issued-at/expiry 做一次条件更新。有效 lease 的 effect count
只能从 0 变为 1；wrong-org/job/audience、篡改、过期、禁用 ServicePrincipal、重放和
并发 loser 均保持零新增 effect。该 bounded proof 不包含 Source/Resource/Revision、
Policy Epoch、end-user delivery audience、idempotency/generation、outbox、File 或生产
worker loop，也不发布或声称完整 canonical `ServiceActor`（其 source/allowed-set/Policy
Epoch 尚不存在），并将完整 `ACCEPT-008` fixture 保持 `future/fail_closed`。

Issue #18 加入 canonical HMAC-SHA256 `ContextAccessTicket` 与 `ActionTicket`
protocols；两者使用同一 validated `AuthenticatedInvocation` /
`TrustedDeliveryContext` identity chain 和 explicit versioned key configuration。
Read protocol 固定
`context-engine.context-access-ticket` / `CE-ContextAccessTicket` /
`synthetic.provider.read` 并派生 `context-read:<provider>`；action protocol 固定
`context-engine.action-ticket` / `CE-ActionTicket` /
`synthetic.channel.noop` 并派生 `im-send:<channel>`。Issuer 与 handler 由 trusted
configuration 绑定一个 Organization/target；Agent/purpose 不接受裸字符串，token 也不
提供公开 value constructor。两个独立 deserializer 在构造 nominal type 前验证签名、
domain/type、fixed operation 与 schema；handler 再校验完整 identity、purpose、bounded
expiry、nonce 和 key version，并在两个独立 synthetic effect 前最后复核 Organization
V0 Policy Epoch。使用同一 key 的 cross-plane deserialize/pass、wrong
target/Organization、identity/audience mismatch、tamper、overlong/expired lifetime、
authority failure 和 committed epoch bump 均返回一个 non-enumerating unavailable 结果，
rejected effect 为零。该 bounded proof 不激活 production Provider
discovery/projection、source credential、Sender/IM、`ActionPlane.prepare`/`perform`、
payload/destination/approval/idempotency、DeliveryAttempt、durable one-shot/replay/
concurrency、stored receipt 或 reconciliation；完整 `ACCEPT-012` carrier 保持
`NOT_ACTIVE`。

### 当前 HTTP exact-authorized Evidence tracer

`POST /v1/context:resolve` 的 conformance 组合可注入一个把 opaque credential
映射为 verified transport facts 的 authenticator、一个为已登记 Organization
签发 request-bound nominal proof 的 trusted authority，以及一个在单次 PostgreSQL
事务内校验 current Membership 并签发 lifetime-bound `UserActor` proof 的 authority；
该事务保持到 sealed Runtime 与 ContextPackage 构造完成。默认组合的有效 Acquire 返回
`200 resolved` 与 evidence-free ContextPackage；显式 synthetic conformance 组合可在同一
事务中把 content-free CandidateRef 依次经过 RLS locator、exact EffectiveScope、body
projection 与 sealed AuthorizationKernel，返回唯一 exact-authorized Evidence/block。
无效 Membership 统一返回通用 401，
数据库 authority 不可用统一返回通用 503，且两者都不会调用内容系统。模块级默认应用的
认证、Organization 与 Membership 三条生产 authority 均 reject-all；scope authority
默认显式返回七个 missing trusted operands，因此不会接受任何生产 credential，也不会
产生可交付 scope。

请求体是 closed `kind` union：Acquire 允许 `need.query`、可选的有限
`packageBudget` 和可选 `requestNarrowing`；Continue 允许 opaque
`continuationToken` 与可选更小的 `packageBudget`；OpenCitation 只允许 opaque
`citationOpenRef`。所有 ref/token 长度与集合数量均受 active profile 限制；每层 unknown field、重复 JSON key
以及重复 singleton security/transport header 都 fail closed；pre-auth body bytes 和
JSON nesting 由 `adapters/http/transport.py` 的 versioned profile 限制。非法
JSON/media type、
认证失败和 closed-schema 失败分别使用 OpenAPI 记录的通用 400、401 和 422 响应，
不会回显 tenant、Principal、Membership 或注入字段。purpose 只来自服务端 route
policy；返回的 `organizationRef` 是新生成的 package-scoped opaque reference，不能作为
后续请求的 trusted tenant input。空包的 blocks/evidence/gaps 均为空，coverage 为
`no_authorized_evidence`；默认无候选路径的 Provider/index/source-content 调用均为零。内容 tracer 对 denied
same-Organization 与 cross-Organization 候选保持零 body bytes、零 Evidence refs 和零外部
effect，并为 authorized block 保持一对一 Evidence 引用闭包与完整 lineage。确定性
denied、cross-Organization 与 nonexistent probes 的 HTTP status、closed product
headers、Package body 与 Runtime domain outcome 在仅归一化 server-authored per-resolve
refs/timestamps 后完全相同；响应不含 Resource 标识、名称、Candidate/denied 数量或拒绝
原因。此门禁不测量或声明 timing equality。

确定性 authorities 与 real-PostgreSQL seeded composition 只属于测试组合。生产 OAuth/JWT、durable
Principal/Agent grant authority、真实 Source/Resource ACL、通用检索与 continuation
不属于这个已激活 tracer。Policy Epoch V0 本身也不激活 UI/外部 admin、DecisionAudit、
outbox、cleanup、真实 Continue/OpenCitation 或完整 production WorkerLease/ticket carrier；
Issue #17 与 Issue #18 仅通过各自 ADR 单独激活前述 bounded proof。
其中 Continue/OpenCitation 的 M0 通用拒绝已经激活，但真实 issuance/redemption carrier
仍保持 future；restricted in-process audit 只保留 `UNSUPPORTED_CAPABILITY` 类别，
durable DecisionAudit 仍为 `NOT_ACTIVE`。

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

当前除固定 commit 的四仓静态证据与仓库内设计拆解外，已有
[`compose.yaml`](./compose.yaml) 固定的真实 PostgreSQL + pgvector 基础 harness，
以及首个 Organization-owned 代表表的 RLS 动态证据。
完整 domain schema、ActorContext、filtered ANN 和飞书 capability 的动态证据仍未
完成，因此不把这个证据切片扩称为完整产品授权能力。

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
