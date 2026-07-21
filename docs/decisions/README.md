# Architecture Decision Records

This directory contains accepted, independently revisitable architecture
decisions. [`CONTEXT.md`](../../CONTEXT.md) owns terminology; the
[`Implementation Design v1.2`](../design/2026-07-18-context-engine-implementation-design.md)
owns integrated implementation detail. An ADR fixes one boundary and names the
evidence required to reopen it.

## Implementation boundary baseline

Read these ADRs before implementation. Together they define the allowed process
and dependency direction, trusted inputs, online contract, mandatory security
kernel, capability separation, and publication visibility model.

| Boundary | Accepted ADR | Fixed choice | Prohibited shortcut |
|---|---|---|---|
| Engine output | [0006 — Engine delivers context, not answers](0006-engine-delivers-context-not-answers.md) | The online engine boundary ends at `ContextPackage` | Generation, planning, or external effects inside ContextEngine |
| Bot caller | [0002 — BotDelivery outside the engine](0002-bot-gateway-outside-engine.md) | BotDelivery is an external caller in a trusted Bot application process | Treating IM as a Runtime transport or importing engine internals |
| Process shape | [0008 — Modular monolith plus worker](0008-modular-monolith-plus-worker.md) | API and independent Supply worker share one domain package; M2 adds the Bot application | Premature engine microservices or ambient worker identity |
| Kernel versus seams | [0012 — Sealed authorization projection](0012-sealed-authorization-projection-pipeline.md) | Policy, audit, budget, provenance, and exact projection are mandatory Kernel behavior | Disable flags, no-op security dependencies, or content before authorization |
| Trusted access boundary | [0017 — Trusted invocation and closed Runtime access](0017-trusted-invocation-and-closed-runtime-access.md) | HTTP, generated SDK, and activated MCP map to one Runtime contract; trusted inputs are ingress-built | Caller-supplied identity/ACL/audience, transport-local policy, or IM as a fourth transport |
| Staged HTTP authentication | [0021 — HTTP authentication before provider selection](0021-stage-http-authentication-before-provider-selection.md) | Test auth proves nominal invocation construction; the default application rejects every credential | Treating the test seam as production authentication or Runtime delivery |
| Staged empty Package | [0022 — Tenant-safe empty ContextPackage](0022-stage-tenant-safe-empty-context-package.md) | One real Runtime Acquire returns only an evidence-free, budgeted Package with server-owned trust facts | Placeholder policy/egress lineage, caller-authored purpose, or content I/O |
| Read versus effect | [0011 — Read/write plane separation](0011-read-write-plane-separation.md) | `ContextAccessTicket` and `ActionTicket` use different audiences and are non-interchangeable | Using content/read authority to execute an external effect |
| Publication visibility | [0018 — Immutable ContextRevision publication](0018-immutable-revision-publication.md) | `ContextResource` content is immutable `ContextRevision`/`ContextFragment` lineage; one transaction changes the active pointer | In-place content mutation, mixed old/new reads, or cleanup-defined visibility |
| Release security catalog | [0019 — Security catalog normalization](0019-security-catalog-normalization.md) | One machine catalog contains exactly fifteen stable release IDs; overlapping labels and derived scenarios keep their safeguards without inflating the count | Parallel prose catalogs, renumbering, or treating inactive cache behavior as a canonical release family |

Each baseline ADR is `accepted` and contains Context, Decision, Rationale,
Consequences, and Revisit trigger sections. A revisit trigger permits review; it
does not silently supersede the current decision. Record the replacement as a
new ADR or an explicit refinement.

## Allowed topology and dependency direction

~~~text
Bot application process
  BotDelivery -> generated HTTP SDK -> Engine HTTP ingress
  BotDelivery -> ActionPlane -> external effects

Engine API process -> shared domain package <- Supply worker process
  HTTP ingress       ContextRuntime              durable job + WorkerLease
                          |
                          v
                 sealed AuthorizationKernel
             policy + audit + budget + provenance
                          |
                          v
             ports implemented by outer Adapters
~~~

Dependencies point toward the shared domain and its ports. The shared domain
does not import BotDelivery, transport, provider, parser, index, or effect
implementations. BotDelivery cannot import engine internals and reaches Runtime
only through the generated HTTP SDK. The worker shares domain behavior but gets
authority only from its exact durable job and `WorkerLease`, never from a
triggering User.

## Public interface map

- Online context read:
  `ContextRuntime.resolve(AuthenticatedInvocation, TrustedDeliveryContext,
  Acquire | Continue | OpenCitation) -> ResolutionOutcome`.
- Online engine deliverable: `ContextPackage`; answer generation remains in
  callers such as BotDelivery.
- V1 ingress: HTTP. The TypeScript SDK is a generated HTTP client artifact. MCP
  is a thin optional ingress that remains `NOT_ACTIVE` until a real caller and
  the same contract/security suite justify it. IM is not a server transport.
- Source seam: `ContextProvider`; provider output is evidence for the sealed
  Kernel, never the final authorization decision.
- Effect seam: `ActionPlane`; its `ActionTicket` cannot be substituted with a
  `ContextAccessTicket`, ContextPackage, or read credential.
- Supply visibility: a `ContextResource` points to one active immutable
  `ContextRevision`; `ContextFragment` values retain that ContextRevision
  lineage.

## Related accepted decisions

The baseline above is the minimum implementation reading set. These accepted
ADRs refine adjacent choices and remain authoritative when their scope is
touched:

- [0001 — Agent-facing documentation standard](0001-adopt-doc-steward-standard.md)
- [0003 — Group-chat intersection authorization](0003-group-chat-intersection-authorization.md)
- [0004 — WeCom-only weak ACL degradation](0004-wecom-only-weak-acl-degradation.md)
- [0005 — Python stack](0005-python-stack.md)
- [0007 — PostgreSQL authorization truth](0007-postgres-as-authorization-truth.md)
- [0009 — pgvector-first index](0009-pgvector-first-index.md)
- [0010 — Policy Epoch revocation](0010-policy-epoch-revocation.md)
- [0013 — Trusted delivery and capability taxonomy](0013-trusted-delivery-egress-and-capability-taxonomy.md)
- [0014 — Curation snapshot and release ownership](0014-curation-snapshot-and-release-ownership.md)
- [0015 — RLS transaction context and schema manifest](0015-rls-transaction-context-and-schema-manifest.md)
- [0016 — Implementation authority and vertical-slice roadmap](0016-implementation-authority-and-vertical-slice-roadmap.md)
- [0019 — Security catalog normalization](0019-security-catalog-normalization.md)
- [0020 — Staged Organization RLS proof](0020-stage-organization-rls-before-actor-context.md)
- [0021 — HTTP authentication before provider selection](0021-stage-http-authentication-before-provider-selection.md)
- [0022 — Tenant-safe empty ContextPackage](0022-stage-tenant-safe-empty-context-package.md)
