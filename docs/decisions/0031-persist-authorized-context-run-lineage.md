---
name: adr-0031-persist-authorized-context-run-lineage
version: "1.0.0"
description: >
  Persist one final authorized-only ContextRun and a separately restricted,
  redacted audit category before returning each successful Acquire Package.
---

# 0031. Persist authorized-only ContextRun lineage before delivery

- Status: accepted
- Date: 2026-07-22
- Refines: ADR-0012, ADR-0015, ADR-0017, ADR-0022, ADR-0026, ADR-0027

## Context

The current HTTP `resolve(Acquire)` path returns a server-authored
`decisionRef`, but an in-memory decision cannot support an operator answering
who requested which purpose, under which policy, or what Package was delivered.
Issue #19 must make every successful empty and authorized Package resolvable to
same-Organization durable lineage without turning observability into a second
content store or an authorization bypass.

Query text is sensitive and often guessable. A plain hash permits offline
dictionary enumeration; retaining the raw query would require encryption,
access, export, deletion, and duration policies that are not active. A full
Package body would duplicate authorized content and silently create another
retention surface. Conversely, a Package digest is useful only if its exact
input document and serialization are frozen rather than described as a vague
"canonical JSON" algorithm.

The public empty outcome intentionally collapses nonexistent,
cross-Organization, and same-Organization denied candidates. Persisted lineage
must preserve that non-enumeration property. A tenant-visible or Learning-safe
record cannot contain denied Fragment bodies, identifiers, names, scores, or
counts. Restricted security lineage may keep a generic category, but it cannot
become a raw candidate trace.

## Decision

### Final ContextRun and restricted DecisionAudit

One successful authenticated `Acquire` appends one final `ContextRun` inside
the retained current-`UserActor` PostgreSQL transaction. `accepted_at` is the
trusted invocation receipt time; `finalized_at` is the Package as-of time. The
active implementation does not expose an independently mutable accepted row:
the two timestamps represent the lifecycle in one immutable terminal insert,
which avoids abandoned intermediate rows. The HTTP response is returned only
after this transaction commits. A persistence or commit failure fails closed as
an unavailable request and returns no `decisionRef` claiming a durable run.

`ContextRun` records the Organization and trusted identity lineage, request and
authentication binding references, purpose, run and decision references,
PolicySnapshot reference and Organization V0 Policy Epoch, effective-scope
digest after the final delivery veto, effective and used PackageBudget
dimensions, terminal outcome,
authorized Evidence references, Package digest profile and value, explicit
Package retention mode, and accepted/finalized/as-of/expiry timestamps. The
active terminal outcomes are exactly `delivered_authorized` and
`delivered_empty`. A valid policy-denied or otherwise no-authorized-Evidence
Acquire is the same delivered-empty public result; it is not a distinct
existence-bearing denial status. For Issue #19, `accepted` means a materialized
Acquire has formed its final `ContextPackage`; a process or transaction failure
that forms no Package does not activate a tenant-visible `failed` ContextRun.

Each `delivered_empty` run also appends one restricted `DecisionAudit` row in
the same transaction. Its complete storage surface is Organization, run,
decision, PolicySnapshot, Policy Epoch, the closed category
`no_authorized_evidence`, and recorded time. It contains no query, query digest,
Candidate/Fragment/Resource reference or body, name, score, denial reason, or
candidate/denied count. An authorized delivery needs no second audit row because
the authorized-only ContextRun is its durable decision lineage.

Unauthenticated transport failures, malformed or injection-style requests, and
failures before a trusted current UserActor is accepted are not ContextRuns.
Ordinary logs may handle those failures under their own future redaction policy;
this decision does not activate such a logging carrier.

### Sensitive query digest

Raw query text is never persisted. `context-query-json-hmac-sha256-v1` stores a
versioned HMAC-SHA256 digest and key version in ContextRun. Its authenticated
bytes are, in order:

1. `context-engine.query-digest.v1` followed by a zero byte;
2. the exact Organization UUID's 16 network-order bytes; and
3. the exact query encoded as a compact `ensure_ascii=true` JSON string.

The query is deliberately not whitespace-, case-, or Unicode-normalized: such
normalization would merge distinct caller inputs and make the lineage claim
ambiguous. The Organization binding prevents cross-Organization correlation.
The keyring is explicit and versioned, requires at least 256-bit keys, has no
ambient or default secret, and is neither serializable nor included in record
representations. Key rotation changes the versioned comparison domain. This
digest supports bounded same-Organization correlation; it is not query
reconstruction or authorization evidence.

### Exact Package digest profile

Every public ContextPackage includes `packageDigest`. The
`context-package-canonical-json-v1` digest is SHA-256 over the exact active
public Package JSON document with `packageDigest` omitted. The document uses the
wire's camelCase field names and omits optional fields that the public serializer
omits. Canonical bytes use RFC 8785 JSON Canonicalization Scheme (JCS):

- accept I-JSON values: JSON null, exact booleans, IEEE 754 numbers,
  Unicode-scalar strings, string-keyed objects, and arrays (the domain tuple
  representation becomes an array);
- reject non-finite numbers, integers that are not exactly representable as
  IEEE 754 binary64 values, non-JSON values, cyclic containers, non-string
  object keys, surrogate code units, and any nested `packageDigest` key;
- sort object properties recursively by the raw property names' UTF-16 code
  units, and serialize numbers using RFC 8785's ECMAScript number rules;
- emit the RFC 8785 canonical JSON representation as UTF-8 bytes.

The implementation delegates canonicalization to the locked RFC 8785 library
instead of maintaining a second number-to-string algorithm. The digest detects
alteration of the documented Package bytes but is not a signature or independent
proof of authenticity. The trusted persisted Organization/run/decision lineage
supplies the binding for the current carrier.

`package_retention_mode` is fixed to `digest_only`. ContextRun may retain the
authorized Evidence references needed for lineage, but neither ContextRun nor
DecisionAudit stores the full Package document or block text. No Package body
retention duration is silently introduced. Before production identity or live
usage activates, an owning issue must establish metadata retention duration,
deletion/export controls, and any proposed full-payload encryption and access
policy; changing from `digest_only` requires a separate accepted decision.

### Read and database authority

Both tables are Organization-owned, use composite same-Organization keys where
they relate, and have enabled and forced RLS. Runtime has INSERT only and must
match the exact transaction-local current UserActor; it cannot SELECT either
table. Worker and Control have no table privilege. The migrator retains schema
administration only.

The minimum read seam authenticates one exact Organization-and-decision request,
then uses the dedicated non-owner Control role to issue an unguessable,
digest-only, 60-second database ticket. The dedicated non-owner
security-operator role may atomically delete that exact ticket before returning
the restricted projection; the application-owned read transaction commits the
deletion before returning the view. The in-process authorization capability is
lifetime-bound and can be used for only one read attempt. Neither application
role can select the
ContextRun, DecisionAudit, or ticket tables directly; a separate NOLOGIN
SECURITY-DEFINER owner alone has FORCE-RLS access to issue, consume, revoke, and
project the bound row. A role, caller-authored session setting, Organization,
decisionRef, or ticket alone yields no row. This is an operator conformance seam,
not a public tenant API or production admin identity system, and grants no write
authority over decision lineage.

The ticket deletion is intentionally not described as durable exactly-once
redemption. PostgreSQL rolls the deletion back if a direct database caller rolls
back the read transaction; such a caller could repeat the same exact projection
until a commit, explicit revocation, or the 60-second expiry. That does not widen
the ticket to another Organization or decision, and the supported reader always
commits before returning, but a future effect-bearing or production operator
protocol must use a durable replay boundary rather than inherit this test seam.

Issue #19 activates only the current Acquire authorized-only ContextRun and
restricted delivered-empty DecisionAudit subcarrier under
`TRACE-REDACTION-012`. It does not claim complete redaction coverage for logs,
metrics, debug output, evaluation, Learning, Continue, OpenCitation, retrieval
traces, feedback, or cross-Organization analytics.

## Rationale

Writing the final run before delivery makes the returned decisionRef a durable
receipt instead of an optimistic trace identifier. Keeping the write in the
already retained current-UserActor transaction preserves the identity and RLS
facts used by authorization and avoids a second, weaker persistence authority.

Separating tenant-safe authorized lineage from the seven-field restricted audit
surface makes the prohibited information structurally difficult to retain.
Organization-bound keyed query digests resist simple offline guessing without
claiming reversibility, while digest-only Package retention detects alteration
without creating another long-lived content copy.

## Consequences

- Every successful empty or authorized current Acquire has one same-Organization
  ContextRun whose decisionRef and Package digest match the public response.
- Empty and policy-denied valid invocations remain publicly indistinguishable
  and retain only the generic restricted audit category.
- A caller cannot manufacture or replay operator authorization, and Org A cannot
  read or affect Org B's run lineage through the non-owner roles.
- Package consumers can recompute the versioned digest and detect a changed
  public Package document.
- Production authentication remains reject-all; this durable conformance slice
  does not by itself approve retention of real user traffic.

## Revisit trigger

Revisit when Continue or OpenCitation persists lineage; when feedback,
candidate/ranking traces, evaluation, Learning, or tenant-visible run reads
activate; when a production operator identity and authorization workflow exists;
when metadata retention duration and deletion/export requirements are fixed; or
when any full Package/query payload retention is proposed. Every revision must
preserve authorization-only ContextRun data, generic restricted denial lineage,
same-Organization RLS, explicit read authorization, deterministic versioned
digests, and commit-before-response behavior.
