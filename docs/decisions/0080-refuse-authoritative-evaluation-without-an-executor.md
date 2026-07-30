---
name: adr-0080-refuse-authoritative-evaluation-without-an-executor
version: "1.1.0"
description: >
  Keep golden evaluation fail-closed: only a run the executor performed itself
  through the tracked Runtime seam can attest security, serialized observations
  never can, and the executor privately owns that construction boundary.
---

# 0080. Refuse authoritative evaluation without an executor

- Status: accepted
- Date: 2026-07-30
- Refines: ADR-0034

## Context

Golden-set v1 adds deterministic layered judges and an absolute evaluation
security veto. A caller-authored run file cannot establish that a run was
clean: validating a serialized empty event list or zero counters proves only
their shape, not their origin. Likewise, a public helper that accepts a caller
callback, transport, or client can be invoked with a no-op and manufacture the
same false clean attestation, so narrowing that helper's arguments is not a
fix. Both failure modes were reproduced during review before this decision.

Adding an HMAC or keyring would protect against a malicious caller that the
single trusted local-operator threat model does not contain, while introducing
provisioning and rotation failure modes. A cryptographic evaluation boundary
would require its own threat model and decision.

## Decision

One evaluation run executor privately owns the construction of every clean and
violating security observation. Its single public entry point takes the golden
set, the blind-judge document, the tracked thresholds, and a report instant. It
takes no callback, transport, client, counter, or security result, and it
composes the run seam itself from the process environment, so no importer can
reach a clean observation by supplying a no-op. `OBSERVED_CLEAN` exists only as
a byproduct of responses the executor fetched itself.

The tracked run seam is the minimal frozen-resolve caller
`DogfoodResolveClient.acquire` over the active loopback dogfood composition
(ADR-0068), recorded as `dogfood-loopback-resolve-acquire-v1` and rendered in
every report. The executor replays each golden case query through that seam and
observes what the delivered ContextPackage establishes:

- `unauthorized_evidence` — one delivered Evidence that does not carry its
  complete decision binding: an absent or blank lineage, projected field set,
  or `sourceAclEvidence` kind, or a `decisionRef`, `policyEpoch`,
  `policySnapshotRef`, `purpose`, `authorizationAsOf`, or `runRef` that does
  not match its enclosing Package. Such content reached the caller without a
  verifiable authorization decision.
- `missing_context_fallback` — content that is not grounded in delivered
  authorized Evidence: a block citing an Evidence the Package did not deliver,
  or a Package whose coverage and delivery disagree.
- `wrong_organization_effect` — a resolve delivered under a different audience
  binding than the rest of the run, or under none at all.

Every counter in the report is derived from those observed events. Refusal is
observed too: an evidence-free Package is the Runtime refusing, never a caller
claim, and that fail-closed refusal is a quality signal, never a security event
— a revoked Resource must collapse recall, not manufacture a violation. The
blind-judge document supplies only what a judge produces — blind
score, critical contradiction, and produced claims — and is refused outright if
it carries an observed-evidence, refusal, or security field.

What the seam cannot observe is stated rather than approximated. The public
wire is deliberately non-enumerating, so the executor sees no Organization
identifier; audience binding consistency across one run is the observable form
of a wrong-Organization effect, not a proof about the Kernel's internal
decision. A structurally unusable response, an unreachable seam, or a coverage
state this seam does not produce refuses the whole run instead of scoring it.

Serialized input keeps producing only the typed `NOT_OBSERVED` or `MALFORMED`
precondition states; both render the overall report `REFUSED` with the closed
reason `no_run_executor_security_observation`, and the file-only report command
therefore still cannot emit an authoritative non-refused report. Retrieval,
citation, and answer judges compute their independent metrics either way, but
no combination of scores, thresholds, or slices can compensate for an absent
security observation, and one observed violation forces whole-report `FAIL`
before any threshold is consulted. Synthetic construction of results remains
test-only so unit tests can exercise the veto without a run; production never
imports that factory.

The threshold, security-result, verified-identity, and privacy-authority types
reject subclassing. Their normal constructors require private inputs supplied
by their owning modules, while supported application composition fixes the
tracked threshold path, the local maintainer verifier, and the run seam. These
seals prevent accident and misuse through supported paths: a caller cannot
select thresholds, skip the calibration event for active values, make
non-tracked thresholds return `PASS`, mint a clean observation, inject an
authenticator or a seam, or construct a verified identity through a supported
entry point.

These seals are not unforgeable or tamper-proof against an in-process adversary
who deliberately bypasses Python constructors, imports private module objects,
mutates instances, monkeypatches code, or stands up a counterfeit Runtime on
the configured loopback address. Evaluation has a single trusted local operator
and its threat model does not include that adversary. Adding tokens,
registries, call-stack inspection, signing, or another cryptographic boundary
would not be justified under the current threat model.

The executor gains no publication authority. It produces reports only; a
ReleaseManifest still activates or rolls back solely through the
release-operator-authorized promote path (ADR-0033, ADR-0073).

## Consequences

- An executed run can attest that it was security-clean; a caller-authored run
  file still cannot, and remains `REFUSED`.
- Caller-supplied counters, event lists, callbacks, transports, and nominal
  result objects cannot become security authority.
- The report distinguishes an unestablished precondition (`REFUSED`) from a
  genuine security violation (`FAIL`) and a genuinely observed clean run
  (`PASS` eligibility), and records which seam, if any, produced it.
- The blind-judge layer stays a judge input because the tracked seam delivers a
  ContextPackage rather than a generated answer; the run observes everything
  else.
- The public-subset authority remains a preparatory privacy check only; this
  decision adds no promotion effect or publication authority.

## Revisit trigger

Revisit when a generation carrier makes the answer layer observable at a
tracked seam, when a richer retrieval path replaces the loopback dogfood
composition as the seam under evaluation, or when the seam gains a coverage
state the executor deliberately refuses today. If evaluation later admits
untrusted callers, multiple tenants, or a remote runner, the supported-path
trust boundary must be revisited in a new ADR with that threat model,
including whether cryptographic provenance is then required.
