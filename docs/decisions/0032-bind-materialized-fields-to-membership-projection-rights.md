---
name: adr-0032-membership-field-projection-rights
version: "1.0.0"
description: >
  Intersect exact Resource authorization with current Membership/version field
  rights before structured Fragment values leave PostgreSQL.
---

# 0032. Bind materialized fields to current Membership projection rights

- Status: accepted
- Date: 2026-07-22
- Refines: ADR-0012, ADR-0015, ADR-0024, ADR-0025, ADR-0031

## Context

The first materialized Runtime tracer stores one opaque `content` body per
Fragment. That proves content-free candidate discovery, exact Resource
authorization, and same-transaction projection, but it cannot represent the
canonical M0 case where two current Memberships in one Organization may read
different fields of the same Resource. Filtering a complete body after reading
it would allow private bytes into Runtime before field authorization. Adding
field names to `CandidateRef`, `Acquire`, or the seven-operation scope algebra
would instead let candidate or caller data influence authority and would turn a
finite Resource target into an implicit wildcard.

Existing body Fragments are also security-relevant during migration. Treating a
missing field policy as unrestricted would create an upgrade bypass for every
legacy row.

## Decision

Resource authorization and field projection are two monotonic intersections.
The existing seven trusted operands and optional request narrowing first decide
whether an exact Resource target is authorized. Only after the content-free
locator matches that target may the retained current-UserActor transaction
project fields. A separate `membership_resource_field_right` relation binds one
Organization, exact Membership and Membership version, Resource, and field
reference. Its absence is denial; there is no wildcard, owner fallback, public
fallback, or request-authored field list.

Structured Fragments use immutable `context_fragment_field` rows with an exact
field reference, deterministic ordinal, and text value. One Fragment has at
most 64 projected rows, matching the public Package contract. PostgreSQL FORCE RLS
permits Runtime to see a field row only when the transaction-local current
Membership/version has the matching right and the current Principal still has
an allowed Resource ACL. The rights relation itself is filtered by the same
live Resource ACL. Before the single projection statement, Runtime takes a
transaction-level shared advisory lock scoped to the Organization; every right
insert, update, or delete takes the matching exclusive lock through a database
trigger. A concurrent right revocation therefore either commits before
projection and yields no row, or waits until the authorized delivery transaction
has completed. The Organization-scoped lock may conservatively serialize
unrelated right changes in that tenant, but cannot broaden authority. Neither
denied values nor denied field names cross
the database boundary between statements. Thus the persistence port receives
only already-reduced values; it never loads a complete private map and filters
it in Python. The exact returned field set is also its nominal projection
ceiling, so the sealed Kernel rejects any adapter result outside that ceiling.
Resource ACL mutation is not serialized by this field-right lock; it continues
through ADR-0027's atomic access-policy/Organization-epoch change and final
current-epoch delivery gate.

`context_fragment.projection_kind` is a closed `body | fields` discriminator.
An old opaque body is the single explicit field `body`; it is visible only with
an exact `body` right and a current allowed Resource ACL. Migration grants no
implicit rights. A structured
Fragment has no `content` value, and a body Fragment has no structured field
rows. The `body` reference is reserved and rejected in structured field rows.
Immutable Revision/Fragment behavior continues to apply to field rows.

The fixed content-bearing order becomes:

~~~text
content-free CandidateRef
  -> same-transaction active-lineage locator
  -> exact EffectiveScope Resource membership
  -> current Membership/version projection ceiling
  -> PostgreSQL/RLS-reduced field values
  -> Kernel validation against that ceiling
  -> AuthorizedProjection
  -> PackageBudget and assembly
~~~

`AuthorizedProjection` and public `Evidence` carry the exact ordered projected
field references. Projection integrity, Evidence integrity, Evidence reference,
and the public `context-package-canonical-json-v2` digest bind those references
together with the rendered body. Version 2 adds `projectedFields` to the exact
public Package document; persisted version 1 rows remain historical lineage but
new ContextRuns use version 2. Structured bodies render deterministically as one
`field_ref=field_value` line per ordinal. Ordinary values remain exact; inside a
structured value, `\\` becomes `\\\\`, `=` becomes `\\=`, and every Unicode
line separator becomes its lowercase `\\uXXXX` form. This closed escaping order
is reversible and keeps one physical line per field. Legacy `body` renders as
its exact value. ContextRun continues to retain only authorized Evidence
references and the Package digest, never field values, denied field names, or
denied counts.

The internal PostgreSQL materialized projection is not an external provider
call. PackageBudget's provider-call usage therefore remains zero; the catalog's
separate acceptance instrumentation may count the single source-projection
operation without redefining public budget accounting.

## Rationale

Keeping field reduction inside PostgreSQL makes the least-privilege row policy,
current Membership check, Resource authorization, and content read part of one
transaction. Exact version binding makes stale Membership rights inert without
inventing a second epoch. A normalized immutable field relation makes it
possible for RLS to withhold private values before they cross the database
boundary, unlike a JSON body that application code must first read.

Separating Resource targets from field rights preserves ADR-0024's finite
seven-operation algebra. Binding projected field references into every content
integrity layer detects either label-only or body-only alteration and makes the
HTTP `projectedFields` evidence executable rather than descriptive.

## Consequences

Legacy body fixtures and future publishers must create explicit field rights;
upgrade alone yields no content. Runtime tests must prove missing, empty,
wrong-Organization, wrong-Membership, and stale-version rights return zero
content, while an authorized full projection remains reachable. The canonical
same-Organization test must reuse one content-free candidate for a broader and
a limited Membership and prove the limited response, Evidence, ContextRun, and
audit contain no private field bytes or names.

The active representation supports text fields only and is not a general
permission DSL or provider-native field ACL protocol. Supply publication,
source capability negotiation, and production field classification remain with
their owning issues.

The revision may be schema-downgraded only while the content schema is empty.
Downgrade first takes an `ACCESS EXCLUSIVE` lock on the Fragment table and then
checks emptiness in that same migration transaction, so a concurrent publisher
cannot commit between the check and DDL. Once any Fragment exists, removing
explicit field rights could broaden access, so rollback refuses before DDL and
requires a forward fix. The historical
ContextRun constraint continues accepting v1 and v2 digest profiles during an
empty-schema rollback so already-retained lineage is not invalidated.

## Revisit trigger

Revisit when a production ContextProvider projects native fields, a richer
typed field value is required, or policy changes independently of Membership
version. Production field-right mutation must move from migrator-owned seeded
data to a dedicated least-privilege Control operation that atomically changes
rights and advances the Organization Policy Epoch; later audit/outbox work must
join that authoritative transaction. Revisit the Organization-wide advisory
lock if measured tenant contention justifies a finer key. Any replacement must
retain missing-as-deny, candidate/request
non-authority, same-operation or same-transaction field reduction, and exact
field binding in AuthorizedProjection, Evidence, and Package integrity.
