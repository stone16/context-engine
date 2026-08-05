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
    "replacement",
    [
        pytest.param("main", id="branch"),
        pytest.param("v1.0.0", id="tag"),
        pytest.param("0123456", id="short-sha"),
    ],
)
def test_non_pinned_commit_is_rejected(tmp_path: Path, replacement: str) -> None:
    registration = write_fixture_tree(tmp_path, SCHEMA)
    _replace(
        registration,
        "0123456789abcdef0123456789abcdef01234567",
        replacement,
    )
    with pytest.raises(GovernanceError, match="commit"):
        validate_tree(tmp_path)


def test_missing_required_field_is_rejected(tmp_path: Path) -> None:
    registration = write_fixture_tree(tmp_path, SCHEMA)
    _replace(registration, 'license = "MIT"\n', "")
    with pytest.raises(GovernanceError, match="license"):
        validate_tree(tmp_path)


def test_path_listed_as_copied_and_excluded_is_rejected(tmp_path: Path) -> None:
    registration = write_fixture_tree(tmp_path, SCHEMA)
    _replace(
        registration,
        'excluded_paths = ["src/private"]',
        'excluded_paths = ["src/example.py"]',
    )
    with pytest.raises(GovernanceError, match="both copied and excluded"):
        validate_tree(tmp_path)


def test_copied_file_inside_excluded_region_is_rejected(tmp_path: Path) -> None:
    registration = write_fixture_tree(tmp_path, SCHEMA)
    _replace(
        registration, 'excluded_paths = ["src/private"]', 'excluded_paths = ["src"]'
    )
    with pytest.raises(GovernanceError, match="resolves into excluded region"):
        validate_tree(tmp_path)


def test_approval_records_must_cover_each_source_region_exactly_once(
    tmp_path: Path,
) -> None:
    registration = write_fixture_tree(tmp_path, SCHEMA)
    _replace(
        registration,
        'approvals = [{ reference = "issue-1", source_paths = ["src/example.py"] }]',
        'approvals = [{ reference = "issue-1", source_paths = ["src/second.py"] }]',
    )

    with pytest.raises(GovernanceError, match="approval coverage"):
        validate_tree(tmp_path)


def test_approval_records_cannot_claim_the_same_source_region_twice(
    tmp_path: Path,
) -> None:
    registration = write_fixture_tree(tmp_path, SCHEMA)
    _replace(
        registration,
        'approvals = [{ reference = "issue-1", source_paths = ["src/example.py"] }]',
        """approvals = [
  { reference = "issue-1", source_paths = ["src/example.py"] },
  { reference = "issue-2", source_paths = ["src/example.py"] },
]""",
    )

    with pytest.raises(GovernanceError, match="multiple approval records"):
        validate_tree(tmp_path)
