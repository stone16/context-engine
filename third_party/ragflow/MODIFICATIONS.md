# RAGFlow Markdown parser registration

## Pinned source

The registered region is `deepdoc/parser/markdown_parser.py` from RAGFlow at
commit `4391e03886b996201f3b8818f671b19eb24d0f7b`. The exact path carries an
Apache-2.0 header and is covered by the repository-root Apache-2.0 license,
reproduced verbatim as `LICENSE.upstream`. The pinned upstream tree contains no
root `NOTICE` file.

## Nested notice scan

The copied file imports only Python standard-library modules and
Python-Markdown. Python-Markdown is licensed under the BSD 3-Clause License.
Its verbatim license for the pinned 3.6 dependency is retained as
`LICENSE.python-markdown`. No other nested third-party dependency is imported
by the copied region.

## Executed reuse and ContextEngine-owned behavior

The exact upstream file is copied and executed: the ContextEngine adapter
constructs its `MarkdownElementExtractor` and calls the upstream fence-marker,
closing-fence, table-row, table-separator, and table-cell recognition methods.
No RAGFlow package initializer or `rag/nlp` dependency carrier is copied or
imported.

The broader compilation pipeline is ContextEngine-owned. It implements the
closed rich grammar, raw-input UTF-8 spans, heading ancestry, hard bounds,
versioned deterministic output, and typed refusal because the upstream parser
does not return the existing ContextEngine contracts and lacks exact byte-span
and hard-bound semantics. This is intentionally narrow reuse of one verified
Apache-2.0 file, not a claim that the entire upstream parser pipeline executes.

`sbom.cyclonedx.json` is the deterministic component inventory for the copied
parser and its sole nested dependency. Both wheel and source distribution
artifacts include it alongside the applicable license texts.

The empty `patches/` directory records that no textual source patch applies:
the current integration wraps and directly executes selected copied helpers.
