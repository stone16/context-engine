---
name: adr-0093-activate-leased-rich-markdown-and-revision-link-graph
version: "1.0.3"
description: >
  Activate rich Markdown v3 behind the exact File-import WorkerLease, persist
  immutable content-free Revision link edges, and admit one authorized graph
  hop into post-authorization ranking. Use when publishing rich Markdown link
  lineage or ranking one authorized graph hop. Not for link edges as authority,
  content-bearing pre-Kernel graphs, or unreauthorized cross-Article content.
---

# 0093. Activate leased rich Markdown and the Revision link graph

- Status: accepted
- Date: 2026-07-31
- Refines: ADR-0038, ADR-0071, ADR-0075, ADR-0076, ADR-0077, ADR-0079,
  ADR-0083

## Context

ADR-0079 delivered rich Markdown v3 as a pure local compiler runner but
explicitly deferred production activation, immutable publication, and graph
semantics. The current compiler accepts and preserves wikilinks, embeds, inline
links and images, and reference links and images. The earlier premise that all
link-bearing documents fail with `LINK_OR_IMAGE` applies only to frozen v1/v2.
V3's validated output retains exact syntax in section source text but exposes no
structured link collection.

One-hop expansion needs a durable content-free graph whose lineage is exact to
one immutable Revision. The graph may propose candidates, but ADR-0077 fixes the
Article as the only content authorization atom: Fragments and edges can never
carry independent ACLs. ADR-0076 and ADR-0083 also require expanded candidates
to join ranking only after authorization and prohibit refused candidates from
affecting visible order or selection.

The File scanner previously performed synchronous compilation as a preflight.
That made a Control-side scan predict a later worker result and left the
production v3 runner without the exact lease-selected execution boundary that
ADR-0075 requires.

## Decision

1. `markdown-config-v3` becomes the active File-import Markdown configuration.
   V1 and v2 remain frozen compatibility representations; existing Revisions
   are not reinterpreted or backfilled. The co-resident local evidence console's
   exact preview flow remains pinned to v1 because it has no durable import job
   or WorkerLease redemption authority with which to select the v3 child. Issue
   #203 and #207 ship the compatibility resolution: when v1's closed refusal is
   exactly `LINK_OR_IMAGE`, `EMPHASIS`, `INLINE_CODE`, or `STRIKETHROUGH` and the
   exact source matches that accepted v3 inline syntax, or when the source
   contains accepted rich link syntax not classified as such by v1, the console
   returns a content-free actionable handoff to the existing source `scan` plus
   independent worker-dispatch path. Malformed syntax and every other compilation
   refusal remain generically unavailable. The worker honors a redeemed, exact
   v1 preview binding only for the console's successful v1 preview/confirm flow;
   all scan-scheduled imports use active v3.
2. A File import redeems and durably verifies its exact WorkerLease before
   selecting the rich compiler subprocess. The child is a pure transform that
   receives source bytes, the closed configuration version, and token ceiling
   only. It receives an empty environment and no lease, Organization, actor,
   service-principal, database connection, filesystem path, network endpoint, or
   persistence interface. The child still executes under the parent operating-
   system identity and imports its installed code, so this boundary does not
   claim an OS sandbox or remove ambient filesystem and network capabilities.
   Timeout, crash, malformed output, invalid deserialization, and unexpected
   runner outcomes become one content-free typed boundary failure. A valid
   compiler refusal retains only its closed refusal category.
3. The File scanner verifies the accepted byte identity and schedules work but
   does not compile or predict a refusal. Durable compilation and refusal
   classification belong to the exact leased Supply worker. This retires
   ADR-0071's `compilationRefusals` scan-report field and aggregate because a
   scanner that no longer compiles cannot measure either value honestly;
   worker-observed refusal categories remain in the separate worker and status
   contracts.
4. Link extraction is a deterministic ContextEngine-owned derivation over
   validated rich-v3 section source. It recognizes local wikilinks, embeds,
   inline links, and reference links; masks inline and fenced code; resolves
   relative note paths; removes aliases and anchors; rejects remote, absolute,
   root-escaping, non-Markdown, or malformed targets; and preserves first-source
   order while deduplicating target paths. Images other than note embeds do not
   create graph edges.
5. The v3 compilation document records the ordered `revisionLinks` array. The
   publication transaction stores the identical edge set in
   `revision_link_edge`, bound by Organization, source Article, and source
   Revision. Rows contain only target path and closed link kind. They contain no
   content, score, Fragment permission, Article decision, ACL hint, audience, or
   delivery fact and are immutable with their Revision. Recovery verifies the
   exact stored edge array before resuming publication.
6. No graph edges are derived for historical v1/v2 Revisions, and v3 activation
   performs no old-Revision backfill. Downgrade is refused while any v3 snapshot,
   v3 recovery row, or Revision link edge remains because removing the contract
   would make retained lineage uninterpretable.
7. Runtime generates at most one hop from main-path `AuthorizedProjection`s.
   The graph resolver returns content-free `CandidateRef` locators for outgoing
   links and backlinks of those exact active Revisions. It never consumes a
   graph result as a new anchor, so a two-hop candidate is never generated.
8. Same-Article expansion inherits the anchor Article decision only after an
   exact same-Article/current-Revision locator lookup and projection succeeds.
   A stale or foreign Revision does not inherit. Every cross-Article candidate
   enters the unchanged `AuthorizationKernel` authorization and projection path
   with the original prepared policy and current UserActor transaction.
9. Only authorized expanded projections may be read for relevance. A bounded
   server-owned lexical graph ranker emits rank evidence only when a strict
   majority of the distinct query tokens occur in the projection, and that
   evidence joins ADR-0083's authorized fusion order with the main-path evidence.
   Expanded projections without graph rank evidence are ineligible for selection
   rather than neutrally appended. Refused candidates supply neither projection
   nor rank evidence and therefore cannot affect order, coverage, gaps,
   sufficiency, ContextRun, or DecisionAudit. The resolver reads deterministic
   pages of at most 64 graph locators and sends every cross-Article locator
   through the unchanged Kernel. It continues until the one-hop graph is
   exhausted, 64 expanded candidates have been authorized, or the configured
   `one_hop_scanned_page_limit` is reached. That page limit is a server-owned,
   corpus-independent, refusal-outcome-independent bound validated at Runtime
   composition against `MAX_ONE_HOP_SCANNED_PAGE_CEILING`; reaching it ends
   expansion without an error, count, gap, or other tenant-visible distinction.
   Refused locators therefore consume neither the authorized expansion bound nor
   unbounded graph examination work.
10. The graph SQL is owned by a NOLOGIN, NOINHERIT least-privilege definer. It
    may select only Organization-scoped graph and locator tables under FORCE
    RLS. Runtime may execute the bounded resolver but receives no direct graph
    table privilege; the worker definer may insert immutable edges only through
    publication.

## Rationale

Deriving links after validated v3 compilation preserves the compiler's exact
source contract without inventing a second parser output model. Persisting
content-free Revision edges makes reproduction and recovery exact while keeping
all authorization at the Article boundary.

Starting from authorized projections establishes the permitted expansion root.
Re-authorizing every cross-Article locator through the existing Kernel prevents
graph structure, index data, or source paths from widening delivery. Ranking
expanded content only after projection gives neighbours a chance to compete
without making expansion an inclusion rule.

Retiring scanner compilation assigns one durable owner to compilation outcome.
The empty child environment plus the data-only protocol makes the runner's lack
of lease, actor, database, source-path, and endpoint authority an executable
process boundary rather than a naming convention; it is process separation, not
an operating-system sandbox.

## Consequences

- File notes whose frozen-v1 preview outcome satisfies the closed accepted
  rich-link, emphasis, inline-code, or strikethrough handoff checks can now
  publish without changing v1/v2 bytes or historical Revision meaning.
- Outgoing links and backlinks are reproducible from immutable v3 Revision
  lineage, but the graph itself grants no access and exposes no content.
- A denied neighbour is indistinguishable from an absent or irrelevant
  neighbour in tenant-visible delivery and authorized-only retained lineage.
- Each graph read and the authorized expansion set are bounded to 64, and the
  number of graph pages examined in one resolve is independently bounded by the
  validated server-owned scanned-page ceiling. Reads are never recursively
  applied; refused neighbours cannot consume the authorized bound or extend
  graph examination past that same request-wide page ceiling.
- Existing databases gain one FORCE-RLS tenant table and one least-privilege
  graph definer role; the security manifest and executable evidence denominator
  include both.
- Adjacent-note and multi-hop evaluation cases remain within the existing
  `cross_doc` slice contract and use only locked synthetic fixtures.

## Revisit trigger

Revisit before adding a second hop, accepting an external URI or non-Markdown
target, deriving edges from unvalidated or historical content, changing edge
retention, adding per-edge or Fragment ACL data, authorizing from graph/index
hints, moving graph relevance before authorization, making expansion automatic
inclusion, increasing the scanned-page hard ceiling, making the examination bound
depend on corpus or refusal outcomes, or giving the compiler child lease, actor,
database, persistence, or external-service coordinates. Introducing an OS
sandbox is a separate hardening choice that requires a portable runner contract
and executable platform evidence.
