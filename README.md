# ContextEngine

[![CI](https://github.com/stone16/context-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/stone16/context-engine/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](./LICENSE)
[![Status](https://img.shields.io/badge/status-pre--release-orange.svg)](./STATUS.md)

**A permission-aware context delivery engine.** Connect your team's knowledge
sources upstream; deliver **authorized, evidence-backed, budget-bounded**
ContextPackages to agents and chat bots downstream.

[简体中文](./README.zh-CN.md)

---

Most knowledge-base products answer *how do I store and search this?* Most RAG
toolchains answer *how do I find the nearest chunk?* ContextEngine exists
because the two questions that actually block shipping a trustworthy assistant
inside a company are different ones:

## 1. What is this audience allowed to know, right now?

Retrieval alone cannot answer that. In ContextEngine the index never returns
deliverable text — it returns a `CandidateRef`. Every candidate must pass
through a sealed `AuthorizationKernel` that performs exact authorization and
field projection before *anything* content-bearing happens. Hydration,
reranking, relevance models, and packaging all run on `AuthorizedProjection`
only. Every parent or neighbor expansion is re-authorized item by item.

Source ACL evidence is explicitly classified as `Live`, `Mirrored`, or `Weak`.
`Weak` is only for sources that genuinely lack fine-grained ACLs — it is never
a fallback when a `Live` or `Mirrored` check fails. That case fails closed.

## 2. Who keeps the knowledge base organized?

Organization cost is the largest hidden cost of any team knowledge base.
ContextEngine assigns the automatable part to agents — semantic
deduplication, staleness marking, terminology capture — while humans keep the
audit. Every AI-produced annotation is proposed, confirmed, then published
atomically as a separate immutable `CurationSnapshot`. Published content
revisions are never mutated in place.

## Project status

> **Pre-release. Not usable in production, and not trying to look like it is.**

ContextEngine is being built milestone by milestone, and each capability is
activated only when executable evidence proves it. Capabilities that have not
been proven are labeled `NOT_ACTIVE` rather than quietly stubbed — including in
the running service's own `/health` response.

| Area | State |
|---|---|
| Real PostgreSQL 17 + pgvector harness, role separation, FORCE RLS | Active |
| Organization / Membership / `UserActor` tenant transaction | Active |
| Sealed `ContextRuntime.resolve` returning tenant-safe ContextPackage | Active |
| Exact-authorized Evidence tracer over a hostile candidate index | Active |
| OpenAPI v0 wire contract + generated TypeScript SDK + breaking-change gate | Active |
| Private File-backed bot delivery flow (deterministic twin) | Active |
| Autonomous File import dispatch + bounded expired-lease reclaim | Active |
| Loopback single-Membership File dogfood `Acquire` | Active when explicitly configured |
| Production authentication (OAuth/JWT) | `NOT_ACTIVE` |
| Real source ACLs, general content retrieval, `Continue` / `OpenCitation` | `NOT_ACTIVE` |
| Live Feishu / Slack / Google Docs connectors, group chat | `NOT_ACTIVE` |

**[→ Full capability ledger with per-issue evidence boundaries (STATUS.md)](./STATUS.md)**

The roadmap and milestone exit criteria live in [PLAN.md](./PLAN.md).

## Quick start

### Prerequisites

| Requirement | Where the version comes from | Why |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | — | Dependency resolution, pinned by `uv.lock` |
| [Python](https://www.python.org/) | `requires-python` in [`pyproject.toml`](./pyproject.toml) — `uv sync` provisions a matching interpreter for you | Engine, adapters, worker |
| [Node.js](https://nodejs.org/) | [`sdk/typescript/.node-version`](./sdk/typescript/.node-version) — `nvm use`, `fnm use`, and `asdf` read it automatically | TypeScript SDK, ActionPlane, BotDelivery |
| Docker (with Compose) | Service versions are pinned in [`compose.yaml`](./compose.yaml) | Real PostgreSQL + pgvector test harness |

Every version above is declared in a checked-in file, so none of them are
repeated here — install the tool, and let it read the repository.

### Install and verify

```bash
make install
```

`make install` syncs the locked Python environment **and** runs `npm ci` for the
three TypeScript workspaces (`sdk/`, `action_plane/`, `bot_delivery/`). Node is
not optional.

Run the same gate CI runs, from a clean checkout:

```bash
make install && make db-up && make check && make db-down
```

### Run the API

Bind an address explicitly so the example below is self-contained — run
`context-engine-api --help` for the defaults and the full flag set
(`--host`, `--port`, `--log-level`):

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

`runtime_delivery: NOT_ACTIVE` is expected and correct: the default application
rejects every credential and performs zero content I/O. The public wire contract
is `POST /v0/resolve`, frozen in [`openapi/v0/openapi.json`](./openapi/v0/openapi.json).

### Run the bounded dogfood API

The only served content-bearing composition is a local, loopback-only dogfood
carrier. It is an explicit opt-in and is not production authentication. First
seed one Organization, User, and current Membership with the configured
migrator connection:

```bash
uv run context-engine-dogfood-seed \
  --organization-id "$CONTEXT_ENGINE_DOGFOOD_ORGANIZATION_ID" \
  --user-id "$CONTEXT_ENGINE_DOGFOOD_USER_ID" \
  --membership-id "$CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_ID"
```

Configure the API with the Runtime database source and these dogfood settings:

```text
CONTEXT_ENGINE_API_COMPOSITION=dogfood-local-v1
CONTEXT_ENGINE_DOGFOOD_SECRET
CONTEXT_ENGINE_DOGFOOD_ORGANIZATION_ID
CONTEXT_ENGINE_DOGFOOD_USER_ID
CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_ID
CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_VERSION
CONTEXT_ENGINE_DOGFOOD_PRINCIPAL_REF
CONTEXT_ENGINE_DOGFOOD_AGENT_VERSION_REF
CONTEXT_ENGINE_DOGFOOD_APPLICATION_REF
CONTEXT_ENGINE_DOGFOOD_AUTHENTICATION_BINDING_REF
CONTEXT_ENGINE_DOGFOOD_EMBEDDING_PROVIDER=deterministic-twin-v1
```

Before activation, freshly reimport the File corpus with the Supply worker's
network-free `twin` embedding mode and use the explicit release procedure
below. The active Release binds the deterministic model and contextual-fragment
input profile; a mismatch fails composition. Then run the API with an explicit
loopback host. A valid composition reports `runtime_delivery: ACTIVE`; missing
or partial configuration fails closed. The dogfood secret must come from one
local secret source and must never be committed or printed.

External query embeddings, production or multi-user authentication, remote
network exposure, group/public delivery, dogfood `OpenCitation`, `Continue`, hybrid retrieval, and
non-File providers remain `NOT_ACTIVE`; see
[ADR-0068](./docs/decisions/0068-activate-loopback-dogfood-runtime.md).

Once the bounded API is running, the maintainer caller and Quality runner are
documented in [`eval/README.md`](./eval/README.md). They call only the frozen
resolve HTTP operation; the evaluation report remains separate from the
security release gate.

### Run the worker

The Supply worker is a separate process from the API, with one entry point and
four modes:

```bash
uv run context-engine-worker --test-mode           # deterministic no-op lifecycle
uv run context-engine-worker --run-file-job        # one exact signed File import job
uv run context-engine-worker --dispatch-file-once  # one deterministic dispatch cycle
uv run context-engine-worker --dispatch-files      # long-running dispatch loop
```

`--test-mode` reports `job_behavior: NOT_ACTIVE`, meaning the default CLI has no
production signing key source, queue loop, or real ingestion handler configured.

`--dispatch-files` is the production long-running entry: it polls on a
server-fixed one-second interval when there is no work, and exits on `SIGTERM`
or `SIGINT`. It emits its existing readiness line, then schema-versioned batch
progress while work is active. Repeated idle polls emit nothing; the first idle
observation after an active drain emits one aggregate summary. Progress records
validate against
[`worker-batch-progress-v1.schema.json`](./docs/contracts/worker-batch-progress-v1.schema.json).

All dispatch modes read **only** a role-specific scheduler, worker URL,
WorkerLease signing key, the server-side JSON root registry
(`CONTEXT_ENGINE_WORKER_FILE_ROOTS_JSON`), and an optional bounded per-file byte
ceiling (`CONTEXT_ENGINE_WORKER_MAX_FILE_BYTES`, default 1 MiB, accepted only
within 1–64 MiB). They also require an explicit embedding provider mode and the
schema-pinned dimension (`CONTEXT_ENGINE_WORKER_EMBEDDING_PROVIDER` and
`CONTEXT_ENGINE_WORKER_EMBEDDING_DIMENSION`). CI uses the network-free `twin`
mode. Real deployments select `external` and supply endpoint, model, and API key
only through the corresponding `CONTEXT_ENGINE_WORKER_EMBEDDING_*` environment
variables, including a required batch size bounded to 1–256 inputs per request.
Markdown files are discovered recursively. **A caller may not
supply Organization, Source, job, or token** — that is the point of the boundary.
Single-cycle output remains limited to `dispatched` / `no_work` / `refused`.
Long-running batch output carries only counts, phase, opaque batch/job refs,
and the closed `file_import_refused` or `worker_lease_refused` category. It has
no path, title, excerpt, principal, Organization, Source, lease, or credential
field.

Lease validation uses the worker's PostgreSQL clock, staying in the same time
domain as database-issued timestamps rather than depending on worker host clock
alignment. Unavailable worker infrastructure terminates dispatch instead of
continuing to claim and strand later jobs. File or content failures return
`refused` and continue scheduling only once that job is durably terminal-failed
or the current authority rejects that exact failure transition.

Activation boundaries for File dispatch, reclaim, and delete execution are
recorded in [STATUS.md](./STATUS.md).

### Scan a local File source

The local operator can run one bounded File acquisition cycle and hand its
scheduled upserts to the existing worker. This remains an explicitly configured
local process; it adds no HTTP operation, polling daemon, publication path, or
delete authority.

Load the generated harness database environment first, then configure the
local operator composition described by
[ADR-0069](./docs/decisions/0069-admit-an-explicit-local-operator-composition.md).
The Control operation allowlist for this workflow is:

```text
register_source,read_source,read_source_progress,activate_file_change_feed,activate_file_delete_observations,accept_file_change_page,schedule_file_change_page
```

The scan and worker share the same server-owned root registry and byte ceiling.
They additionally require one durable File-import receiver, the current private
dogfood audience, and two distinct persistent Ed25519 proof keys:

```text
CONTEXT_ENGINE_WORKER_FILE_ROOTS_JSON
CONTEXT_ENGINE_WORKER_MAX_FILE_BYTES                 # optional
CONTEXT_ENGINE_WORKER_MAX_FILE_CHANGE_BASELINE_SIZE  # optional
CONTEXT_ENGINE_WORKER_FILE_CURATED_SUBTREES_JSON     # optional
CONTEXT_ENGINE_WORKER_SERVICE_PRINCIPAL_ID
CONTEXT_ENGINE_DOGFOOD_PRINCIPAL_REF
CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_ID
CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_VERSION
CONTEXT_ENGINE_FILE_CHANGE_PROVIDER_SIGNING_KEY_HEX
CONTEXT_ENGINE_FILE_CHANGE_CHECKPOINT_SIGNING_KEY_HEX
CONTEXT_ENGINE_WORKER_LEASE_SIGNING_KEY_HEX
```

File scans keep ADR-0065's default limit of 10,000 Markdown paths. An operator
may explicitly set `CONTEXT_ENGINE_WORKER_MAX_FILE_CHANGE_BASELINE_SIZE` to a
positive integer no greater than 15,000; invalid values fail process
configuration. The effective value is signed with provider pages and retained
on durable scan provenance. Crossing it fails closed before a partial baseline
is accepted, and `status` reports the content-free closed condition
`scan_bound_exceeded` plus the effective bound.

The alternative configuration path keeps the default bound and selects a
curated subtree per logical root. The value is JSON from each configured root
reference to one nonempty canonical relative directory, for example:

```text
CONTEXT_ENGINE_WORKER_FILE_CURATED_SUBTREES_JSON={"maintainer-notes":"curated/notes"}
```

The registered root remains the descriptor-anchored read capability. Scan
traversal starts at the selected subtree while keeping full registered-root
relative path identities; switching a whole-root baseline to a selection that
would reinterpret active paths refuses before accepting a page. Absolute
paths, empty components, `.` / `..`, backslashes, and unknown root references
are refused at configuration time. Whole-vault versus curated subtree remains
a maintainer decision. Their synthetic measured costs and the single-command
reproduction are recorded in
[`2026-07-30-file-scan-baseline-measurement.md`](./docs/design/2026-07-30-file-scan-baseline-measurement.md).

Each proof-key value is exactly 32 random bytes encoded as 64 lowercase or
uppercase hexadecimal characters. Keep both in the same local secret source
across process restarts and never print or commit them. They must be distinct
from each other and from the Control, release, dogfood, and worker secrets. The
worker signing key is already required by the explicit local operator
composition and is checked here only to preserve that cross-plane separation.
Seed the receiver together with the dogfood identity (the command is
idempotent for the exact same bindings):

```bash
uv run context-engine-dogfood-seed \
  --organization-id "$CONTEXT_ENGINE_DOGFOOD_ORGANIZATION_ID" \
  --user-id "$CONTEXT_ENGINE_DOGFOOD_USER_ID" \
  --membership-id "$CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_ID" \
  --provision-release-operator-grant \
  --file-import-service-principal-id \
    "$CONTEXT_ENGINE_WORKER_SERVICE_PRINCIPAL_ID"
```

Register the logical root, copy the returned `sourceRef` into
`CONTEXT_ENGINE_FILE_SOURCE_REF`, activate its two existing immutable
capability transitions, and run the cycle:

```bash
uv run context-engine-control register-file-source \
  --organization-id "$CONTEXT_ENGINE_OPERATOR_ORGANIZATION_ID" \
  --display-name "Maintainer notes" \
  --root-ref "maintainer-notes" \
  --idempotency-key "maintainer-notes-v1"

uv run context-engine-control activate-change-feed \
  --organization-id "$CONTEXT_ENGINE_OPERATOR_ORGANIZATION_ID" \
  --source-ref "$CONTEXT_ENGINE_FILE_SOURCE_REF"

uv run context-engine-control activate-delete-observations \
  --organization-id "$CONTEXT_ENGINE_OPERATOR_ORGANIZATION_ID" \
  --source-ref "$CONTEXT_ENGINE_FILE_SOURCE_REF"

uv run context-engine-control scan \
  --organization-id "$CONTEXT_ENGINE_OPERATOR_ORGANIZATION_ID" \
  --source-ref "$CONTEXT_ENGINE_FILE_SOURCE_REF"

uv run context-engine-worker --dispatch-file-once

uv run context-engine-control status \
  --organization-id "$CONTEXT_ENGINE_OPERATOR_ORGANIZATION_ID" \
  --source-ref "$CONTEXT_ENGINE_FILE_SOURCE_REF"
```

Repeat the worker command until it reports `no_work`, or run the existing
long-lived dispatcher. Then promote the exact current active corpus through
the sole Learning publication owner. Promotion additionally requires a
separate 32-byte, hex-encoded evaluation signing key and its positive decimal
version:

```text
CONTEXT_ENGINE_RELEASE_EVALUATION_SIGNING_KEY_VERSION
CONTEXT_ENGINE_RELEASE_EVALUATION_SIGNING_KEY_HEX
```

The key must be retained across invocations, supplied from the same local
secret source as the operator configuration, and distinct from every Control,
release, dogfood, and worker secret. Prepare a reviewed JSON evidence file
outside the repository with exactly the four lowercase `pass` gate statuses,
their SHA-256 evidence digests, the capability-coverage and fixture digests,
and the commands that produced the evidence:

```json
{
  "budget": {"evidenceDigest": "<64 lowercase hex>", "status": "pass"},
  "capabilityCoverageDigest": "<64 lowercase hex>",
  "fixtureDigest": "<64 lowercase hex>",
  "quality": {"evidenceDigest": "<64 lowercase hex>", "status": "pass"},
  "reliability": {"evidenceDigest": "<64 lowercase hex>", "status": "pass"},
  "security": {"evidenceDigest": "<64 lowercase hex>", "status": "pass"},
  "verificationCommands": ["make check"]
}
```

Run the explicit release command, then boot the dogfood API as described
above:

```bash
uv run context-engine-control promote-release \
  --organization-id "$CONTEXT_ENGINE_OPERATOR_ORGANIZATION_ID" \
  --evidence-file "$CONTEXT_ENGINE_RELEASE_EVIDENCE_FILE"
```

The content-free JSON result reports the active generation, exact active
Revision count, dogfood index profile, and manifest reference. An empty corpus,
stale or absent release grant, partial configuration, wrong plane credential,
or failing gate is refused generically. Re-running without a corpus change
advances the same immutable manifest through a new audited generation, matching
the existing `ContextLearning.promote` contract.

`scan` requires that exact delete-observation activation because its complete
durable baseline is also what makes unchanged-path scheduling decisions
idempotent. A v1, v2, or v3 source is refused generically.

The scan prints deterministic, content-free JSON counts.
`advancedCursor` is the accepted durable checkpoint reference; an exact
unchanged replay reports zero accepted changes and scheduled imports while
retaining that already-advanced checkpoint when no accepted page is missing its
schedule. Before returning, scan idempotently schedules any accepted current-
scan upsert page that has no durable acquisition. Those counts are baseline deltas.
Compilation refusals are counted before handoff using the worker's exact active
Markdown configuration, but the worker remains the only publication path and
makes the authoritative terminal transition for each scheduled import.

`status` prints content-free progress and freshness JSON for that source. It
distinguishes a source that has never published, counts active Resources, and
lists current observed unpublished paths using only a closed refusal category.

For recurring work after registration and capability activation, scan and
inspect every active registered File source without copying any `sourceRef`:

```bash
uv run context-engine-control scan-all \
  --organization-id "$CONTEXT_ENGINE_OPERATOR_ORGANIZATION_ID"

uv run context-engine-control status \
  --organization-id "$CONTEXT_ENGINE_OPERATOR_ORGANIZATION_ID"
```

`scan-all` first discovers the Organization's active File sources under one
exact existing `read_source` call, then runs the same operation-exact bounded
scan for each. A source-local refusal is reported with the closed
`operation_refused` category and does not prevent later discovered Sources from
scanning; shared authorization, configuration, or process failure still aborts
the command generically. Bare source-wide `status` also requires one
`read_source` discovery call before it independently consumes one
`read_source_progress` call per discovered source; an operator with only
`read_source_progress` can use `status --source-ref` but cannot use bare
`status`. Neither command changes the six-variable operator opt-in, widens a
WorkerLease, or promotes a Release.
Source-wide status aggregates refusal categories and counts without rendering
the path-bearing single-source refusal projection.
The fresh before/after ceremony audit is recorded in
[`worker batch progress and operator ceremony measurement`](./docs/design/2026-07-30-worker-batch-progress-ceremony-measurement.md).
It does not repair or retry work, expose diagnostics, or participate in Runtime
authorization.

### Development commands

```bash
make install        # sync locked Python env + npm ci for the 3 TS workspaces
make build          # build wheel and sdist
make lint           # Ruff
make typecheck      # strict mypy + TS typecheck
make test           # Python unit tests
make catalog        # static security catalog tests and validation
make smoke          # API / worker process smoke suite
make db-up          # start the pinned PostgreSQL 17 + pgvector harness
make db-down        # stop it, preserving the disposable data volume
make db-reset       # destroy and rebuild only that disposable volume
make integration    # real-PostgreSQL integration/security harness
make security-gate  # executable M0 security veto gate (needs make db-up)
make check          # everything above (needs make db-up)
```

On first start the harness generates random credentials into a git-ignored,
mode-`0600` `.context-engine/database.env`. That file is the single live source
for local migration, runtime, worker, and security-test connection settings, and
it gives each checkout its own Compose project identity so parallel worktrees
never share containers, networks, or volumes. Image and topology versions are
pinned in [`compose.yaml`](./compose.yaml); PostgreSQL binds only one
dynamically chosen `127.0.0.1` port. Migration, runtime, and worker use distinct
roles, and runtime never falls back to migration or bootstrap credentials.

`make security-gate` discovers and runs only registered M0 security evidence,
cross-checks the live PostgreSQL RLS inventory, and writes machine-readable raw
evidence plus an independent release-gate report to
`.context-engine/security-gate/`. Because Reliability, Quality, and Budget are
not yet in M0 scope, that report emits only an `m0SecurityDecision` and
explicitly records the others as `not-evaluated` — a passing security gate is
never reported as an overall release PASS.

## Architecture

### Three loops

| Loop | Responsibility | Key objects |
|---|---|---|
| **Supply** | Sources → trusted candidates: fetch, parse, chunk, index, publish atomically | `ContextSource` / `ContextResource` / `ContextRevision` / `ContextFragment` |
| **Runtime** | Authenticated invocation → ContextPackage: candidates, authorized projection, relevance, packaging | `CandidateRef` / `AuthorizedProjection` / `ContextRun` / `ContextPackage` |
| **Learning** | Authorized-only traces → releasable improvements: golden sets, slice gates, versioned profiles | golden set / `ReleaseManifest` / `CurationSnapshot` |

### The one public online contract

```text
ContextRuntime.resolve(AuthenticatedInvocation, TrustedDeliveryContext,
                       Acquire | Continue | OpenCitation)

  → query understanding + dual recall (FTS + vector, RRF fusion)
  → CandidateRef                        ← carries NO deliverable body
  → AuthorizationKernel                 ← exact authorization + field projection
  → AuthorizedProjection                ← the first content-bearing value
  → post-authorization hydration / rerank
      + small-to-big expansion, each item re-authorized
  → PackageBudget packing + sufficiency signal
  → ContextPackage                      ← citations / purpose / TTL / asOf
```

This is the Runtime's **only** public capability. HTTP is the V1 server ingress;
the TypeScript SDK is a generated HTTP client, not a second transport. MCP stays
`NOT_ACTIVE` until a real caller exists.

`Continue` uses a principal-bound, one-shot, budget-accumulating token.
`OpenCitation` uses an opaque `CitationOpenRef` that carries no authority of its
own — every open re-authenticates and re-authorizes.

### Repository layout

```text
engine/            The sealed core — no HTTP, no vendor SDKs
  runtime/           resolve() orchestration, AuthorizationKernel, tickets,
                     budget, provenance, ContextRun, policy epoch
  supply/            source → revision → fragment ingestion contracts
  learning/          evaluation, candidates, sole release-promotion authority
  control/           operator-facing access + file-import authority
  persistence/       PostgreSQL connectivity, tenant context, RLS boundary
adapters/          Everything that touches the outside world
  http/              FastAPI ingress, authentication, transport limits, routes
  parsers/           format parsers (PDF / Markdown / Office)
applications/      Thin process entry points (~200 LOC total)
  api.py             `context-engine-api`
  worker.py          `context-engine-worker`
bot_delivery/      M2 trusted Bot process (TypeScript); generated-SDK caller
action_plane/      prepare() → one-shot ticket → exact external effect
sdk/typescript/    OpenAPI-generated HTTP client
eval/              golden sets, slice gates, judges, security catalogs
migrations/        Alembic migrations
tests/             unit / integration / catalog / process suites
docs/              design authority, numbered ADRs, threat model, PRD, research
CONTEXT.md         domain glossary (terms only, no implementation)
PLAN.md            vision, principles, roadmap, non-goals
```

Two structural facts worth noticing:

- **Thin entry points, thick core.** `applications/` is roughly 200 lines. All
  behavior lives in `engine/`, which is what makes "the production composition
  root cannot substitute, skip, or wire a no-op `AuthorizationKernel`" an
  enforceable property rather than a slogan.
- **Tests outweigh implementation ~3:1.** Roughly 21k lines under `engine/`
  against roughly 68k lines under `tests/`. For a project whose central claim is
  a security invariant, the executable evidence *is* the product.

### What is pluggable, and what is not

| Layer | Pluggable (seam) | Sealed (kernel) |
|---|---|---|
| Parsing | PDF / Markdown / Office parsers | — |
| Representation | embeddings, reranker, LLM | — |
| Storage | V1 fixed on PostgreSQL FTS + pgvector; only an in-Runtime candidate-injection test seam | authorization source of truth (PostgreSQL) |
| Ingress | connectors, HTTP server ingress, MCP once a real caller exists; the generated SDK is a client artifact | authenticated invocation + `TrustedDeliveryContext` construction |
| Governance | evaluation judge models | sealed `ContextRuntime` orchestration, `AuthorizationKernel`, `DecisionAudit`, budget, provenance |

Portability is deliberately not promised before a second real backend exists.

### Trusted delivery

IM delivery is handled by `BotDelivery`, a trusted deep module that runs as its
own process from M2 and reaches the engine only through the generated HTTP SDK.
It does **not** declare its own audience in the wire body. It passes an opaque
`DeliveryEvidenceRef` in authenticated transport metadata; the ingress redeems
that for a `TrustedDeliveryContext` / `AudienceSnapshot`. Group permission
intersection is computed by the `AuthorizationKernel`, never by BotDelivery.

A group-visible answer and an asker-private answer are two separate
audience-bound resolves — never one package split after the fact. All external
side effects go through `ActionPlane.prepare` then `ActionPlane.perform`, each
with its own org-scoped, audience- and payload-bound, one-shot `ActionTicket`.

## The three hard invariants

These are **release vetoes, not scores**:

- Unauthorized Evidence leaked = **0**
- Wrong-Organization effect = **0**
- Missing tenant context = **fail closed, always**

No feature win offsets a failure in any of them. Every release reports
`PASS / FAIL / NOT_ACTIVE / NOT_APPLICABLE` against a versioned catalog and
lists capability coverage separately, so an inactive capability can never
masquerade as a passing one.

## Documentation

| Document | What it gives you |
|---|---|
| [CONTEXT.md](./CONTEXT.md) | Domain glossary — the repository's authority on identity, security, content, and lifecycle terms |
| [PLAN.md](./PLAN.md) | Vision, non-negotiable design principles, roadmap, explicit non-goals |
| [STATUS.md](./STATUS.md) | Per-issue capability activation ledger and evidence boundaries |
| [ADR index](./docs/decisions/README.md) | Numbered decision records: boundaries, dependency direction, forbidden shortcuts, revisit triggers |
| [Implementation Design](./docs/design/2026-07-18-context-engine-implementation-design.md) | The integrated implementation authority and milestone boundaries |
| [Threat Model](./docs/security/context-engine-threat-model.md) | Assets, trust boundaries, threats, hard oracles |
| [Program PRD](./docs/agents/prd-contextengine-implementation.md) · [Epic Tech Spec](./docs/specs/2026-07-19-context-engine-implementation-epic.md) | Requirements, 100 user stories, contract shapes, work packages |
| [Prior-art evidence baseline](./docs/research/2026-07-19-four-public-repositories-evidence.md) | Strengths, limits, clean-room breakdown, and evidence gaps of four public repositories |
| [D0 Baseline Candidate](./DESIGN-BASELINE.md) | Current candidate state and unclosed evidence gates |

## Prior art

The design draws on architectural study of four public open-source projects —
**Dify**, **RAGFlow**, **MaxKB**, and **Onyx** — limited strictly to observable
behavior, interface shape, test oracles, and product workflows. **Zero code was
copied.** Pinned versions and first-party links are recorded in the
[evidence baseline](./docs/research/2026-07-19-four-public-repositories-evidence.md).

ContextEngine's security and multi-tenancy protocols are designed independently
from its own requirements and threat model. Research from outside this
repository may inform reasoning, but it is never cited as public provenance.

## Contributing

This project holds an unusually strict evidence bar — security invariants are
veto gates, and capabilities may not be activated without executable proof.
Please read [CONTRIBUTING.md](./CONTRIBUTING.md) before opening a pull request;
it covers the verification contract, the ADR workflow, and what "done" means
here.

Issues and PRDs are tracked in
[GitHub Issues](https://github.com/stone16/context-engine/issues).

## License

Copyright 2026 stone16. Licensed under the
[Apache License 2.0](./LICENSE) — which includes an explicit patent grant.
Attribution notices are in [NOTICE](./NOTICE).
