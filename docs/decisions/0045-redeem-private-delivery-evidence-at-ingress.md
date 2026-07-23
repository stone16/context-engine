---
name: adr-0045-redeem-private-delivery-evidence-at-ingress
version: "1.0.0"
description: >
  Issue and redeem digest-only, request-bound private DeliveryEvidenceRef values
  through dedicated identity and Runtime database capabilities before content work.
---

# 0045. Redeem private delivery evidence at ingress

- Status: accepted
- Date: 2026-07-23
- Refines: ADR-0002, ADR-0007, ADR-0013, ADR-0015, ADR-0017, ADR-0023, ADR-0025

## Context

M2 introduces a remote trusted Bot application process. Transport authentication
can establish which service is calling, but the Bot cannot author Organization,
asker Membership, purpose, private destination, consumer, audience, or Policy
Epoch facts in an HTTP body. Passing those facts directly would turn the remote
caller into an authorization authority. Passing the platform credential or raw
audience evidence would also enlarge the wire and logging exposure.

The direct authenticated HTTP path already constructs one
`AuthenticatedInvocation`, retains one current UserActor transaction, and calls
the sealed Runtime. The first private delivery carrier must reuse that path and
must fail before candidate discovery or content work when its trusted evidence is
missing, stale, forged, or bound to another request.

Public repositories may inform clean-room behavioral observations and test
oracles, but their implementations cannot supply this trust boundary. The
ContextEngine design, accepted ADRs, threat model, and repository evidence report
remain the implementation and public-provenance authorities.

## Decision

The trusted identity Adapter issues one random opaque `DeliveryEvidenceRef` for
an exact private resolve request. Its versioned profile supplies the maximum
lifetime and digest/retention rules. The bearer exists only in the issuer result
and authenticated transport metadata. PostgreSQL stores only its SHA-256 digest,
plus the private trusted bindings required for exact redemption:

- Organization, User, Membership, and Membership version;
- authenticated service and authentication binding;
- resolve request id, private destination, consumer, purpose, and audience
  digest;
- current Organization Policy Epoch, issued time, expiry, profile, and one stable
  logical resolution reference.

A dedicated `context_engine_identity` NOINHERIT login can execute issuance and
Organization-scoped expired-row cleanup functions. It has no table read or
direct table mutation privilege. A separate NOLOGIN SECURITY DEFINER role owns
the functions and exact table privileges. Runtime can execute only the redemption
function. Control, worker, learning, security-operator, and ordinary tenant roles
can neither read the table nor execute either side's capabilities.

Issuance verifies one current same-Organization Membership and exact current
Policy Epoch. The Organization/service/request unique boundary prevents a retry
or conflicting destination, consumer, or audience from creating a second logical
identity. Expired cleanup uses database-owned current time and deletes only
private evidence in one requested Organization. Production composition supplies
the versioned lifetime profile; no volatile lifetime is embedded in prose or a
generic application default.

Ingress accepts the opaque reference only in authenticated HTTP metadata. It
hashes the value in memory and redeems it through the existing current UserActor
transaction. Redemption requires exact service, authentication binding, request,
Organization, User, Membership/version, authenticated route destination and
consumer, recomputed private audience digest, purpose, Policy Epoch, issuance,
expiry, private kind, and current durable Membership/epoch state. The first successful
redemption records a stable trusted request time; an identical
retry observes the same logical resolution reference and does not refresh or
broaden authority.

Only a successful redemption can construct the nominal private
`TrustedDeliveryContext`. The sealed `Runtime.resolve` entry remains unchanged:
private and direct delivery both traverse the same Policy, authorization,
projection, budget, provenance, audit, and final epoch gates. Forged, expired,
cross-request, wrong-service, wrong-Organization, stale-Membership, stale-epoch,
or non-private references map to the same external authentication failure before
Provider, index, Package, model, or effect work. Authority failure maps to the
existing generic service-unavailable response.

Bearer and trusted private-delivery carriers redact their representations. The
bearer, destination, consumer, audience digest, and other raw trusted
private-delivery or audience facts are absent from wire bodies, responses,
ContextRun, DecisionAudit, errors, ordinary authentication/invocation/outcome
representations, and logs. ContextRun continues to retain the trusted identity
lineage required by ADR-0031 without retaining the bearer or private-delivery
bindings. The schema downgrade is permitted only while the evidence table is
empty; committed evidence requires forward repair or explicit expiry cleanup.

This decision activates only the private Acquire DeliveryEvidenceRef carrier.
Group audience snapshots, public-group resolution, EgressGrant, ModelGateway,
ActionPlane, BotDelivery, OpenCitation, the frozen OpenAPI contract, and the
generated TypeScript SDK remain inactive under their owning issues.

## Rationale

Digest-only persistence makes a database disclosure insufficient to replay the
bearer. Exact durable bindings make the locator an attestation lookup rather than
a transport-authored authorization grant. Keeping redemption in the current
UserActor transaction closes the gap between Membership validation, Policy Epoch,
field projection, ContextRun persistence, and delivery-context construction.

The dedicated identity login prevents Runtime, Control, and Supply code from
minting or rebinding trusted delivery evidence. The NOLOGIN definer contains the
minimal cross-row authority without granting an application role direct tenant
table access. Stable logical redemption identity provides retry safety without
making the bearer one-shot or extending its lifetime.

Forking or copying a general-purpose open-source RAG implementation was rejected
because its trust topology would become an unreviewed runtime foundation and
cannot establish ContextEngine's sealed authorization and multi-tenant evidence
invariants. Clean-room behavioral reference remains useful, but the security
boundary and its executable oracles are implemented from ContextEngine's own
authorities.

## Consequences

- A private authenticated HTTP Acquire can deliver File-backed authorized
  Evidence without accepting raw trusted delivery facts on the wire.
- One stolen reference is useless outside its exact service, binding, request,
  Organization, asker Membership/version, purpose, Policy Epoch, and lifetime.
- The database retains private trusted facts but never the bearer; only the
  migrator and NOLOGIN definer can read the table.
- Cleanup is explicit, Organization-scoped, expiry-only, and cannot delete live
  evidence.
- The direct authenticated caller path remains available and cannot manufacture
  an `AudienceSnapshot` or private delivery provenance.
- M2 contract freeze and Bot application activation still require their later
  issues; this ADR does not claim those carriers.

## Revisit trigger

Revisit before group/public delivery evidence, multiple private delivery kinds,
profile rotation, partitioned retention, an external identity service, or a
durable response cache keyed by logical resolution identity. Any revision must
preserve digest-only bearer storage, exact request and audience binding, current
Membership/epoch revalidation, least-privilege role separation, generic failure,
zero pre-authorization content work, and the single sealed Runtime path.
