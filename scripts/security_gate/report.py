"""Pure reconciliation and independent release-gate report construction."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import cast

from scripts.security_gate.observations import (
    ORACLE_RESULT_KEYS,
    SECURITY_EVIDENCE_MARKER_VERSION,
)

REPORT_VERSION = "1.0.0"
TERMINAL_SUCCESS = "passed"
FORBIDDEN_OUTCOMES = frozenset(
    {"failed", "error", "skipped", "xfailed", "xpassed", "incomplete"}
)
_DIGEST_PROVENANCE_FIELDS = (
    "catalogDigest",
    "catalogSchemaDigest",
    "configurationDigest",
    "contentStateDigest",
    "executionRegistryDigest",
    "executionRegistrySchemaDigest",
    "fixtureDigest",
    "migrationStateDigest",
    "rawResultDigest",
    "schemaManifestDigest",
    "testResultDigest",
    "trackedDiffDigest",
    "stagedDiffDigest",
    "unstagedDiffDigest",
    "untrackedContentDigest",
)


def canonical_json(value: object) -> bytes:
    """Return stable UTF-8 JSON for report/config digest provenance."""

    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_digest(value: object) -> str:
    """Hash one canonical JSON document with SHA-256."""

    return hashlib.sha256(canonical_json(value)).hexdigest()


def _provenance_failures(provenance: Mapping[str, object]) -> list[str]:
    failures: list[str] = []
    commit = provenance.get("commit")
    if not isinstance(commit, str) or len(commit) not in {40, 64} or any(
        character not in "0123456789abcdef" for character in commit
    ):
        failures.append("provenance Git commit is unavailable or invalid")
    for field in _DIGEST_PROVENANCE_FIELDS:
        value = provenance.get(field)
        if not isinstance(value, str) or len(value) != 64 or any(
            character not in "0123456789abcdef" for character in value
        ):
            failures.append(f"provenance {field} is unavailable or invalid")
    if not isinstance(provenance.get("trackedDirty"), bool):
        failures.append("provenance trackedDirty is unavailable")
    if not isinstance(provenance.get("contentStateDirty"), bool):
        failures.append("provenance contentStateDirty is unavailable")
    untracked_count = provenance.get("untrackedFileCount")
    if (
        isinstance(untracked_count, bool)
        or not isinstance(untracked_count, int)
        or untracked_count < 0
    ):
        failures.append("provenance untrackedFileCount is unavailable or invalid")
    execution_command = provenance.get("executionCommand")
    if not isinstance(execution_command, list) or not execution_command or not all(
        isinstance(value, str) and value for value in execution_command
    ):
        failures.append("provenance executionCommand is unavailable or invalid")
    alembic_head = provenance.get("alembicHead")
    live_revision = provenance.get("liveDatabaseRevision")
    if not isinstance(alembic_head, str) or not alembic_head:
        failures.append("provenance Alembic head is unavailable")
    if live_revision != alembic_head:
        failures.append("provenance live database revision differs from Alembic head")
    if not isinstance(provenance.get("runnerVersion"), str):
        failures.append("provenance runner version is unavailable")
    return failures


def canonical_test_results(raw_evidence: Mapping[str, object]) -> dict[str, object]:
    """Project nondeterministic raw timing away from the result digest."""

    pytest_section = _mapping(raw_evidence.get("pytest"))
    tests: list[dict[str, object]] = []
    for test in _mapping_sequence(pytest_section.get("tests")):
        tests.append(
            {
                "nodeId": test.get("nodeId"),
                "outcome": test.get("outcome"),
                "observations": test.get("observations", []),
                "observationErrors": test.get("observationErrors", []),
            }
        )
    return {
        "exitCode": pytest_section.get("exitCode"),
        "selectedSelectors": pytest_section.get("selectedSelectors", []),
        "collectedNodeIds": pytest_section.get("collectedNodeIds", []),
        "securityEvidenceMarkerVersion": pytest_section.get(
            "securityEvidenceMarkerVersion"
        ),
        "collectedTests": pytest_section.get("collectedTests", []),
        "collectionErrors": pytest_section.get("collectionErrors", []),
        "tests": sorted(tests, key=lambda item: str(item["nodeId"])),
    }


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _mapping_sequence(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _strings(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    return [item for item in value if isinstance(item, str)]


def _selector_matches(selector: str, node_id: str) -> bool:
    return node_id == selector or node_id.startswith(f"{selector}[")


def _registry_evidence(
    registry: Mapping[str, object],
) -> tuple[dict[str, Mapping[str, object]], dict[str, str]]:
    evidence_by_id: dict[str, Mapping[str, object]] = {}
    selector_by_id: dict[str, str] = {}
    for entry in _mapping_sequence(registry.get("evidence")):
        evidence_id = entry.get("id")
        selector = entry.get("selector")
        if isinstance(evidence_id, str) and isinstance(selector, str):
            evidence_by_id[evidence_id] = entry
            selector_by_id[evidence_id] = selector
    return evidence_by_id, selector_by_id


def _mapped_evidence_refs(mapping: Mapping[str, object]) -> list[str]:
    evidence_refs = mapping.get("evidenceRefs")
    if isinstance(evidence_refs, Mapping):
        refs: list[str] = []
        for layer in ("property", "postgres", "runtime"):
            refs.extend(_strings(evidence_refs.get(layer)))
        return refs
    return _strings(evidence_refs)


def _expected_observations(
    registry: Mapping[str, object],
) -> set[tuple[str, str, str]]:
    expected: set[tuple[str, str, str]] = set()
    for fixture in _mapping_sequence(registry.get("fixtureMappings")):
        fixture_ref = fixture.get("fixtureRef")
        hard_refs = _mapping(fixture.get("hardOracleEvidenceRefs"))
        if not isinstance(fixture_ref, str):
            continue
        for result_key in ORACLE_RESULT_KEYS:
            for evidence_ref in _strings(hard_refs.get(result_key)):
                expected.add((fixture_ref, evidence_ref, result_key))
    return expected


def reconcile_execution(
    registry: Mapping[str, object],
    raw_evidence: Mapping[str, object],
    *,
    catalog: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Reconcile exact selected pytest leaves, mappings, and explicit counters."""

    failures: list[str] = []
    evidence_by_id, selector_by_id = _registry_evidence(registry)
    pytest_section = _mapping(raw_evidence.get("pytest"))
    tests = _mapping_sequence(pytest_section.get("tests"))
    test_by_node: dict[str, Mapping[str, object]] = {}
    for test in tests:
        node_id = test.get("nodeId")
        if not isinstance(node_id, str):
            failures.append("test result has no exact nodeId")
            continue
        if node_id in test_by_node:
            failures.append(f"duplicate test result: {node_id}")
        test_by_node[node_id] = test

    collected_node_ids = _strings(pytest_section.get("collectedNodeIds"))
    if Counter(collected_node_ids) != Counter(test_by_node.keys()):
        failures.append("collected node IDs differ from test results")
    if pytest_section.get("securityEvidenceMarkerVersion") != (
        SECURITY_EVIDENCE_MARKER_VERSION
    ):
        failures.append("security_evidence marker version is missing or unsupported")

    collected_metadata_by_node: dict[str, Mapping[str, object]] = {}
    for metadata in _mapping_sequence(pytest_section.get("collectedTests")):
        node_id = metadata.get("nodeId")
        if not isinstance(node_id, str):
            failures.append("collected test metadata has no exact nodeId")
            continue
        if node_id in collected_metadata_by_node:
            failures.append(f"duplicate collected test metadata: {node_id}")
        collected_metadata_by_node[node_id] = metadata
    if Counter(collected_node_ids) != Counter(collected_metadata_by_node.keys()):
        failures.append("collected test metadata differs from collected node IDs")

    declared_by_node: dict[str, Counter[tuple[str, str]]] = {}
    for node_id, metadata in collected_metadata_by_node.items():
        declaration_counter: Counter[tuple[str, str]] = Counter()
        for declaration in _mapping_sequence(metadata.get("securityEvidence")):
            evidence_id = declaration.get("id")
            layer = declaration.get("layer")
            if not isinstance(evidence_id, str) or not isinstance(layer, str):
                failures.append(f"malformed security_evidence marker at {node_id}")
                continue
            pair = (evidence_id, layer)
            declaration_counter[pair] += 1
            if declaration_counter[pair] > 1:
                failures.append(
                    "duplicate security_evidence marker at "
                    f"{node_id}: {evidence_id} {layer}"
                )
            registry_entry = evidence_by_id.get(evidence_id)
            if registry_entry is None:
                failures.append(
                    f"orphan security_evidence marker at {node_id}: {evidence_id}"
                )
                continue
            expected_layer = registry_entry.get("layer")
            if layer != expected_layer:
                failures.append(
                    "wrong security_evidence layer at "
                    f"{node_id}: {evidence_id} expected {expected_layer!r}, "
                    f"got {layer!r}"
                )
            selector = selector_by_id.get(evidence_id)
            if selector is None or not _selector_matches(selector, node_id):
                failures.append(
                    "security_evidence marker emitted by wrong test: "
                    f"{node_id} {evidence_id}"
                )
        errors = _strings(metadata.get("securityEvidenceErrors"))
        failures.extend(
            f"malformed security_evidence marker at {node_id}: {error}"
            for error in errors
        )
        declared_by_node[node_id] = declaration_counter

    if pytest_section.get("exitCode") != 0:
        failures.append(f"pytest exited nonzero: {pytest_section.get('exitCode')!r}")
    collection_errors = _mapping_sequence(pytest_section.get("collectionErrors"))
    if collection_errors:
        failures.append("pytest collection reported errors")

    selected = _strings(pytest_section.get("selectedSelectors"))
    expected_selectors = list(dict.fromkeys(selector_by_id.values()))
    if selected != expected_selectors:
        failures.append(
            "selected selectors differ from the deduplicated registry order"
        )

    evidence_results: list[dict[str, object]] = []
    passed_evidence_ids: set[str] = set()
    for evidence_id, selector in selector_by_id.items():
        matching = [
            test
            for node_id, test in test_by_node.items()
            if _selector_matches(selector, node_id)
        ]
        evidence_failures: list[str] = []
        if not matching:
            evidence_failures.append("missing test")
        for test in matching:
            node_id = cast(str, test.get("nodeId"))
            layer = evidence_by_id.get(evidence_id, {}).get("layer")
            if not isinstance(layer, str) or declared_by_node.get(node_id, Counter())[
                (evidence_id, layer)
            ] != 1:
                evidence_failures.append(
                    f"missing security_evidence marker at {node_id}"
                )
            outcome = test.get("outcome")
            if outcome != TERMINAL_SUCCESS:
                evidence_failures.append(
                    f"forbidden outcome {outcome!r} at {test.get('nodeId')}"
                )
            observation_errors = _strings(test.get("observationErrors"))
            if observation_errors:
                evidence_failures.extend(
                    f"malformed observation at {test.get('nodeId')}: {message}"
                    for message in observation_errors
                )
        if not evidence_failures:
            passed_evidence_ids.add(evidence_id)
        else:
            failures.extend(
                f"{evidence_id}: {message}" for message in evidence_failures
            )
        evidence_results.append(
            {
                "evidenceRef": evidence_id,
                "selector": selector,
                "nodeIds": sorted(
                    cast(str, test["nodeId"])
                    for test in matching
                    if isinstance(test.get("nodeId"), str)
                ),
                "status": "PASS" if not evidence_failures else "FAIL",
                "failures": evidence_failures,
            }
        )

    expected_observations = _expected_observations(registry)
    observations: list[dict[str, object]] = []
    observed_triples: Counter[tuple[str, str, str]] = Counter()
    fixture_leaf_pairs: Counter[tuple[str, str, str]] = Counter()
    for test in tests:
        node_id = test.get("nodeId")
        if not isinstance(node_id, str):
            continue
        for observation in _mapping_sequence(test.get("observations")):
            fixture_ref = observation.get("fixtureRef")
            evidence_ref = observation.get("evidenceRef")
            values = _mapping(observation.get("values"))
            if not isinstance(fixture_ref, str) or not isinstance(evidence_ref, str):
                failures.append(f"malformed observation at {node_id}")
                continue
            fixture_leaf_pair = (node_id, fixture_ref, evidence_ref)
            fixture_leaf_pairs[fixture_leaf_pair] += 1
            if fixture_leaf_pairs[fixture_leaf_pair] > 1:
                failures.append(
                    "duplicate observation within one test leaf: "
                    f"{node_id} {fixture_ref} {evidence_ref}"
                )
            observation_selector = selector_by_id.get(evidence_ref)
            if observation_selector is None or not _selector_matches(
                observation_selector, node_id
            ):
                failures.append(
                    "observation emitted by wrong test: "
                    f"{node_id} {fixture_ref} {evidence_ref}"
                )
            normalized_values: dict[str, int] = {}
            for result_key in ORACLE_RESULT_KEYS:
                value = values.get(result_key)
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    failures.append(
                        f"malformed observation value at {node_id}: {result_key}"
                    )
                    continue
                normalized_values[result_key] = value
                triple = (fixture_ref, evidence_ref, result_key)
                observed_triples[triple] += 1
                if triple not in expected_observations:
                    failures.append(
                        "orphan observation: "
                        f"{fixture_ref} {evidence_ref} {result_key} at {node_id}"
                    )
            observations.append(
                {
                    "nodeId": node_id,
                    "fixtureRef": fixture_ref,
                    "evidenceRef": evidence_ref,
                    "values": normalized_values,
                }
            )

    for fixture_ref, evidence_ref, result_key in sorted(expected_observations):
        if observed_triples[(fixture_ref, evidence_ref, result_key)] == 0:
            failures.append(
                "missing observation: "
                f"{fixture_ref} {evidence_ref} {result_key}"
            )

    for fixture in _mapping_sequence(registry.get("fixtureMappings")):
        fixture_ref = fixture.get("fixtureRef")
        if not isinstance(fixture_ref, str):
            continue
        for evidence_ref in _mapped_evidence_refs(fixture):
            fixture_selector = selector_by_id.get(evidence_ref)
            if fixture_selector is None:
                continue
            for node_id in test_by_node:
                if (
                    _selector_matches(fixture_selector, node_id)
                    and fixture_leaf_pairs[(node_id, fixture_ref, evidence_ref)] == 0
                ):
                    failures.append(
                        "missing observation at fixture test leaf: "
                        f"{node_id} {fixture_ref} {evidence_ref}"
                    )

    fixture_results: list[dict[str, object]] = []
    failed_fixture_refs: set[str] = set()
    for mapping in _mapping_sequence(registry.get("fixtureMappings")):
        fixture_ref = mapping.get("fixtureRef")
        refs = _mapped_evidence_refs(mapping)
        missing_evidence = [ref for ref in refs if ref not in passed_evidence_ids]
        fixture_observations = [
            observation
            for observation in observations
            if observation["fixtureRef"] == fixture_ref
        ]
        missing_observation = any(
            triple[0] == fixture_ref and observed_triples[triple] == 0
            for triple in expected_observations
        )
        hard_refs = _mapping(mapping.get("hardOracleEvidenceRefs"))
        failed_oracle_result_keys = [
            result_key
            for result_key in ORACLE_RESULT_KEYS
            if any(
                observation["fixtureRef"] == fixture_ref
                and observation["evidenceRef"] in _strings(hard_refs.get(result_key))
                and cast(Mapping[str, object], observation["values"]).get(result_key)
                != 0
                for observation in fixture_observations
            )
        ]
        fixture_failed = bool(
            missing_evidence or missing_observation or failed_oracle_result_keys
        )
        if fixture_failed and isinstance(fixture_ref, str):
            failed_fixture_refs.add(fixture_ref)
        fixture_results.append(
            {
                "fixtureRef": fixture_ref,
                "status": "FAIL" if fixture_failed else "PASS",
                "evidenceRefs": refs,
                "failedEvidenceRefs": missing_evidence,
                "failedOracleResultKeys": failed_oracle_result_keys,
                "carrierStatusAtM0": mapping.get("carrierStatusAtM0"),
                "m0Expectation": mapping.get("m0Expectation"),
                "observationCount": len(fixture_observations),
            }
        )

    invariant_to_fixture_refs: dict[str, list[str]] = {}
    if catalog is not None:
        for fixture in _mapping_sequence(catalog.get("fixtures")):
            fixture_ref = fixture.get("id")
            if not isinstance(fixture_ref, str):
                continue
            for invariant_ref in _strings(fixture.get("invariantRefs")):
                invariant_to_fixture_refs.setdefault(invariant_ref, []).append(
                    fixture_ref
                )

    invariant_results: list[dict[str, object]] = []
    for mapping in _mapping_sequence(registry.get("invariantMappings")):
        raw_invariant_ref = mapping.get("invariantRef")
        invariant_ref = (
            raw_invariant_ref if isinstance(raw_invariant_ref, str) else ""
        )
        refs = _mapped_evidence_refs(mapping)
        missing = [ref for ref in refs if ref not in passed_evidence_ids]
        fixture_failures = [
            fixture_ref
            for fixture_ref in invariant_to_fixture_refs.get(invariant_ref, [])
            if fixture_ref in failed_fixture_refs
        ]
        invariant_results.append(
            {
                "invariantRef": invariant_ref,
                "status": "FAIL" if missing or fixture_failures else "PASS",
                "evidenceRefs": refs,
                "failedEvidenceRefs": missing,
                "failedFixtureRefs": fixture_failures,
            }
        )

    hard_oracles: list[dict[str, object]] = []
    for adapter in _mapping_sequence(registry.get("hardOracleAdapters")):
        oracle_ref = adapter.get("oracleRef")
        raw_result_key = adapter.get("resultKey")
        result_key = raw_result_key if isinstance(raw_result_key, str) else ""
        required_value = adapter.get("requiredValue")
        observation_contract = _mapping(adapter.get("observation"))
        configured_evidence_refs = set(
            _strings(observation_contract.get("evidenceRefs"))
        )
        evidence_refs = configured_evidence_refs or {
            triple[1]
            for triple in expected_observations
            if triple[2] == result_key
        }
        oracle_values: list[int] = []
        for observation in observations:
            observation_values = cast(
                Mapping[str, object], observation["values"]
            )
            candidate = observation_values.get(result_key)
            if (
                observation["evidenceRef"] in evidence_refs
                and isinstance(candidate, int)
                and not isinstance(candidate, bool)
            ):
                oracle_values.append(candidate)
        observed_value = sum(oracle_values)
        complete = bool(oracle_values) and all(
            triple[2] != result_key
            or triple[1] not in evidence_refs
            or observed_triples[triple] > 0
            for triple in expected_observations
        )
        passed = complete and observed_value == required_value
        if not passed:
            failures.append(
                f"hard oracle veto {oracle_ref}: observed {observed_value!r}, "
                f"required {required_value!r}, complete={complete}"
            )
        hard_oracles.append(
            {
                "oracleRef": oracle_ref,
                "resultKey": result_key,
                "requiredValue": required_value,
                "observedValue": observed_value,
                "observationCount": len(oracle_values),
                "status": "PASS" if passed else "FAIL",
                "veto": True,
            }
        )

    unique_failures = list(dict.fromkeys(failures))
    return {
        "passed": not unique_failures,
        "failures": unique_failures,
        "passedEvidenceIds": sorted(passed_evidence_ids),
        "evidence": evidence_results,
        "invariants": invariant_results,
        "fixtures": fixture_results,
        "hardOracles": hard_oracles,
        "observations": observations,
        "testResultDigest": canonical_digest(canonical_test_results(raw_evidence)),
    }


def build_release_gate_report(
    *,
    reconciliation: Mapping[str, object],
    rls_audit: Mapping[str, object],
    provenance: Mapping[str, object],
    raw_result_digest: str,
) -> dict[str, object]:
    """Build four independent gates; Security alone is evaluated at M0."""

    complete_provenance = {
        **dict(provenance),
        "rawResultDigest": raw_result_digest,
        "testResultDigest": reconciliation.get("testResultDigest"),
    }
    provenance_failures = _provenance_failures(complete_provenance)
    security_passed = (
        reconciliation.get("passed") is True
        and rls_audit.get("passed") is True
        and not provenance_failures
    )
    rls_failures = _strings(rls_audit.get("failures"))
    if rls_audit.get("passed") is not True and not rls_failures:
        rls_failures = ["RLS audit did not pass"]
    security_failures = [
        *_strings(reconciliation.get("failures")),
        *rls_failures,
        *provenance_failures,
    ]
    security = {
        "status": "pass" if security_passed else "fail",
        "veto": True,
        "invariants": reconciliation.get("invariants", []),
        "fixtures": reconciliation.get("fixtures", []),
        "hardOracles": reconciliation.get("hardOracles", []),
        "rls": rls_audit,
        "failures": security_failures,
    }
    not_evaluated = {
        "status": "not-evaluated",
        "reason": "No M0 gate is defined for this independent dimension.",
    }
    return {
        "reportVersion": REPORT_VERSION,
        "m0SecurityDecision": "pass" if security_passed else "fail",
        "releaseDecision": "not-evaluated" if security_passed else "fail",
        "promotionReadiness": "not-evaluated",
        "provenance": complete_provenance,
        "gates": {
            "Security": security,
            "Reliability": dict(not_evaluated),
            "Quality": dict(not_evaluated),
            "Budget": dict(not_evaluated),
        },
    }
