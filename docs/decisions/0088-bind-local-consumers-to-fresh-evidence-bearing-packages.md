---
name: adr-0088-bind-local-consumers-to-fresh-evidence-bearing-packages
version: "1.0.0"
description: >
  Require each local read-only consumer question to acquire and render one
  fresh expiring loopback Package with intact Block/Evidence lineage and no
  persisted dogfood bearer. Use when a local read-only consumer answers one
  question from authorized context. Not for Package reuse, post-expiry use,
  stripped Evidence lineage, or persisted consumer secrets.
---

# 0088. Bind local consumers to fresh evidence-bearing Packages

- Status: accepted
- Date: 2026-07-30
- Refines: ADR-0047, ADR-0062, ADR-0063, ADR-0068

## Context

The first real consumer of the loopback dogfood Runtime is a repository-local
Claude Code skill. It needs no new engine surface: the frozen
`POST /v0/resolve` Acquire operation and the minimal caller already exercise the
same sealed Runtime path that produces the sole online deliverable,
`ContextPackage`.

The wire contract cannot enforce what a local consumer does after delivery. A
consumer could reuse a request-scoped Package for a later question, use it after
expiry, strip the one-to-one Block/Evidence lineage when presenting context to
the agent, or expose the dogfood bearer through its invocation or session state.
Those failures do not bypass the Kernel, but they would make the first dogfood
consumer violate the Package contract and turn local transcripts into a secret
store. The same obligations will bind any later local consumer, including a pi
extension, even though pi is not scheduled now.

## Decision

The Claude Code skill is a local read-only consumer of the existing explicit
loopback dogfood composition. It adds no Adapter, transport, authentication
composition, trusted delivery fact, egress hop, or external effect. It invokes
only Acquire through `POST /v0/resolve`; purpose remains the server-fixed
`context.answer` value.

Every local read-only consumer must satisfy all four obligations below.

1. **One fresh resolve per question.** Each distinct user question creates one
   new Acquire request with a fresh request id. A Package, `packageId`, response
   object, rendered block, or other Package-derived state from one question is
   never cached, resumed, or reused for another question. Session continuity is
   not Package authority.
2. **Honor `expiresAt`.** The consumer checks the returned Package expiry before
   using any Block and never uses or presents Package-derived context at or after
   `expiresAt`. Rendered context states the expiry. An already expired Package is
   discarded and produces the closed local refusal; it is not silently treated
   as current or refreshed from retained Package state.
3. **Retain citation lineage in agent output.** The consumer accepts only the
   frozen Package closure in which every Block has exactly one `evidenceRefs`
   member naming Evidence in that Package. It renders each Block's text together
   with that single `evidenceRef`, and any agent answer that uses the Block keeps
   the ref attached as its citation. A missing, extra, unknown, or stripped ref
   rejects the Package rather than becoming a formatting choice. A non-null
   `citationOpenRef` may be displayed only as lineage; dogfood `OpenCitation`
   remains `NOT_ACTIVE`.
4. **Never persist or render the dogfood secret.** The bearer is read only by the
   existing caller from its environment-held configuration. Its value never
   appears in the skill file, command arguments, rendered output, tool results,
   logs, errors, persisted session state, or any Package-derived cache. The skill
   may name the environment contract but must not expand, print, copy, or pass
   the value. If that exclusion cannot be maintained, the consumer refuses to
   invoke the Runtime.

A `request_not_available` outcome, an expired Package, malformed Block/Evidence
closure, or a Package with no authorized Blocks produces an explicit local
refusal that says authorized context is unavailable for this question. It must
not be presented as an empty successful answer or as evidence that the corpus
contains nothing.

This category ends when a consumer forwards Package content to an IM channel or
performs an external effect. Such a caller is governed by BotDelivery,
`DeliveryEvidenceRef`, per-hop `EgressGrant`, and ActionPlane instead. The local
display consumer neither redeems nor spends the optional egress grant in the
resolve envelope.

The shared Agent Skills format may let a later pi consumer reuse this contract,
but pi remains pulled-not-scheduled. MCP remains `NOT_ACTIVE`; this decision
creates no MCP Adapter or conditional MCP ADR, and `Continue` remains
`NOT_ACTIVE` in the dogfood carrier.

## Rationale

A consumer-side skill is the smallest real workload for ADR-0062 because it
reuses the frozen loopback ingress and the maintainer's daily question flow.
Making the four post-delivery obligations explicit and testable contains the
main risk of a prose-level shim without creating a second credential holder or
authorization path.

Fresh acquisition and expiry refusal preserve the request-scoped nature of a
Package. Keeping each Block adjacent to its exact Evidence ref preserves the
authorization and provenance lineage that makes delivered text usable as
context rather than unattributed prose. Environment-only secret handling keeps
the local session and its durable transcript outside the bearer boundary.

## Consequences

- The first local consumer can generate dogfood questions through the existing
  API without changing OpenAPI v0 or the sealed Runtime.
- The skill implementation must prove fresh-question acquisition, expiry
  refusal, complete Block/Evidence rendering, and secret absence from both its
  content and prescribed invocation.
- A local refusal can occur even after a successful HTTP response when the
  Package is expired or its consumer-visible closure is invalid.
- Package reuse, citation-free rendering, and convenience arguments containing
  the bearer are contract violations, not optional consumer behavior.
- This decision activates no pi, MCP, `Continue`, `OpenCitation`, BotDelivery,
  channel egress, or external effect carrier.

## Revisit trigger

Revisit when a second kind of local consumer needs a shared executable library,
when three or more local shims demonstrate contract drift, when pi becomes a
real daily-use consumer, or when a real MCP-native caller justifies an Adapter.
Any replacement must preserve fresh per-question authorization, expiry refusal,
Block/Evidence lineage, and non-persistence of every credential.
