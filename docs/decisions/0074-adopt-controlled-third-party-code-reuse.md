---
name: adr-0074-adopt-controlled-third-party-code-reuse
version: "1.0.0"
description: >
  Replace the absolute zero-copy rule with license-tiered, registered,
  pinned-commit third-party reuse, keeping clean-room discipline for every
  restricted source region. Use when proposing, importing, or shipping
  third-party code. Not for unregistered copies, floating provenance, or reuse
  from clean-room-only source regions.
---

# 0074. Adopt controlled third-party code reuse

- Status: accepted
- Date: 2026-07-29
- Refines: the repository reuse policy previously stated in `AGENTS.md`
  ("Zero code copying from Dify, RAGFlow, MaxKB, or Onyx") and the
  clean-room evidence discipline of
  `docs/research/2026-07-19-four-public-repositories-evidence.md`

## Context

The zero-copy rule treated all four reference products as one undifferentiated
legal region. Verified license texts at pinned commits show four different
regimes: RAGFlow is Apache-2.0; Onyx is MIT outside every `ee` directory and
enterprise-licensed inside them; Dify's root license adds a multi-tenant
restriction on top of Apache-2.0 that conflicts with this product's stated
multi-tenant purpose; MaxKB is GPLv3, whose §5(c) would govern the combined
distributed work. The repository is public, so any copied code is distributed.

Maintainer-directed investigations (2026-07-29) established that selected
permissive upstream code — most prominently RAGFlow's Markdown parser and
Onyx's MIT connector framework — solves measured product gaps at a fraction of
reimplementation cost, while an undifferentiated copy freedom would create
license contamination and provenance loss.

## Decision

Reuse is permitted per source region, never per product, under four tiers:

1. **Copy+patch permitted**: Apache-2.0 regions (RAGFlow) and MIT regions
   (Onyx outside every `ee` directory; Dify's separately-licensed MIT SDK
   subtrees), only for exact paths whose license region was verified at a
   pinned commit, and only after nested third-party notice scanning.
2. **Clean-room only**: Dify root-licensed code, MaxKB GPLv3 code, and Onyx
   `ee` code. Reuse happens through a two-room protocol: a Room-A observer
   reads upstream source and produces behavior specifications plus test
   oracles; a Room-B implementer never reads that source and implements from
   the specification and tests alone. Room-A artifacts are stored as
   maintainer-local research until sanitized.
3. **Registered provenance**: every copied subtree lives under `third_party/`
   with `LICENSE.upstream`, `UPSTREAM.toml` (repo, exact commit, source paths,
   excluded paths, file hashes, reuse mode, approval), `MODIFICATIONS.md`, and
   local patches. A root `THIRD_PARTY_NOTICES.md` aggregates attribution.
4. **Distribution completeness**: every built artifact (wheel, sdist, npm
   tarball, container image) must physically include the applicable
   LICENSE/NOTICE/third-party texts and an SBOM; Git-only attribution is not
   compliance.

Every future migration re-verifies the exact upstream path at its pinned
commit before copying; a product-level allowlist never substitutes for
path-level verification. `AGENTS.md` and `CONTRIBUTING.md` are updated to
state this policy through the documentation-steward workflow.

## Consequences

- The measured supply gap (0/116 raw-note acceptance) becomes addressable by
  copy+patch of one Apache-licensed parser file instead of a reimplementation.
- Copying from Dify root, MaxKB, or Onyx `ee` remains prohibited regardless of
  convenience; those regions contribute behavior specifications only.
- Attribution and SBOM work becomes part of the definition of done for any
  migration; an unregistered copy is a policy violation even when the license
  permits it.
- The public-reference evidence discipline is unchanged: public claims still
  trace to the four-repositories evidence report or verifiable upstream
  sources at pinned commits.

## Revisit trigger

Revisit if the project's own outbound license changes, if a commercial
authorization for a restricted region is obtained, or if a copied subtree's
upstream relicenses.
