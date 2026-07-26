# ContextEngine

[![CI](https://github.com/stone16/context-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/stone16/context-engine/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](./LICENSE)
[![Python](https://img.shields.io/badge/python-3.13-blue.svg)](./pyproject.toml)
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

### 1. What is this audience allowed to know, right now?

Retrieval alone cannot answer that. In ContextEngine the index never returns
deliverable text — it returns a `CandidateRef`. Every candidate must pass
through a sealed `AuthorizationKernel` that performs exact authorization and
field projection before *anything* content-bearing happens. Hydration,
reranking, relevance models, and packaging all run on `AuthorizedProjection`
only. Every parent or neighbor expansion is re-authorized item by item.

Source ACL evidence is explicitly classified as `Live`, `Mirrored`, or `Weak`.
`Weak` is only for sources that genuinely lack fine-grained ACLs — it is never
a fallback when a `Live` or `Mirrored` check fails. That case fails closed.

### 2. Who keeps the knowledge base organized?

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
| Production authentication (OAuth/JWT) | `NOT_ACTIVE` |
| Real source ACLs, general content retrieval, `Continue` / `OpenCitation` | `NOT_ACTIVE` |
| Live Feishu / Slack / Google Docs connectors, group chat | `NOT_ACTIVE` |

**[→ Full capability ledger with per-issue evidence boundaries (STATUS.md)](./STATUS.md)**

The roadmap and milestone exit criteria live in [PLAN.md](./PLAN.md).

## Quick start

### Prerequisites

| Requirement | Version | Why |
|---|---|---|
| [Python](https://www.python.org/) | 3.13 (`>=3.13,<3.14`) | Engine, adapters, worker |
| [uv](https://docs.astral.sh/uv/) | any recent | Dependency resolution, pinned by `uv.lock` |
| [Node.js](https://nodejs.org/) | 22.12.0 (see [`sdk/typescript/.node-version`](./sdk/typescript/.node-version)) | TypeScript SDK, ActionPlane, BotDelivery |
| Docker (with Compose) | any recent | Real PostgreSQL 17 + pgvector test harness |

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

```bash
uv run context-engine-api
```

Defaults to `127.0.0.1:8000`; override with `--host`, `--port`, `--log-level`
(see `context-engine-api --help`). Then:

```bash
curl http://127.0.0.1:8000/health
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
or `SIGINT`.

All dispatch modes read **only** a role-specific scheduler, worker URL,
WorkerLease signing key, and the server-side JSON root registry
(`CONTEXT_ENGINE_WORKER_FILE_ROOTS_JSON`). **A caller may not supply
Organization, Source, job, or token** — that is the point of the boundary.
Output is limited to `dispatched` / `no_work` / `refused`.

Lease validation uses the worker's PostgreSQL clock, staying in the same time
domain as database-issued timestamps rather than depending on worker host clock
alignment. Unavailable worker infrastructure terminates dispatch instead of
continuing to claim and strand later jobs. File or content failures return
`refused` and continue scheduling only once that job is durably terminal-failed
or the current authority rejects that exact failure transition.

Activation boundaries for File dispatch, reclaim, and delete execution are
recorded in [STATUS.md](./STATUS.md).

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

```
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

```
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
docs/              design authority, 60 ADRs, threat model, PRD, research
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
| [ADR index](./docs/decisions/README.md) | 60 decision records: boundaries, dependency direction, forbidden shortcuts, revisit triggers |
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
