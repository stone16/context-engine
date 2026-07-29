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
No other nested third-party dependency is imported by the copied region.

## ContextEngine modifications

The upstream bytes are retained under `deepdoc/parser/markdown_parser.py` for
auditable provenance. ContextEngine adapters patch behavior through the owned
compiler-runner and do not import RAGFlow package initializers or its `rag/nlp`
dependency carriers. Local changes add exact UTF-8 spans, heading ancestry,
hard bounds, versioned deterministic output, and typed refusal at the
ContextEngine seam.

The empty `patches/` directory is reserved for future upgrades that change the
registered upstream file itself. The current integration wraps the copied file,
so no source patch applies.
