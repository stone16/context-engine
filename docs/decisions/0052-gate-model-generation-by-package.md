---
name: adr-0052-gate-model-generation-by-package
version: "1.0.0"
description: >
  Permit private BotDelivery generation only from one current audience-bound
  ContextPackage and an exactly matching one-shot model EgressGrant.
---

# 0052. Gate model generation by one authorized Package

- Status: accepted
- Date: 2026-07-24
- Refines: ADR-0002, ADR-0006, ADR-0011, ADR-0012, ADR-0017, ADR-0031, ADR-0045, ADR-0046, ADR-0048, ADR-0049, ADR-0050

## Context

The engine deliberately delivers context rather than answers. BotDelivery may
generate an answer only after the engine has returned one complete,
audience-bound `ContextPackage`; allowing arbitrary prompt context, individual
projections, candidates, multiple Packages, or a Package without final egress
authority would create a second disclosure path outside the sealed Runtime.

The earlier EgressGrant decision establishes exact one-shot authority, but a
production-shaped BotDelivery boundary must also constrain the provider request,
model output, usage, audit, and package distribution. This first carrier must be
testable without implying that a real model provider, streaming, group delivery,
or action effect is active.

## Decision

The trusted Bot application contains a private TypeScript BotDelivery module.
It imports only the generated SDK contract, never engine internals. Its
`prepareAuthorizedModelInput` factory accepts exactly one complete current
`ContextPackage`, one opaque model `EgressGrant`, a closed question envelope,
one versioned private model profile, and the trusted local time. It validates
the exact frozen Package shape and digest, sufficient coverage, one-to-one
Block/Evidence closure, Evidence lineage, audience, purpose, Policy Epoch,
expiry, and input limits, then creates a nominal, redacted, non-serializing
`AuthorizedModelInput`. Callers cannot construct or recover its private state.
Live, Mirrored, and Weak SourceAclEvidence are validated against the exact
public OpenAPI union. Weak evidence additionally requires an ordered
snapshot/check/package timeline, a proof not expired at preparation time, and
a proof expiry no earlier than Package expiry; malformed or shorter-lived
proofs cannot cross the model boundary.

The boundary has no public constructor or database injection interface. The
sealed `createPrivateModelGenerationBoundary` factory creates and owns its
concrete PostgreSQL pool; a package consumer can supply neither a structural
`query` object nor an accepted/recorded result. The resulting boundary accepts
only that nominal value, the same grant, and an exact deterministic
`ModelGateway` twin owned by this module. Immediately before provider bytes it calls
the existing function-only grant redemption with exact Organization, Package,
payload, audience, purpose, Policy Epoch, provider, model, region, retention,
sensitivity, issuer, consumer, lifetime, hop, and profile bindings. Mismatch,
staleness, expiry, wrong hop, database failure, or replay returns one generic
`generation_not_available` outcome with zero provider bytes.

The provider request contains only the authorized Package Block text and
Evidence refs plus the declared question and instructions. It contains no grant,
trusted identity, audience digest, decision audit, denied detail, arbitrary
extra context, or transport credential. The deterministic twin is not a public
provider interface and cannot be subclassed or directly invoked to obtain
outbound behavior.

Every digest-bearing TypeScript document is constrained to exact JSON values,
finite IEEE 754 numbers, dense arrays, enumerable data properties, and Unicode
scalar strings and keys before RFC 8785 encoding. One shared Python/TypeScript
fixture fixes Unicode, UTF-16 property order, numeric edge, and lone-surrogate
rejection behavior so the two digest authorities cannot silently diverge. The
wire-level `packageDigest` is excluded before hashing; any nested digest field
is rejected in both runtimes rather than silently excluded.

The module registers one exact versioned profile for the activated deterministic
carrier and exposes only its zero-argument factory; callers cannot construct a
profile with the same reference but different limits or bindings. That profile bounds input, question, instruction,
provider-call count, cost, elapsed time, and output bytes. A generated answer is released only when
its citations are unique members of that Package's Evidence and have matching
opaque `CitationOpenRef` values. The answer exposes only text, citations, a
canonical answer-payload digest, and bounded usage. It cannot contain an effect
intent, operation, destination, audience, ticket, or ActionPlane bypass; any
later effect must independently canonicalize and bind the answer during
`ActionPlane.prepare`.

After grant consumption, PostgreSQL records exactly one restricted outcome row
before a successful answer is released. FORCE RLS storage contains only grant,
Package, input, question, and optional answer digests; category; bounded usage;
profile lineage; database time; and the fixed retention deadline. It contains
no bearer, Package content, question, answer text, identity, or denied detail.
Only the dedicated egress definer can record it, and only the restricted
security operator can delete one Organization's expired rows.

Issue #70 activates only the deterministic private ModelGateway carrier. Real
provider network calls, streaming, group/public audience generation, complete
BotDelivery orchestration, and external effects remain `NOT_ACTIVE`.

## Rationale

Making `ContextPackage` the sole content input preserves the engine's one online
deliverable and keeps provider egress downstream of final authorization. Exact
database redemption makes the grant—not JavaScript object possession—the
disclosure authority. A closed provider request and digest-only outcome audit
make the active carrier measurable while minimizing content and secret spread.

## Consequences

- Installed generated-SDK output can feed installed BotDelivery without a
  handwritten duplicate wire schema.
- Every denied binding and replay is observable as zero provider bytes and the
  same generic outcome.
- Output citations remain openable references to Evidence from exactly one
  Package; the model cannot invent source authority.
- Audit failure suppresses a generated answer after provider work rather than
  releasing an unaudited result.
- This boundary produces no external effect authority; ActionPlane remains the
  only path for mutations.

## Revisit trigger

Revisit before enabling a real provider, streaming or cancellation, multiple
Package composition, group/public generation, another retention/profile
version, or any provider output that must cross into ActionPlane.
