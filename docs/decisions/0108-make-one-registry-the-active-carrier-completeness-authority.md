---
name: adr-0108-make-one-registry-the-active-carrier-completeness-authority
version: "1.0.0"
description: >
  Make one versioned registry the active-carrier inventory, status, and
  completeness authority. Use when adding, activating, deferring, or reviewing
  a carrier. Not for defining carrier behavior or evaluating the Security veto.
---

# 0108. Make one registry the active-carrier completeness authority

- Status: accepted
- Date: 2026-08-13
- Refines: ADR-0019, ADR-0034, ADR-0098
- Related: [issue #244](https://github.com/stone16/context-engine/issues/244)

## Context

ContextEngine records bounded carriers across accepted ADRs, executable
entrypoints, migrations, public tests, gates, operator documentation, and the
human-readable status ledger. Each artifact owns a necessary part of the claim,
but none previously proved that the repository had joined every known active and
deferred carrier to all of those parts. A stale prose list could therefore omit
a carrier or disagree with its evidence without one closed completeness result.

The Security catalog and executable Security gate already own invariant meaning
and release-veto evidence; the missing boundary is a closed cross-carrier
status/evidence join.

## Decision

`eval/catalogs/active-carriers-v1.json` is the canonical cross-carrier inventory,
status, and completeness authority. It contains every known bounded active
carrier and every closed deferred relation represented by a `NOT_ACTIVE`
carrier. Each carrier's accepted ADRs remain the behavioral-authority inputs:
the registry may point to an accepted decision but cannot define, widen, or
activate that carrier's behavior. `STATUS.md` remains a human-readable ledger
whose closed carrier/status inventory is a machine-checked mirror, not a second
authority.

For this registry, a carrier is any shipped executable composition that an
accepted ADR activates as a bounded capability, including Control, release,
and provisioning/operator compositions. Read-only diagnostic observers whose
accepted ADR explicitly says that no active carrier is added are outside the
inventory. Accordingly, `context-engine-model-materializer` is an
`ACTIVE_BOUNDED` carrier, while `context-engine-control preflight` is not a
carrier.

The closed v1 schema requires each carrier to have a unique ID and a unique
status-owner `(path, marker)` pair. It also requires repository-contained
`(path, marker)` references for accepted authority ADRs, executable entrypoints,
migrations or a closed N/A rationale, highest-public proof, and operator
documentation, plus closed CI targets and exact `NOT_ACTIVE` relations. Missing,
duplicate, dangling, escaping, marker-mismatched, status-drifted, or incomplete
relationships fail validation.

The validator derives each required proof lane mechanically from the
highest-public-test path: `tests/unit/` maps to `test`, `tests/catalog/` to
`catalog`, `tests/process/` to `smoke`, and `tests/integration/` to
`integration`. A carrier's gate targets must be exactly its derived proof
lane or lanes plus `check`; an otherwise live proof cannot be registered under
a different or additional lane.

`scripts/validate_active_carriers.py` validates the schema itself before using
it, validates registry shape and repository-contained references, and compares
the registry with the closed `STATUS.md` mirror. It returns a versioned,
content-free result with closed failure categories. `make catalog`, and thus
`make check`, executes this validation in CI.

Registry completeness and the executable Security veto are independent.
`COMPLETE` means only that every required carrier relationship is present and
live; it neither executes Security evidence nor activates or promotes a carrier,
and it cannot turn `NOT_ACTIVE`, unevaluated Security, or failed Security into
`PASS`. Activation still requires the carrier's accepted behavioral ADR and
proving seam.

## Rationale

One closed join makes omissions and drift mechanically visible without turning
a navigational ledger into architecture or merging capability coverage with a
release veto. Repository-contained references keep the result reproducible for
the reviewed checkout, while per-carrier ADR ownership preserves the deeper
behavioral decisions at their existing authority boundary.

## Consequences

- Every carrier addition, activation, deferral, removal, or status change must
  update the registry and its checked `STATUS.md` mirror in the same change.
- Malformed registry/schema JSON and schemas rejected by Draft 2020-12
  meta-schema validation return the fixed, content-free
  `REGISTRY_SCHEMA_INVALID` result on stdout with empty stderr.
- Accepted per-carrier ADRs, public tests, gates, and operator documents remain
  independently reviewable and cannot be replaced by a complete registry row.
- Release reporting retains ADR-0034's independent Security decision; the
  completeness result adds no aggregate score or promotion decision.

## Revisit trigger

Revisit when a second independently deployed registry consumer requires a new
transport, when carrier relationships cannot be represented by the closed v1
schema, or when signed external evidence must be admitted. Any successor must
preserve one canonical completeness owner, repository-reviewable provenance,
per-carrier behavioral authority, the checked human mirror, and independence
from the Security veto.
