---
name: adr-0090-admit-a-co-resident-local-evidence-console
version: "1.0.0"
description: >
  Admit one explicitly authenticated server-rendered evidence console inside
  the existing API process while preserving the public HTTP, Runtime, Control,
  and release-publication authority boundaries.
---

# 0090. Admit a co-resident local evidence console

- Status: accepted
- Date: 2026-07-30
- Refines: ADR-0047, ADR-0068, ADR-0069, ADR-0076, ADR-0077

## Context

M1 needs a first daily-driver delivery surface for seven operator jobs. The
maintainer selected server-side FastAPI templates in the existing API process;
a separate frontend process would add topology without isolation or performance
evidence. The surface still crosses two deliberately separate authorities.
Ask and Hit Test are ordinary current-Membership Runtime reads, while source
inspection, File import, and Article policy administration are Control work.
ADR-0069 originally kept the provisional local Control composition out of HTTP
because no authenticated administrative carrier then existed.

A co-resident template is not permission to call engine objects directly. An
anonymous browser must not inherit the process's configured dogfood Principal,
and a current Membership must not become a Control operator. Hit Test is also a
dangerous place to expose pre-authorization rank or score gaps. Finally,
feedback must remain evidence only; the release operator remains the sole owner
of activation, rollback, and promotion.

## Decision

1. The Evidence Console is installed in the existing API process as Jinja2
   templates and static CSS. It adds no process, frontend runtime, package
   manager, or network dependency.
2. A browser obtains a short-lived, `HttpOnly`, `SameSite=Strict` session proof
   only after explicitly presenting the configured dogfood credential. The
   proof is request-scoped browser state, contains no credential or trusted
   identity claim, and is insufficient if the public API authenticator rejects
   the underlying credential. Missing or expired proof renders a refusal.
3. Runtime jobs call `/v0/resolve` through an in-process ASGI HTTP client. Ask
   opens every returned `citationOpenRef` through `OpenCitation` and requires
   the replacement Package to close over the exact Article, Revision, Fragment,
   and Policy Epoch before rendering a clean answer.
4. The Control-backed routes are typed HTTP carriers inside the same API ingress.
   They remain schema-hidden so the immutable public OpenAPI v0 and generated SDK
   do not drift without a separately reviewed contract version. Every request
   requires the separate Control credential in authenticated transport metadata,
   constructs one `TrustedControlCall` for exactly one `ControlOperation`, and
   consumes that call in the Control gate before database or File-root work.
   The credential is entered for the operation, is never reflected into HTML,
   and is not stored in the browser session.
5. The Control operations admitted here are source-progress read, preview-bound
   File import, Article-policy read, and preview-bound Article-policy change.
   Exact current Membership is additionally rechecked for the acting audience.
   No release-operator credential or `ContextLearning.promote` capability is
   reachable from the UI. Feedback is not a Control operation: it persists only
   through a Runtime-role function that rechecks the current Membership and exact
   actor-owned ContextRun, and that role has no Release publication authority.
6. Hit Test renders only Blocks and Evidence from the post-Kernel
   `ContextPackage`. Candidate rank evidence and optional ranker scores remain
   outside the Package by ADR-0076; the UI states that score is not exposed by
   the rank-free public contract instead of inventing one or observing a denied
   score gap.
7. Import preview tokens bind the acting Membership, exact File bytes, compiler
   version, and exact Fragment set. Article preview tokens bind the acting
   Membership, expected policy version, expected Policy Epoch, and proposed
   policy. Confirmation rechecks current Membership and Control authority, and
   the database commits only that bound effect.
8. This is the local single-operator dogfood carrier, not a production
   administrative ancestor. A second operator, remote production exposure,
   delegated roles, or a durable browser-session service requires a new ADR.

## Rationale

Keeping presentation on the API's HTTP ingress seam makes the same authentication,
Kernel, provenance, and refusal behavior observable that another delivery client
would receive. Requiring an explicit browser proof prevents a loopback process
credential from becoming ambient anonymous authority. Requiring the independently
configured Control credential on each Control request preserves the read/write
plane split while permitting the maintainer-selected local UI to invoke already
bounded operations.

The rank-free limitation is deliberate. Adding diagnostic score fields to the
only online deliverable would widen the Runtime contract and could reveal the
shape of refused candidates. An explicit unavailable label is less convenient
but truthful and safe.

## Consequences

- OpenAPI v0 and the generated SDK remain byte-for-byte frozen. The typed local
  backing carriers and the HTML presentation are both schema-hidden.
- The API process may hold the Control database adapter only when the existing
  explicit local operator configuration is present. No Control authority exists
  by default.
- Operators re-enter the Control credential for Control-backed jobs. The cost is
  intentional: the session proves a reader, not an administrator.
- The Hit Test cannot display numeric scores until an accepted Runtime contract
  carries post-authorization scoring without denied-rank leakage.

## Revisit trigger

Revisit before production or non-loopback exposure, a second operator identity,
delegated Control roles, a separate UI process, durable session storage, or any
numeric Hit Test score. Any revision must keep anonymous requests fail-closed,
retain one-operation Control calls, preserve OpenCitation reauthorization, and
keep release publication authority out of feedback and Control.
