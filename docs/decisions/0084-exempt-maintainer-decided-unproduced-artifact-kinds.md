---
name: adr-0084-exempt-maintainer-decided-unproduced-artifact-kinds
version: "1.0.0"
description: >
  Refine distribution-completeness checks so a maintainer-decided unproduced
  artifact kind is explicit and schema-validated without weakening evidence
  checks for produced artifacts. Use when a maintainer explicitly declares an
  artifact kind unproduced. Not for skipping attribution, license, notice, or
  SBOM evidence for an artifact that is produced.
---

# 0084. Exempt maintainer-decided unproduced artifact kinds

- Status: accepted
- Date: 2026-07-30
- Refines: ADR-0074 §4

## Context

ADR-0074 §4 requires complete attribution and SBOM evidence in every built
artifact kind. The first executable governance check treated an absent wheel,
source distribution, npm tarball, or container image as a failure. That is the
correct fail-closed default, but it also made the repository-wide verification
chain permanently fail for a container image that the project does not yet
produce.

An absent artifact cannot prove whether it was intentionally deferred or
accidentally omitted. That distinction needs tracked maintainer authority; an
implicit skip or code-only allowlist would lose the decision and could silently
outlive it.

## Decision

An artifact kind may report `NOT_PRODUCED` only when a tracked exemption record
declares it `not-produced-by-maintainer-decision`. The governance schema
requires each exemption to name the artifact kind and carry a non-empty approval
reference. Missing policy, malformed records, duplicate exemptions, and absent
approval references fail validation.

The container kind is exempt under
`stone16/context-engine#142 maintainer decision 2026-07-30` until a container
build ships. Its check emits the loud status
`container: NOT_PRODUCED (maintainer decision)` and succeeds. An unproduced kind
without an exemption remains a failure.

An exemption applies only to absence. If an artifact path is supplied, the
artifact must exist and the normal physical LICENSE, NOTICE, third-party notice,
and SBOM checks apply. An exemption can never turn missing, stale, or
uninspectable evidence in a produced artifact into success.

## Consequences

- Repository-wide verification can remain green while the maintainer-decided
  set of shipped artifact kinds is explicit.
- Every non-production decision is reviewable in tracked configuration and
  attributable to a maintainer approval.
- There are no silent skips: exempt absence has its own status, while
  unapproved absence and incomplete produced artifacts remain closed failures.
- The container exemption must be removed in the same change that introduces a
  shipping container build. That build must satisfy ADR-0074 §4 before release.

## Revisit trigger

Remove the container exemption when any ContextEngine container build becomes a
shipped artifact. Revisit the mechanism if artifact production moves to an
external release system whose produced-kind inventory cannot be established by
the repository check.
