#!/usr/bin/env python3
"""Validate and report the versioned ContextEngine security catalog.

The ``.yaml`` catalog deliberately uses JSON-compatible YAML so this D0 check
has no dependency on an application environment or a third-party YAML parser.
"""

from __future__ import annotations

import argparse
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
SUPPORTED_CATALOG_VERSION = "1.0.0"
EXPECTED_INVARIANT_COUNT = 15
EXPECTED_FIXTURE_COUNT = 12
ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-[0-9]{3}$")

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
    "invariants",
    "fixtures",
)
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
    "ACCEPT-005": ("request_not_available", "request_not_available"),
    "ACCEPT-009": ("request_not_available", "request_not_available"),
    "ACCEPT-010": ("citation_not_available", "citation_not_available"),
    "ACCEPT-011": ("resolved", "ContextPackage"),
}

NON_RETRYABLE_RUNTIME_FIXTURES = frozenset({"ACCEPT-005", "ACCEPT-009"})
RESOLVED_EMPTY_RUNTIME_FIXTURES = frozenset({"ACCEPT-001", "ACCEPT-011"})


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
            if (
                response_body is not None
                and response_body.get("coverageReason") != "no_authorized_evidence"
            ):
                collector.add(
                    f"{path}.expected.externalResponse.body.coverageReason",
                    "must be 'no_authorized_evidence' for a hidden or missing Acquire",
                )
            if (
                package_or_error is not None
                and package_or_error.get("gap") != "no_authorized_evidence"
            ):
                collector.add(
                    f"{path}.expected.packageOrError.gap",
                    "must be 'no_authorized_evidence' for a hidden or missing Acquire",
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
            if comparison_fields is not None and "timingBucket" in comparison_fields:
                collector.add(
                    f"{path}.operation.comparisonFields",
                    "must not claim timing equality before the preregistered M5 gate",
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
        item = _resolve_schema_node(
            root,
            array_schema.get("items"),
            f"schema.properties.{name}.items",
            collector,
        )
        if item is not None:
            _require_closed_object_schema(
                item, required_fields, f"schema.{name}.items", collector
            )
            item_nodes[name] = item

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
        for child, fields in (("carrier", CARRIER_FIELDS), ("setup", SETUP_FIELDS)):
            child_schema = _schema_child(
                root, fixture_item, child, "schema.fixtures.items", collector
            )
            if child_schema is not None:
                _require_closed_object_schema(
                    child_schema,
                    fields,
                    f"schema.fixtures.items.properties.{child}",
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
            for child, fields in (
                ("evidence", EVIDENCE_FIELDS),
                ("businessEffects", BUSINESS_EFFECT_FIELDS),
                ("io", IO_FIELDS),
            ):
                child_schema = _schema_child(
                    root,
                    expected_schema,
                    child,
                    "schema.fixtures.items.properties.expected",
                    collector,
                )
                if child_schema is not None:
                    _require_closed_object_schema(
                        child_schema,
                        fields,
                        f"schema.fixtures.items.properties.expected.properties.{child}",
                        collector,
                    )

    definitions = root.get("$defs")
    definitions = collector.require_mapping(definitions, "schema.$defs")
    if definitions is not None:
        closed_object_definitions = {
            "authority": ("issueRefs", "documentRefs", "reconciliation"),
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
        for definition_name, required_fields in closed_object_definitions.items():
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
            if required_fields:
                _require_schema_fields(
                    definition_mapping, required_fields, definition_path, collector
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
    for ref_path, ref in _iter_catalog_authority_refs(catalog):
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
                f"{ref_path}: Markdown heading anchor does not exist: {ref!r}"
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
