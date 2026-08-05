from __future__ import annotations

from pathlib import Path

import pytest

from scripts.third_party_governance import GovernanceError, validate_tree
from tests.unit._third_party_governance_fixtures import write_fixture_tree

SCHEMA = Path(__file__).parents[2] / "schemas/third-party-upstream.schema.json"
VALID_SELECTOR = (
    'source_selectors = [{ source_path = "src/example.py", kind = "function", '
    'name = "selected", patch_path = "third_party/example/patches/example.patch", '
    'pinned_sha256 = "a7314094fde8a95d2dccb8e593ca0fb49c3feeeb2e3c6134464e'
    '49761e9555f7", vendored_sha256 = "9837e34301992075076adaca0a7826a80066738b'
    '82444ce51c380b4ebf35abbf" }]'
)


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


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        (
            "source_paths",
            'source_paths = ["src/example.py", "src/./example.py"]',
        ),
        (
            "excluded_paths",
            'excluded_paths = ["src/private", "src/./private"]',
        ),
    ),
)
def test_registration_rejects_duplicate_canonical_paths(
    tmp_path: Path,
    field: str,
    replacement: str,
) -> None:
    registration = write_fixture_tree(tmp_path, SCHEMA)
    original = (
        'source_paths = ["src/example.py"]'
        if field == "source_paths"
        else 'excluded_paths = ["src/private"]'
    )
    _replace(registration, original, replacement)

    with pytest.raises(GovernanceError, match=f"{field}.*duplicate canonical"):
        validate_tree(tmp_path)


def test_approval_rejects_duplicate_canonical_paths(tmp_path: Path) -> None:
    registration = write_fixture_tree(tmp_path, SCHEMA)
    _replace(
        registration,
        'source_paths = ["src/example.py"] }]',
        'source_paths = ["src/example.py", "src/./example.py"] }]',
    )

    with pytest.raises(GovernanceError, match="approval.*duplicate canonical"):
        validate_tree(tmp_path)


def test_source_selector_must_belong_to_a_registered_copied_source(
    tmp_path: Path,
) -> None:
    registration = write_fixture_tree(tmp_path, SCHEMA)
    _replace(
        registration,
        'source_paths = ["src/example.py"] }]',
        VALID_SELECTOR.replace(
            'source_path = "src/example.py"', 'source_path = "src/other.py"'
        )
        + " }]",
    )

    with pytest.raises(
        GovernanceError, match="source selector.*not.*registered copied"
    ):
        validate_tree(tmp_path)


def test_source_selector_cannot_overlap_whole_file_approval(tmp_path: Path) -> None:
    registration = write_fixture_tree(tmp_path, SCHEMA)
    _replace(
        registration,
        'source_paths = ["src/example.py"] }]',
        'source_paths = ["src/example.py"], ' + VALID_SELECTOR + " }]",
    )

    with pytest.raises(GovernanceError, match="ambiguous whole-file and selector"):
        validate_tree(tmp_path)


def test_source_selector_cannot_be_owned_by_multiple_approvals(
    tmp_path: Path,
) -> None:
    registration = write_fixture_tree(tmp_path, SCHEMA)
    _replace(
        registration,
        'approvals = [{ reference = "issue-1", source_paths = ["src/example.py"] }]',
        "approvals = ["
        '{ reference = "issue-1", '
        + VALID_SELECTOR
        + " }, "
        + '{ reference = "issue-2", '
        + VALID_SELECTOR
        + " }]",
    )

    with pytest.raises(GovernanceError, match="selector.*multiple approval records"):
        validate_tree(tmp_path)


def test_selector_only_approval_covers_registered_source(tmp_path: Path) -> None:
    registration = write_fixture_tree(tmp_path, SCHEMA)
    _replace(
        registration,
        'source_paths = ["src/example.py"] }]',
        VALID_SELECTOR + " }]",
    )

    assert [item.name for item in validate_tree(tmp_path)] == ["example"]


def test_source_selector_must_exist_in_vendored_and_pinned_bytes(
    tmp_path: Path,
) -> None:
    registration = write_fixture_tree(tmp_path, SCHEMA)
    _replace(
        registration,
        'source_paths = ["src/example.py"] }]',
        VALID_SELECTOR.replace('name = "selected"', 'name = "missing"') + " }]",
    )

    with pytest.raises(GovernanceError, match="selector.*missing.*vendored"):
        validate_tree(tmp_path)


def test_source_selector_hashes_bind_the_named_region(tmp_path: Path) -> None:
    registration = write_fixture_tree(tmp_path, SCHEMA)
    _replace(
        registration,
        'source_paths = ["src/example.py"] }]',
        VALID_SELECTOR.replace('name = "selected"', 'name = "other"') + " }]",
    )

    with pytest.raises(GovernanceError, match="selector.*vendored hash mismatch"):
        validate_tree(tmp_path)


def test_decision_document_anchor_must_resolve(tmp_path: Path) -> None:
    registration = write_fixture_tree(tmp_path, SCHEMA)
    document = tmp_path / "docs/approval.md"
    document.parent.mkdir()
    document.write_text("# Actual approval\n", encoding="utf-8")
    _replace(
        registration,
        'reference = "issue-1",',
        'reference = "issue-1", decision = "D6", '
        'decision_document = "docs/approval.md#missing",',
    )

    with pytest.raises(GovernanceError, match="decision document anchor.*missing"):
        validate_tree(tmp_path)
