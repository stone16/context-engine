from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import cast

from scripts.validate_security_catalog import (
    ACL_PROOF_CASE_IDS,
    AUDIENCE_ACTION_CASE_IDS,
    CANONICAL_INVARIANT_IDS,
    DEFAULT_CATALOG_PATH,
    DEFAULT_SCHEMA_PATH,
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
    "ACCEPT-005": ("request_not_available", "request_not_available"),
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
                    "property": [f"PROP-{number:03d}"],
                    "postgres": [f"PG-{number:03d}"],
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
            if fixture_id in {"ACCEPT-001", "ACCEPT-011"}:
                body["package"] = {
                    "packageId": "opaque-package-ref",
                    "packageDigest": "sha256-package-digest",
                    "purpose": "context.answer",
                    "audienceDigest": "audience-bound-digest",
                    "policyEpoch": "current-policy-epoch",
                    "decisionRef": "opaque-decision-ref",
                    "releaseManifestRef": "active-release-manifest-ref",
                    "retentionPolicyRef": "active-retention-policy-ref",
                    "asOf": "current-rfc3339-time",
                    "expiresAt": "bounded-rfc3339-expiry",
                    "tokenizerRef": "active-tokenizer-ref",
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
                }
                body["egressGrant"] = "opaque-matching-egress-grant"
                package_or_error["coverageStatus"] = "empty"
                package_or_error["coverageReason"] = "no_authorized_evidence"
            if fixture_id in {"ACCEPT-005", "ACCEPT-009"}:
                body["retryable"] = False
            if fixture_id == "ACCEPT-011":
                external_response["timingEqualityClaimed"] = False
                operation["comparisonFields"] = ["status", "body", "headers"]
                operation["normalizationAllowlist"] = [
                    "body.package.packageId",
                    "body.package.packageDigest",
                    "body.package.decisionRef",
                    "body.package.asOf",
                    "body.package.expiresAt",
                    "body.package.budgetUsage.elapsedMs",
                    "body.egressGrant",
                    "headers.X-Context-Request-Id",
                ]
                adversarial_mutation["probes"] = [
                    "resource-cross-org",
                    "resource-same-org-denied",
                    "resource-missing",
                ]
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
        if fixture_id in {"ACCEPT-009", "ACCEPT-012"}:
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
                    "activatedOracle": (
                        "The activated carrier preserves the named "
                        "fail-closed contract."
                    ),
                }
                for case_id in derived_case_ids
            ]
        fixtures.append(
            {
                "id": fixture_id,
                "title": f"Acceptance fixture {number}",
                "decisionStatus": "accepted",
                "carrier": {
                    "statusAtM0": "available",
                    "m0Expectation": "active_fail_closed",
                    "upgradeTrigger": "Upgrade when the complete carrier is activated.",
                },
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
                        "indexCalls": 0,
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
        "catalogVersion": "1.0.0",
        "authority": {
            "issueRefs": ["#5"],
            "documentRefs": ["docs/security/context-engine-threat-model.md"],
            "reconciliation": "Accepted decisions take precedence.",
        },
        "hardOracles": [
            {"name": name, "requiredValue": 0, "veto": True}
            for name in HARD_ORACLE_NAMES
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
            "invariants",
            "fixtures",
        ],
        "properties": {
            "catalogVersion": {"const": "1.0.0"},
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
            "catalogVersion: must be the supported version '1.0.0'",
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
        schema = make_schema()
        object_at(schema, "$defs", "invariant")["additionalProperties"] = True
        object_at(schema, "properties", "invariants")["type"] = "object"
        object_at(schema, "$defs", "invariant", "properties")["id"] = {"type": "string"}

        error = self.assert_catalog_error(
            make_catalog(),
            "schema.properties.invariants.type: must be 'array'",
            schema,
        )
        self.assertIn(
            "schema.invariants.items.additionalProperties: must be false",
            error.errors,
        )
        self.assertIn(
            "schema.invariants.items.properties.id.enum: must freeze the "
            "canonical ordered IDs",
            error.errors,
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
        repository_root = Path(__file__).resolve().parents[2]
        if not (repository_root / "eval/catalogs/security-invariants.yaml").is_file():
            self.skipTest("tracked catalog is landing in the parallel TDD task")

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
        if not schema_path.is_file():
            self.skipTest("tracked schema is landing in the parallel TDD task")

        report = validate_files(
            catalog_path, schema_path, repository_root=repository_root
        )

        self.assertEqual(report.invariant_count, 15)
        self.assertEqual(report.fixture_count, 12)

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
        for fixture_id in ("ACCEPT-001", "ACCEPT-011"):
            fixture = fixtures[fixture_id]
            response_body = fixture["expected"]["externalResponse"]["body"]
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
            self.assertTrue(response_body["egressGrant"])
            self.assertEqual(
                fixture["expected"]["packageOrError"]["coverageReason"],
                "no_authorized_evidence",
            )
            self.assertEqual(
                fixture["expected"]["packageOrError"]["coverageStatus"], "empty"
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
        self.assertEqual(accept_011["expected"]["packageOrError"]["packageCount"], 3)

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
        acl_cases = object_list_at(
            fixtures[8], "adversarialMutation", "parameterizedCases"
        )
        del acl_cases[0]["expectedStatus"]

        with self.assertRaises(CatalogValidationError) as raised:
            validate_catalog(catalog, schema)

        self.assertIn(
            "invariants[5].expectedEvidence.runtimeOrDelivery: must preserve "
            "absorbed derived case 'PROV-019' for REVOCATION-006",
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

    def test_resolved_empty_outcome_shape_drift_is_rejected(self) -> None:
        catalog = load_document(DEFAULT_CATALOG_PATH)
        schema = load_document(DEFAULT_SCHEMA_PATH)
        fixtures = object_list_at(catalog, "fixtures")
        body = object_at(fixtures[0], "expected", "externalResponse", "body")
        package = object_at(body, "package")
        coverage = object_at(package, "coverage")
        body.pop("egressGrant")
        coverage["reason"] = "source_unavailable"
        package["gaps"] = [{"category": "capability_unsupported"}]

        with self.assertRaises(CatalogValidationError) as raised:
            validate_catalog(catalog, schema)

        self.assertIn(
            "fixtures[0].expected.externalResponse.body.egressGrant: must be a "
            "non-empty string",
            raised.exception.errors,
        )
        self.assertIn(
            "fixtures[0].expected.externalResponse.body.package.coverage.reason: "
            "must be 'no_authorized_evidence' for a hidden or missing Acquire",
            raised.exception.errors,
        )
        self.assertIn(
            "fixtures[0].expected.externalResponse.body.package.gaps: must be empty "
            "because no_authorized_evidence is coverage, not a Provider gap",
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
