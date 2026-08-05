from __future__ import annotations

import re
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OPENVIKING_SHA = "49b182045b42d34ad530948ad77d9d0226897da8"
BASELINE_PATH = "docs/research/2026-08-02-five-public-repositories-evidence.md"
BASELINE_NAME = BASELINE_PATH.rsplit("/", maxsplit=1)[1]
PREDECESSOR_PATH = "docs/research/2026-07-19-four-public-repositories-evidence.md"
ROOM_A_PATH = "docs/research/2026-07-31-openviking-blueprint-evaluation.md"
DOSSIER_PATH = "docs/research/2026-08-02-openviking-legal-review-dossier.md"
BLUEPRINT_PATH = "docs/research/2026-07-31-five-repository-implementation-blueprint.md"
OPENVIKING_URL = re.compile(
    r"https://github\.com/volcengine/OpenViking/(?:blob|tree)/([^/)]+)/"
)
OPENVIKING_SOURCE_URL = re.compile(
    r"https://github\.com/volcengine/OpenViking/(?:blob|tree)/"
    rf"{OPENVIKING_SHA}/[^)]+"
)
EXACT_LINE_RANGE = re.compile(r"#L(?P<start>\d+)-L(?P<end>\d+)$")
UPSTREAM_CLAIM_BLOCK = re.compile(
    r"^### 上游路径与(?:可观察行为|核验结果|本仓 thesis)\n\n"
    r"(?P<claim>.*?)(?=\n\n### )",
    re.MULTILINE | re.DOTALL,
)
EXPECTED_REPOSITORY_SNAPSHOTS = {
    "langgenius/dify": "120c38bad8d27cbe1e6a1d5522fd66f5caf6d0d5",
    "infiniflow/ragflow": "4391e03886b996201f3b8818f671b19eb24d0f7b",
    "1Panel-dev/MaxKB": "32b2d885e47ad04639abd7a18490bf5937f9c072",
    "onyx-dot-app/onyx": "2fb3dd10493b3883870fa8adced5b1a0e114feff",
    "volcengine/OpenViking": OPENVIKING_SHA,
}
EXPLICIT_NON_AUTHORITY_PATTERNS = (
    re.compile(
        r"\bopenviking\b"
        r"(?:(?!\bopenviking\b|[|。.!?;；]).){0,120}?"
        r"\b(?:is|remains)\s+"
        r"(?:a\s+)?(?:non-authoritative|not (?:public )?authorit(?:y|ative))\b"
    ),
    re.compile(r"\bdoes not cite\s+openviking\s+as\s+authority\b"),
    re.compile(r"不把\s+openviking\s+引作\s+authority\b"),
)
AUTHORITY_TERM = re.compile(r"\bauthorit(?:y|ative)\b")
PRONOUN_AUTHORITY_CLAIM = re.compile(
    r"\b(?:it|the candidate|this candidate)\b[^|。.!?;；]{0,120}"
    r"\bauthorit(?:y|ative)\b"
)


def _read(path: str) -> str:
    return (REPOSITORY_ROOT / path).read_text(encoding="utf-8")


def _collapse_whitespace(value: str) -> str:
    return " ".join(value.split())


def _collapse_blockquote(value: str) -> str:
    return _collapse_whitespace(value.replace(">", ""))


def _has_exact_positive_line_range(url: str) -> bool:
    match = EXACT_LINE_RANGE.search(url)
    if match is None:
        return False
    start, end = (int(match.group(name)) for name in ("start", "end"))
    return 0 < start <= end


def _assert_openviking_paragraph_is_non_authoritative(
    paragraph: str, path: Path
) -> None:
    normalized_paragraph = _collapse_whitespace(paragraph).lower()
    assert "#205" in normalized_paragraph, (path, normalized_paragraph)
    matched_boundary = any(
        pattern.search(normalized_paragraph)
        for pattern in EXPLICIT_NON_AUTHORITY_PATTERNS
    )
    assert matched_boundary, (path, normalized_paragraph)

    for clause in re.split(r"[|。.!?;；]+", normalized_paragraph):
        if "openviking" not in clause:
            continue
        residual = clause
        for pattern in EXPLICIT_NON_AUTHORITY_PATTERNS:
            residual = pattern.sub("", residual)
        assert AUTHORITY_TERM.search(residual) is None, (path, normalized_paragraph)
    assert PRONOUN_AUTHORITY_CLAIM.search(normalized_paragraph) is None, (
        path,
        normalized_paragraph,
    )


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
        "docs/decisions/0016-implementation-authority-and-vertical-slice-roadmap.md",
    )

    for path in authority_paths:
        document = _read(path)
        assert BASELINE_PATH in document, path
        assert ROOM_A_PATH not in document, path


def test_five_repository_baseline_is_an_explicit_versioned_successor() -> None:
    baseline = _read(BASELINE_PATH)

    assert "> Version: 2.0.0" in baseline
    predecessor_name = PREDECESSOR_PATH.rsplit("/", maxsplit=1)[1]
    assert f"> Supersedes: [`{predecessor_name}`]" in baseline
    for repository, snapshot in EXPECTED_REPOSITORY_SNAPSHOTS.items():
        assert f"https://github.com/{repository}/commit/{snapshot}" in baseline
    admitted_rows = {
        "Dify": EXPECTED_REPOSITORY_SNAPSHOTS["langgenius/dify"],
        "RAGFlow": EXPECTED_REPOSITORY_SNAPSHOTS["infiniflow/ragflow"],
        "MaxKB": EXPECTED_REPOSITORY_SNAPSHOTS["1Panel-dev/MaxKB"],
        "Onyx": EXPECTED_REPOSITORY_SNAPSHOTS["onyx-dot-app/onyx"],
    }
    for repository, snapshot in admitted_rows.items():
        row = next(
            line
            for line in baseline.splitlines()
            if line.startswith(f"| {repository} |")
        )
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        assert cells[0] == repository
        assert f"[`{snapshot}`]" in cells[1]
        assert cells[2] == "Admitted"
    openviking_row = next(
        line for line in baseline.splitlines() if line.startswith("| OpenViking |")
    )
    openviking_cells = [
        cell.strip() for cell in openviking_row.strip("|").split("|")
    ]
    assert openviking_cells[2] == (
        "Candidate; not public authority while `#205` is open"
    )
    assert (
        "## 3. OpenViking candidate packet "
        "(non-authoritative while `#205` is open)" in baseline
    )


def test_synthesis_separates_admitted_inputs_from_candidate_observations() -> None:
    baseline = _read(BASELINE_PATH)
    expected_candidate_cells = {
        "Product/Control UX": "OpenViking browse/trajectory UX",
        "Document compilation": "—",
        "Supply/freshness": "—",
        "Retrieval/assembly": "OpenViking density tiering",
        "Curation/Learning": "OpenViking session candidate UX",
        "Delivery/exposure": "OpenViking agent lifecycle UX",
    }

    assert (
        "| ContextEngine area | Admitted public reference input | "
        "Non-authoritative candidate observation | Boundary ContextEngine must own |"
        in baseline
    )
    for area, candidate in expected_candidate_cells.items():
        row = next(
            line for line in baseline.splitlines() if line.startswith(f"| {area} |")
        )
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        assert len(cells) == 4, row
        assert cells[0] == area
        if candidate == "—":
            assert cells[2] == candidate
        else:
            assert cells[2] == (
                f"{candidate}; candidate and non-authoritative while `#205` is open"
            )
        assert "OpenViking" not in cells[1]


def test_public_docs_keep_openviking_non_authoritative_until_issue_205_closes() -> None:
    baseline_paths = (
        Path("CONTRIBUTING.md"),
        Path("README.md"),
        Path("README.zh-CN.md"),
        Path("PLAN.md"),
        Path("STATUS.md"),
        Path("docs/agents/prd-contextengine-implementation.md"),
        Path("docs/decisions/0016-implementation-authority-and-vertical-slice-roadmap.md"),
        Path("docs/design/2026-07-18-context-engine-implementation-design.md"),
        Path("docs/design/2026-07-26-repo-state-review-and-course-correction.md"),
        Path("docs/security/Test-Architecture-与可验证性设计.md"),
        Path("docs/security/安全负向测试清单.md"),
        Path("docs/specs/2026-07-19-context-engine-implementation-epic.md"),
    )
    public_paths = (
        *baseline_paths,
        *sorted(Path("docs/design").rglob("*.md")),
    )
    forbidden_authority_phrases = (
        "conditionally admitted openviking",
        "openviking claim families",
        "draws on architectural study of five",
        "allowlisted to dify, ragflow, maxkb, onyx, and openviking",
    )

    for path in baseline_paths:
        assert BASELINE_NAME in _read(path.as_posix()), path

    for path in dict.fromkeys(public_paths):
        document = _read(path.as_posix())
        normalized_document = _collapse_whitespace(document).lower()
        assert (
            "https://github.com/volcengine/openviking" not in normalized_document
        ), path
        assert ROOM_A_PATH not in document, path
        for phrase in forbidden_authority_phrases:
            assert phrase not in normalized_document, (path, phrase)

        openviking_paragraphs = (
            _collapse_whitespace(paragraph).lower()
            for paragraph in re.split(r"\n\s*\n", document)
            if "openviking" in paragraph.lower()
        )
        for paragraph in openviking_paragraphs:
            assert BASELINE_NAME in document, path
            _assert_openviking_paragraph_is_non_authoritative(paragraph, path)


@pytest.mark.parametrize(
    "paragraph",
    (
        (
            "openviking is not authority while #205 is open. "
            "openviking is authoritative."
        ),
        (
            "openviking remains non-authoritative while #205 is open. "
            "we cite openviking as authority."
        ),
        (
            "openviking is authority but openviking is not authority "
            "while #205 is open."
        ),
        (
            "openviking is not authority while #205 is open. "
            "it is authoritative."
        ),
        (
            "openviking is not authority while #205 is open. "
            "we cite openviking as public authority."
        ),
        (
            "openviking is not authority while #205 is open, but "
            "openviking serves as authority."
        ),
        (
            "openviking is not authority while #205 is open. "
            "openviking supplies authoritative evidence."
        ),
        "openviking is a candidate and remains not_active while #205 is open.",
        "openviking is non-authoritative until the admission issue closes.",
    ),
)
def test_openviking_paragraph_guard_rejects_incomplete_boundaries(
    paragraph: str,
) -> None:
    with pytest.raises(AssertionError):
        _assert_openviking_paragraph_is_non_authoritative(
            paragraph, Path("adversarial.md")
        )


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


def test_room_a_claim_permalinks_pin_exact_source_regions() -> None:
    room_a = _read(ROOM_A_PATH)
    source_urls = OPENVIKING_SOURCE_URL.findall(room_a)

    assert source_urls
    assert all(_has_exact_positive_line_range(url) for url in source_urls)

    claim_blocks = UPSTREAM_CLAIM_BLOCK.findall(room_a)
    assert len(claim_blocks) == 8
    for claim in claim_blocks:
        claim_urls = OPENVIKING_SOURCE_URL.findall(claim)
        assert claim_urls, claim
        assert all(_has_exact_positive_line_range(url) for url in claim_urls)

    capability_table = room_a.split(
        "# 2. 能力盘点 → ContextEngine 区域映射表", maxsplit=1
    )[1].split("# 3. 逐能力蓝图", maxsplit=1)[0]
    capability_rows = [
        line
        for line in capability_table.splitlines()
        if line.startswith("|")
        and "OpenViking 能力" not in line
        and not set(line.replace("|", "").strip()) <= {"-"}
    ]
    assert len(capability_rows) == 9
    for row in capability_rows:
        row_urls = OPENVIKING_SOURCE_URL.findall(row)
        assert row_urls, row
        assert all(_has_exact_positive_line_range(url) for url in row_urls)


@pytest.mark.parametrize("line_range", ("#L0-L0", "#L20-L1", "#L1-L0"))
def test_exact_source_region_rejects_invalid_line_ranges(line_range: str) -> None:
    assert not _has_exact_positive_line_range(f"https://example.test/file{line_range}")


def test_exact_source_region_accepts_ordered_positive_line_range() -> None:
    assert _has_exact_positive_line_range("https://example.test/file#L1-L20")


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
