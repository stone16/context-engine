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
| Current online actor | [0023 — Current Membership-backed UserActor](0023-bind-runtime-to-current-membership-user-actor.md) | Trusted auth selects one exact current Membership; one complete PostgreSQL UserActor transaction and nominal proof remain live through Runtime and Package construction | Organization-only tenant access, caller-authored or default Membership, or proof reuse outside its authority transaction |
| First effective scope | [0024 — Finite target EffectiveScope](0024-model-effective-scope-as-finite-target-intersection.md) | Seven mandatory trusted finite target sets intersect; omitted RequestNarrowing is an explicit identity and supplied narrowing only filters | Stringly permission DSLs, missing-as-unrestricted, Agent-as-grant semantics, or caller-authored trusted scope |
| First authorized Evidence | [0025 — Current-transaction materialized projection](0025-bind-materialized-projection-to-the-current-runtime-transaction.md) | Content-free candidates use same-transaction RLS lineage, exact Kernel scope, then exact body projection before request-scoped Evidence | Second hydration transactions, pre-authorization body reads, index-as-authority, or persistent Evidence rows |
| Non-enumerating empty result | [0026 — Normalize no-authorized-Evidence](0026-normalize-no-authorized-evidence.md) | Cross-Organization, same-Organization denied, and missing candidates share one canonical empty Package and narrowly normalized comparison | Existence-specific status, shape, header, count, reason, or an unproven timing claim |
| First revocation boundary | [0027 — Organization Policy Epoch](0027-organization-policy-epoch.md) | One internal non-owner Control transaction revokes access and advances the Organization epoch; Acquire revalidates immediately before delivery | Cleanup-defined revocation, stale-decision reuse, external admin claims, or implying future carriers are active |
| Unavailable Runtime capabilities | [0028 — Fail-closed unavailable Runtime capabilities](0028-fail-closed-unavailable-runtime-capabilities.md) | Closed Acquire/Continue/OpenCitation wire; server-owned capability gate returns generic M0 domain refusals before content I/O | Caller-authored capability plans, fake empty success, existence detail, or claiming a real future carrier |
| First worker lease | [0029 — Persistent no-op WorkerLease](0029-bound-first-worker-lease-to-persistent-no-op-jobs.md) | Versioned HMAC-SHA256 exact-job lease permits one atomic persistent no-op completion; the full Supply carrier remains deferred | Ambient worker identity, signature-only authority, in-memory replay defense, or claiming full `ACCEPT-008` PASS |
| Read versus effect | [0011 — Read/write plane separation](0011-read-write-plane-separation.md) | `ContextAccessTicket` and `ActionTicket` use different audiences and are non-interchangeable | Using content/read authority to execute an external effect |
| First ticket audience split | [0030 — Bound ticket audiences](0030-bound-ticket-audiences.md) | Same identity/key configuration, but distinct signed read/action domains, nominal types, fixed synthetic operations, and target audiences; current epoch is checked before effect | Cross-plane reuse, caller-authored claims/targets, or claiming the synthetic no-op as production Provider or ActionPlane |
| Durable decision lineage | [0031 — Authorized-only ContextRun lineage](0031-persist-authorized-context-run-lineage.md) | Final digest-only ContextRun commits in the retained current-UserActor transaction; empty delivery adds a seven-field restricted DecisionAudit; one-use application authorization obtains an exact Organization-and-decision database ticket whose consumption the supported reader commits | Raw query/Package retention, denied identifiers or counts, post-response persistence, role/GUC-only operator access, cross-binding or post-expiry replay, claiming durable exactly-once redemption across a direct database rollback, or claiming complete observability redaction |
| Membership field projection | [0032 — Membership-bound materialized fields](0032-bind-materialized-fields-to-membership-projection-rights.md) | Exact Resource authorization is monotonically narrowed by current Membership/version field rights before field values leave PostgreSQL; projected fields bind Evidence and Package integrity | Candidate/request-authored fields, missing-as-all, Python-side filtering of private values, or implicit access to legacy bodies |
| Release promotion owner | [0033 — Organization release promotion owner](0033-promote-organization-releases-through-one-learning-owner.md) | Organization-owned immutable release lineage advances only through one generation-bound, release-operator-authorized `ContextLearning.promote` transaction | Pointer seeds, direct application DML, manifest-only CAS, evaluator/Control/Curation publication, or rollback mutation |
| Publication visibility | [0018 — Immutable ContextRevision publication](0018-immutable-revision-publication.md) | `ContextResource` content is immutable `ContextRevision`/`ContextFragment` lineage; one transaction changes the active pointer | In-place content mutation, mixed old/new reads, or cleanup-defined visibility |
| Release security catalog | [0019 — Security catalog normalization](0019-security-catalog-normalization.md) | One machine catalog contains exactly fifteen stable release IDs; overlapping labels and derived scenarios keep their safeguards without inflating the count | Parallel prose catalogs, renumbering, or treating inactive cache behavior as a canonical release family |
| Executable M0 security veto | [0034 — Registered executable security evidence](0034-execute-the-m0-security-veto-from-registered-evidence.md) | Exact current tests, explicit hard-oracle observations, and live all-table RLS facts produce provenance-bearing independent gate artifacts | Planned IDs presented as executed proof, skip/retry-to-green, manifest-only RLS claims, or aggregate scoring |
| First File source registration | [0035 — Trusted File source registration](0035-register-file-sources-through-context-control.md) | One operation-bound trusted Control call atomically creates an Organization-owned source plus immutable active first version; all acquisition carriers remain unavailable | Caller-authored Organization/mode, host paths, registration-time File I/O, future capability claims, or cross-tenant idempotency |
| First Markdown compiler | [0036 — Deterministic narrow Markdown compilation](0036-compile-narrow-markdown-deterministically.md) | Exact bytes compile purely into one canonical heading-plus-paragraph ParsedDocument with normalized coordinates and versioned content/compilation identities | Path-coupled parsing, silent unsupported syntax, partial documents, unversioned derived identities, or parser-side I/O |
| Structural Markdown compiler | [0038 — Structural Markdown units](0038-compile-and-publish-structural-markdown.md) | Explicit v2 compilation publishes one coherent Fragment per heading, paragraph, list, fenced code block, or table with exact provenance and same-Fragment heading ancestry | Silent v1 reinterpretation, item/cell fragmentation, post-authorization parent expansion, or unbudgeted context |
| Unchanged File acquisition | [0039 — File acquisition no-op](0039-deduplicate-unchanged-file-acquisitions.md) | Tenant/source/resource-scoped canonical identity and one PostgreSQL guard lock classify a complete active artifact before publication; each observation retains an immutable digest-only outcome | Cross-tenant/global deduplication, process-local locking, partial-artifact reuse, silent version reuse, or no-op by bypassing publication validation |
| File Resource deletion | [0042 — Tombstone before cleanup](0042-tombstone-file-resources-before-cleanup.md) | One trusted Control transaction tombstones the active File Resource, advances its Organization Policy Epoch, and records immutable pending cleanup lineage before any physical deletion | Cleanup-defined visibility, caller-authored tenant/epoch/cleanup identity, index deletion as authorization, restore, or native watcher claims |
| File Source progress | [0043 — Separate acquisition and publication progress](0043-separate-file-acquisition-progress-from-publication-progress.md) | Append accepted changes separately from contiguous Runtime-visibility completion and expose them through an Organization/Source-scoped Control read | One ambiguous checkpoint, skipped publication gaps, Runtime authorization from watermarks, or false standard ProviderPort capability claims |
| File Source offboarding | [0044 — Disable before cleanup](0044-disable-file-sources-before-cleanup.md) | One trusted Control transaction terminally disables the Source, advances its Organization Policy Epoch, cancels outstanding work, and records immutable pending cleanup lineage | Cleanup-defined revocation, bulk Resource deletion, application-only lifecycle checks, post-disable leases/tickets, or treating progress as authority |
| Private delivery ingress | [0045 — Redeem private delivery evidence at ingress](0045-redeem-private-delivery-evidence-at-ingress.md) | One digest-only service/request/asker/audience/epoch-bound DeliveryEvidenceRef constructs private TrustedDeliveryContext inside the current UserActor transaction before content work | Raw trusted delivery facts on the wire, bearer persistence, application-role minting/table reads, alternate Runtime paths, or claiming later M2 carriers |

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
- [0023 — Current Membership-backed UserActor](0023-bind-runtime-to-current-membership-user-actor.md)
- [0024 — Finite target EffectiveScope](0024-model-effective-scope-as-finite-target-intersection.md)
- [0026 — Normalize no-authorized-Evidence](0026-normalize-no-authorized-evidence.md)
- [0027 — Organization Policy Epoch](0027-organization-policy-epoch.md)
- [0028 — Fail-closed unavailable Runtime capabilities](0028-fail-closed-unavailable-runtime-capabilities.md)
- [0029 — Persistent no-op WorkerLease](0029-bound-first-worker-lease-to-persistent-no-op-jobs.md)
- [0030 — Bound ticket audiences](0030-bound-ticket-audiences.md)
- [0031 — Authorized-only ContextRun lineage](0031-persist-authorized-context-run-lineage.md)
- [0032 — Membership-bound materialized fields](0032-bind-materialized-fields-to-membership-projection-rights.md)
- [0033 — Organization release promotion owner](0033-promote-organization-releases-through-one-learning-owner.md)
- [0034 — Registered executable security evidence](0034-execute-the-m0-security-veto-from-registered-evidence.md)
- [0035 — Trusted File source registration](0035-register-file-sources-through-context-control.md)
- [0036 — Deterministic narrow Markdown compilation](0036-compile-narrow-markdown-deterministically.md)
- [0037 — First File publication](0037-publish-first-file-through-exact-worker-lease.md)
- [0038 — Structural Markdown units](0038-compile-and-publish-structural-markdown.md)
- [0039 — File acquisition no-op](0039-deduplicate-unchanged-file-acquisitions.md)
- [0040 — Atomic File replacement](0040-stage-and-atomically-activate-file-replacements.md)
- [0041 — Durable File publication recovery](0041-recover-file-publication-by-durable-boundary.md)
- [0042 — Tombstone File Resources before cleanup](0042-tombstone-file-resources-before-cleanup.md)
- [0043 — Separate File acquisition and publication progress](0043-separate-file-acquisition-progress-from-publication-progress.md)
- [0044 — Disable File sources before cleanup](0044-disable-file-sources-before-cleanup.md)
- [0045 — Redeem private delivery evidence at ingress](0045-redeem-private-delivery-evidence-at-ingress.md)
