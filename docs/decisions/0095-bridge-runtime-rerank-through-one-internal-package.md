---
name: adr-0095-bridge-runtime-rerank-through-one-internal-package
version: "1.0.0"
description: >
  Bridge Runtime model-backed rerank to the ADR-0052 Package-derived model
  input rule through one internal, undelivered, audience-bound pre-rerank
  ContextPackage, keeping one nominal AuthorizedModelInput contract with one
  constructor per authorized process composition and at most one caller-visible
  grant per resolve. Use when implementing the authorized rerank carrier
  (ADR-0075 lift 5) or any future Runtime-internal model inference. Not an
  activation of the rerank carrier and not a relaxation of ADR-0012, ADR-0046,
  or ADR-0052 outside the exact carve-outs recorded here.
---

# 0095. Bridge Runtime rerank through one internal pre-rerank Package

- Status: accepted
- Date: 2026-07-31
- Refines: ADR-0012, ADR-0046, ADR-0052, ADR-0075, ADR-0076
- Related: ADR-0077, ADR-0083
- Decision input: `docs/research/2026-07-31-five-repository-implementation-blueprint.md` §5 (D2, decided 2026-07-31) and `docs/research/2026-07-31-onyx-blueprint-evaluation.md` §3.5 (maintainer-local Room-A research; not public provenance)

## Context

ADR-0052 fixes model-generation governance to one nominal `AuthorizedModelInput`
constructed from one complete current audience-bound ContextPackage plus a
matching one-shot `EgressGrant`, with the constructor realized as the private
TypeScript `prepareAuthorizedModelInput` factory inside the trusted Bot
application process. ADR-0012 fixes that inside Runtime, content-bearing rerank
and the other content-bearing stages accept `AuthorizedProjection` only, and
that the Package-derived `AuthorizedModelInput` belongs to the separate
downstream answer-generation boundary. ADR-0075 clause 4 repeats the
projection-only rule for content-bearing stages and schedules an authorized
rerank carrier (lift 5) inside the engine's governed model-inference port,
which lives in the Runtime process (Python), not in the Bot application.

Taken literally and unreconciled, these decisions make the rerank carrier
unimplementable: the Bot-side constructor cannot be called from Runtime (the
accepted topology admits Bot → generated SDK → engine and forbids the reverse
import direction), while an engine-side constructor would be a second nominal
type unless the decisions are refined to say what "one nominal contract" means
across process compositions. The Onyx lift-5 evaluation confirmed that every
upstream rerank shape holds raw credentials, provider fallbacks, and plain
passage lists with no audience/package/grant/budget boundary, so porting any of
it is excluded; the gap is purely a ContextEngine composition question.

Maintainer decision D2 selects the two-Package timing — form one internal
undelivered Package and feed it to the Package-derived model input rule — with
rerank output fixed as an exact Evidence permutation and no second nominal type
with the same name. This ADR records the exact refinements that make D2
consistent with ADR-0012, ADR-0046, ADR-0052, ADR-0075, and ADR-0076.

## Decision

1. **One internal pre-rerank Package.** After authorization and the authorized
   ranking stage (ADR-0076), Runtime composes one **internal, undelivered**
   ContextPackage over exactly the admitted `AuthorizedProjection`s. It carries
   every delivered-Package invariant: current Organization/Membership,
   audience, purpose, Policy snapshot/epoch, Block↔Evidence one-to-one closure,
   expiry, and package digest. Its purpose is the server-owned closed value
   `pre_rerank_model_input`; callers never supply or select it (the purpose
   binding discipline of ADR-0022 and ADR-0046 applies). It is never delivered
   and never caller-visible; its sole content egress is the exact internal
   model hop of clause 4, after the mandatory internal `EgressGate` and
   one-shot grant redemption — every other egress remains prohibited. It
   produces no operator-visible Package; its digest and its meter reservations
   never enter ContextRun or DecisionAudit; the one ContextRun of the resolve
   binds only the final delivered Package digest (ADR-0031).
2. **One nominal contract, one constructor per authorized process
   composition.** The ADR-0052 rule — exactly one complete current
   audience-bound ContextPackage, a matching one-shot `EgressGrant`, the closed
   question envelope, trusted time, and the Release-manifest-bound versioned
   model profile, yielding `AuthorizedModelInput` — is one nominal contract,
   not one process. One nominal contract means one contract definition
   (construction rule, canonical serialization, and digest), not one
   language-runtime type: digest-equivalent twins validated under shared
   fixtures are how this repository names cross-composition contract identity,
   as ADR-0052's own Python/TypeScript digest authorities already establish.
   Decision D2's prohibition on "a second nominal type with the same name"
   forbids any second contract under this name with different construction
   rules — projection-fed, candidate-fed, or caller-authored input; no such
   contract exists. The Bot-application TypeScript factory remains the sole
   constructor for generation hops and its private boundary is unchanged. For
   the Runtime rerank port, the engine exposes the same nominal
   `AuthorizedModelInput` contract with exactly one engine-side constructor
   that applies the identical rule to the internal pre-rerank Package; the
   Bot-side and engine-side constructors are proven digest-equivalent under
   shared fixtures (ADR-0052 digest-twin discipline), including the versioned
   profile binding. No other constructor exists. `AuthorizedProjection`s,
   `CandidateRef`s, duck-typed packages, two-Package batches, and inputs
   missing the closed question envelope, trusted time, or release-bound profile
   cannot construct `AuthorizedModelInput` in either process.
3. **Exact-permutation output contract.** The rerank provider returns an exact
   permutation of **all** input Evidence indices — nothing else. Invented refs,
   duplicate or out-of-range indices, and non-finite scores are construction
   failures. Subset selection happens only during final Package construction
   under the budget rules; the rerank result itself is a permutation. Returned
   scores never enter authorization decisions, never become public Package
   fields, and never leave the governed port except as closed digest/category
   trace.
4. **Grant issuance (refines ADR-0046).** ADR-0046's sentence "Runtime issues
   at most one variant for a resolve" is replaced, for resolves in which the
   rerank carrier is active, by: Runtime issues at most one **internal
   model-hop grant** — issued inside the retained current-UserActor
   transaction after internal Package construction, budget, provenance, and
   current-epoch validation pass a mandatory **internal `EgressGate`**
   mirroring the final gate inside Runtime; bound with the complete relevant
   ADR-0046 binding set (Organization, internal Package digest, canonical
   payload digest, purpose, audience digest, Policy Epoch, hop variant
   `internal_model`, retention and sensitivity profiles, issuer Runtime,
   consumer the governed rerank port, provider/model/region, issuance, expiry,
   profile lineage); redeemed under the same one-shot digest-only retention
   and generic zero-byte failure discipline as the final hop; redeemed inside
   Runtime and never returned to any caller or transport — plus at most one
   **final-hop grant** carrying the same complete binding set against the
   delivered Package. Callers still receive at most one grant variant (the
   final hop); the internal-only default for resolves without rerank is
   unchanged; the mandatory final `EgressGate` still runs after final Package
   construction; grants are never reused across hops or resolves. Multi-hop
   batches beyond this exact two-hop sequence remain deferred under ADR-0046's
   revisit trigger.
5. **Order precedence (refines ADR-0076).** Rerank consumes the ADR-0076
   authorized-ranking-stage order as its sole ordering input. While the rerank
   carrier is active, selection, budget packing, and assembly order derive
   solely from the exact rerank permutation; while it is inactive, the
   authorized-ranking-stage order governs unchanged. This is the permitted
   reading of ADR-0076 clause 4's "read rank only from this stage" once rerank
   is active; rerank scores remain non-authoritative, never enter
   authorization, and never become Package fields. ADR-0096 clause 5 mirrors
   this precedence for token accounting and packing.
6. **Projection-rule carve-out (refines ADR-0012 and ADR-0075 clause 4).**
   Inside Runtime, the model-backed rerank port is the single content-bearing
   stage that consumes the nominal `AuthorizedModelInput`, constructed only over
   the internal Package whose Evidence is exactly the admitted
   `AuthorizedProjection`s. Every other content-bearing stage — dedupe, token
   accounting, expansion hydration, Assembler, ordinary trace, ContextRun —
   keeps the ADR-0012/ADR-0075 rule: `AuthorizedProjection` only. The
   `CandidateRef → AuthorizationKernel → AuthorizedProjection` order is
   preserved: Kernel authorization and ADR-0076 ranking both precede internal
   Package composition. A static gate proves the previous projection-fed rerank
   request shape cannot reach any provider.
7. **Release-bound profile, fail closed.** The rerank profile is an immutable
   Release-manifest-bound Runtime profile, validated at composition activation
   and on every request (ADR-0068 clause 6 discipline). Its unavailability
   behavior is frozen in the profile and defaults to closed unavailability. No
   fallback model call and no silent degradation to retrieval order exist
   unless a separate recorded maintainer decision defines them and their
   activation evidence.
8. **Activation stays deferred.** The rerank carrier remains `NOT_ACTIVE`. Its
   owning issue must register, before activation: the security oracle that
   denied/cross-Organization candidates mixed into an authorized set contribute
   zero content bytes to the rerank gateway and assembler; release-binding and
   per-request validation of the rerank profile; one-shot grant redemption
   evidence for both hops; exact-permutation negative tests; and cumulative
   shared-meter usage in the final Package (ADR-0096). Real provider network
   calls remain a separate activation gate.

## Consequences

- ADR-0052's letter holds: every `AuthorizedModelInput` derives from one
  current audience-bound ContextPackage through one nominal contract; "one
  constructor per authorized process composition, digest-twin validated" is the
  recorded meaning across the Bot and engine boundaries.
- ADR-0046's caller-facing guarantees hold: a caller still sees at most one
  grant variant per resolve; the internal model-hop grant never leaves Runtime.
- ADR-0012 and ADR-0075 keep the projection-only rule for every content-bearing
  stage except the single carved-out model-backed rerank port.
- Onyx lift 5 is unblocked without porting any upstream rerank code.
- One extra internal Package composition per reranked resolve is the accepted
  price of one nominal contract and one governance story.

## Revisit trigger

Revisit if measured internal-Package overhead becomes material, if a second
Runtime-internal model use case appears that would generalize the internal
purpose envelope or the per-composition constructor rule, or if a rerank
fallback policy or a multi-hop batch beyond this exact two-hop sequence is
proposed (each requires its own decision under the ADR-0046 revisit trigger).
