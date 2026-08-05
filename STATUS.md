# Capability Status Ledger

This file records **what ContextEngine has actually proven, and what it has
not**. It exists because "a demo ran" and "the authorization path is
implemented" are very different claims, and conflating them in a security
product is how trust gets destroyed.

[← Back to README](./README.md) · [Roadmap and milestones](./PLAN.md) ·
[ADR index](./docs/decisions/README.md)

## How to read this file

| Label | Meaning |
|---|---|
| **Active** | An executable, registered proof exists and runs in CI. The claim is bounded to exactly what that proof covers — never generalized. |
| **`NOT_ACTIVE`** | Deliberately not implemented or not proven yet. The running service reports this in its own responses (`/health`, worker output) rather than silently stubbing it. |

Two rules govern every entry:

1. **A bounded proof never grows into a general claim.** Where a proof uses a
   synthetic fixture, a deterministic authority, or an injected test
   composition, that is stated. It does not imply the production carrier works.
2. **The ADRs are authoritative.** This file is a navigational summary. When
   this file and an ADR disagree, the ADR wins — and this file is the bug.

## Global invariants

These hold across every activation below and are release vetoes, not scores:

- Unauthorized Evidence leaked = **0**
- Wrong-Organization effect = **0**
- Missing tenant context = **fail closed, always**

Every release reports `PASS / FAIL / NOT_ACTIVE / NOT_APPLICABLE` against a
versioned catalog, with capability coverage listed separately, so an inactive
capability can never be reported as a passing one.

## Currently `NOT_ACTIVE`

The default application **rejects every credential and performs zero content
I/O**. ADR-0068 separately activates one explicit loopback dogfood composition;
it does not widen the default. ADR-0069 also admits a separate, short-lived
local operator process only when complete Control, release, dogfood, and worker
credential separation is explicitly configured; it adds no HTTP route and
grants one Control operation per call. ADR-0073 adds one explicit local
`promote-release` command under the separate release identity; it assembles a
candidate from the exact current File corpus and still delegates activation
only to `ContextLearning.evaluate` and `ContextLearning.promote`. Production
operator authentication, multiple operators, durable role assignment,
delegation, RBAC, and every production or non-loopback operator surface remain
`NOT_ACTIVE`. ADR-0083 separately activates one loopback-only, server-rendered
Evidence Console inside the API process; its Control-backed jobs require the
same separate exact-operation local Control credential. The following are known,
designed, and deliberately not active:

| Capability | Note |
|---|---|
| Production authentication (OAuth / JWT) | Module-level default application is reject-all across all three production authorities (authentication, Organization, Membership) |
| Production operator authentication / admin API | The opt-in local operator composition is one fixed identity per plane, local-process-only, and never a production ancestor |
| Durable general Principal / Agent grants | The default scope authority returns seven missing operands; dogfood separately carries the bounded current File operands and binds one configured Agent/purpose to the Release ceiling only |
| General / multi-user Source and Resource ACLs | Dogfood uses current mirrored File access plus Membership field rights. A separate deterministic Feishu twin proves bounded source-native Article ACL ingestion, but no live or general administration carrier is active |
| General content retrieval | Only the loopback File pgvector dogfood `Acquire` carrier is active |
| Local consumer expansion | The repo-local Claude Code skill and spawn-per-session local stdio MCP `Acquire` translator are active; pi and every broader MCP carrier remain `NOT_ACTIVE` |
| Maintainer Context CLI expansion | `context-engine-context` activates only loopback File `Acquire` and untrusted public Package inspection; citation open, evaluation replay, Control, ActionPlane, models, effects, and promotion are absent |
| Capability-aware agent operation discovery | ADR-0098 accepts a future generated short-lived `AgentOperationManifest`; no discovery endpoint, contract version, SDK method, or active manifest carrier exists |
| `Continue` carrier | The bounded dogfood composition keeps Continue unavailable; its private File `OpenCitation` carrier is active for UI citation closure |
| Structured acquisition / live surface context | ADR-0099 selects one exact bounded collaboration conversation as the first design workload; structured-family terms, provider carrier, live network, mixed-family Evidence, and surface attachments remain `NOT_ACTIVE` |
| Federated discovery, source-native authorization | Deterministic refusal only |
| Runtime model-inference carriers (`rewrite`, `rerank`, `select`) | The governed port is implemented, but no carrier is active until its consuming issue proves the exact profile and grant path, passes the resolve-owned shared `PackageBudgetMeter`, and publishes that meter's cumulative usage in the final package; real provider network calls remain `NOT_ACTIVE` |
| Live Feishu credentials/network, Slack, Google Docs, and issue #127 Feishu ingestion connectors | Issue #133 activates only twin-bounded downstream private-event verification and exact Sender-effect conformance; live network and ingestion remain separate gates |
| Group/public delivery, compensating deletes | The Feishu delivery profile is asker-private only; wider audience and compensation remain `NOT_ACTIVE` |
| Session-derived Learning intake | ADR-0100 accepts only a future consented, minimized, provenance-bound candidate path; raw tape reads/storage, extractor model egress, candidate carrier, governed Memory artifact, release bridge, and Runtime serving remain `NOT_ACTIVE` |
| Deployment/plugin productization | ADR-0101 bounds any future plugin to static composition at accepted ports, but defers generic categories/manifest/loader until a second implementation or measured drift; tenant code, hot loading, marketplace, and plugin-created service remain `NOT_ACTIVE` |
| MCP ingress | Only the maintainer's spawn-per-session local stdio `context_resolve` tool is active; remote/shared HTTP, multi-user/tenant, private/group, `Continue`, `OpenCitation`, generation, effects, resources/prompts, and MCP egress-grant handling remain `NOT_ACTIVE` |
| OpenViking public-reference admission | The versioned successor baseline, permalink audit, legal dossier, and upstream license-metadata question are prepared in #205, but the OpenViking packet is not authority while #205 remains open; maintainer/legal sign-off and the maintainer doc-steward decision remain pending. This activates no code reuse, dependency, Runtime carrier, or security claim. |
| Worker dead-letter transition / operator requeue | ADR-0060 adds bounded reclaim only; generation four is left untouched after expiry |
| Provider polling, delete execution beyond ADR-0057 | See the File Provider ADRs for exact boundaries |
| Streaming delivery | Explicit V1 non-goal — placeholder + edit instead |
| Answer generation inside the engine | Permanent non-goal — generation always lives above the engine boundary |
| Authoritative golden evaluation report from a file | Only a run the executor performed itself through the tracked seam can attest security; a caller-authored run file keeps its metrics and still reports `REFUSED` |

### Bounded dogfood Runtime

| ADR | Activates |
|---|---|
| [0068](./docs/decisions/0068-activate-loopback-dogfood-runtime.md), [0102](./docs/decisions/0102-activate-release-bound-local-query-embedding.md) | Explicit loopback single-Membership authentication plus File pgvector `Acquire`, with exact EffectiveScope removal before ANN `LIMIT`, sealed Kernel reauthorization, release-bound hash-verified local Qwen query embedding, mixed-profile refusal, internal budget enforcement, and final Policy Epoch veto |

`RUNTIME-DOGFOOD-AUTH-102`, `RUNTIME-DOGFOOD-CARRIER-102`, and
`RUNTIME-DOGFOOD-EPOCH-102` are registered release-veto evidence. The default
application remains reject-all and reports `NOT_ACTIVE`. Production
authentication, a second human, network exposure beyond the maintainer machine,
group/public audience, `Continue`, hybrid retrieval, non-File providers, and
external/network query embeddings remain `NOT_ACTIVE` pending issue #217.

### Read-only maintainer Context CLI

`context-engine-context query` sends one fresh closed `Acquire` through the
same loopback HTTP and environment-held bearer composition. Human output keeps
Package purpose/as-of/expiry, coverage, budget usage, every Block, and exact
Evidence/citation lineage together. Strict JSON is the validated public wire
document without a CLI wrapper; its one deliberate substitution is the
redeemable `egressGrant` value, which this non-egress caller never emits or
persists. `inspect` treats file or stdin JSON as untrusted and validates the
whole closed Package, digest, lineage, accounting, lifetime, and expiry before
rendering; the capture never becomes authority or reusable context.

Stable exits distinguish success, explicit refusal, service unavailable,
malformed Package, expired Package, and invalid local configuration. Empty
authorized context is an explicit non-enumerating refusal, not a corpus answer.
Dogfood `OpenCitation`, `Continue`, evaluation replay, remote/multi-user
operation, Control, ActionPlane, models, effects, and promotion remain absent or
`NOT_ACTIVE`; see the
[operator guide](./docs/operations/maintainer-context-cli.md).

### Repo-local Claude Code consumer

| ADR | Activates |
|---|---|
| [0088](./docs/decisions/0088-bind-local-consumers-to-fresh-evidence-bearing-packages.md) | One repo-local Claude Code skill that performs one fresh loopback `Acquire` per question, rejects expired or malformed Packages, retains each Block's exact Evidence ref, and captures question-only golden-set candidates under the configured durable private root |

The consumer takes a question only through standard input, generates the request
id internally, and reads the bearer only through the existing environment-held
caller configuration. It renders no Package past `expiresAt`; unavailable,
empty, expired, malformed, and secret-exclusion outcomes are distinguishable
closed local refusals. Candidate capture contains no corpus path or Package
content. This activates no pi consumer, MCP Adapter, `Continue`, dogfood
`OpenCitation`, IM delivery, channel egress, or external effect.

ADR-0088's historical statement above remains scoped to that consumer; the MCP
carrier is activated separately below and does not widen the consumer.

### Local MCP Acquire translator

| ADR | Activates |
|---|---|
| [0103](./docs/decisions/0103-activate-one-local-mcp-acquire-translation.md) | One maintainer-local, spawn-per-session stdio `context_resolve` tool that validates the existing closed `AcquireWire`, calls only the loopback dogfood `POST /v0/resolve`, and returns its exact closed outcome as structured content |

`RUNTIME-MCP-CARRIER-215` proves through an MCP SDK client, a real spawned
stdio process, loopback HTTP, PostgreSQL 17, File publication, candidate
discovery, the sealed Kernel and Package assembly that one allowed question
retains exact Block/Evidence lineage while unauthorized narrowing returns a
content-free empty Package. Unit conformance additionally proves closed schemas,
trusted-field rejection before HTTP, secret/redirect discipline, fresh request
ids and missing-environment refusal. `Continue`, `OpenCitation`, generation,
effects, group/public delivery, remote/shared or multi-user/tenant MCP, prompts,
resources and all MCP-side `EgressGrant` handling remain `NOT_ACTIVE`.

### Co-resident local Evidence Console

| ADR | Activates |
|---|---|
| [0090](./docs/decisions/0090-admit-a-co-resident-local-evidence-console.md), [0093](./docs/decisions/0093-activate-leased-rich-markdown-and-revision-link-graph.md) | Explicitly authenticated server-rendered loopback UI, private File citation reopening, and separately Control-authorized source/import/Article jobs through schema-hidden typed HTTP carriers while OpenAPI v0 remains frozen; imports satisfying ADR-0093's qualifying v1-refusal or accepted-rich-link gate and whole-document rich-v3 acceptance check receive a content-free handoff to the activated File scan and exact leased worker path |

Feedback persists through the current Runtime identity and exact ContextRun
binding, with no Control or release-publication authority. Numeric Hit Test scores
remain unavailable because the public rank-free `ContextPackage` intentionally
contains no pre-authorization rank evidence.
### Bounded local Release promotion

| ADR | Activates |
|---|---|
| [0073](./docs/decisions/0073-compose-explicit-release-candidates-from-current-corpus.md) | Exact current-corpus candidate assembly and explicit four-gate promotion through the existing sole Learning publication owner |

Real-PostgreSQL fixture evidence proves that every active Revision is selected,
the dogfood profile becomes active, the API process then reports
`runtime_delivery: ACTIVE`, unchanged reruns preserve the same immutable
manifest while advancing its audited generation, empty corpora fail closed,
and Control/release credentials are not interchangeable. This does not claim a
maintainer-private corpus run. Rollback, autonomous candidate generation,
automatic post-scan promotion, Curation publication, production grant
administration, and a network operator surface remain `NOT_ACTIVE`.

## Activation ledger

Each accepted ADR below activated a bounded, separately proven capability.
Follow the ADR for its exact evidence boundary.

### Security foundation and the sealed Runtime

| ADR | Activates |
|---|---|
| [0030](./docs/decisions/0030-bound-ticket-audiences.md) | Bound the first ticket audiences to synthetic effects |
| [0031](./docs/decisions/0031-persist-authorized-context-run-lineage.md) | Persist authorized-only ContextRun lineage before delivery |
| [0032](./docs/decisions/0032-bind-materialized-fields-to-membership-projection-rights.md) | Bind materialized fields to current Membership projection rights |
| [0033](./docs/decisions/0033-promote-organization-releases-through-one-learning-owner.md) | Promote Organization releases through one Learning owner |
| [0034](./docs/decisions/0034-execute-the-m0-security-veto-from-registered-evidence.md) | Execute the M0 security veto from registered evidence |

### File Provider (Provider #1) — Supply loop

| ADR | Activates |
|---|---|
| [0035](./docs/decisions/0035-register-file-sources-through-context-control.md) | Register File sources through one trusted ContextControl transaction |
| [0036](./docs/decisions/0036-compile-narrow-markdown-deterministically.md) | Compile the first Markdown shape from canonical bytes |
| [0037](./docs/decisions/0037-publish-first-file-through-exact-worker-lease.md) | Publish the first File through an exact WorkerLease |
| [0038](./docs/decisions/0038-compile-and-publish-structural-markdown.md) | Compile and publish structural Markdown units |
| [0039](./docs/decisions/0039-deduplicate-unchanged-file-acquisitions.md) | Deduplicate unchanged File acquisitions before publication |
| [0040](./docs/decisions/0040-stage-and-atomically-activate-file-replacements.md) | Stage and atomically activate File replacements |
| [0041](./docs/decisions/0041-recover-file-publication-by-durable-boundary.md) | Recover File publication by durable boundary |
| [0042](./docs/decisions/0042-tombstone-file-resources-before-cleanup.md) | Tombstone File Resources before cleanup |
| [0043](./docs/decisions/0043-separate-file-acquisition-progress-from-publication-progress.md) | Separate File acquisition progress from publication progress |
| [0044](./docs/decisions/0044-disable-file-sources-before-cleanup.md) | Disable File sources before cleanup |
| [0054](./docs/decisions/0054-acknowledge-file-change-pages-before-cursor-advance.md) | Acknowledge File change pages before cursor advance |
| [0055](./docs/decisions/0055-schedule-accepted-file-observations-explicitly.md) | Schedule accepted File observations explicitly |
| [0056](./docs/decisions/0056-detect-file-deletions-without-tombstone-authority.md) | Detect File deletions without tombstone authority |
| [0057](./docs/decisions/0057-execute-current-file-deletes-through-tombstone-authority.md) | Execute current File deletes through tombstone authority |
| [0058](./docs/decisions/0058-schedule-only-upserts-from-mixed-file-pages.md) | Schedule only upserts from mixed File pages |
| [0059](./docs/decisions/0059-dispatch-scheduled-file-imports-through-exact-leases.md) | Dispatch scheduled File imports through exact leases |
| [0060](./docs/decisions/0060-reclaim-expired-file-imports-with-bounded-retries.md) | Reclaim expired File imports with bounded retries |
| [0065](./docs/decisions/0065-recurse-file-discovery-with-anchored-descriptors.md) | Recurse File discovery through anchored descriptors under one bounded byte ceiling |
| [0066](./docs/decisions/0066-embed-fragments-before-publication.md) | Embed newly published Fragments before activation through an explicit provider |
| [0070](./docs/decisions/0070-activate-file-change-feed-from-registration.md) | Advance an exact registered v1 or import-enabled v2 File source to the existing immutable v3 change-feed manifest |
| [0071](./docs/decisions/0071-compose-bounded-file-scan-cycles.md) | Compose a bounded local File scan from operation-exact accept and schedule calls with checkpoint idempotence |
| [0072](./docs/decisions/0072-report-file-source-status-with-closed-refusals.md) | Report content-free File freshness and current unpublished paths using closed retained refusal categories |
| [0073](./docs/decisions/0073-compose-explicit-release-candidates-from-current-corpus.md) | Compose and explicitly promote the exact current dogfood File corpus through ContextLearning |
| [0086](./docs/decisions/0086-report-worker-batches-and-compose-source-wide-cycles.md) | Emit privacy-shaped worker batch progress and compose source-wide scan/status from existing exact Control calls |
| [0091](./docs/decisions/0091-reconcile-connector-acl-freshness-at-acceptance.md) | Admit the credential-free Feishu Docs twin through the leased connector runner, preserve lease-reconciled ACL freshness provenance, and atomically isolate or fix one Article ACL with a database Policy Epoch advance |
| [0092](./docs/decisions/0092-authorize-feishu-subjects-and-bound-mirrored-freshness.md) | Recompute Feishu subject grants from engine-owned mappings, bind artifacts to exact Articles, and expire Mirrored Feishu evidence under one closed five-minute Runtime profile |
| [0093](./docs/decisions/0093-activate-leased-rich-markdown-and-revision-link-graph.md) | Activate lease-selected rich Markdown v3 publication and one bounded authorized Revision-graph hop |

ADR-0065 extends the active File Provider boundary from a flat root to
deterministic recursive discovery of canonical nested Markdown paths. Each
directory hop and final read stays anchored to the registered root with
no-follow descriptors and stable before/after identity checks; symlinks,
non-regular targets, path escapes, unstable scans, and roots beyond the retained
10,000-path baseline bound fail closed. Worker reads use a server-owned bounded
byte ceiling (1 MiB by default, configurable only within 1–64 MiB), and the real
PostgreSQL evidence covers nested publication plus mixed flat/nested replay.

This does **not** activate provider polling/watchers, a full-resync mechanism,
new delete authority, or any non-Markdown carrier.

ADR-0066 adds one Supply-owned embedding seam to File publication. New Fragment
rows receive validated 384-dimensional float32 vectors in the same durable
publication boundary before activation; unchanged acquisitions and recovery
past preparation do not call the provider again. The partial HNSW index is a
future candidate-discovery implementation detail and has no authorization role.

ADR-0102 now activates the bounded local Qwen File publication and query carrier.
It binds the exact provider profile to Release and Fragment lineage, refuses a
mixed active corpus before ANN, debits the internal query budget, and permits
activation only through ADR-0073 promotion. It does **not** activate an
external/network embedding provider, production authentication, or a public
cumulative-accounting contract; those remain blocked pending issue #217.

ADR-0071 composes the opt-in ADR-0069 local operator process to drive one
bounded File scan over an explicitly configured anchored root, accept every new
provider page, schedule only changed upserts, reconcile accepted current-scan
upsert pages missing durable jobs, and hand those jobs to the existing
autonomous worker. Real-PostgreSQL fixture evidence covers exact unchanged
replay, interrupted scheduling recovery, one-note addition, delete observation
without delete execution, and
384-dimensional Fragment publication. This does not claim that the maintainer's
private corpus has run; it activates no watcher, alternate publisher, new
tombstone authority, or network operation.

ADR-0072 adds a Control-only `status` read for one registered File source. It
reports acquisition/publication progress, the current scan and baseline,
active Resource count, explicit never-succeeded or database-observed success
age, and current observed unpublished paths with one closed compiler refusal
category. Real-PostgreSQL fixture evidence covers unsupported Markdown,
successful publication, and removal on the next complete scan. The worker's
caller-visible refusal remains generic; no note content or compiler diagnostic
is retained. This status is not Runtime authority and activates no
`stale_evidence`, repair, retry, metrics, HTTP, watcher, or grammar surface.

ADR-0086 adds tracked machine-readable progress for contiguous autonomous File
dispatch batches, with opaque per-job attribution, aggregate counters, bounded
in-flight observations, closed refusal categories, and idle-poll suppression.
It also composes `scan-all` and source-wide `status` from one exact existing
Source discovery call plus the independently consumed per-Source operations.
Source discovery intentionally adds active-Source enumeration for a
`READ_SOURCE` operator. `scan-all` reports a closed source-local refusal and
continues later independently durable cycles; bare `status` requires
`READ_SOURCE` before its per-Source `READ_SOURCE_PROGRESS` calls.
It adds no `ControlOperation`, does not widen or reuse a WorkerLease, preserves
all credential planes, and never promotes or activates a Release implicitly.

ADR-0091 and ADR-0092 add an offline-only Feishu Docs Supply carrier. The deterministic twin
runs in the ContextEngine-owned connector-runner subprocess, emits accepted
ChangePages for synthetic documents and deletes, and carries Mirrored Article ACL
observations with nested-group flattening. The database recomputes local identity
and group grants from engine-owned mappings, binds each artifact to its exact
Article, isolates unresolved, failed, or forged claims, and commits every accepted
observation with one Policy Epoch advance. Runtime refuses Feishu mirrors more than
five minutes old using the trusted request transaction time without rebuilding the
index. The exact Room-A oracle suite and real-PostgreSQL tests require no network or
credentials.

This does **not** activate a live Feishu client, tenant credentials, external
network calls, production subject-mapping administration, or Feishu delivery.
Those surfaces remain `NOT_ACTIVE`.

ADR-0093 activates the exact File-import worker's rich Markdown v3 compiler
subprocess and immutable content-free Revision link edges. Runtime follows one
outgoing or backlink hop only from authorized main-path projections, verifies
same-Article/current-Revision lineage before inheritance, re-authorizes every
cross-Article candidate through the unchanged Kernel, and admits only relevant
authorized neighbours into the existing ranking competition. Registered
PostgreSQL and generated-SDK evidence proves a denied neighbour leaves no
tenant-visible or retained decision trace. Recursive graph traversal,
historical edge backfill, external-URI expansion, and graph-carried authority
remain `NOT_ACTIVE`.

### Wire contract, SDK, and trusted delivery

| ADR | Activates |
|---|---|
| [0045](./docs/decisions/0045-redeem-private-delivery-evidence-at-ingress.md) | Redeem private delivery evidence at ingress |
| [0046](./docs/decisions/0046-bind-egress-to-one-exact-package-hop.md) | Bind egress to one exact Package hop |
| [0047](./docs/decisions/0047-freeze-openapi-v0-through-one-runtime-path.md) | Freeze OpenAPI v0 through one sealed Runtime path |
| [0048](./docs/decisions/0048-generate-typescript-sdk-behind-a-closed-facade.md) | Generate the TypeScript SDK behind a closed facade |
| [0049](./docs/decisions/0049-prepare-one-exact-private-effect.md) | Prepare one exact private effect before Sender |
| [0050](./docs/decisions/0050-perform-one-exact-private-effect.md) | Perform one exact private effect under one provider attempt |
| [0051](./docs/decisions/0051-reauthorize-opaque-citation-opens.md) | Reauthorize every opaque citation open |
| [0052](./docs/decisions/0052-gate-model-generation-by-package.md) | Gate model generation by one authorized Package |
| [0053](./docs/decisions/0053-compose-one-private-bot-delivery.md) | Compose one private File-backed Bot delivery |

The complete, current list of accepted decisions — including everything before
ADR-0030 — is the [ADR index](./docs/decisions/README.md).

## Boundary notes

These record the exact scope of the foundational M0 proofs, including what each
one explicitly does **not** claim.

### Database and tenant isolation

The database harness proves the `compose.yaml`-pinned PostgreSQL 17 + pgvector
topology, role isolation, migrations, and connection-pool cleanup, plus a
transaction-scoped tenant context built from Organization + current
Membership-backed `UserActor` + `organization_record`, with composite ownership
and FORCE RLS.

It does **not** claim durable Principal/Agent grants, real ACLs,
production-grade content authorization, or production ContextPackage delivery.

### Issue #12 — fail-closed EffectiveScope

An injected conformance composition proves the current Membership gate and a
synthetic `EffectiveScope` on a fail-closed, monotonically non-expanding path.

### Issue #13 — hostile candidate index

A synthetic exact-authorized Evidence path proves that a hostile
`CandidateIndex` can deliver exactly one synthetic authorized Evidence block,
and only by passing through FORCE RLS in the same PostgreSQL transaction, an
exact `EffectiveScope`, and the sealed `AuthorizationKernel`.

### Issue #14 — convergent empty packages

A paired Runtime/HTTP gate proves that cross-Organization, same-Organization
denied, and nonexistent-Candidate probes all converge on the same tenant-safe
empty Package.

HTTP status, closed product headers, Package body, and Runtime domain outcome
are identical after normalizing only server-authored per-resolve refs and
timestamps plus the `packageDigest` necessarily derived from them; each
un-normalized Package still verifies its own digest first.

**This gate does not measure or claim timing equivalence.**

### Issue #15 — V0 Policy Epoch

An internal, least-privilege, non-owner Control transaction atomically revokes
seeded access and advances the Organization-level V0 Policy Epoch. A sealed
`Acquire` re-checks the current epoch before delivery, so an identical query,
`CandidateRef`, and persisted Fragment return zero Evidence on the first
post-revocation request, with Organization B unaffected.

This test capability is **not** a production grant or admin workflow. Policy
Epoch V0 does not activate UI or external admin, access-mutation
`DecisionAudit`, outbox, cleanup, or real `Continue` / `OpenCitation`.

### Issue #16 — closed capability gate

The public Runtime wire is fixed to the closed `Acquire | Continue |
OpenCitation` union. A server-owned `RuntimeCapabilityGate` activates the M0
rejection path: known-but-uncarried `Continue`, `OpenCitation`, federated
discovery, and source-native authorization each return a generic domain-level
`request_not_available` or `citation_not_available` **before any
Provider/index/source-content I/O**. Unknown variants or caller-declared
capability remain a generic 422.

This proves deterministic refusal only. It does not mean continuation,
citation, federated or source-native Providers, or File publication are
implemented. The restricted in-process audit retains only the
`UNSUPPORTED_CAPABILITY` category.

### Issue #17 — WorkerLease (persistent no-op sub-carrier)

Adds Organization-owned `service_principal` and `worker_noop_job` tables plus a
canonical HMAC-SHA256 WorkerLease with an explicit versioned keyring.

The Control issuer signs leases using database transaction time and a
server-owned bounded TTL. If a prior lease has expired by database time, a new
time and nonce allow atomic takeover — recovering the "transaction committed
but token never delivered" crash window, after which the old token has zero
effect. The worker seam must verify signature, Organization, job, and validity
against its own configured registered ServicePrincipal identity and clock
*before* opening a database transaction. The durable receiver is fixed to
`supply.noop` + `context-engine-worker` + `noop.complete` and accepts no
caller override.

The worker holds no direct `SELECT` on the two tenant tables and no `UPDATE` on
the job. A dedicated non-login definer function is the only durable read/write
boundary, performing one conditional update under FORCE RLS keyed on database
current time, key version, nonce digest, and issued-at/expiry. A valid lease's
effect count can only go from 0 to 1; wrong-org/job/audience, tampering,
expiry, disabled ServicePrincipal, replay, and concurrent losers all keep zero
additional effect.

This bounded proof excludes Source/Resource/Revision, Policy Epoch, end-user
delivery audience, idempotency/generation, outbox, and the production worker
loop. It does **not** publish or claim a complete canonical `ServiceActor` —
its source, allowed-set, and Policy Epoch do not exist yet — and keeps the full
`ACCEPT-008` fixture at `future/fail_closed`.

### Issue #18 — separated ticket planes (ADR-0030)

Adds canonical HMAC-SHA256 `ContextAccessTicket` and `ActionTicket` protocols.
Both use the same validated `AuthenticatedInvocation` / `TrustedDeliveryContext`
identity chain and explicit versioned key configuration, but differ in every
other dimension:

| | Read protocol | Action protocol |
|---|---|---|
| Domain | `context-engine.context-access-ticket` | `context-engine.action-ticket` |
| Signed prefix | `CE-ContextAccessTicket` | `CE-ActionTicket` |
| Fixed operation | `synthetic.provider.read` | `synthetic.channel.noop` |
| Derived audience | `context-read:<provider>` | `im-send:<channel>` |

Issuer and handler are bound by trusted configuration to one
Organization/target. Agent and purpose accept no bare strings, and tokens
expose no public value constructor. Two independent deserializers validate
signature, domain/type, fixed operation, and schema before constructing a
nominal type; the handler then checks full identity, purpose, bounded expiry,
nonce, and key version, and re-checks the Organization V0 Policy Epoch last,
before two independent synthetic effects.

Cross-plane deserialize/pass using the same key, wrong target/Organization,
identity or audience mismatch, tampering, overlong or expired lifetime,
authority failure, and a committed epoch bump all return one non-enumerating
unavailable result with **zero** rejected effect.

This bounded proof does not activate production Provider discovery or
projection, source credentials, Sender/IM, `ActionPlane.prepare`/`perform`,
payload/destination/approval/idempotency, `DeliveryAttempt`, durable
one-shot/replay/concurrency, stored receipts, or reconciliation. The full
`ACCEPT-012` carrier remains `NOT_ACTIVE` under this activation.

### Issue #19 — authorized-only ContextRun lineage (ADR-0031)

Every successful empty or exact-authorized Package now commits, before
returning and inside the retained current-`UserActor` transaction, one
same-Organization, final, authorized-only `ContextRun`. Its public
`decisionRef` resolves only through a dedicated non-owner security operator,
an exact Organization, and an explicit trusted authorization seam.

An empty package additionally writes a restricted `DecisionAudit` containing
only Organization/run/decision, PolicySnapshot/epoch, the
`no_authorized_evidence` category, and time. It stores **no** raw query, and no
denied Candidate/Fragment/Resource body, ID, name, reason, or count.

Queries are retained only as an Organization-bound, versioned HMAC-SHA256
digest. Packages expose and persist a verifiable versioned canonical SHA-256
digest, with retention mode fixed to `digest_only` — full Packages are not
retained. Unauthenticated or injection failures are not a ContextRun.

This bounded `TRACE-REDACTION-012` activation does not extend to logs, metrics,
debug, evaluation, or Learning; nor to `Continue` / `OpenCitation`, feedback,
full retrieval traces, or production operator identity. The default
application's production authentication remains reject-all.

### Issue #71 — private File-backed delivery twin

Activates a complete private-chat, File-backed, deterministic-twin carrier: an
independent TypeScript Bot process reaches the Runtime **only** through the
installed generated SDK; the controlled model consumes exactly one current
Package; placeholder and final/follow-up messages each go through
`ActionPlane.prepare` + `perform`; and only digest/ref-form `DeliveryReceipt`
plus a restricted audit are retained.

A bounded File import job runs through the same `context-engine-worker
--run-file-job` process entry point, consuming one exact signed FileImport
WorkerLease. It requires an explicit worker credential, a registered
ServicePrincipal, a logical File root, and a job binding; it exits after one
terminal state and introduces no fourth process type.

At the issue #71 activation boundary, live Feishu, real models and Senders,
group chat, compensating deletes, `Continue`, and MCP were `NOT_ACTIVE`.
ADR-0103 later activates only the local MCP carrier recorded above.

### Issue #133 — bounded private Feishu delivery conformance

Activates a production-shaped but network-free replacement for the two
provider-facing twins inside the existing `BotDelivery + ActionPlane` process.
The versioned event verifier binds one configured application, provider tenant,
Organization, consumer, private destination, current asker mapping, exact event
kind/request/purpose, HMAC envelope, trusted lifetime, and one-shot event ID
before it can mint a nominal turn or select one opaque `DeliveryEvidenceRef`.
The exact Sender twin remains callable only by `ActionPlane.perform`; it observes
the operation, destination, payload digest, ticket-derived provider idempotency,
and durable provider attempt without making a network call.

This activation is **twin-bounded conformance**, not live Feishu. Feishu
credentials and network calls, real model providers, group/public audience,
`Continue`, compensation/delete, automatic reconciliation, multiple Feishu
tenants or Bot instances, pi, issue #127 ingestion, and every MCP carrier except
ADR-0103's local stdio `Acquire` translator remain `NOT_ACTIVE`. Channel
`EgressGrant` remains preflight-only and cannot substitute for trusted identity
or an `ActionTicket`; the generated-answer flow uses no channel grant.

### HTTP exact-authorized Evidence tracer

The conformance composition for the resolve route can inject an authenticator
that maps an opaque credential to verified transport facts, a trusted authority
that issues request-bound nominal proof for a registered Organization, and an
authority that validates current Membership inside a single PostgreSQL
transaction and issues a lifetime-bound `UserActor` proof. That transaction is
held open until the sealed Runtime and ContextPackage construction complete.

**No `200` is reachable from the production default.** `create_app()` selects
`RejectingAuthenticator`, `RejectingOrganizationAuthority`, and
`RejectingMembershipAuthority` whenever no authorities are injected, so every
credential is rejected before an `Acquire` can reach a successful response. Do
not try to reproduce the results below against a default-built application —
they exist only under an explicitly injected composition.

Within that injected conformance composition there are two variants:

| Variant | Result |
|---|---|
| No candidate injection | A valid `Acquire` returns `200 resolved` with an evidence-free ContextPackage |
| Explicit synthetic candidate injection | A content-free `CandidateRef` passes through the RLS locator, exact `EffectiveScope`, body projection, and the sealed `AuthorizationKernel` in that same transaction, returning exactly one authorized Evidence block |

Invalid Membership returns a generic 401; an unavailable database authority
returns a generic 503. **Neither calls any content system.**

**Request shape.** The body is a closed `kind` union. `Acquire` permits
`need.query`, an optional bounded `packageBudget`, and optional
`requestNarrowing`. `Continue` permits an opaque `continuationToken` and an
optionally smaller `packageBudget`. `OpenCitation` permits only an opaque
`citationOpenRef`. All ref/token lengths and collection sizes are limited by the
active profile. Unknown fields at every level, duplicate JSON keys, and
duplicate singleton security/transport headers all fail closed; pre-auth body
bytes and JSON nesting are limited by the versioned profile in
[`adapters/http/transport.py`](./adapters/http/transport.py).

**Response shape.** Malformed JSON or media type, authentication failure, and
closed-schema failure use the generic 400, 401, and 422 responses recorded in
OpenAPI, and never echo tenant, Principal, Membership, or injected fields.
Purpose comes only from server-side route policy. The returned
`organizationRef` is a freshly generated package-scoped opaque reference and
cannot be used as trusted tenant input on a later request. An empty package has
empty blocks, evidence, and gaps, with coverage `no_authorized_evidence`, and
makes zero Provider/index/source-content calls.

The content tracer holds zero body bytes, zero Evidence refs, and zero external
effects for denied same-Organization and cross-Organization candidates, while
maintaining a one-to-one Evidence reference closure and full lineage for
authorized blocks.

**Deterministic authorities and the real-PostgreSQL seeded composition belong to
the test composition only.** Production OAuth/JWT, durable Principal/Agent grant
authority, real Source/Resource ACLs, general retrieval, and continuation are
not part of this activated tracer.

## Evidence and reporting

Golden evaluation validates and locks schema-v1 sets, computes deterministic
retrieval/citation metrics plus attributable answer metrics, evaluates slice
floors, and renders report machinery. One run executor privately owns the
security-observation constructor: `context-engine-eval execute` replays every
golden query through the recorded `dogfood-loopback-resolve-acquire-v1` seam and
derives observed Evidence, refusal, and typed security events from the delivered
ContextPackages, so an executed clean run reaches `observed_clean` and one
observed violation forces whole-report `FAIL` at any score. The executor takes
no caller-supplied transport, callback, or counter, and holds no publication
authority. File-only reports keep their metrics and still emit `REFUSED` with
`no_run_executor_security_observation`. The public-subset maintainer
authority is a preparatory privacy check with no promotion effect.

The private corpus is recoverable rather than merely stored once. A second
configured durable root outside every worktree holds immutable staged-then-
renamed snapshots carrying per-file and per-snapshot digests; verification
refuses truncation, corruption, missing or unexpected content, and any file
readable beyond its owner, and recovery refuses both an unverified snapshot and
a non-empty destination. Expectation lineage that a Release promotion left
unresolvable is reported as `stale_lineage` and refuses the report; it is never
scored as a retrieval miss. This is proven on synthetic corpora and one
executed recovery drill — the real maintainer corpus is still pending delivery
(#103), so no claim is made about it.

`make security-gate` discovers and executes only registered M0 security
evidence, cross-checks the live PostgreSQL RLS inventory, and writes
machine-readable raw evidence plus an independent release-gate report into the
git-ignored `.context-engine/security-gate/` directory. CI retains both as build
artifacts.

Security is an independent veto gate. Reliability, Quality, and Budget are not
yet in M0 scope and are explicitly recorded as `not-evaluated`, so the report
emits only an `m0SecurityDecision` — a passing security gate is never reported
as an overall release or promotion PASS.

Beyond the pinned-commit evidence for the four admitted repositories Dify,
RAGFlow, MaxKB, and Onyx in the
[versioned five-repository baseline](./docs/research/2026-08-02-five-public-repositories-evidence.md),
the dynamic evidence that exists today is
the `compose.yaml`-pinned PostgreSQL + pgvector harness and RLS evidence for the
first Organization-owned representative table. Dynamic evidence for the complete
domain schema, ActorContext, filtered ANN, and Feishu capability is still
outstanding — which is why this evidence slice is not described as a complete
product authorization capability.

The same versioned baseline carries an OpenViking candidate packet, but this
STATUS does not cite OpenViking as authority while issue #205 remains open.
