from __future__ import annotations

from pathlib import Path

import pytest

from scripts.third_party_governance import GovernanceError, validate_tree
from tests.unit._third_party_governance_fixtures import write_fixture_tree

SCHEMA = Path(__file__).parents[2] / "schemas/third-party-upstream.schema.json"


def test_every_subtree_requires_a_registration(tmp_path: Path) -> None:
    write_fixture_tree(tmp_path, SCHEMA)
    (tmp_path / "third_party/unregistered").mkdir()
    with pytest.raises(GovernanceError, match="lacks UPSTREAM.toml"):
        validate_tree(tmp_path)


def test_orphan_file_fails_discovery(tmp_path: Path) -> None:
    write_fixture_tree(tmp_path, SCHEMA)
    (tmp_path / "third_party/example/orphan.txt").write_text(
        "unclaimed", encoding="utf-8"
    )
    with pytest.raises(GovernanceError, match="orphan file"):
        validate_tree(tmp_path)
