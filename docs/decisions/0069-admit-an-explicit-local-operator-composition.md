---
name: adr-0069-admit-an-explicit-local-operator-composition
version: "1.0.0"
description: >
  Allow one explicitly configured local operator composition that maps
  separately held secrets to one Control operator identity and one release
  operator identity, while no operator authority exists by default and no
  operator operation becomes network-reachable.
---

# 0069. Admit an explicit local operator composition

- Status: accepted
- Date: 2026-07-27
- Refines: ADR-0011, ADR-0033, ADR-0035, ADR-0063

## Context

Eleven `ControlOperation` members and the matching `ContextControl` methods
are implemented and proven under FORCE RLS, and `ContextLearning.promote` is
the sole release publication owner. None of them is composed by any shipped
application. `ControlOperatorAuthenticator` and `ReleaseOperatorAuthenticator`
are Protocols whose only implementations live under `tests/`, and
`alembic upgrade head` is likewise executed only from `tests/`.

The consequences compound. No shipped command brings a database to schema
head, registers a ContextSource, schedules an acquisition, reads source
progress, or promotes a Release. ADR-0068 refuses to activate the dogfood
Runtime unless an already-promoted Release exists whose index profile digest
matches, so the served composition cannot boot on a fresh machine at all.
ADR-0062 orders the roadmap by observed dogfood pull, but no maintainer can
place a single note into the system, so the ordering rule that governs what
this repository builds next cannot execute.

ADR-0063 already resolved this class of problem for the read plane: one
explicitly configured local trust composition, with the default composition
and the entire authorization chain unchanged. The write plane has no
equivalent, and ADR-0011 separates the two planes precisely so that one
cannot be solved by widening the other.

## Decision

One local operator composition is admitted under these fixed boundaries:

1. **Explicit opt-in only.** Operator authorities are constructed only when
   local configuration explicitly supplies operator credentials. There is no
   default operator identity: absent configuration, every Control operation
   and every promotion is refused, and the refusal is generic and
   non-enumerating.
2. **Local process only.** Operator operations are reachable exclusively from
   a local process entry point. No operator operation is added to the HTTP
   ingress, the OpenAPI contract, the generated SDK, or any other
   network-reachable surface.
3. **Identity verification is the only simplification.** The composition
   verifies a locally configured secret and resolves it to one
   `VerifiedControlOperatorIdentity` with an explicitly enumerated
   `allowed_operations` set. From there `ControlOperatorAuthority`, the
   one-operation `TrustedControlCall` lifetime, the `ContextControl` store
   ports, the least-privilege database role, and FORCE RLS run unchanged. No
   authority, gate, or ownership fence is replaced or narrowed.
4. **One operation per invocation.** Each operator subcommand declares
   exactly the one `ControlOperation` it performs and obtains its own trusted
   call. A subcommand never holds ambient authority for operations it does
   not perform, and no invocation may request the full operation set.
5. **Separate credentials, separate planes.** The Control operator
   credential, the release operator credential, the ADR-0063 dogfood runtime
   credential, and the worker credential are four distinct configured
   secrets. Keeping the release operator credential separate preserves
   ADR-0033's single publication owner: the ability to register and ingest
   never implies the ability to activate a ReleaseManifest.
6. **Schema bootstrap is not a Control operation.** Applying migrations runs
   under the migration role, asserts that role, and carries no operator
   identity, no Organization context, and no `ControlOperation`.
7. **Not a production ancestor.** Production operator authentication —
   multiple operators, durable role assignment, delegation, an administrative
   API — is a separate future composition with its own ADR. This composition
   is never widened into it; a second operator identity requires that ADR
   first.

The prohibited shortcut is exposing any operator operation on a network
surface, reusing the dogfood runtime or worker credential as an operator
credential, granting one configured identity the full `ControlOperation` set
together with promotion authority, calling `ContextControl` store ports or
`ContextLearning.promote` from the entry point without passing through the
operator authorities, or performing Control operations under the migration
role.

## Rationale

ADR-0062's pull rule has no falsifier while ingestion has no entry point, and
the three honest alternatives are each worse. Continuing to drive Control
from pytest fixtures makes the maintainer's own corpus a test artifact and
leaves the served process unbootable, which is the state this ADR exists to
end. Building a production administrative API now front-loads multi-operator
role assignment and delegation for exactly one operator. Widening the
ADR-0063 dogfood credential to cover writes would collapse the read and write
planes that ADR-0011 separates, and would hand the single reader identity the
authority to publish.

A single explicitly configured local entry, with every authority, ownership
fence, and RLS predicate intact, changes who can invoke an already-proven
operation without changing what any operation is permitted to do.

## Consequences

- A shipped local entry point can bring a database to schema head, register a
  File source, activate its change feed, schedule and accept change pages,
  read source progress, and promote a Release under explicit local
  configuration.
- ADR-0062's pull rule becomes executable for the first time, and the
  golden set of the evaluation work can be authored against a real corpus
  with real lineage references instead of fixtures.
- This ADR is a kernel-lane change under ADR-0064 because it introduces
  authentication compositions. It ships with full ceremony: catalog
  activation evidence that absent configuration refuses every Control
  operation and every promotion, that each subcommand carries exactly one
  `ControlOperation`, and that the Control and release credentials are not
  interchangeable in either direction.
- The subcommands built on top of this composition are product lane under
  ADR-0064; they compose existing proven modules and register no catalog
  invariant.
- `STATUS.md` records the composition's existence and its opt-in boundary so
  that reject-all claims about the served HTTP application remain accurate
  and are not read as covering the local write plane.

## Revisit trigger

Revisit before a second operator identity, any network-reachable operator
surface, any operator credential shared with the runtime or worker planes, or
any attempt to derive production operator authentication from this
composition.
