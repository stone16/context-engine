# ContextEngine five public reference repositories evidence baseline

> Version: 2.0.0
>
> Date: 2026-08-02
>
> Status: candidate successor; prepared for maintainer legal decision in
> issue #205
>
> Supersedes: [`2026-07-19-four-public-repositories-evidence.md`](./2026-07-19-four-public-repositories-evidence.md)
> as the current public-reference aggregation entry; the v1 report remains the
> historical evidence record for its four original snapshots

## 1. Scope and decision boundary

This candidate baseline records five fixed repository snapshots as public prior-art
evidence. They may support bounded claims about observable behavior, Interface
shapes, tests, and product workflows. They do not establish ContextEngine's
authorization, security, compliance, performance, or production-readiness
claims.

| Repository | Fixed snapshot | Admitted evidence domain |
|---|---|---|
| Dify | [`120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5`](https://github.com/langgenius/dify/commit/120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5) | Product orchestration, workflow, Provider/Index seams, retrieval operations |
| RAGFlow | [`4391e03886b996201f3b8818f671b19eb24d0f7b`](https://github.com/infiniflow/ragflow/commit/4391e03886b996201f3b8818f671b19eb24d0f7b) | Document compilation, structured chunks, retrieval, hydration, differential tests |
| MaxKB | [`32b2d885e47ad04639abd7a18490bf5937f9c072`](https://github.com/1Panel-dev/MaxKB/commit/32b2d885e47ad04639abd7a18490bf5937f9c072) | Preview/confirm, knowledge operations, Hit Test, human curation workflow |
| Onyx | [`2fb3dd10493b3883870fa8adced5b1a0e114feff`](https://github.com/onyx-dot-app/onyx/commit/2fb3dd10493b3883870fa8adced5b1a0e114feff) | Connectors, checkpoints, staged indexing, retrieval composition, real-dependency test layering |
| OpenViking | [`49b182045b42d34ad530948ad77d9d0226897da8`](https://github.com/volcengine/OpenViking/commit/49b182045b42d34ad530948ad77d9d0226897da8) | Context filesystem and L0–L2 tiering, session-to-candidate UX, observable trajectory, agent exposure |

The first four rows retain the claims, limitations, clean-room mappings, and
pinned first-party links recorded in the [v1 baseline](./2026-07-19-four-public-repositories-evidence.md#2-证据纪律).
This successor does not expand those claims. Section 3 adds OpenViking using
only first-party material at its fixed snapshot.

OpenViking's admission is deliberately narrower than the original four:

- **Whitelist:** context filesystem and information-density tiering,
  session-to-candidate workflow, observable retrieval trajectory, and agent
  exposure UX.
- **Blacklist:** multi-tenant authorization or security proof, Runtime
  foundation, compliance proof, and every form of copy+patch.
- **No transitive authority:** the tracked Room-A evaluation is not public
  provenance. Public claims must cite this baseline or the pinned upstream
  sources linked below.

## 2. Evidence discipline

This report uses the v1 evidence grades unchanged:

- **[Primary static]:** official source, test, license, or documentation at a
  full commit SHA; it proves only what is present at that snapshot.
- **[Repository synthesis]:** ContextEngine's clean-room interpretation of
  primary static evidence; it must remain traceable to a pinned permalink.
- **[Not evidenced]:** no dynamic run, fault injection, penetration test, or
  benchmark supports the statement.

No upstream system was run as part of this baseline revision. A fixed source
shape is not dynamic proof. A repository snapshot does not establish a security
guarantee for ContextEngine, and later upstream changes do not enter this
baseline without a new reviewed version.

The five repositories are not a common-corpus benchmark. Qualitative evidence
domains must not be converted into a retrieval-quality ranking.

## 3. OpenViking conditional admission

### 3.1 Context filesystem and information-density tiering

- **[Primary static]** OpenViking describes L0 abstract, L1 overview, and L2
  detail as progressively denser context representations loaded according to
  need; see [`docs/en/concepts/03-context-layers.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/en/concepts/03-context-layers.md#L1-L20)
  and its [retrieval guidance](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/en/concepts/03-context-layers.md#L165-L180).
- **[Primary static]** The `viking://` namespaces expose context through a
  browsable filesystem-shaped interface; see
  [`docs/en/concepts/04-viking-uri.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/en/concepts/04-viking-uri.md#L1-L35)
  and the [`ls` API shape](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/en/api/03-filesystem.md#L1-L25).
- **[Repository synthesis]** ContextEngine may use information density as an
  AssemblyProfile and post-authorization browse UX. Directory depth, URI
  prefix, and tier never grant access; content still follows
  `CandidateRef → AuthorizationKernel → AuthorizedProjection`, PackageBudget,
  provenance, and audit gates.

### 3.2 Session-to-candidate workflow

- **[Primary static]** Session commit archives messages before asynchronous
  summarization and memory extraction; see
  [`docs/en/concepts/08-session.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/en/concepts/08-session.md#L17-L24)
  and its [commit sequence](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/en/concepts/08-session.md#L107-L122).
- **[Primary static]** The extraction flow records candidate-level `skip`,
  `create`, and `none` decisions plus per-existing-item `merge` and `delete`
  decisions; see the [memory operation description](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/en/concepts/08-session.md#L144-L176).
- **[Repository synthesis]** ContextEngine admits the workflow only as a
  candidate UX: consented and authorized input may produce a reviewable
  candidate, while ContextLearning's release-operator path remains the sole
  publication authority. Raw transcript intake and direct model mutation of
  active memory remain `NOT_ACTIVE`.

### 3.3 Observable trajectory

- **[Primary static]** A `QueryResult` records a typed query, matched contexts,
  searched directories, and a `ThinkingTrace`; each `MatchedContext` can carry
  URI, level, score, and match reason. See the
  [`MatchedContext` and `QueryResult` types](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/openviking_cli/retrieve/types.py#L283-L319)
  and the [provenance test oracle](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/tests/retrieve/test_provenance.py#L17-L73).
- **[Repository synthesis]** ContextEngine admits observability as a product
  pattern only after authorization. ContextRun stays authorized-only; raw
  query, denied path, score, count, and reasoning trace remain excluded from
  tenant-visible output and Learning.

### 3.4 Agent exposure

- **[Primary static]** OpenViking exposes REST and a co-process MCP endpoint;
  the pinned guide enumerates the MCP surface in
  [`docs/en/guides/06-mcp-integration.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/en/guides/06-mcp-integration.md#L130-L148),
  while [`openviking/server/mcp_endpoint.py`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/openviking/server/mcp_endpoint.py#L1-L10)
  identifies the server endpoint.
- **[Primary static]** The Helper presents supported agent integrations and
  lifecycle traces; see
  [`docs/en/agent-integrations/14-openviking-helper.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/en/agent-integrations/14-openviking-helper.md#L21-L43).
- **[Primary static]** VikingBot combines context, model, tools, and delivery
  channels in one product surface; see
  [`docs/en/concepts/15-vikingbot.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/en/concepts/15-vikingbot.md#L1-L38).
- **[Repository synthesis]** ContextEngine admits the lifecycle and exposure UX,
  not the process boundary or tool count. HTTP, generated SDK, and any future
  thin MCP adapter must preserve one sealed Runtime contract. The engine does
  not generate answers, own delivery channels, or execute tools.

### 3.5 Explicitly unsupported claims

The reviewed OpenViking evidence documents account-global and current-user
namespaces in
[`docs/en/concepts/04-viking-uri.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/docs/en/concepts/04-viking-uri.md#L17-L31).
That is not evidence of ContextEngine-equivalent Organization isolation,
Membership freshness, audience authorization, FORCE RLS, sealed Kernel ordering,
or denied-trace non-enumeration. The bounded statement is: **no equivalent proof
exists in the reviewed snapshot evidence**. This baseline makes no broader claim
about OpenViking's complete security capabilities.

OpenViking cannot serve as ContextEngine's Runtime foundation, authorization
authority, second online index, or content transport around ContextPackage.
No OpenViking code, dependency, service, generated artifact, schema, or asset is
admitted for copying or Runtime use by this decision.

## 4. License facts and reuse boundary

The fixed root [`LICENSE`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/LICENSE#L1-L12)
is GNU AGPLv3, and the root
[`pyproject.toml`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/pyproject.toml#L12-L20)
declares `AGPL-3.0`. AGPLv3 §13's remote-network text is present at
[`LICENSE` lines 541–560](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/LICENSE#L541-L560).
These are source facts, not a legal conclusion.

The snapshot also contains conflicting or path-specific declarations:

- [`crates/LICENSE`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/crates/LICENSE#L1-L5)
  is Apache-2.0, while
  [`crates/ov_cli/Cargo.toml`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/crates/ov_cli/Cargo.toml#L1-L8)
  declares MIT and the
  [`README.md` license table](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/README.md#L233-L240)
  calls the Rust CLI Apache-2.0.
- [`examples/LICENSE`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/examples/LICENSE#L1-L5)
  is Apache-2.0, but the OpenWebUI example declares
  [`AGPL-3.0`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/examples/openwebui-plugin/pyproject.toml#L7-L12),
  the OpenCode example declares
  [`Apache-2.0`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/examples/opencode-plugin/package.json#L40-L46),
  and the OpenClaw example declares
  [`MIT`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/examples/openclaw-plugin/package.json#L56-L65).

The complete review packet and unresolved questions are recorded in the
[`OpenViking legal-review dossier`](./2026-08-02-openviking-legal-review-dossier.md).
The upstream metadata question is tracked in
[`volcengine/OpenViking#3689`](https://github.com/volcengine/OpenViking/issues/3689).

Regardless of later disambiguation, ContextEngine's D9 engineering decision is
**no OpenViking copy+patch**. Legal approval of behavior observation would not
authorize copying, vendoring, linking, execution, or a Runtime dependency.

## 5. Synthesis and evidence gaps

The five-repository synthesis retains ContextEngine's existing module ownership:

| ContextEngine area | Public reference input | Boundary ContextEngine must own |
|---|---|---|
| Product/Control UX | Dify orchestration; MaxKB preview/confirm; OpenViking browse/trajectory UX | Operator authorization, versioned candidate, sole promote authority |
| Document compilation | RAGFlow structural compiler | ParsedDocument family, deterministic provenance, publication transaction |
| Supply/freshness | Onyx checkpoint/staging; RAGFlow recovery; Dify visibility | Signed WorkerLease, dual watermarks, atomic active pointer |
| Retrieval/assembly | RAGFlow/Onyx retrieval; Dify routing; OpenViking density tiering | CandidateRef → Kernel → AuthorizedProjection, budget, provenance, audit |
| Curation/Learning | MaxKB operations; OpenViking session candidate UX | Authorized-only input, consent, review, ReleaseManifest promotion |
| Delivery/exposure | Dify/Onyx transport; OpenViking agent lifecycle UX | One sealed Runtime, trusted delivery, audience-bound ContextPackage |

Remaining evidence gaps are unchanged in kind:

1. No common-corpus upstream benchmark or complete production-topology run was
   performed for this baseline.
2. Static repository evidence cannot prove ContextEngine security or production
   capability.
3. Edition and path-specific license facts do not transfer across repository
   regions.
4. The maintainer legal decision for OpenViking behavior-observation-only reuse
   remains the final authority gate; this PR must not merge before that decision
   is recorded.

## 6. Repository authority index

- Current public-reference aggregation entry: this document after merge.
- Historical four-repository evidence and its pinned links:
  [`2026-07-19-four-public-repositories-evidence.md`](./2026-07-19-four-public-repositories-evidence.md).
- Implementation authority:
  [`docs/design/2026-07-18-context-engine-implementation-design.md`](../design/2026-07-18-context-engine-implementation-design.md).
- Reuse policy:
  [ADR-0074](../decisions/0074-adopt-controlled-third-party-code-reuse.md).
- OpenViking legal decision packet:
  [`2026-08-02-openviking-legal-review-dossier.md`](./2026-08-02-openviking-legal-review-dossier.md).
