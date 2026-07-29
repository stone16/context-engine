from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.learning.governance import (
    PublicSubsetPromotionRejected,
    assert_tracked_golden_tree_is_synthetic,
    authorize_public_subset_promotion,
    load_public_subset_governance,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_tracked_golden_tree_contains_only_placeholder_synthetic_cases() -> None:
    assert_tracked_golden_tree_is_synthetic(REPOSITORY_ROOT / "eval/golden")


def test_personal_or_non_placeholder_tracked_case_is_refused(tmp_path: Path) -> None:
    golden_root = tmp_path / "golden"
    golden_root.mkdir()
    case_path = golden_root / "case.json"
    case_path.write_text(
        json.dumps(
            {
                "schemaVersion": "context-engine-golden-set-v1",
                "name": "synthetic-fixture",
                "synthetic": True,
                "entries": [
                    {
                        "caseRef": "synthetic-case",
                        "query": "Where is my project roadmap?",
                        "expectedAnswer": "It is in a real note.",
                        "requiredClaims": ["The project has a real deadline."],
                        "expectedEvidence": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="placeholder"):
        assert_tracked_golden_tree_is_synthetic(golden_root)


def test_only_configured_maintainer_authority_can_promote_public_subset() -> None:
    configuration = load_public_subset_governance(
        REPOSITORY_ROOT / "eval/public-subset-governance.json"
    )
    assert configuration.promotion_authority == "maintainer"

    authorize_public_subset_promotion("maintainer", configuration)
    with pytest.raises(PublicSubsetPromotionRejected):
        authorize_public_subset_promotion("release-operator", configuration)
    with pytest.raises(PublicSubsetPromotionRejected):
        authorize_public_subset_promotion("designated-privacy-reviewer", configuration)
