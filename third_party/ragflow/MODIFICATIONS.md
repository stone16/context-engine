# RAGFlow parser modifications

## Pinned source and license region

The registered region is pinned to RAGFlow commit
`4391e03886b996201f3b8818f671b19eb24d0f7b`. Each copied file carries an
Apache-2.0 header and is covered by the repository-root Apache-2.0 license,
reproduced verbatim as `LICENSE.upstream`. The pinned upstream tree contains no
root `NOTICE` file.

The independently verified original SHA-256 values are:

| Upstream path | Original SHA-256 |
|---|---|
| `deepdoc/parser/markdown_parser.py` | `94c8e2515d05e141fcf65e10336ceca7f9116e54b31a668637ba3f901943cb66` |
| `deepdoc/parser/docx_parser.py` | `891ffc11d2a3ac32e5c0d8b25b35aa62ab8cda1033c9e0a93782e9d45e759586` |
| `deepdoc/parser/utils.py` | `7d1674fb7c92b2db24964575cb2290139a823a923da89a321cbdaea795452849` |

The registration records post-patch hashes for the two modified files. The
machine-readable refresh patches under `patches/` reconstruct each post-patch
file from its pinned original.

## Nested notice scan

- `markdown_parser.py` imports Python-Markdown 3.6, licensed BSD 3-Clause
  (`BSD-3-Clause`);
  its verbatim license is `LICENSE.python-markdown`.
- The patched `docx_parser.py` imports python-docx 1.2.0, licensed MIT; its
  verbatim primary license is `LICENSE.python-docx`.
- The patched outline-only `utils.py` imports pypdf 6.13.1, licensed
  BSD-3-Clause; its verbatim primary license is `LICENSE.pypdf`.
- The patched outline-only `utils.py` imports pypdfium2 5.12.1 to hash actual
  deterministic page-render pixels. The wrapper is BSD-3-Clause and its wheel
  additionally distributes Apache-2.0 PDFium plus bundled dependency notices;
  `LICENSE.pypdfium2` retains the wrapper license and
  `LICENSE.pdfium-bundle` retains the complete native-bundle license directory.

The python-docx runtime also imports lxml, but no lxml code is copied into this
subtree. lxml remains an ordinary locked transitive project dependency rather
than a component derived from the registered RAGFlow source region.

## Executed reuse and ContextEngine-owned behavior

The Markdown integration remains unchanged: the ContextEngine adapter executes
selected helpers from the copied `MarkdownElementExtractor`; the surrounding
rich Markdown compilation pipeline remains ContextEngine-owned.

`docx_parser.py` is copied and patched to remove RAGFlow tokenizer,
`LazyImage`, Pandas, logging, and application constants. It now traverses
paragraph and table XML children in exact OOXML body order and returns bounded
raw blocks. ContextEngine-owned code maps those blocks into the ADR-0094 nominal
`DocxXmlLocator` family, structural units, identities, and typed refusals.
Image-bearing DOCX artifacts refuse because a bounded figure-byte policy has
not been admitted; images are never silently discarded.

Only the `extract_pdf_outlines` region of `utils.py` is retained. The `get_text`
helper and `rag.nlp.find_codec` dependency are removed, and the blanket
exception handler is removed. ContextEngine-owned code validates outline title,
depth, page range, bounds, and maps it into `PdfRegionLocator`. The locator
digest covers a 72-DPI BGR page render (shape, stride, channel count, pixel
format, and exact pixels), not the PDF container bytes; malformed or
outline-free inputs refuse with a closed category.

The owned runner selects only `docx-config-v1` or `pdf-text-outline-v1` before
artifact bytes are read, uses a fixed deadline plus an explicit repository-root
working directory and `PYTHONPATH`-only environment, and round-trips through the
self-validating canonical document constructor. No
RAGFlow package initializer, PDF/OCR parser, vision module, model asset,
application service, network client, database, or index is copied or imported.

`sbom.cyclonedx.json` inventories the copied regions and their direct nested
dependencies. Built wheel and source distributions physically carry the root
notices/SBOM plus all applicable license texts.
