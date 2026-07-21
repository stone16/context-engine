#!/usr/bin/env python3
"""Validate and report the versioned ContextEngine security catalog.

The ``.yaml`` catalog deliberately uses JSON-compatible YAML so this D0 check
has no dependency on an application environment or a third-party YAML parser.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG_PATH = REPOSITORY_ROOT / "eval/catalogs/security-invariants.yaml"
DEFAULT_SCHEMA_PATH = REPOSITORY_ROOT / "eval/catalogs/security-catalog.schema.json"
SUPPORTED_CATALOG_VERSION = "1.1.0"
EXPECTED_INVARIANT_COUNT = 15
EXPECTED_FIXTURE_COUNT = 12
ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-[0-9]{3}$")
EVIDENCE_REF_PATTERN = re.compile(r"^ev_[0-9a-f]{64}$")

CANONICAL_INVARIANT_IDS: tuple[str, ...] = (
    "TENANT-OWNERSHIP-001",
    "TENANT-FK-002",
    "RLS-FAIL-CLOSED-003",
    "SCOPE-INTERSECTION-004",
    "INDEX-NOT-AUTHORITY-005",
    "REVOCATION-006",
    "WORKER-LEASE-007",
    "TRANSPORT-UNTRUSTED-008",
    "NON-ENUMERATION-009",
    "CITATION-AUTH-010",
    "EGRESS-011",
    "TRACE-REDACTION-012",
    "ACTION-SEPARATION-014",
    "CROSS-ORG-LEARN-015",
    "RELEASE-OWNER-019",
)
CANONICAL_FIXTURE_IDS: tuple[str, ...] = tuple(
    f"ACCEPT-{number:03d}" for number in range(1, EXPECTED_FIXTURE_COUNT + 1)
)

HARD_ORACLES: tuple[str, ...] = (
    "Unauthorized Evidence",
    "wrong-Organization effect",
    "missing-context fallback",
)

TOP_LEVEL_FIELDS = (
    "catalogVersion",
    "authority",
    "hardOracles",
    "activations",
    "invariants",
    "fixtures",
)
ACTIVATION_FIELDS = (
    "issueRef",
    "invariantRef",
    "carrier",
    "status",
    "policyEpochScope",
    "controlBoundary",
    "testEvidence",
    "deferredEvidence",
    "futureCarriers",
    "notActive",
)
ACTIVATION_TEST_EVIDENCE_FIELDS = ("id", "surface", "oracle")
INVARIANT_FIELDS = (
    "id",
    "title",
    "purpose",
    "threatRefs",
    "protectedAssets",
    "deterministicOracle",
    "hardOracleRefs",
    "applicability",
    "capabilityRef",
    "requiredMilestones",
    "evidenceStatus",
    "expectedEvidence",
    "authorityRefs",
)
EXPECTED_EVIDENCE_FIELDS = ("property", "postgres", "runtimeOrDelivery")
FIXTURE_FIELDS = (
    "id",
    "title",
    "decisionStatus",
    "carrier",
    "setup",
    "adversarialMutation",
    "operation",
    "expected",
    "invariantRefs",
    "authorityRefs",
)
CARRIER_FIELDS = ("statusAtM0", "m0Expectation", "upgradeTrigger")
SETUP_FIELDS = ("preconditions", "trustedIdentity")
EXPECTED_FIELDS = (
    "externalResponse",
    "packageOrError",
    "evidence",
    "businessEffects",
    "io",
)
EVIDENCE_FIELDS = (
    "unauthorizedEvidenceCount",
    "unauthorizedContentBytes",
    "missingContextFallbackCount",
    "outboundBytes",
)
BUSINESS_EFFECT_FIELDS = (
    "wrongOrganizationEffectCount",
    "mutationEffectCount",
    "totalEffectsAfterScenario",
)
IO_FIELDS = ("providerCalls", "indexCalls", "modelCalls", "actionCalls")
PARAMETERIZED_CASE_FIELDS = (
    "id",
    "mutation",
    "expectedStatus",
    "expectedOutcome",
    "expectedNewDurableEffects",
    "expectedWrongOrganizationEffects",
    "expectedContentWorkCalls",
)

CANONICAL_REQUIRED_MILESTONES: dict[str, tuple[str, ...]] = {
    "TENANT-OWNERSHIP-001": ("M0", "M1"),
    "TENANT-FK-002": ("M0", "M1"),
    "RLS-FAIL-CLOSED-003": ("M0",),
    "SCOPE-INTERSECTION-004": ("M0", "M1", "M5"),
    "INDEX-NOT-AUTHORITY-005": ("M0", "M1", "M3"),
    "REVOCATION-006": ("M1", "M2"),
    "WORKER-LEASE-007": ("M1", "M3"),
    "TRANSPORT-UNTRUSTED-008": ("M1", "M2"),
    "NON-ENUMERATION-009": ("M1", "M5"),
    "CITATION-AUTH-010": ("M2", "M3"),
    "EGRESS-011": ("M2", "M5"),
    "TRACE-REDACTION-012": ("M0", "M1"),
    "ACTION-SEPARATION-014": ("M2",),
    "CROSS-ORG-LEARN-015": ("M0", "M3"),
    "RELEASE-OWNER-019": ("M0", "M3"),
}

RUNTIME_OUTCOME_KINDS: dict[str, tuple[str, str]] = {
    "ACCEPT-001": ("resolved", "ContextPackage"),
    "ACCEPT-003": ("resolved", "ContextPackage"),
    "ACCEPT-004": ("resolved", "ContextPackage"),
    "ACCEPT-005": ("request_not_available", "request_not_available"),
    "ACCEPT-006": ("resolved", "ContextPackage"),
    "ACCEPT-009": ("request_not_available", "request_not_available"),
    "ACCEPT-010": ("citation_not_available", "citation_not_available"),
    "ACCEPT-011": ("resolved", "ContextPackage"),
}

CANONICAL_FAIL_CLOSED_OUTCOMES: dict[str, dict[str, object]] = {
    "ACCEPT-007": {
        "externalResponse": {
            "status": 422,
            "code": "invalid_request",
            "fieldNamesEchoed": False,
        },
        "packageOrError": {
            "kind": "error",
            "contextPackageCreated": False,
            "trustedContextConstructedFromBody": False,
        },
    },
    "ACCEPT-008": {
        "externalResponse": {
            "status": 404,
            "code": "work_not_available",
            "leaseClaimsEchoed": False,
        },
        "packageOrError": {
            "kind": "worker_rejection",
            "reasonVisibleToWorker": "generic_unavailable",
            "newReceiptCreated": False,
        },
    },
    "ACCEPT-012": {
        "externalResponse": {
            "status": 404,
            "code": "action_not_available",
            "body": {"kind": "generic_unavailable"},
        },
        "packageOrError": {
            "kind": "action_rejection",
            "actionTicketCreated": False,
            "contextTicketConsumed": False,
            "capabilityReportedAsPass": False,
        },
    },
}

NON_RETRYABLE_RUNTIME_FIXTURES = frozenset({"ACCEPT-005", "ACCEPT-009"})
RESOLVED_EMPTY_RUNTIME_FIXTURES = frozenset({"ACCEPT-011"})
RESOLVED_CONTENT_RUNTIME_FIXTURES = frozenset(
    {"ACCEPT-001", "ACCEPT-003", "ACCEPT-004", "ACCEPT-006"}
)
CANDIDATE_RANK_MEMBERS: tuple[str, ...] = (
    "candidate-authorized-a",
    "candidate-denied-a",
    "candidate-hostile-b",
)
CANDIDATE_RANK_ORDERS: tuple[tuple[str, ...], ...] = (
    ("candidate-authorized-a", "candidate-denied-a", "candidate-hostile-b"),
    ("candidate-authorized-a", "candidate-hostile-b", "candidate-denied-a"),
    ("candidate-denied-a", "candidate-authorized-a", "candidate-hostile-b"),
    ("candidate-denied-a", "candidate-hostile-b", "candidate-authorized-a"),
    ("candidate-hostile-b", "candidate-authorized-a", "candidate-denied-a"),
    ("candidate-hostile-b", "candidate-denied-a", "candidate-authorized-a"),
)
PUBLIC_EVIDENCE_FIELDS: tuple[str, ...] = (
    "evidenceRef",
    "sourceRef",
    "resourceRef",
    "revisionRef",
    "fragmentRef",
    "runRef",
    "purpose",
    "authorizationAsOf",
    "decisionRef",
    "policySnapshotRef",
    "policyEpoch",
    "sourceDecisionRef",
)

TRANSPORT_CASE_IDS: tuple[str, ...] = (
    "BODY-INJECTION",
    "DELIV-001",
    "DELIV-002",
    "DELIV-003",
    "DELIV-004",
)
WORKER_LEASE_CASE_IDS: tuple[str, ...] = (
    "LEASE-ORGANIZATION",
    "LEASE-JOB",
    "LEASE-OPERATION",
    "LEASE-SOURCE",
    "LEASE-RESOURCE",
    "LEASE-REVISION",
    "LEASE-SERVICE-ACTOR",
    "LEASE-WORKLOAD",
    "LEASE-POLICY-EPOCH",
    "LEASE-AUDIENCE",
    "LEASE-IDEMPOTENCY",
    "LEASE-GENERATION",
    "LEASE-ISSUED-AT",
    "LEASE-EXPIRY",
    "LEASE-NONCE",
    "LEASE-REPLAY",
    "LEASE-USER-IMPERSONATION",
)
ACL_PROOF_CASE_IDS: tuple[str, ...] = (
    "PROV-013",
    "PROV-014",
    "PROV-015",
    "PROV-018",
    "PROV-019",
)
AUDIENCE_ACTION_CASE_IDS: tuple[str, ...] = (
    "AUTH-010",
    "RUN-014",
    "EGR-003",
    "EGR-005",
    "EGR-006",
    "ACTION-001",
    "ACTION-002",
    "ACTION-003",
    "ACTION-004",
    "ACTION-005",
    "ACTION-006",
    "ACTION-007",
    "ACTION-008",
    "ACTION-009",
)

TRANSPORT_CASE_OUTCOMES: dict[str, tuple[int, str]] = {
    "BODY-INJECTION": (422, "invalid_request"),
    "DELIV-001": (200, "request_not_available"),
    "DELIV-002": (200, "request_not_available"),
    "DELIV-003": (200, "request_not_available"),
    "DELIV-004": (200, "request_not_available"),
}
WORKER_LEASE_CASE_OUTCOME = (404, "work_not_available")
ACL_PROOF_CASE_OUTCOME = (200, "request_not_available")
AUDIENCE_ACTION_CASE_OUTCOME = (404, "action_not_available")

CANONICAL_ACTIVATED_ORACLE_DIGESTS: dict[str, str] = {
    "PROV-013": "597d1b8511d430398f0de6982df350a6158ef57135b33eb1636b19d57222e7f6",
    "PROV-014": "105e5e581bd363375e86c098f3bcded295024d91f380e7d3221545c5b5877bdd",
    "PROV-015": "b34bfee66ab61a1a187182d8137412151c0f52b1c60ba030febecf0af9b2aa24",
    "PROV-018": "f39c044b3475f536c990eae3c78c422b08080c134ec03c9eeffbd1aa31de01f4",
    "PROV-019": "933086bc50969a6c04dfed9b46d3509e25e815bf58d5c025dfde298578d05698",
    "AUTH-010": "ee7342e4fa1b5a5f43337b43e3dfd0a0b1a6065cdefcaf610eabc48397767dfb",
    "RUN-014": "5edc224d7cf4d7b44773f8c0b5a8d6065a83113fb0361dd799239128c22e4393",
    "EGR-003": "a7e1b84b711534fe9a8bf2f399d117f508f357aea498f07e562e6bdd027e4822",
    "EGR-005": "dfce84151968132f0df53f9261981c8e1bb9d3bbffe9920d95cc106649ceda25",
    "EGR-006": "50196c9621df31c1c7e4b86613da509d64d5a81c3aae55d3bd51e88582d187f6",
    "ACTION-001": "bfc12ae00de249b37531f6521a49979e2d43dab1e3e229426e1f3a5e7cfb3ee9",
    "ACTION-002": "68d07a06d265d0f3e833db78521eac5b1e2e0f90229f800e6291e2210710338e",
    "ACTION-003": "9f18bb396da1b6ff513366704c8b0d4ed913430cb54a153e9d6a56a92bf3061e",
    "ACTION-004": "f9140ac2129845d1bb582f9fa84eed77ace2ce1d840ae0abddfa209d4d4a7fae",
    "ACTION-005": "a285bc30ba4b55755c17e565d6a40d0fdb900f952b5b91fe9efbdad36ffbfb09",
    "ACTION-006": "f1701a889c9b0613e75eda58cf4fb129ab5135d39cc352b079c6932f943df4d9",
    "ACTION-007": "3e1acc8a53d43f00f5214ab5c339b685dc039cb5a2a4d38f128c9ff94888cc57",
    "ACTION-008": "a62b4872153da75ad63b2f9678efa4c38250e2464c82f3ee15d55f33545f007a",
    "ACTION-009": "9ea27dea249b98198811ff1bfffa1e5307d59f84bea8e31050c5e2ea0b3528ce",
}

REQUIRED_RUNTIME_EVIDENCE: dict[str, tuple[str, ...]] = {
    "SCOPE-INTERSECTION-004": ("AUTH-010", "RUN-014"),
    "INDEX-NOT-AUTHORITY-005": (
        "PROV-010",
        "PROV-013",
        "PROV-014",
        "PROV-015",
        "PROV-018",
        "PROV-019",
        "PROV-020",
    ),
    "REVOCATION-006": (
        "RUN-006",
        "PROV-013",
        "PROV-014",
        "PROV-015",
        "PROV-018",
        "PROV-019",
        "PROV-020",
    ),
    "TRANSPORT-UNTRUSTED-008": (
        "DELIV-001",
        "DELIV-002",
        "DELIV-003",
        "DELIV-004",
    ),
    "EGRESS-011": ("EGR-003", "EGR-005", "EGR-006", "RUN-014"),
    "ACTION-SEPARATION-014": tuple(f"ACTION-{number:03d}" for number in range(1, 10)),
}

REQUIRED_POSTGRES_EVIDENCE: dict[str, tuple[str, ...]] = {
    "REVOCATION-006": ("PG-REVOCATION-006", "CACHE-002", "BLOB-002"),
}

ACCEPT_005_FUTURE_CARRIER: dict[str, str] = {
    "statusAtM0": "future",
    "m0Expectation": "fail_closed",
    "upgradeTrigger": (
        "Issue #16 activates the M0 unavailable-capability refusal without "
        "redeeming a continuation; issue #15 separately proves next-request "
        "Acquire revocation, and the future real-continuation owner upgrades "
        "this fixture only when continuation issuance and redemption exist."
    ),
}

ACCEPT_009_FUTURE_CARRIER: dict[str, str] = {
    "statusAtM0": "future",
    "m0Expectation": "fail_closed",
    "upgradeTrigger": (
        "The owning File authorized-Package issue #23 first upgrades this "
        "fixture for Mirrored FileSourceAccess; a later federated-provider "
        "issue upgrades it for real Live source-native ACL evidence."
    ),
}

ACCEPT_010_FUTURE_CARRIER: dict[str, str] = {
    "statusAtM0": "future",
    "m0Expectation": "fail_closed",
    "upgradeTrigger": (
        "The owning M2 OpenCitation implementation issue upgrades this "
        "fixture only after current-opener authorization and distinct token "
        "variants exist at the public HTTP seam."
    ),
}

ACCEPT_008_FUTURE_CARRIER: dict[str, str] = {
    "statusAtM0": "future",
    "m0Expectation": "fail_closed",
    "upgradeTrigger": (
        "Issue #17 independently activates only the signed one-shot persistent "
        "no-op durable-job carrier. The full ACCEPT-008 fixture upgrades only "
        "after Source, Resource, Revision, Policy Epoch, end-user delivery "
        "audience, idempotency, generation, business mutation, outbox, and File "
        "carriers run their full per-binding matrix."
    ),
}

ACCEPT_012_UNAVAILABLE_CARRIER: dict[str, str] = {
    "statusAtM0": "unavailable",
    "m0Expectation": "fail_closed",
    "upgradeTrigger": (
        "Issue #18 independently activates only distinct signed synthetic "
        "ContextAccessTicket Provider-read and ActionTicket no-op channel-action "
        "carriers. The full ACCEPT-012 fixture upgrades only when the owning M2 "
        "ActionPlane issue proves prepare and perform against a real Sender with "
        "all durable effect bindings and reconciliation."
    ),
}

CANONICAL_DEFERRED_FIXTURE_CARRIERS: dict[str, dict[str, str]] = {
    "ACCEPT-005": ACCEPT_005_FUTURE_CARRIER,
    "ACCEPT-008": ACCEPT_008_FUTURE_CARRIER,
    "ACCEPT-009": ACCEPT_009_FUTURE_CARRIER,
    "ACCEPT-010": ACCEPT_010_FUTURE_CARRIER,
    "ACCEPT-012": ACCEPT_012_UNAVAILABLE_CARRIER,
}

CANONICAL_REVOCATION_ACTIVATION: dict[str, object] = {
    "issueRef": "#15",
    "invariantRef": "REVOCATION-006",
    "carrier": "ContextRuntime.resolve(Acquire)",
    "status": "active_fail_closed",
    "policyEpochScope": "organization-v0",
    "controlBoundary": (
        "PostgreSQLAccessPolicyControl.change_access(ResourceAccessRevocation)"
    ),
    "testEvidence": [
        {
            "id": "PG-REVOCATION-006",
            "surface": "tests/integration/test_access_policy_revocation.py",
            "oracle": (
                "The internal non-owner Control transaction atomically revokes "
                "exact same-Organization access and advances one monotonic "
                "Organization epoch; rollback and overflow expose neither half, "
                "concurrent successful changes lose no bump, and Org B remains "
                "unchanged."
            ),
        },
        {
            "id": "RUN-006",
            "surface": (
                "tests/integration/test_runtime_policy_epoch_integration.py"
            ),
            "oracle": (
                "The same authenticated HTTP Acquire with the same query and "
                "unchanged CandidateIndex/Fragment returns authorized Evidence "
                "before revoke and zero Evidence on the first request after "
                "commit; Org B remains authorized and the persistent Fragment "
                "remains present."
            ),
        },
        {
            "id": "CACHE-002",
            "surface": "tests/unit/test_runtime_authorized_evidence.py",
            "oracle": (
                "An injected pre-revocation authorization decision or a "
                "mid-resolve epoch change fails the final current-epoch gate and "
                "delivers zero stale Evidence without relying on candidate or "
                "content removal."
            ),
        },
    ],
    "deferredEvidence": ["BLOB-002"],
    "futureCarriers": [
        "Continue",
        "OpenCitation",
        "Policy-Epoch-bound WorkerLease",
        "production ContextAccessTicket",
        "production ActionTicket",
    ],
    "notActive": [
        "DecisionAudit",
        "outbox",
        "cleanup",
        "Source/Resource Policy Epochs",
        "UI/external admin",
    ],
}

CANONICAL_UNAVAILABLE_CAPABILITY_ACTIVATION: dict[str, object] = {
    "issueRef": "#16",
    "invariantRef": "INDEX-NOT-AUTHORITY-005",
    "carrier": (
        "ContextRuntime.resolve(Continue | OpenCitation | server-owned "
        "unavailable Acquire plan)"
    ),
    "status": "active_fail_closed",
    "policyEpochScope": "organization-v0",
    "controlBoundary": (
        "RuntimeCapabilityGate.require_available(RuntimeCapability)"
    ),
    "testEvidence": [
        {
            "id": "RUN-UNAVAILABLE-016",
            "surface": "tests/unit/test_runtime_unavailable_capabilities.py",
            "oracle": (
                "Table-driven Runtime cases prove unavailable Continue, "
                "OpenCitation, and server-owned Acquire plans traverse the "
                "content-free sealed Kernel preflight and stop before Provider, "
                "index, or source I/O; the restricted mandatory audit retains "
                "only UNSUPPORTED_CAPABILITY."
            ),
        },
        {
            "id": "HTTP-UNAVAILABLE-016",
            "surface": "tests/unit/test_http_unavailable_capabilities.py",
            "oracle": (
                "The closed HTTP and OpenAPI request union admits its declared "
                "variants, maps known unavailable capabilities to generic 200 "
                "domain outcomes before configured scope-authority or content I/O, "
                "rejects unknown fields and every query string with 422, and "
                "serializes no internal cause or protected detail."
            ),
        },
    ],
    "deferredEvidence": [
        "real-continuation-redemption",
        "real-citation-redemption",
        "real-federated-source-native-authorization",
    ],
    "futureCarriers": [
        "Continue",
        "OpenCitation",
        "federated/source-native ContextProvider",
    ],
    "notActive": [
        "continuation issuance/redemption",
        "citation locator redemption",
        "federated Provider/source-native ACL I/O",
        "File publication",
    ],
}

CANONICAL_WORKER_LEASE_ACTIVATION: dict[str, object] = {
    "issueRef": "#17",
    "invariantRef": "WORKER-LEASE-007",
    "carrier": "signed one-shot persistent no-op durable-job WorkerLease",
    "status": "active_fail_closed",
    "policyEpochScope": "not-bound-issue-17",
    "controlBoundary": (
        "complete_persistent_noop_job(PostgreSQLWorkerLeaseAuthority, "
        "WorkerLeaseRedemption)"
    ),
    "testEvidence": [
        {
            "id": "LEASE-SIGNING-017",
            "surface": "tests/unit/test_worker_lease.py",
            "oracle": (
                "Versioned domain-separated canonical HMAC-SHA256 signing "
                "with an explicit injected keyring accepts one exact no-op "
                "lease and generically rejects unknown versions, malformed "
                "tokens, tampering, expiry, claim mutation, and UserActor "
                "substitution without exposing claims or key material."
            ),
        },
        {
            "id": "PG-WORKER-LEASE-NOOP-017",
            "surface": "tests/integration/test_worker_lease.py",
            "oracle": (
                "The bounded registered ServicePrincipal receiver binding "
                "redeems one exact same-Organization persistent no-op job only "
                "through the database function's atomic current-row "
                "compare-and-set; the worker role has no table SELECT, and "
                "rollback, replay, mismatch, expiry, and concurrent losers "
                "create zero additional durable transitions and zero "
                "wrong-Organization effects. This does not claim the full "
                "canonical ServiceActor."
            ),
        },
        {
            "id": "WORKER-LEASE-REPLAY-007",
            "surface": "tests/integration/test_worker_lease.py",
            "oracle": (
                "The real worker application seam completes exactly one "
                "persistent no-op job with its server-minted lease; replay "
                "returns only generic work-not-available and leaves the "
                "completed durable state unchanged."
            ),
        },
    ],
    "deferredEvidence": [
        "PROP-WORKER-LEASE-007",
        "PG-WORKER-LEASE-007",
        "DB-011",
        "JOB-001",
        "JOB-005",
        "full ACCEPT-008 per-binding matrix",
    ],
    "futureCarriers": [
        "Source-bound acquisition",
        "Resource/Revision mutation",
        "Policy-Epoch/end-user-delivery-audience-bound WorkerLease",
        "idempotency/generation-bound business mutation",
        "outbox dispatch",
        "File publication",
    ],
    "notActive": [
        "Source",
        "Resource",
        "Revision",
        "Policy Epoch",
        "end-user delivery audience",
        "idempotency",
        "generation",
        "content-bearing mutation",
        "outbox",
        "File publication",
        "full ACCEPT-008 PASS",
    ],
}

CANONICAL_TICKET_AUDIENCE_ACTIVATION: dict[str, object] = {
    "issueRef": "#18",
    "invariantRef": "ACTION-SEPARATION-014",
    "carrier": (
        "signed synthetic ContextAccessTicket Provider read | signed synthetic "
        "ActionTicket no-op channel action"
    ),
    "status": "active_fail_closed",
    "policyEpochScope": "organization-v0",
    "controlBoundary": (
        "ContextAccessTicketReadHandler.read | ActionTicketNoopHandler.perform"
    ),
    "testEvidence": [
        {
            "id": "TICKET-AUDIENCE-018",
            "surface": "tests/unit/test_ticket_audience_separation.py",
            "oracle": (
                "Distinct nominal signed ContextAccessTicket and ActionTicket "
                "types use one explicit versioned keyring while separate signing "
                "domains and token types prevent cross-plane authority. They bind "
                "a validated AuthenticatedInvocation/TrustedDeliveryContext "
                "identity and purpose, trusted Organization, current Organization "
                "Policy Epoch, bounded expiry, operation, and "
                "provider-specific context-read or channel-specific im-send "
                "audiences. Their type-aware deserializers and structurally "
                "separate synthetic Provider-read "
                "and no-op channel handlers accept only the exact matching ticket; "
                "cross-plane, target, audience, identity, freshness, tamper, and "
                "malformed-token probes return one generic non-enumerating "
                "rejection with zero rejected synthetic calls. This is not "
                "production ContextProvider, Sender, or ActionPlane evidence."
            ),
        },
        {
            "id": "PG-TICKET-EPOCH-018",
            "surface": "tests/integration/test_ticket_policy_epoch.py",
            "oracle": (
                "The real PostgreSQL non-owner UserActor transaction exercises "
                "both ticket types before a trusted Control transaction commits an "
                "Organization epoch bump, then rejects both previously valid "
                "tickets before their separate synthetic read and action effect "
                "counters increment. This proves current Organization-v0 epoch "
                "binding, not durable ticket consumption or a real external effect."
            ),
        },
    ],
    "deferredEvidence": [
        "PROP-ACTION-SEPARATION-014",
        "PG-ACTION-SEPARATION-014",
        "ACTION-001 through ACTION-009",
        "full ACCEPT-012 matrix",
    ],
    "futureCarriers": [
        "production ContextProvider read/projection",
        "ContextRuntime ticket integration",
        "BotDelivery",
        "M2 ActionPlane and real Sender",
    ],
    "notActive": [
        "full M2 ActionPlane.prepare/perform",
        "real Sender/external effect",
        "payload/destination/approval/idempotency",
        "durable one-shot/replay/reconciliation",
        "full ACCEPT-012 PASS",
    ],
}

CANONICAL_ACTIVATIONS: list[dict[str, object]] = [
    CANONICAL_REVOCATION_ACTIVATION,
    CANONICAL_UNAVAILABLE_CAPABILITY_ACTIVATION,
    CANONICAL_WORKER_LEASE_ACTIVATION,
    CANONICAL_TICKET_AUDIENCE_ACTIVATION,
]
CANONICAL_ACTIVATION_ISSUE_LIST = ", ".join(
    f"Issue {activation['issueRef']}" for activation in CANONICAL_ACTIVATIONS
)


@dataclass(frozen=True)
class ValidationReport:
    """The stable information emitted by the catalog validation CLI."""

    invariant_count: int
    fixture_count: int
    fixture_mappings: tuple[tuple[str, tuple[str, ...]], ...]


class CatalogValidationError(ValueError):
    """One or more independently actionable catalog validation failures."""

    def __init__(self, errors: Sequence[str]):
        self.errors = tuple(errors)
        super().__init__("\n".join(self.errors))


class _Collector:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def add(self, path: str, message: str) -> None:
        error = f"{path}: {message}"
        if error not in self.errors:
            self.errors.append(error)

    def require_mapping(self, value: object, path: str) -> Mapping[str, Any] | None:
        if not isinstance(value, Mapping):
            self.add(path, "must be an object")
            return None
        return value

    def require_fields(
        self, value: Mapping[str, Any], fields: Sequence[str], path: str
    ) -> None:
        for field in fields:
            if field not in value:
                self.add(f"{path}.{field}", "is required")

    def require_exact_fields(
        self, value: Mapping[str, Any], fields: Sequence[str], path: str
    ) -> None:
        self.require_fields(value, fields, path)
        allowed = set(fields)
        for field in value:
            if field not in allowed:
                self.add(f"{path}.{field}", "is not allowed")

    def require_nonempty_string(self, value: object, path: str) -> bool:
        if not isinstance(value, str) or not value.strip():
            self.add(path, "must be a non-empty string")
            return False
        return True

    def require_string_list(self, value: object, path: str) -> list[str] | None:
        if not isinstance(value, list) or not value:
            self.add(path, "must be a non-empty array of non-empty strings")
            return None
        valid = True
        for index, entry in enumerate(value):
            valid = self.require_nonempty_string(entry, f"{path}[{index}]") and valid
        string_entries = [entry for entry in value if isinstance(entry, str)]
        if len(string_entries) != len(set(string_entries)):
            self.add(path, "must contain unique strings")
            valid = False
        return value if valid else None

    def require_nonempty_object(
        self, value: object, path: str
    ) -> Mapping[str, Any] | None:
        mapping = self.require_mapping(value, path)
        if mapping is not None and not mapping:
            self.add(path, "must not be empty")
            return None
        return mapping

    def require_count(self, value: object, path: str) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            self.add(path, "must be an integer greater than or equal to 0")
            return None
        return value


def load_document(path: str | Path) -> dict[str, Any]:
    """Load a JSON document (and therefore the catalog's JSON-compatible YAML)."""

    document_path = Path(path)
    try:
        with document_path.open(encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CatalogValidationError(
            [f"{document_path}: cannot load JSON-compatible YAML/JSON: {error}"]
        ) from error
    if not isinstance(value, dict):
        raise CatalogValidationError(
            [f"{document_path}: document root must be an object"]
        )
    return value


def _validate_authority(catalog: Mapping[str, Any], collector: _Collector) -> set[str]:
    authority = collector.require_mapping(catalog.get("authority"), "authority")
    if authority is None:
        return set()
    collector.require_exact_fields(
        authority, ("issueRefs", "documentRefs", "reconciliation"), "authority"
    )
    issue_refs = collector.require_string_list(
        authority.get("issueRefs"), "authority.issueRefs"
    )
    document_refs = collector.require_string_list(
        authority.get("documentRefs"), "authority.documentRefs"
    )
    collector.require_nonempty_string(
        authority.get("reconciliation"), "authority.reconciliation"
    )
    return set(issue_refs or ()) | set(document_refs or ())


def _validate_hard_oracles(catalog: Mapping[str, Any], collector: _Collector) -> None:
    hard_oracles = catalog.get("hardOracles")
    if not isinstance(hard_oracles, list):
        collector.add("hardOracles", "must be an array")
        return
    if len(hard_oracles) != len(HARD_ORACLES):
        collector.add("hardOracles", "must contain exactly 3 hard oracles")

    found_names: list[str] = []
    for index, value in enumerate(hard_oracles):
        path = f"hardOracles[{index}]"
        oracle = collector.require_mapping(value, path)
        if oracle is None:
            continue
        collector.require_exact_fields(oracle, ("name", "requiredValue", "veto"), path)
        name = oracle.get("name")
        if collector.require_nonempty_string(name, f"{path}.name"):
            assert isinstance(name, str)
            found_names.append(name)
        required_value = oracle.get("requiredValue")
        if isinstance(required_value, bool) or required_value != 0:
            collector.add(f"{path}.requiredValue", "must be the numeric constant 0")
        if oracle.get("veto") is not True:
            collector.add(f"{path}.veto", "must be true")

    if len(found_names) != len(set(found_names)):
        collector.add("hardOracles", "names must be unique")
    if set(found_names) != set(HARD_ORACLES):
        collector.add(
            "hardOracles",
            "names must be exactly: " + ", ".join(HARD_ORACLES),
        )
    elif tuple(found_names) != HARD_ORACLES:
        collector.add(
            "hardOracles",
            "must use the canonical order: " + ", ".join(HARD_ORACLES),
        )


def _validate_activations(catalog: Mapping[str, Any], collector: _Collector) -> None:
    activations = catalog.get("activations")
    if not isinstance(activations, list):
        collector.add("activations", "must be an array")
        return
    if len(activations) != len(CANONICAL_ACTIVATIONS):
        collector.add(
            "activations",
            f"must contain exactly the canonical ordered "
            f"{CANONICAL_ACTIVATION_ISSUE_LIST} "
            "activation records",
        )

    for index, value in enumerate(activations):
        path = f"activations[{index}]"
        activation = collector.require_mapping(value, path)
        if activation is None:
            continue
        collector.require_exact_fields(activation, ACTIVATION_FIELDS, path)
        test_evidence = activation.get("testEvidence")
        if not isinstance(test_evidence, list):
            collector.add(f"{path}.testEvidence", "must be an array")
        else:
            for evidence_index, evidence_value in enumerate(test_evidence):
                evidence_path = f"{path}.testEvidence[{evidence_index}]"
                evidence = collector.require_mapping(evidence_value, evidence_path)
                if evidence is None:
                    continue
                collector.require_exact_fields(
                    evidence,
                    ACTIVATION_TEST_EVIDENCE_FIELDS,
                    evidence_path,
                )
                for field in ACTIVATION_TEST_EVIDENCE_FIELDS:
                    collector.require_nonempty_string(
                        evidence.get(field), f"{evidence_path}.{field}"
                    )
        for field in ("deferredEvidence", "futureCarriers", "notActive"):
            collector.require_string_list(activation.get(field), f"{path}.{field}")

    if activations != CANONICAL_ACTIVATIONS:
        collector.add(
            "activations",
            f"must exactly preserve the canonical ordered "
            f"{CANONICAL_ACTIVATION_ISSUE_LIST} "
            "activation records and their future/NOT_ACTIVE boundaries",
        )


def _validate_identifier(value: object, path: str, collector: _Collector) -> str | None:
    if not collector.require_nonempty_string(value, path):
        return None
    assert isinstance(value, str)
    if ID_PATTERN.fullmatch(value) is None:
        collector.add(path, f"must match {ID_PATTERN.pattern}")
        return None
    return value


def _validate_authority_refs(
    value: object, path: str, known_authority_refs: set[str], collector: _Collector
) -> None:
    refs = collector.require_string_list(value, path)
    if refs is None:
        return
    for index, ref in enumerate(refs):
        base_ref = ref.split("#", 1)[0]
        if ref not in known_authority_refs and base_ref not in known_authority_refs:
            collector.add(f"{path}[{index}]", f"unknown authority reference {ref!r}")


def _validate_applicability(value: object, path: str, collector: _Collector) -> None:
    applicability = collector.require_mapping(value, path)
    if applicability is None:
        return
    collector.require_exact_fields(
        applicability, ("mode", "applicableFrom", "rationale"), path
    )
    mode = applicability.get("mode")
    if mode not in {"required", "conditional", "not_applicable"}:
        collector.add(
            f"{path}.mode", "must be required, conditional, or not_applicable"
        )
        return
    if mode in {"required", "conditional"}:
        collector.require_nonempty_string(
            applicability.get("applicableFrom"), f"{path}.applicableFrom"
        )
        if applicability.get("rationale") is not None:
            collector.require_nonempty_string(
                applicability.get("rationale"), f"{path}.rationale"
            )
    else:
        if applicability.get("applicableFrom") is not None:
            collector.add(
                f"{path}.applicableFrom", "must be null when mode is not_applicable"
            )
        collector.require_nonempty_string(
            applicability.get("rationale"), f"{path}.rationale"
        )


def _validate_invariants(
    catalog: Mapping[str, Any], known_authority_refs: set[str], collector: _Collector
) -> set[str]:
    invariants = catalog.get("invariants")
    if not isinstance(invariants, list):
        collector.add("invariants", "must be an array")
        return set()
    if len(invariants) != EXPECTED_INVARIANT_COUNT:
        collector.add("invariants", "must contain exactly 15 entries")

    invariant_ids: list[str] = []
    for index, value in enumerate(invariants):
        path = f"invariants[{index}]"
        invariant = collector.require_mapping(value, path)
        if invariant is None:
            continue
        collector.require_exact_fields(invariant, INVARIANT_FIELDS, path)
        invariant_id = _validate_identifier(
            invariant.get("id"), f"{path}.id", collector
        )
        if invariant_id is not None:
            invariant_ids.append(invariant_id)
        for field in ("title", "purpose", "deterministicOracle", "capabilityRef"):
            collector.require_nonempty_string(invariant.get(field), f"{path}.{field}")
        for field in ("threatRefs", "protectedAssets"):
            collector.require_string_list(invariant.get(field), f"{path}.{field}")

        hard_oracle_refs = collector.require_string_list(
            invariant.get("hardOracleRefs"), f"{path}.hardOracleRefs"
        )
        if hard_oracle_refs is not None:
            for ref_index, ref in enumerate(hard_oracle_refs):
                if ref not in HARD_ORACLES:
                    collector.add(
                        f"{path}.hardOracleRefs[{ref_index}]",
                        f"unknown hard oracle {ref!r}",
                    )
        applicability = collector.require_mapping(
            invariant.get("applicability"), f"{path}.applicability"
        )
        _validate_applicability(
            invariant.get("applicability"), f"{path}.applicability", collector
        )
        required_milestones = collector.require_string_list(
            invariant.get("requiredMilestones"), f"{path}.requiredMilestones"
        )
        if invariant_id in CANONICAL_REQUIRED_MILESTONES:
            canonical_milestones = CANONICAL_REQUIRED_MILESTONES[invariant_id]
            first_required = canonical_milestones[0]
            if (
                applicability is not None
                and applicability.get("applicableFrom") != first_required
            ):
                collector.add(
                    f"{path}.applicability.applicableFrom",
                    f"must be {first_required!r} for {invariant_id}",
                )
            if (
                required_milestones is not None
                and tuple(required_milestones) != canonical_milestones
            ):
                collector.add(
                    f"{path}.requiredMilestones",
                    "must be the canonical sequence "
                    f"{list(canonical_milestones)!r} for {invariant_id}",
                )
        if invariant.get("evidenceStatus") != "accepted":
            collector.add(f"{path}.evidenceStatus", "must be accepted")

        expected_evidence = collector.require_mapping(
            invariant.get("expectedEvidence"), f"{path}.expectedEvidence"
        )
        if expected_evidence is not None:
            collector.require_exact_fields(
                expected_evidence, EXPECTED_EVIDENCE_FIELDS, f"{path}.expectedEvidence"
            )
            for field in EXPECTED_EVIDENCE_FIELDS:
                collector.require_string_list(
                    expected_evidence.get(field), f"{path}.expectedEvidence.{field}"
                )
            runtime_evidence = collector.require_string_list(
                expected_evidence.get("runtimeOrDelivery"),
                f"{path}.expectedEvidence.runtimeOrDelivery",
            )
            if (
                runtime_evidence is not None
                and invariant_id in REQUIRED_RUNTIME_EVIDENCE
            ):
                for case_id in REQUIRED_RUNTIME_EVIDENCE[invariant_id]:
                    if case_id not in runtime_evidence:
                        collector.add(
                            f"{path}.expectedEvidence.runtimeOrDelivery",
                            "must preserve absorbed derived case "
                            f"{case_id!r} for {invariant_id}",
                        )
            postgres_evidence = collector.require_string_list(
                expected_evidence.get("postgres"),
                f"{path}.expectedEvidence.postgres",
            )
            if (
                postgres_evidence is not None
                and invariant_id in REQUIRED_POSTGRES_EVIDENCE
            ):
                for case_id in REQUIRED_POSTGRES_EVIDENCE[invariant_id]:
                    if case_id not in postgres_evidence:
                        collector.add(
                            f"{path}.expectedEvidence.postgres",
                            "must preserve canonical revocation evidence "
                            f"{case_id!r} for {invariant_id}",
                        )
        _validate_authority_refs(
            invariant.get("authorityRefs"),
            f"{path}.authorityRefs",
            known_authority_refs,
            collector,
        )

    seen: set[str] = set()
    for invariant_id in invariant_ids:
        if invariant_id in seen:
            collector.add("invariants", f"duplicate id {invariant_id!r}")
        seen.add(invariant_id)
    if tuple(invariant_ids) != CANONICAL_INVARIANT_IDS:
        collector.add(
            "invariants",
            "ids must be the canonical ordered set: "
            + ", ".join(CANONICAL_INVARIANT_IDS),
        )
    return seen


def _validate_metric_object(
    value: object, fields: Sequence[str], path: str, collector: _Collector
) -> dict[str, int]:
    result: dict[str, int] = {}
    metrics = collector.require_mapping(value, path)
    if metrics is None:
        return result
    collector.require_exact_fields(metrics, fields, path)
    for field in fields:
        count = collector.require_count(metrics.get(field), f"{path}.{field}")
        if count is not None:
            result[field] = count
    return result


def _validate_parameterized_case_ids(
    mutation: Mapping[str, Any],
    expected_ids: tuple[str, ...],
    path: str,
    collector: _Collector,
    expected_outcomes: Mapping[str, tuple[int, str]] | None = None,
    *,
    require_activated_oracle: bool = False,
) -> None:
    cases = mutation.get("parameterizedCases")
    if not isinstance(cases, list) or not cases:
        collector.add(f"{path}.parameterizedCases", "must be a non-empty array")
        return
    case_ids: list[str] = []
    for index, case_value in enumerate(cases):
        case = collector.require_mapping(
            case_value, f"{path}.parameterizedCases[{index}]"
        )
        if case is None:
            continue
        case_id = case.get("id")
        if collector.require_nonempty_string(
            case_id, f"{path}.parameterizedCases[{index}].id"
        ):
            assert isinstance(case_id, str)
            case_ids.append(case_id)
            if expected_outcomes is not None and case_id in expected_outcomes:
                expected_status, expected_outcome = expected_outcomes[case_id]
                if case.get("expectedStatus") != expected_status:
                    collector.add(
                        f"{path}.parameterizedCases[{index}].expectedStatus",
                        f"must be {expected_status} for {case_id}",
                    )
                if case.get("expectedOutcome") != expected_outcome:
                    collector.add(
                        f"{path}.parameterizedCases[{index}].expectedOutcome",
                        f"must be {expected_outcome!r} for {case_id}",
                    )
        mutation_value = case.get("mutation")
        mutation_path = f"{path}.parameterizedCases[{index}].mutation"
        if isinstance(mutation_value, str):
            collector.require_nonempty_string(mutation_value, mutation_path)
        elif (
            isinstance(mutation_value, bool)
            or not isinstance(mutation_value, int)
            or mutation_value < 0
        ):
            collector.add(
                mutation_path,
                "must be a non-empty string or a non-negative integer",
            )
        if require_activated_oracle:
            oracle_path = f"{path}.parameterizedCases[{index}].activatedOracle"
            oracle = case.get("activatedOracle")
            if collector.require_nonempty_string(oracle, oracle_path):
                assert isinstance(oracle, str)
                assert isinstance(case_id, str)
                canonical_digest = CANONICAL_ACTIVATED_ORACLE_DIGESTS.get(case_id)
                oracle_digest = hashlib.sha256(oracle.encode("utf-8")).hexdigest()
                if canonical_digest is not None and oracle_digest != canonical_digest:
                    collector.add(
                        oracle_path,
                        f"must preserve the canonical activated oracle for {case_id}",
                    )
        for field in (
            "expectedNewDurableEffects",
            "expectedWrongOrganizationEffects",
            "expectedContentWorkCalls",
        ):
            if case.get(field) != 0 or isinstance(case.get(field), bool):
                collector.add(
                    f"{path}.parameterizedCases[{index}].{field}",
                    "must be the numeric constant 0",
                )
    if len(case_ids) != len(set(case_ids)):
        collector.add(f"{path}.parameterizedCases", "ids must be unique")
    if tuple(case_ids) != expected_ids:
        collector.add(
            f"{path}.parameterizedCases",
            f"ids must be the canonical ordered set {list(expected_ids)!r}",
        )


def _validate_fixture(
    fixture: Mapping[str, Any],
    path: str,
    known_invariant_ids: set[str],
    known_authority_refs: set[str],
    collector: _Collector,
) -> tuple[str | None, tuple[str, ...]]:
    collector.require_exact_fields(fixture, FIXTURE_FIELDS, path)
    fixture_id = _validate_identifier(fixture.get("id"), f"{path}.id", collector)
    collector.require_nonempty_string(fixture.get("title"), f"{path}.title")
    if fixture.get("decisionStatus") not in {"accepted", "future_case"}:
        collector.add(
            f"{path}.decisionStatus",
            "must be accepted or future_case; skipped and deferred are forbidden",
        )

    carrier = collector.require_mapping(fixture.get("carrier"), f"{path}.carrier")
    carrier_status: object = None
    if carrier is not None:
        collector.require_exact_fields(carrier, CARRIER_FIELDS, f"{path}.carrier")
        carrier_status = carrier.get("statusAtM0")
        if carrier_status not in {"available", "unavailable", "future"}:
            collector.add(
                f"{path}.carrier.statusAtM0",
                "must be available, unavailable, or future",
            )
        expected_m0 = (
            "active_fail_closed" if carrier_status == "available" else "fail_closed"
        )
        if carrier.get("m0Expectation") != expected_m0:
            collector.add(
                f"{path}.carrier.m0Expectation",
                f"must be {expected_m0!r} when statusAtM0 is {carrier_status!r}",
            )
        collector.require_nonempty_string(
            carrier.get("upgradeTrigger"), f"{path}.carrier.upgradeTrigger"
        )
        canonical_deferred_carrier = CANONICAL_DEFERRED_FIXTURE_CARRIERS.get(
            fixture_id or ""
        )
        if (
            canonical_deferred_carrier is not None
            and carrier != canonical_deferred_carrier
        ):
            if fixture_id == "ACCEPT-005":
                message = (
                    "must preserve ACCEPT-005 as the future Continue carrier; "
                    "Issue #16 activates only its M0 refusal"
                )
            elif fixture_id == "ACCEPT-008":
                message = (
                    "must preserve the full ACCEPT-008 fixture as "
                    "future/fail_closed; Issue #17 activates only its "
                    "independent persistent no-op carrier"
                )
            elif fixture_id == "ACCEPT-012":
                message = (
                    "must preserve the full ACCEPT-012 fixture as "
                    "unavailable/fail_closed; Issue #18 activates only its "
                    "independent synthetic ticket-audience carrier"
                )
            else:
                message = (
                    "must exactly preserve the canonical future carrier; "
                    "Issue #16 activates only its M0 refusal"
                )
            collector.add(f"{path}.carrier", message)

    setup = collector.require_mapping(fixture.get("setup"), f"{path}.setup")
    if setup is not None:
        collector.require_exact_fields(setup, SETUP_FIELDS, f"{path}.setup")
        collector.require_string_list(
            setup.get("preconditions"), f"{path}.setup.preconditions"
        )
        collector.require_nonempty_object(
            setup.get("trustedIdentity"), f"{path}.setup.trustedIdentity"
        )
    collector.require_nonempty_object(
        fixture.get("adversarialMutation"), f"{path}.adversarialMutation"
    )
    adversarial_mutation = collector.require_mapping(
        fixture.get("adversarialMutation"), f"{path}.adversarialMutation"
    )
    if fixture_id == "ACCEPT-007" and adversarial_mutation is not None:
        _validate_parameterized_case_ids(
            adversarial_mutation,
            TRANSPORT_CASE_IDS,
            f"{path}.adversarialMutation",
            collector,
            TRANSPORT_CASE_OUTCOMES,
        )
    if fixture_id == "ACCEPT-008" and adversarial_mutation is not None:
        _validate_parameterized_case_ids(
            adversarial_mutation,
            WORKER_LEASE_CASE_IDS,
            f"{path}.adversarialMutation",
            collector,
            {case_id: WORKER_LEASE_CASE_OUTCOME for case_id in WORKER_LEASE_CASE_IDS},
        )
    if fixture_id == "ACCEPT-009" and adversarial_mutation is not None:
        if adversarial_mutation.get("caseRef") != "PROV-010":
            collector.add(
                f"{path}.adversarialMutation.caseRef",
                "must be 'PROV-010' for the top-level service-account substitution",
            )
        _validate_parameterized_case_ids(
            adversarial_mutation,
            ACL_PROOF_CASE_IDS,
            f"{path}.adversarialMutation",
            collector,
            {case_id: ACL_PROOF_CASE_OUTCOME for case_id in ACL_PROOF_CASE_IDS},
            require_activated_oracle=True,
        )
    if fixture_id == "ACCEPT-012" and adversarial_mutation is not None:
        _validate_parameterized_case_ids(
            adversarial_mutation,
            AUDIENCE_ACTION_CASE_IDS,
            f"{path}.adversarialMutation",
            collector,
            {
                case_id: AUDIENCE_ACTION_CASE_OUTCOME
                for case_id in AUDIENCE_ACTION_CASE_IDS
            },
            require_activated_oracle=True,
        )
    if fixture_id == "ACCEPT-006" and adversarial_mutation is not None:
        rank_orders = adversarial_mutation.get("candidateRankOrders")
        rendered_orders = (
            tuple(tuple(order) for order in rank_orders)
            if isinstance(rank_orders, list)
            and all(isinstance(order, list) for order in rank_orders)
            else ()
        )
        if rendered_orders != CANDIDATE_RANK_ORDERS:
            collector.add(
                f"{path}.adversarialMutation.candidateRankOrders",
                "must contain all six canonical authorized, denied, and "
                "cross-Organization CandidateRef rank orders",
            )
        for order_index, order in enumerate(rendered_orders):
            if len(order) != len(CANDIDATE_RANK_MEMBERS) or set(order) != set(
                CANDIDATE_RANK_MEMBERS
            ):
                collector.add(
                    f"{path}.adversarialMutation.candidateRankOrders[{order_index}]",
                    "must contain each canonical content-free CandidateRef "
                    "exactly once",
                )
        if adversarial_mutation.get("candidatePayloadFields") != []:
            collector.add(
                f"{path}.adversarialMutation.candidatePayloadFields",
                "must be empty because CandidateRef is content-free",
            )
    collector.require_nonempty_object(fixture.get("operation"), f"{path}.operation")

    expected = collector.require_mapping(fixture.get("expected"), f"{path}.expected")
    io_counts: dict[str, int] = {}
    if expected is not None:
        collector.require_exact_fields(expected, EXPECTED_FIELDS, f"{path}.expected")
        collector.require_nonempty_object(
            expected.get("externalResponse"), f"{path}.expected.externalResponse"
        )
        collector.require_nonempty_object(
            expected.get("packageOrError"), f"{path}.expected.packageOrError"
        )
        evidence_counts = _validate_metric_object(
            expected.get("evidence"),
            EVIDENCE_FIELDS,
            f"{path}.expected.evidence",
            collector,
        )
        business_effect_counts = _validate_metric_object(
            expected.get("businessEffects"),
            BUSINESS_EFFECT_FIELDS,
            f"{path}.expected.businessEffects",
            collector,
        )
        for field in (
            "unauthorizedEvidenceCount",
            "unauthorizedContentBytes",
            "missingContextFallbackCount",
        ):
            if evidence_counts.get(field) != 0:
                collector.add(
                    f"{path}.expected.evidence.{field}",
                    "must be 0 for every acceptance fixture",
                )
        if business_effect_counts.get("wrongOrganizationEffectCount") != 0:
            collector.add(
                f"{path}.expected.businessEffects.wrongOrganizationEffectCount",
                "must be 0 for every acceptance fixture",
            )
        io_counts = _validate_metric_object(
            expected.get("io"), IO_FIELDS, f"{path}.expected.io", collector
        )
    if carrier_status in {"unavailable", "future"}:
        for field in IO_FIELDS:
            if io_counts.get(field) != 0:
                collector.add(
                    f"{path}.expected.io.{field}",
                    "must be 0 for an unavailable or future carrier",
                )

    canonical_fail_closed = CANONICAL_FAIL_CLOSED_OUTCOMES.get(fixture_id or "")
    if canonical_fail_closed is not None and expected is not None:
        for field, canonical_value in canonical_fail_closed.items():
            if expected.get(field) != canonical_value:
                collector.add(
                    f"{path}.expected.{field}",
                    "must preserve the canonical fail-closed outcome "
                    f"for {fixture_id}",
                )

    if fixture_id in RUNTIME_OUTCOME_KINDS and expected is not None:
        external_response = collector.require_mapping(
            expected.get("externalResponse"), f"{path}.expected.externalResponse"
        )
        package_or_error = collector.require_mapping(
            expected.get("packageOrError"), f"{path}.expected.packageOrError"
        )
        response_body = (
            collector.require_mapping(
                external_response.get("body"),
                f"{path}.expected.externalResponse.body",
            )
            if external_response is not None
            else None
        )
        expected_body_kind, expected_result_kind = RUNTIME_OUTCOME_KINDS[fixture_id]
        if external_response is not None and external_response.get("status") != 200:
            collector.add(
                f"{path}.expected.externalResponse.status",
                "must be 200 for the canonical Runtime outcome",
            )
        if (
            response_body is not None
            and response_body.get("kind") != expected_body_kind
        ):
            collector.add(
                f"{path}.expected.externalResponse.body.kind",
                f"must be {expected_body_kind!r} for {fixture_id}",
            )
        if (
            package_or_error is not None
            and package_or_error.get("kind") != expected_result_kind
        ):
            collector.add(
                f"{path}.expected.packageOrError.kind",
                f"must be {expected_result_kind!r} for {fixture_id}",
            )
        if fixture_id in NON_RETRYABLE_RUNTIME_FIXTURES and (
            response_body is not None and response_body.get("retryable") is not False
        ):
            collector.add(
                f"{path}.expected.externalResponse.body.retryable",
                f"must be false for {fixture_id}",
            )
        if fixture_id in RESOLVED_EMPTY_RUNTIME_FIXTURES:
            resolved_package = (
                collector.require_mapping(
                    response_body.get("package"),
                    f"{path}.expected.externalResponse.body.package",
                )
                if response_body is not None
                else None
            )
            coverage = (
                collector.require_mapping(
                    resolved_package.get("coverage"),
                    f"{path}.expected.externalResponse.body.package.coverage",
                )
                if resolved_package is not None
                else None
            )
            if coverage is not None and coverage.get("status") != "empty":
                collector.add(
                    f"{path}.expected.externalResponse.body.package.coverage.status",
                    "must be 'empty' for a hidden or missing Acquire",
                )
            if (
                coverage is not None
                and coverage.get("reason") != "no_authorized_evidence"
            ):
                collector.add(
                    f"{path}.expected.externalResponse.body.package.coverage.reason",
                    "must be 'no_authorized_evidence' for a hidden or missing Acquire",
                )
            for field in ("blocks", "evidence", "gaps"):
                if resolved_package is not None and resolved_package.get(field) != []:
                    message = "must be empty for a resolved empty Package"
                    if field == "gaps":
                        message = (
                            "must be empty because no_authorized_evidence is coverage, "
                            "not a Provider gap"
                        )
                    collector.add(
                        f"{path}.expected.externalResponse.body.package.{field}",
                        message,
                    )
            if package_or_error is not None and (
                package_or_error.get("coverageStatus") != "empty"
            ):
                collector.add(
                    f"{path}.expected.packageOrError.coverageStatus",
                    "must be 'empty' for a hidden or missing Acquire",
                )
            if package_or_error is not None and (
                package_or_error.get("coverageReason") != "no_authorized_evidence"
            ):
                collector.add(
                    f"{path}.expected.packageOrError.coverageReason",
                    "must be 'no_authorized_evidence' for a hidden or missing Acquire",
                )
        if fixture_id in RESOLVED_CONTENT_RUNTIME_FIXTURES:
            resolved_package = (
                collector.require_mapping(
                    response_body.get("package"),
                    f"{path}.expected.externalResponse.body.package",
                )
                if response_body is not None
                else None
            )
            blocks = resolved_package.get("blocks") if resolved_package else None
            evidence = resolved_package.get("evidence") if resolved_package else None
            coverage = (
                collector.require_mapping(
                    resolved_package.get("coverage"),
                    f"{path}.expected.externalResponse.body.package.coverage",
                )
                if resolved_package is not None
                else None
            )
            if coverage is not None and coverage != {"status": "sufficient"}:
                collector.add(
                    f"{path}.expected.externalResponse.body.package.coverage",
                    "must be exactly {'status': 'sufficient'} for authorized content",
                )
            if not isinstance(blocks, list) or len(blocks) != 1:
                collector.add(
                    f"{path}.expected.externalResponse.body.package.blocks",
                    "must contain exactly one authorized block",
                )
            if not isinstance(evidence, list) or len(evidence) != 1:
                collector.add(
                    f"{path}.expected.externalResponse.body.package.evidence",
                    "must contain exactly one authorized Evidence",
                )
            if isinstance(blocks, list) and len(blocks) == 1 and isinstance(
                evidence, list
            ) and len(evidence) == 1:
                block = collector.require_mapping(
                    blocks[0],
                    f"{path}.expected.externalResponse.body.package.blocks[0]",
                )
                item = collector.require_mapping(
                    evidence[0],
                    f"{path}.expected.externalResponse.body.package.evidence[0]",
                )
                if block is not None and item is not None:
                    evidence_ref = item.get("evidenceRef")
                    block_id = block.get("blockId")
                    block_text = block.get("text")
                    if (
                        not isinstance(evidence_ref, str)
                        or EVIDENCE_REF_PATTERN.fullmatch(evidence_ref) is None
                    ):
                        collector.add(
                            f"{path}.expected.externalResponse.body.package.evidence[0].evidenceRef",
                            "must use the closed ev_<64 lowercase hex> format",
                        )
                    if block.get("evidenceRefs") != [evidence_ref]:
                        collector.add(
                            f"{path}.expected.externalResponse.body.package.blocks[0].evidenceRefs",
                            "must resolve to exactly the one Evidence in this Package",
                        )
                    expected_block_id = (
                        f"block_{evidence_ref.removeprefix('ev_')}"
                        if isinstance(evidence_ref, str)
                        and EVIDENCE_REF_PATTERN.fullmatch(evidence_ref)
                        else None
                    )
                    if block_id != expected_block_id:
                        collector.add(
                            f"{path}.expected.externalResponse.body.package.blocks[0].blockId",
                            "must be derived from its exact EvidenceRef",
                        )
                    if (
                        not isinstance(block_text, str)
                        or not block_text
                        or block_text.isspace()
                    ):
                        collector.add(
                            f"{path}.expected.externalResponse.body.package.blocks[0].text",
                            "must be nonblank authorized text",
                        )
                    for evidence_field, package_field in (
                        ("purpose", "purpose"),
                        ("authorizationAsOf", "asOf"),
                        ("decisionRef", "decisionRef"),
                    ):
                        assert resolved_package is not None
                        if item.get(evidence_field) != resolved_package.get(
                            package_field
                        ):
                            collector.add(
                                f"{path}.expected.externalResponse.body.package.evidence[0].{evidence_field}",
                                f"must equal enclosing Package {package_field}",
                            )
                    if tuple(item) != PUBLIC_EVIDENCE_FIELDS:
                        collector.add(
                            f"{path}.expected.externalResponse.body.package.evidence[0]",
                            "must carry the closed complete public authorization "
                            "lineage",
                        )
                    if evidence_ref in CANDIDATE_RANK_MEMBERS:
                        collector.add(
                            f"{path}.expected.externalResponse.body.package.evidence[0].evidenceRef",
                            "must be a request-scoped EvidenceRef distinct from "
                            "CandidateRef",
                        )
                    for forbidden in (
                        "principalRef",
                        "candidateRef",
                        "candidateOrganizationRef",
                        "deniedCount",
                        "deniedFields",
                    ):
                        if forbidden in item:
                            collector.add(
                                f"{path}.expected.externalResponse.body.package.evidence[0].{forbidden}",
                                "must not be public Package Evidence",
                            )
            budget_usage = (
                collector.require_mapping(
                    resolved_package.get("budgetUsage"),
                    f"{path}.expected.externalResponse.body.package.budgetUsage",
                )
                if resolved_package is not None
                else None
            )
            if budget_usage is not None and budget_usage.get("providerCalls") != 0:
                collector.add(
                    f"{path}.expected.externalResponse.body.package.budgetUsage.providerCalls",
                    "must be 0 for internal materialized PostgreSQL projection",
                )
            if isinstance(blocks, list) and budget_usage is not None:
                block_texts = [
                    block.get("text")
                    for block in blocks
                    if isinstance(block, Mapping)
                ]
                if all(isinstance(text, str) for text in block_texts):
                    authorized_bytes = sum(
                        len(text.encode("utf-8"))
                        for text in block_texts
                        if isinstance(text, str)
                    )
                    if budget_usage.get("tokens") != authorized_bytes:
                        collector.add(
                            f"{path}.expected.externalResponse.body.package.budgetUsage.tokens",
                            "must equal the authorized block UTF-8 byte count",
                        )
            if io_counts.get("providerCalls") != 0:
                collector.add(
                    f"{path}.expected.io.providerCalls",
                    "must be 0 for internal materialized PostgreSQL projection",
                )
            package_or_error = collector.require_mapping(
                expected.get("packageOrError"), f"{path}.expected.packageOrError"
            )
            if package_or_error is not None:
                for field in (
                    "unauthorizedFieldCount",
                    "unauthorizedEvidenceRefCount",
                ):
                    if package_or_error.get(field) != 0:
                        collector.add(
                            f"{path}.expected.packageOrError.{field}",
                            "must be the numeric constant 0 for authorized content",
                        )
        if fixture_id == "ACCEPT-011":
            if (
                external_response is not None
                and external_response.get("timingEqualityClaimed") is not False
            ):
                collector.add(
                    f"{path}.expected.externalResponse.timingEqualityClaimed",
                    "must be false before the preregistered M5 timing gate",
                )
            operation = collector.require_mapping(
                fixture.get("operation"), f"{path}.operation"
            )
            comparison_fields = (
                collector.require_string_list(
                    operation.get("comparisonFields"),
                    f"{path}.operation.comparisonFields",
                )
                if operation is not None
                else None
            )
            canonical_comparison_fields = (
                "status",
                "body",
                "headers",
                "domainOutcome",
            )
            if (
                comparison_fields is not None
                and tuple(comparison_fields) != canonical_comparison_fields
            ):
                collector.add(
                    f"{path}.operation.comparisonFields",
                    "must be the canonical ordered comparison set "
                    f"{list(canonical_comparison_fields)!r}",
                )
            normalization_allowlist = (
                collector.require_string_list(
                    operation.get("normalizationAllowlist"),
                    f"{path}.operation.normalizationAllowlist",
                )
                if operation is not None
                else None
            )
            canonical_allowlist = (
                "body.package.organizationRef",
                "body.package.decisionRef",
                "body.package.asOf",
                "body.package.expiresAt",
                "headers.X-Context-Request-Id",
            )
            if (
                normalization_allowlist is not None
                and tuple(normalization_allowlist) != canonical_allowlist
            ):
                collector.add(
                    f"{path}.operation.normalizationAllowlist",
                    "must be the canonical ordered allowlist "
                    f"{list(canonical_allowlist)!r}",
                )
            probes = (
                collector.require_string_list(
                    adversarial_mutation.get("probes"),
                    f"{path}.adversarialMutation.probes",
                )
                if adversarial_mutation is not None
                else None
            )
            canonical_probes = (
                "resource-cross-org",
                "resource-same-org-denied",
                "resource-missing",
            )
            if probes is not None and tuple(probes) != canonical_probes:
                collector.add(
                    f"{path}.adversarialMutation.probes",
                    "must be the canonical ordered probe set "
                    f"{list(canonical_probes)!r}",
                )
            canonical_order: tuple[str, ...] = (
                "cross_organization_denied",
                "same_organization_denied",
                "missing",
            )
            canonical_outcome_order = (
                collector.require_string_list(
                    adversarial_mutation.get("order"),
                    f"{path}.adversarialMutation.order",
                )
                if adversarial_mutation is not None
                else None
            )
            if (
                canonical_outcome_order is not None
                and tuple(canonical_outcome_order) != canonical_order
            ):
                collector.add(
                    f"{path}.adversarialMutation.order",
                    "must be the canonical ordered outcome set "
                    f"{list(canonical_order)!r}",
                )
            if (
                external_response is not None
                and external_response.get("normalizedByteIdenticalAcrossProbes")
                is not True
            ):
                collector.add(
                    f"{path}.expected.externalResponse."
                    "normalizedByteIdenticalAcrossProbes",
                    "must be true for the deterministic non-enumeration gate",
                )
            canonical_headers = {
                "Content-Type": "application/json",
                "Cache-Control": "no-store",
                "X-Context-Request-Id": "normalized-request-id",
            }
            if (
                external_response is not None
                and external_response.get("headers") != canonical_headers
            ):
                collector.add(
                    f"{path}.expected.externalResponse.headers",
                    "must preserve the canonical non-enumerating response headers",
                )
            canonical_empty_package = {
                "organizationRef": (
                    "orgpkg_0000000000000000000000000000000a"
                ),
                "purpose": "context.answer",
                "ttlSeconds": 30,
                "asOf": "2026-07-21T09:30:00Z",
                "expiresAt": "2026-07-21T09:30:30Z",
                "decisionRef": "dec_0000000000000000000000000000000a",
                "blocks": [],
                "evidence": [],
                "gaps": [],
                "budgetUsage": {
                    "tokens": 0,
                    "providerCalls": 0,
                    "costMicrounits": 0,
                    "elapsedMs": 0,
                },
                "coverage": {
                    "status": "empty",
                    "reason": "no_authorized_evidence",
                },
            }
            observed_package = (
                response_body.get("package")
                if response_body is not None
                else None
            )
            if observed_package != canonical_empty_package:
                collector.add(
                    f"{path}.expected.externalResponse.body.package",
                    "must preserve the canonical non-enumerating empty Package",
                )
            canonical_package_summary = {
                "kind": "ContextPackage",
                "packageCount": 4,
                "coverageStatus": "empty",
                "coverageReason": "no_authorized_evidence",
                "deniedCountExposed": False,
                "existenceDetailCount": 0,
            }
            if (
                package_or_error is not None
                and package_or_error != canonical_package_summary
            ):
                collector.add(
                    f"{path}.expected.packageOrError",
                    "must preserve the canonical non-enumerating Package summary",
                )
            canonical_io = {
                "providerCalls": 0,
                "indexCalls": 1,
                "modelCalls": 0,
                "actionCalls": 0,
            }
            if io_counts != canonical_io:
                collector.add(
                    f"{path}.expected.io",
                    "must preserve one CandidateIndex call per probe and zero "
                    "provider, model, or action calls",
                )
            if expected is not None:
                canonical_evidence_metrics = {
                    "unauthorizedEvidenceCount": 0,
                    "unauthorizedContentBytes": 0,
                    "missingContextFallbackCount": 0,
                    "outboundBytes": 0,
                }
                if expected.get("evidence") != canonical_evidence_metrics:
                    collector.add(
                        f"{path}.expected.evidence",
                        "must preserve all zero non-enumeration Evidence metrics",
                    )
                canonical_effects = {
                    "wrongOrganizationEffectCount": 0,
                    "mutationEffectCount": 0,
                    "totalEffectsAfterScenario": 0,
                }
                if expected.get("businessEffects") != canonical_effects:
                    collector.add(
                        f"{path}.expected.businessEffects",
                        "must preserve all zero non-enumeration effects",
                    )

    invariant_refs = collector.require_string_list(
        fixture.get("invariantRefs"), f"{path}.invariantRefs"
    )
    mapping_refs: tuple[str, ...] = tuple(invariant_refs or ())
    if invariant_refs is not None:
        for index, ref in enumerate(invariant_refs):
            if ref not in known_invariant_ids:
                collector.add(
                    f"{path}.invariantRefs[{index}]", f"unknown invariant {ref!r}"
                )
    _validate_authority_refs(
        fixture.get("authorityRefs"),
        f"{path}.authorityRefs",
        known_authority_refs,
        collector,
    )
    return fixture_id, mapping_refs


def _validate_fixtures(
    catalog: Mapping[str, Any],
    known_invariant_ids: set[str],
    known_authority_refs: set[str],
    collector: _Collector,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    fixtures = catalog.get("fixtures")
    if not isinstance(fixtures, list):
        collector.add("fixtures", "must be an array")
        return ()
    if len(fixtures) != EXPECTED_FIXTURE_COUNT:
        collector.add("fixtures", "must contain exactly 12 entries")

    mappings: list[tuple[str, tuple[str, ...]]] = []
    fixture_ids: list[str] = []
    for index, value in enumerate(fixtures):
        path = f"fixtures[{index}]"
        fixture = collector.require_mapping(value, path)
        if fixture is None:
            continue
        fixture_id, refs = _validate_fixture(
            fixture, path, known_invariant_ids, known_authority_refs, collector
        )
        if fixture_id is not None:
            fixture_ids.append(fixture_id)
            mappings.append((fixture_id, refs))

    seen: set[str] = set()
    for fixture_id in fixture_ids:
        if fixture_id in seen:
            collector.add("fixtures", f"duplicate id {fixture_id!r}")
        seen.add(fixture_id)
    if tuple(fixture_ids) != CANONICAL_FIXTURE_IDS:
        collector.add(
            "fixtures",
            "ids must be the canonical ordered set: "
            + ", ".join(CANONICAL_FIXTURE_IDS),
        )
    return tuple(sorted(mappings))


def _resolve_schema_node(
    schema: Mapping[str, Any], node: object, path: str, collector: _Collector
) -> Mapping[str, Any] | None:
    mapping = collector.require_mapping(node, path)
    if mapping is None:
        return None
    reference = mapping.get("$ref")
    if reference is None:
        return mapping
    if not isinstance(reference, str) or not reference.startswith("#/"):
        collector.add(path, "must use a local JSON Pointer $ref")
        return None
    current: object = schema
    for part in reference[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, Mapping) or part not in current:
            collector.add(path, f"unresolvable schema reference {reference!r}")
            return None
        current = current[part]
    resolved = collector.require_mapping(
        current,
        reference,
    )
    return resolved


def _json_values_equal(left: object, right: object) -> bool:
    """Compare JSON values without treating booleans as integers."""

    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, int | float) and isinstance(right, int | float):
        return left == right
    return type(left) is type(right) and left == right


def _matches_json_type(value: object, expected_type: str) -> bool:
    match expected_type:
        case "object":
            return isinstance(value, dict)
        case "array":
            return isinstance(value, list)
        case "string":
            return isinstance(value, str)
        case "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        case "number":
            return isinstance(value, int | float) and not isinstance(value, bool)
        case "boolean":
            return isinstance(value, bool)
        case "null":
            return value is None
        case _:
            return False


def _validate_schema_instance(
    value: object,
    node: object,
    root_schema: Mapping[str, Any],
    path: str,
    collector: _Collector,
) -> None:
    """Apply the Draft 2020-12 keywords used by the tracked catalog schema."""

    if node is False:
        collector.add(path, "is forbidden by the schema")
        return
    if node is True:
        return
    schema_node = collector.require_mapping(node, f"schema for {path}")
    if schema_node is None:
        return

    reference = schema_node.get("$ref")
    if reference is not None:
        resolved = _resolve_schema_node(
            root_schema, schema_node, f"schema for {path}", collector
        )
        if resolved is None:
            return
        _validate_schema_instance(value, resolved, root_schema, path, collector)
        remaining = {key: item for key, item in schema_node.items() if key != "$ref"}
        if remaining:
            _validate_schema_instance(value, remaining, root_schema, path, collector)
        return

    expected_type_names: tuple[str, ...]
    expected_types = schema_node.get("type")
    if isinstance(expected_types, str):
        expected_type_names = (expected_types,)
    elif isinstance(expected_types, list) and all(
        isinstance(entry, str) for entry in expected_types
    ):
        expected_type_names = tuple(expected_types)
    else:
        expected_type_names = ()
    if expected_type_names and not any(
        _matches_json_type(value, expected_type)
        for expected_type in expected_type_names
    ):
        rendered = " or ".join(repr(name) for name in expected_type_names)
        collector.add(path, f"must have type {rendered}")
        return

    if "const" in schema_node and not _json_values_equal(value, schema_node["const"]):
        collector.add(path, f"must equal {schema_node['const']!r}")
    enum = schema_node.get("enum")
    if isinstance(enum, list) and not any(
        _json_values_equal(value, item) for item in enum
    ):
        collector.add(path, "must be one of the schema's enumerated values")

    if isinstance(value, str):
        minimum_length = schema_node.get("minLength")
        if isinstance(minimum_length, int) and len(value) < minimum_length:
            collector.add(path, f"must contain at least {minimum_length} characters")
        pattern = schema_node.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            collector.add(path, f"must match schema pattern {pattern!r}")

    if isinstance(value, int | float) and not isinstance(value, bool):
        minimum = schema_node.get("minimum")
        maximum = schema_node.get("maximum")
        if isinstance(minimum, int | float) and value < minimum:
            collector.add(path, f"must be greater than or equal to {minimum}")
        if isinstance(maximum, int | float) and value > maximum:
            collector.add(path, f"must be less than or equal to {maximum}")

    if isinstance(value, list):
        minimum_items = schema_node.get("minItems")
        maximum_items = schema_node.get("maxItems")
        if isinstance(minimum_items, int) and len(value) < minimum_items:
            collector.add(path, f"must contain at least {minimum_items} items")
        if isinstance(maximum_items, int) and len(value) > maximum_items:
            collector.add(path, f"must contain no more than {maximum_items} items")
        if schema_node.get("uniqueItems") is True:
            encoded = [
                json.dumps(
                    item, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                )
                for item in value
            ]
            if len(encoded) != len(set(encoded)):
                collector.add(path, "must contain unique items")
        prefix_items = schema_node.get("prefixItems")
        prefix_count = 0
        if isinstance(prefix_items, list):
            prefix_count = min(len(value), len(prefix_items))
            for index in range(prefix_count):
                _validate_schema_instance(
                    value[index],
                    prefix_items[index],
                    root_schema,
                    f"{path}[{index}]",
                    collector,
                )
        item_schema = schema_node.get("items")
        if item_schema is not None:
            for index in range(prefix_count, len(value)):
                _validate_schema_instance(
                    value[index],
                    item_schema,
                    root_schema,
                    f"{path}[{index}]",
                    collector,
                )

    if isinstance(value, dict):
        minimum_properties = schema_node.get("minProperties")
        if isinstance(minimum_properties, int) and len(value) < minimum_properties:
            collector.add(
                path, f"must contain at least {minimum_properties} properties"
            )
        required = schema_node.get("required")
        if isinstance(required, list):
            for field in required:
                if isinstance(field, str) and field not in value:
                    collector.add(f"{path}.{field}", "is required by the schema")
        properties = schema_node.get("properties")
        if isinstance(properties, Mapping):
            for field, child_schema in properties.items():
                if field in value:
                    _validate_schema_instance(
                        value[field],
                        child_schema,
                        root_schema,
                        f"{path}.{field}",
                        collector,
                    )
            if schema_node.get("additionalProperties") is False:
                for field in value:
                    if field not in properties:
                        collector.add(f"{path}.{field}", "is not allowed by the schema")

    condition = schema_node.get("if")
    if condition is not None:
        probe = _Collector()
        _validate_schema_instance(value, condition, root_schema, path, probe)
        branch = (
            schema_node.get("then") if not probe.errors else schema_node.get("else")
        )
        if branch is not None:
            _validate_schema_instance(value, branch, root_schema, path, collector)


def _schema_properties(
    node: Mapping[str, Any], path: str, collector: _Collector
) -> Mapping[str, Any] | None:
    return collector.require_mapping(node.get("properties"), f"{path}.properties")


def _require_schema_fields(
    node: Mapping[str, Any], fields: Sequence[str], path: str, collector: _Collector
) -> None:
    required = node.get("required")
    if not isinstance(required, list):
        collector.add(f"{path}.required", "must be an array")
        return
    for field in fields:
        if field not in required:
            collector.add(f"{path}.required", f"must declare {field!r}")


def _require_closed_object_schema(
    node: Mapping[str, Any], fields: Sequence[str], path: str, collector: _Collector
) -> None:
    if node.get("type") != "object":
        collector.add(f"{path}.type", "must be 'object'")
    if node.get("additionalProperties") is not False:
        collector.add(f"{path}.additionalProperties", "must be false")
    _require_schema_fields(node, fields, path, collector)


def _schema_child(
    schema: Mapping[str, Any],
    node: Mapping[str, Any],
    child_name: str,
    path: str,
    collector: _Collector,
) -> Mapping[str, Any] | None:
    properties = _schema_properties(node, path, collector)
    if properties is None or child_name not in properties:
        collector.add(f"{path}.properties.{child_name}", "is required")
        return None
    return _resolve_schema_node(
        schema, properties[child_name], f"{path}.properties.{child_name}", collector
    )


def _validate_schema(
    schema: object, catalog_version: object, collector: _Collector
) -> None:
    root = collector.require_mapping(schema, "schema")
    if root is None:
        return
    if root.get("type") != "object":
        collector.add("schema.type", "must be 'object'")
    if root.get("additionalProperties") is not False:
        collector.add("schema.additionalProperties", "must be false")
    _require_schema_fields(root, TOP_LEVEL_FIELDS, "schema", collector)
    if root.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        collector.add("schema.$schema", "must declare Draft 2020-12")

    version_schema = _schema_child(root, root, "catalogVersion", "schema", collector)
    if version_schema is not None and version_schema.get("const") != catalog_version:
        collector.add(
            "schema.properties.catalogVersion.const",
            "must equal the catalog's catalogVersion",
        )

    hard_oracle_schema = _schema_child(root, root, "hardOracles", "schema", collector)
    if hard_oracle_schema is not None:
        if hard_oracle_schema.get("type") != "array":
            collector.add("schema.properties.hardOracles.type", "must be 'array'")
        for keyword in ("minItems", "maxItems"):
            if hard_oracle_schema.get(keyword) != 3:
                collector.add(f"schema.properties.hardOracles.{keyword}", "must be 3")
        prefix_items = hard_oracle_schema.get("prefixItems")
        if not isinstance(prefix_items, list) or len(prefix_items) != 3:
            collector.add(
                "schema.properties.hardOracles.prefixItems",
                "must freeze exactly 3 hard-oracle entries",
            )
        else:
            for index, expected_name in enumerate(HARD_ORACLES):
                item = _resolve_schema_node(
                    root,
                    prefix_items[index],
                    f"schema.properties.hardOracles.prefixItems[{index}]",
                    collector,
                )
                if item is None:
                    continue
                _require_closed_object_schema(
                    item,
                    ("name", "requiredValue", "veto"),
                    f"schema.properties.hardOracles.prefixItems[{index}]",
                    collector,
                )
                properties = _schema_properties(
                    item,
                    f"schema.properties.hardOracles.prefixItems[{index}]",
                    collector,
                )
                if properties is None:
                    continue
                expected_constants = (
                    ("name", expected_name, str),
                    ("requiredValue", 0, int),
                    ("veto", True, bool),
                )
                for field, expected_constant, expected_type in expected_constants:
                    field_schema = properties.get(field)
                    if (
                        not isinstance(field_schema, Mapping)
                        or type(field_schema.get("const")) is not expected_type
                        or field_schema.get("const") != expected_constant
                    ):
                        collector.add(
                            f"schema.properties.hardOracles.prefixItems[{index}].properties.{field}.const",
                            f"must be {expected_constant!r}",
                        )
        if hard_oracle_schema.get("items") is not False:
            collector.add("schema.properties.hardOracles.items", "must be false")

    array_specs = (
        ("activations", len(CANONICAL_ACTIVATIONS), ACTIVATION_FIELDS),
        ("invariants", EXPECTED_INVARIANT_COUNT, INVARIANT_FIELDS),
        ("fixtures", EXPECTED_FIXTURE_COUNT, FIXTURE_FIELDS),
    )
    item_nodes: dict[str, Mapping[str, Any]] = {}
    for name, count, required_fields in array_specs:
        array_schema = _schema_child(root, root, name, "schema", collector)
        if array_schema is None:
            continue
        if array_schema.get("type") != "array":
            collector.add(f"schema.properties.{name}.type", "must be 'array'")
        for keyword in ("minItems", "maxItems"):
            if array_schema.get(keyword) != count:
                collector.add(f"schema.properties.{name}.{keyword}", f"must be {count}")
        if array_schema.get("uniqueItems") is not True:
            collector.add(f"schema.properties.{name}.uniqueItems", "must be true")
        if name == "activations":
            prefix_items = array_schema.get("prefixItems")
            expected_activation_constants = tuple(CANONICAL_ACTIVATIONS)
            if not isinstance(prefix_items, list) or len(prefix_items) != len(
                expected_activation_constants
            ):
                collector.add(
                    "schema.properties.activations.prefixItems",
                    "must freeze exactly the canonical ordered activation records",
                )
            else:
                for index, expected_activation in enumerate(
                    expected_activation_constants
                ):
                    prefix_item = prefix_items[index]
                    if (
                        not isinstance(prefix_item, Mapping)
                        or prefix_item.get("const") != expected_activation
                    ):
                        collector.add(
                            "schema.properties.activations.prefixItems"
                            f"[{index}].const",
                            "must freeze the canonical "
                            f"Issue {expected_activation['issueRef']} "
                            "activation record",
                        )
            if array_schema.get("items") is not False:
                collector.add(
                    "schema.properties.activations.items", "must be false"
                )
        item_node = array_schema.get("items")
        if name == "activations" and item_node is False:
            prefix_items = array_schema.get("prefixItems")
            item_node = (
                prefix_items[0]
                if isinstance(prefix_items, list) and prefix_items
                else None
            )
            if isinstance(item_node, Mapping) and "const" in item_node:
                item_node = {"$ref": "#/$defs/activation"}
        item = _resolve_schema_node(
            root,
            item_node,
            f"schema.properties.{name}.items",
            collector,
        )
        if item is not None:
            _require_closed_object_schema(
                item, required_fields, f"schema.{name}.items", collector
            )
            item_nodes[name] = item

    activation_item = item_nodes.get("activations")
    if activation_item is not None:
        test_evidence_schema = _schema_child(
            root,
            activation_item,
            "testEvidence",
            "schema.activations.items",
            collector,
        )
        if test_evidence_schema is not None:
            if test_evidence_schema.get("type") != "array":
                collector.add(
                    "schema.activations.items.properties.testEvidence.type",
                    "must be 'array'",
                )
            evidence_item = _resolve_schema_node(
                root,
                test_evidence_schema.get("items"),
                "schema.activations.items.properties.testEvidence.items",
                collector,
            )
            if evidence_item is not None:
                _require_closed_object_schema(
                    evidence_item,
                    ACTIVATION_TEST_EVIDENCE_FIELDS,
                    "schema.activations.items.properties.testEvidence.items",
                    collector,
                )

    invariant_item = item_nodes.get("invariants")
    if invariant_item is not None:
        invariant_id_schema = _schema_child(
            root, invariant_item, "id", "schema.invariants.items", collector
        )
        if (
            invariant_id_schema is not None
            and tuple(invariant_id_schema.get("enum", ())) != CANONICAL_INVARIANT_IDS
        ):
            collector.add(
                "schema.invariants.items.properties.id.enum",
                "must freeze the canonical ordered IDs",
            )
        for child, fields in (
            ("applicability", ("mode", "applicableFrom", "rationale")),
            ("expectedEvidence", EXPECTED_EVIDENCE_FIELDS),
        ):
            child_schema = _schema_child(
                root, invariant_item, child, "schema.invariants.items", collector
            )
            if child_schema is not None:
                _require_closed_object_schema(
                    child_schema,
                    fields,
                    f"schema.invariants.items.properties.{child}",
                    collector,
                )

    fixture_item = item_nodes.get("fixtures")
    if fixture_item is not None:
        fixture_id_schema = _schema_child(
            root, fixture_item, "id", "schema.fixtures.items", collector
        )
        if (
            fixture_id_schema is not None
            and tuple(fixture_id_schema.get("enum", ())) != CANONICAL_FIXTURE_IDS
        ):
            collector.add(
                "schema.fixtures.items.properties.id.enum",
                "must freeze the canonical ordered IDs",
            )
        fixture_children: tuple[tuple[str, tuple[str, ...]], ...] = (
            ("carrier", CARRIER_FIELDS),
            ("setup", SETUP_FIELDS),
        )
        for fixture_child, fixture_fields in fixture_children:
            child_schema = _schema_child(
                root,
                fixture_item,
                fixture_child,
                "schema.fixtures.items",
                collector,
            )
            if child_schema is not None:
                _require_closed_object_schema(
                    child_schema,
                    fixture_fields,
                    f"schema.fixtures.items.properties.{fixture_child}",
                    collector,
                )
        expected_schema = _schema_child(
            root, fixture_item, "expected", "schema.fixtures.items", collector
        )
        if expected_schema is not None:
            _require_closed_object_schema(
                expected_schema,
                EXPECTED_FIELDS,
                "schema.fixtures.items.properties.expected",
                collector,
            )
            expected_children: tuple[tuple[str, tuple[str, ...]], ...] = (
                ("evidence", EVIDENCE_FIELDS),
                ("businessEffects", BUSINESS_EFFECT_FIELDS),
                ("io", IO_FIELDS),
            )
            for expected_child, expected_fields in expected_children:
                child_schema = _schema_child(
                    root,
                    expected_schema,
                    expected_child,
                    "schema.fixtures.items.properties.expected",
                    collector,
                )
                if child_schema is not None:
                    _require_closed_object_schema(
                        child_schema,
                        expected_fields,
                        "schema.fixtures.items.properties.expected.properties."
                        f"{expected_child}",
                        collector,
                    )

        all_of = fixture_item.get("allOf")
        frozen_fixture_carriers = {
            "ACCEPT-008": (
                ACCEPT_008_FUTURE_CARRIER,
                "future/fail_closed",
            ),
            "ACCEPT-012": (
                ACCEPT_012_UNAVAILABLE_CARRIER,
                "unavailable/fail_closed",
            ),
        }
        for frozen_fixture_id, (
            expected_carrier,
            expected_state,
        ) in frozen_fixture_carriers.items():
            fixture_frozen = False
            if isinstance(all_of, list):
                for rule in all_of:
                    if not isinstance(rule, Mapping):
                        continue
                    condition = rule.get("if")
                    consequence = rule.get("then")
                    if not isinstance(condition, Mapping) or not isinstance(
                        consequence, Mapping
                    ):
                        continue
                    condition_properties = condition.get("properties")
                    consequence_properties = consequence.get("properties")
                    if not isinstance(
                        condition_properties, Mapping
                    ) or not isinstance(consequence_properties, Mapping):
                        continue
                    fixture_id_condition = condition_properties.get("id")
                    carrier_consequence = consequence_properties.get("carrier")
                    if (
                        isinstance(fixture_id_condition, Mapping)
                        and fixture_id_condition.get("const") == frozen_fixture_id
                        and isinstance(carrier_consequence, Mapping)
                        and carrier_consequence.get("const") == expected_carrier
                    ):
                        fixture_frozen = True
                        break
            if not fixture_frozen:
                collector.add(
                    "schema.fixtures.items.allOf",
                    f"must independently freeze {frozen_fixture_id} as the "
                    f"canonical {expected_state} fixture carrier",
                )

    definitions = root.get("$defs")
    definitions = collector.require_mapping(definitions, "schema.$defs")
    if definitions is not None:
        closed_object_definitions: dict[str, tuple[str, ...]] = {
            "authority": ("issueRefs", "documentRefs", "reconciliation"),
            "activation": ACTIVATION_FIELDS,
            "activationTestEvidence": ACTIVATION_TEST_EVIDENCE_FIELDS,
            "applicability": ("mode", "applicableFrom", "rationale"),
            "expectedEvidence": EXPECTED_EVIDENCE_FIELDS,
            "carrier": CARRIER_FIELDS,
            "setup": SETUP_FIELDS,
            "trustedIdentity": (),
            "invocationIdentity": ("organizationRef", "principalRef", "purpose"),
            "adversarialMutation": ("kind",),
            "probeAttempt": ("invocation", "target"),
            "requestNarrowing": ("sourceRefs",),
            "injectedBodyFields": (
                "organizationRef",
                "principalRef",
                "purpose",
                "audience",
                "acl",
                "rawSql",
                "bypassAuthorization",
            ),
            "mutatedClaim": (),
            "parameterizedCase": PARAMETERIZED_CASE_FIELDS,
            "operation": ("interface", "request"),
            "externalResponse": ("status",),
            "responseBody": (),
            "packageOrError": ("kind",),
            "evidenceMetrics": EVIDENCE_FIELDS,
            "businessEffectMetrics": BUSINESS_EFFECT_FIELDS,
            "ioMetrics": IO_FIELDS,
            "expected": EXPECTED_FIELDS,
            "invariant": INVARIANT_FIELDS,
            "fixture": FIXTURE_FIELDS,
        }
        for definition_name, definition_fields in closed_object_definitions.items():
            definition = definitions.get(definition_name)
            definition_path = f"schema.$defs.{definition_name}"
            if definition is None:
                continue
            definition_mapping = collector.require_mapping(definition, definition_path)
            if definition_mapping is None:
                continue
            if definition_mapping.get("type") != "object":
                collector.add(f"{definition_path}.type", "must be 'object'")
            if definition_mapping.get("additionalProperties") is not False:
                collector.add(
                    f"{definition_path}.additionalProperties", "must be false"
                )
            if definition_fields:
                _require_schema_fields(
                    definition_mapping, definition_fields, definition_path, collector
                )

        invariant_id_value = definitions.get("invariantId")
        invariant_id = (
            collector.require_mapping(invariant_id_value, "schema.$defs.invariantId")
            if invariant_id_value is not None
            else None
        )
        if (
            invariant_id is not None
            and tuple(invariant_id.get("enum", ())) != CANONICAL_INVARIANT_IDS
        ):
            collector.add(
                "schema.$defs.invariantId.enum",
                "must freeze the canonical ordered IDs",
            )
        fixture_id_value = definitions.get("fixtureId")
        fixture_id = (
            collector.require_mapping(fixture_id_value, "schema.$defs.fixtureId")
            if fixture_id_value is not None
            else None
        )
        if (
            fixture_id is not None
            and tuple(fixture_id.get("enum", ())) != CANONICAL_FIXTURE_IDS
        ):
            collector.add(
                "schema.$defs.fixtureId.enum",
                "must freeze the canonical ordered IDs",
            )


def validate_catalog(
    catalog: Mapping[str, Any], schema: Mapping[str, Any]
) -> ValidationReport:
    """Validate a catalog and schema together, returning report-ready facts."""

    collector = _Collector()
    collector.require_exact_fields(catalog, TOP_LEVEL_FIELDS, "catalog")
    collector.require_nonempty_string(catalog.get("catalogVersion"), "catalogVersion")
    if catalog.get("catalogVersion") != SUPPORTED_CATALOG_VERSION:
        collector.add(
            "catalogVersion",
            f"must be the supported version {SUPPORTED_CATALOG_VERSION!r}",
        )
    known_authority_refs = _validate_authority(catalog, collector)
    _validate_hard_oracles(catalog, collector)
    _validate_activations(catalog, collector)
    invariant_ids = _validate_invariants(catalog, known_authority_refs, collector)
    mappings = _validate_fixtures(
        catalog, invariant_ids, known_authority_refs, collector
    )
    _validate_schema(schema, catalog.get("catalogVersion"), collector)
    if isinstance(schema, Mapping):
        _validate_schema_instance(catalog, schema, schema, "catalog", collector)
    if collector.errors:
        raise CatalogValidationError(collector.errors)

    return ValidationReport(
        invariant_count=len(catalog["invariants"]),
        fixture_count=len(catalog["fixtures"]),
        fixture_mappings=mappings,
    )


_MARKDOWN_HEADING = re.compile(r"^ {0,3}#{1,6}(?:[ \t]+|$)(.*)$")
_MARKDOWN_CLOSING_HASHES = re.compile(r"[ \t]+#+[ \t]*$")
_MARKDOWN_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_MARKDOWN_LINK = re.compile(r"!?\[([^]]*)\]\([^)]*\)")
_MARKDOWN_HTML_TAG = re.compile(r"<[^>]+>")


def _github_heading_slug(heading: str) -> str:
    """Return the GitHub-style base slug for a Markdown heading."""

    visible_text = _MARKDOWN_LINK.sub(r"\1", heading)
    visible_text = _MARKDOWN_HTML_TAG.sub("", visible_text)
    visible_text = html.unescape(visible_text).lower()
    slug_characters = (
        character
        for character in visible_text
        if character.isalnum() or character.isspace() or character in {"-", "_"}
    )
    return re.sub(r"\s", "-", "".join(slug_characters))


def _markdown_heading_anchors(document: Path) -> set[str]:
    """Extract GitHub-style anchors, including deterministic duplicate suffixes."""

    anchors: set[str] = set()
    fence_character: str | None = None
    fence_length = 0
    for line in document.read_text(encoding="utf-8").splitlines():
        fence = _MARKDOWN_FENCE.match(line)
        if fence is not None:
            marker = fence.group(1)
            if fence_character is None:
                fence_character = marker[0]
                fence_length = len(marker)
            elif marker[0] == fence_character and len(marker) >= fence_length:
                fence_character = None
                fence_length = 0
            continue
        if fence_character is not None:
            continue

        match = _MARKDOWN_HEADING.match(line)
        if match is None:
            continue
        heading = _MARKDOWN_CLOSING_HASHES.sub("", match.group(1)).strip()
        base_slug = _github_heading_slug(heading)
        if not base_slug:
            continue
        anchor = base_slug
        suffix = 0
        while anchor in anchors:
            suffix += 1
            anchor = f"{base_slug}-{suffix}"
        anchors.add(anchor)
    return anchors


def _git_tracks(repository_root: Path, ref: str) -> bool:
    """Return whether *ref* is present in the repository's Git index."""

    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repository_root),
                "ls-files",
                "--error-unmatch",
                "--",
                ref,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    return result.returncode == 0


def _iter_catalog_authority_refs(
    catalog: Mapping[str, Any],
) -> Sequence[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    for collection_name in ("invariants", "fixtures"):
        collection = catalog.get(collection_name)
        if not isinstance(collection, list):
            continue
        for item_index, item in enumerate(collection):
            if not isinstance(item, Mapping):
                continue
            authority_refs = item.get("authorityRefs")
            if not isinstance(authority_refs, list):
                continue
            for ref_index, ref in enumerate(authority_refs):
                if isinstance(ref, str):
                    refs.append(
                        (
                            f"{collection_name}[{item_index}].authorityRefs[{ref_index}]",
                            ref,
                        )
                    )
    return refs


def _validate_document_paths(catalog: Mapping[str, Any], repository_root: Path) -> None:
    errors: list[str] = []
    authority = catalog.get("authority")
    if not isinstance(authority, Mapping):
        return
    document_refs = authority.get("documentRefs")
    if not isinstance(document_refs, list):
        return
    root = repository_root.resolve()
    tracked_documents: dict[str, Path] = {}
    for index, ref in enumerate(document_refs):
        if not isinstance(ref, str):
            continue
        path = f"authority.documentRefs[{index}]"
        ref_path = Path(ref)
        if ref_path.is_absolute() or ".." in ref_path.parts:
            errors.append(f"{path}: must be a repository-relative path without '..'")
            continue
        resolved = (root / ref_path).resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            errors.append(f"{path}: must resolve inside the repository")
            continue
        if not resolved.is_file():
            errors.append(f"{path}: tracked document does not exist: {ref!r}")
            continue
        if not _git_tracks(root, ref):
            errors.append(f"{path}: must reference a Git-tracked file: {ref!r}")
            continue
        tracked_documents[ref] = resolved

    heading_anchors: dict[str, set[str]] = {}
    for reference_path, ref in _iter_catalog_authority_refs(catalog):
        document_ref, separator, fragment = ref.partition("#")
        if not document_ref or not separator:
            # Bare references such as issue ``#5`` are not document anchors.
            continue
        document = tracked_documents.get(document_ref)
        if document is None or document.suffix.lower() not in {".md", ".markdown"}:
            continue
        anchors = heading_anchors.get(document_ref)
        if anchors is None:
            try:
                anchors = _markdown_heading_anchors(document)
            except (OSError, UnicodeError):
                # Existence/tracking errors above remain the actionable boundary.
                continue
            heading_anchors[document_ref] = anchors
        if unquote(fragment) not in anchors:
            errors.append(
                f"{reference_path}: Markdown heading anchor does not exist: {ref!r}"
            )
    if errors:
        raise CatalogValidationError(errors)


def validate_files(
    catalog_path: str | Path = DEFAULT_CATALOG_PATH,
    schema_path: str | Path = DEFAULT_SCHEMA_PATH,
    *,
    repository_root: str | Path = REPOSITORY_ROOT,
) -> ValidationReport:
    """Load and validate the tracked catalog and schema files."""

    catalog = load_document(catalog_path)
    report = validate_catalog(catalog, load_document(schema_path))
    _validate_document_paths(catalog, Path(repository_root))
    return report


def render_report(report: ValidationReport) -> str:
    """Render the count and complete fixture-to-invariant mapping evidence."""

    lines = [
        (
            "security catalog valid: "
            f"{report.invariant_count} invariants, {report.fixture_count} fixtures"
        ),
        "fixture -> invariants:",
    ]
    for fixture_id, invariant_refs in report.fixture_mappings:
        lines.append(f"  {fixture_id}: {', '.join(invariant_refs)}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "catalog",
        nargs="?",
        type=Path,
        default=DEFAULT_CATALOG_PATH,
        help=(
            "catalog path (default: repository eval/catalogs/security-invariants.yaml)"
        ),
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_SCHEMA_PATH,
        help=(
            "schema path (default: repository "
            "eval/catalogs/security-catalog.schema.json)"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = validate_files(args.catalog, args.schema)
    except CatalogValidationError as error:
        print("security catalog invalid:", file=sys.stderr)
        for message in error.errors:
            print(f"  - {message}", file=sys.stderr)
        return 1
    print(render_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
