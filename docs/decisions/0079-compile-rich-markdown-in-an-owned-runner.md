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

V3 accepts both nonempty `---`-delimited frontmatter and thematic breaks, so it
must define how an initial `---` without a valid closing delimiter is
classified. Treating every leading `---` as frontmatter would make the accepted
thematic-break grammar depend on document position. Independent construction
testing also showed that v3 profile labels alone do not bind the exact
compiler/configuration identity, and a runner subprocess without a deadline
could fail to produce either an accepted document or a typed refusal.

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
2. **Closed rich grammar.** V3 accepts UTF-8 Markdown containing nonempty,
   `---`-delimited YAML frontmatter, ATX and setext headings, nested ordered or
   unordered lists,
   backtick or tilde fenced code blocks whose content may contain shorter fence
   runs, pipe tables, wikilinks, embeds, footnotes, HTML blocks, Obsidian
   callouts, ordinary blockquotes, inline math, emphasis/strong text, inline
   code, inline or reference links/images and reference definitions,
   strikethrough, angle-bracket literals, hard line breaks, thematic breaks,
   and ordinary paragraphs. Pipe tables retain
   ragged or empty rows as one exact atomic source block rather than silently
   truncating the document; typed cell metadata remains best-effort within
   that exact source block.
   A leading `---` opens frontmatter only when the first later `---` closes a
   nonempty YAML-shaped mapping or sequence payload. The complete delimiter
   matrix is closed as follows: a bare
   `---` is one thematic break; adjacent delimiters are two thematic breaks;
   delimiters separated only by blank lines remain thematic breaks; a nonempty
   YAML-shaped mapping or sequence payload with a closing delimiter is
   frontmatter; the same payload without a closing delimiter is ordinary
   content after the leading thematic break; ordinary prose between delimiters
   is a leading thematic break followed by ordinary Markdown (so a closing
   `---` may serve as its setext underline); and adjacent delimiters followed
   by text remain two thematic breaks followed by ordinary content. CRLF and LF
   forms have identical grammar, and an optional leading BOM changes none of
   these classifications. The mapping/sequence check is lexical rather than
   semantic YAML interpretation. This delimiter-complete rule preserves both
   constructs and does not infer metadata from empty payloads or ordinary
   following prose.
   A bare unmatched fence-marker line plus following nonblank text is retained
   as one exact literal paragraph block; an empty or language-bearing
   unterminated fence remains a typed refusal.
   These additional bounded constructs are explicit because the real-corpus
   acceptance gate showed they are required to close the measured v1 gap; raw
   syntax remains exact source text and is not re-rendered. CRLF, lone CR, and
   LF are treated uniformly for grammar recognition and line/column
   calculation, but exact decoded source bytes are retained so every byte span
   round-trips against the original UTF-8 input and representation digests
   distinguish distinct inputs. Frontmatter payload bytes are retained but not
   interpreted or used as authority; semantic YAML validation, CommonMark
   compatibility, and unrestricted HTML compatibility are not claimed. An
   optional leading UTF-8 BOM is retained for representation identity and
   treated only as a transport marker preceding the first Fragment.
3. **Existing output contracts.** The runner emits and deserializes only the
   existing `ParsedDocument`, `CompiledFragment`, `SourceSpan`,
   `StructuralPath`, `CompilationProvenance`, or typed `CompilationFailure`
   contracts. Rich constructs are projected into those existing section kinds;
   no parallel document or Fragment model is introduced.
4. **Exact provenance.** Every Fragment carries an end-exclusive UTF-8 byte,
   line, and column span. Its `source_text` is exactly the original input byte
   slice at that span. Non-whitespace source bytes cannot be omitted. Table
   Fragments obey the same round-trip rule. Heading ancestry is derived during
   compilation and copied into the same budget-visible Fragment. The v3 domain
   constructor independently re-derives the closed source grammar, typed
   section metadata, coordinates, paths, parent headings, stable Fragment
   references, search phrases, contextual text, and the provenance-bound
   ceiling; parser-supplied metadata cannot bypass those checks.
   Rich provenance binds the exact `context-engine-markdown-v3` compiler and
   `markdown-config-v3` configuration identifiers as well as the v3 profiles.
   The self-validating domain constructor rejects older or arbitrary identities.
   The constructor and parser share only the closed control-character
   classifier: C0 controls other than tab and line endings, DEL, and C1 controls
   are refused. The constructor invokes it independently over exact source, so
   fenced-code metadata cannot bypass a parser-ingress check.
5. **Hard splitting gate.** V3 uses a representation-bound 2,048-token default
   ceiling, recorded in both configuration and compilation provenance. The
   deterministic counter treats each non-whitespace run as one token. A block
   whose contextual text would exceed the ceiling is split at source token
   boundaries into ordered Fragments; every part retains the same preceding
   heading ancestry, exact source span, and structural lineage. If heading
   ancestry alone leaves no capacity, or an indivisible construct cannot be
   represented within the ceiling, compilation refuses all-or-nothing.
6. **Owned pure process.** This issue delivers an unleased local/acceptance
   process only. It receives exact source bytes and configuration, executes the
   registered RAGFlow element-recognition helpers plus the ContextEngine-owned
   grammar, hierarchy, raw-span, and bounds kernel, and emits deterministic
   bytes. The copied upstream file remains deliberately unmodified so its hash
   stays independently auditable; the adapter calls its fence and table
   recognition methods directly. ContextEngine rewrites rich construct
   classification, exact raw-byte position mapping, ancestry, typed output,
   and splitting because the upstream return shape cannot express those
   contracts. The process performs no network or database I/O and retains no
   independent state, cache, index, or checkpoint. Both the direct compiler
   seam and subprocess envelope convert unexpected parser or domain-constructor
   rejection into a typed all-or-nothing `CompilationFailure`; no partial
   document or raw exception crosses the runner boundary.
7. **Activation remains deferred.** This decision proves the v3 pure transform
   and local acceptance reporting only. The active File import configuration,
   database publication functions, immutable Revision schemas, embeddings,
   and existing v1 Revision migration remain unchanged. Activating v3 for
   production publication requires a separate decision and complete atomic
   publication and re-embedding evidence. The Supply execution bridge in issue
   #125 owns the future production invocation and must bind it to the exact
   parent WorkerLease. No production module may call this issue's unleased
   local subprocess helper.
8. **Runtime remains sealed.** Compilation changes no Runtime composition.
   After any future v3 activation, each published Fragment is still only a
   candidate until it crosses the exact
   `CandidateRef -> AuthorizationKernel -> AuthorizedProjection` path.

## Consequences

- Delimiter completeness makes the two already accepted `---` meanings
  deterministic without changing v1 or v2. A document beginning with an
  unclosed `---` compiles that line as a thematic break; complete nonempty
  frontmatter retains its exact raw span.
- Exact identity binding keeps stored provenance interpretable. Forged v3
  documents cannot substitute an older or arbitrary compiler/config identity
  while retaining rich profiles. Sharing the finite character predicate
  prevents drift without trusting any parser-provided structure, span, or
  construct metadata.
- The local compiler-runner call has a fixed positive timeout. Child timeout or
  launch failure produces the same content-free typed boundary failure as any
  other subprocess-boundary failure; an unbounded wait is not an outcome. A
  positive deadline completes the all-or-nothing process contract even when a
  child wedges before emitting bytes.
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
state, changing the same-Fragment ancestry rule, changing leading delimiter
disambiguation or v3 identity binding, or making the fixed subprocess timeout a
caller-controlled runtime parameter.
