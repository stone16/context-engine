---
name: adr-0092-authorize-feishu-subjects-and-bound-mirrored-freshness
version: "1.0.0"
description: >
  Treat runner-emitted Feishu subject claims as reproducibility inputs, derive
  Article grants from engine-owned mappings, and expire Mirrored Feishu ACL
  evidence after one closed five-minute Runtime freshness profile.
---

# 0092. Authorize Feishu subjects and bound Mirrored freshness

- Status: accepted
- Date: 2026-07-31
- Refines: ADR-0012, ADR-0025, ADR-0075, ADR-0077, ADR-0091

## Context

The clean-room Feishu connector can observe external users and nested groups,
but its runner executes outside the delivery-authorization boundary. Local
principal and group references inside a staged ACL artifact therefore cannot
be trusted as grants. An artifact also must not be replayable from one Article
envelope into another.

ADR-0091 reconciles the observation time and Policy Epoch against database and
lease authority at page acceptance. That bounds self-attested future values,
but it does not define when a previously accepted Mirrored Feishu observation
expires if no later page arrives. Room-A oracle O12 requires such an old mirror
to stop authorizing during a source outage.

## Decision

1. Runner-emitted identity mappings, group mappings, graph digests, and flattened
   local references are reproducibility claims only. The database recomputes
   reachable groups and principals from the canonical graph plus the
   Organization-and-Source-bound `feishu_subject_mapping` and
   `article_access_group` authorities. Missing, mismatched, or unresolved
   mappings isolate the Article.
2. The inner ACL artifact `document_ref` must equal the exact outer Article
   locator selected from the accepted page. A mismatch is committed as
   `unresolved_group` isolation and produces no grant.
3. A failed ACL dependency observation, including identity or group lookup
   failure after a successful ACL response, is staged as a timestamped
   `failed` Mirrored observation. Applying a failed, unresolved, non-private,
   or delete observation revokes retained principal grants for that Article in
   the same transaction that fixes the Article policy and advances the Policy
   Epoch.
4. Accepted Feishu Mirrored evidence uses the closed
   `feishu-docs-mirrored-five-minute-v1` freshness profile. Runtime compares
   `source_acl_as_of` with the current UserActor transaction's trusted
   `checked_at`; evidence older than five minutes cannot enter effective scope
   or produce a materialized locator. Expiry does not mutate the Article or
   index, does not require a new observation, and never downgrades to Weak.
5. The File source keeps its current-transaction profile unchanged. A different
   Feishu interval requires a newly versioned profile and evidence; it is not a
   feature flag or caller setting.
6. Migration downgrade refuses while any Feishu Source, SourceVersion,
   observation authority, or subject mapping remains. Restoring the prior
   runner-trusting function while retained Feishu authorization state exists is
   prohibited.

## Consequences

- The runner cannot grant a local subject, substitute another Article's ACL,
  keep an old principal grant after a dependency outage, or keep a Mirrored
  grant live indefinitely during an outage.
- The Package names the exact freshness profile that Runtime enforced.
- Five minutes is a deliberately conservative first profile for the bounded
  offline carrier. It limits unobserved revocation exposure while leaving
  measured live-source polling and service-level tuning to later activation
  evidence.
- Live Feishu transport, credentials, production mapping administration, and
  source polling remain `NOT_ACTIVE`.

## Revisit trigger

Revisit when live conformance evidence establishes a different polling and
revocation budget, when Feishu supplies source-signed subject or time evidence,
or when production mapping administration gains an independently authorized
Control operation.
