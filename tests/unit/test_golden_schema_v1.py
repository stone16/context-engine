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
        (lambda case: case.pop("expectedAnswer"), "expectedAnswer"),
        (lambda case: case.__setitem__("requiredClaims", []), "requiredClaims"),
        (lambda case: case.__setitem__("slice", "global"), "slice"),
        (lambda case: case.__setitem__("unexpected", True), "Additional"),
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
