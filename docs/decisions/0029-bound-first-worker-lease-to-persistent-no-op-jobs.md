---
name: adr-0029-bound-first-worker-lease-to-persistent-no-op-jobs
version: "1.0.0"
description: >
  Activate the first signed one-shot WorkerLease only for a persistent no-op
  durable job while preserving the complete Supply lease carrier as deferred.
---

# 0029. Bound the first WorkerLease to persistent no-op jobs

- Status: accepted
- Date: 2026-07-22
- Refines: ADR-0008, ADR-0015, ADR-0019, ADR-0027

## Context

The complete `WORKER-LEASE-007` contract binds every authority-bearing Supply
dimension and checks it against current durable state. Issue #17 introduces the
first real API/worker lease path, but it has no Source, Resource, Revision,
Policy Epoch, idempotent business mutation, outbox, or File publication carrier.
Treating its no-op lifecycle as proof of that complete contract would create a
false-green release claim.

The first carrier is owned by the independent worker application boundary and
changes durable job state. It therefore needs a signed, expiring, exact-job
capability, a registered
least-privilege service identity, and atomic one-shot redemption rather than an
ambient worker credential or an in-memory replay check.

## Decision

Issue #17 activates only a persistent no-op durable-job subcarrier under
`WORKER-LEASE-007`.

The Control-side issuer mints a versioned, domain-separated canonical token
authenticated with standard-library HMAC-SHA256. Signing and verification
receive an explicit, injected, versioned keyring; there is no environment
lookup, default key, or implicit active version in the domain contract.
Canonical encoding rejects unknown, missing, duplicate, or non-canonical fields
before signature use. Lease issuance time comes from the database transaction,
and the issuer applies a bounded server-owned lifetime; neither timestamp is
accepted from an issuance request.

The token binds the Issue #17 fields that exist: Organization, durable job,
registered `ServicePrincipal`/`ServiceActor`, workload, worker audience, the
exact persistent no-op operation, issued-at, expiry, and nonce. The current
durable job row is
the authority for those same values. A valid signature alone never authorizes
work.

The untrusted redemption carrier contains only the opaque token plus independent
Organization/job routing references. The receiver's registered
ServicePrincipal, workload, audience, operation, and current time come from the
worker authority's trusted composition, not from that carrier. A local clock can
reject early, while PostgreSQL transaction time remains the final expiry
authority.

Issuance and completion use separate, narrowly granted `SECURITY DEFINER`
functions owned by a dedicated non-login, non-owner role with fixed
`search_path`, forced RLS, and exact `session_user` checks. Control can execute
only issuance; the worker can execute only completion and has no direct job
`UPDATE`. The completion function atomically compares the current no-op job row,
including the server-stored SHA-256 nonce digest, and performs the only state
change. Only the expected leased state and unconsumed nonce may transition once;
replay, expiry, tampering, wrong Organization/job/actor/workload/operation,
stale state, and concurrent losers make no new durable change. The worker never
impersonates the triggering user.

Rejections expose one generic unavailable result. Restricted audit records only
a safe reason category plus digests; it does not record the token, signature,
key material, raw claims, or tenant content.

Issue #17 does not bind a Policy Epoch. Its activation record therefore states
`not-bound-issue-17`, not `organization-v0`. Source, Resource, Revision, Policy
Epoch, end-user delivery audience, idempotency, generation, content-bearing
mutation, outbox, and
File publication remain `NOT_ACTIVE`. The complete `ACCEPT-008` parameterized
fixture remains preserved as future authority and is not reported PASS by this
bounded activation.

## Rationale

HMAC-SHA256 is sufficient for a server-minted/server-verified capability within
the current trust boundary and is available without adding a cryptography
dependency. Domain separation, canonical bytes, and explicit key versions make
algorithm and rotation behavior testable. Durable compare-and-set redemption
puts one-shot authority at the same transaction boundary as the only state
change it permits. Keeping direct table mutation away from the worker role
prevents valid database credentials and caller-authored GUC values from becoming
an alternate completion path.

## Consequences

- Unit evidence can prove canonical signing, versioned-key selection, tamper
  rejection, expiry, and generic rejection without claiming database authority.
- Real PostgreSQL evidence is required for registered service identity,
  database-owned lease time, function ownership/grants, denial of direct table
  mutation, exact-row comparison, atomic one-shot completion, rollback, and
  concurrency.
- The real worker application seam plus PostgreSQL proves one persistent no-op
  completion and replay rejection; default CLI smoke still proves only process
  boot/readiness because no production key source or job loop exists yet.
- The catalog carries a third exact activation record while retaining the full
  `WORKER-LEASE-007` and `ACCEPT-008` oracles as accepted but deferred.

## Revisit trigger

Revisit before any Source/File acquisition, Resource or Revision mutation,
Policy-Epoch/end-user-delivery-audience-bound lease, idempotent business effect,
generation rollover,
outbox dispatch, or publication carrier is activated. That owner must extend
the durable-row binding and independently run the corresponding full
`ACCEPT-008` cases; this no-op proof cannot be promoted by implication.
