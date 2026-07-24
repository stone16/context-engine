from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from adapters.http.contracts import ContextPackageWire
from scripts.validate_security_catalog import (
    ACCEPT_002_ACTIVE_CARRIER,
    ACCEPT_005_FUTURE_CARRIER,
    ACCEPT_008_FUTURE_CARRIER,
    ACCEPT_009_FUTURE_CARRIER,
    ACCEPT_010_FUTURE_CARRIER,
    ACCEPT_012_UNAVAILABLE_CARRIER,
    ACL_PROOF_CASE_IDS,
    AUDIENCE_ACTION_CASE_IDS,
    CANONICAL_ACTION_PREPARE_ACTIVATION,
    CANONICAL_ACTIVATION_ISSUE_LIST,
    CANONICAL_ACTIVATIONS,
    CANONICAL_CONTEXT_RUN_ACTIVATION,
    CANONICAL_EGRESS_GRANT_ACTIVATION,
    CANONICAL_FAIL_CLOSED_OUTCOMES,
    CANONICAL_FIELD_PROJECTION_ACTIVATION,
    CANONICAL_INVARIANT_IDS,
    CANONICAL_OPENAPI_V0_ACTIVATION,
    CANONICAL_PRIVATE_DELIVERY_EVIDENCE_ACTIVATION,
    CANONICAL_REVOCATION_ACTIVATION,
    CANONICAL_TICKET_AUDIENCE_ACTIVATION,
    CANONICAL_TYPESCRIPT_SDK_ACTIVATION,
    CANONICAL_UNAVAILABLE_CAPABILITY_ACTIVATION,
    CANONICAL_WORKER_LEASE_ACTIVATION,
    DEFAULT_CATALOG_PATH,
    DEFAULT_SCHEMA_PATH,
    REQUIRED_POSTGRES_EVIDENCE,
    REQUIRED_PROPERTY_EVIDENCE,
    REQUIRED_RUNTIME_EVIDENCE,
    CatalogValidationError,
    load_document,
    main,
    render_report,
    validate_catalog,
    validate_files,
)

HARD_ORACLE_NAMES = (
    "Unauthorized Evidence",
    "wrong-Organization effect",
    "missing-context fallback",
)

CANONICAL_REQUIRED_MILESTONES = {
    "TENANT-OWNERSHIP-001": ["M0", "M1"],
    "TENANT-FK-002": ["M0", "M1"],
    "RLS-FAIL-CLOSED-003": ["M0"],
    "SCOPE-INTERSECTION-004": ["M0", "M1", "M5"],
    "INDEX-NOT-AUTHORITY-005": ["M0", "M1", "M3"],
    "REVOCATION-006": ["M1", "M2"],
    "WORKER-LEASE-007": ["M1", "M3"],
    "TRANSPORT-UNTRUSTED-008": ["M1", "M2"],
    "NON-ENUMERATION-009": ["M1", "M5"],
    "CITATION-AUTH-010": ["M2", "M3"],
    "EGRESS-011": ["M2", "M5"],
    "TRACE-REDACTION-012": ["M0", "M1"],
    "ACTION-SEPARATION-014": ["M2"],
    "CROSS-ORG-LEARN-015": ["M0", "M3"],
    "RELEASE-OWNER-019": ["M0", "M3"],
}

RUNTIME_OUTCOMES = {
    "ACCEPT-001": ("resolved", "ContextPackage"),
    "ACCEPT-003": ("resolved", "ContextPackage"),
    "ACCEPT-004": ("resolved", "ContextPackage"),
    "ACCEPT-005": ("request_not_available", "request_not_available"),
    "ACCEPT-006": ("resolved", "ContextPackage"),
    "ACCEPT-009": ("request_not_available", "request_not_available"),
    "ACCEPT-010": ("citation_not_available", "citation_not_available"),
    "ACCEPT-011": ("resolved", "ContextPackage"),
}

TRANSPORT_CASE_IDS = [
    "BODY-INJECTION",
    "DELIV-001",
    "DELIV-002",
    "DELIV-003",
    "DELIV-004",
]
WORKER_LEASE_CASE_IDS = [
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
]


def object_at(mapping: dict[str, object], *keys: str) -> dict[str, object]:
    """Return a nested object while keeping malformed test data type-safe."""
    current: object = mapping
    for key in keys:
        assert isinstance(current, dict)
        current = current[key]
    assert isinstance(current, dict)
    return cast(dict[str, object], current)


def object_list_at(mapping: dict[str, object], *keys: str) -> list[dict[str, object]]:
    """Return a nested list of objects with runtime shape assertions."""
    current: object = mapping
    for key in keys:
        assert isinstance(current, dict)
        current = current[key]
    assert isinstance(current, list)
    assert all(isinstance(item, dict) for item in current)
    return cast(list[dict[str, object]], current)


def test_active_evidence_surfaces_reference_existing_test_nodes() -> None:
    """Canonical active evidence cannot drift to a renamed or deleted test."""

    for activation in CANONICAL_ACTIVATIONS:
        test_evidence = activation["testEvidence"]
        assert isinstance(test_evidence, list)
        for evidence in test_evidence:
            assert isinstance(evidence, dict)
            surface = evidence["surface"]
            assert isinstance(surface, str)
            for reference in surface.split():
                file_ref, separator, node_ref = reference.partition("::")
                path = Path(__file__).parents[2] / file_ref
                assert path.is_file(), reference
                if separator:
                    source = path.read_text(encoding="utf-8")
                    assert f"def {node_ref}(" in source, reference


def test_reusable_schema_accepts_every_canonical_activation_value() -> None:
    """Reusable activation definitions cannot lag canonical frozen records."""

    catalog = load_document(DEFAULT_CATALOG_PATH)
    schema = load_document(DEFAULT_SCHEMA_PATH)
    validate_catalog(catalog, schema)


def make_catalog() -> dict[str, object]:
    invariants = []
    for number, invariant_id in enumerate(CANONICAL_INVARIANT_IDS, start=1):
        required_milestones = CANONICAL_REQUIRED_MILESTONES[invariant_id]
        invariants.append(
            {
                "id": invariant_id,
                "title": f"Invariant {number}",
                "purpose": "Make the security boundary deterministic.",
                "threatRefs": ["TM-01"],
                "protectedAssets": ["A-01"],
                "deterministicOracle": "The prohibited observation is exactly zero.",
                "hardOracleRefs": [HARD_ORACLE_NAMES[(number - 1) % 3]],
                "applicability": {
                    "mode": "required",
                    "applicableFrom": required_milestones[0],
                    "rationale": None,
                },
                "capabilityRef": "tenant-isolation",
                "requiredMilestones": required_milestones,
                "evidenceStatus": "accepted",
                "expectedEvidence": {
                    "property": [
                        f"PROP-{number:03d}",
                        *REQUIRED_PROPERTY_EVIDENCE.get(invariant_id, ()),
                    ],
                    "postgres": [
                        f"PG-{number:03d}",
                        *REQUIRED_POSTGRES_EVIDENCE.get(invariant_id, ()),
                    ],
                    "runtimeOrDelivery": [
                        f"RUNTIME-{number:03d}",
                        *REQUIRED_RUNTIME_EVIDENCE.get(invariant_id, ()),
                    ],
                },
                "authorityRefs": [
                    "docs/security/context-engine-threat-model.md#5-hard-oracles"
                ],
            }
        )

    revocation_evidence = cast(
        dict[str, object], invariants[CANONICAL_INVARIANT_IDS.index("REVOCATION-006")]
    )["expectedEvidence"]
    assert isinstance(revocation_evidence, dict)
    revocation_evidence["postgres"] = [
        "PG-REVOCATION-006",
        "CACHE-002",
        "BLOB-002",
    ]

    fixtures = []
    for number in range(1, 13):
        fixture_id = f"ACCEPT-{number:03d}"
        external_response: dict[str, object] = {
            "status": 404,
            "body": "generic",
        }
        package_or_error: dict[str, object] = {
            "kind": "error",
            "code": "not_found",
        }
        operation: dict[str, object] = {"kind": "resolve"}
        adversarial_mutation: dict[str, object] = {
            "kind": "cross_organization_reference"
        }
        if fixture_id in RUNTIME_OUTCOMES:
            body_kind, result_kind = RUNTIME_OUTCOMES[fixture_id]
            body: dict[str, object] = {"kind": body_kind}
            external_response = {"status": 200, "body": body}
            package_or_error = {"kind": result_kind, "code": "domain_outcome"}
            if fixture_id in {
                "ACCEPT-001",
                "ACCEPT-003",
                "ACCEPT-004",
                "ACCEPT-006",
                "ACCEPT-011",
            }:
                package: dict[str, object] = {
                    "packageId": "pkg_0000000000000000000000000000000a",
                    "purpose": "context.answer",
                    "audienceDigest": "a" * 64,
                    "policyEpoch": 1,
                    "policySnapshotRef": "policy-snapshot-a",
                    "decisionRef": "dec_0000000000000000000000000000000a",
                    "runRef": "run-authorized-a",
                    "releaseManifestRef": "manifest-m0-empty-v0",
                    "retentionPolicyRef": "package-digest-only-retention-v1",
                    "asOf": "2026-07-21T09:30:00Z",
                    "expiresAt": "2026-07-21T09:30:30Z",
                    "ttlSeconds": 30,
                    "tokenizerRef": "utf8-byte-budget-v1",
                    "packageSchemaRef": "context-package-openapi-v0",
                    "blocks": [],
                    "evidence": [],
                    "gaps": [],
                    "coverage": {
                        "status": "empty",
                        "reason": "no_authorized_evidence",
                    },
                    "budgetUsage": {
                        "tokens": 0,
                        "providerCalls": 0,
                        "costMicrounits": 0,
                        "elapsedMs": 0,
                    },
                    "continuation": None,
                }
                body["package"] = package
                package_or_error["coverageStatus"] = "empty"
                package_or_error["coverageReason"] = "no_authorized_evidence"
                if fixture_id in {
                    "ACCEPT-001",
                    "ACCEPT-003",
                    "ACCEPT-004",
                    "ACCEPT-006",
                }:
                    evidence_ref = "ev_" + "a" * 64
                    package["blocks"] = [
                        {
                            "blockId": "block_" + "a" * 64,
                            "text": "A-safe",
                            "evidenceRefs": [evidence_ref],
                        }
                    ]
                    package["evidence"] = [
                        {
                            "evidenceRef": evidence_ref,
                            "sourceRef": "source-synthetic-a",
                            "resourceRef": "resource-authorized-a",
                            "revisionRef": "revision-authorized-a",
                            "fragmentRef": "fragment-authorized-a",
                            "projectedFields": ["body"],
                            "runRef": "run-authorized-a",
                            "purpose": "context.answer",
                            "authorizationAsOf": "2026-07-21T09:30:00Z",
                            "decisionRef": "dec_0000000000000000000000000000000a",
                            "policySnapshotRef": "policy-snapshot-a",
                            "policyEpoch": 1,
                            "sourceAclEvidence": {
                                "kind": "mirrored",
                                "projectionRef": "source-decision-a",
                                "aclAsOf": "2026-07-21T09:30:00Z",
                                "freshnessProfileRef": (
                                    "file-source-access-current-transaction-v1"
                                ),
                            },
                            "citationOpenRef": None,
                        }
                    ]
                    package["coverage"] = {"status": "sufficient"}
                    budget_usage = package["budgetUsage"]
                    assert isinstance(budget_usage, dict)
                    budget_usage["tokens"] = 6
                    package_or_error["coverageStatus"] = "sufficient"
                    package_or_error.pop("coverageReason")
                    package_or_error["unauthorizedFieldCount"] = 0
                    package_or_error["unauthorizedEvidenceRefCount"] = 0
                    package["packageDigest"] = (
                        "9dbfcc7aa6c6a05acd5d61122fc2b8d6a4741794ac2eff59a1bbbaae2e1c616f"
                    )
            if fixture_id in {"ACCEPT-005", "ACCEPT-009"}:
                body["retryable"] = False
            if fixture_id == "ACCEPT-011":
                package["packageDigest"] = (
                    "94d68444d124f453eb6c62e0132ea8e90a3c4017230e8e7b3bfe138d1daa10d1"
                )
                external_response["headers"] = {
                    "Content-Type": "application/json",
                    "Cache-Control": "no-store",
                    "X-Context-Request-Id": "normalized-request-id",
                }
                external_response["normalizedByteIdenticalAcrossProbes"] = True
                external_response["timingEqualityClaimed"] = False
                operation["comparisonFields"] = [
                    "status",
                    "body",
                    "headers",
                    "domainOutcome",
                ]
                operation["normalizationAllowlist"] = [
                    "body.package.packageId",
                    "body.package.decisionRef",
                    "body.package.policySnapshotRef",
                    "body.package.runRef",
                    "body.package.asOf",
                    "body.package.expiresAt",
                    "body.package.packageDigest",
                    "headers.X-Context-Request-Id",
                ]
                adversarial_mutation["probes"] = [
                    "resource-cross-org",
                    "resource-same-org-denied",
                    "resource-missing",
                ]
                adversarial_mutation["order"] = [
                    "cross_organization_denied",
                    "same_organization_denied",
                    "missing",
                ]
                package_or_error.pop("code")
                package_or_error.update(
                    {
                        "packageCount": 4,
                        "deniedCountExposed": False,
                        "existenceDetailCount": 0,
                    }
                )
        if fixture_id in {"ACCEPT-007", "ACCEPT-008"}:
            case_ids = (
                TRANSPORT_CASE_IDS
                if fixture_id == "ACCEPT-007"
                else WORKER_LEASE_CASE_IDS
            )
            adversarial_mutation["parameterizedCases"] = [
                {
                    "id": case_id,
                    "mutation": "Mutate exactly the named trust binding.",
                    "expectedStatus": (
                        422
                        if case_id == "BODY-INJECTION"
                        else 200
                        if fixture_id == "ACCEPT-007"
                        else 404
                    ),
                    "expectedOutcome": (
                        "invalid_request"
                        if case_id == "BODY-INJECTION"
                        else "request_not_available"
                        if fixture_id == "ACCEPT-007"
                        else "work_not_available"
                    ),
                    "expectedNewDurableEffects": 0,
                    "expectedWrongOrganizationEffects": 0,
                    "expectedContentWorkCalls": 0,
                }
                for case_id in case_ids
            ]
        if fixture_id == "ACCEPT-006":
            adversarial_mutation["candidatePayloadFields"] = []
            adversarial_mutation["candidateRankOrders"] = [
                list(order)
                for order in (
                    (
                        "candidate-authorized-a",
                        "candidate-denied-a",
                        "candidate-hostile-b",
                    ),
                    (
                        "candidate-authorized-a",
                        "candidate-hostile-b",
                        "candidate-denied-a",
                    ),
                    (
                        "candidate-denied-a",
                        "candidate-authorized-a",
                        "candidate-hostile-b",
                    ),
                    (
                        "candidate-denied-a",
                        "candidate-hostile-b",
                        "candidate-authorized-a",
                    ),
                    (
                        "candidate-hostile-b",
                        "candidate-authorized-a",
                        "candidate-denied-a",
                    ),
                    (
                        "candidate-hostile-b",
                        "candidate-denied-a",
                        "candidate-authorized-a",
                    ),
                )
            ]
        if fixture_id in {"ACCEPT-009", "ACCEPT-012"}:
            tracked_catalog = load_document(DEFAULT_CATALOG_PATH)
            tracked_oracles: dict[str, str] = {}
            for tracked_fixture in cast(
                list[dict[str, Any]], tracked_catalog["fixtures"]
            ):
                if tracked_fixture["id"] not in {"ACCEPT-009", "ACCEPT-012"}:
                    continue
                tracked_cases = cast(
                    list[dict[str, Any]],
                    tracked_fixture["adversarialMutation"]["parameterizedCases"],
                )
                tracked_oracles.update(
                    {
                        cast(str, case["id"]): cast(str, case["activatedOracle"])
                        for case in tracked_cases
                    }
                )
            derived_case_ids = (
                ACL_PROOF_CASE_IDS
                if fixture_id == "ACCEPT-009"
                else AUDIENCE_ACTION_CASE_IDS
            )
            adversarial_mutation["caseRef"] = (
                "PROV-010" if fixture_id == "ACCEPT-009" else "ACTION-001"
            )
            adversarial_mutation["parameterizedCases"] = [
                {
                    "id": case_id,
                    "mutation": "Exercise the named derived security obligation.",
                    "expectedStatus": 200 if fixture_id == "ACCEPT-009" else 404,
                    "expectedOutcome": (
                        "request_not_available"
                        if fixture_id == "ACCEPT-009"
                        else "action_not_available"
                    ),
                    "expectedNewDurableEffects": 0,
                    "expectedWrongOrganizationEffects": 0,
                    "expectedContentWorkCalls": 0,
                    "activatedOracle": tracked_oracles[case_id],
                }
                for case_id in derived_case_ids
            ]
        if fixture_id in CANONICAL_FAIL_CLOSED_OUTCOMES:
            canonical_outcome = copy.deepcopy(
                CANONICAL_FAIL_CLOSED_OUTCOMES[fixture_id]
            )
            external_response = cast(
                dict[str, object], canonical_outcome["externalResponse"]
            )
            package_or_error = cast(
                dict[str, object], canonical_outcome["packageOrError"]
            )
        carrier = {
            "statusAtM0": "available",
            "m0Expectation": "active_fail_closed",
            "upgradeTrigger": "Upgrade when the complete carrier is activated.",
        }
        if fixture_id == "ACCEPT-002":
            carrier = copy.deepcopy(ACCEPT_002_ACTIVE_CARRIER)
        elif fixture_id == "ACCEPT-005":
            carrier = copy.deepcopy(ACCEPT_005_FUTURE_CARRIER)
        elif fixture_id == "ACCEPT-008":
            carrier = copy.deepcopy(ACCEPT_008_FUTURE_CARRIER)
        elif fixture_id == "ACCEPT-009":
            carrier = copy.deepcopy(ACCEPT_009_FUTURE_CARRIER)
        elif fixture_id == "ACCEPT-010":
            carrier = copy.deepcopy(ACCEPT_010_FUTURE_CARRIER)
        elif fixture_id == "ACCEPT-012":
            carrier = copy.deepcopy(ACCEPT_012_UNAVAILABLE_CARRIER)
        fixtures.append(
            {
                "id": fixture_id,
                "title": f"Acceptance fixture {number}",
                "decisionStatus": "accepted",
                "carrier": carrier,
                "setup": {
                    "preconditions": ["Two isolated Organizations exist."],
                    "trustedIdentity": {"kind": "authenticated_invocation"},
                },
                "adversarialMutation": adversarial_mutation,
                "operation": operation,
                "expected": {
                    "externalResponse": external_response,
                    "packageOrError": package_or_error,
                    "evidence": {
                        "unauthorizedEvidenceCount": 0,
                        "unauthorizedContentBytes": 0,
                        "missingContextFallbackCount": 0,
                        "outboundBytes": 0,
                    },
                    "businessEffects": {
                        "wrongOrganizationEffectCount": 0,
                        "mutationEffectCount": 0,
                        "totalEffectsAfterScenario": 0,
                    },
                    "io": {
                        "providerCalls": 0,
                        "indexCalls": 1 if fixture_id == "ACCEPT-011" else 0,
                        "modelCalls": 0,
                        "actionCalls": 0,
                    },
                },
                "invariantRefs": [CANONICAL_INVARIANT_IDS[(number - 1) % 15]],
                "authorityRefs": [
                    "#5",
                    "docs/security/context-engine-threat-model.md#5-hard-oracles",
                ],
            }
        )

    return {
        "catalogVersion": "1.3.0",
        "authority": {
            "issueRefs": ["#5"],
            "documentRefs": ["docs/security/context-engine-threat-model.md"],
            "reconciliation": "Accepted decisions take precedence.",
        },
        "hardOracles": [
            {"name": name, "requiredValue": 0, "veto": True}
            for name in HARD_ORACLE_NAMES
        ],
        "activations": [
            copy.deepcopy(CANONICAL_REVOCATION_ACTIVATION),
            copy.deepcopy(CANONICAL_UNAVAILABLE_CAPABILITY_ACTIVATION),
            copy.deepcopy(CANONICAL_WORKER_LEASE_ACTIVATION),
            copy.deepcopy(CANONICAL_TICKET_AUDIENCE_ACTIVATION),
            copy.deepcopy(CANONICAL_CONTEXT_RUN_ACTIVATION),
            copy.deepcopy(CANONICAL_FIELD_PROJECTION_ACTIVATION),
            copy.deepcopy(CANONICAL_PRIVATE_DELIVERY_EVIDENCE_ACTIVATION),
            copy.deepcopy(CANONICAL_EGRESS_GRANT_ACTIVATION),
            copy.deepcopy(CANONICAL_OPENAPI_V0_ACTIVATION),
            copy.deepcopy(CANONICAL_TYPESCRIPT_SDK_ACTIVATION),
            copy.deepcopy(CANONICAL_ACTION_PREPARE_ACTIVATION),
        ],
        "invariants": invariants,
        "fixtures": fixtures,
    }


def make_schema() -> dict[str, object]:
    invariant_required = [
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
    ]
    fixture_required = [
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
    ]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "catalogVersion",
            "authority",
            "hardOracles",
            "activations",
            "invariants",
            "fixtures",
        ],
        "properties": {
            "catalogVersion": {"const": "1.3.0"},
            "authority": {"type": "object"},
            "hardOracles": {
                "type": "array",
                "minItems": 3,
                "maxItems": 3,
                "prefixItems": [
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["name", "requiredValue", "veto"],
                        "properties": {
                            "name": {"const": name},
                            "requiredValue": {"const": 0},
                            "veto": {"const": True},
                        },
                    }
                    for name in HARD_ORACLE_NAMES
                ],
                "items": False,
            },
            "activations": {
                "type": "array",
                "minItems": len(CANONICAL_ACTIVATIONS),
                "maxItems": len(CANONICAL_ACTIVATIONS),
                "uniqueItems": True,
                "prefixItems": [
                    {"const": copy.deepcopy(CANONICAL_REVOCATION_ACTIVATION)},
                    {
                        "const": copy.deepcopy(
                            CANONICAL_UNAVAILABLE_CAPABILITY_ACTIVATION
                        )
                    },
                    {"const": copy.deepcopy(CANONICAL_WORKER_LEASE_ACTIVATION)},
                    {"const": copy.deepcopy(CANONICAL_TICKET_AUDIENCE_ACTIVATION)},
                    {"const": copy.deepcopy(CANONICAL_CONTEXT_RUN_ACTIVATION)},
                    {"const": copy.deepcopy(CANONICAL_FIELD_PROJECTION_ACTIVATION)},
                    {
                        "const": copy.deepcopy(
                            CANONICAL_PRIVATE_DELIVERY_EVIDENCE_ACTIVATION
                        )
                    },
                    {"const": copy.deepcopy(CANONICAL_EGRESS_GRANT_ACTIVATION)},
                    {"const": copy.deepcopy(CANONICAL_OPENAPI_V0_ACTIVATION)},
                    {"const": copy.deepcopy(CANONICAL_TYPESCRIPT_SDK_ACTIVATION)},
                    {"const": copy.deepcopy(CANONICAL_ACTION_PREPARE_ACTIVATION)},
                ],
                "items": False,
            },
            "invariants": {
                "type": "array",
                "minItems": 15,
                "maxItems": 15,
                "uniqueItems": True,
                "items": {"$ref": "#/$defs/invariant"},
            },
            "fixtures": {
                "type": "array",
                "minItems": 12,
                "maxItems": 12,
                "uniqueItems": True,
                "items": {"$ref": "#/$defs/fixture"},
            },
        },
        "$defs": {
            "activationTestEvidence": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "surface", "oracle"],
                "properties": {
                    "id": {},
                    "surface": {},
                    "oracle": {},
                },
            },
            "activation": {
                "type": "object",
                "additionalProperties": False,
                "required": [
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
                ],
                "properties": {
                    "issueRef": {},
                    "invariantRef": {},
                    "carrier": {},
                    "status": {},
                    "policyEpochScope": {},
                    "controlBoundary": {},
                    "testEvidence": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/activationTestEvidence"},
                    },
                    "deferredEvidence": {},
                    "futureCarriers": {},
                    "notActive": {},
                },
            },
            "invariant": {
                "type": "object",
                "additionalProperties": False,
                "required": invariant_required,
                "properties": {
                    **{field: {} for field in invariant_required},
                    "id": {"enum": list(CANONICAL_INVARIANT_IDS)},
                    "applicability": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["mode", "applicableFrom", "rationale"],
                        "properties": {
                            "mode": {},
                            "applicableFrom": {},
                            "rationale": {},
                        },
                    },
                    "expectedEvidence": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["property", "postgres", "runtimeOrDelivery"],
                        "properties": {
                            "property": {},
                            "postgres": {},
                            "runtimeOrDelivery": {},
                        },
                    },
                },
            },
            "fixture": {
                "type": "object",
                "additionalProperties": False,
                "required": fixture_required,
                "properties": {
                    **{field: {} for field in fixture_required},
                    "id": {"enum": [f"ACCEPT-{number:03d}" for number in range(1, 13)]},
                    "carrier": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["statusAtM0", "m0Expectation", "upgradeTrigger"],
                        "properties": {
                            "statusAtM0": {},
                            "m0Expectation": {},
                            "upgradeTrigger": {},
                        },
                    },
                    "setup": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["preconditions", "trustedIdentity"],
                        "properties": {
                            "preconditions": {},
                            "trustedIdentity": {},
                        },
                    },
                    "expected": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "externalResponse",
                            "packageOrError",
                            "evidence",
                            "businessEffects",
                            "io",
                        ],
                        "properties": {
                            "externalResponse": {},
                            "packageOrError": {},
                            "evidence": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": [
                                    "unauthorizedEvidenceCount",
                                    "unauthorizedContentBytes",
                                    "missingContextFallbackCount",
                                    "outboundBytes",
                                ],
                                "properties": {
                                    "unauthorizedEvidenceCount": {},
                                    "unauthorizedContentBytes": {},
                                    "missingContextFallbackCount": {},
                                    "outboundBytes": {},
                                },
                            },
                            "businessEffects": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": [
                                    "wrongOrganizationEffectCount",
                                    "mutationEffectCount",
                                    "totalEffectsAfterScenario",
                                ],
                                "properties": {
                                    "wrongOrganizationEffectCount": {},
                                    "mutationEffectCount": {},
                                    "totalEffectsAfterScenario": {},
                                },
                            },
                            "io": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": [
                                    "providerCalls",
                                    "indexCalls",
                                    "modelCalls",
                                    "actionCalls",
                                ],
                                "properties": {
                                    "providerCalls": {},
                                    "indexCalls": {},
                                    "modelCalls": {},
                                    "actionCalls": {},
                                },
                            },
                        },
                    },
                },
                "allOf": [
                    {
                        "if": {
                            "properties": {"id": {"const": "ACCEPT-002"}},
                            "required": ["id"],
                        },
                        "then": {
                            "properties": {
                                "carrier": {
                                    "const": copy.deepcopy(ACCEPT_002_ACTIVE_CARRIER)
                                }
                            }
                        },
                    },
                    {
                        "if": {
                            "properties": {"id": {"const": "ACCEPT-008"}},
                            "required": ["id"],
                        },
                        "then": {
                            "properties": {
                                "carrier": {
                                    "const": copy.deepcopy(ACCEPT_008_FUTURE_CARRIER)
                                }
                            }
                        },
                    },
                    {
                        "if": {
                            "properties": {"id": {"const": "ACCEPT-012"}},
                            "required": ["id"],
                        },
                        "then": {
                            "properties": {
                                "carrier": {
                                    "const": copy.deepcopy(
                                        ACCEPT_012_UNAVAILABLE_CARRIER
                                    )
                                }
                            }
                        },
                    },
                ],
            },
        },
    }


class ValidateSecurityCatalogTests(unittest.TestCase):
    def assert_catalog_error(
        self,
        catalog: dict[str, object],
        expected_message: str,
        schema: dict[str, object] | None = None,
    ) -> CatalogValidationError:
        with self.assertRaises(CatalogValidationError) as raised:
            validate_catalog(catalog, schema or make_schema())
        self.assertIn(expected_message, raised.exception.errors)
        return raised.exception

    def test_valid_catalog_returns_counts_and_fixture_mapping_report(self) -> None:
        report = validate_catalog(make_catalog(), make_schema())

        self.assertEqual(report.invariant_count, 15)
        self.assertEqual(report.fixture_count, 12)
        self.assertEqual(
            render_report(report).splitlines()[:4],
            [
                "security catalog valid: 15 invariants, 12 fixtures",
                "fixture -> invariants:",
                "  ACCEPT-001: TENANT-OWNERSHIP-001",
                "  ACCEPT-002: TENANT-FK-002",
            ],
        )

    def test_catalog_version_is_frozen(self) -> None:
        catalog = make_catalog()
        schema = make_schema()
        catalog["catalogVersion"] = "999.0.0"
        object_at(schema, "properties", "catalogVersion")["const"] = "999.0.0"

        self.assert_catalog_error(
            catalog,
            "catalogVersion: must be the supported version '1.3.0'",
            schema,
        )

    def test_hard_oracle_order_is_frozen(self) -> None:
        catalog = make_catalog()
        hard_oracles = object_list_at(catalog, "hardOracles")
        hard_oracles[0], hard_oracles[1] = hard_oracles[1], hard_oracles[0]

        self.assert_catalog_error(
            catalog,
            "hardOracles: must use the canonical order: "
            + ", ".join(HARD_ORACLE_NAMES),
        )

    def test_issue_15_revocation_activation_is_frozen(self) -> None:
        catalog = make_catalog()
        activation = object_list_at(catalog, "activations")[0]
        self.assertEqual(activation, CANONICAL_REVOCATION_ACTIVATION)
        future_carriers = activation["futureCarriers"]
        assert isinstance(future_carriers, list)
        self.assertEqual(
            future_carriers[-2:],
            ["production ContextAccessTicket", "production ActionTicket"],
        )

        test_evidence = object_list_at(activation, "testEvidence")
        test_evidence[0]["surface"] = "tests/unit/false_green.py"
        test_evidence[1]["id"] = "RUN-011"
        activation["policyEpochScope"] = "resource"
        activation["deferredEvidence"] = []
        activation["futureCarriers"] = ["Continue"]
        activation["notActive"] = []

        error = self.assert_catalog_error(
            catalog,
            "activations: must exactly preserve the canonical ordered "
            f"{CANONICAL_ACTIVATION_ISSUE_LIST} activation records and their "
            "future/NOT_ACTIVE boundaries",
        )
        self.assertTrue(
            any(message.startswith("catalog.activations") for message in error.errors)
            or any(message.startswith("activations") for message in error.errors)
        )

    def test_issue_16_unavailable_capability_activation_is_frozen(self) -> None:
        catalog = make_catalog()
        activation = object_list_at(catalog, "activations")[1]
        self.assertEqual(activation, CANONICAL_UNAVAILABLE_CAPABILITY_ACTIVATION)

        test_evidence = object_list_at(activation, "testEvidence")
        test_evidence[0]["surface"] = "tests/unit/false_green.py"
        activation["controlBoundary"] = "ContextProvider"
        activation["futureCarriers"] = []

        self.assert_catalog_error(
            catalog,
            "activations: must exactly preserve the canonical ordered "
            f"{CANONICAL_ACTIVATION_ISSUE_LIST} activation records and their "
            "future/NOT_ACTIVE boundaries",
        )

    def test_issue_17_worker_lease_activation_is_bounded_and_frozen(self) -> None:
        catalog = make_catalog()
        activation = object_list_at(catalog, "activations")[2]
        self.assertEqual(activation, CANONICAL_WORKER_LEASE_ACTIVATION)
        self.assertEqual(activation["policyEpochScope"], "not-bound-issue-17")

        test_evidence = object_list_at(activation, "testEvidence")
        test_evidence[0]["surface"] = "tests/unit/false_green.py"
        activation["policyEpochScope"] = "organization-v0"
        activation["futureCarriers"] = []
        activation["notActive"] = ["none"]

        self.assert_catalog_error(
            catalog,
            "activations: must exactly preserve the canonical ordered "
            f"{CANONICAL_ACTIVATION_ISSUE_LIST} activation records and their "
            "future/NOT_ACTIVE boundaries",
        )

    def test_issue_18_ticket_audience_activation_is_bounded_and_frozen(self) -> None:
        catalog = make_catalog()
        activation = object_list_at(catalog, "activations")[3]
        self.assertEqual(activation, CANONICAL_TICKET_AUDIENCE_ACTIVATION)
        self.assertEqual(activation["invariantRef"], "ACTION-SEPARATION-014")
        self.assertEqual(activation["policyEpochScope"], "organization-v0")
        self.assertEqual(
            [
                (evidence["id"], evidence["surface"])
                for evidence in object_list_at(activation, "testEvidence")
            ],
            [
                (
                    "TICKET-AUDIENCE-018",
                    "tests/unit/test_ticket_audience_separation.py",
                ),
                (
                    "PG-TICKET-EPOCH-018",
                    "tests/integration/test_ticket_policy_epoch.py",
                ),
            ],
        )
        self.assertEqual(
            activation["notActive"],
            [
                "full M2 ActionPlane.prepare/perform",
                "real Sender/external effect",
                "payload/destination/approval/idempotency",
                "durable one-shot/replay/reconciliation",
                "full ACCEPT-012 PASS",
            ],
        )

        test_evidence = object_list_at(activation, "testEvidence")
        test_evidence[0]["surface"] = "tests/unit/false_green.py"
        activation["carrier"] = "production ActionPlane"
        activation["notActive"] = []

        self.assert_catalog_error(
            catalog,
            "activations: must exactly preserve the canonical ordered "
            f"{CANONICAL_ACTIVATION_ISSUE_LIST} activation records and their "
            "future/NOT_ACTIVE boundaries",
        )

    def test_issue_18_synthetic_tickets_do_not_false_green_full_accept_012(
        self,
    ) -> None:
        catalog = make_catalog()
        fixture = object_list_at(catalog, "fixtures")[11]
        carrier = object_at(fixture, "carrier")
        self.assertEqual(
            object_list_at(catalog, "activations")[3]["status"],
            "active_fail_closed",
        )
        self.assertEqual(carrier, ACCEPT_012_UNAVAILABLE_CARRIER)
        self.assertEqual(carrier["statusAtM0"], "unavailable")
        self.assertEqual(carrier["m0Expectation"], "fail_closed")

        carrier.update(
            {
                "statusAtM0": "available",
                "m0Expectation": "active_fail_closed",
                "upgradeTrigger": "Issue #18 proves the full fixture.",
            }
        )
        self.assert_catalog_error(
            catalog,
            "fixtures[11].carrier: must preserve the full ACCEPT-012 fixture as "
            "unavailable/fail_closed; Issue #18 activates only its independent "
            "synthetic ticket-audience carrier",
        )

    def test_issue_19_context_run_activation_is_bounded_and_frozen(self) -> None:
        catalog = make_catalog()
        activation = object_list_at(catalog, "activations")[4]
        self.assertEqual(activation, CANONICAL_CONTEXT_RUN_ACTIVATION)
        self.assertEqual(activation["invariantRef"], "TRACE-REDACTION-012")
        self.assertEqual(activation["policyEpochScope"], "organization-v0")
        self.assertEqual(
            [
                (evidence["id"], evidence["surface"])
                for evidence in object_list_at(activation, "testEvidence")
            ],
            [
                ("DIGEST-019", "tests/unit/test_package_digest.py"),
                (
                    "RUN-LINEAGE-019",
                    "tests/integration/test_runtime_empty_package_integration.py",
                ),
                (
                    "AUTHORIZED-RUN-019",
                    "tests/integration/test_runtime_authorized_evidence_integration.py",
                ),
                (
                    "PG-TRACE-REDACTION-012",
                    "tests/integration/test_context_run_schema.py",
                ),
            ],
        )
        not_active = activation["notActive"]
        assert isinstance(not_active, list)
        self.assertIn("raw query retention", not_active)
        self.assertIn("full ContextPackage body retention", not_active)

        object_list_at(activation, "testEvidence")[1]["surface"] = (
            "tests/unit/false_green.py"
        )
        activation["futureCarriers"] = []
        activation["notActive"] = []

        self.assert_catalog_error(
            catalog,
            "activations: must exactly preserve the canonical ordered "
            f"{CANONICAL_ACTIVATION_ISSUE_LIST} activation records and their "
            "future/NOT_ACTIVE boundaries",
        )

    def test_issue_48_accept_002_field_projection_activation_is_frozen(self) -> None:
        catalog = make_catalog()
        activation = object_list_at(catalog, "activations")[5]
        self.assertEqual(activation, CANONICAL_FIELD_PROJECTION_ACTIVATION)
        self.assertEqual(activation["invariantRef"], "SCOPE-INTERSECTION-004")
        self.assertEqual(activation["policyEpochScope"], "organization-v0")
        canonical_test_evidence = CANONICAL_FIELD_PROJECTION_ACTIVATION["testEvidence"]
        assert isinstance(canonical_test_evidence, list)
        assert all(isinstance(evidence, dict) for evidence in canonical_test_evidence)
        self.assertEqual(
            [
                (evidence["id"], evidence["surface"])
                for evidence in object_list_at(activation, "testEvidence")
            ],
            [
                (
                    "PROP-FIELD-PROJECTION-048",
                    canonical_test_evidence[0]["surface"],
                ),
                (
                    "PG-FIELD-PROJECTION-048",
                    canonical_test_evidence[1]["surface"],
                ),
                (
                    "HTTP-ACCEPT-002-048",
                    "tests/integration/"
                    "test_membership_field_projection_integration.py::"
                    "test_accept_002_same_organization_memberships_receive_only_"
                    "authorized_fields",
                ),
            ],
        )
        not_active = activation["notActive"]
        assert isinstance(not_active, list)
        self.assertIn("Issue #20 gate or runner substitution", not_active)

        object_list_at(activation, "testEvidence")[2]["surface"] = (
            "tests/integration/false_green.py::test_accept_002"
        )
        activation["futureCarriers"] = []
        activation["notActive"] = []

        self.assert_catalog_error(
            catalog,
            "activations: must exactly preserve the canonical ordered "
            f"{CANONICAL_ACTIVATION_ISSUE_LIST} activation records and their "
            "future/NOT_ACTIVE boundaries",
        )

    def test_accept_002_carrier_is_frozen_to_issue_48_activation(self) -> None:
        catalog = make_catalog()
        fixture = object_list_at(catalog, "fixtures")[1]
        self.assertEqual(fixture["carrier"], ACCEPT_002_ACTIVE_CARRIER)

        object_at(fixture, "carrier")["upgradeTrigger"] = (
            "A conceptual fixture is enough."
        )
        self.assert_catalog_error(
            catalog,
            "fixtures[1].carrier: must preserve ACCEPT-002 as the exact Issue #48 "
            "active Membership field-projection carrier",
        )

    def test_issue_48_evidence_ids_are_required_on_all_mapped_invariants(self) -> None:
        catalog = make_catalog()
        invariants = {
            invariant["id"]: invariant
            for invariant in object_list_at(catalog, "invariants")
        }
        for invariant_id in (
            "SCOPE-INTERSECTION-004",
            "INDEX-NOT-AUTHORITY-005",
            "TRACE-REDACTION-012",
        ):
            expected = object_at(invariants[invariant_id], "expectedEvidence")
            for field, evidence_id in (
                ("property", "PROP-FIELD-PROJECTION-048"),
                ("postgres", "PG-FIELD-PROJECTION-048"),
                ("runtimeOrDelivery", "HTTP-ACCEPT-002-048"),
            ):
                values = expected[field]
                assert isinstance(values, list)
                self.assertIn(evidence_id, values)

        expected = object_at(invariants["SCOPE-INTERSECTION-004"], "expectedEvidence")
        property_evidence = expected["property"]
        assert isinstance(property_evidence, list)
        property_evidence.remove("PROP-FIELD-PROJECTION-048")
        self.assert_catalog_error(
            catalog,
            "invariants[3].expectedEvidence.property: must preserve activated "
            "field-projection evidence 'PROP-FIELD-PROJECTION-048' for "
            "SCOPE-INTERSECTION-004",
        )

    def test_issue_17_noop_does_not_false_green_full_accept_008(self) -> None:
        catalog = make_catalog()
        fixture = object_list_at(catalog, "fixtures")[7]
        carrier = object_at(fixture, "carrier")
        self.assertEqual(
            object_list_at(catalog, "activations")[2]["status"],
            "active_fail_closed",
        )
        self.assertEqual(carrier, ACCEPT_008_FUTURE_CARRIER)
        self.assertEqual(carrier["statusAtM0"], "future")
        self.assertEqual(carrier["m0Expectation"], "fail_closed")

        carrier.update(
            {
                "statusAtM0": "available",
                "m0Expectation": "active_fail_closed",
                "upgradeTrigger": "Issue #17 proves the full fixture.",
            }
        )
        error = self.assert_catalog_error(
            catalog,
            "fixtures[7].carrier: must preserve the full ACCEPT-008 fixture as "
            "future/fail_closed; Issue #17 activates only its independent "
            "persistent no-op carrier",
        )
        self.assertNotIn("full ACCEPT-008 PASS", str(error))

    def test_issue_16_refusal_does_not_false_green_future_carriers(self) -> None:
        catalog = make_catalog()
        fixtures = {
            fixture["id"]: fixture for fixture in object_list_at(catalog, "fixtures")
        }

        for fixture_id in ("ACCEPT-005", "ACCEPT-009", "ACCEPT-010"):
            fixture = fixtures[fixture_id]
            fixture["carrier"] = {
                "statusAtM0": "available",
                "m0Expectation": "active_fail_closed",
                "upgradeTrigger": "Issue #16 activates the real carrier.",
            }

        error = self.assert_catalog_error(
            catalog,
            "fixtures[4].carrier: must preserve ACCEPT-005 as the future Continue "
            "carrier; Issue #16 activates only its M0 refusal",
        )
        self.assertIn(
            "fixtures[8].carrier: must exactly preserve the canonical future "
            "carrier; Issue #16 activates only its M0 refusal",
            error.errors,
        )

    def test_activation_schema_independently_freezes_record_order_and_values(
        self,
    ) -> None:
        catalog = make_catalog()
        schema = load_document(DEFAULT_SCHEMA_PATH)
        activations = object_list_at(catalog, "activations")
        activations[0]["issueRef"] = "#16"
        activations[1]["issueRef"] = "#15"

        with self.assertRaises(CatalogValidationError) as raised:
            validate_catalog(catalog, schema)
        self.assertTrue(
            any(
                error.startswith("catalog.activations[0]: must equal")
                for error in raised.exception.errors
            )
        )
        self.assertTrue(
            any(
                error.startswith("catalog.activations[1]: must equal")
                for error in raised.exception.errors
            )
        )

        catalog = make_catalog()
        activation = object_list_at(catalog, "activations")[0]
        activation["futureCarriers"] = ["Continue"]
        with self.assertRaises(CatalogValidationError) as raised:
            validate_catalog(catalog, schema)
        self.assertTrue(
            any(
                error.startswith("catalog.activations[0]: must equal")
                for error in raised.exception.errors
            )
        )

    def test_issue_67_action_prepare_activation_stops_before_effects(self) -> None:
        catalog = make_catalog()
        activation = object_list_at(catalog, "activations")[10]

        self.assertEqual(activation, CANONICAL_ACTION_PREPARE_ACTIVATION)
        self.assertEqual(activation["invariantRef"], "ACTION-SEPARATION-014")
        self.assertEqual(
            object_list_at(activation, "testEvidence")[0]["id"],
            "PG-ACTION-PREPARE-067",
        )
        future_carriers = activation["futureCarriers"]
        not_active = activation["notActive"]
        assert isinstance(future_carriers, list)
        assert isinstance(not_active, list)
        self.assertIn("ActionPlane.perform", future_carriers)
        self.assertIn(
            "external channel write or business effect",
            not_active,
        )
        self.assertIn("full ACCEPT-012 pass", not_active)

        activation["notActive"] = []
        self.assert_catalog_error(
            catalog,
            "activations: must exactly preserve the canonical ordered "
            f"{CANONICAL_ACTIVATION_ISSUE_LIST} activation records and their "
            "future/NOT_ACTIVE boundaries",
        )

    def test_schema_independently_freezes_full_accept_008_as_future(self) -> None:
        catalog = make_catalog()
        schema = load_document(DEFAULT_SCHEMA_PATH)
        fixture_schema = object_at(schema, "$defs", "fixture")
        all_of = fixture_schema["allOf"]
        assert isinstance(all_of, list)
        accept_008_rule = next(
            rule
            for rule in all_of
            if isinstance(rule, dict)
            and isinstance(rule.get("if"), dict)
            and isinstance(rule["if"].get("properties"), dict)
            and isinstance(rule["if"]["properties"].get("id"), dict)
            and rule["if"]["properties"]["id"].get("const") == "ACCEPT-008"
        )
        object_at(accept_008_rule, "then", "properties", "carrier")["const"] = {
            "statusAtM0": "available",
            "m0Expectation": "active_fail_closed",
            "upgradeTrigger": "Issue #17 proves the full fixture.",
        }

        self.assert_catalog_error(
            catalog,
            "schema.fixtures.items.allOf: must independently freeze ACCEPT-008 "
            "as the canonical future/fail_closed fixture carrier",
            schema,
        )

    def test_schema_independently_freezes_full_accept_012_as_unavailable(
        self,
    ) -> None:
        catalog = make_catalog()
        schema = load_document(DEFAULT_SCHEMA_PATH)
        fixture_schema = object_at(schema, "$defs", "fixture")
        all_of = fixture_schema["allOf"]
        assert isinstance(all_of, list)
        accept_012_rule = next(
            rule
            for rule in all_of
            if isinstance(rule, dict)
            and isinstance(rule.get("if"), dict)
            and isinstance(rule["if"].get("properties"), dict)
            and isinstance(rule["if"]["properties"].get("id"), dict)
            and rule["if"]["properties"]["id"].get("const") == "ACCEPT-012"
        )
        object_at(accept_012_rule, "then", "properties", "carrier")["const"] = {
            "statusAtM0": "available",
            "m0Expectation": "active_fail_closed",
            "upgradeTrigger": "Issue #18 proves the full fixture.",
        }

        self.assert_catalog_error(
            catalog,
            "schema.fixtures.items.allOf: must independently freeze ACCEPT-012 "
            "as the canonical unavailable/fail_closed fixture carrier",
            schema,
        )

    def test_future_carrier_drift_reports_the_whole_carrier_not_a_false_status(
        self,
    ) -> None:
        catalog = make_catalog()
        fixtures = object_list_at(catalog, "fixtures")
        carrier = object_at(fixtures[8], "carrier")
        carrier["upgradeTrigger"] = "Wrong owner."

        error = self.assert_catalog_error(
            catalog,
            "fixtures[8].carrier: must exactly preserve the canonical future "
            "carrier; Issue #16 activates only its M0 refusal",
        )
        self.assertNotIn(
            "fixtures[8].carrier.statusAtM0: must remain 'future'; Issue #16 "
            "activates only the M0 unavailable-capability refusal",
            error.errors,
        )

    def test_issue_15_evidence_and_future_continue_cannot_false_green(self) -> None:
        catalog = make_catalog()
        invariants = {
            invariant["id"]: invariant
            for invariant in object_list_at(catalog, "invariants")
        }
        revocation = invariants["REVOCATION-006"]
        postgres = object_at(revocation, "expectedEvidence")["postgres"]
        runtime = object_at(revocation, "expectedEvidence")["runtimeOrDelivery"]
        assert isinstance(postgres, list)
        assert isinstance(runtime, list)
        postgres.remove("PG-REVOCATION-006")
        postgres.remove("CACHE-002")
        runtime.remove("RUN-006")

        fixtures = {
            fixture["id"]: fixture for fixture in object_list_at(catalog, "fixtures")
        }
        accept_005 = fixtures["ACCEPT-005"]
        accept_005["carrier"] = {
            "statusAtM0": "available",
            "m0Expectation": "active_fail_closed",
            "upgradeTrigger": "Issue #15 activates Continue.",
        }

        with self.assertRaises(CatalogValidationError) as raised:
            validate_catalog(catalog, make_schema())

        self.assertIn(
            "invariants[5].expectedEvidence.postgres: must preserve canonical "
            "revocation evidence 'PG-REVOCATION-006' for REVOCATION-006",
            raised.exception.errors,
        )
        self.assertIn(
            "invariants[5].expectedEvidence.postgres: must preserve canonical "
            "revocation evidence 'CACHE-002' for REVOCATION-006",
            raised.exception.errors,
        )
        self.assertIn(
            "invariants[5].expectedEvidence.runtimeOrDelivery: must preserve "
            "absorbed derived case 'RUN-006' for REVOCATION-006",
            raised.exception.errors,
        )
        self.assertIn(
            "fixtures[4].carrier: must preserve ACCEPT-005 as the future Continue "
            "carrier; Issue #16 activates only its M0 refusal",
            raised.exception.errors,
        )

    def test_unknown_catalog_fields_are_rejected_at_every_boundary(self) -> None:
        catalog = make_catalog()
        catalog["unexpected"] = True
        invariants = object_list_at(catalog, "invariants")
        fixtures = object_list_at(catalog, "fixtures")
        object_at(invariants[0], "applicability")["unexpected"] = True
        object_at(fixtures[0], "expected", "io")["unexpected"] = 0

        error = self.assert_catalog_error(
            catalog,
            "catalog.unexpected: is not allowed",
        )
        self.assertIn(
            "invariants[0].applicability.unexpected: is not allowed",
            error.errors,
        )
        self.assertIn(
            "fixtures[0].expected.io.unexpected: is not allowed",
            error.errors,
        )

    def test_schema_must_freeze_nested_shapes_and_canonical_ids(self) -> None:
        schema = load_document(DEFAULT_SCHEMA_PATH)
        object_at(schema, "$defs", "invariant")["additionalProperties"] = True
        object_at(schema, "properties", "invariants")["type"] = "object"
        object_at(schema, "$defs", "invariant", "properties")["id"] = {"type": "string"}
        object_at(schema, "$defs", "parameterizedCase")["additionalProperties"] = True
        object_at(schema, "$defs", "parameterizedCase")["required"] = ["id"]

        error = self.assert_catalog_error(
            load_document(DEFAULT_CATALOG_PATH),
            "schema.properties.invariants.type: must be 'array'",
            schema,
        )
        self.assertIn(
            "schema.invariants.items.additionalProperties: must be false",
            error.errors,
        )
        self.assertIn(
            "schema.$defs.parameterizedCase.additionalProperties: must be false",
            error.errors,
        )
        self.assertIn(
            "schema.$defs.parameterizedCase.required: must declare 'mutation'",
            error.errors,
        )
        self.assertIn(
            "schema.invariants.items.properties.id.enum: must freeze the "
            "canonical ordered IDs",
            error.errors,
        )

    def test_malformed_schema_definitions_fail_with_catalog_error(self) -> None:
        schema = load_document(DEFAULT_SCHEMA_PATH)
        schema["$defs"] = "not-an-object"

        self.assert_catalog_error(
            load_document(DEFAULT_CATALOG_PATH),
            "schema.$defs: must be an object",
            schema,
        )

    def test_schema_hard_oracle_tuple_is_closed(self) -> None:
        schema = make_schema()
        hard_oracle_schema = object_at(schema, "properties", "hardOracles")
        hard_oracle_schema.pop("items", None)
        object_list_at(hard_oracle_schema, "prefixItems")[0].pop("required", None)

        error = self.assert_catalog_error(
            make_catalog(),
            "schema.properties.hardOracles.items: must be false",
            schema,
        )
        self.assertIn(
            "schema.properties.hardOracles.prefixItems[0].required: must be an array",
            error.errors,
        )

    def test_catalog_is_validated_against_schema_constraints(self) -> None:
        catalog = make_catalog()
        schema = make_schema()
        object_at(schema, "properties", "catalogVersion")["pattern"] = "^never$"

        self.assert_catalog_error(
            catalog,
            "catalog.catalogVersion: must match schema pattern '^never$'",
            schema,
        )

    def test_validate_files_rejects_missing_and_escaping_authority_documents(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = make_catalog()
            object_at(catalog, "authority")["documentRefs"] = [
                "docs/security/missing.md"
            ]
            for invariant in object_list_at(catalog, "invariants"):
                invariant["authorityRefs"] = ["docs/security/missing.md#oracle"]
            for fixture in object_list_at(catalog, "fixtures"):
                fixture["authorityRefs"] = ["#5", "docs/security/missing.md#oracle"]
            catalog_path = root / "eval/catalogs/security-invariants.yaml"
            schema_path = root / "eval/catalogs/security-catalog.schema.json"
            catalog_path.parent.mkdir(parents=True)
            catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
            schema_path.write_text(json.dumps(make_schema()), encoding="utf-8")

            with self.assertRaises(CatalogValidationError) as raised:
                validate_files(catalog_path, schema_path, repository_root=root)

            self.assertIn(
                "authority.documentRefs[0]: tracked document does not exist: "
                "'docs/security/missing.md'",
                raised.exception.errors,
            )

            object_at(catalog, "authority")["documentRefs"] = ["../outside.md"]
            for invariant in object_list_at(catalog, "invariants"):
                invariant["authorityRefs"] = ["../outside.md#oracle"]
            for fixture in object_list_at(catalog, "fixtures"):
                fixture["authorityRefs"] = ["#5", "../outside.md#oracle"]
            catalog_path.write_text(json.dumps(catalog), encoding="utf-8")

            with self.assertRaises(CatalogValidationError) as escaping:
                validate_files(catalog_path, schema_path, repository_root=root)

            self.assertIn(
                "authority.documentRefs[0]: must be a repository-relative path "
                "without '..'",
                escaping.exception.errors,
            )

    def test_validate_files_rejects_a_nonexistent_markdown_heading_anchor(self) -> None:
        repository_root = Path(__file__).resolve().parents[2]
        catalog = make_catalog()
        invariants = catalog["invariants"]
        assert isinstance(invariants, list)
        invariants[0]["authorityRefs"] = [
            "docs/security/context-engine-threat-model.md#not-a-real-heading"
        ]

        with tempfile.TemporaryDirectory() as directory:
            catalog_path = Path(directory, "security-invariants.yaml")
            schema_path = Path(directory, "security-catalog.schema.json")
            catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
            schema_path.write_text(json.dumps(make_schema()), encoding="utf-8")

            with self.assertRaises(CatalogValidationError) as raised:
                validate_files(
                    catalog_path,
                    schema_path,
                    repository_root=repository_root,
                )

        self.assertIn(
            (
                "invariants[0].authorityRefs[0]: Markdown heading anchor does "
                "not exist: "
                "'docs/security/context-engine-threat-model.md#not-a-real-heading'"
            ),
            raised.exception.errors,
        )

    def test_validate_files_rejects_untracked_and_ignored_authority_documents(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(
                ["git", "-C", str(root), "init", "--quiet"],
                check=True,
                capture_output=True,
                text=True,
            )
            docs = root / "docs"
            docs.mkdir()
            (docs / "untracked.md").write_text("# Real heading\n", encoding="utf-8")
            (docs / "ignored.md").write_text("# Real heading\n", encoding="utf-8")
            (root / ".gitignore").write_text("docs/ignored.md\n", encoding="utf-8")

            catalog = make_catalog()
            object_at(catalog, "authority")["documentRefs"] = [
                "docs/untracked.md",
                "docs/ignored.md",
            ]
            for invariant in object_list_at(catalog, "invariants"):
                invariant["authorityRefs"] = ["docs/untracked.md#real-heading"]
            for fixture in object_list_at(catalog, "fixtures"):
                fixture["authorityRefs"] = ["#5", "docs/ignored.md#real-heading"]

            catalog_path = root / "security-invariants.yaml"
            schema_path = root / "security-catalog.schema.json"
            catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
            schema_path.write_text(json.dumps(make_schema()), encoding="utf-8")

            with self.assertRaises(CatalogValidationError) as raised:
                validate_files(catalog_path, schema_path, repository_root=root)

        self.assertIn(
            (
                "authority.documentRefs[0]: must reference a Git-tracked file: "
                "'docs/untracked.md'"
            ),
            raised.exception.errors,
        )
        self.assertIn(
            (
                "authority.documentRefs[1]: must reference a Git-tracked file: "
                "'docs/ignored.md'"
            ),
            raised.exception.errors,
        )

    def test_validate_files_accepts_unicode_and_duplicate_heading_anchors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(
                ["git", "-C", str(root), "init", "--quiet"],
                check=True,
                capture_output=True,
                text=True,
            )
            docs = root / "docs"
            docs.mkdir()
            authority_document = docs / "authority.md"
            authority_document.write_text(
                "# 重复 标题\n\n# 重复 标题\n",
                encoding="utf-8",
            )
            subprocess.run(
                ["git", "-C", str(root), "add", "--", "docs/authority.md"],
                check=True,
                capture_output=True,
                text=True,
            )

            catalog = make_catalog()
            object_at(catalog, "authority")["documentRefs"] = ["docs/authority.md"]
            for invariant in object_list_at(catalog, "invariants"):
                invariant["authorityRefs"] = ["docs/authority.md#重复-标题"]
            for fixture in object_list_at(catalog, "fixtures"):
                fixture["authorityRefs"] = ["#5", "docs/authority.md#重复-标题-1"]

            catalog_path = root / "security-invariants.yaml"
            schema_path = root / "security-catalog.schema.json"
            catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
            schema_path.write_text(json.dumps(make_schema()), encoding="utf-8")

            report = validate_files(catalog_path, schema_path, repository_root=root)

        self.assertEqual(report.invariant_count, 15)
        self.assertEqual(report.fixture_count, 12)

    def test_cli_default_paths_are_anchored_to_repository(self) -> None:
        previous_cwd = Path.cwd()
        stdout = io.StringIO()
        stderr = io.StringIO()
        try:
            os.chdir(tempfile.gettempdir())
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = main([])
        finally:
            os.chdir(previous_cwd)

        self.assertEqual(exit_code, 0, stderr.getvalue())
        self.assertIn(
            "security catalog valid: 15 invariants, 12 fixtures", stdout.getvalue()
        )

    def test_tracked_catalog_and_schema_validate_together(self) -> None:
        repository_root = Path(__file__).resolve().parents[2]
        catalog_path = repository_root / "eval/catalogs/security-invariants.yaml"
        schema_path = repository_root / "eval/catalogs/security-catalog.schema.json"

        report = validate_files(
            catalog_path, schema_path, repository_root=repository_root
        )

        self.assertEqual(report.invariant_count, 15)
        self.assertEqual(report.fixture_count, 12)

    def test_tracked_catalog_freezes_issue_19_authority_and_bounded_scope(
        self,
    ) -> None:
        catalog = load_document(DEFAULT_CATALOG_PATH)
        authority = object_at(catalog, "authority")
        issue_refs = authority["issueRefs"]
        document_refs = authority["documentRefs"]
        reconciliation = authority["reconciliation"]
        assert isinstance(issue_refs, list)
        assert isinstance(document_refs, list)
        assert isinstance(reconciliation, str)

        self.assertEqual(catalog["catalogVersion"], "1.3.0")
        self.assertEqual(
            issue_refs[-11:],
            [
                "#15",
                "#16",
                "#17",
                "#18",
                "#19",
                "#48",
                "#63",
                "#65",
                "#66",
                "#64",
                "#67",
            ],
        )
        self.assertIn(
            "docs/decisions/0031-persist-authorized-context-run-lineage.md",
            document_refs,
        )
        for boundary in (
            "Issue #19 activates only the current Acquire authorized-only ContextRun",
            "DIGEST-019",
            "RUN-LINEAGE-019",
            "AUTHORIZED-RUN-019",
            "PG-TRACE-REDACTION-012",
            "Raw query retention",
            "full ContextPackage body retention",
            "general observability redaction",
        ):
            self.assertIn(boundary, reconciliation)

        invariants = {
            invariant["id"]: invariant
            for invariant in object_list_at(catalog, "invariants")
        }
        fixtures = {
            fixture["id"]: fixture for fixture in object_list_at(catalog, "fixtures")
        }
        for authority_owner in (
            invariants["TRACE-REDACTION-012"],
            fixtures["ACCEPT-011"],
        ):
            refs = authority_owner["authorityRefs"]
            assert isinstance(refs, list)
            self.assertIn("#19", refs)
            self.assertIn(
                "docs/decisions/0031-persist-authorized-context-run-lineage.md#decision",
                refs,
            )

    def test_tracked_catalog_freezes_later_carrier_and_source_acl_semantics(
        self,
    ) -> None:
        catalog = load_document(DEFAULT_CATALOG_PATH)
        fixtures = {
            fixture["id"]: fixture
            for fixture in catalog["fixtures"]
            if isinstance(fixture, dict)
        }

        accept_005 = fixtures["ACCEPT-005"]
        self.assertEqual(accept_005["carrier"]["statusAtM0"], "future")
        self.assertEqual(accept_005["carrier"]["m0Expectation"], "fail_closed")
        self.assertEqual(
            accept_005["expected"]["io"],
            {
                "providerCalls": 0,
                "indexCalls": 0,
                "modelCalls": 0,
                "actionCalls": 0,
            },
        )
        self.assertEqual(
            fixtures["ACCEPT-009"]["invariantRefs"],
            ["INDEX-NOT-AUTHORITY-005", "REVOCATION-006"],
        )

    def test_tracked_catalog_activates_issue_13_authorized_evidence_carriers(
        self,
    ) -> None:
        catalog = load_document(DEFAULT_CATALOG_PATH)
        fixtures = {
            fixture["id"]: fixture
            for fixture in catalog["fixtures"]
            if isinstance(fixture, dict)
        }

        for fixture_id in ("ACCEPT-001", "ACCEPT-003", "ACCEPT-004", "ACCEPT-006"):
            fixture = fixtures[fixture_id]
            carrier = fixture["carrier"]
            self.assertEqual(carrier["statusAtM0"], "available")
            self.assertEqual(carrier["m0Expectation"], "active_fail_closed")
            self.assertNotIn("Issue #13", carrier["upgradeTrigger"])

            package = fixture["expected"]["externalResponse"]["body"]["package"]
            self.assertEqual(package["coverage"], {"status": "sufficient"})
            self.assertEqual(len(package["blocks"]), 1)
            self.assertEqual(len(package["evidence"]), 1)
            block = package["blocks"][0]
            evidence = package["evidence"][0]
            self.assertEqual(block["evidenceRefs"], [evidence["evidenceRef"]])
            self.assertNotEqual(evidence["evidenceRef"], "candidate-authorized-a")
            self.assertEqual(evidence["projectedFields"], ["body"])
            self.assertNotIn("principalRef", evidence)
            self.assertEqual(package["budgetUsage"]["providerCalls"], 0)

        accept_006 = fixtures["ACCEPT-006"]
        self.assertEqual(
            accept_006["adversarialMutation"]["candidateRankOrders"],
            [
                ["candidate-authorized-a", "candidate-denied-a", "candidate-hostile-b"],
                ["candidate-authorized-a", "candidate-hostile-b", "candidate-denied-a"],
                ["candidate-denied-a", "candidate-authorized-a", "candidate-hostile-b"],
                ["candidate-denied-a", "candidate-hostile-b", "candidate-authorized-a"],
                ["candidate-hostile-b", "candidate-authorized-a", "candidate-denied-a"],
                ["candidate-hostile-b", "candidate-denied-a", "candidate-authorized-a"],
            ],
        )

        accept_011_package = fixtures["ACCEPT-011"]["expected"]["externalResponse"][
            "body"
        ]["package"]
        self.assertEqual(
            accept_011_package["coverage"],
            {"status": "empty", "reason": "no_authorized_evidence"},
        )
        self.assertEqual(accept_011_package["blocks"], [])
        self.assertEqual(accept_011_package["evidence"], [])

    def test_exact_package_examples_are_executable_v3_public_documents(self) -> None:
        catalog = load_document(DEFAULT_CATALOG_PATH)
        fixtures = {
            fixture["id"]: fixture for fixture in object_list_at(catalog, "fixtures")
        }

        for fixture_id in (
            "ACCEPT-001",
            "ACCEPT-003",
            "ACCEPT-004",
            "ACCEPT-006",
            "ACCEPT-011",
        ):
            package = object_at(
                fixtures[fixture_id],
                "expected",
                "externalResponse",
                "body",
                "package",
            )
            public_package = ContextPackageWire.model_validate(package)
            observed_package = public_package.model_dump(
                mode="json", exclude_none=False
            )
            if observed_package["coverage"].get("reason") is None:
                del observed_package["coverage"]["reason"]
            self.assertEqual(
                observed_package,
                package,
            )

        for fixture_id in ("ACCEPT-001", "ACCEPT-003", "ACCEPT-004", "ACCEPT-006"):
            evidence = object_list_at(
                fixtures[fixture_id],
                "expected",
                "externalResponse",
                "body",
                "package",
                "evidence",
            )[0]
            self.assertEqual(evidence["projectedFields"], ["body"])

        self.assertEqual(
            fixtures["ACCEPT-001"]["invariantRefs"],
            [
                "TENANT-OWNERSHIP-001",
                "TENANT-FK-002",
                "RLS-FAIL-CLOSED-003",
                "SCOPE-INTERSECTION-004",
                "INDEX-NOT-AUTHORITY-005",
                "NON-ENUMERATION-009",
            ],
        )
        self.assertEqual(
            fixtures["ACCEPT-004"]["invariantRefs"],
            [
                "SCOPE-INTERSECTION-004",
                "INDEX-NOT-AUTHORITY-005",
                "TRANSPORT-UNTRUSTED-008",
            ],
        )
        self.assertEqual(
            fixtures["ACCEPT-006"]["invariantRefs"],
            [
                "TENANT-OWNERSHIP-001",
                "TENANT-FK-002",
                "RLS-FAIL-CLOSED-003",
                "SCOPE-INTERSECTION-004",
                "INDEX-NOT-AUTHORITY-005",
                "TRACE-REDACTION-012",
            ],
        )

    def test_issue_13_evidence_rank_lineage_and_reference_mutations_fail(
        self,
    ) -> None:
        catalog = load_document(DEFAULT_CATALOG_PATH)
        schema = load_document(DEFAULT_SCHEMA_PATH)
        fixtures = {
            fixture["id"]: fixture for fixture in object_list_at(catalog, "fixtures")
        }
        accept_006 = fixtures["ACCEPT-006"]
        rank_orders = object_at(accept_006, "adversarialMutation")[
            "candidateRankOrders"
        ]
        assert isinstance(rank_orders, list)
        rank_orders.pop()
        block = object_list_at(
            accept_006,
            "expected",
            "externalResponse",
            "body",
            "package",
            "blocks",
        )[0]
        evidence = object_list_at(
            accept_006,
            "expected",
            "externalResponse",
            "body",
            "package",
            "evidence",
        )[0]
        block["evidenceRefs"] = ["ev_dangling"]
        block["blockId"] = "block_" + "b" * 64
        block["text"] = " "
        evidence["evidenceRef"] = "candidate-authorized-a"
        evidence["purpose"] = "admin.export"
        evidence["authorizationAsOf"] = "2026-07-21T09:31:00Z"
        evidence["decisionRef"] = "dec_0000000000000000000000000000000b"
        evidence["projectedFields"] = ["private_note"]
        del evidence["sourceAclEvidence"]
        object_at(accept_006, "expected", "packageOrError")[
            "unauthorizedEvidenceRefCount"
        ] = 1
        object_at(accept_006, "expected", "io")["providerCalls"] = 1
        object_at(
            accept_006,
            "expected",
            "externalResponse",
            "body",
            "package",
            "budgetUsage",
        )["tokens"] = 999

        with self.assertRaises(CatalogValidationError) as raised:
            validate_catalog(catalog, schema)

        self.assertIn(
            "fixtures[5].adversarialMutation.candidateRankOrders: must contain "
            "all six canonical authorized, denied, and cross-Organization "
            "CandidateRef rank orders",
            raised.exception.errors,
        )
        self.assertIn(
            "fixtures[5].expected.externalResponse.body.package.blocks[0]."
            "evidenceRefs: must resolve to exactly the one Evidence in this Package",
            raised.exception.errors,
        )
        self.assertIn(
            "fixtures[5].expected.externalResponse.body.package.evidence[0]."
            "evidenceRef: must be a request-scoped EvidenceRef distinct from "
            "CandidateRef",
            raised.exception.errors,
        )
        self.assertIn(
            "fixtures[5].expected.externalResponse.body.package.evidence[0]."
            "evidenceRef: must use the closed ev_<64 lowercase hex> format",
            raised.exception.errors,
        )
        self.assertIn(
            "fixtures[5].expected.externalResponse.body.package.blocks[0].blockId: "
            "must be derived from its exact EvidenceRef",
            raised.exception.errors,
        )
        self.assertIn(
            "fixtures[5].expected.externalResponse.body.package.blocks[0].text: "
            "must be nonblank authorized text",
            raised.exception.errors,
        )
        for field, package_field in (
            ("purpose", "purpose"),
            ("authorizationAsOf", "asOf"),
            ("decisionRef", "decisionRef"),
        ):
            self.assertIn(
                "fixtures[5].expected.externalResponse.body.package.evidence[0]."
                f"{field}: must equal enclosing Package {package_field}",
                raised.exception.errors,
            )
        self.assertIn(
            "fixtures[5].expected.externalResponse.body.package.evidence[0]."
            "projectedFields: must be exactly ['body'] for the canonical "
            "legacy-body Package example",
            raised.exception.errors,
        )
        self.assertIn(
            "fixtures[5].expected.externalResponse.body.package.budgetUsage.tokens: "
            "must equal the authorized block UTF-8 byte count",
            raised.exception.errors,
        )
        self.assertIn(
            "fixtures[5].expected.externalResponse.body.package.evidence[0]: "
            "must carry the closed complete public authorization lineage",
            raised.exception.errors,
        )
        self.assertIn(
            "fixtures[5].expected.packageOrError.unauthorizedEvidenceRefCount: "
            "must be the numeric constant 0 for authorized content",
            raised.exception.errors,
        )
        self.assertIn(
            "fixtures[5].expected.io.providerCalls: must be 0 for internal "
            "materialized PostgreSQL projection",
            raised.exception.errors,
        )

    def test_issue_13_schema_closes_public_evidence_and_coverage_union(self) -> None:
        catalog = load_document(DEFAULT_CATALOG_PATH)
        schema = load_document(DEFAULT_SCHEMA_PATH)
        fixtures = {
            fixture["id"]: fixture for fixture in object_list_at(catalog, "fixtures")
        }
        evidence = object_list_at(
            fixtures["ACCEPT-006"],
            "expected",
            "externalResponse",
            "body",
            "package",
            "evidence",
        )[0]
        evidence["principalRef"] = "principal-must-not-be-public"
        object_at(
            fixtures["ACCEPT-006"],
            "expected",
            "externalResponse",
            "body",
            "package",
            "coverage",
        )["reason"] = "no_authorized_evidence"

        with self.assertRaises(CatalogValidationError) as raised:
            validate_catalog(catalog, schema)

        self.assertIn(
            "catalog.fixtures[5].expected.externalResponse.body.package.evidence[0]."
            "principalRef: is not allowed by the schema",
            raised.exception.errors,
        )
        self.assertIn(
            "catalog.fixtures[5].expected.externalResponse.body.package.coverage."
            "reason: is forbidden by the schema",
            raised.exception.errors,
        )

    def test_v2_public_evidence_requires_a_closed_projected_field_list(self) -> None:
        catalog = load_document(DEFAULT_CATALOG_PATH)
        schema = load_document(DEFAULT_SCHEMA_PATH)
        fixtures = {
            fixture["id"]: fixture for fixture in object_list_at(catalog, "fixtures")
        }
        evidence = object_list_at(
            fixtures["ACCEPT-004"],
            "expected",
            "externalResponse",
            "body",
            "package",
            "evidence",
        )[0]
        del evidence["projectedFields"]

        with self.assertRaises(CatalogValidationError) as raised:
            validate_catalog(catalog, schema)

        self.assertIn(
            "catalog.fixtures[3].expected.externalResponse.body.package.evidence[0]."
            "projectedFields: is required by the schema",
            raised.exception.errors,
        )

    def test_tracked_catalog_matches_runtime_outcome_and_timing_authority(
        self,
    ) -> None:
        catalog = load_document(DEFAULT_CATALOG_PATH)
        fixtures = {
            fixture["id"]: fixture
            for fixture in catalog["fixtures"]
            if isinstance(fixture, dict)
        }

        for fixture_id, (body_kind, result_kind) in RUNTIME_OUTCOMES.items():
            fixture = fixtures[fixture_id]
            self.assertEqual(fixture["expected"]["externalResponse"]["status"], 200)
            self.assertEqual(
                fixture["expected"]["externalResponse"]["body"]["kind"],
                body_kind,
            )
            self.assertEqual(fixture["expected"]["packageOrError"]["kind"], result_kind)

        accept_011 = fixtures["ACCEPT-011"]
        self.assertNotIn("sameTimingBucket", accept_011["expected"]["externalResponse"])
        self.assertNotIn("timingBucket", accept_011["operation"]["comparisonFields"])
        self.assertIs(
            accept_011["expected"]["externalResponse"]["timingEqualityClaimed"],
            False,
        )
        response_body = accept_011["expected"]["externalResponse"]["body"]
        self.assertEqual(
            response_body["package"]["coverage"],
            {"status": "empty", "reason": "no_authorized_evidence"},
        )
        self.assertEqual(response_body["package"]["blocks"], [])
        self.assertEqual(response_body["package"]["evidence"], [])
        self.assertEqual(response_body["package"]["gaps"], [])
        self.assertEqual(
            response_body["package"]["budgetUsage"],
            {
                "tokens": 0,
                "providerCalls": 0,
                "costMicrounits": 0,
                "elapsedMs": 0,
            },
        )
        self.assertEqual(
            accept_011["expected"]["packageOrError"]["coverageReason"],
            "no_authorized_evidence",
        )
        self.assertEqual(
            accept_011["expected"]["packageOrError"]["coverageStatus"], "empty"
        )
        self.assertNotIn("Issue #14", accept_011["carrier"]["upgradeTrigger"])
        self.assertEqual(
            accept_011["operation"]["comparisonFields"],
            ["status", "body", "headers", "domainOutcome"],
        )
        self.assertEqual(
            accept_011["operation"]["normalizationAllowlist"],
            [
                "body.package.packageId",
                "body.package.decisionRef",
                "body.package.policySnapshotRef",
                "body.package.runRef",
                "body.package.asOf",
                "body.package.expiresAt",
                "body.package.packageDigest",
                "headers.X-Context-Request-Id",
            ],
        )
        self.assertIs(
            accept_011["expected"]["externalResponse"][
                "normalizedByteIdenticalAcrossProbes"
            ],
            True,
        )
        self.assertEqual(
            accept_011["expected"]["externalResponse"]["headers"],
            {
                "Content-Type": "application/json",
                "Cache-Control": "no-store",
                "X-Context-Request-Id": "normalized-request-id",
            },
        )
        self.assertEqual(
            accept_011["expected"]["packageOrError"],
            {
                "kind": "ContextPackage",
                "packageCount": 4,
                "coverageStatus": "empty",
                "coverageReason": "no_authorized_evidence",
                "deniedCountExposed": False,
                "existenceDetailCount": 0,
            },
        )
        self.assertEqual(
            accept_011["expected"]["io"],
            {
                "providerCalls": 0,
                "indexCalls": 1,
                "modelCalls": 0,
                "actionCalls": 0,
            },
        )
        for fixture_id in ("ACCEPT-005", "ACCEPT-009"):
            self.assertIs(
                fixtures[fixture_id]["expected"]["externalResponse"]["body"][
                    "retryable"
                ],
                False,
            )

    def test_tracked_catalog_matches_required_milestone_authority(self) -> None:
        catalog = load_document(DEFAULT_CATALOG_PATH)
        for invariant in catalog["invariants"]:
            invariant_id = invariant["id"]
            expected = CANONICAL_REQUIRED_MILESTONES[invariant_id]
            self.assertEqual(invariant["applicability"]["applicableFrom"], expected[0])
            self.assertEqual(invariant["requiredMilestones"], expected)

    def test_tracked_catalog_preserves_required_parameterized_security_cases(
        self,
    ) -> None:
        catalog = load_document(DEFAULT_CATALOG_PATH)
        fixtures = {
            fixture["id"]: fixture
            for fixture in catalog["fixtures"]
            if isinstance(fixture, dict)
        }
        expected_case_ids = {
            "ACCEPT-007": [
                "BODY-INJECTION",
                "DELIV-001",
                "DELIV-002",
                "DELIV-003",
                "DELIV-004",
            ],
            "ACCEPT-008": [
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
            ],
            "ACCEPT-009": list(ACL_PROOF_CASE_IDS),
            "ACCEPT-012": list(AUDIENCE_ACTION_CASE_IDS),
        }
        for fixture_id, expected_ids in expected_case_ids.items():
            cases = fixtures[fixture_id]["adversarialMutation"]["parameterizedCases"]
            self.assertEqual([case["id"] for case in cases], expected_ids)
            for case in cases:
                if fixture_id == "ACCEPT-007" and case["id"] == "BODY-INJECTION":
                    expected_status, expected_outcome = 422, "invalid_request"
                elif fixture_id == "ACCEPT-007":
                    expected_status, expected_outcome = 200, "request_not_available"
                elif fixture_id == "ACCEPT-008":
                    expected_status, expected_outcome = 404, "work_not_available"
                elif fixture_id == "ACCEPT-009":
                    expected_status, expected_outcome = 200, "request_not_available"
                else:
                    expected_status, expected_outcome = 404, "action_not_available"
                self.assertEqual(case["expectedStatus"], expected_status)
                self.assertEqual(case["expectedOutcome"], expected_outcome)
                self.assertEqual(case["expectedNewDurableEffects"], 0)
                self.assertEqual(case["expectedWrongOrganizationEffects"], 0)
                self.assertEqual(case["expectedContentWorkCalls"], 0)
                if fixture_id in {"ACCEPT-009", "ACCEPT-012"}:
                    self.assertTrue(case["activatedOracle"])
            self.assertEqual(
                fixtures[fixture_id]["expected"]["io"],
                {
                    "providerCalls": 0,
                    "indexCalls": 0,
                    "modelCalls": 0,
                    "actionCalls": 0,
                },
            )

        accept_011 = fixtures["ACCEPT-011"]
        self.assertEqual(
            accept_011["adversarialMutation"]["probes"],
            [
                "resource-cross-org",
                "resource-same-org-denied",
                "resource-missing",
            ],
        )
        self.assertEqual(accept_011["expected"]["packageOrError"]["packageCount"], 4)

    def test_parameterized_security_case_deletions_are_rejected(self) -> None:
        catalog = load_document(DEFAULT_CATALOG_PATH)
        schema = load_document(DEFAULT_SCHEMA_PATH)
        fixtures = object_list_at(catalog, "fixtures")
        transport_cases = object_list_at(
            fixtures[6], "adversarialMutation", "parameterizedCases"
        )
        worker_cases = object_list_at(
            fixtures[7], "adversarialMutation", "parameterizedCases"
        )
        acl_cases = object_list_at(
            fixtures[8], "adversarialMutation", "parameterizedCases"
        )
        audience_action_cases = object_list_at(
            fixtures[11], "adversarialMutation", "parameterizedCases"
        )
        nonenumeration_mutation = object_at(fixtures[10], "adversarialMutation")
        probes = nonenumeration_mutation["probes"]
        assert isinstance(probes, list)
        transport_cases.pop()
        worker_cases.pop(4)
        probes.pop(0)
        transport_cases[0]["expectedContentWorkCalls"] = 1
        transport_cases[1]["expectedStatus"] = 503
        worker_cases[0]["expectedOutcome"] = "organization_mismatch"
        acl_cases.pop(1)
        audience_action_cases[-1]["activatedOracle"] = ""

        with self.assertRaises(CatalogValidationError) as raised:
            validate_catalog(catalog, schema)

        self.assertIn(
            "fixtures[6].adversarialMutation.parameterizedCases: ids must be the "
            "canonical ordered set ['BODY-INJECTION', 'DELIV-001', 'DELIV-002', "
            "'DELIV-003', 'DELIV-004']",
            raised.exception.errors,
        )
        self.assertIn(
            "fixtures[6].adversarialMutation.parameterizedCases[1].expectedStatus: "
            "must be 200 for DELIV-001",
            raised.exception.errors,
        )
        self.assertIn(
            "fixtures[7].adversarialMutation.parameterizedCases[0].expectedOutcome: "
            "must be 'work_not_available' for LEASE-ORGANIZATION",
            raised.exception.errors,
        )
        self.assertIn(
            "fixtures[6].adversarialMutation.parameterizedCases[0]."
            "expectedContentWorkCalls: must be the numeric constant 0",
            raised.exception.errors,
        )
        self.assertTrue(
            any(
                error.startswith(
                    "fixtures[7].adversarialMutation.parameterizedCases: ids "
                    "must be the canonical ordered set"
                )
                for error in raised.exception.errors
            )
        )
        self.assertTrue(
            any(
                error.startswith(
                    "fixtures[8].adversarialMutation.parameterizedCases: ids "
                    "must be the canonical ordered set"
                )
                for error in raised.exception.errors
            )
        )
        self.assertIn(
            "fixtures[11].adversarialMutation.parameterizedCases[13]."
            "activatedOracle: must be a non-empty string",
            raised.exception.errors,
        )
        self.assertIn(
            "fixtures[10].adversarialMutation.probes: must be the canonical "
            "ordered probe set ['resource-cross-org', "
            "'resource-same-org-denied', 'resource-missing']",
            raised.exception.errors,
        )

    def test_absorbed_evidence_and_case_outcomes_cannot_be_removed(self) -> None:
        catalog = load_document(DEFAULT_CATALOG_PATH)
        schema = load_document(DEFAULT_SCHEMA_PATH)
        invariants = {
            invariant["id"]: invariant
            for invariant in catalog["invariants"]
            if isinstance(invariant, dict)
        }
        fixtures = object_list_at(catalog, "fixtures")
        revocation_evidence = invariants["REVOCATION-006"]["expectedEvidence"][
            "runtimeOrDelivery"
        ]
        assert isinstance(revocation_evidence, list)
        revocation_evidence.remove("PROV-019")
        transport_evidence = invariants["TRANSPORT-UNTRUSTED-008"]["expectedEvidence"][
            "runtimeOrDelivery"
        ]
        assert isinstance(transport_evidence, list)
        self.assertIn("DELIV-004", transport_evidence)
        transport_evidence.remove("DELIV-004")
        acl_cases = object_list_at(
            fixtures[8], "adversarialMutation", "parameterizedCases"
        )
        del acl_cases[0]["expectedStatus"]
        acl_cases[1]["mutation"] = None
        acl_cases[2]["activatedOracle"] = "pass"

        with self.assertRaises(CatalogValidationError) as raised:
            validate_catalog(catalog, schema)

        self.assertIn(
            "invariants[5].expectedEvidence.runtimeOrDelivery: must preserve "
            "absorbed derived case 'PROV-019' for REVOCATION-006",
            raised.exception.errors,
        )
        self.assertIn(
            "invariants[7].expectedEvidence.runtimeOrDelivery: must preserve "
            "absorbed derived case 'DELIV-004' for TRANSPORT-UNTRUSTED-008",
            raised.exception.errors,
        )
        self.assertIn(
            "fixtures[8].adversarialMutation.parameterizedCases[0]."
            "expectedStatus: must be 200 for PROV-013",
            raised.exception.errors,
        )
        self.assertTrue(
            any(
                error.endswith(".expectedStatus: is required by the schema")
                for error in raised.exception.errors
            )
        )
        self.assertIn(
            "fixtures[8].adversarialMutation.parameterizedCases[1].mutation: "
            "must be a non-empty string or a non-negative integer",
            raised.exception.errors,
        )
        self.assertIn(
            "fixtures[8].adversarialMutation.parameterizedCases[2]."
            "activatedOracle: must preserve the canonical activated oracle "
            "for PROV-015",
            raised.exception.errors,
        )

    def test_runtime_outcome_and_milestone_drift_are_rejected(self) -> None:
        catalog = load_document(DEFAULT_CATALOG_PATH)
        schema = load_document(DEFAULT_SCHEMA_PATH)
        fixtures = object_list_at(catalog, "fixtures")
        invariants = object_list_at(catalog, "invariants")
        object_at(fixtures[0], "expected", "externalResponse")["status"] = 404
        object_at(invariants[5], "applicability")["applicableFrom"] = "M0"
        invariants[5]["requiredMilestones"] = ["M0", "M1"]

        with self.assertRaises(CatalogValidationError) as raised:
            validate_catalog(catalog, schema)

        self.assertIn(
            "fixtures[0].expected.externalResponse.status: must be 200 for "
            "the canonical Runtime outcome",
            raised.exception.errors,
        )

    def test_non_runtime_fail_closed_outcome_drift_is_rejected(self) -> None:
        catalog = load_document(DEFAULT_CATALOG_PATH)
        schema = load_document(DEFAULT_SCHEMA_PATH)
        fixtures = {
            fixture["id"]: fixture for fixture in object_list_at(catalog, "fixtures")
        }
        object_at(fixtures["ACCEPT-007"], "expected")["externalResponse"] = {
            "status": 200,
            "code": "resolved",
        }
        object_at(fixtures["ACCEPT-008"], "expected")["packageOrError"] = {
            "kind": "ContextPackage",
            "newReceiptCreated": True,
        }
        object_at(fixtures["ACCEPT-012"], "expected")["externalResponse"] = {
            "status": 200,
            "body": {"kind": "resolved"},
        }

        with self.assertRaises(CatalogValidationError) as raised:
            validate_catalog(catalog, schema)

        self.assertIn(
            "fixtures[6].expected.externalResponse: must preserve the canonical "
            "fail-closed outcome for ACCEPT-007",
            raised.exception.errors,
        )
        self.assertIn(
            "fixtures[7].expected.packageOrError: must preserve the canonical "
            "fail-closed outcome for ACCEPT-008",
            raised.exception.errors,
        )
        self.assertIn(
            "fixtures[11].expected.externalResponse: must preserve the canonical "
            "fail-closed outcome for ACCEPT-012",
            raised.exception.errors,
        )

    def test_resolved_empty_outcome_shape_drift_is_rejected(self) -> None:
        catalog = load_document(DEFAULT_CATALOG_PATH)
        schema = load_document(DEFAULT_SCHEMA_PATH)
        fixtures = object_list_at(catalog, "fixtures")
        body = object_at(fixtures[10], "expected", "externalResponse", "body")
        package = object_at(body, "package")
        coverage = object_at(package, "coverage")
        coverage["reason"] = "source_unavailable"
        package["gaps"] = [{"category": "capability_unsupported"}]

        with self.assertRaises(CatalogValidationError) as raised:
            validate_catalog(catalog, schema)

        self.assertIn(
            "fixtures[10].expected.externalResponse.body.package.coverage.reason: "
            "must be 'no_authorized_evidence' for a hidden or missing Acquire",
            raised.exception.errors,
        )
        self.assertIn(
            "fixtures[10].expected.externalResponse.body.package.gaps: must be empty "
            "because no_authorized_evidence is coverage, not a Provider gap",
            raised.exception.errors,
        )

    def test_non_enumeration_comparison_contract_drift_is_rejected(self) -> None:
        catalog = load_document(DEFAULT_CATALOG_PATH)
        schema = load_document(DEFAULT_SCHEMA_PATH)
        fixture = object_list_at(catalog, "fixtures")[10]
        operation = object_at(fixture, "operation")
        mutation = object_at(fixture, "adversarialMutation")
        expected = object_at(fixture, "expected")
        external_response = object_at(expected, "externalResponse")
        package_summary = object_at(expected, "packageOrError")
        io = object_at(expected, "io")
        evidence = object_at(expected, "evidence")
        effects = object_at(expected, "businessEffects")
        body = object_at(external_response, "body")
        package = object_at(body, "package")

        operation["comparisonFields"] = ["status", "body"]
        operation["normalizationAllowlist"] = [
            "body.package.organizationRef",
            "body.package.resourceRef",
        ]
        mutation["order"] = ["missing", "same_organization_denied"]
        external_response["normalizedByteIdenticalAcrossProbes"] = False
        external_response["headers"] = {
            "Content-Type": "application/json",
            "Cache-Control": "private",
            "X-Context-Request-Id": "normalized-request-id",
        }
        package_summary["deniedCountExposed"] = True
        package_summary["existenceDetailCount"] = 1
        io["indexCalls"] = 0
        evidence["outboundBytes"] = 1
        effects["mutationEffectCount"] = 1
        object_at(package, "budgetUsage")["elapsedMs"] = 1

        with self.assertRaises(CatalogValidationError) as raised:
            validate_catalog(catalog, schema)

        errors = raised.exception.errors
        self.assertTrue(
            any(
                "comparisonFields" in error and "canonical ordered" in error
                for error in errors
            )
        )
        self.assertTrue(
            any(
                "normalizationAllowlist" in error and "canonical ordered" in error
                for error in errors
            )
        )
        self.assertTrue(any("adversarialMutation.order" in error for error in errors))
        self.assertTrue(
            any("normalizedByteIdenticalAcrossProbes" in error for error in errors)
        )
        self.assertTrue(any("externalResponse.headers" in error for error in errors))
        self.assertTrue(any("packageOrError" in error for error in errors))
        self.assertTrue(any("expected.io" in error for error in errors))
        self.assertTrue(
            any("externalResponse.body.package" in error for error in errors)
        )
        self.assertTrue(any("expected.evidence" in error for error in errors))
        self.assertTrue(any("expected.businessEffects" in error for error in errors))

    def test_public_package_digest_fixture_drift_is_rejected(self) -> None:
        catalog = load_document(DEFAULT_CATALOG_PATH)
        schema = load_document(DEFAULT_SCHEMA_PATH)
        package = object_at(
            object_list_at(catalog, "fixtures")[10],
            "expected",
            "externalResponse",
            "body",
            "package",
        )
        package["ttlSeconds"] = 31

        with self.assertRaises(CatalogValidationError) as raised:
            validate_catalog(catalog, schema)

        self.assertIn(
            "fixtures[10].expected.externalResponse.body.package.packageDigest: "
            "must equal SHA-256 over the RFC 8785-canonicalized UTF-8 public "
            "Package document with packageDigest omitted",
            raised.exception.errors,
        )

    def test_milestone_drift_is_rejected(self) -> None:
        catalog = load_document(DEFAULT_CATALOG_PATH)
        schema = load_document(DEFAULT_SCHEMA_PATH)
        invariants = object_list_at(catalog, "invariants")
        object_at(invariants[5], "applicability")["applicableFrom"] = "M0"
        invariants[5]["requiredMilestones"] = ["M0", "M1"]

        with self.assertRaises(CatalogValidationError) as raised:
            validate_catalog(catalog, schema)

        self.assertIn(
            "invariants[5].applicability.applicableFrom: must be 'M1' for "
            "REVOCATION-006",
            raised.exception.errors,
        )
        self.assertIn(
            "invariants[5].requiredMilestones: must be the canonical sequence "
            "['M1', 'M2'] for REVOCATION-006",
            raised.exception.errors,
        )

    def test_cli_reads_json_compatible_yaml_and_prints_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog_path = Path(directory, "security-invariants.yaml")
            schema_path = Path(directory, "security-catalog.schema.json")
            catalog_path.write_text(json.dumps(make_catalog()), encoding="utf-8")
            schema_path.write_text(json.dumps(make_schema()), encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()

            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = main([str(catalog_path), "--schema", str(schema_path)])

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertIn(
            "security catalog valid: 15 invariants, 12 fixtures\n", stdout.getvalue()
        )
        self.assertIn("  ACCEPT-012: TRACE-REDACTION-012\n", stdout.getvalue())

    def test_duplicate_invariant_id_is_rejected(self) -> None:
        catalog = make_catalog()
        invariants = catalog["invariants"]
        assert isinstance(invariants, list)
        invariants[1]["id"] = invariants[0]["id"]

        self.assert_catalog_error(
            catalog,
            "invariants: duplicate id 'TENANT-OWNERSHIP-001'",
        )

    def test_regex_valid_but_noncanonical_invariant_id_is_rejected(self) -> None:
        catalog = make_catalog()
        invariants = catalog["invariants"]
        assert isinstance(invariants, list)
        invariants[12]["id"] = "CACHE-SCOPE-013"

        error = self.assert_catalog_error(
            catalog,
            (
                "invariants: ids must be the canonical ordered set: "
                + ", ".join(CANONICAL_INVARIANT_IDS)
            ),
        )
        self.assertNotIn(
            "invariants[12].id: must match ^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-[0-9]{3}$",
            error.errors,
        )

    def test_unknown_fixture_invariant_reference_is_rejected(self) -> None:
        catalog = make_catalog()
        fixtures = catalog["fixtures"]
        assert isinstance(fixtures, list)
        fixtures[0]["invariantRefs"] = ["UNKNOWN-999"]

        self.assert_catalog_error(
            catalog,
            "fixtures[0].invariantRefs[0]: unknown invariant 'UNKNOWN-999'",
        )

    def test_duplicate_fixture_invariant_reference_is_rejected(self) -> None:
        catalog = make_catalog()
        fixtures = catalog["fixtures"]
        assert isinstance(fixtures, list)
        invariant_ref = fixtures[0]["invariantRefs"][0]
        fixtures[0]["invariantRefs"] = [invariant_ref, invariant_ref]

        self.assert_catalog_error(
            catalog,
            "fixtures[0].invariantRefs: must contain unique strings",
        )

    def test_missing_fixture_field_is_rejected(self) -> None:
        catalog = make_catalog()
        fixtures = catalog["fixtures"]
        assert isinstance(fixtures, list)
        del fixtures[0]["operation"]

        error = self.assert_catalog_error(
            catalog,
            "fixtures[0].operation: is required",
        )
        self.assertIn("fixtures[0].operation: must be an object", error.errors)

    def test_unavailable_carrier_must_fail_closed_with_zero_io(self) -> None:
        catalog = make_catalog()
        fixtures = catalog["fixtures"]
        assert isinstance(fixtures, list)
        fixture = fixtures[0]
        fixture["decisionStatus"] = "future_case"
        fixture["carrier"] = {
            "statusAtM0": "unavailable",
            "m0Expectation": "skipped",
            "upgradeTrigger": "Activate the trusted delivery carrier.",
        }
        fixture["expected"]["io"]["modelCalls"] = 1

        error = self.assert_catalog_error(
            catalog,
            (
                "fixtures[0].carrier.m0Expectation: must be 'fail_closed' "
                "when statusAtM0 is 'unavailable'"
            ),
        )
        self.assertIn(
            "fixtures[0].expected.io.modelCalls: must be 0 for an unavailable "
            "or future carrier",
            error.errors,
        )

    def test_unavailable_carrier_requires_an_upgrade_trigger(self) -> None:
        catalog = make_catalog()
        fixtures = catalog["fixtures"]
        assert isinstance(fixtures, list)
        fixtures[0]["decisionStatus"] = "future_case"
        fixtures[0]["carrier"] = {
            "statusAtM0": "future",
            "m0Expectation": "fail_closed",
            "upgradeTrigger": "",
        }

        self.assert_catalog_error(
            catalog,
            "fixtures[0].carrier.upgradeTrigger: must be a non-empty string",
        )

    def test_skipped_or_deferred_fixture_status_is_rejected(self) -> None:
        catalog = make_catalog()
        fixtures = catalog["fixtures"]
        assert isinstance(fixtures, list)
        fixtures[0]["decisionStatus"] = "skipped"

        self.assert_catalog_error(
            catalog,
            (
                "fixtures[0].decisionStatus: must be accepted or future_case; "
                "skipped and deferred are forbidden"
            ),
        )

    def test_hard_veto_metrics_must_be_zero_for_every_fixture(self) -> None:
        catalog = make_catalog()
        fixtures = catalog["fixtures"]
        assert isinstance(fixtures, list)
        fixtures[0]["expected"]["evidence"]["unauthorizedEvidenceCount"] = 1
        fixtures[0]["expected"]["evidence"]["missingContextFallbackCount"] = 1
        fixtures[0]["expected"]["businessEffects"]["wrongOrganizationEffectCount"] = 1

        error = self.assert_catalog_error(
            catalog,
            (
                "fixtures[0].expected.evidence.unauthorizedEvidenceCount: "
                "must be 0 for every acceptance fixture"
            ),
        )
        self.assertIn(
            (
                "fixtures[0].expected.evidence.missingContextFallbackCount: "
                "must be 0 for every acceptance fixture"
            ),
            error.errors,
        )
        self.assertIn(
            (
                "fixtures[0].expected.businessEffects.wrongOrganizationEffectCount: "
                "must be 0 for every acceptance fixture"
            ),
            error.errors,
        )

    def test_schema_count_drift_is_rejected(self) -> None:
        schema = make_schema()
        object_at(schema, "properties", "invariants")["maxItems"] = 16

        self.assert_catalog_error(
            make_catalog(),
            "schema.properties.invariants.maxItems: must be 15",
            schema,
        )

    def test_cli_reports_validation_errors_to_stderr(self) -> None:
        catalog = make_catalog()
        object_list_at(catalog, "hardOracles")[0]["veto"] = False
        with tempfile.TemporaryDirectory() as directory:
            catalog_path = Path(directory, "security-invariants.yaml")
            schema_path = Path(directory, "security-catalog.schema.json")
            catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
            schema_path.write_text(json.dumps(make_schema()), encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()

            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = main([str(catalog_path), "--schema", str(schema_path)])

        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("security catalog invalid:\n", stderr.getvalue())
        self.assertIn("hardOracles[0].veto: must be true", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
