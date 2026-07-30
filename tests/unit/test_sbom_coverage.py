from __future__ import annotations

from pathlib import Path

import pytest

from scripts.third_party_governance import (
    GovernanceError,
    validate_sbom_coverage,
    validate_tree,
)
from tests.unit._third_party_governance_fixtures import write_fixture_tree

SCHEMA = Path(__file__).parents[2] / "schemas/third-party-upstream.schema.json"


def test_vendored_subtree_absent_from_sbom_fails(tmp_path: Path) -> None:
    write_fixture_tree(tmp_path, SCHEMA)
    registrations = validate_tree(tmp_path)
    with pytest.raises(GovernanceError, match="SBOM missing vendored subtree"):
        validate_sbom_coverage(registrations, {"components": []})
