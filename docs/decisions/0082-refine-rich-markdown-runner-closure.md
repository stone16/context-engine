---
name: adr-0082-refine-rich-markdown-runner-closure
version: "1.0.0"
description: >
  Refine rich-Markdown frontmatter disambiguation, provenance identity, and
  bounded runner termination without reopening the v3 representation.
---

# 0082. Refine rich Markdown runner closure

- Status: accepted
- Date: 2026-07-30
- Refines: ADR-0079

## Context

ADR-0079 accepts both nonempty `---`-delimited frontmatter and thematic breaks,
but it did not define how an initial `---` without a valid closing delimiter is
classified. Treating every leading `---` as frontmatter made the accepted
thematic-break grammar depend on document position. Independent construction
testing also showed that v3 profile labels alone did not bind the exact
compiler/configuration identity, and a runner subprocess without a deadline
could fail to produce either an accepted document or a typed refusal.

## Decision

1. A leading `---` opens frontmatter only when a later `---` closes a nonempty
   payload. Without that closing delimiter, the leading line is an accepted
   thematic break. This delimiter-complete rule preserves both constructs in
   the closed grammar and does not infer metadata from ordinary following prose.
2. Rich provenance binds the exact `context-engine-markdown-v3` compiler and
   `markdown-config-v3` configuration identifiers as well as the v3 profiles.
   The self-validating domain constructor rejects older or arbitrary identities.
3. The local compiler-runner call has a fixed positive timeout. Child timeout
   or launch failure produces the same content-free typed boundary failure as
   any other subprocess-boundary failure; an unbounded wait is not an outcome.
4. The constructor and parser share only the closed control-character
   classifier: C0 controls other than tab and line endings, DEL, and C1 controls
   are refused. The constructor invokes it independently over exact source, so
   fenced-code metadata cannot bypass a parser-ingress check.

## Rationale

Delimiter completeness makes the two already accepted `---` meanings
deterministic without changing v1 or v2. Exact identity binding keeps stored
provenance interpretable, while a positive deadline completes ADR-0079's
all-or-nothing process contract even when a child wedges before emitting bytes.
Sharing the finite character predicate prevents drift without trusting any
parser-provided structure, span, or construct metadata.

## Consequences

- A document beginning with an unclosed `---` compiles that line as a thematic
  break; complete nonempty frontmatter retains its exact raw span.
- Forged v3 documents cannot substitute an older or arbitrary compiler/config
  identity while retaining rich profiles.
- Local compiler-runner callers receive a typed content-free failure after the
  bounded deadline instead of waiting indefinitely.

## Revisit trigger

Revisit before changing leading delimiter disambiguation, v3 identity binding,
or the fixed subprocess timeout into a caller-controlled runtime parameter.
