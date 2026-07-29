from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from engine.learning.governance import (
    PublicSubsetMaintainerAuthentication,
    PublicSubsetPromotionAuthority,
    PublicSubsetPromotionRejected,
    VerifiedPublicSubsetMaintainerIdentity,
    assert_tracked_golden_tree_is_synthetic,
    load_public_subset_governance,
)
from tests.support.golden import golden_document, valid_composed_entries

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class _SyntheticMaintainerAuthenticator:
    def __init__(self, *, credential: str, authority_ref: str) -> None:
        self._credential = credential
        self._authority_ref = authority_ref

    def authenticate(
        self,
        opaque_credential: str,
    ) -> PublicSubsetMaintainerAuthentication:
        if opaque_credential != self._credential:
            raise PublicSubsetPromotionRejected("synthetic authentication rejected")
        return PublicSubsetMaintainerAuthentication(
            principal_ref="synthetic-maintainer-principal",
            authentication_binding_ref="synthetic-maintainer-authentication",
            authority_ref=self._authority_ref,
        )


def _authority(
    *,
    credential: str = "synthetic-maintainer-credential",
    authority_ref: str = "maintainer",
) -> PublicSubsetPromotionAuthority:
    configuration = load_public_subset_governance(
        REPOSITORY_ROOT / "eval/public-subset-governance.json"
    )
    return PublicSubsetPromotionAuthority(
        configuration,
        _SyntheticMaintainerAuthenticator(
            credential=credential,
            authority_ref=authority_ref,
        )
    )


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

    _authority().authorize("synthetic-maintainer-credential")
    with pytest.raises(PublicSubsetPromotionRejected):
        _authority(
            credential="synthetic-release-credential",
            authority_ref="release-operator",
        ).authorize(
            "synthetic-release-credential",
        )
    with pytest.raises(PublicSubsetPromotionRejected):
        _authority(
            credential="synthetic-privacy-credential",
            authority_ref="designated-privacy-reviewer",
        ).authorize(
            "synthetic-privacy-credential",
        )


def test_raw_maintainer_claim_cannot_forge_verified_privacy_authority() -> None:
    with pytest.raises(TypeError, match="authenticated"):
        VerifiedPublicSubsetMaintainerIdentity(
            principal_ref="synthetic-forged-principal",
            authentication_binding_ref="synthetic-forged-authentication",
            authority_ref="maintainer",
        )
    with pytest.raises(PublicSubsetPromotionRejected):
        _authority().authorize("maintainer")


@pytest.mark.parametrize("unknown_field", ("memo", "title", "excerpt", "body"))
def test_unknown_fields_cannot_bypass_tracked_fixture_privacy_scan(
    tmp_path: Path,
    unknown_field: str,
) -> None:
    golden_root = tmp_path / "golden"
    golden_root.mkdir()
    entries = valid_composed_entries()
    for entry in entries:
        entry["caseRef"] = f"synthetic-{entry['caseRef']}"
        claims = cast(list[dict[str, object]], entry["requiredClaims"])
        for claim in claims:
            claim["claimRef"] = f"synthetic-{claim['claimRef']}"
    document = golden_document(entries)
    entries = cast(list[dict[str, object]], document["entries"])
    entries[0][unknown_field] = "personal-content-bypass"
    (golden_root / "fixture.json").write_text(
        json.dumps(document),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="schema"):
        assert_tracked_golden_tree_is_synthetic(golden_root)


def test_unknown_json_shape_cannot_bypass_tracked_fixture_privacy_scan(
    tmp_path: Path,
) -> None:
    golden_root = tmp_path / "golden"
    golden_root.mkdir()
    (golden_root / "unknown.json").write_text(
        json.dumps({"memo": "personal-content-bypass"}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="schema"):
        assert_tracked_golden_tree_is_synthetic(golden_root)


def test_unexpected_schema_filename_cannot_bypass_privacy_scan(
    tmp_path: Path,
) -> None:
    golden_root = tmp_path / "golden"
    rogue_root = golden_root / "rogue"
    rogue_root.mkdir(parents=True)
    (rogue_root / "schema.json").write_text(
        json.dumps({"description": "personal-content-bypass"}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="schema path"):
        assert_tracked_golden_tree_is_synthetic(golden_root)
