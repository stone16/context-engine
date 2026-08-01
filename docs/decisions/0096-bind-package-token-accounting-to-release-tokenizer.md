---
name: adr-0096-bind-package-token-accounting-to-release-tokenizer
version: "1.0.0"
description: >
  Bind Package token accounting to a ReleaseManifest-fixed tokenizer profile
  and one resolve-owned cumulative budget meter (tokens, provider calls, cost,
  elapsed) published in the final Package and ContextRun, through a reviewed
  new contract version that never mutates frozen v0. Use when preparing to
  activate a model-backed Runtime carrier (rewrite, rerank, select). Not for
  activating those carriers or choosing a tokenizer vendor.
---

# 0096. Bind Package token accounting to a release tokenizer

- Status: accepted
- Date: 2026-07-31
- Refines: ADR-0047 (the release-lineage tokenizer reference and the `utf8-byte-budget-v1` accounting profile it currently fixes on v0); ADR-0048 (generated-SDK coexistence under the new reviewed contract version)
- Related: ADR-0012, ADR-0022, ADR-0031, ADR-0033, ADR-0066, ADR-0067, ADR-0068, ADR-0079
- Decision input: `docs/research/2026-07-31-five-repository-implementation-blueprint.md` §5 (D4, decided 2026-07-31) and `docs/research/2026-07-31-onyx-blueprint-evaluation.md` §3.6 (maintainer-local Room-A research; not public provenance)

## Context

The delivered-Package token accounting currently fixed on v0 release lineage
(ADR-0047) is the byte-based accounting profile `utf8-byte-budget-v1`. A
separate constant, `utf8-byte-token-v1`, exists only as code in the
not-yet-active model-inference port (`engine/runtime/model_inference.py`) and
has never been admitted by any ADR; it is not the delivered-Package accounting
profile. (ADR-0066 governs Fragment embeddings and contains no token
accounting.) The byte profile is honest for the loopback dogfood carrier
(ADR-0068), which makes no provider calls and reports zero-filled usage
honestly, but it cannot govern model-backed Runtime carriers (`rewrite`,
`rerank`, `select`): their profiles need true token counts, and their cost must
be metered cumulatively with assembly bytes. The atomic `PackageBudgetMeter`
(reserve/commit/cancel) already exists; what is missing is one resolve-owned
meter shared by all stages, a Release-bound real tokenizer identity, and
publication of cumulative usage. The Onyx lift-6 evaluation showed upstream
budgeting helpers (fixed 75-token metadata estimates, best-effort BPE
trimming) are unsafe as hard budgets, so nothing is ported.

Maintainer decision D4 selects introducing a real ReleaseManifest-bound
tokenizer **before** any model-backed carrier activates, with one
schema/OpenAPI migration, over keeping byte accounting indefinitely or
publishing two accounting dimensions side by side.

## Decision

1. **Tokenizer profile in the ReleaseManifest.** Token accounting is governed
   by an immutable tokenizer profile referenced by the active ReleaseManifest:
   pinned tokenizer artifact digest, vocabulary/normalization identity, and
   accounting version. Package `tokens` are counted only under the manifest's
   tokenizer. The tokenizer artifact is a pinned, hash-verified dependency; no
   network fetch at resolve time; provider-side tokenizer APIs never decide a
   hard budget. **Fail closed:** a missing, unknown, unavailable, or
   hash-mismatched tokenizer artifact; a carrier profile naming a different
   tokenizer than the active ReleaseManifest; or mixed tokenizer identities
   within one resolve — each refuses before any counting and before the first
   provider byte, with zero fallback to `utf8-byte-budget-v1`, to a provider
   tokenizer API, to cached counts, or to a stage-local meter. This ADR
   governs Package accounting only; ADR-0079's representation-bound compile-time
   splitting counter remains profile-local and unchanged.
2. **Ordered migration, new contract version, one deadline.** The migration
   completes **before** any model-backed Runtime carrier is activated, in this
   order: (a) admit an immutable tokenizer-profile record with digest bindings
   in the release lineage, giving substance to the ADR-0047 tokenizer
   reference; (b) land cumulative usage semantics and tokenizer profile
   references through a **reviewed new public contract version** per ADR-0047 —
   historical v0 artifacts are never mutated — refining ADR-0048 as follows:
   the new version is a separate generated tree with its own checksum gate;
   the frozen v0 generated tree, facade export, and `/v0/resolve` client are
   unchanged and coexist with the new-version client until the owning
   contract-version issue records migration; package-consumer negative gates
   are regenerated for both versions; activation and migration ownership lies
   with that owning issue, not with any carrier activation; (c) fix read
   semantics for
   Packages produced under `utf8-byte-budget-v1` (their accounting profile is
   recorded and preserved, never recounted); (d) make tokenizer identity part
   of ReleaseManifest compatibility, so a tokenizer change composes a new
   manifest generation through the sole promote path (ADR-0033); (e) refuse
   downgrade and mixed-generation resolves (a resolve never counts under two
   tokenizer identities). Until the migration completes, `utf8-byte-budget-v1`
   remains the explicit named accounting profile of the current surface — never
   an implicit default.
3. **One resolve-owned cumulative meter.** Each resolve creates exactly one
   `PackageBudgetMeter` over the effective budget intersection. Query rewrite,
   query embedding, rerank, selection, and assembly reserve their profile
   maximum before any provider byte, commit actuals, charge the frozen maximum
   when a call occurred but its result is unusable, and cancel on pre-call
   refusal. Metered dimensions are tokens, provider calls, cost, and elapsed,
   alongside the existing block/evidence/latency bounds. Stage-local meters,
   usage resets, and reconstruction of usage from block bytes alone are
   removed.
4. **Cumulative usage is published and digest-covered.** The final
   ContextPackage and its ContextRun publish the same cumulative meter usage;
   the package digest commits to that usage. A consumer can read the cumulative
   usage and verify the Package digest binds it; provider calls, cost, and
   elapsed are execution facts that a consumer cannot recompute from Package
   blocks alone, and this ADR does not claim otherwise. If independent
   verification of those facts is ever required, it is served by a separate
   restricted digest-only settlement proof admitted by its own decision, never
   by Package-body reconstruction.
5. **Accounting seam rules only.** Package-content tokenization and packing
   consume `AuthorizedProjection`s only, and count with the Release tokenizer
   exactly; non-content provider stages (query rewrite, query embedding) meter
   their closed request/response payloads under the same Release tokenizer and
   the same resolve-owned meter, accepting no `CandidateRef` and no
   unauthorized source content. Ordering follows the precedence fixed by
   ADR-0095 clause 5 (authorized ranking stage order, or the exact rerank
   permutation while the rerank carrier is active). Packing selection,
   trimming policy, and any gap semantics remain under the frozen v0 contract —
   whose `gaps` field stays empty — until a separate decision admits new
   packing policy through a new reviewed contract version. This clause adds no
   selection algorithm and no public-contract field.
6. **No retroactive recounting.** Delivered Packages and released manifest
   generations keep the accounting profile under which they were produced.
   Changing the tokenizer is a new manifest generation: it composes a new
   release candidate, re-evaluates, and promotes through the sole
   release-operator path (ADR-0033); it never mutates an active profile in
   place and never recounts history.
7. **Determinism evidence.** A network-free CI twin proves cross-process count
   equality under the pinned artifact; the activation record for the first
   tokenizer profile records the twin digest and the exact counting contract.
   Semantic equivalence to any provider tokenizer is out of scope — accounting
   determinism, not vendor parity, is the gate.
8. **Activation evidence.** Before any model-backed carrier reports cumulative
   usage as active, the owning issue registers executed evidence for: every
   clause-1 fail-closed refusal (missing, unknown, unavailable,
   hash-mismatched, carrier-mismatched, and mixed tokenizer identities, each
   with its own activation oracle); one-meter identity across rewrite,
   embedding, rerank, selection, and assembly (stage-local meters and usage
   resets are statically and dynamically rejected); concurrent reservation
   behavior (over-limit concurrent reserves admit exactly one; cancels do not
   leak reservations); maximum charging when a call occurred but its result is
   unusable; zero provider bytes before reservation; and exact equality of
   final Package and ContextRun cumulative usage with digest binding.

## Consequences

- Model-backed carriers gain a complete meter: every provider call, cost, and
  elapsed interval is reserved, settled, and published with the Package,
  closing the ADR-0067 and ADR-0068 revisit triggers on Runtime-enforced
  provider-call, cost, and elapsed accounting.
- One reviewed new contract version (tokenizer profile references plus
  cumulative usage semantics) happens before model-backed activation, rather
  than mutating v0 or migrating twice. The budget usage field shape already
  exists on v0; the migration changes its accounting profile binding and
  cumulative semantics, not its existence.
- `STATUS.md` reporting for `rewrite`/`rerank`/`select` can move from
  zero-filled usage to cumulative usage only after this profile is fixed.
- Byte-accounting history remains auditable: old Packages stay recomputable
  under `utf8-byte-budget-v1`.

## Revisit trigger

Revisit if a replacement tokenizer is required (new manifest generation, same
promote path), if cost accounting needs finer dimensions than calls/cost/
elapsed, if packing or gap policy is proposed (separate decision, new contract
version), or if multi-target determinism evidence forces a counting-contract
change.
