---
name: adr-0064-split-process-ceremony-along-the-kernel-seam-boundary
version: "1.0.0"
description: >
  Keep full evidence ceremony for changes that touch sealed authorization
  surfaces, and run seam-side product work with a lighter process, using the
  architecture's existing kernel-versus-seam boundary as the lane test. Use
  when choosing the evidence lane for a change. Not for weakening ceremony on
  sealed authorization surfaces or treating seam work as security-neutral.
---

# 0064. Split process ceremony along the kernel-seam boundary

- Status: accepted
- Date: 2026-07-26
- Refines: ADR-0012, ADR-0016, ADR-0034

## Context

Every issue to date has carried full ceremony: an ADR, security-catalog
activation entries with deferred/future/not-active ledgers, and
narrowest-carrier activation. On sealed authorization surfaces this produced
evidence of unusual quality, including mutation tests of the security gate
itself. Applied uniformly, the same ceremony makes seam-side product work —
retrieval quality, ingestion breadth, parsing, evaluation — slow far beyond
its risk, and it is the direct cause of artifacts like a 4,096-byte
production file ceiling. ADR-0062 requires product-lane velocity that uniform
ceremony cannot supply. The architecture already draws the needed line:
ADR-0012 seals the authorization pipeline, and the code enforces it with
type-identity checks, while retrieval, parsing, and provider surfaces are
deliberate seams.

## Decision

Process weight follows the existing kernel-versus-seam boundary:

1. **Kernel lane — full ceremony.** Any change touching the sealed surfaces:
   AuthorizationKernel and its gate dependencies, EffectiveScope,
   RLS/schema-security manifest, Policy Epoch, ContextAccessTicket /
   ActionTicket / WorkerLease issuance and redemption, EgressGrant,
   TrustedDeliveryContext and DeliveryEvidenceRef handling, authentication
   compositions, and the security catalog itself. These changes keep the
   complete discipline — an ADR, catalog activation with explicit not-active
   ledgers, and adversarial evidence.
2. **Product lane — light process.** Changes that live entirely behind
   declared seams: candidate index implementations, parsers and compilation
   profiles, provider read paths, ingestion limits and traversal, embedding
   integration, evaluation harnesses and golden sets. These need an issue and
   tests. An ADR is written only when all three of the following hold: the
   choice is hard to reverse, it would surprise a reader without context,
   and it resolves a real trade-off; this ADR owns that test. Product-lane
   work adds no security-catalog activation entries; the catalog remains a
   security instrument, not a progress ledger.
3. **Lane assignment is mechanical.** If a change modifies a type-identity-
   sealed surface or any invariant the catalog registers, it is kernel lane;
   otherwise it is product lane. Activating a carrier that the catalog or the
   served process currently records as `NOT_ACTIVE` — including the first
   content-bearing delivery carrier in the served composition — is a
   kernel-lane event even when the implementing code sits entirely behind a
   seam. The three hard oracles remain veto for both lanes, and the existing
   integration suite continues to run for every change.

The prohibited shortcut is smuggling kernel-surface modifications inside a
product-lane change, adding catalog entries to narrate product progress, or
relaxing a hard oracle for velocity in either lane.

## Rationale

The ceremony is the repository's differentiated asset exactly where the
architecture is sealed, and pure overhead exactly where the architecture
already confines blast radius behind seams the kernel revalidates. Reusing
the sealed-versus-seam line as the process line means lane assignment needs
no judgment call per issue, and the alternative extremes are both known
failures: uniform ceremony starves the product lane, while a blanket
relaxation quietly erodes the security posture and never gets re-tightened.

## Consequences

- Slice A (ADR-0062) runs mostly in the product lane; its kernel-lane
  components — the first content-bearing delivery carrier activation in the
  served composition and the dogfood authentication composition — carry full
  ceremony, the latter under ADR-0063.
- Seam-side iteration speed increases without any new authorization risk
  class; hostile-input conformance suites at the seams remain in force.
- Some product-lane decisions will be recorded only in issues and tests;
  reviewers accept this as intended, not as missing ceremony.
- If a product-lane change is later found to have needed kernel-lane
  treatment, the correction is a kernel-lane follow-up, not retroactive
  relabeling.

## Revisit trigger

Revisit if a security regression is ever traced to a product-lane change, if
the catalog's invariant set needs to grow to cover a seam that has become
authorization-relevant, or when a second maintainer joins and lane assignment
stops being mechanical.
