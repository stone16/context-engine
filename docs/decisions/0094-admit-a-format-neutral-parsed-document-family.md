---
name: adr-0094-admit-a-format-neutral-parsed-document-family
version: "1.0.0"
description: >
  Fix one format-neutral ParsedDocument family with nominal per-format source
  locators, a closed structural-kind vocabulary including FIGURE, and explicit
  versioned format profiles, so DOCX and PDF compilation can be built without
  disguising their provenance as Markdown byte spans. Use when adding a
  non-Markdown compiler or changing publication provenance contracts. Not an
  approval of any third-party copy or of PDF model activation.
---

# 0094. Admit a format-neutral parsed document family

- Status: accepted
- Date: 2026-07-31
- Refines: ADR-0036, ADR-0038, ADR-0079
- Related: ADR-0018, ADR-0066, ADR-0074
- Decision input: `docs/research/2026-07-31-five-repository-implementation-blueprint.md` §5 (D1, decided 2026-07-31) and `docs/research/2026-07-31-ragflow-blueprint-evaluation.md` §3.1 (maintainer-local Room-A research; not public provenance)

## Context

The accepted Markdown compilers fix `ParsedDocument` around `SectionKind` and
UTF-8 byte spans into canonical source text: ADR-0036 (v1) and ADR-0038 (v2)
are the activated publication contracts, while ADR-0079 (v3) admits only a
local/acceptance transform whose production publication remains `NOT_ACTIVE`.
That representation is honest for Markdown but cannot express a DOCX zip
member (`part_uri` + block ordinal + XML digest) or a PDF region (page number,
bounding box, render digest, extraction method). The five-repository blueprint
evaluation established that the first non-Markdown lifts under ADR-0074 are
RAGFlow's `deepdoc/parser/docx_parser.py` and `extract_pdf_outlines`, and that
writing either compiler before the representation contract is fixed would force
PDF bounding boxes to masquerade as Markdown byte spans, corrupting Revision
provenance identity and every citation that derives from it.

Maintainer decision D1 selects one format-neutral family contract with nominal
per-format locator subtypes and a new `FIGURE` structural kind, over parallel
sibling document types or indefinite deferral.

## Decision

1. **One family contract.** `ParsedDocument` is the format-neutral publication
   representation: canonical serialization, all-or-nothing construction,
   self-validating domain constructors, server-owned hard bounds (artifact
   bytes, pages, pixels, blocks, cells, text length, runner wall time), a
   closed typed refusal vocabulary, and two never-collapsed identities over the
   whole canonical document: a content identity digest and a compilation
   identity digest, generalizing ADR-0036's `content_hash`/`compilation_digest`
   separation to every format. The Supply publication seam
   (`prepared → indexed → active`, ADR-0018) accepts any family member through
   the same protocol.
2. **Nominal source locator union.** Every structural unit carries one or more
   typed locators from the closed union: `TextByteSpan(source_identity_digest,
   start, end)` over the profile's declared canonical source text — for the
   frozen v1/v2 Markdown profiles, ADR-0036's normalized canonical UTF-8,
   preserving existing v1/v2 content identities (v3 falls under the next
   branch, its byte spans round-tripping against the original input per
   ADR-0079 clause 4); profiles that publish original-artifact round-trips bind
   the original artifact digest;
   `DocxXmlLocator(artifact_digest, part_uri, block_ordinal, xml_digest)`;
   `PdfRegionLocator(artifact_digest, page_number, bbox_points,
   page_render_digest, extraction_method)`. Tables and figures may carry
   multiple ordered locators but remain one structural unit. Locators are
   nominal types, not interchangeable dicts; a locator from another format is a
   construction failure, not a fallback.
3. **Closed structural-kind vocabulary.** `HEADING`, `PARAGRAPH`, `LIST`,
   `TABLE`, and `FENCED_CODE` are the existing frozen nominal kinds; `FIGURE`
   is the only kind this decision adds. One
   structural unit is exactly one Fragment; table cells, OCR words, and PDF
   lines are typed metadata inside their unit, never independent Fragments.
   Heading ancestry is copied into the same Fragment at compile time and counts
   toward budget (ADR-0038); Runtime never fetches a "parent Fragment" to
   restore headings. `FIGURE` units reference image bytes through a separate
   bounded image-artifact policy; until that policy is admitted by its own
   decision, a format profile either refuses figure-bearing artifacts or emits
   a content-less figure descriptor (caption/locator only), never silent
   omission and never inline bytes.
4. **Explicit versioned format profiles.** Each format compiles under an
   immutable profile identity (the existing `context-engine-markdown-v*`
   grammar family and `markdown-config-v*` configuration family for Markdown;
   new formats add their own, for example `docx-config-v1` and
   `pdf-text-outline-v1`) carried by `CompilationProfileRef`.
   A profile fixes its grammar surface, locator usage, bounds, determinism
   policy, and refusal categories. Unknown or unsupported profiles refuse before
   artifact bytes are opened and before any model is loaded.
5. **Revision identity binds the full provenance chain.** `ContextRevision`
   binds the original artifact SHA-256, the exact compiler/profile identity,
   any model-bundle identity, and the complete canonical parsed-document
   digest. A format's derived identities are versioned artifacts of that
   profile, never recomputed silently.
6. **Frozen Markdown, no reinterpretation.** Markdown v1/v2/v3 fixtures and
   semantics are frozen. The family contract must not reinterpret an existing
   Revision; new formats receive their own format versions and the publication
   seam distinguishes profiles explicitly.
7. **No copy and no activation inside this decision.** This ADR approves no
   `third_party/` addition and no PDF profile activation. DOCX/outline copying
   proceeds only under its own approval issue (decision D6) with ADR-0074
   registration; PDF profiles requiring model assets remain `NOT_ACTIVE` until
   the asset gate closes under the D12 offline digest-bound bundle and single
   runtime target decision, admitted by a separate activation record.

## Consequences

- DOCX and PDF-outline compilation become specifiable and testable against one
  publication seam; PDF bbox provenance can never be disguised as a byte span.
- The ADR-0079 runner envelope (fixed deadline, typed `CompilationFailure`, no
  network/DB/state) is reused unchanged for new format runners.
- `FIGURE` content policy is deliberately deferred; figure-bearing profiles are
  honest about the gap instead of silently dropping images.
- Every new format pays the representation cost once (profile, locators,
  fixtures, determinism proof) before product code.

## Revisit trigger

Revisit if a required format needs locator semantics that do not fit the closed
union, if the bounded image-artifact policy for `FIGURE` is admitted, or if
model-asset verification reopens PDF layout profiles under a different runtime
target policy.
