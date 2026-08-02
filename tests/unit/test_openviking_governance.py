from __future__ import annotations

import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OPENVIKING_SHA = "49b182045b42d34ad530948ad77d9d0226897da8"
BASELINE_PATH = "docs/research/2026-08-02-five-public-repositories-evidence.md"
ROOM_A_PATH = "docs/research/2026-07-31-openviking-blueprint-evaluation.md"
DOSSIER_PATH = "docs/research/2026-08-02-openviking-legal-review-dossier.md"
BLUEPRINT_PATH = "docs/research/2026-07-31-five-repository-implementation-blueprint.md"
OPENVIKING_URL = re.compile(
    r"https://github\.com/volcengine/OpenViking/(?:blob|tree)/([^/)]+)/"
)


def _read(path: str) -> str:
    return (REPOSITORY_ROOT / path).read_text(encoding="utf-8")


def _collapse_whitespace(value: str) -> str:
    return " ".join(value.split())


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
    dossier = _read(DOSSIER_PATH)

    assert "**no OpenViking copy+patch**" in baseline
    assert "PREPARED_AWAITING_MAINTAINER" in dossier
    assert "Maintainer/legal decision recorded" in dossier
    assert "- [ ] Maintainer/legal decision recorded." in dossier


def test_openviking_evidence_claims_match_the_pinned_snapshot() -> None:
    baseline = _read(BASELINE_PATH)
    normalized_baseline = _collapse_whitespace(baseline)
    blueprint = _read(BLUEPRINT_PATH)

    assert "`RetrievalResult`" not in baseline
    assert "[`MatchedContext` and `QueryResult` types]" in baseline
    assert "openviking_cli/retrieve/types.py#L283-L319" in baseline
    assert "[`ls` API shape]" in baseline
    assert "`ls`, `tree`, and read API shape" not in baseline
    assert (
        "candidate-level `skip`, `create`, and `none` decisions"
        in normalized_baseline
    )
    assert (
        "per-existing-item `merge` and `delete` decisions"
        in normalized_baseline
    )
    assert (
        "- [ ] OpenViking 准入前，README/PLAN/STATUS/设计文档不出现 "
        "OpenViking 作为 authority 的引用。"
    ) in blueprint
    assert (
        "- [x] OpenViking 公开 authority 只通过版本化五仓基线的固定四类"
        "白名单；Room-A 报告不作为 provenance。"
    ) in blueprint


def test_prepared_agents_amendment_is_complete_but_not_applied() -> None:
    agents = _read("AGENTS.md")
    dossier = _read(DOSSIER_PATH)

    assert "2026-08-02-five-public-repositories-evidence.md" not in agents
    assert "Apply all three replacements atomically" in dossier
    assert "2026-07-19-four-public-repositories-evidence.md" in dossier
    assert "2026-08-02-five-public-repositories-evidence.md" in dossier
    assert "all OpenViking source regions remain clean-room only" in dossier
    assert "five-repository evidence baseline" in dossier


def test_legal_dossier_records_the_single_author_constraint() -> None:
    dossier = _read(DOSSIER_PATH)
    normalized_dossier = _collapse_whitespace(dossier)

    assert (
        "one unique author email under two display names" in normalized_dossier
    )
    assert "personnel separation cannot be claimed" in normalized_dossier
    assert "documentary and temporal separation" in normalized_dossier
