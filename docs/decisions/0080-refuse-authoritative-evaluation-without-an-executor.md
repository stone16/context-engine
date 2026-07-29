---
name: adr-0080-refuse-authoritative-evaluation-without-an-executor
version: "1.0.0"
description: >
  Keep M1 golden evaluation fail-closed: serialized observations cannot attest
  security, no production clean-result constructor exists, and reports remain
  refused until a real executor owns that construction boundary.
---

# 0080. Refuse authoritative evaluation without an executor

- Status: accepted
- Date: 2026-07-30
- Refines: ADR-0034

## Context

Golden-set v1 adds deterministic layered judges and an absolute evaluation
security veto. M1 has no evaluation executor authorized to run cases through a
tracked Runtime seam and observe authorization outcomes. A caller-authored run
file cannot establish that a run was clean: validating a serialized empty event
list or zero counters proves only their shape, not their origin. Likewise, a
public helper that accepts a caller callback can be invoked with a no-op and
manufacture the same false clean attestation.

Adding an HMAC or keyring would protect against a malicious caller that M1's
single trusted local-operator threat model does not contain, while introducing
provisioning and rotation failure modes. A cryptographic evaluation boundary
would require its own threat model and decision.

## Decision

M1 exposes no production constructor for a clean or violating evaluation
security observation. Serialized input can produce only the typed
`NOT_OBSERVED` or `MALFORMED` precondition states; both render the overall
report `REFUSED` with the closed reason
`no_run_executor_security_observation`. Retrieval, citation, and answer judges
still compute and report their independent metrics, but no combination of
scores, thresholds, or slices can compensate for the absent security
observation.

The `OBSERVED_CLEAN` and violation result types remain as the report/veto
contract for the future executor. Synthetic construction exists only under the
test tree so unit tests can prove that one violation yields whole-report
`FAIL`, observed zero alone could satisfy the veto, and production never imports
the synthetic factory. The file-only CLI therefore cannot emit an authoritative
non-refused report in M1.

The threshold, security-result, verified-identity, and privacy-authority types
reject subclassing. Their normal constructors require private inputs supplied
by their owning modules, while supported application composition fixes the
tracked threshold path and local maintainer verifier. These seals prevent
accident and misuse through supported paths: a caller cannot select thresholds,
skip the calibration event for active values, make non-tracked thresholds
return `PASS`, mint a clean observation, inject an authenticator, or construct a
verified identity through a supported entry point.

These seals are not unforgeable or tamper-proof against an in-process adversary
who deliberately bypasses Python constructors, imports private module objects,
mutates instances, or monkeypatches code. M1 has a single trusted local operator
and its threat model does not include that adversary. Adding tokens, registries,
call-stack inspection, signing, or another cryptographic boundary would not be
justified under the current threat model.

## Consequences

- M1 ships schema, locking, judges, veto semantics, and report machinery, but
  deliberately cannot attest that an evaluation run was security-clean.
- Caller-supplied counters, event lists, callbacks, and nominal result objects
  cannot become security authority.
- The report distinguishes an unestablished precondition (`REFUSED`) from a
  genuine security violation (`FAIL`) and a genuinely observed clean run
  (`PASS` eligibility).
- The public-subset authority remains a preparatory privacy check only; M1 adds
  no promotion effect or publication authority.

## Revisit trigger

Issue #160 must define and implement the concrete evaluation executor, the
tracked Runtime seam it executes, and sole private-constructor ownership. If
evaluation later admits untrusted callers, multiple tenants, or a remote
runner, the supported-path trust boundary must be revisited in a new ADR with
that threat model, including whether cryptographic provenance is then required.
