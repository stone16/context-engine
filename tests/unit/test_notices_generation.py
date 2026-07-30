from __future__ import annotations

from pathlib import Path

import pytest

from scripts.third_party_governance import GovernanceError, generate_aggregates
from tests.unit._third_party_governance_fixtures import write_fixture_tree

SCHEMA = Path(__file__).parents[2] / "schemas/third-party-upstream.schema.json"


def test_notice_check_detects_committed_drift(tmp_path: Path) -> None:
    write_fixture_tree(tmp_path, SCHEMA)
    generate_aggregates(root=tmp_path, check=False)
    notice = tmp_path / "THIRD_PARTY_NOTICES.md"
    notice.write_text(
        notice.read_text(encoding="utf-8") + "hand edit\n", encoding="utf-8"
    )
    with pytest.raises(
        GovernanceError, match="generated file drift: THIRD_PARTY_NOTICES.md"
    ):
        generate_aggregates(root=tmp_path, check=True)
