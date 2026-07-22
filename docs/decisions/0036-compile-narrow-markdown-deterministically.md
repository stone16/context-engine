---
name: adr-0036-deterministic-narrow-markdown-compiler
version: "1.0.0"
description: >
  Fix the first pure Markdown compiler to one heading-plus-paragraph shape,
  canonical normalized text, end-exclusive positions, and versioned digests.
---

# 0036. Compile the first Markdown shape from canonical bytes

- Status: accepted
- Date: 2026-07-22
- Refines: ADR-0016, ADR-0018

## Context

The first File tracer bullet needs a reproducible compiler result before any
`ContextRevision`, `ContextFragment`, persistence, indexing, or publication
behavior is activated. Passing a path would couple compilation to acquisition
and host state. Treating unsupported Markdown syntax as ordinary paragraph text
would make later parser upgrades silently change meaning. A content-only hash
also cannot identify which compiler/configuration semantics produced the typed
structure.

Source positions need one stable coordinate system after BOM and newline
normalization. Original byte offsets cannot remain comparable when CRLF becomes
LF, while character offsets alone are insufficient for exact serialized
provenance.

## Decision

Supply owns the typed `ParsedDocument`, failure, provenance, serialization, and
digest contracts. The pure `adapters/parsers` implementation exposes
`compile_markdown(bytes, MarkdownCompilerConfig)` and imports those inward-facing
contracts; the shared domain never imports parser implementation. The adapter
accepts exact UTF-8 bytes, removes at most one leading UTF-8 BOM, normalizes CRLF
and CR to LF, and canonicalizes trailing newlines to exactly one LF. The only
successful grammar is one level-one ATX heading, one blank line, and one plain
single-line paragraph. All source positions refer to the canonical normalized
UTF-8 text: line and column are one-based, byte offset is zero-based, and spans
are end-exclusive.

Invalid UTF-8 and every recognized out-of-scope Markdown construct return a
typed all-or-nothing `CompilationFailure`. They never return a partial
`ParsedDocument`. The current compiler emits an exact empty warning tuple; it
does not claim any lossy warning behavior.

The closed-grammar classifier is a Supply-domain invariant shared by the
adapter and `ParsedDocument` self-validation. Parser ingress is therefore not
the only enforcement point: direct typed construction cannot manufacture a
valid digest for content that the active narrow grammar rejects.

`ParsedDocument` contains canonical text, source-ordered typed sections,
structural paths, canonical source spans, compiler/configuration provenance,
and two distinct SHA-256 identities:

1. `content_hash` hashes canonical normalized UTF-8 only, so BOM/newline
   transport variants share content identity; and
2. `compilation_digest` hashes an RFC 8785 canonical document under a domain
   separator and includes structure, positions, warnings, compiler version,
   configuration version, profiles, and content hash.

The public canonical serialization adds the verified compilation digest to the
same document. Frozen bytes and output plus two fresh interpreter processes with
different hash seeds prove reproducibility.

## Rationale

A bytes-only pure function creates the smallest stable seam between future File
acquisition and immutable publication. Canonical coordinates make normalized
inputs comparable. Separating content identity from compilation identity allows
safe unchanged-byte reasoning without treating a compiler/configuration change
as the same derived artifact.

Failing closed on syntax outside the deliberately narrow grammar prevents the
first compiler from advertising CommonMark coverage it does not implement.

## Consequences

Issue #22 creates no source discovery, filesystem reads, database access,
network calls, model calls, `ContextRevision`, `ContextFragment`, or publication
state. Later parser expansion must version compiler/configuration provenance and
add frozen fixtures before accepting another construct. Acquisition owns the
mapping from original source bytes to this compiler input; it cannot redefine
the canonical output contract.

## Revisit trigger

Revisit when a later issue adds a Markdown construct, source-map requirement,
or representation-affecting configuration. Any replacement must preserve pure
bytes input, typed all-or-nothing failure, version-sensitive compilation
identity, frozen canonical serialization, and cross-process determinism.
