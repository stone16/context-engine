---
name: adr-0079-compile-rich-markdown-in-an-owned-runner
version: "1.0.0"
description: >
  Add an explicit rich-Markdown v3 representation behind a pure owned
  compiler-runner while keeping frozen v1/v2 publication inactive.
---

# 0079. Compile rich Markdown in an owned runner

- Status: accepted
- Date: 2026-07-29
- Refines: ADR-0038, ADR-0074, ADR-0075

## Context

The frozen Markdown v1 and v2 representations deliberately reject syntax used
by the measured File corpus. ADR-0038 requires a new decision before accepting
another construct or adding size-based splitting. It also requires any
replacement to retain version-explicit compilation, exact source provenance,
all-or-nothing failure, atomic immutable publication, budget-visible context,
and the sealed Runtime authorization order.

ADR-0074 permits a pinned, registered copy of RAGFlow's Apache-2.0 Markdown
parser region after path-level license and nested-dependency verification.
ADR-0075 permits Supply work in a ContextEngine-owned runner subprocess only
when the runner is a pure transform: it owns no persistence, cache, corpus, or
index and executes under the exact parent WorkerLease binding.

## Decision

1. **Explicit v3 representation.** `markdown-config-v3` selects
   `context-engine-markdown-v3` with its own canonicalization and compilation
   digest profiles. V1 and v2 remain frozen and byte-reproducible; v3 never
   silently reinterprets either version.
2. **Closed rich grammar.** V3 accepts UTF-8 Markdown containing YAML
   frontmatter, ATX and setext headings, nested ordered or unordered lists,
   backtick or tilde fenced code blocks whose content may contain shorter fence
   runs, pipe tables, wikilinks, embeds, footnotes, HTML blocks, Obsidian
   callouts, inline math, and ordinary paragraphs. CRLF and lone CR input are
   normalized to LF before positions and digests are derived. CommonMark or
   unrestricted HTML compatibility is not claimed.
3. **Existing output contracts.** The runner emits and deserializes only the
   existing `ParsedDocument`, `CompiledFragment`, `SourceSpan`,
   `StructuralPath`, `CompilationProvenance`, or typed `CompilationFailure`
   contracts. Rich constructs are projected into those existing section kinds;
   no parallel document or Fragment model is introduced.
4. **Exact provenance.** Every Fragment carries an end-exclusive UTF-8 byte,
   line, and column span. Its `source_text` is exactly the canonical input byte
   slice at that span. Non-whitespace source bytes cannot be omitted. Table
   Fragments obey the same round-trip rule. Heading ancestry is derived during
   compilation and copied into the same budget-visible Fragment.
5. **Hard splitting gate.** V3 uses a representation-bound token ceiling. The
   deterministic counter treats each non-whitespace run as one token. A block
   whose contextual text would exceed the ceiling is split at source token
   boundaries into ordered Fragments; every part retains the same preceding
   heading ancestry, exact source span, and structural lineage. If heading
   ancestry alone leaves no capacity, or an indivisible construct cannot be
   represented within the ceiling, compilation refuses all-or-nothing.
6. **Owned pure process.** The compiler-runner receives exact source bytes and
   configuration, invokes the registered vendored parser region plus the
   ContextEngine hierarchy/span/bounds kernel, and emits deterministic bytes.
   It performs no network or database I/O and retains no independent state,
   cache, index, or checkpoint.
7. **Activation remains deferred.** This decision proves the v3 pure transform
   and local acceptance reporting only. The active File import configuration,
   database publication functions, immutable Revision schemas, embeddings,
   and existing v1 Revision migration remain unchanged. Activating v3 for
   production publication requires a separate decision and complete atomic
   publication and re-embedding evidence.
8. **Runtime remains sealed.** Compilation changes no Runtime composition.
   After any future v3 activation, each published Fragment is still only a
   candidate until it crosses the exact
   `CandidateRef -> AuthorizationKernel -> AuthorizedProjection` path.

## Consequences

- Rich-note and hard-bound behavior can be verified without creating durable
  content or adding another persistence truth.
- Deterministic source spans and heading context remain part of the same
  Fragment and therefore visible to existing provenance and Package budgets.
- The first controlled RAGFlow reuse pays its own pinned registration and
  dependency-audit cost; artifact-wide aggregation remains owned by its
  separately accepted work.
- Production File imports continue to use their current frozen representation
  until activation and migration are decided explicitly.

## Revisit trigger

Revisit before accepting a construct outside the closed v3 grammar, changing
the token counter or ceiling, splitting an indivisible construct differently,
activating v3 publication, migrating an existing Revision, adding runner-local
state, or changing the same-Fragment ancestry rule.
