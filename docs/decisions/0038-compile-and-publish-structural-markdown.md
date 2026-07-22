---
name: adr-0038-compile-and-publish-structural-markdown
version: "1.0.0"
description: >
  Version Markdown compilation so logical structural units retain exact source
  provenance and same-Fragment heading ancestry through atomic publication.
---

# 0038. Compile and publish structural Markdown units

- Status: accepted
- Date: 2026-07-23
- Refines: ADR-0018, ADR-0036, ADR-0037

## Context

The first File tracer compiled only one heading plus one paragraph and published
only the paragraph. Issue #24 needs headings, paragraphs, flat lists, fenced
code blocks, and pipe tables without losing exact source positions or the
heading context needed to understand a retrieved child unit. Treating a list
item, code line, or table cell as an independently delivered Fragment would
produce incoherent blocks. Retrieving a small child and expanding to a separate
parent after authorization would introduce another content-bearing access path.

The Issue #22 v1 canonical bytes are already a compatibility contract. Extending
that grammar under the same version would make an unchanged source compile to a
different representation without an explicit identity change.

## Decision

`markdown-config-v1` continues to dispatch to the exact frozen
`context-engine-markdown-v1` grammar and serialization. The structural grammar
is an explicit `markdown-config-v2` and `context-engine-markdown-v2` contract
with new canonicalization and compilation-digest profiles. Unknown versions
fail before parsing.

Version 2 accepts only:

- one level-one ATX heading followed by source-ordered ATX headings that do not
  skip a hierarchy level;
- contiguous plain lines as one paragraph;
- one flat ordered or unordered list as one list unit;
- one non-empty triple-backtick fenced code block with an optional bounded
  language token; and
- one pipe table with a header, plain hyphen delimiter, and at least one
  width-matched row.

Every heading and every content block is one typed section and exactly one
Fragment. A list, fenced code block, or table remains atomic. Each value carries
a deterministic structural path and an end-exclusive line, column, and UTF-8
byte span into canonical source. The compiler retains both exact `sourceText`
and typed unit metadata. Its self-validating domain constructor re-derives
source text, coordinates, paths, parent headings, stable Fragment references,
and search phrases; parser-provided metadata cannot bypass the closed grammar.

`contextualText` is the exact source unit preceded by its ordered parent heading
ancestry. That ancestry is copied into the same Fragment before publication. It
is not a separate parent Fragment lookup or a post-authorization expansion.
Consequently the whole delivered block has one Resource, Revision, Fragment,
access decision, field projection, provenance record, and Package budget cost.
Candidate discovery may hash the whole source unit plus exact list items, code
body, and table cells, but every digest points back to that coherent Fragment.

The worker stores the complete v2 canonical compilation document on the
immutable Revision snapshot and publishes every Fragment, all exact-phrase
candidates, access rights, ordered events, active pointer, and terminal job
state in one PostgreSQL transaction. The original v1 publication function stays
available for frozen v1 documents; a separate version-bound structural
function accepts only v2 profiles. Its worker role remains function-only and
each resulting `CandidateRef` still crosses the sealed
`AuthorizationKernel -> AuthorizedProjection` path before content is read.

Any construct or shape outside the listed grammar returns a typed all-or-nothing
failure. In particular nested lists, alternative list markers, blockquotes,
indented or tilde code blocks, table alignment syntax, links, emphasis, inline
code, HTML, entities, escapes, and control characters are not reinterpreted as
plain content. The schema downgrade refuses to discard an existing v2 snapshot.

## Rationale

Logical-unit Fragments preserve reading coherence without activating general
chunking, hydration, or parent expansion. Copying heading ancestry during pure
compilation keeps Runtime's security path unchanged: retrieval identifies one
candidate and authorization governs the complete content-bearing value.
Explicit compiler and database-function versions make representational changes
observable while retaining old deterministic artifacts.

## Consequences

- Frozen fixtures and literal digests cover each supported unit and a combined
  hierarchy; two fresh interpreters continue to prove deterministic bytes.
- Parent heading text consumes the same Package budget as its child unit. There
  is no hidden or unbudgeted context.
- The immutable snapshot contains source-bearing compiler provenance and is not
  exposed to Runtime or tenant callers.
- CommonMark compatibility is not claimed. Inline formatting, nested blocks,
  table escaping/alignment, setext headings, HTML, and parent/neighbor hydration
  remain unavailable.

## Revisit trigger

Revisit before accepting another Markdown construct, adding size-based chunking
or parent/neighbor hydration, changing contextual-text composition, indexing a
new derived phrase, or migrating existing v1 Revisions. A replacement must keep
version-explicit compilation, exact source provenance, all-or-nothing failure,
atomic immutable publication, budget-visible context, and the sealed
`CandidateRef -> AuthorizationKernel -> AuthorizedProjection` boundary.
