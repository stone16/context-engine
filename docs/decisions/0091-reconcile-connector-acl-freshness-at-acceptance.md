---
name: adr-0091-reconcile-connector-acl-freshness-at-acceptance
version: "1.0.0"
description: >
  Treat connector ACL timestamps and epochs as observations by reconciling them
  with the exact WorkerLease and database clock before page acceptance. Use
  when accepting connector ACL observations under a WorkerLease. Not for
  runner-authored time authority, stale leases, or unchecked ACL epochs.
---

# 0091. Reconcile connector ACL freshness at Supply acceptance

- Status: accepted
- Date: 2026-07-31
- Refines: ADR-0075, ADR-0077, ADR-0085

## Context

`SourceAclObservation.policy_epoch` and `observed_at` originate in a connector
runner. Their types and bounds make them valid observation data, but do not make
them authoritative freshness facts. A compromised runner could otherwise claim
the largest epoch or a far-future observation time and make stale evidence look
current. The page-acceptance function already receives the database-redeemed
WorkerLease Policy Epoch and owns a database timestamp.

Migration 0043 also pins each delete entry to exactly `acl_observation` and
`document_ref`. Freshness reconciliation therefore must strengthen the existing
ACL object validation without adding a delete field or changing that allowlist.

## Decision

1. The Supply page-acceptance boundary refuses every upsert and delete ACL
   observation whose epoch is ahead of the exact `requested_policy_epoch` bound
   to the redeemed WorkerLease. An epoch at or behind the lease remains
   non-authoritative provenance; it cannot advance or select engine policy.
2. Every observation timestamp must carry an explicit time-zone designator,
   parse as a PostgreSQL timestamp, and not be later than the acceptance
   function's database-owned time. Parse failures and future values refuse the
   entire page before staging. Epoch comparison uses a non-overflowing numeric
   domain before comparison with the bounded lease epoch.
3. The runner-authored values remain useful provenance only after these checks.
   They do not become delivery authorization and do not replace request-time
   policy checks.
4. Migration 0049 replaces the acceptance function after migration 0048 and
   preserves the exact two-key delete-entry allowlist unchanged.
5. Data exceptions inside the bounded payload-decoding block are converted into
   a content-free refusal. Database writes occur after that block; database and
   integrity failures there are not swallowed and abort the transaction, so
   staging and checkpoint state cannot be reported accepted after a failed write.

## Consequences

- A runner cannot manufacture freshness beyond its exact lease or beyond the
  database acceptance clock.
- Pages with any mismatched observation fail atomically: no staged page and no
  checkpoint advance remain.
- Clock skew that places a runner timestamp in the future is fail-closed. A
  future source requiring tolerated skew needs an explicit, measured contract.

## Revisit trigger

Revisit when a connector supplies source-signed observation time, when leases
bind a range of admitted epochs, or when measured clock skew requires a bounded
server-owned tolerance.
