from __future__ import annotations

import importlib
from typing import Any

import pytest

from applications import embedding_benchmark as cli
from eval.embedding_benchmark import BenchmarkUnavailable, RetrievalJudgeCase


def _synthetic_cases() -> tuple[RetrievalJudgeCase, ...]:
    return (
        RetrievalJudgeCase(
            case_ref="synthetic-partial",
            expected_evidence=("doc-alpha", "doc-beta"),
            retrieved_evidence=("doc-alpha", "doc-noise"),
            slice_name="multi_doc",
        ),
        RetrievalJudgeCase(
            case_ref="synthetic-hit",
            expected_evidence=("doc-gamma",),
            retrieved_evidence=("doc-gamma",),
            slice_name="single_doc",
        ),
    )


def test_fixed_retrieval_judge_seam_loads_and_judges_deterministically() -> None:
    judge = cli.load_retrieval_judge()

    first = judge.evaluate_retrieval(_synthetic_cases())
    second = judge.evaluate_retrieval(_synthetic_cases())

    assert first == second
    assert first.case_hit.hits == 1
    assert first.case_hit.total_cases == 2
    assert first.case_hit.value == pytest.approx(0.5)
    assert first.evidence_recall.macro.value == pytest.approx(0.75)
    assert first.evidence_recall.micro.hits == 2
    assert first.evidence_recall.micro.total_expected == 3
    assert first.evidence_recall.micro.value == pytest.approx(2 / 3)
    assert first.per_slice["multi_doc"].evidence_recall.macro.value == 0.5
    assert first.per_slice["single_doc"].case_hit.value == 1.0


def test_loader_fails_closed_when_the_fixed_judge_is_broken(
    monkeypatch: Any,
) -> None:
    class BrokenModule:
        @staticmethod
        def create_judge() -> object:
            return object()

    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda _module_name: BrokenModule,
    )

    with pytest.raises(BenchmarkUnavailable, match="judge is unavailable"):
        cli.load_retrieval_judge()
