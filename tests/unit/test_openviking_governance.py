from __future__ import annotations

import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OPENVIKING_SHA = "49b182045b42d34ad530948ad77d9d0226897da8"
BASELINE_PATH = "docs/research/2026-08-02-five-public-repositories-evidence.md"
ROOM_A_PATH = "docs/research/2026-07-31-openviking-blueprint-evaluation.md"
OPENVIKING_URL = re.compile(
    r"https://github\.com/volcengine/OpenViking/(?:blob|tree)/([^/)]+)/"
)


def _read(path: str) -> str:
    return (REPOSITORY_ROOT / path).read_text(encoding="utf-8")


def test_current_public_authority_uses_the_versioned_five_repository_baseline() -> (
    None
):
    authority_paths = (
        "CONTEXT.md",
        "PLAN.md",
        "STATUS.md",
        "docs/design/2026-07-18-context-engine-implementation-design.md",
    )

    for path in authority_paths:
        document = _read(path)
        assert BASELINE_PATH in document, path
        assert ROOM_A_PATH not in document, path


def test_openviking_public_evidence_uses_only_the_admitted_snapshot() -> None:
    evidence_paths = (
        BASELINE_PATH,
        "docs/research/2026-08-02-openviking-legal-review-dossier.md",
        ROOM_A_PATH,
    )

    for path in evidence_paths:
        snapshots = OPENVIKING_URL.findall(_read(path))
        assert snapshots, path
        assert set(snapshots) == {OPENVIKING_SHA}, path


def test_openviking_copy_and_legal_authority_remain_closed() -> None:
    baseline = _read(BASELINE_PATH)
    dossier = _read(
        "docs/research/2026-08-02-openviking-legal-review-dossier.md"
    )

    assert "**no OpenViking copy+patch**" in baseline
    assert "PREPARED_AWAITING_MAINTAINER" in dossier
    assert "Maintainer/legal decision recorded" in dossier
    assert "- [ ] Maintainer/legal decision recorded." in dossier
