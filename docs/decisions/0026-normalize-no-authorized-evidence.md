---
name: adr-0026-normalize-no-authorized-evidence
version: "1.0.0"
description: >
  Make denied, cross-Organization, and nonexistent Acquire candidates share one
  externally indistinguishable no-authorized-Evidence outcome.
---

# 0026. Normalize every no-authorized-Evidence Acquire outcome

- Status: accepted
- Date: 2026-07-21
- Refines: ADR-0012, ADR-0017, ADR-0019, ADR-0022, ADR-0025

## Context

The first content-bearing Runtime slice can now distinguish internal states that
must not become a Resource-existence oracle. A Candidate may name an existing
cross-Organization Fragment hidden by FORCE RLS, an existing same-Organization
Fragment outside the exact EffectiveScope, or lineage that does not exist.
Hydration may also return no body after a locator was selected. Returning
different statuses, shapes, headers, counts, or reasons for these branches would
let a caller enumerate protected Resources.

`ACCEPT-011` and `NON-ENUMERATION-009` already freeze deterministic comparison
of denied and missing probes. Statistical latency equivalence is a separate M5
gate and is not evidence supplied by this decision.

## Decision

For the current `resolve(Acquire)` carrier, every branch that leaves zero exact-
authorized Evidence returns the same public outcome:

~~~text
HTTP 200 resolved
  -> empty ContextPackage
  -> blocks = evidence = gaps = []
  -> zero budget usage
  -> coverage = empty / no_authorized_evidence
~~~

`no_authorized_evidence` is tenant-safe coverage, not a Provider gap. The
response never reports a denied or candidate count, Resource name or identifier,
existence detail, or authorization reason. A denied Candidate remains equivalent
when ranked first or when it is the only Candidate. The authorized control from
ADR-0025 continues to return its exact block and Evidence; this decision does
not turn infrastructure errors into successful empty Packages.

The deterministic oracle compares status, body, the closed product headers, and
the Runtime domain outcome. It may normalize only the pre-registered per-resolve
values, in this order:

1. `body.package.organizationRef`
2. `body.package.decisionRef`
3. `body.package.asOf`
4. `body.package.expiresAt`
5. `headers.X-Context-Request-Id`

The compared product headers are `Content-Type`, `Cache-Control`, and
`X-Context-Request-Id`. Incidental framework or server headers are not part of
the product contract. Fixed zero usage, including `elapsedMs`, is compared and
cannot be normalized away.

The sealed audit gate currently records only the generic
`no_authorized_evidence` category, authorized Evidence count zero, and denied
detail count zero. It retains no existence-specific branch. A later restricted
DecisionAudit may add redacted operator categories only when they are bound to
the trusted invocation Organization, contain no Candidate/Resource content or
counts, remain absent from `Resolved` and HTTP, and cannot treat hostile
Candidate metadata as an observed fact.

## Rationale

One useful typed coverage reason lets callers distinguish an empty Context
Package from transport or infrastructure failure without learning why no
Evidence survived. Keeping the allowlist narrow makes the executable comparison
fail when a new field accidentally becomes existence-dependent. Exercising the
real non-owner PostgreSQL transaction and HTTP seam proves that RLS not-found and
Kernel denial converge at the public boundary while the authorized control
remains live.

## Consequences

Runtime and HTTP regression suites compare cross-Organization, same-Organization
denied, denied-first, and missing Candidate probes. The security catalog freezes
the comparison fields, normalization allowlist, headers, four empty Packages,
and one CandidateIndex discovery per probe. Closed wire models continue to
reject added denial, Candidate, and existence fields.

This decision makes no constant-time, latency-bucket, or statistical timing
claim. It introduces no sleep, retry, citation-open, Provider API, or public
Resource lookup behavior.

## Revisit trigger

Revisit when a new public read carrier, Provider outcome, citation-open path, or
restricted durable DecisionAudit activates, or at the separately preregistered
M5 timing gate. Every revision must preserve the generic empty outcome,
document an exact normalization allowlist, keep infrastructure failure distinct,
and retain an authorized control.
