---
name: adr-0077-fix-the-article-as-the-content-authorization-atom
version: "1.0.0"
description: >
  Make the Article (ContextResource) the only content authorization atom with
  a versioned access policy and a tenant/source default-visibility cascade
  fixed at first ingestion; Fragments never carry independent ACLs.
---

# 0077. Fix the Article as the content authorization atom

- Status: accepted
- Date: 2026-07-29
- Refines: ADR-0068; formalizes the Resource-keyed authorization already
  present in the implementation (`engine/runtime/scope.py`, migration 0005)

## Context

Retrieval operates on sub-Article Fragments, which invites a design error:
per-Fragment ACLs. The implementation already keys authorization by
ContextResource, and comparative analysis of four reference products showed
that per-chunk permissioning appears nowhere while per-document permissioning
is the workable enterprise pattern. The maintainer confirmed Article-level
granularity as a product decision; "Article" is the product-facing alias of
`ContextResource`, and `Article/ContextResource` remains one glossary term,
not two objects. What was missing is a written rule for visibility defaults,
their timing, and expansion semantics.

## Decision

1. **One atom.** The Article (ContextResource) is the only content
   authorization atom. A versioned `ArticleAccessPolicy` — `PRIVATE`,
   `ORGANIZATION`, or `GROUPS(group_refs)` with at least one valid group in
   the owning Organization — governs every Fragment of its active Revision.
   Fragments carry structure, retrieval, projection, and provenance roles
   only; a Fragment-level ACL must never exist.
2. **Default cascade, fixed at first ingestion.** Effective policy for a new
   Article resolves as: explicit Article setting, else Source default, else
   Tenant default, else isolation (not published). The resolved value is
   fixed into the Article's versioned policy at first ingestion; later changes
   to Source or Tenant defaults affect only future Articles.
3. **No implicit historical change.** Existing Articles change policy only
   through explicit, previewed, confirmed bulk changes committed atomically
   with a Policy Epoch advance.
4. **Source-native ACL floor.** Where source-native ACL evidence exists,
   local defaults may narrow but never widen effective access; missing,
   failed, or unresolved ACL observation isolates the Article (fail closed)
   rather than falling back to a broader default.
5. **Expansion semantics.** Within one authorized Article and its current
   active Revision, expansion inherits the Article decision after lineage
   verification. Any expansion that reaches another Article emits a new
   `CandidateRef` and re-authorizes; index-carried ACL hints are never
   authorization.

## Consequences

- Retrieval granularity (Fragments, ~512-token units) and authorization
  granularity (Articles) are decoupled by design; "chunk recall, Article
  delivery" is legal because delivery authorization happens per Article.
- Revocation and grants take effect for new resolves at the request-time
  policy check once observed and committed with their Policy Epoch, matching
  the Mirrored-evidence promise; the candidate index never holds decisive ACL
  state.
- Bulk policy administration becomes a first-class, auditable operation and a
  required product surface rather than an ad-hoc data fix.

## Revisit trigger

Revisit if a source emerges whose native permissions are genuinely
sub-document, or if audience-bound delivery (BotDelivery group resolves)
surfaces a need the Article atom cannot express.
