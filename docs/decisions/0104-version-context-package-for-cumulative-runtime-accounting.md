---
name: adr-0104-version-context-package-for-cumulative-runtime-accounting
version: "1.0.0"
description: >
  Admit a digest-bound tokenizer profile and a public v1 ContextPackage whose
  one resolve-owned meter publishes cumulative Runtime accounting. Use when
  producing or consuming v1 Packages. Not for recounting v0 history, changing
  packing policy, answer generation, or activating network model providers.
---

# 0104. Version ContextPackage for cumulative Runtime accounting

- Status: accepted
- Date: 2026-08-05
- Refines: ADR-0031, ADR-0033, ADR-0047, ADR-0048, ADR-0067, ADR-0095,
  ADR-0096, ADR-0098, ADR-0102
- Related: [issue #217](https://github.com/stone16/context-engine/issues/217)

## Context

The frozen public v0 Package defines `utf8-byte-budget-v1`: its `tokens` equal
the UTF-8 bytes of selected blocks and its other usage fields omit the active
local query-embedding ledger. That contract is truthful within its admitted
carrier boundary, but cannot acquire cumulative Runtime meaning in place.
Meanwhile ADR-0102 enforces query-embedding limits with a temporary stage-local
meter and discards its settlement before Package and ContextRun construction.

ADR-0096 requires an immutable Release tokenizer identity, one resolve-owned
meter, digest-covered cumulative usage, and explicit failure evidence. It also
forbids changing selection and packing as part of accounting migration. The
version boundary must therefore preserve v0 bytes and history while making v1
semantics unambiguous.

## Decision

1. **Immutable tokenizer lineage.** `RuntimeProfileRef` binds a canonical
   tokenizer-profile document and SHA-256 profile digest as well as its opaque
   ref. The profile binds the artifact digest, vocabulary identity,
   normalization identity, accounting version, and exact counting contract.
   The first admitted v1 profile is a network-free Unicode-scalar tokenizer:
   one Unicode scalar is one token, with no normalization. Its small canonical
   artifact is shipped in the wheel and hash-verified before use. Missing,
   unknown, unavailable, or hash-mismatched profiles refuse. A carrier ref or
   digest mismatch, or a different active Release generation on the same meter,
   refuses before reservation and before provider bytes. No byte/provider/cached
   fallback exists.
2. **Separate public v1.** `POST /v1/resolve` and its generated v1 SDK package
   expose the reviewed cumulative contract. `ContextPackageV1Wire` adds the
   required `tokenizerProfileDigest`; `budgetUsage` means the cumulative
   resolve ledger. Its Package digest covers both. The v1 OpenAPI snapshot,
   generated tree, facade, checksum, negative consumer gates, and package name
   are independent. The frozen `openapi/v0`, v0 generated tree, v0 facade,
   checksum, package identity, and `/v0/resolve` client stay byte-for-byte
   unchanged.
3. **Profile-matched coexistence.** One HTTP handler and one sealed Runtime
   composition serve both paths. Before Runtime work, ingress requires the
   current Release's package schema to match the requested public version.
   Mismatch returns generic service unavailable before provider bytes. There is
   no v1-to-v0 projection of a cumulative resolve and no v0-to-v1 recount.
4. **One ledger.** After the effective PackageBudget intersection is fixed,
   Runtime creates exactly one `PackageBudgetMeter`, bound to the active
   tokenizer profile and Release generation. The same nominal instance crosses
   rewrite, query embedding, rerank, selection, and assembly seams. Active
   stages reserve maxima before provider bytes; actual success commits actuals;
   an unusable post-call result commits the maximum; a pre-call refusal cancels.
   The active query-embedding stage counts the provider-profile-prefixed query
   request and its exact validated 384-dimensional float32 response. The
   response accounting representation is the big-endian float32 bytes encoded
   with standard Base64 in canonical compact
   `{"embedding":["<base64>"]}` JSON; its size is known before the call and
   therefore belongs in the reservation maximum.
   Final assembly reserves and commits exact authorized block tokens only after
   the final Policy Epoch veto. UTF-8 byte packing remains unchanged.
5. **One final snapshot.** Runtime takes one immutable meter snapshot after
   assembly, constructs and digests the v1 Package from it, and builds
   ContextRun only from that Package. Package and ContextRun therefore expose
   identical tokens, calls, cost, and elapsed, and any usage mutation changes
   the Package digest. Stage-local meters, resets, and usage reconstruction from
   block bytes are rejected by static and dynamic activation tests.
6. **History and migration.** Existing `utf8-byte-budget-v1` Release generations,
   Packages, Package digests, and ContextRuns remain readable under their
   original semantics and are never recounted or rewritten. A tokenizer change
   always creates and promotes a new manifest generation through ContextLearning.
   Consumers have a coexistence window through **2026-11-05** to move to the v1
   SDK. On that date v0 resolve becomes eligible for retirement only through a
   separate accepted ADR with observed migration evidence; this ADR does not
   delete it.
7. **Excluded activations.** Rewrite, rerank, model selection, answer generation,
   and external/network embeddings remain `NOT_ACTIVE` unless their owning ADR
   activates them. This change activates cumulative reporting for the already
   accepted local query embedding and deterministic final assembly only.

## Migration and verification plan

1. Add tokenizer profile bindings to Release domain, persistence, active
   observation, and migration defaults. Existing rows receive only the explicit
   historical byte profile; no usage row is recalculated.
2. Add v1 Package/domain projection and route, freeze its OpenAPI, generate the
   independent SDK, and retain recursive negative consumer gates for each
   version.
3. Replace the ADR-0102 stage-local meter with the resolve-owned meter, tokenize
   the closed query payload, and settle assembly after finalization.
4. Execute clause-8 oracles for all tokenizer failures, carrier/generation
   mismatch, meter identity, static reset rejection, reservation concurrency,
   cancel, maximum charge, zero pre-reservation provider bytes, Package/run
   equality, and digest binding at HTTP/generated-SDK seams.
5. Rehearse migration and downgrade. Downgrade refuses while any v1 tokenizer
   lineage is retained; historical byte-profile rows remain intact.

## Consequences

- Consumers can treat v1 usage as the complete active Runtime total without
  inferring execution facts from Package text.
- The first tokenizer is deliberately simple and deterministic; changing its
  quality or semantics is a promoted Release change, not an in-place library
  upgrade.
- During coexistence, an Organization Release selects one Package version at a
  time. This constraint rules out ambiguous dual accounting for one resolve.

## Revisit trigger

Revisit for v0 retirement after the deadline and migration evidence, for a new
tokenizer through the promote path, for a packing-policy change, or for any
external/network model provider activation. None may weaken the one-meter or
zero-provider-byte refusal invariants.
