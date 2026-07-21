---
name: adr-0023-bind-runtime-to-current-membership-user-actor
version: "1.0.0"
description: >
  Bind online Runtime Acquire to one current Membership, a complete
  transaction-local UserActor, and a nominal proof that remains live through
  ContextPackage construction.
---

# 0023. Bind Runtime to a current Membership-backed UserActor

- Status: accepted
- Date: 2026-07-21
- Refines: ADR-0015, ADR-0017, ADR-0020, ADR-0021, ADR-0022

## Context

ADR-0022 activates an evidence-free Runtime Acquire while deliberately leaving
complete request database binding to Issue #11. ADR-0020's earlier
Organization-only transaction is isolation evidence, not sufficient online
authority: a global User has no Organization rights without one current
Organization-scoped Membership, and an Organization value alone cannot identify
the acting member.

Checking Membership before opening the Runtime transaction would also create a
reusable, stale authorization result. The trusted identity, PostgreSQL RLS
context, nominal Runtime operand, and current durable Membership must instead be
one request-bound operation. Invalid identity categories must remain
indistinguishable to callers, while database unavailability must not be
misreported as an invalid identity.

## Decision

### Trusted identity and current Membership

The trusted authenticator supplies canonical internal `user_ref` and
`membership_ref` values plus an exact positive integer `membership_version`, in
addition to the already trusted Organization, Principal, application, Agent,
and authentication-binding facts. These values never come from the Acquire
body. Ingress does not infer a default Membership, guest Membership, or
service-principal fallback.

User remains global and carries no tenant rights. Membership is scoped to one
Organization and one User, with at most one Membership for an
Organization/User pair. Its tenant key contains both Organization and
Membership identifiers; neither current lookup nor a tenant-owned reference may
treat the Membership identifier alone as ownership. For the request's trusted
UTC check time, a Membership is current only when the authenticated
Organization, Membership, User, and version match the same durable row exactly,
its status is `active`, its validity has begun, and its exclusive end time, when
present, has not been reached. A missing row, inactive or revoked status,
not-yet-valid or expired interval, stale version, mismatched User, or
cross-Organization reference is not current.

Principal and Agent references remain trusted identity facts in this slice;
carrying them does not activate Principal grants, Agent delegation, roles, or
content scope.

### One PostgreSQL transaction and complete UserActor context

The Membership authority begins one real PostgreSQL request transaction before
current-Membership validation. Before querying the Membership, it sets and
reads back the complete transaction-local UserActor context: actor kind,
Organization, User, Membership identifier and version, Principal, request
identifier, authentication binding, and trusted check time. A missing,
malformed, partial, or mismatched setting fails closed.

The same transaction remains open through Membership validation, trusted
invocation construction, `ContextRuntime.resolve`, ContextPackage construction,
and HTTP response construction. Successful completion commits only after that
scope exits. Error and cancellation paths roll back, and pooled connections
must not retain any actor setting.

The non-owner Runtime role sees the Membership and the current representative
tenant-owned record only through FORCE RLS policies that require this exact
current UserActor. Runtime has read-only access to Membership; writes to the
representative tenant record require the same context through both policy
checks and the existing fail-fast write guard. Organization-only, User-only,
stale, partial, or absent context exposes zero tenant rows and grants no write
authority. This complete online protocol replaces ADR-0020's staged
Organization-only access; that boundary remains historical database evidence,
not an alternate Runtime path.

### Nominal proof lifetime

Only the trusted Membership authority may construct a nominal
`CurrentMembershipVerification` and its exactly matching `UserActor`. Neither
type is caller-constructible or serializable. The proof is bound to the full
Organization, User, Membership, version, Principal, request, authentication,
and check-time tuple and to a private authority scope that is active only while
the owning database transaction is open.

Runtime validates the UserActor's nominal type, trusted construction
provenance, exact tuple match, and active proof scope at its public seam before
the policy pipeline. Closing the Membership authority scope invalidates the
proof, so neither the proof nor an invocation carrying it can become reusable
authorization outside the request transaction.

### External outcomes and content boundary

An exactly matching active Membership preserves Issue #10's successful,
evidence-free empty ContextPackage, including its sealed Kernel path and zero
index, provider, or source-content I/O. Current Membership is necessary tenant
authority; it is not sufficient content authorization and does not add Evidence
or broaden the package.

Malformed required trusted identity facts and every non-current Membership
category produce the same generic `401 authentication_failed` response and
Bearer challenge before Runtime or content I/O. The response does not reveal
whether a User, Membership, or Organization exists or which check failed.

An inability of the required PostgreSQL Membership authority to open, bind,
read back, or validate its transaction produces the generic
`503 service_unavailable` response before Runtime. A failure while completing
that transaction produces the same response and prevents the prepared success
response from escaping. Neither case is collapsed into the authentication
response or exposes database detail, and the evidence-free path still performs
zero content I/O.

### Explicit exclusions

Issue #12 and later own every broader authorization capability. In particular,
this decision does not activate:

- Principal grants, role semantics, AgentVersion delegation ceilings,
  EffectiveScope, or request-scope intersection, which begin in Issue #12;
- Candidate retrieval, Resource ACL, exact hydration authorization, Evidence,
  or any other content-bearing path, which begin in Issue #13 and later;
- denied-versus-nonexistent content equivalence beyond the existing empty path,
  which is owned by Issue #14; or
- production OAuth or external-identity selection, ServiceActor/WorkerLease
  authority, Policy Epoch, audience authorization, durable audit/package
  records, release selection, or egress grants.

## Rationale

One transaction and one lifetime-bound nominal proof make the durable
Membership decision inseparable from the Runtime operation that consumes it.
The full tuple prevents an authenticated User or Membership identifier from
being replayed under another Organization, request, version, or authentication
binding. FORCE RLS then treats PostgreSQL as authorization truth instead of
trusting an application-side tenant filter.

Separating generic authentication failure from generic authority
unavailability preserves non-enumeration without turning an infrastructure
incident into a false identity denial. Keeping the valid output empty proves
the new authority boundary without manufacturing the grant and content
semantics owned by later issues.

## Consequences

Issue #11 activates the minimum User and Membership persistence and the online
UserActor transaction for the bounded empty Acquire path. The default
application remains fail closed until owning production authentication,
Organization, and Membership authorities are deliberately composed; seeded or
deterministic authorities remain conformance twins only.

Required evidence spans the HTTP, domain, and real PostgreSQL seams. It must
cover the active and every non-current Membership category, byte-equivalent
generic invalid responses, generic database unavailability, zero downstream
content calls, non-owner FORCE RLS, cross-Organization isolation, partial and
missing actor context, proof invalidation after scope exit, rollback, and pooled
connection reuse. A green active-Membership case proves only the same empty
ContextPackage already fixed by ADR-0022.

## Revisit trigger

Revisit when Issue #12 adds the first EffectiveScope operands, when Issue #13
adds the first content-bearing authorization path, when a production identity
provider owns the trusted references, or when a ServiceActor first enters a
worker transaction. Each refinement must preserve exact current-Membership
validation, one transaction through the consuming operation, nominal proof
lifetime, generic invalid responses, distinct generic unavailability, and
zero-rights behavior for missing or partial context.
