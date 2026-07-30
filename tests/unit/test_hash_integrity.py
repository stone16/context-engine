from __future__ import annotations

from pathlib import Path

import pytest

from scripts.third_party_governance import GovernanceError, validate_tree
from tests.unit._third_party_governance_fixtures import write_fixture_tree

SCHEMA = Path(__file__).parents[2] / "schemas/third-party-upstream.schema.json"


def test_mutating_one_vendored_byte_fails_hash_check(tmp_path: Path) -> None:
    write_fixture_tree(tmp_path, SCHEMA)
    vendored = tmp_path / "third_party/example/src/example.py"
    vendored.write_bytes(vendored.read_bytes()[:-1] + b"X")
    with pytest.raises(GovernanceError, match="hash mismatch"):
        validate_tree(tmp_path)
