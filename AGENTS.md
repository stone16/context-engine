---
name: agents-charter
version: "0.4.0"
description: Repository authority, security invariants, and task-specific context routing.
---

# ContextEngine

Address the user as **Stometa**.

**Stack**: Python 3.13 + FastAPI/Pydantic + SQLAlchemy/Alembic + PostgreSQL 17 + pgvector (ADR-0009); TS SDK via OpenAPI codegen. Multi-tenant context delivery engine — Supply/Runtime/Learning loops, ContextPackage as the only online deliverable.

**Design authority**: `docs/design/2026-07-18-context-engine-implementation-design.md`
(implementation authority), accepted ADRs under `docs/decisions/`, and
`CONTEXT.md` (glossary). Public reference claims must trace to
`docs/research/2026-07-19-four-public-repositories-evidence.md` or first-party
ContextEngine requirements and `docs/security/context-engine-threat-model.md`.
Repository-external
research may inform independent reasoning, but it is neither public authority nor
publishable provenance.

D0 closes semantic contracts before production code; disposable evidence spikes
must not become runtime foundations. Create planned directories only when used.

Process topology is explicit: the engine is an API process plus an independent
Supply worker; Supply work may execute in ContextEngine-owned runner
subprocesses (connector-runner, compiler-runner) under exact WorkerLease
binding with no independent persistence or index (ADR-0075); M2 adds one
trusted Bot application process containing BotDelivery and ActionPlane. No
further process boundary is added without measured isolation or performance
evidence.

## Commands

Use the root `Makefile` for current commands. For implementation changes, select
lint, typecheck, tests, and builds for the affected surface; run `make check` for
release validation. Runtime, authorization, worker, or tenant-isolation changes
also require the relevant integration evidence and `make security-gate`, backed
by the real database harness (`make db-up`). A documentation-only change needs
its applicable document/contract checks, not database startup or reset.

## Verification Contract

Report the checks actually run and any missing evidence. Preserve required CI and
release gates; a narrower local check does not establish release readiness. `.context-engine/database.env` is the generated,
ignored, mode-0600 source for local database connection contracts; `compose.yaml`
owns the pinned test service topology. A green process smoke proves only
boot/readiness. The database harness additionally proves Organization, current
Membership, the online UserActor transaction, and representative-record FORCE-RLS
isolation. `make security-gate` executes the registered M0 evidence and writes
raw evidence plus its independent release-gate report beneath the ignored
`.context-engine/security-gate/` directory. Content-bearing Runtime delivery
and worker-job behavior are active only for the exact carriers recorded by
their accepted catalog activations; deferred carrier semantics remain
`NOT_ACTIVE` until their owning issues verify them.

## Safety-Rails / Do Not

- Never blind-delete repo-specific content.
- Never commit secrets, `.env` values, or credentials — reference a single live source.
- Do not hardcode volatile values (URLs, ports, versions) in prose; point to their source of truth.
- **Runtime path is sealed, not merely wired**: production `ContextRuntime.resolve(AuthenticatedInvocation, TrustedDeliveryContext, Acquire | Continue | OpenCitation)` must pass through one non-pluggable `AuthorizationKernel` plus PackageBudget/provenance/audit gates; no feature flag, alternate composition, no-op dependency, or direct retriever-to-assembler path may bypass them.
- **Security is veto, not score**: Unauthorized Evidence = 0, wrong-Organization effect = 0, missing-context fallback = 0. No feature win offsets a failed invariant.
- **Controlled third-party reuse (ADR-0074)** — copying is permitted only from
  license-verified permissive regions at pinned commits (RAGFlow Apache-2.0;
  Onyx outside every `ee/` directory, MIT; separately-licensed MIT SDK
  subtrees), registered under `third_party/` with full attribution and SBOM
  coverage in shipped artifacts. Dify root-licensed code, MaxKB GPLv3 code,
  and Onyx `ee/` code remain clean-room only: behavior observations, interface
  shapes, and test oracles via the two-room protocol. Every public reference
  claim still traces through the four-repository evidence report;
  repository-external research inputs must never be cited, linked, or
  presented as public provenance.
- Missing tenant context = fail closed, always. Index/cache filters never make authorization decisions.
- Worker database context uses a registered least-privilege ServiceActor and a server-minted signed WorkerLease with exact durable-job binding. Never impersonate the triggering user or treat ingestion authority as delivery authority.
- Inside Runtime, content-bearing rerank/hydration/relevance-model/assembly accepts `AuthorizedProjection` only. BotDelivery's generation ModelGateway accepts only `AuthorizedModelInput` derived from one current audience-bound ContextPackage plus a matching EgressGrant. `CandidateRef` must pass exact authorization and field projection first. The Article (`ContextResource`) is the only content authorization atom and Fragments never carry independent ACLs (ADR-0077): expansion within the same Article and current Revision inherits that Article's decision after lineage verification; every cross-Article expansion is re-authorized.
- `SourceAclEvidence` is explicitly Live, Mirrored, or Weak. Weak is permitted only when the source genuinely lacks stronger ACL semantics; it is never a fallback for a failed Live/Mirrored check.
- `TrustedDeliveryContext` and `AudienceSnapshot` are trusted facts. Callers cannot manufacture them; the Kernel, not BotDelivery, computes group authorization. Public-group and asker-private packages are separate resolves.
- Remote BotDelivery passes only a per-resolve opaque `DeliveryEvidenceRef` in authenticated transport metadata. Raw trusted identity or audience claims never belong in the wire body; ingress must redeem and validate the reference before content work.
- External effects go only through `ActionPlane.prepare` then `ActionPlane.perform`; each effect has its own org/audience/payload-bound one-shot ActionTicket. Never reuse a create ticket for edit/send.
- ContextRun is authorized-only. Denied details belong only in restricted DecisionAudit as reason categories/digests, never as tenant-visible content or Learning corpus.
- ContextLearning may produce candidates and evaluation reports; only its release-operator-authorized promote path may activate or roll back a ReleaseManifest. ContextControl never publishes profiles. Never give two modules production publication authority.

## Shelf routing (Progressive Disclosure)

Each shelf owns its own rules. This table routes; it never restates a shelf.

| When you are… | Read first |
|---|---|
| changing domain behavior or terminology | `docs/agents/domain.md` |
| filing, reading, or triaging a work item | `docs/agents/issue-tracker.md` |
| labelling a work item | `docs/agents/triage-labels.md` |
| touching the UI / components | Existing UI conventions; `DESIGN.md` if present |
| making an architectural choice | `docs/decisions/` (write a new ADR) |

## Definition of Done

- [ ] Change does what the task asked; edge cases considered.
- [ ] Applicable checks pass with fresh evidence; unavailable checks and release gaps are explicit.
- [ ] Runtime tests use the highest public seam available (HTTP/generated SDK) and prove `CandidateRef → AuthorizationKernel → AuthorizedProjection`; no raw candidate reaches content-bearing consumers.
- [ ] No secrets or volatile values baked into docs/code.
- [ ] Architectural decisions are recorded as ADRs under `docs/decisions/`.
