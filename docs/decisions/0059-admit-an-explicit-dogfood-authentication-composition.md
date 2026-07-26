---
name: adr-0059-admit-an-explicit-dogfood-authentication-composition
version: "1.0.0"
description: >
  Allow one explicitly configured local trust composition that maps a
  locally held secret to one seeded Membership-backed UserActor, while the
  default composition stays reject-all and the full authorization chain
  remains untouched.
---

# 0059. Admit an explicit dogfood authentication composition

- Status: accepted
- Date: 2026-07-26
- Refines: ADR-0021, ADR-0023

## Context

The default application composition rejects every credential, and the only
compositions that resolve non-empty results are test compositions. ADR-0058
requires a real caller: the maintainer's own tooling must reach
`ContextRuntime.resolve` and receive real Evidence. Building production
authentication (external identity provider, account and admin workflows) for
a single local caller is premature, but any ad-hoc trust shortcut would erode
the reject-all posture that the README and ADR-0021 commit to.

## Decision

One dogfood authentication composition is admitted under these fixed
boundaries:

1. **Explicit opt-in only.** The composition is constructed only when local
   configuration explicitly supplies it (environment-provided secret and
   seeded identity references). The module-level default composition remains
   reject-all, unchanged.
2. **Identity verification is the only simplification.** The composition
   verifies a locally configured secret and resolves it to one pre-seeded
   Organization, User, and current Membership; from there the standard
   lifetime-bound `UserActor` transaction, sealed AuthorizationKernel,
   EffectiveScope intersection, FORCE RLS, Policy Epoch check, and Evidence
   lineage run unchanged. No kernel dependency, gate, or budget is replaced
   or narrowed.
3. **Secret hygiene.** The secret is supplied through local configuration,
   never committed, never logged, and never echoed in responses; rejection
   remains generic and non-enumerating.
4. **Not a production ancestor.** Production authentication is a separate
   future composition with its own ADR. The dogfood composition is never
   widened into it and is disabled in any deployment serving a second human.

The prohibited shortcut is enabling the dogfood authenticator in the default
composition, resolving it to an Organization without a current Membership
check, minting trusted delivery facts from the wire body, or extending the
dogfood secret into a multi-user or production credential scheme.

## Rationale

Dogfood pull (ADR-0058) fails without a legitimate caller, and the two honest
alternatives are both worse: full production authentication now front-loads
cost with no second user, while test-composition reuse in a served process
would blur the one boundary the repository most loudly guarantees. A single
explicitly configured trust entry, with the entire authorization chain intact,
changes who can knock on the door without changing what any caller may see.

## Consequences

- The served process can, under explicit local configuration, deliver real
  authorized ContextPackages to the maintainer's tooling.
- README and gate prose must state the dogfood composition's existence and
  its opt-in boundary so reject-all claims stay accurate.
- This change touches the kernel lane of ADR-0060: it ships with full
  ceremony — catalog activation evidence that the default composition still
  rejects all credentials and that the dogfood path enforces Membership,
  RLS, and Policy Epoch checks.
- Seeding one Organization, User, and Membership for dogfood use becomes a
  supported local operation.

## Revisit trigger

Revisit before any second human caller, any network exposure beyond the
maintainer's own machines, or any attempt to derive production authentication
from this composition.
