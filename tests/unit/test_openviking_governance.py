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


def _collapse_blockquote(value: str) -> str:
    return _collapse_whitespace(value.replace(">", ""))


def _amendment_pair(dossier: str, number: int) -> tuple[str, str]:
    heading = f"### Pair {number} "
    section = dossier.split(heading, maxsplit=1)[1]
    if number < 3:
        section = section.split(f"### Pair {number + 1} ", maxsplit=1)[0]

    current, prepared = section.split("Prepared replacement:", maxsplit=1)
    return (
        _collapse_blockquote(current.split("Current text:", maxsplit=1)[1]),
        _collapse_blockquote(prepared),
    )


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
    normalized_agents = _collapse_whitespace(agents)

    expected_pairs = (
        (
            "`CONTEXT.md` (glossary). Public reference claims must trace to "
            "`docs/research/2026-07-19-four-public-repositories-evidence.md` "
            "or first-party ContextEngine requirements and "
            "`docs/security/context-engine-threat-model.md`.",
            "`CONTEXT.md` (glossary). Public reference claims must trace to "
            "`docs/research/2026-08-02-five-public-repositories-evidence.md` "
            "or first-party ContextEngine requirements and "
            "`docs/security/context-engine-threat-model.md`.",
        ),
        (
            "**Controlled third-party reuse (ADR-0074)** — copying is permitted "
            "only from license-verified permissive regions at pinned commits "
            "(RAGFlow Apache-2.0; Onyx outside every `ee/` directory, MIT; "
            "separately-licensed MIT SDK subtrees), registered under "
            "`third_party/` with full attribution and SBOM coverage in shipped "
            "artifacts. Dify root-licensed code, MaxKB GPLv3 code, and Onyx "
            "`ee/` code remain clean-room only: behavior observations, interface "
            "shapes, and test oracles via the two-room protocol. Every public "
            "reference claim still traces through the four-repository evidence "
            "report; repository-external research inputs must never be cited, "
            "linked, or presented as public provenance.",
            "**Controlled third-party reuse (ADR-0074)** — copying is permitted "
            "only from license-verified permissive regions at pinned commits "
            "(RAGFlow Apache-2.0; Onyx outside every `ee/` directory, MIT; "
            "separately-licensed MIT SDK subtrees), registered under "
            "`third_party/` with full attribution and SBOM coverage in shipped "
            "artifacts. Dify root-licensed code, MaxKB GPLv3 code, Onyx `ee/` "
            "code, and all OpenViking source regions remain clean-room only: "
            "behavior observations, interface shapes, and test oracles through "
            "the applicable maintainer-approved clean-room protocol. D9 "
            "additionally prohibits every OpenViking copy+patch path even if "
            "upstream later clarifies a permissive region. Every public "
            "prior-art reference claim still traces through the five-repository "
            "evidence baseline; ContextEngine's own claims trace to its "
            "first-party requirements and threat model. Repository-external "
            "research inputs must never be cited, linked, or presented as public "
            "provenance.",
        ),
        (
            "Repository-external research may inform independent reasoning, "
            "but it is neither public authority nor publishable provenance.",
            "Repository-external research may inform independent reasoning and "
            "may be tracked under `docs/research/` as maintainer-local input, "
            "but it is never citable as public authority or claim provenance; "
            "public prior-art reference claims still trace only to the versioned "
            "repository-evidence baseline, while ContextEngine's own claims "
            "trace to first-party requirements and the threat model.",
        ),
    )

    assert "2026-08-02-five-public-repositories-evidence.md" not in agents
    assert "Apply all three replacements atomically" in dossier
    for number, (expected_current, expected_prepared) in enumerate(
        expected_pairs, start=1
    ):
        current, prepared = _amendment_pair(dossier, number)
        assert current == expected_current
        assert prepared.startswith(expected_prepared)
        assert expected_current in normalized_agents
        assert expected_prepared not in normalized_agents


def test_legal_dossier_records_the_single_author_constraint() -> None:
    dossier = _read(DOSSIER_PATH)
    normalized_dossier = _collapse_whitespace(dossier)

    assert (
        "one unique author email under two display names" in normalized_dossier
    )
    assert "personnel separation cannot be claimed" in normalized_dossier
    assert "documentary and temporal separation" in normalized_dossier
    assert "That attribution does not prove who performed the work" in dossier
