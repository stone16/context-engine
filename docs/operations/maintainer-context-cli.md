# Maintainer Context CLI

`context-engine-context` is the read-only daily-driver caller for the active
loopback, single-Membership File `Acquire` carrier. It sends each question to
the frozen `POST /v0/resolve` public seam, or validates a deliberately captured
public `ContextPackage`. It never imports Runtime internals or reaches Control,
ActionPlane, ContextLearning promotion, a model, a sender, or a database.

It is a local read-only consumer, so the post-delivery obligations in
[ADR-0088](../decisions/0088-bind-local-consumers-to-fresh-evidence-bearing-packages.md)
bind every invocation.

## Configuration

Run the explicitly configured dogfood API on loopback first. The CLI reads its
destination and bearer only from these environment contracts:

```text
CONTEXT_ENGINE_DOGFOOD_BASE_URL
CONTEXT_ENGINE_DOGFOOD_SECRET
```

The base URL must be explicit loopback HTTP with a port. The bearer has no
command-line option: it must not enter argv, shell history, output, errors,
reports, or captured Package files. The caller ignores proxy configuration,
rejects redirects, and bounds the query, response, and captured input.

## Query

Every invocation generates a fresh non-secret request ID and sends one fresh
`Acquire`; neither Package nor request ID can be supplied or reused.
The only caller-supplied body fields are the untrusted query, an optional
`PackageBudget` ceiling, and optional source/resource narrowing:

```bash
uv run context-engine-context query \
  "Which accepted decision governs local consumers?"

uv run context-engine-context query \
  "Which accepted decision governs local consumers?" \
  --format json \
  --max-tokens 2048 \
  --source-ref source:file:maintainer
```

The budget flags cover the same four `PackageBudget` dimensions the wire
accepts — tokens, provider calls, cost, and elapsed time — and `--help` is the
authoritative listing of the closed flag set.

`--format human` shows Package identity, digest, purpose, `asOf`, `expiresAt`,
coverage, budget usage, every authorized Block, and its exact Evidence and
citation lineage. `citationOpenRef` is display-only and every human citation
states `citationOpen: NOT_ACTIVE` for this carrier.

`--format json` emits the exact validated, closed `ResolutionOutcome` received
from the server, without wrapper metadata, renamed fields, or interpretations.
It is suitable for deliberate shell capture:

```bash
uv run context-engine-context query \
  "Which accepted decision governs local consumers?" \
  --format json > .context-engine/context-package.json
```

The single deliberate exception to that exactness is the one-hop
`egressGrant`. This caller never performs model or channel egress, so it never
emits or persists a redeemable grant: the `egressGrant` object, its `kind`, and
every other field stay exactly as the server sent them, while the secret
`value` alone is replaced by the fixed sentinel `REDACTED-EGRESS-GRANT`. That
substitution is the only value this CLI ever changes; a capture carrying the
sentinel still inspects normally. Redeeming a grant requires a fresh resolve by
an authorized egress caller.

The ignored `.context-engine/` location above is optional and
non-authoritative. A capture is already-delivered content: protect it according
to the Package retention policy and delete it when it is no longer needed. The
CLI keeps no persisted state itself.

## Inspect an untrusted capture

`inspect` accepts a Package or a strict-JSON query capture from a file or `-`
for standard input. The bytes are untrusted. The entire closed public envelope
and Package schema, package digest, exact Block/Evidence
closure, Evidence decision lineage, budget accounting, lifetime, coverage, and
expiry are validated before any content is rendered. Package instants must be
timezone-aware and normalizable to UTC; a naive or unrepresentable instant is
malformed, never silently reinterpreted in the local zone. A captured public
refusal is reported as that same explicit refusal, not as malformed input:

```bash
uv run context-engine-context inspect \
  .context-engine/context-package.json

uv run context-engine-context inspect - --format json \
  < .context-engine/context-package.json
```

Here `--format json` emits the exact validated Package document rather than the
whole envelope: a captured `query` outcome reduces to its own `package` member,
so re-inspecting that output is stable and never re-emits the already-redacted
grant. A captured public refusal is emitted exactly as captured.

Inspection proves only that the captured public document is internally valid
and current at inspection time. It does not authenticate the producer,
authorize a candidate, reconstruct an `AuthorizedProjection`, mint or redeem a
token/grant, reopen a citation, refresh a Package, or make the capture current
context for another question. Each new question requires a fresh `query`.

## Outcomes and exit classes

Human refusals are content-free typed lines on standard error; they never
partially render an invalid or expired Package. Strict JSON preserves a valid
content-free public refusal/package shape exactly where one exists.

| Exit | Class | Meaning |
|---:|---|---|
| 0 | success | Current, valid, sufficient Package rendered |
| 10 | explicit refusal | Request unavailable (resolved or captured), empty authorized set, or typed coverage gap |
| 11 | service unavailable | Loopback resolve transport or served capability unavailable |
| 12 | malformed Package | Closed schema, digest, lineage, lifetime, instant, or capture validation failed |
| 13 | expired Package | `expiresAt` is at or before inspection/render time |
| 14 | invalid local configuration | URL, bearer, request, budget, narrowing, or secret-exclusion input is invalid |

An empty authorized set means only that authorized context is unavailable for
this question. It is not evidence that the corpus has no answer. Stale
evidence, source unavailability, budget exhaustion, and unsupported capability
remain distinct public coverage reasons.

The served composition answers every rejected request with a closed
content-free `{"code": ...}` document. The CLI classifies those by transport
status class alone and never reads, echoes, or renders the response: a status
that indicts this caller's own input — the environment bearer, or the request
document it composed from the arguments above — is local configuration, while
every other rejection is the served capability being unavailable.

| Served status | Closed code | Exit |
|---:|---|---:|
| 401 | `authentication_failed` | 14 |
| 400, 422 | `invalid_request` | 14 |
| 403 | `application_forbidden` | 11 |
| 429 | `rate_limited` | 11 |
| 5xx | `service_unavailable` | 11 |
| any other status, redirect, or transport failure | not read | 11 |

A public `request_not_available` outcome is a successful `200` answer, not a
rejected request; it keeps exit 10 above. Because `query` sends only `Acquire`,
`resolved` and `request_not_available` are the only served outcome kinds it
accepts; any other kind is an unavailable served capability, not a refusal. A
captured `citation_not_available` document still inspects as that same explicit
refusal.

## Explicit v1 non-goals

- `OpenCitation` is `NOT_ACTIVE` for the dogfood CLI carrier. A future command
  requires carrier activation and fresh HTTP reauthentication/reauthorization;
  `CitationOpenRef` alone never grants access.
- `Continue`, remote ingress, multiple tenants/users, group/public delivery,
  non-File sources, and external query embeddings remain `NOT_ACTIVE` under
  the bounded dogfood composition.
- Evaluation replay stays in `context-engine-dogfood-eval` and the governed
  evaluation tools. The real private corpus remains the maintainer's work under
  issue #103; this CLI fabricates no cases and makes no Quality claim.
- Control and release operations stay in `context-engine-control` with separate
  local operator and release credentials. ActionPlane, model generation,
  Sender effects, and promotion are outside this read caller permanently.

The executable name is the accepted v1 contract. Because it is public-facing,
maintainers may still rename it during the implementation PR review before the
contract is released.
