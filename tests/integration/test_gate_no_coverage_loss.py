"""Corpus independence must not be bought by deleting a downgrade guard.

Making the registered downgrade evidence a function of its own property is only
sound while the guards, their exact refusal messages, and their registration in
the M0 evidence registry all survive. A diff that reaches green by weakening any
of the three is a coverage loss, so each is pinned here.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from scripts.validate_security_catalog import load_document

pytestmark = pytest.mark.integration
ROOT = Path(__file__).parents[2]
REGISTRY_PATH = ROOT / "eval/catalogs/m0-security-evidence.yaml"
MIGRATION_DIRECTORY = ROOT / "migrations/versions"

DOWNGRADE_GUARD_EVIDENCE = (
    (
        "tests/integration/test_file_change_pages.py"
        "::test_control_executes_a_nonterminal_current_delete_observation",
        "20260725_0031",
        "cannot downgrade with File delete",
    ),
    (
        "tests/integration/test_file_change_pages.py"
        "::test_control_schedules_only_the_upserts_from_a_current_mixed_file_page",
        "20260725_0032",
        "mixed File upsert scheduling downgrade requires no retained",
    ),
    (
        "tests/integration/test_file_change_pages.py"
        "::test_control_atomically_schedules_exact_accepted_file_upserts",
        "20260725_0029",
        "requires no retained accepted-change acquisition lineage",
    ),
    (
        "tests/integration/test_file_source_registration.py"
        "::test_control_atomically_activates_one_immutable_v3_file_source_version",
        "20260725_0028",
        "File change-feed downgrade requires no retained",
    ),
)


def _registered_selectors() -> list[str]:
    registry = load_document(REGISTRY_PATH)
    evidence = registry["evidence"]
    assert isinstance(evidence, list)
    return [entry["selector"] for entry in evidence]


def _function_source(selector: str) -> str:
    relative, name = selector.split("::", 1)
    source = (ROOT / relative).read_text(encoding="utf-8")
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            segment = ast.get_source_segment(source, node)
            assert segment is not None
            return segment
    raise AssertionError(f"registered selector has no test function: {selector}")


def _migration_source(revision: str) -> str:
    matches = sorted(MIGRATION_DIRECTORY.glob(f"{revision}_*.py"))
    assert len(matches) == 1, f"revision is not exactly one migration: {revision}"
    return matches[0].read_text(encoding="utf-8")


def _migration_messages(revision: str) -> list[str]:
    """Return the migration's literal text, joining implicit concatenation."""

    return [
        node.value
        for node in ast.walk(ast.parse(_migration_source(revision)))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def test_registered_evidence_has_no_duplicate_or_orphan_selectors() -> None:
    selectors = _registered_selectors()
    for selector in selectors:
        relative, _name = selector.split("::", 1)
        assert (ROOT / relative).is_file()
    assert len(selectors) == len(load_document(REGISTRY_PATH)["evidence"])


@pytest.mark.parametrize(
    ("selector", "revision", "guard"), DOWNGRADE_GUARD_EVIDENCE
)
def test_downgrade_guard_evidence_stays_registered_and_exact(
    selector: str, revision: str, guard: str
) -> None:
    assert selector in _registered_selectors()
    body = _function_source(selector)
    assert f'downgrade_revision(migration_configuration, "{revision}")' in body
    assert guard in body
    assert any(guard in message for message in _migration_messages(revision))


def test_recursive_path_guard_remains_whole_database() -> None:
    """The guard whose traversal order caused the failure is never narrowed."""

    source = _migration_source("20260726_0035")
    assert "recursive File path downgrade requires no retained nested lineage" in source
    assert "organization_id" not in source


def test_change_feed_guard_keeps_every_blocker_branch() -> None:
    """Restore the discrimination the 0028 prefix assertion cannot carry.

    Revision 0028 names only the first whole-database blocker it finds, so
    matching the exact string made the assertion corpus-sensitive again while
    matching the prefix leaves a deleted branch undetectable: with the
    acquisition-lineage predicate false everywhere, branch 1 still refuses and
    the prefix still matches. Pin the branch structure at source level instead.
    """

    source = _migration_source("20260725_0028")
    for blocker in (
        "accepted page stream",
        "File acquisition lineage",
        "File source cleanup lineage",
        "ActionTicket lineage",
    ):
        assert f"THEN '{blocker}'" in source


def test_v3_activation_still_binds_its_blocker_to_its_own_organization() -> None:
    """The one multi-branch guard keeps naming the tenant it proves."""

    body = _function_source(
        "tests/integration/test_file_source_registration.py"
        "::test_control_atomically_activates_one_immutable_v3_file_source_version"
    )
    assert (
        "_retains_v3_acquisition_lineage(migration_configuration, organization_id)"
        in body
    )
