from __future__ import annotations

from pathlib import Path

import pytest

from scripts.third_party_governance import GovernanceError, validate_tree
from tests.unit._third_party_governance_fixtures import write_fixture_tree

SCHEMA = Path(__file__).parents[2] / "schemas/third-party-upstream.schema.json"


def _replace(path: Path, old: str, new: str) -> None:
    path.write_text(
        path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8"
    )


def test_valid_registration_passes(tmp_path: Path) -> None:
    write_fixture_tree(tmp_path, SCHEMA)
    assert [item.name for item in validate_tree(tmp_path)] == ["example"]


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (('license = "MIT"\n', ""), "license"),
        (
            (
                "0123456789abcdef0123456789abcdef01234567",
                "main",
            ),
            "commit",
        ),
        (
            (
                'excluded_paths = ["src/private"]',
                'excluded_paths = ["src/example.py"]',
            ),
            "both copied and excluded",
        ),
    ],
)
def test_malformed_registration_fails_distinctly(
    tmp_path: Path, mutation: tuple[str, str], reason: str
) -> None:
    registration = write_fixture_tree(tmp_path, SCHEMA)
    _replace(registration, *mutation)
    with pytest.raises(GovernanceError, match=reason):
        validate_tree(tmp_path)


def test_short_sha_is_rejected(tmp_path: Path) -> None:
    registration = write_fixture_tree(tmp_path, SCHEMA)
    _replace(
        registration,
        "0123456789abcdef0123456789abcdef01234567",
        "0123456",
    )
    with pytest.raises(GovernanceError, match="commit"):
        validate_tree(tmp_path)


def test_copied_file_inside_excluded_region_is_rejected(tmp_path: Path) -> None:
    registration = write_fixture_tree(tmp_path, SCHEMA)
    _replace(
        registration, 'excluded_paths = ["src/private"]', 'excluded_paths = ["src"]'
    )
    with pytest.raises(GovernanceError, match="resolves into excluded region"):
        validate_tree(tmp_path)
