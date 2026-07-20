---
name: agents-charter
version: "0.3.0"
description: >
  Anchor the ContextEngine agent's always-resident context: identity, architecture
  map, commands, and safety rails. Use when starting any task, before reading the
  request. Not for prose copy-editing or application code review.
---

# Identity & Context Awareness

**CRITICAL**: Address the user as "stometa" at the start of EVERY response.

This is a context-awareness signal — if missing, context has drifted.

---

# ContextEngine

**Stack**: Python 3.13 + FastAPI/Pydantic + SQLAlchemy/Alembic + PostgreSQL 17 + pgvector (ADR-0009); TS SDK via OpenAPI codegen. Multi-tenant context delivery engine — Supply/Runtime/Learning loops, ContextPackage as the only online deliverable.

**Design authority**: `docs/design/2026-07-18-context-engine-implementation-design.md`
(implementation authority), accepted ADRs under `docs/decisions/`, and
`CONTEXT.md` (glossary). Public reference claims must trace to
`docs/research/2026-07-19-four-public-repositories-evidence.md` or first-party
ContextEngine requirements and `docs/security/context-engine-threat-model.md`.
Repository-external
research may inform independent reasoning, but it is neither public authority nor
publishable provenance.

## Architecture Map

```
ContextEngine/
├── engine/           # Supply/Runtime/Learning + sealed AuthorizationKernel
├── adapters/         # parsers, connectors, HTTP ingress, optional future MCP; File is Provider #1
├── bot_delivery/      # M2 Bot app process; trusted IM delivery; generated HTTP SDK caller
├── action_plane/      # co-resident Bot app Module; prepare -> ticket -> exact effect
├── contract_kit/     # base runner + twins before Feishu; versioned kit v1 proven by Slack
├── eval/             # golden set, slice gates, judges
├── tests/            # incl. security suite: real PG17 + non-owner role + FORCE RLS
├── docs/             # design/ + decisions/ (ADRs)
├── CONTEXT.md        # domain glossary (terms only, no implementation)
├── AGENTS.md         # this charter (CLAUDE.md is the compatibility bridge)
└── DESIGN.md         # design system — add when the UI surface stabilizes
```

(Directories are the planned M0–M2 shape; create on first use, don't pre-scaffold empties. D0 closes and versions semantic contracts before production code; isolated disposable evidence spikes are allowed and must not become runtime foundations.)

Process topology is explicit: the engine is an API process plus an independent
Supply worker; M2 adds one trusted Bot application process containing
BotDelivery and ActionPlane. No further process boundary is added without
measured isolation or performance evidence.

## Commands

```bash
make install   # sync the locked Python 3.13 environment
make build     # build wheel and source distribution
make lint      # Ruff
make typecheck # strict mypy
make test      # unit test suite
make catalog   # static security catalog tests and validation
make smoke     # API and worker process smoke suite
make check     # all required repository checks
```

## Verification Contract

Before claiming an implementation done, run the verified commands recorded
above. Never fabricate output. A green process smoke proves only boot/readiness;
Runtime delivery, database and worker-job capabilities remain `NOT_ACTIVE` until
their owning issues implement and verify them.

## Safety-Rails / Do Not

- Never blind-delete repo-specific content.
- Never commit secrets, `.env` values, or credentials — reference a single live source.
- Do not hardcode volatile values (URLs, ports, versions) in prose; point to their source of truth.
- **Runtime path is sealed, not merely wired**: production `ContextRuntime.resolve(AuthenticatedInvocation, TrustedDeliveryContext, Acquire | Continue | OpenCitation)` must pass through one non-pluggable `AuthorizationKernel` plus PackageBudget/provenance/audit gates; no feature flag, alternate composition, no-op dependency, or direct retriever-to-assembler path may bypass them.
- **Security is veto, not score**: Unauthorized Evidence = 0, wrong-Organization effect = 0, missing-context fallback = 0. No feature win offsets a failed invariant.
- **Zero code copying from Dify, RAGFlow, MaxKB, or Onyx** — use clean-room
  observations of behavior, interface shape, test oracles, and product
  workflows only, with every public reference claim traced through the
  four-repository evidence report. Repository-external research inputs must
  never be cited, linked, or presented as public provenance.
- Missing tenant context = fail closed, always. Index/cache filters never make authorization decisions.
- Worker database context uses a registered least-privilege ServiceActor and a server-minted signed WorkerLease with exact durable-job binding. Never impersonate the triggering user or treat ingestion authority as delivery authority.
- Inside Runtime, content-bearing rerank/hydration/relevance-model/assembly accepts `AuthorizedProjection` only. BotDelivery's generation ModelGateway accepts only `AuthorizedModelInput` derived from one current audience-bound ContextPackage plus a matching EgressGrant. `CandidateRef` must pass exact authorization and field projection first; every parent/neighbor expansion is re-authorized.
- `SourceAclEvidence` is explicitly Live, Mirrored, or Weak. Weak is permitted only when the source genuinely lacks stronger ACL semantics; it is never a fallback for a failed Live/Mirrored check.
- `TrustedDeliveryContext` and `AudienceSnapshot` are trusted facts. Callers cannot manufacture them; the Kernel, not BotDelivery, computes group authorization. Public-group and asker-private packages are separate resolves.
- Remote BotDelivery passes only a per-resolve opaque `DeliveryEvidenceRef` in authenticated transport metadata. Raw trusted identity or audience claims never belong in the wire body; ingress must redeem and validate the reference before content work.
- External effects go only through `ActionPlane.prepare` then `ActionPlane.perform`; each effect has its own org/audience/payload-bound one-shot ActionTicket. Never reuse a create ticket for edit/send.
- ContextRun is authorized-only. Denied details belong only in restricted DecisionAudit as reason categories/digests, never as tenant-visible content or Learning corpus.
- ContextLearning may produce candidates and evaluation reports; only its release-operator-authorized promote path may activate or roll back a ReleaseManifest. ContextControl never publishes profiles. Never give two modules production publication authority.

## Shelf routing (Progressive Disclosure)

| When you are… | Read first |
|---|---|
| touching the UI / components | `DESIGN.md` (add when the UI stabilizes) |
| making an architectural choice | `docs/decisions/` (write a new ADR) |

## Definition of Done

- [ ] Change does what the task asked; edge cases considered.
- [ ] Tests pass; verification command run with fresh evidence.
- [ ] Runtime tests use the highest public seam available (HTTP/generated SDK) and prove `CandidateRef → AuthorizationKernel → AuthorizedProjection`; no raw candidate reaches content-bearing consumers.
- [ ] No secrets or volatile values baked into docs/code.
- [ ] Any non-obvious decision recorded as an ADR under `docs/decisions/`.
