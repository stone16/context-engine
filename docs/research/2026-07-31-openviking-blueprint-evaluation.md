> **Room-A 研究产物 — 维护者本地研究，非公开 provenance；第五仓准入已由维护者于 2026-07-31 有条件批准（D9）。66/66 上游 evidence links 已固定到 `49b1820…`，版本化候选五仓基线与 legal dossier 已于 2026-08-02 准备；maintainer/legal sign-off 仍由 #205 关闭。其余开放问题决定（D8/D10）见 [`five-repository-implementation-blueprint.md`](./2026-07-31-five-repository-implementation-blueprint.md) §5；正文推荐项保留评估时刻的状态。**

# 1. 固定 commit、许可证核验与证据基线状态

## 研究固定点

- 上游仓库：`volcengine/OpenViking`。
- 固定 commit：[`49b182045b42d34ad530948ad77d9d0226897da8`](https://github.com/volcengine/OpenViking/commit/49b182045b42d34ad530948ad77d9d0226897da8)。本地以 `git show -s --format='%H%n%cI%n%s'` 核对的提交时间为 **2026-07-31T11:38:57+08:00**，提交标题为 `refactor(parser): Refactor code summaries to fixed skeleton-first routing (#3568)`；研究日期为 **2026-07-31**。
- 本文所有 OpenViking 结构性判断只以该 commit 的固定链接为证据。后续上游行为、文档或许可证变化不自动更新本文结论；`main` 链接不构成本文证据。
- 研究方法：Room A 只观察文档、接口形状、行为与测试 oracle，不复制 AGPL 实现；没有运行 OpenViking 的联网服务，也没有把上游依赖或源码带入 ContextEngine。

## 路径级许可证核验

| 上游区域 | 固定证据 | 2026-07-31 核验结论 | ContextEngine 处置 |
|---|---|---|---|
| 仓库根与 Python 主项目 | 根 [`LICENSE`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/LICENSE) 是 GNU AGPL v3；[`pyproject.toml`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/pyproject.toml) 自报 `AGPL-3.0`；[`README.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/README.md) 也称 Main Project 为 AGPLv3 | **AGPLv3 区域**。除经单独许可证覆盖的路径外，`openviking/`、`openviking_cli/`、`web-studio/`、`bot/`、主项目测试与文档所描述的实现均按 AGPL 边界处理 | 严格 clean-room：仅行为规格、接口形状与测试 oracle；零源码复制、零派生实现、零主项目运行时依赖 |
| `crates/` 父目录 | [`crates/LICENSE`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/crates/LICENSE) 是 Apache License 2.0；README 称 `crates/ov_cli` 为 Apache-2.0 | 父目录给出 Apache-2.0 证据，但不能覆盖子项目 manifest 的相反声明 | 逐文件、逐 manifest 核验后才可能 copy+patch；不能把整个 `crates/` 当作已清洁素材池 |
| `crates/ov_cli` | [`crates/ov_cli/Cargo.toml`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/crates/ov_cli/Cargo.toml) 自报 `MIT`，与 [`crates/LICENSE`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/crates/LICENSE) 和 [`README.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/README.md) 的 Apache-2.0 声明冲突 | **许可证边界未消歧，当前不可复制**。不能任选对项目更方便的一种许可证解释 | 需要上游/maintainer/legal 明确适用许可证、精确 source path 和所需 notice；在此之前只作 strategic-reference-only |
| `examples/` 父目录 | [`examples/LICENSE`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/examples/LICENSE) 是 Apache License 2.0；README 称 examples 为 Apache-2.0 | 没有下层相反声明的具体文件，才有 Apache-2.0 初步依据；这不是对子树所有文件的无条件覆盖 | 每个候选需核对 manifest、SPDX、嵌套 LICENSE、依赖锁和实际 source path 后再登记 |
| `examples/openwebui-plugin` | [`pyproject.toml`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/examples/openwebui-plugin/pyproject.toml) 自报 AGPL-3.0；[`tools.py`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/examples/openwebui-plugin/openviking_openwebui/tools.py) 带 AGPL-3.0 SPDX | 子项目明确为 **AGPL-3.0**，不得因父目录 `examples/LICENSE` 而误判为 Apache | clean-room only，禁止 copy+patch |
| 其他 examples | 例如 [`opencode-plugin/package.json`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/examples/opencode-plugin/package.json) 自报 Apache-2.0，而 [`openclaw-plugin/package.json`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/examples/openclaw-plugin/package.json) 自报 MIT；[`claude-code-memory-plugin/package.json`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/examples/claude-code-memory-plugin/package.json) 没有项目 license 字段 | examples 内存在多种或缺省声明，必须缩小到确切文件集合。依赖的 license 不是项目源码的 license | 当前无 copy+patch 提案；需要复用时另开 path-level 审批，不从本报告推定许可 |

AGPL 网络服务影响必须按工程红线理解：AGPLv3 第 13 节要求，若修改后的 AGPL 程序通过网络与用户交互，运营者须向这些用户提供获得该版本 Corresponding Source 的明确机会。ContextEngine 的 API/worker 是网络服务；复制、修改或依赖 OpenViking 主项目服务代码会把源码提供义务带到我们的网络交付面，并产生与 ADR-0074 受控复用政策不相容的风险。因此所有 AGPL 区域一律 clean-room，不能以“没有分发二进制”为由放宽。此结论是仓库工程治理边界，不代替法律意见。

## 证据基线状态与准入条件

OpenViking 的条件准入公开证据只存在于候选的 [`2026-08-02-five-public-repositories-evidence.md`](./2026-08-02-five-public-repositories-evidence.md)。本文仍只能影响 maintainer-local 推理，不能作为 provenance；maintainer/legal sign-off 未记录前，候选基线不得合并。

若要成为第五仓，至少需要一次显式的 maintainer admission，且在同一变更中完成：

1. 固定仓库、完整 SHA、研究日期和路径级许可证矩阵，并由 maintainer/legal 处理 `ov_cli` 与 examples 的冲突或排除这些路径；
2. 将每一项拟公开 claim 改写为可核验、有限的结构性陈述，并逐项链接到该 SHA 的文件/测试 permalink；无法固定取证的内容保留 **[未取证]**，不得以推断补齐；
3. 修订四仓证据报告为明确版本化的五仓基线，更新设计 authority、PLAN/STATUS 中的公开来源范围和 attribution；说明 OpenViking 只证明 context filesystem、memory workflow 与 exposure UX，不证明 ContextEngine 的多租户授权、安全或合规结论；
4. 登记 clean-room 边界与两室流程；如有任何 permissive copy+patch，再独立提交 `third_party/<upstream>/UPSTREAM.toml`、上游许可证/NOTICE、修改日志、逐文件哈希和 SBOM 覆盖；
5. 维护者复核所有公开文本不再引用浮动分支、博客截图或未固定 benchmark，并明确未来上游更新不自动继承准入。

# 2. 能力盘点 → ContextEngine 区域映射表

| OpenViking 能力 | 上游固定证据 | ContextEngine 映射区域 | 复刻分类 | 结论 |
|---|---|---|---|---|
| L0 abstract / L1 overview / L2 detail | [`context-layers.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/en/concepts/03-context-layers.md)、[`hierarchical_retriever.py`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/openviking/retrieve/hierarchical_retriever.py) | Supply 编译的 Fragment/heading ancestry；Runtime AssemblyProfile、PackageBudget、Assembler | **clean-room Room-A spec** | 采用“信息密度逐级揭示”和逐 hop 预算；删除“层级/深度产生权限”的暗示 |
| `viking://` 虚拟文件系统、`ls/tree/glob/read` | [`viking-uri.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/en/concepts/04-viking-uri.md)、[`filesystem.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/en/api/03-filesystem.md) | EffectiveScope、AuthorizationKernel、AuthorizedProjection、OpenCitation、Evidence Console | **clean-room Room-A spec** | 仅可做 post-authorization 的 browse UX 投影；不能暴露源 URI、目录存在性或 prefix 递归权限 |
| Session commit 后异步提取 memory | [`session.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/en/concepts/08-session.md)、[`session-memory-extraction-flow.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/design/session-memory-extraction-flow.md) | authorized-only Learning input、CurationCandidate/Annotation、CurationSnapshot、ReleaseCandidate/evaluate/promote | **clean-room Room-A spec** | 采用归档 intent、幂等队列、候选 diff；禁止模型直接 create/merge/delete active memory |
| 检索 trajectory / provenance | [`retrieval.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/en/concepts/07-retrieval.md)、[`test_provenance.py`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/tests/retrieve/test_provenance.py) | ContextRun、DecisionAudit、Package digest、Evidence Console | **clean-room Room-A spec** | 只保留 authorized-only lineage、预算与版本化 digest；拒绝 pre-auth path/query/score/count/thinking trace |
| Studio playground / Helper | [`observability.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/en/guides/05-observability.md)、[`openviking-helper.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/en/agent-integrations/14-openviking-helper.md) | ADR-0090 co-resident local Evidence Console | **clean-room Room-A spec** | 采用同进程、timeline、health、preview/confirm UX；必须经 HTTP seam、显式 browser auth 和 one-Control-operation-per-call |
| VikingBot 的 context/model/tools/channel 一体化 | [`vikingbot.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/en/concepts/15-vikingbot.md) | 外部 BotDelivery、ActionPlane；Engine Runtime | **do-not-take** | 与 ADR-0006 冲突；Engine 不生成答案、不拥有渠道或工具执行，只交付 ContextPackage |
| 同进程 `/mcp`、13 tools、coding-agent hooks | [`mcp_endpoint.py`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/openviking/server/mcp_endpoint.py)、[`mcp-integration.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/en/guides/06-mcp-integration.md) | ADR-0017 sealed Runtime、OpenAPI/generated SDK、可选 thin MCP adapter | **clean-room Room-A spec** | 采用同认证、薄协议、hook 生命周期；不采用宽写面、目录直读、remember/forget 或旁路授权 |
| `crates/ov_cli` 和 examples 的客户端实现 | [`crates/ov_cli`](https://github.com/volcengine/OpenViking/tree/49b182045b42d34ad530948ad77d9d0226897da8/crates/ov_cli)、[`examples`](https://github.com/volcengine/OpenViking/tree/49b182045b42d34ad530948ad77d9d0226897da8/examples) | TS generated SDK、MCP/API exposure spikes、`third_party/` registry | **strategic-reference-only**（当前） | 许可证冲突且本仓已有 generated SDK authority；当前结论为 none，不提出 copy+patch |
| 完整 context layer / agent memory filesystem thesis | [`README.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/README.md) | ADR-0006、ADR-0061、Supply/Runtime/Learning 三环 | **strategic-reference-only** | 产品叙事相邻；我们以多租户授权真相和 audience-bound ContextPackage 为差异化核心 |

# 3. 逐能力蓝图

## 3.1 Tiered context loading → 预算约束的 progressive disclosure

### 上游路径与可观察行为

OpenViking 把内容写成 L0 摘要、L1 overview、L2 完整详情，并建议先读低成本层、按需深入；其固定描述与 token 量级见 [`docs/en/concepts/03-context-layers.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/en/concepts/03-context-layers.md)。层次检索先做全局 L0/L1 搜索，再以 priority queue 递归子目录；实现中有收敛轮次与并发边界，见 [`openviking/retrieve/hierarchical_retriever.py`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/openviking/retrieve/hierarchical_retriever.py)，rerank/回退行为由 [`tests/retrieve/test_hierarchical_retriever_rerank.py`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/tests/retrieve/test_hierarchical_retriever_rerank.py) 固定。上游的目录与层级只是检索结构，不提供可移植的多租户授权证明。

### 本仓 seam / authority

- [ADR-0038](../decisions/0038-compile-and-publish-structural-markdown.md)：heading ancestry 在编译时进入同一 Fragment；Runtime 不得为了“补父标题”读取另一个未授权 Fragment。
- [ADR-0012](../decisions/0012-sealed-authorization-projection-pipeline.md) 与 [ADR-0077](../decisions/0077-fix-the-article-as-the-content-authorization-atom.md)：`CandidateRef → AuthorizationKernel → AuthorizedProjection`；Article 是内容授权原子。同 Article + 同 current Revision 的扩展在 lineage 校验后继承该 Article 决定，跨 Article 扩展必须重新产生 CandidateRef 并授权。
- [ADR-0006](../decisions/0006-engine-delivers-context-not-answers.md)：最终仍只形成 ContextPackage，不形成答案。
- 实现 seam：`engine/runtime/budget.py::PackageBudgetMeter`、`engine/runtime/evidence.py::{CandidateRef, AuthorizedProjection}`、Runtime Assembler/PackageBudget gate。

### Room-A 行为规格

1. 在 `RuntimeProfile` 引用的不可变 `AssemblyProfile` 中定义三种**信息密度目标**，不把它们命名为存储层或 ACL：`abstract`（可判相关的最短投影）、`overview`（含结构/heading ancestry 的任务导航投影）、`detail`（获授权 Fragment 的原文投影）。默认从 `abstract` 开始；profile 可以限定 `max_density`、每 Article 的最多 expansion hops、每 hop token 上限、总 expansion 数和停止阈值。
2. 每个初始候选先经 Kernel 形成 `AuthorizedProjection`。Assembler 的任何 content-bearing relevance、rerank、hydration 与 expansion 只接受该类型；不能接受 URI、Fragment id 或 `CandidateRef` 原始内容。
3. 同 Article 扩展的输入必须携带 `article_ref + revision_ref + authorization_decision_ref + lineage_digest`。扩展前验证 current Revision 与 lineage；验证失败视作没有可扩展内容，不回退旧决定。跨 Article 链接转换为新的无内容 `CandidateRef`，从 Kernel 重走授权。
4. 每个 hop 在读取正文前向同一 `PackageBudgetMeter` 预留候选数、token、block、evidence 和 latency 预算，完成后以实际值结算；超额则确定性停止并输出已形成的合法 Package，不使用未计量回退。heading ancestry 已在当前 Fragment 内，按同一 block token 计量。
5. 停止条件只来自 profile + budget + 已授权相关性：达到 `max_density`、每 Article hop 数、总预算、无授权 child 或收益低于阈值。上游的 `MAX_CONVERGENCE_ROUNDS` 可作为性能 oracle，但不能成为授权或“目录已搜尽”的声明。
6. 上游 rerank 失败回退 vector score 的可用性行为不得照搬到任何会改变授权/投影的阶段。授权后 ranker 不可用时，只能走 profile 中显式、经过评估且不接触 denied 数据的 deterministic policy；否则 fail closed/unavailable。

### 我方接口形状草图

```python
class DisclosureDensity(StrEnum):
    ABSTRACT = "abstract"
    OVERVIEW = "overview"
    DETAIL = "detail"

@dataclass(frozen=True)
class AssemblyProfile:
    profile_ref: str
    start_density: DisclosureDensity
    max_density: DisclosureDensity
    max_expansion_hops_per_article: int
    max_total_expansions: int
    max_tokens_per_hop: int
    min_authorized_gain_micros: int

@dataclass(frozen=True)
class AuthorizedExpansionRequest:
    parent: AuthorizedProjection
    requested_density: DisclosureDensity
    article_ref: str
    revision_ref: str
    lineage_digest: str

class ProgressiveAssembler(Protocol):
    def assemble(
        self,
        projections: tuple[AuthorizedProjection, ...],
        profile: AssemblyProfile,
        budget: PackageBudgetMeter,
    ) -> ContextPackage: ...
```

该接口不暴露 `depth_is_authorized`、raw URI 或 directory prefix；`DisclosureDensity` 只决定同一已授权内容的表达密度。

### 测试 oracle

- 同一 query/profile/budget/Revision 得到确定的 density/hop 顺序和 Package digest；预算降一单位时在同一边界停止。
- 任一 content consumer 传入 `CandidateRef`、Fragment record 或 string body 而非 `AuthorizedProjection` 都在类型/运行时双重拒绝。
- 同 Article + 同 Revision 扩展成功；Revision 变化、lineage digest 错误、跨 Article 未重授权都返回不含内容的安全结果。
- denied child 与不存在 child 形成同一公开 Package/错误形状，且没有 child count、path 或 score 差异。
- heading ancestry 从当前 Fragment block 取得；测试 DB 中放置不可访问的“父 Fragment”也不得读取。
- PackageBudget 在 abstract→overview、overview→detail 的每 hop 预留/结算；并发扩展不能超卖预算。
- ranker 故障不得将 pre-auth vector score 或 candidate 内容送入 assembler；安全 veto 不能降级。

### 验证命令、工作量、依赖

- 命令：`make lint`、`make typecheck`、`make test`、`make catalog`；涉及真实 RLS/跨 Article fixture 时 `make db-up && make integration && make security-gate`；最终 `make check`。
- 工作量：**6–9 engineer-days**。
- 依赖：AssemblyProfile/RuntimeProfile contract 版本决策；ADR-0038 Fragment lineage；ADR-0077 Article atom；PackageBudget 并发预留语义；授权后 ranker 的已接受激活边界。

## 3.2 Virtual context filesystem → AuthorizedProjection 之上的安全 browse UX

### 上游路径与可观察行为

OpenViking 使用 `viking://<scope>/<path>`，区分 resources/user/agent 等 namespace，见 [`docs/en/concepts/04-viking-uri.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/en/concepts/04-viking-uri.md) 与 [`openviking/core/namespace.py`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/openviking/core/namespace.py)。文件系统 API 暴露 `ls`、recursive listing、tree、glob、read 等行为，见 [`docs/en/api/03-filesystem.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/en/api/03-filesystem.md)、[`openviking/storage/viking_fs.py`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/openviking/storage/viking_fs.py) 和 [`openviking/service/fs_service.py`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/openviking/service/fs_service.py)。`node_limit`/`level_limit` 约束资源使用，但不等价于 ContextEngine 的 finite-target authorization。

### 本仓 seam / authority

- [ADR-0024](../decisions/0024-model-effective-scope-as-finite-target-intersection.md)：EffectiveScope 是来源、租户、purpose 与请求目标的精确有限交集，不接受 caller prefix 代表无限后代。
- [ADR-0051](../decisions/0051-reauthorize-opaque-citation-opens.md)：OpenCitation 只接受 opaque `CitationOpenRef`；每次以新 current UserActor、DeliveryEvidenceRef 和 policy epoch 重授权，不能把旧决定或 source URI 当权限。
- [ADR-0077](../decisions/0077-fix-the-article-as-the-content-authorization-atom.md)：同 Article/current Revision 的 Fragment 展开需 lineage 校验，跨 Article 重新授权。
- [ADR-0090](../decisions/0090-admit-a-co-resident-local-evidence-console.md)：browse 只能通过 local Evidence Console 的 authenticated Runtime/Control HTTP seam 组合。

### Room-A 行为规格

结论：**browse-like-files 可以干净地作为 AuthorizedProjection 之后的 UX，但不能成为检索或授权抽象。** 实现规则如下：

1. UI 的“文件夹”是一个 Package 内已授权 Evidence/Block 的临时分组，不是可遍历的源 namespace。可显示的 label、heading 与 child 只来自当前 ContextPackage 或一次新的 resolve；服务绝不回答“这个目录真实存在吗”。
2. 初始 browse 请求仍是 `Acquire`，target 必须在 server 解析为 ADR-0024 的 exact finite set；不得传 `viking://org/**`、path prefix、glob 或 recursive flag 作为授权目标。
3. 可展开节点只携带 opaque `citationOpenRef` 或 server-minted `BrowseContinuationRef`。后者绑定 Organization、audience、purpose、current Revision/Policy Epoch、父 Package digest、exact authorized Article set、下一 hop 上限和短 TTL；它不是 bearer ACL，redeem 后仍重建 trusted context 并过 Kernel。
4. OpenCitation 返回新的 ContextPackage。UI 用新 Package 替换/追加视图，但不能在 wire 上得到 source URI、database id、未授权 sibling 数量、总 child 数、被裁剪数量、pre-auth score 或“有更多但无权限”的标志。
5. 不存在、跨 Organization、same-Organization denied、过期 ref 和 policy epoch 变化必须收敛为同一 generic not-available 结果。空目录与全 denied 目录不得通过 timing、分页总数或占位符区分；如需 pagination，只返回 opaque next ref，不能返回 total。
6. PackageBudget 对每一次 browse hop 生效；`level_limit`/`node_limit` 只能成为 meter 的 caller-independent server cap，不能增加 scope。跨 Article 的每个 child 都重新授权；同 Article expansion 也必须验证 current Revision lineage。

### 我方接口形状草图

```python
@dataclass(frozen=True)
class AuthorizedBrowseNode:
    label: str                    # only from AuthorizedProjection
    density: DisclosureDensity
    citation_open_ref: str | None # opaque, short-lived
    children: tuple["AuthorizedBrowseNode", ...]

@dataclass(frozen=True)
class BrowseProjection:
    package_digest: str
    nodes: tuple[AuthorizedBrowseNode, ...]
    next_ref: str | None          # no total/count-of-hidden

def project_browse(package: ContextPackage) -> BrowseProjection: ...

ContextRuntime.resolve(invocation, trusted_delivery, Acquire | OpenCitation)
    -> ResolutionOutcome
```

`project_browse` 是 presentation-only 纯函数，不能访问 database/index。若产品需要新的 `BrowseContinuationRef`，必须先用 ADR 激活相应 carrier；在此之前只使用已声明的 Acquire/OpenCitation，并诚实显示 unavailable。

### 测试 oracle

- 构造同名的 nonexistent、cross-org、denied 和 expired-ref case，断言 HTTP status/domain code、body shape、是否有 next ref 均一致；对 timing 做宽容的侧信道上界测试。
- RLS fixture 中只授权目录的一个 Article；Package/HTML 仅出现该 Article 衍生 label，不出现 sibling path、count、gap、score 或 source URI。
- caller 提交 prefix/glob/recursive target 时在 content I/O 前拒绝；server 只能使用 exact finite targets。
- current Membership、audience、purpose、Revision 或 Policy Epoch 改变后，旧 OpenCitation/Browse ref 不能打开内容。
- 同 Article expansion 验证 lineage；跨 Article 一定产生新的 Kernel decision；直接 `project_browse(CandidateRef)` 类型拒绝。
- 每 hop 预算可复算，分页 ref 绑定父 Package digest 且一次篡改/跨 org 重放失败。

### 验证命令、工作量、依赖

- 命令：`make lint`、`make typecheck`、`make test`、`make catalog`、`make smoke`；真实隔离用 `make db-up && make integration && make security-gate`；最终 `make check`。
- 工作量：**8–12 engineer-days**。
- 依赖：OpenCitation 的持久化/激活 issue（当前不可把未来 carrier 说成 active）；EffectiveScope target contract；Evidence Console browse job；opaque ref issuer/redeemer；PackageBudget。

## 3.3 Session → persistent memory extraction → 只产生 Learning 候选

### 上游路径与可观察行为

OpenViking 的 `session.commit()` 同步归档消息，再异步生成摘要并提取 memory，最终写 `memory_diff.json`；阶段与输出见 [`docs/en/concepts/08-session.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/en/concepts/08-session.md) 和 [`docs/design/session-memory-extraction-flow.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/design/session-memory-extraction-flow.md)。实现的 session/archive/queue 路径见 [`openviking/session/session.py`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/openviking/session/session.py) 与 [`openviking/session/compressor_v2.py`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/openviking/session/compressor_v2.py)，`memory_diff` 的 add/update/delete oracle 见 [`tests/session/memory/test_memory_diff.py`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/tests/session/memory/test_memory_diff.py)。上游会把抽取结果 create/merge/delete 到用户 memory；这个激活路径不能移植。

### 本仓 seam / authority

- [ADR-0031](../decisions/0031-persist-authorized-context-run-lineage.md)：ContextRun 只持久化 authorized Evidence refs、版本化 query HMAC、Package digest 与预算等 digest-only lineage；不保存原始 query、Package body 或 denied trace。
- [ADR-0014](../decisions/0014-curation-snapshot-and-release-ownership.md)：curation 输出不可变 CurationSnapshot，pipeline 无直接 activation operation。
- [ADR-0033](../decisions/0033-promote-organization-releases-through-one-learning-owner.md)：`ContextLearning.evaluate(ReleaseCandidateRef)` 产生 evaluation；只有 release-operator-authorized promote 可激活/回滚 ReleaseManifest。
- [ADR-0073](../decisions/0073-compose-explicit-release-candidates-from-current-corpus.md)：候选从 current corpus/profile/snapshot 显式组合，不能由模型输出跳到 active pointer。

### Room-A 行为规格

1. **先解决输入 authority，不从 ContextRun 反推正文。** ContextRun 的 digest/evidence refs 只能证明 lineage，不能重建 session。若要使用对话正文，先接受单独 ADR：定义用户/Organization consent、最小字段、加密/retention、删除/export、purpose、Learning 读取角色和 RLS。没有该 carrier 时，候选生成只能消费现有 authorized Package evidence 与显式 feedback，原始 session extraction 保持 `NOT_ACTIVE`。
2. 对获准的 session material 创建 immutable `LearningInputRef`：绑定 Organization、actor/audience、source ContextRun refs、exact authorized Evidence refs、Package digest、consent/retention profile、as-of 和 input digest。它不能引用 denied candidate，也不授予对原始 Source 的新读权。
3. “commit”分两相：事务内只写 recoverable candidate-generation intent/outbox；worker 用 exact durable-job-bound WorkerLease 消费。重试以 `(organization_id, input_digest, generator_profile_ref)` 幂等，`.failed` 等价物只能是受限状态，不泄露正文。
4. 生成器输出 `CurationCandidate`，而不是 memory record：操作为 `propose_add | propose_update | propose_suppress`，携带 target Revision compatibility、candidate body/digest、来源 Evidence refs、模型/规则 profile、confidence/evidence category。`memory_diff` 只作为 operator-friendly candidate diff 形状，绝不执行 create/merge/delete。
5. Curation 审核将 accepted candidate 变为 audited `CurationAnnotation`；一批兼容 annotations 固化为 immutable `CurationSnapshot`。失败或缺失 curation 的 Runtime 行为是 curation-off，不允许半完成 candidate 生效。
6. release composer 以 current Content/Index/Runtime/Curation profiles 和 compatible Revision set 形成 `ReleaseCandidateRef`；`ContextLearning.evaluate` 运行已登记 slices、安全 veto、sample threshold 与 uncertainty。只有携带 release-operator authority 的 `promote` 可原子切换 ReleaseManifest；Control、candidate worker、model 和 Evidence Console 均没有这个 capability。
7. Candidate body 不是 ContextRun，也不是 tenant-visible delivery；进入后续训练/eval 前再次保证 authorized-only，并继承明确 retention。撤销 Membership/ACL 后必须有 tombstone/revalidation policy；旧 Evidence refs 不能隐式续权。

### 我方接口形状草图

```python
@dataclass(frozen=True)
class LearningInputRef:
    organization_id: UUID
    source_run_refs: tuple[str, ...]
    authorized_evidence_refs: tuple[str, ...]
    package_digests: tuple[str, ...]
    consent_profile_ref: str
    input_digest: str
    as_of: datetime

@dataclass(frozen=True)
class CurationCandidate:
    candidate_ref: str
    operation: Literal["propose_add", "propose_update", "propose_suppress"]
    compatible_revision_refs: tuple[str, ...]
    proposed_annotation: bytes
    provenance_digest: str
    generator_profile_ref: str

class SessionCandidateGenerator(Protocol):
    def propose(self, input_ref: LearningInputRef, lease: WorkerLease) \
        -> tuple[CurationCandidate, ...]: ...

CurationSnapshotBuilder.accept(audited_annotations) -> CurationSnapshot
ContextLearning.evaluate(release_candidate_ref) -> ReleaseEvaluation
ContextLearning.promote(evaluation_ref, release_operator_authority) -> ReleaseManifest
```

### 测试 oracle

- 未接受 session-retention contract、缺 consent、缺 tenant/current actor、Evidence 非 authorized 或 WorkerLease job 不匹配时，在读取正文前 fail closed。
- 相同 input/profile 重投不产生重复候选；archive 成功后 worker crash 可恢复；失败不会修改 active ReleaseManifest。
- 模型输出 add/update/delete 只能形成 diff；直接调用 corpus/index/profile activation 的能力不存在。
- candidate 的 Organization、Revision compatibility、Evidence refs 全做同 org 强约束；Org A candidate 不能进入 Org B snapshot/evaluation。
- CurationSnapshot 不可变，且 Runtime 只从 active ReleaseManifest 读取 compatible snapshot；missing/failed snapshot 正常 curation-off。
- evaluate 未通过 security veto、sample threshold 或 compatibility 时 promote 拒绝；Control credential、worker lease、candidate ref 均不能 promote。
- ACL/Membership 撤销 fixture 验证旧 candidate 不因历史 delivery 自动获得当前授权；删除/export/retention fixture 与新 ADR 一起激活。

### 验证命令、工作量、依赖

- 命令：`make lint`、`make typecheck`、`make test`、`make catalog`；outbox/RLS/release 原子性用 `make db-up && make integration && make security-gate`；进程恢复用 `make smoke`；最终 `make check`。
- 工作量：**10–15 engineer-days**，不含新的 session-body 加密/retention 系统；后者需另估。
- 依赖：session/Learning input retention ADR；CurationCandidate 与 annotation schema；WorkerLease；CurationSnapshot builder；ReleaseCandidate composer；evaluation executor 与 release-operator auth。

## 3.4 Observable retrieval trajectories → authorized-only、digest-only 轨迹

### 上游路径与可观察行为

OpenViking 的 retrieval result/provenance 可带 query、匹配 URI、level、score、searched directories、match reason 等，结构见 [`openviking_cli/retrieve/types.py`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/openviking_cli/retrieve/types.py) 与 [`tests/retrieve/test_provenance.py`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/tests/retrieve/test_provenance.py)；递归轨迹由 [`openviking/retrieve/hierarchical_retriever.py`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/openviking/retrieve/hierarchical_retriever.py) 形成，汇总指标见 [`openviking/retrieve/retrieval_stats.py`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/openviking/retrieve/retrieval_stats.py)。这些字段适合单机调试，但在多租户系统会泄露被拒绝资源的存在与相似度。

### 本仓 seam / authority

- [ADR-0031](../decisions/0031-persist-authorized-context-run-lineage.md)：成功 Acquire 在 commit-before-response 的 ContextRun 保存 authorized-only Evidence refs、effective/used budget、Policy/epoch、版本化 query HMAC 与 Package digest；完整 Package/body 不持久化。delivered-empty 的 restricted DecisionAudit 只有闭合类别 `no_authorized_evidence`，没有 query/candidate/id/name/score/count。
- [ADR-0076](../decisions/0076-rejoin-rank-evidence-after-authorization.md)：rank/rejoin 发生在授权后，但 public ContextPackage 不因此成为 score debug contract。
- [ADR-0090](../decisions/0090-admit-a-co-resident-local-evidence-console.md)：Hit Test 只显示 Package Blocks/Evidence，明确不显示 candidate rank/score。

### Room-A 行为规格

轨迹分三层，禁止用一个“debug trace”对象跨层复用：

| 接收者 | 允许信号 | 禁止信号 |
|---|---|---|
| Runtime consumer / tenant Evidence Console | `runRef`、`decisionRef`、Package digest/profile、release/policy epoch refs、effective/used PackageBudget、terminal `delivered_authorized|delivered_empty`、Package 已携带的 authorized Evidence refs/Blocks；可显示 Package 顺序但无 numeric rank | 原始 query、source URI、Candidate/denied ids、pre-auth path、score、rank gap、hidden/denied count、thinking trace |
| 独立授权的同 Organization operator read | 上述 digest-only ContextRun projection；可见 Organization-bound versioned query HMAC 用于有限相关性比较；只允许 Kernel **之后**且经独立 ADR 审查的粗粒度阶段耗时 | Package body副本、可逆 query、任何 denied trace；当前 ADR 未激活逐候选 post-auth score/rank 持久化，因此默认也不可见 |
| restricted security DecisionAudit | exact org/run/decision/policy/epoch、闭合 category `no_authorized_evidence`、recorded time | 除上述七字段外全部内容，尤其 query digest、candidate/ref/count/reason/score |

实现上只从最终 `ContextPackage` 与 Kernel decision receipt 投影 ContextRun。pre-auth retriever 可以在请求内用 transient counters 做预算，但不能把 URI/score/路径送入 logs/metrics/debug/Learning；若要持久化阶段耗时，必须先证明数值不会把 denied candidate 数量编码出来，优先使用 Kernel 后阶段和固定桶。空结果与 denied 结果共享 terminal/category，不发布“searched N directories”。

### 我方接口形状草图

```python
@dataclass(frozen=True)
class AuthorizedTrajectoryProjection:
    run_ref: str
    decision_ref: str
    policy_snapshot_ref: str
    policy_epoch: int
    query_digest_profile: str
    query_digest: str
    package_digest_profile: str
    package_digest: str
    effective_budget: PackageBudget
    used_budget: PackageBudget
    authorized_evidence_refs: tuple[str, ...]
    outcome: Literal["delivered_authorized", "delivered_empty"]

@dataclass(frozen=True)
class RestrictedDecisionAudit:
    organization_id: UUID
    run_ref: str
    decision_ref: str
    policy_snapshot_ref: str
    policy_epoch: int
    category: Literal["no_authorized_evidence"]
    recorded_at: datetime
```

不存在通用 `RetrievalTrace(uri, score, query, thinking)` 类型；operator UI 读取的是 one-shot、same-org、digest-only projection，而非 Runtime 表直读。

### 测试 oracle

- 对 delivered-authorized、nonexistent、cross-org、same-org-denied、empty-index 建 golden serialization，扫描 ContextRun/DecisionAudit/HTTP/log/metrics 中无 raw query、URI、candidate/ref、name、score、count、body。
- query digest 使用版本化、Organization-bound HMAC；同 org 相同 bytes 可比较，跨 org 不相关，key rotation 改变 domain，密钥不序列化。
- Package digest 可按固定 canonical profile 重算；数据库只保存 digest 与 authorized Evidence refs，不保存 Package body。
- Runtime role 只能 INSERT、不能 SELECT；Control/worker 无表权；one-shot operator read 强制 same-org、exact decision、短 TTL 和提交后消费。
- delivered-empty 只有 generic audit category；authorized delivery 不产生第二份 denied audit。
- 任意 telemetry adapter 接到 CandidateRef/pre-auth score 时测试失败；阶段耗时只允许已登记字段与桶。

### 验证命令、工作量、依赖

- 命令：`make lint`、`make typecheck`、`make test`、`make catalog`；FORCE-RLS/roles/operator ticket 用 `make db-up && make integration && make security-gate`；HTTP projection 用 `make smoke`；最终 `make check`。
- 工作量：**6–10 engineer-days**。
- 依赖：ADR-0031 当前载体；日志/metrics redaction catalog；operator auth；若增加阶段耗时或 post-auth ranking，需新 ADR 与 retention policy，不能由本蓝图静默激活。

## 3.5 Studio / Helper / VikingBot → co-resident local Evidence Console

### 上游路径与可观察行为

OpenViking 在同一服务的 `/studio` 提供 Home、Resources、Retrieval、Sessions、Request Logs，见 [`docs/en/guides/05-observability.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/en/guides/05-observability.md) 和 [`openviking/server/routers/console.py`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/openviking/server/routers/console.py)。Helper 检测 Claude Code/Codex/Cursor 等集成并展示 recall、prompt injection、capture、commit timeline，见 [`docs/en/agent-integrations/14-openviking-helper.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/en/agent-integrations/14-openviking-helper.md)。VikingBot 把 context、model、tool 和 delivery 组合为 agent，见 [`docs/en/concepts/15-vikingbot.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/en/concepts/15-vikingbot.md) 与 [`docs/en/guides/17-vikingbot.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/en/guides/17-vikingbot.md)。最后一种一体化边界不适用于 ContextEngine。

### 本仓 seam / authority

- [ADR-0090](../decisions/0090-admit-a-co-resident-local-evidence-console.md) 已固定 Jinja2 + static CSS、现有 API 进程、显式 browser auth、Runtime 经 in-process ASGI HTTP、Control 每次独立 credential/one operation、无 promote UI、无 score。
- [ADR-0006](../decisions/0006-engine-delivers-context-not-answers.md)：Ask 的干净答案属于外部/consumer presentation；Engine 在线产物仍是 ContextPackage。
- ActionPlane 必须 `prepare → ticket → exact effect`；Evidence Console 不得因同进程而直调 effect 或 release authority。

### Room-A 行为规格

建议把上游 UX 模式映射为 ADR-0090 已准入的七类 operator job，而不是复制 Web Studio：

1. **Home/health**：显示 API/worker readiness、active release/policy refs、最近 authorized/empty run 数的安全聚合；不显示 tenant-wide query 或 denied candidate 统计。
2. **Source progress**：Control read，一次请求一个 `SourceProgressRead`；浏览器每次提交 Control credential，server 不存储、不回显。
3. **File import**：先 preview，token 绑定 current Membership、exact bytes digest、compiler version、Fragment set；confirm 是新的单一 `FileImportConfirm` 操作并重新校验。
4. **Article policy**：read/change 分开；change preview token 绑定 expected policy version/epoch/proposed policy，confirm 只提交 exact effect。
5. **Hit Test / Retrieval playground**：调用 `/v0/resolve` 的 Acquire，只渲染 ContextPackage Blocks/Evidence/预算/digest；numeric score/rank 明示 unavailable。
6. **Ask**：同样先经 Runtime；每个 citationOpenRef 以 OpenCitation 获得 replacement Package，核对 exact Article/Revision/Fragment/Policy Epoch 后才由独立 presentation/model 形成文本。OpenCitation 未 active 时不伪造成功。
7. **Timeline/feedback**：只展示 ADR-0031 authorized-only digest lineage；反馈是 evidence candidate，不是 Control 操作，不触达 promote。

同进程只减少部署拓扑，不合并权限。匿名 browser 不继承 dogfood principal；browser session proof 不包含 credential/identity claim；Control credential 与 Runtime browser proof 相互不能替代。VikingBot 的 generation、tool loop 与 channel adapter 必须留在 BotDelivery/ActionPlane/consumer，不移入 console 或 engine。

### 我方接口形状草图

```python
@router.post("/console/runtime/hit-test")
async def hit_test(browser_proof: BrowserProof, form: HitTestForm) -> HTMLResponse:
    package = await in_process_http.post("/v0/resolve", acquire_wire(form))
    return render_package_without_scores(package)

@router.post("/console/control")
async def control_call(
    browser_proof: BrowserProof,
    control_credential: TransportSecret,
    operation: ClosedControlOperation,  # exactly one union member
) -> HTMLResponse:
    trusted = authenticate_one_control_call(control_credential, operation)
    return render(await control.consume_once(trusted, operation))
```

`ClosedControlOperation` 仅含 ADR-0090 明列的 source-progress read、preview-bound File import、Article-policy read/change；接口不接受 operation list、generic method name、release credential 或 engine object reference。

### 测试 oracle

- 匿名、expired browser proof、public authenticator reject 都在 Runtime/Control I/O 前拒绝；cookie 为 short-lived/HttpOnly/SameSite=Strict 且不含原 credential。
- monkeypatch 证明 Hit Test/Ask 经过 HTTP `/v0/resolve`，没有 direct retriever/assembler/DB 调用；citation 显示前确实 OpenCitation 重授权并校验 replacement Package lineage。
- 每个 Control request 恰好一个 operation；批量 body、credential reuse/omission、cross-org Membership、stale preview token 全拒绝。
- HTML snapshot 无 credential、raw query、source URI、denied count、score/rank；CSRF/session fixation/HTML escaping 有负面测试。
- feedback path 只有 Runtime-role function 权限；UI 无 release operator field/route，Control role 不能 promote。
- 不启动第二进程、Node runtime 或外部 CDN；`make smoke` 证明 API/worker 现有 topology。

### 验证命令、工作量、依赖

- 命令：`make lint`、`make typecheck`、`make test`、`make catalog`、`make smoke`；browser auth、RLS、Control credential 用 `make db-up && make integration && make security-gate`；最终 `make check`。
- 工作量：**7–11 engineer-days**。
- 依赖：ADR-0090 carriers、server templates/static assets、dogfood browser auth、local Control composition、OpenCitation activation（Ask 完整 citation path）、feedback evidence contract。

## 3.6 MCP client integrations + SDK → ONE sealed Runtime 的薄 parity

### 上游路径与可观察行为

OpenViking 把 `/mcp` 与 REST 同进程，以 streamable HTTP 暴露 13 个工具，包括 find/search/recall/read/list/remember/add_resource/watch/grep/glob/forget/health；工具表见 [`docs/en/guides/06-mcp-integration.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/en/guides/06-mcp-integration.md)，实现注册见 [`openviking/server/mcp_endpoint.py`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/openviking/server/mcp_endpoint.py)。Claude Code、Codex、Cursor 组合 hooks 与 MCP：prompt 前 recall/inject，response 后 capture，PreCompact/session end commit，见 [`claude-code.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/en/agent-integrations/02-claude-code.md)、[`codex.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/en/agent-integrations/04-codex.md)、[`cursor.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/en/agent-integrations/12-cursor.md)；通用 client 连接形状见 [`mcp-clients.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/en/agent-integrations/06-mcp-clients.md)。

### 本仓 seam / authority

- [ADR-0017](../decisions/0017-trusted-invocation-and-closed-runtime-access.md)：所有 carrier 调同一非插件化 `ContextRuntime.resolve(AuthenticatedInvocation, TrustedDeliveryContext, Acquire|Continue|OpenCitation)`。
- [ADR-0047](../decisions/0047-freeze-openapi-v0-through-one-runtime-path.md) 与 [ADR-0048](../decisions/0048-generate-typescript-sdk-behind-a-closed-facade.md)：HTTP/OpenAPI/generated SDK 是 contract authority；MCP 不能形成第二套模型或 capability。
- [`2026-07-28-exposure-spike-mcp-vs-api.md`](./2026-07-28-exposure-spike-mcp-vs-api.md) 与 [`2026-07-28-pi-agent-consumer-spike.md`](./2026-07-28-pi-agent-consumer-spike.md)：MCP 只在真实 consumer gap 被证明时增加，pi/agent 也只能消费 fresh evidence-bearing ContextPackage。

### Room-A 行为规格

1. 第一版 MCP 只有 `resolve_context`，参数是与 OpenAPI `Acquire` 等价的 purpose/query/已激活 target shape；authenticated MCP transport 由 ingress adapter 兑换 per-resolve opaque `DeliveryEvidenceRef`，caller 不能在 tool body 制造 Organization、audience、trusted identity 或 raw delivery claims。
2. tool handler 只做 schema translation → 调用 generated/typed HTTP facade → 原样映射 `ResolutionOutcome`。返回完整 ContextPackage（含 digest、expiry、Evidence/Blocks），不摘取“答案”、不缓存、不自行 vector search、不自行目录 read、不自行授权。
3. `open_citation` 只有在同一 OpenCitation carrier 被正式激活后才加入，输入仅为 opaque citationOpenRef；`Continue` 同理。未激活能力返回现有 generic `request_not_available`，不能返回假 empty success。
4. 不复制上游的 `read/list/glob/grep`：它们会把 filesystem 当 authority 并暴露 existence。不复制 `remember/forget/add_resource`：Learning candidate、Control import、ActionPlane effect 必须各走自己的 authority，不能混入 Runtime MCP。
5. coding-agent hook 可以学习生命周期，但行为收窄为：`UserPromptSubmit/beforeSubmitPrompt` 发起 fresh Acquire 并把**完整、未过期、audience-bound** Package 交给 agent adapter；PreCompact/Stop/SessionStart 不直接写 memory。若未来记录反馈或 Learning input，必须调用已接受的独立 evidence/candidate seam，不能把 transcript 通过 MCP `remember` 激活。
6. credentials 只来自平台的 MCP authenticated transport/secret store；禁止把 DeliveryEvidenceRef、API key 或 trusted audience 写入 prompt、tool body、logs。每次 resolve 新建 evidence ref，不在 long-lived MCP session 中复用。
7. 更新两份 2026-07-28 spike 时记录：OpenViking 证明 Hooks + MCP proxy 能覆盖 Claude Code/Codex/Cursor 的安装与 lifecycle UX，但不证明宽 tool surface 是必要的；ContextEngine 的 parity 标准是“相同 sealed Runtime 语义与错误”，不是工具数量。

### 我方接口形状草图

```json
{
  "name": "resolve_context",
  "inputSchema": {
    "type": "object",
    "additionalProperties": false,
    "required": ["purpose", "query"],
    "properties": {
      "purpose": {"type": "string"},
      "query": {"type": "string"},
      "targetRefs": {"type": "array", "items": {"type": "string"}}
    }
  }
}
```

```python
async def resolve_context(tool_input: AcquireToolInput, transport: McpTransport) -> dict:
    invocation, evidence_ref = await trusted_ingress.authenticate_and_issue(transport)
    wire = acquire_to_closed_resolve_wire(tool_input)
    outcome = await generated_runtime_sdk.resolve(
        wire,
        delivery_evidence_ref=evidence_ref,
    )
    return exact_context_package_or_closed_refusal(outcome)
```

若 `targetRefs` 还不是已激活 public contract 就从 v0 schema 删除，不能为 MCP 单独发明。MCP response 的语义 golden 必须与 HTTP SDK response 一致。

### 测试 oracle

- 对同一 authenticated invocation，HTTP、generated SDK、MCP 得到字段等价、digest 可复算的 ContextPackage 或同一 closed refusal；MCP 不改变排序/预算/expiry。
- MCP handler 的依赖图只能指向 typed HTTP/generated facade；静态 catalog 禁止 import retriever、Kernel plugin、DB repository、assembler、Control/Learning。
- tool body 注入 org/audience/identity/DeliveryEvidenceRef、跨 session replay、expired ref、缺 tenant context 均在 content work 前 generic fail closed。
- tool list snapshot 初版恰为 `resolve_context`；不存在 remember/forget/list/read/glob/grep/add_resource。未来 OpenCitation 需 capability activation test 才改变 snapshot。
- Hook 每个 prompt fresh Acquire；过期/不同 audience Package 不注入；Runtime unavailable/missing context 时停止 context-dependent consumer 行为，而非继续生成并声称有上下文。
- logs/telemetry 不含 prompt、Package body、credentials、DeliveryEvidenceRef；MCP auth error 不回显 secret。

### 验证命令、工作量、依赖

- 命令：`make lint`、`make typecheck`、`make test`、`make catalog`、`make smoke`；认证/RLS/DeliveryEvidenceRef 用 `make db-up && make integration && make security-gate`；最终 `make check`。
- 工作量：**6–9 engineer-days**，不含每个 consumer 的独立安装器。
- 依赖：真实 consumer gap/maintainer go decision；MCP transport dependency 审核；OpenAPI/generated SDK parity fixtures；MCP authenticated ingress；DeliveryEvidenceRef issuer；首个目标 consumer（建议 Codex 或 Claude Code 二选一）。

## 3.7 Apache-2.0 subtrees → 逐路径 copy+patch 决策

### 上游路径与核验结果

候选表面是 [`crates/ov_cli`](https://github.com/volcengine/OpenViking/tree/49b182045b42d34ad530948ad77d9d0226897da8/crates/ov_cli) 与 [`examples`](https://github.com/volcengine/OpenViking/tree/49b182045b42d34ad530948ad77d9d0226897da8/examples)。但是 `ov_cli` 的 [`Cargo.toml`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/crates/ov_cli/Cargo.toml) 自报 MIT，和 [`crates/LICENSE`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/crates/LICENSE)/README 的 Apache 声明冲突；examples 内又有 [`openwebui-plugin/pyproject.toml`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/examples/openwebui-plugin/pyproject.toml) 明确 AGPL、[`opencode-plugin/package.json`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/examples/opencode-plugin/package.json) Apache、[`openclaw-plugin/package.json`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/examples/openclaw-plugin/package.json) MIT 等路径差异。

### 本仓 seam / authority

- [ADR-0074](../decisions/0074-adopt-controlled-third-party-code-reuse.md)：只复制 pinned、license-verified permissive region，并在 `third_party/` 登记 attribution、modification 和 SBOM。
- [ADR-0048](../decisions/0048-generate-typescript-sdk-behind-a-closed-facade.md)：generated SDK 是客户端 contract authority；手抄 CLI/client 会产生第二份 wire 模型。
- 静态安全 catalog 与 `third_party/ARTIFACT_EXEMPTIONS.toml`： shipped artifacts 必须可追溯。

### Copy+patch 复刻配方与本次结论

**本次结论：none — generated SDK + 自有薄 MCP adapter 路径更好。** 没有任何上游文件应在本 spike 后进入 `third_party/`。理由不是 permissive 代码不可用，而是：`ov_cli` 许可证尚未消歧；examples 逐路径许可证不一致；上游 wire 与 filesystem/memory authority 不符合 sealed Runtime；复制客户端 glue 的维护收益低于 generated SDK。

未来若出现 generated SDK 无法覆盖的、可量化的 consumer gap，只能按以下配方开独立 PR：

1. 将候选收窄到逐文件列表，核对每个文件的 SPDX、最近的 LICENSE/NOTICE、manifest、生成物和嵌套依赖；冲突即停止并请求 maintainer/legal 书面消歧。
2. 证明候选不 import/link AGPL 主项目，也不包含由 AGPL 代码生成或搬运的实现；固定同一 SHA 并记录原始逐文件 SHA-256。
3. 维护者批准具体 reuse issue 后，复制到 `third_party/openviking/<subtree>/`，保留上游 license/notice，新建 `MODIFICATIONS.md` 与 SBOM；只从该 vendored path import。
4. 删除/重写任何 filesystem authority、memory write、raw URI、caller-authored tenant 或 direct REST model；适配到 generated closed facade。若重写量接近全部逻辑，取消复用，回到 clean-room。
5. `make catalog` 必须拒绝未登记文件、AGPL SPDX、未固定 commit、hash drift、越界 import 和 shipped SBOM 缺项。

如将来获准，`UPSTREAM.toml` 至少采用以下形状（占位符不能在审批前填成推定事实）：

```toml
repository = "https://github.com/volcengine/OpenViking.git"
commit = "49b182045b42d34ad530948ad77d9d0226897da8"
source_paths = ["<exact/permissive/file>"]
excluded_paths = ["openviking", "openviking_cli", "web-studio", "bot", "<AGPL overrides>"]
reuse_mode = "copy-patch"
approval = "<maintainer-approved-issue-or-pr>"
license = "<path-level verified SPDX expression>"

[[files]]
upstream_path = "<exact/permissive/file>"
vendored_path = "third_party/openviking/<subtree>/<file>"
sha256 = "<sha256 of pinned upstream bytes>"
```

### 我方接口形状草图

本区域不新增 runtime 接口。若未来 copy+patch，只能实现已经由 generated SDK 封闭的 adapter protocol：

```python
class ContextPackageConsumerAdapter(Protocol):
    async def acquire(self, request: GeneratedAcquireWire) -> GeneratedResolutionOutcome: ...
```

adapter 不得自己定义 tenant、authorization、filesystem 或 memory contracts。

### 测试 oracle

- `UPSTREAM.toml` 的每个 `source_path` 存在于 pinned commit，vendored bytes/修改日志/许可证/SBOM 可追溯；未列文件不得 ship。
- catalog 注入 AGPL SPDX、manifest/license 冲突、父许可证覆盖子 AGPL、浮动 branch、hash drift，均必须失败。
- dependency graph 无 AGPL package/link；adapter contract golden 与 generated SDK 完全一致。
- vendored adapter 删除后 generated SDK 主路径仍可构建/运行，证明它不是 Runtime foundation。

### 验证命令、工作量、依赖

- 当前文档决策命令：`make catalog`、`make lint`、`make test`；有 vendored artifact 时再运行 `make build` 并检查 wheel/sdist SBOM；最终 `make check`。
- 工作量：**2–3 engineer-days** 用于一次正式 path-level/legal/provenance 决策；若实际 vendoring 另估。
- 依赖：maintainer/legal 处理许可证冲突；明确 consumer gap；ADR-0074 approval；artifact/SBOM catalog。当前依赖未满足，因此 copy+patch 保持关闭。

## 3.8 战略判断：相邻竞品，也是有限互补方；不是 runtime foundation

### 上游路径与本仓 thesis

OpenViking 自称 agent 的 context database，把 memories/resources/skills 组织为可浏览的 virtual filesystem，以 tiered loading、recursive retrieval、session memory 和 observable trajectory 支撑 agent，见 [`README.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/README.md)；VikingBot 进一步组合 context/model/tools/channel，见 [`docs/en/concepts/15-vikingbot.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/en/concepts/15-vikingbot.md)。从这些固定材料可观察到 account/user/agent-local 的使用模型，但“缺乏与 ContextEngine 等价的 FORCE-RLS、多租户 audience authorization”只能表述为**在已核验路径中未见同等证据**，不能扩张为对上游全部安全能力的断言。

### 本仓 seam / authority

ContextEngine 的边界由 [ADR-0006](../decisions/0006-engine-delivers-context-not-answers.md) 和 [ADR-0061](../decisions/0061-commit-to-the-complete-context-layer-thesis.md) 固定：我们建设完整 context layer，但在线只交付 current-audience-bound、可审计、可预算的 ContextPackage；generation/channel/effect 分离，多租户授权真相是 Kernel 的不可替代核心。

### Room-A 行为规格（产品边界）与明确答案

- **竞争重叠**：两者都争夺“agent 不应直接面对黑盒向量库”的 context layer 位置，都重视结构化摄取、分层披露、session-derived learning、可观察检索和 Claude Code/Codex/Cursor 等消费者。对单人/本地 agent memory 需求，它是相邻竞品。
- **关键分歧**：OpenViking 的主对象是可读写 memory filesystem 和 agent integration；ContextEngine 的主对象是经当前身份、Organization、audience、purpose、policy epoch、budget 和 provenance 封闭的 ContextPackage。目录深度/路径不是 authority，Learning 不能发布，ContextRun 不保存 denied trace，Engine 不生成答案或执行工具。
- **互补可能**：OpenViking 可以作为外部 consumer，通过未来 thin MCP/SDK 获取 ContextPackage；也可被评估为 Supply source，但只有 connector 能取得并证明 Live/Mirrored/真正 Weak `SourceAclEvidence`、映射 exact Organization/Article 且运行在 WorkerLease 下时才准入。若只有单用户 bearer/本地路径，没有可证明 ACL，就不能作为多租户授权来源。
- **禁止关系**：不能把 OpenViking server、filesystem、retriever、memory store、Studio 或 VikingBot 作为 ContextEngine Runtime foundation；不能让它绕过 Kernel、保留第二索引/持久化或把 ContextPackage 拆成 raw content transport。

因此最准确定位是：**产品层的相邻竞品，协议/来源层的条件式互补方，安全架构上仅作战略参考。** ADR-0061 的“完整”不意味着吞并 agent runtime；完整的是从 Supply 到 authorized delivery 再到 governed Learning 的 context truth 闭环。

### 我方接口形状草图

```python
class ContextConsumer(Protocol):
    async def consume(self, package: ContextPackage) -> None: ...

class OptionalOpenVikingSupplyConnector(Protocol):
    async def discover(self, lease: WorkerLease) -> tuple[SourceObjectRef, ...]: ...
    async def acl_evidence(self, ref: SourceObjectRef, lease: WorkerLease) \
        -> SourceAclEvidence: ...
```

consumer 只能收完整 current ContextPackage；Supply connector 只产出候选 source objects/ACL evidence，经 ContextEngine 编译与授权，不把 `viking://`、上游 index 或 memory decision带进 Runtime。若 `acl_evidence` 无法达到已登记 carrier 的语义，connector 保持 `NOT_ACTIVE`。

### 测试 oracle

- 架构 import/call graph 证明任何 OpenViking adapter 都不能被 `engine/runtime` 当 retriever/Kernel/assembler 使用，不能持有第二 Runtime index。
- consumer 收到过期、其他 audience、digest 不匹配的 Package 时拒绝；不能仅用 Blocks/raw content 绕过 package lineage。
- 可选 Supply spike 对缺 org mapping、缺 WorkerLease、ACL evidence 失败、Weak 被当 fallback、source ACL 撤销全部 fail closed。
- BotDelivery/ActionPlane 测试证明 generation 与 exact effect 不在 Engine；缺 ContextPackage 的 context-dependent answer/effect 为 missing-context refusal。
- 任何战略/公开 claim 在准入第五仓前不引用本报告为 public provenance。

### 验证命令、工作量、依赖

- 命令：战略 boundary 的静态检查为 `make lint`、`make typecheck`、`make test`、`make catalog`；未来 connector/RLS 用 `make db-up && make integration && make security-gate`；consumer/API 用 `make smoke`；最终 `make check`。
- 工作量：**2–4 engineer-days** 用于边界 ADR/产品文字与 dependency tests；实际 OpenViking Supply connector 不在此估算内。
- 依赖：maintainer 的第五仓/产品定位决定；MCP exposure go/no-go；任何 connector 都需独立 SourceAclEvidence、license/network、threat-model 和 activation issue。

# 4. 不可借鉴清单与必须杀死的隐含前提

| 必须杀死的隐含前提 / 上游诱因 | 为什么危险 | ContextEngine 强制替代 |
|---|---|---|
| L0/L1/L2 或目录 depth 越深，权限越大 | 把数据结构误当授权，能读取未授权 parent/child | tier 仅是 AssemblyProfile 信息密度；每个初始/跨 Article CandidateRef 走 Kernel |
| 先查 raw candidate/正文，再做权限过滤 | content-bearing rank/hydration 已看见拒绝内容 | `CandidateRef → AuthorizationKernel → AuthorizedProjection`，后续 consumer 只接受 projection |
| path prefix、glob、recursive flag 等于 scope | 无限后代、未来新增对象和 sibling 被隐式纳入 | ADR-0024 exact finite EffectiveScope intersection；每 hop 重授权 |
| `viking://` URI、文件名、目录是否存在是无害 metadata | 名称、数量和路径本身泄露敏感资源存在 | opaque locator；denied/nonexistent/cross-org 同形；只投影 authorized labels |
| `node_limit`/`level_limit` 是安全控制 | 它们只限制资源量，不能证明 audience/purpose/tenant | PackageBudget + Kernel；limit 永不扩大 scope |
| 同一次历史授权可永久打开 citation | Membership、ACL、Revision、Policy Epoch 已变化 | ADR-0051 每次 OpenCitation 新身份/DeliveryEvidenceRef/当前重授权 |
| rerank 故障可无条件回退 vector score | 可能把 pre-auth score/content 带入安全路径，改变选择而无评估 | 仅授权后、profile-登记的 deterministic fallback；否则 unavailable/fail closed |
| session model 输出可直接 create/merge/delete active memory | 模型成为第二个 publication authority，绕过评估/回滚 | immutable candidate/diff → CurationSnapshot → evaluate → release-operator promote |
| ContextRun digest 能重建 session/query | digest-only lineage故意不可逆；反推会建立暗中内容存储 | 新的 consent/retention Learning input carrier，未接受则 session extraction NOT_ACTIVE |
| retrieval trajectory 越详细越可观察 | denied URI/score/count/query/thinking 形成枚举与新内容库 | authorized-only ContextRun；七字段 generic DecisionAudit；redaction catalog |
| numeric rank/score 属于 operator 基本权利 | score gap 可泄露 denied candidates，且 public Package 当前 rank-free | Console 只显示 Blocks/Evidence/预算/digest，score 明示 unavailable |
| 同进程 Studio 可直调 engine/DB 或继承 dogfood principal | presentation 变成旁路 authority，匿名浏览器获得环境权限 | 显式 browser proof；Runtime 经 ASGI HTTP；Control 独立 credential、一次一操作 |
| feedback/Helper 的“同步 memory”可以顺带上线 | feedback 变成发布或 Control 能力 | feedback 只产生 evidence/candidate；无 Console promote route |
| VikingBot 的 model/tool/channel 应进入 Engine | Engine 开始交付答案与效果，破坏 ADR-0006/ActionPlane | BotDelivery/consumer generation，ActionPlane prepare→ticket→perform，Engine 只出 Package |
| MCP 工具越多 parity 越好 | read/list/remember/forget 形成第二 Runtime/Control/Learning authority | 初版一个 `resolve_context`，以后只镜像已激活 closed Runtime capability |
| MCP 长连接可复用 tenant/audience/DeliveryEvidenceRef | trusted context 过期或跨 prompt/session replay | authenticated transport 每 resolve 兑换短期、请求绑定的 opaque evidence ref |
| recall/memory 失败后 agent 可继续并声称有上下文 | 违反 missing-context fail closed，产出不可追溯答案/效果 | context-dependent consumer 在无 current Package 时拒绝；无假 empty success |
| 单 agent/local account 语义可直接推广到多租户 | 没有 Organization、current Membership、group audience 与 RLS 证明 | SourceAclEvidence + UserActor/WorkerLease + FORCE RLS + Kernel/security gate |
| `examples/LICENSE` 可覆盖所有子项目 | `openwebui-plugin` 已明确 AGPL；其他 manifest 也有 MIT/Apache/缺省 | 每次逐文件/manifest/SPDX/NOTICE 核验，冲突即停止 |
| `ov_cli` 已确定是 Apache-2.0 | Cargo manifest 自报 MIT，与父 LICENSE/README 冲突 | legal/maintainer 消歧前不复制；当前 none |
| AGPL 网络部署不算分发，所以可复制服务代码 | AGPL 第 13 节专门覆盖修改程序的远程网络交互 | 主项目严格 clean-room，零 code/dependency copy；必要时寻求法律意见 |
| [未取证] 可由 README 宣传或实现直觉补齐 | 会把推断包装成公开 provenance | 保留 `[未取证]`；只用 pinned permalink 支撑有限 claim |

# 5. 推荐实现顺序 + 给 coordinator 的开放问题

## 推荐顺序

1. **先做治理决策（2–3d）**：决定 OpenViking 是否有条件成为第五仓；记录 AGPL clean-room、`ov_cli`/examples 排除项和 public-claim 范围。在决定前不改四仓公开基线，也不创建 `third_party/openviking`。
2. **冻结 Room-A contracts 与 oracle（2–4d，和第 1 步可并行评审）**：为 `AssemblyProfile` density/hop、Authorized browse、trajectory redaction、Learning candidate diff 写新 ADR/contract fixtures；明确哪些 future carrier 仍是 `NOT_ACTIVE`。
3. **先实现 tiered Assembly（6–9d）**：它复用现有 AuthorizedProjection、Article lineage 和 PackageBudget，价值高且不要求新的外部协议。先通过 raw-candidate rejection 与跨 Article reauthorization tests。
4. **收紧 trajectory projection（6–10d）**：在增加更丰富 UI/MCP 前让 logs/metrics/operator view 有结构性红线；扩展 catalog 对 URI/query/score/count 的扫描。
5. **完成 ADR-0090 Evidence Console UX（7–11d）**：先 Hit Test/source progress/preview-confirm；browse 只投影当前 Package。OpenCitation 未激活前，citation/browse 深入诚实显示 unavailable。
6. **激活 OpenCitation 后做 Authorized browse（8–12d）**：opaque ref、current reauthorization、no-total pagination、per-hop budget 和 non-enumeration 一起交付，不单独上线 filesystem endpoints。
7. **另立 retention 决策后做 session candidate generation（10–15d）**：先 LearningInput consent/retention，再 intent/outbox/WorkerLease、candidate diff、CurationSnapshot、evaluate/promote；绝不从 ContextRun digest 反推正文。
8. **最后按真实 consumer gap 决定 thin MCP（6–9d）**：用一个目标 consumer 做 `resolve_context` parity；成功标准是 ContextPackage/closed-refusal 等价，不是复制 13 tools。同步把本文结论提炼进 MCP-vs-API 和 pi-agent 两份 spike，但只有在第五仓准入后才能把固定链接升级为 public provenance。
9. **保持 copy+patch 为 none**：只有 generated SDK 确认无法解决的 gap、路径许可证消歧和 ADR-0074 审批同时满足时，才重新评估 permissive subtree。

总量不是简单相加：contracts/redaction 与 Console/Runtime 可共享 fixtures；不含 OpenCitation carrier、生产 operator identity、session 加密 retention 或新 Supply connector 的基础建设。按当前边界，核心蓝图约 **47–73 engineer-days**，治理/接口评审可与部分测试设计并行，但安全 gate 不可并行绕过。

## 给 coordinator 的开放问题

1. **OpenViking 是否进入公开四仓证据基线，成为第五仓？** 建议：**有条件进入**，仅作为 context filesystem/tiering、session candidate UX、observable retrieval 和 agent exposure 的参考；明确排除它作为多租户授权、安全证明或 runtime foundation。先完成路径许可证/legal 复核与逐 claim permalink，再改公开基线。
2. `AssemblyProfile` 是否接受 `DisclosureDensity + per-hop limits` 为新的 immutable RuntimeProfile constituent，还是只在现有 PackageBudget/profile 内编码？建议新建显式 profile contract，防止调用方把 tier/depth 当 request-time authority。
3. Authorized browse 的首个 UX 是否必须等待 OpenCitation 正式激活？建议等待；在此之前 Console 只对当前 ContextPackage 做纯投影，不创建临时 filesystem API 或伪 continuation。
4. 是否愿意为 session-derived Learning input 承担独立的正文 consent、加密、retention、delete/export contract？若否，应明确只从现有 authorized Package evidence + explicit feedback 产生 candidates，原始 transcript extraction 长期保持 NOT_ACTIVE。
5. operator trajectory 是否确有逐阶段 latency 的需求？建议首版只显示 ADR-0031 已有 digest/budget/outcome；任何新 timing/rank 字段先做侧信道 threat analysis，numeric score 继续关闭。
6. thin MCP 的首个真实 consumer 选 Codex 还是 Claude Code，且 consumer 缺口是否足以优先于 generated SDK/HTTP？建议只选一个做 parity spike，初始工具面固定为 `resolve_context`。
7. 是否请 maintainer/legal 向上游确认 `crates/ov_cli` 的 MIT vs Apache-2.0 冲突？即使确认，也建议暂不复制 CLI，因为 generated SDK 更符合 ADR-0048；确认结果只用于未来治理准确性。
8. 是否允许未来把 OpenViking 作为 Supply source 做独立 spike？建议只有在能取得强 SourceAclEvidence 与 exact Organization mapping 时开启；单用户 token/local path 只能证明连接性，不能激活多租户 carrier。

在上述问题被回答前，本报告的默认执行边界是：**AGPL 全部 clean-room、无 copy+patch、无新 public provenance、无 filesystem/MCP/Learning carrier 自动激活，所有实现继续穿过唯一 sealed Runtime 与 release-operator publication authority。**
