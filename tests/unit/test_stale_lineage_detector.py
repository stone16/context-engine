from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from applications.eval_v1 import main
from engine.learning.eval_run import _lineage_ref
from engine.learning.golden import (
    EvidenceLineage,
    GoldenSet,
    create_golden_lock,
    load_golden_set,
)
from engine.learning.golden_storage import GOLDEN_ROOT_ENV
from engine.learning.judges import RetrievalCaseInput, judge_retrieval
from engine.learning.lineage import (
    LineageMapUnavailable,
    LineageResolutionReport,
    StaleGoldenLineage,
    detect_stale_lineage,
    load_lineage_map,
    require_resolved_lineage,
    scorable_cases,
)
from tests.support.golden import evidence, golden_case, valid_composed_entries
from tests.support.golden_backup import (
    expected_lineages,
    golden_document,
    lineage_document,
    stage_corpus,
    write_json,
)

PROMOTED_REVISION = "synthetic-revision-dev-00-promoted"


@pytest.fixture(autouse=True)
def _durable_golden_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GOLDEN_ROOT_ENV, str(tmp_path))


def _promoted_lineages(entries: list[dict[str, object]]) -> list[dict[str, str]]:
    """The lineage a promoted Release resolves: dev-00 has a new Revision."""

    promoted = []
    for lineage in expected_lineages(entries):
        if lineage["resourceRef"] == "synthetic-resource-dev-00":
            promoted.append({**lineage, "revisionRef": PROMOTED_REVISION})
        else:
            promoted.append(lineage)
    return promoted


def _resolution(
    tmp_path: Path,
    lineages: list[dict[str, str]],
) -> tuple[GoldenSet, LineageResolutionReport]:
    corpus = stage_corpus(tmp_path / "corpus")
    write_json(corpus.lineage_map_path, lineage_document(lineages))
    golden_set = load_golden_set(corpus.golden_path, lock_path=corpus.lock_path)
    return golden_set, detect_stale_lineage(
        golden_set,
        load_lineage_map(corpus.lineage_map_path),
    )


def test_a_current_lineage_map_resolves_every_expected_case(tmp_path: Path) -> None:
    golden_set, resolution = _resolution(
        tmp_path,
        expected_lineages(valid_composed_entries()),
    )

    require_resolved_lineage(resolution)
    assert resolution.status == "resolved"
    assert resolution.stale_cases == ()
    assert resolution.resolved_case_count == len(golden_set.cases)


def test_promoted_refs_are_reported_as_stale_lineage_not_as_retrieval_misses(
    tmp_path: Path,
) -> None:
    entries = valid_composed_entries()
    golden_set, resolution = _resolution(tmp_path, _promoted_lineages(entries))
    stale = next(case for case in golden_set.cases if case.case_ref == "dev-00")
    expected_refs = frozenset(
        _lineage_ref(item.lineage) for item in stale.expected_evidence
    )
    promoted_refs = frozenset(
        _lineage_ref(
            EvidenceLineage(
                source_ref=item.lineage.source_ref,
                resource_ref=item.lineage.resource_ref,
                revision_ref=PROMOTED_REVISION,
                fragment_ref=item.lineage.fragment_ref,
            )
        )
        for item in stale.expected_evidence
    )

    naive = judge_retrieval(
        (RetrievalCaseInput("dev-00", expected_refs, promoted_refs),)
    )
    scorable = scorable_cases(golden_set, resolution)

    assert promoted_refs != expected_refs
    assert naive.cases[0].evidence_recall == 0.0
    assert naive.cases[0].case_hit is False
    assert resolution.status == "stale_lineage"
    assert resolution.stale_case_refs == ("dev-00",)
    assert resolution.stale_cases[0].unresolved_count == 1
    assert "dev-00" not in {case.case_ref for case in scorable}
    assert len(scorable) == len(golden_set.cases) - 1


def test_a_stale_set_refuses_scoring_rather_than_reporting_a_lower_number(
    tmp_path: Path,
) -> None:
    _, resolution = _resolution(tmp_path, _promoted_lineages(valid_composed_entries()))

    with pytest.raises(StaleGoldenLineage, match="cases=1 of 70"):
        require_resolved_lineage(resolution)


def test_unanswerable_cases_never_go_stale(tmp_path: Path) -> None:
    entries = valid_composed_entries()
    golden_set, resolution = _resolution(tmp_path, _promoted_lineages(entries))
    unanswerable = {
        case.case_ref
        for case in golden_set.cases
        if case.answerability == "unanswerable"
    }

    assert unanswerable
    assert not unanswerable & set(resolution.stale_case_refs)


def test_a_partially_resolvable_case_is_stale_rather_than_partially_scored(
    tmp_path: Path,
) -> None:
    entries = valid_composed_entries()
    first = cast(list[dict[str, str]], entries[0]["expectedEvidence"])
    first.append(evidence("dev-00-second"))
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir()
    golden_path = corpus_root / "golden-v1.json"
    lock_path = corpus_root / "golden-v1.lock.json"
    map_path = corpus_root / "lineage-map.json"
    write_json(golden_path, golden_document(entries, "synthetic"))
    create_golden_lock(
        load_golden_set(golden_path, allow_unlocked_pilot_for_initial_lock=True),
        lock_path,
        authority="maintainer",
        reason="synthetic-staged-lock",
        recorded_at=datetime(2026, 7, 29, tzinfo=UTC),
    )
    lineages = [
        lineage
        for lineage in expected_lineages(entries)
        if lineage["resourceRef"] != "synthetic-resource-dev-00-second"
    ]
    write_json(map_path, lineage_document(lineages))

    resolution = detect_stale_lineage(
        load_golden_set(golden_path, lock_path=lock_path),
        load_lineage_map(map_path),
    )

    assert resolution.stale_case_refs == ("dev-00",)
    assert resolution.stale_cases[0].expected_count == 2
    assert resolution.stale_cases[0].unresolved_count == 1


def test_a_resolution_from_another_set_cannot_select_scorable_cases(
    tmp_path: Path,
) -> None:
    golden_set, resolution = _resolution(
        tmp_path,
        expected_lineages(valid_composed_entries()),
    )
    other = load_golden_set(
        _write_other_set(tmp_path),
        allow_unlocked_pilot_for_initial_lock=True,
    )

    with pytest.raises(StaleGoldenLineage, match="different set"):
        scorable_cases(other, resolution)

    assert other.digest != golden_set.digest


def _write_other_set(tmp_path: Path) -> Path:
    entries = valid_composed_entries()
    entries.append(golden_case("dev-20"))
    path = tmp_path / "other-golden-v1.json"
    write_json(path, golden_document(entries, "synthetic"))
    return path


@pytest.mark.parametrize(
    "mutation",
    (
        {"schemaVersion": "context-engine-golden-lineage-map-v0"},
        {"entries": []},
        {"releaseRef": " "},
        {"capturedAt": "2026-07-29T00:00:00"},
    ),
)
def test_a_malformed_lineage_map_is_refused_as_a_whole(
    tmp_path: Path,
    mutation: dict[str, object],
) -> None:
    corpus = stage_corpus(tmp_path / "corpus")
    document = json.loads(corpus.lineage_map_path.read_text(encoding="utf-8"))
    document.update(mutation)
    write_json(corpus.lineage_map_path, document)

    with pytest.raises(LineageMapUnavailable):
        load_lineage_map(corpus.lineage_map_path)


def test_the_cli_refuses_a_report_whose_lineage_no_longer_resolves(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    corpus = stage_corpus(tmp_path / "corpus")
    write_json(
        corpus.lineage_map_path,
        lineage_document(_promoted_lineages(corpus.entries)),
    )
    run_path = corpus.source_root / "run.json"
    write_json(run_path, {"schemaVersion": "context-engine-eval-run-v1"})
    output_path = tmp_path / ".context-engine/eval/report.json"

    with pytest.raises(SystemExit) as error:
        main(
            [
                "report",
                "--golden-set",
                str(corpus.golden_path),
                "--lock",
                str(corpus.lock_path),
                "--run",
                str(run_path),
                "--lineage-map",
                str(corpus.lineage_map_path),
                "--output",
                str(output_path),
                "--generated-at",
                "2026-07-29T12:00:00Z",
            ]
        )

    assert error.value.code == 1
    assert "golden lineage is stale" in capsys.readouterr().err
    assert not output_path.exists()


def test_the_cli_lineage_check_reports_counts_without_any_lineage_value(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    corpus = stage_corpus(tmp_path / "corpus")
    write_json(
        corpus.lineage_map_path,
        lineage_document(_promoted_lineages(corpus.entries)),
    )

    with pytest.raises(SystemExit) as error:
        main(
            [
                "lineage-check",
                "--golden-set",
                str(corpus.golden_path),
                "--lock",
                str(corpus.lock_path),
                "--lineage-map",
                str(corpus.lineage_map_path),
            ]
        )

    captured = capsys.readouterr()
    assert error.value.code == 1
    assert "cases=1 of 70" in captured.err
    assert PROMOTED_REVISION not in captured.err + captured.out
    assert "synthetic-resource-dev-00" not in captured.err + captured.out


def test_the_cli_lineage_check_passes_on_a_recaptured_map(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    corpus = stage_corpus(tmp_path / "corpus")

    main(
        [
            "lineage-check",
            "--golden-set",
            str(corpus.golden_path),
            "--lock",
            str(corpus.lock_path),
            "--lineage-map",
            str(corpus.lineage_map_path),
        ]
    )

    assert "golden lineage resolved: 70 cases" in capsys.readouterr().out
