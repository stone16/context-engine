---
name: adr-0081-seal-candidate-discovery-behind-a-data-only-session
version: "1.0.0"
description: >
  Seal candidate discovery behind a data-only result while retaining the
  Runtime transaction. Use when implementing the CandidateIndex seam. Not for
  changing authorization, ranking, or database transaction semantics.
---

# 0081. Seal candidate discovery behind a data-only session

- Status: accepted
- Date: 2026-07-29
- Refines: ADR-0067, ADR-0075

## Context

ADR-0067 requires candidate discovery to execute on the retained UserActor
transaction so FORCE RLS, current Membership, and the exact authorization
lifetime remain aligned. ADR-0075 requires every pre-Kernel stage to touch only
content-free references and rank evidence.

The first ranked-candidate composition passed the whole
`MaterializedProjectionSession` to the replaceable `CandidateIndex`. That
object retained a reachable content-bearing projection port: pre-Kernel code
could reach locator and projection operations even though its declared job was
only discovery. An independent security evaluation reproduced the capability
leak by reading and mutating content before authorization and carrying the
result through the normal resolve. A private attribute or a convention not to
call it is not a capability boundary.

The repair must preserve ADR-0067's same-transaction discovery without handing
the replaceable index the connection-owning session or any object graph that
can reach content-bearing operations.

## Decision

1. The replaceable `CandidateIndex` owns discovery request and result shaping.
   It prepares one narrow, data-only discovery request from the `Acquire` and a
   content-free discovery-scope view. After execution, it converts only the
   returned content-free references into named ranked candidate lists. Runtime
   later normalizes those lists into rank evidence for the sealed path.
2. Runtime, not the replaceable index, executes the prepared discovery request
   against the retained `MaterializedProjectionSession`. Execution uses the
   existing UserActor transaction and its existing database connection. It
   opens no connection and creates no transaction.
3. Runtime places the bounded tuple of exact `CandidateRef` results into a
   request-scoped `CandidateDiscoverySession`. That session is the only
   execution result passed to the replaceable `CandidateIndex`; it contains no
   materialized projection session, persistence port, connection, locator,
   projector, or callable that can recover any of them.
4. Runtime closes the `CandidateDiscoverySession` immediately after result
   shaping. The resulting `CandidateRef`s and rank evidence remain untrusted
   and content-free, and every candidate still traverses the sealed
   `CandidateRef → AuthorizationKernel → AuthorizedProjection` path.

## Rationale

The split preserves ownership at the seam: an index decides how to express its
narrow query and how to label and rank its content-free results, while Runtime
alone owns the authority-bearing session and executes the query. Copying only
primitive content-free results across that boundary removes the excessive
capability instead of relying on naming, privacy conventions, or cooperative
implementations.

## Consequences

- ADR-0067's substantive guarantees are unchanged: discovery still runs on the
  retained UserActor transaction and connection, FORCE RLS remains defense in
  depth, candidates remain content-free and untrusted, and no second connection
  or transaction is introduced.
- Content-bearing ports are now **structurally unreachable** from replaceable
  pre-Kernel code rather than merely unused by it; Runtime's trusted executor
  retains the session only to perform the prepared narrow request.
- Replaceable index implementations can shape requests and rank results, but
  cannot locate, hydrate, project, or mutate content before authorization.
- Adding a discovery operation requires a narrow data request plus Runtime-owned
  execution; extending `CandidateDiscoverySession` with a persistence or
  content capability is prohibited.

## Revisit trigger

Revisit before a measured retrieval requirement needs a discovery operation
that the narrow request vocabulary cannot express, or if an adversarial
capability-graph test finds any path from replaceable pre-Kernel code to a
content-bearing port. Any revision must retain same-transaction execution and
prove that only content-free references and rank evidence cross the seam.
