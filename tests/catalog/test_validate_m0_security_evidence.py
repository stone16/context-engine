from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from scripts.validate_security_catalog import (
    CANONICAL_FIXTURE_IDS,
    CANONICAL_INVARIANT_IDS,
    DEFAULT_CATALOG_PATH,
    DEFAULT_EXECUTION_REGISTRY_PATH,
    DEFAULT_EXECUTION_REGISTRY_SCHEMA_PATH,
    DEFAULT_SCHEMA_PATH,
    HARD_ORACLES,
    CatalogValidationError,
    load_document,
    validate_execution_registry,
)


def _documents() -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    return (
        load_document(DEFAULT_EXECUTION_REGISTRY_PATH),
        load_document(DEFAULT_EXECUTION_REGISTRY_SCHEMA_PATH),
        load_document(DEFAULT_CATALOG_PATH),
    )


def _validation_error(registry: dict[str, Any]) -> CatalogValidationError:
    _, schema, catalog = _documents()
    with pytest.raises(CatalogValidationError) as raised:
        validate_execution_registry(
            registry,
            schema,
            catalog,
            repository_root=Path(__file__).resolve().parents[2],
        )
    return raised.value


def _temporary_marker_repository(
    tmp_path: Path,
    *,
    test_source: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], Path]:
    registry, schema, catalog = _documents()
    repository = tmp_path / "repository"
    test_file = repository / "tests/unit/test_marker_contract.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    evidence_by_selector: dict[str, list[dict[str, str]]] = {}
    for evidence in registry["evidence"]:
        evidence_by_selector.setdefault(evidence["selector"], []).append(evidence)
    generated_sources = ["import pytest\n\n", test_source]
    for index, entries in enumerate(evidence_by_selector.values()):
        function_ref = "test_registered" if index == 0 else f"test_registered_{index}"
        selector = f"tests/unit/test_marker_contract.py::{function_ref}"
        for entry in entries:
            entry["selector"] = selector
        if index == 0:
            continue
        generated_sources.extend(
            f'@pytest.mark.security_evidence(id="{entry["id"]}", '
            f'layer="{entry["layer"]}")\n'
            for entry in entries
        )
        generated_sources.append(f"def {function_ref}() -> None:\n    pass\n\n")
    test_file.write_text("".join(generated_sources), encoding="utf-8")
    return registry, schema, catalog, repository


def test_tracked_execution_registry_covers_every_canonical_authority() -> None:
    registry, schema, catalog = _documents()

    report = validate_execution_registry(
        registry,
        schema,
        catalog,
        repository_root=Path(__file__).resolve().parents[2],
    )

    assert report.invariant_count == 15
    assert report.fixture_count == 12
    assert report.evidence_count >= 27
    invariant_refs = tuple(
        mapping["invariantRef"] for mapping in registry["invariantMappings"]
    )
    assert invariant_refs == CANONICAL_INVARIANT_IDS
    assert tuple(mapping["fixtureRef"] for mapping in registry["fixtureMappings"]) == (
        CANONICAL_FIXTURE_IDS
    )


def test_planned_catalog_evidence_is_separate_from_executable_refs() -> None:
    registry, _, catalog = _documents()
    release = next(
        invariant
        for invariant in catalog["invariants"]
        if invariant["id"] == "RELEASE-OWNER-019"
    )
    mapping = next(
        item
        for item in registry["invariantMappings"]
        if item["invariantRef"] == "RELEASE-OWNER-019"
    )

    assert release["expectedEvidence"] == {
        "property": ["PROP-RELEASE-OWNER-019"],
        "postgres": ["PG-RELEASE-OWNER-019", "LEARN-006", "LEARN-007"],
        "runtimeOrDelivery": ["LEARN-004", "LEARN-008", "LEARN-009"],
    }
    assert mapping["evidenceRefs"] == {
        "property": ["PROP-RELEASE-OWNER-019"],
        "postgres": ["PG-RELEASE-OWNER-019"],
        "runtime": ["RUNTIME-RELEASE-OWNER-019"],
    }


def test_m0_registry_uses_activated_egress_and_honest_learning_evidence() -> None:
    registry, _, _ = _documents()
    evidence = {entry["id"]: entry["selector"] for entry in registry["evidence"]}

    unavailable_carrier = (
        "tests/integration/test_m0_unavailable_security_carriers.py::"
        "test_unavailable_citation_and_real_provider_carriers_fail_closed"
    )
    assert evidence["PG-CITATION-AUTH-010"] == unavailable_carrier
    assert evidence["PROP-EGRESS-011"] == (
        "tests/unit/test_egress_grant.py::"
        "test_each_egress_binding_mutation_and_cross_kind_emits_zero_bytes_effects"
    )
    assert evidence["PG-EGRESS-011"] == (
        "tests/integration/test_egress_grant.py::"
        "test_digest_only_grant_is_atomic_one_shot_and_audited"
    )
    assert evidence["RUNTIME-EGRESS-011"] == (
        "tests/integration/test_z_egress_grant_file.py::"
        "test_file_http_package_redeems_exact_model_grant_before_gateway_bytes"
    )
    egress_mapping = next(
        mapping
        for mapping in registry["invariantMappings"]
        if mapping["invariantRef"] == "EGRESS-011"
    )
    assert egress_mapping["evidenceRefs"]["postgres"] == [
        "PG-EGRESS-011",
        "PG-ACTION-PERFORM-068",
    ]
    assert evidence["PROP-CROSS-ORG-LEARN-015"] == (
        "tests/unit/test_m0_learning_isolation.py::"
        "test_m0_learning_artifact_contract_has_no_cross_organization_carrier"
    )
    assert evidence["PG-CROSS-ORG-LEARN-015"] == (
        "tests/integration/test_m0_unavailable_security_carriers.py::"
        "test_learning_persistence_is_organization_bound"
    )
    assert evidence["RUNTIME-CROSS-ORG-LEARN-015"] == (
        "tests/unit/test_m0_learning_isolation.py::"
        "test_context_learning_rejects_cross_organization_candidate_lineage"
    )
    assert evidence["PG-FIELD-PROJECTION-RLS-048"] == (
        "tests/integration/test_authorized_field_schema.py::"
        "test_cross_organization_field_authority_and_values_fail_closed"
    )
    assert evidence["PG-FILE-SOURCE-FK-021"] == (
        "tests/integration/test_file_source_registration.py::"
        "test_source_version_is_immutable_and_active_pointer_stays_in_organization"
    )
    tenant_fk = next(
        mapping
        for mapping in registry["invariantMappings"]
        if mapping["invariantRef"] == "TENANT-FK-002"
    )
    assert "PG-FILE-SOURCE-FK-021" in tenant_fk["evidenceRefs"]["postgres"]


def test_registry_requires_every_invariant_evidence_layer() -> None:
    registry, _, _ = _documents()
    broken = copy.deepcopy(registry)
    broken["invariantMappings"][0]["evidenceRefs"]["postgres"] = []

    error = _validation_error(broken)

    assert any(
        message.startswith(
            "registry.invariantMappings[0].evidenceRefs.postgres: "
        )
        for message in error.errors
    )


def test_registry_rejects_duplicate_and_orphan_evidence_ids() -> None:
    registry, _, _ = _documents()
    broken = copy.deepcopy(registry)
    duplicate = copy.deepcopy(broken["evidence"][0])
    duplicate["selector"] = broken["evidence"][1]["selector"]
    broken["evidence"].append(duplicate)
    broken["evidence"].append(
        {
            "id": "ORPHAN-EVIDENCE-020",
            "layer": "property",
            "selector": (
                "tests/unit/test_package_budget.py::"
                "test_requested_ceilings_never_increase_any_server_dimension"
            ),
        }
    )

    error = _validation_error(broken)

    assert any("duplicate evidence id" in message for message in error.errors)
    assert any(
        "orphan evidence id 'ORPHAN-EVIDENCE-020'" in message
        for message in error.errors
    )


def test_registry_rejects_wrong_layer_unknown_refs_and_empty_selectors() -> None:
    registry, _, _ = _documents()
    wrong_layer = copy.deepcopy(registry)
    evidence_id = wrong_layer["invariantMappings"][0]["evidenceRefs"]["property"][0]
    declared = next(
        item for item in wrong_layer["evidence"] if item["id"] == evidence_id
    )
    declared["layer"] = "postgres"
    assert any(
        "is declared as layer 'postgres'" in message
        for message in _validation_error(wrong_layer).errors
    )

    empty_selector = copy.deepcopy(registry)
    empty_selector["evidence"][0]["selector"] = ""
    assert any(
        "must be a non-empty string" in message
        for message in _validation_error(empty_selector).errors
    )

    unknown_ref = copy.deepcopy(registry)
    unknown_ref["fixtureMappings"][0]["evidenceRefs"] = [
        "UNKNOWN-EVIDENCE-020"
    ]
    assert any(
        "unknown evidence ref 'UNKNOWN-EVIDENCE-020'" in message
        for message in _validation_error(unknown_ref).errors
    )


def test_registry_requires_exact_fixture_observation_owners() -> None:
    registry, _, _ = _documents()
    broken = copy.deepcopy(registry)
    mapping = broken["fixtureMappings"][0]
    mapping["hardOracleEvidenceRefs"].pop("missingContextFallbackCount")

    error = _validation_error(broken)

    assert any(
        "hardOracleEvidenceRefs.missingContextFallbackCount" in message
        for message in error.errors
    )


def test_registry_hard_oracle_adapters_match_catalog_vetoes() -> None:
    registry, _, _ = _documents()
    assert registry["execution"]["observationProperty"] == (
        "context_engine.security_gate.observation.v1"
    )
    assert [adapter["oracleRef"] for adapter in registry["hardOracleAdapters"]] == list(
        HARD_ORACLES
    )
    assert [adapter["resultKey"] for adapter in registry["hardOracleAdapters"]] == [
        "unauthorizedEvidenceCount",
        "wrongOrganizationEffectCount",
        "missingContextFallbackCount",
    ]
    assert all(
        adapter["observation"] == {
            "source": "pytest-user-property",
            "reducer": "sum",
        }
        for adapter in registry["hardOracleAdapters"]
    )


def test_registry_selector_paths_and_functions_exist() -> None:
    registry, _, _ = _documents()
    root = Path(__file__).resolve().parents[2]

    for evidence in registry["evidence"]:
        file_ref, separator, function_ref = evidence["selector"].partition("::")
        assert separator == "::"
        assert function_ref.startswith("test_")
        source = (root / file_ref).read_text(encoding="utf-8")
        assert f"def {function_ref}(" in source


@pytest.mark.parametrize(
    ("marker", "failure_fragment"),
    [
        ("", "missing @pytest.mark.security_evidence"),
        (
            '@pytest.mark.security_evidence(id="WRONG-001", layer="property")\n',
            "marker IDs differ from registry IDs",
        ),
        (
            '@pytest.mark.security_evidence('
            'id="PROP-TENANT-OWNERSHIP-001", layer="runtime")\n',
            "marker layer",
        ),
        (
            '@pytest.mark.security_evidence('
            '"PROP-TENANT-OWNERSHIP-001", layer="property")\n',
            "must use exact id= and layer= string arguments",
        ),
    ],
)
def test_registry_rejects_missing_wrong_or_malformed_evidence_markers(
    tmp_path: Path,
    marker: str,
    failure_fragment: str,
) -> None:
    registry, schema, catalog, repository = _temporary_marker_repository(
        tmp_path,
        test_source=(
            f"{marker}"
            "def test_registered() -> None:\n"
            "    pass\n\n"
        ),
    )

    with pytest.raises(CatalogValidationError) as raised:
        validate_execution_registry(
            registry,
            schema,
            catalog,
            repository_root=repository,
        )

    assert any(failure_fragment in message for message in raised.value.errors)


def test_repeatable_markers_must_exactly_match_shared_selector_registrations(
    tmp_path: Path,
) -> None:
    registry, schema, catalog, repository = _temporary_marker_repository(
        tmp_path,
        test_source=(
            '@pytest.mark.security_evidence(id="PROP-TENANT-OWNERSHIP-001", '
            'layer="property")\n'
            '@pytest.mark.security_evidence(id="UNRELATED-999", layer="runtime")\n'
            "def test_registered() -> None:\n"
            "    pass\n"
        ),
    )

    with pytest.raises(CatalogValidationError) as raised:
        validate_execution_registry(
            registry,
            schema,
            catalog,
            repository_root=repository,
        )

    assert any(
        "marker IDs differ from registry IDs" in message
        for message in raised.value.errors
    )


def test_registry_rejects_security_evidence_marker_on_unregistered_test(
    tmp_path: Path,
) -> None:
    registry, schema, catalog, repository = _temporary_marker_repository(
        tmp_path,
        test_source=(
            '@pytest.mark.security_evidence(id="PROP-TENANT-OWNERSHIP-001", '
            'layer="property")\n'
            "def test_registered() -> None:\n"
            "    pass\n\n"
            '@pytest.mark.security_evidence(id="UNREGISTERED-999", '
            'layer="runtime")\n'
            "def test_unregistered() -> None:\n"
            "    pass\n"
        ),
    )

    with pytest.raises(CatalogValidationError) as raised:
        validate_execution_registry(
            registry,
            schema,
            catalog,
            repository_root=repository,
        )

    assert any(
        "security_evidence marker is not declared by the registry" in message
        for message in raised.value.errors
    )


def test_fixture_carrier_status_is_frozen_from_catalog() -> None:
    registry, _, catalog = _documents()
    catalog_carriers = {
        fixture["id"]: fixture["carrier"] for fixture in catalog["fixtures"]
    }

    for fixture in registry["fixtureMappings"]:
        carrier = catalog_carriers[fixture["fixtureRef"]]
        assert fixture["carrierStatusAtM0"] == carrier["statusAtM0"]
        assert fixture["m0Expectation"] == carrier["m0Expectation"]

    assert "carrierStatusAtM0" in json.dumps(registry)


@pytest.mark.parametrize("field", ["carrierStatusAtM0", "m0Expectation"])
def test_registry_rejects_missing_or_drifted_fixture_carrier_status(field: str) -> None:
    registry, _, _ = _documents()
    missing = copy.deepcopy(registry)
    missing["fixtureMappings"][0].pop(field)
    assert any(field in message for message in _validation_error(missing).errors)

    drifted = copy.deepcopy(registry)
    drifted["fixtureMappings"][0][field] = "future"
    assert any(
        "must exactly match catalog fixture.carrier" in message
        for message in _validation_error(drifted).errors
    )


def test_catalog_schema_and_registry_paths_are_distinct_authorities() -> None:
    assert DEFAULT_SCHEMA_PATH.name == "security-catalog.schema.json"
    assert DEFAULT_EXECUTION_REGISTRY_PATH.name == "m0-security-evidence.yaml"
    assert DEFAULT_EXECUTION_REGISTRY_SCHEMA_PATH.name == (
        "m0-security-evidence.schema.json"
    )
