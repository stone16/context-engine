from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from engine.learning.golden import GoldenSetUnavailable, load_golden_set
from tests.support.golden import golden_case, golden_document


def _write(path: Path, document: dict[str, object]) -> None:
    path.write_text(json.dumps(document), encoding="utf-8")


def test_schema_v1_accepts_one_strict_synthetic_case(tmp_path: Path) -> None:
    path = tmp_path / "golden.json"
    _write(path, golden_document([golden_case("valid-case")]))

    golden_set = load_golden_set(path, validate_set_composition=False)

    assert golden_set.cases[0].case_ref == "valid-case"
    assert golden_set.cases[0].required_claims[0].claim_ref == "claim-valid-case"
    assert golden_set.cases[0].required_claims[0].claim == (
        "synthetic-required-claim-valid-case"
    )


@pytest.mark.parametrize(
    ("mutation", "reason"),
    (
        (lambda case: case.pop("expectedAnswer"), "required"),
        (lambda case: case.__setitem__("requiredClaims", []), "minItems"),
        (lambda case: case.__setitem__("slice", "global"), "enum"),
        (lambda case: case.__setitem__("unexpected", True), "additionalProperties"),
    ),
)
def test_schema_v1_rejects_malformed_cases(
    tmp_path: Path,
    mutation: object,
    reason: str,
) -> None:
    case = copy.deepcopy(golden_case("invalid-case"))
    assert callable(mutation)
    mutation(case)
    path = tmp_path / "golden.json"
    _write(path, golden_document([case]))

    with pytest.raises(GoldenSetUnavailable, match=reason):
        load_golden_set(path, validate_set_composition=False)


def test_schema_v1_requires_topic_binding_on_each_hard_negative(
    tmp_path: Path,
) -> None:
    case = copy.deepcopy(golden_case("missing-hard-negative-topic"))
    hard_negatives = case["hardNegativeEvidence"]
    assert isinstance(hard_negatives, list)
    assert isinstance(hard_negatives[0], dict)
    hard_negatives[0].pop("topicCluster")
    path = tmp_path / "golden.json"
    _write(path, golden_document([case]))

    with pytest.raises(GoldenSetUnavailable, match="required"):
        load_golden_set(path, validate_set_composition=False)


def test_schema_errors_never_echo_private_case_content(tmp_path: Path) -> None:
    sensitive_markers = (
        "PRIVATE_QUERY_MARKER",
        "PRIVATE_ANSWER_MARKER",
        "PRIVATE_CLAIM_MARKER",
    )
    case = copy.deepcopy(golden_case("private-error-redaction"))
    case["query"] = sensitive_markers[0] * 500
    case["expectedAnswer"] = sensitive_markers[1] * 1_000
    claims = case["requiredClaims"]
    assert isinstance(claims, list)
    assert isinstance(claims[0], dict)
    claims[0]["claim"] = sensitive_markers[2] * 500
    path = tmp_path / "golden.json"
    _write(path, golden_document([case]))

    with pytest.raises(GoldenSetUnavailable) as error:
        load_golden_set(path, validate_set_composition=False)

    message = str(error.value)
    assert all(marker not in message for marker in sensitive_markers)
