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
    CANONICAL_INVARIANT_IDS,
    DEFAULT_CATALOG_PATH,
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


def object_at(mapping: dict[str, object], *keys: str) -> dict[str, object]:
    """Return a nested object while keeping malformed test data type-safe."""
    current: object = mapping
    for key in keys:
        assert isinstance(current, dict)
        current = current[key]
    assert isinstance(current, dict)
    return cast(dict[str, object], current)


def object_list_at(
    mapping: dict[str, object], *keys: str
) -> list[dict[str, object]]:
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
                    "applicableFrom": "M0",
                    "rationale": None,
                },
                "capabilityRef": "tenant-isolation",
                "requiredMilestones": ["M0"],
                "evidenceStatus": "accepted",
                "expectedEvidence": {
                    "property": [f"PROP-{number:03d}"],
                    "postgres": [f"PG-{number:03d}"],
                    "runtimeOrDelivery": [f"RUNTIME-{number:03d}"],
                },
                "authorityRefs": [
                    "docs/security/context-engine-threat-model.md#5-hard-oracles"
                ],
            }
        )

    fixtures = []
    for number in range(1, 13):
        fixtures.append(
            {
                "id": f"ACCEPT-{number:03d}",
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
                "adversarialMutation": {"kind": "cross_organization_reference"},
                "operation": {"kind": "resolve"},
                "expected": {
                    "externalResponse": {"status": 404, "body": "generic"},
                    "packageOrError": {"kind": "error", "code": "not_found"},
                    "evidence": {
                        "unauthorizedEvidenceCount": 0,
                        "unauthorizedContentBytes": 0,
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
                                    "outboundBytes",
                                ],
                                "properties": {
                                    "unauthorizedEvidenceCount": {},
                                    "unauthorizedContentBytes": {},
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
        object_at(schema, "$defs", "invariant", "properties")["id"] = {
            "type": "string"
        }

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
            object_at(catalog, "authority")["documentRefs"] = [
                "docs/authority.md"
            ]
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
