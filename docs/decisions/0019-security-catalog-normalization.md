---
name: adr-0019-security-catalog-normalization
version: "1.0.0"
description: >
  Normalize the release security catalog to fifteen stable invariant IDs and
  distinguish canonical acceptance scenarios from derived evidence cases.
---

# 0019. Normalize the release security catalog to fifteen stable IDs

- Status: accepted
- Date: 2026-07-20

## Context

The security checklist, test architecture, and historical design accumulated
overlapping labels for the same release vetoes. Counting those labels as
independent invariant families produced a nineteen-entry prose list, while an
older acceptance section promoted ten later parameterizations into additional
top-level scenarios. Neither expansion added a new security boundary, but both
made release reports and milestone exits ambiguous.

The hard oracles, sealed authorization ordering, trusted delivery construction,
audience intersection, ACL-proof behavior, revocation behavior, and single
release owner are already fixed by the
[threat model](../security/context-engine-threat-model.md),
[ADR-0003](0003-group-chat-intersection-authorization.md),
[ADR-0010](0010-policy-epoch-revocation.md),
[ADR-0012](0012-sealed-authorization-projection-pipeline.md),
[ADR-0013](0013-trusted-delivery-egress-and-capability-taxonomy.md),
[ADR-0014](0014-curation-snapshot-and-release-ownership.md), and
[ADR-0017](0017-trusted-invocation-and-closed-runtime-access.md). Catalog
normalization must not weaken or renumber those safeguards.

## Decision

The canonical release catalog contains exactly these fifteen stable IDs, in
this order:

1. `TENANT-OWNERSHIP-001`
2. `TENANT-FK-002`
3. `RLS-FAIL-CLOSED-003`
4. `SCOPE-INTERSECTION-004`
5. `INDEX-NOT-AUTHORITY-005`
6. `REVOCATION-006`
7. `WORKER-LEASE-007`
8. `TRANSPORT-UNTRUSTED-008`
9. `NON-ENUMERATION-009`
10. `CITATION-AUTH-010`
11. `EGRESS-011`
12. `TRACE-REDACTION-012`
13. `ACTION-SEPARATION-014`
14. `CROSS-ORG-LEARN-015`
15. `RELEASE-OWNER-019`

`eval/catalogs/security-invariants.yaml` is the machine authority for this
set. `eval/catalogs/security-catalog.schema.json` validates its shape, and
`python3 scripts/validate_security_catalog.py` validates the catalog and its
tracked document references. The catalog uses JSON-compatible YAML so the D0
validator can use the Python standard library while no dependency manifest
exists.

The following labels retain all of their tests and safeguards but are not
additional canonical release IDs:

- `AUDIENCE-016` is covered by `SCOPE-INTERSECTION-004` plus `EGRESS-011`.
- `ACL-PROOF-017` is covered by `INDEX-NOT-AUTHORITY-005` plus
  `REVOCATION-006`.
- `DELIVERY-EVIDENCE-018` is covered by `TRANSPORT-UNTRUSTED-008`.

`CACHE-SCOPE-013` remains a preregistered conditional extension outside the
canonical fifteen. It becomes applicable with the first authorization-sensitive
final `ContextPackage` or `AuthorizedProjection` cache. Activating that
capability requires a future versioned catalog and schema change that adds the
extension and its proving cases; until then, composition tests must prove that
no such cache is active. Existing numbering is never reused or shifted.

The canonical V1 acceptance fixture has twelve top-level scenarios:
`ACCEPT-001` cross-Organization isolation (one fixture with bidirectional A/B
assertions), `ACCEPT-002` same-Organization Membership isolation,
`ACCEPT-003` Agent ceiling, `ACCEPT-004` request narrowing, `ACCEPT-005`
revocation, `ACCEPT-006` hostile index, `ACCEPT-007` transport injection,
`ACCEPT-008` WorkerLease replay/binding, `ACCEPT-009` source-native ACL,
`ACCEPT-010` citation revocation, `ACCEPT-011` denied/not-found equivalence,
and `ACCEPT-012` Context/Action separation. Cases historically numbered 13
through 22 remain required parameterized or derived cases mapped to those
twelve scenarios or directly to invariant evidence. They are not ten
additional top-level acceptance IDs.

## Rationale

One stable machine-readable set makes release completeness mechanically
checkable and prevents prose counts from becoming a second authority. Absorbing
overlapping labels preserves their negative cases while making each release
veto independently reportable. Keeping the cache rule conditional avoids
claiming an inactive cache capability while ensuring its security gate is
defined before activation.

## Consequences

Security documentation and generated reports must use the exact fifteen IDs
and may not describe the absorbed labels as extra families. Every absorbed or
derived case still needs evidence under its mapped canonical invariant. A
validator failure, unmapped active case, or missing evidence is a release
failure; normalization cannot turn it into `NOT_ACTIVE` or
`NOT_APPLICABLE`.

Reviewers can compare release reports without ID churn. Adding a genuinely new
security boundary requires an explicit catalog/schema version change and an
ADR; it cannot be introduced by silently extending a prose table.

## Revisit trigger

Reopen when a new implemented security boundary cannot be represented by the
canonical fifteen, or when an authorization-sensitive final
`ContextPackage`/`AuthorizedProjection` cache is first activated. Any revision
must preserve existing IDs and evidence history, name the new proving seam and
milestone applicability, update the versioned machine catalog and schema, and
retain the three hard zero oracles.
