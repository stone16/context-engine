from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, cast

import pytest

from eval.embedding_benchmark import (
    EMBEDDING_DIMENSION,
    BenchmarkCase,
    BenchmarkDataset,
    BenchmarkDocument,
    BenchmarkUnavailable,
    CaseHitMetric,
    EvidenceRecallMetric,
    MacroRecallMetric,
    MicroRecallMetric,
    ModelIdentity,
    RetrievalJudgeCase,
    RetrievalMetrics,
    SliceMetrics,
    compare_model_metrics,
    run_benchmark,
)


def _identity(model_id: str) -> ModelIdentity:
    return ModelIdentity(
        model_id=model_id,
        revision="a" * 40,
        artifact_digest=("b" if model_id.startswith("Qwen") else "c") * 64,
        dimension=EMBEDDING_DIMENSION,
        normalization="l2",
        pooling="mean",
        query_prefix="query: ",
        document_prefix="passage: ",
        reduction=(
            "matryoshka_truncate_384"
            if model_id.startswith("Qwen")
            else "none_native_384"
        ),
        precision="float32",
        batch_size=8,
    )


@dataclass
class SyntheticProvider:
    identity: ModelIdentity

    def embed_queries(self, values: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(_vector(value) for value in values)

    def embed_documents(
        self, values: tuple[str, ...]
    ) -> tuple[tuple[float, ...], ...]:
        return tuple(_vector(value) for value in values)


def _vector(value: str) -> tuple[float, ...]:
    first = 1.0 if "alpha" in value else 0.0
    second = 1.0 if "beta" in value else 0.0
    third = 1.0 if first == second == 0.0 else 0.0
    return (first, second, third, *(0.0 for _ in range(EMBEDDING_DIMENSION - 3)))


class RecordingJudge:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def evaluate_retrieval(
        self, cases: tuple[RetrievalJudgeCase, ...]
    ) -> RetrievalMetrics:
        self.calls.append(cases)
        # Hand-checked oracle: recalls 1/2 and 1/1 => macro .75, micro 2/3.
        return RetrievalMetrics(
            case_hit=CaseHitMetric(hits=2, total_cases=2, value=1.0),
            evidence_recall=EvidenceRecallMetric(
                macro=MacroRecallMetric(value=0.75),
                micro=MicroRecallMetric(hits=2, total_expected=3, value=2 / 3),
            ),
            per_slice={
                "single_doc": SliceMetrics(
                    case_hit=CaseHitMetric(hits=2, total_cases=2, value=1.0),
                    evidence_recall=EvidenceRecallMetric(
                        macro=MacroRecallMetric(value=0.75),
                        micro=MicroRecallMetric(
                            hits=2,
                            total_expected=3,
                            value=2 / 3,
                        ),
                    ),
                )
            },
        )


def _dataset() -> BenchmarkDataset:
    return BenchmarkDataset(
        documents=(
            BenchmarkDocument(document_ref="doc-alpha", text="alpha"),
            BenchmarkDocument(document_ref="doc-beta", text="beta"),
            BenchmarkDocument(document_ref="doc-neither", text="neither"),
        ),
        cases=(
            BenchmarkCase(
                case_ref="case-alpha",
                query="alpha",
                expected_document_refs=("doc-alpha", "doc-neither"),
                slice_name="single_doc",
            ),
            BenchmarkCase(
                case_ref="case-beta",
                query="beta",
                expected_document_refs=("doc-beta",),
                slice_name="single_doc",
            ),
        ),
    )


def test_runner_delegates_metrics_to_the_injected_129_retrieval_judge() -> None:
    judge = RecordingJudge()

    report = run_benchmark(
        dataset=_dataset(),
        primary=SyntheticProvider(_identity("Qwen/Qwen3-Embedding-0.6B")),
        baseline=SyntheticProvider(_identity("intfloat/multilingual-e5-small")),
        judge=judge,
        top_k=2,
        clock=lambda: 1.0,
    )

    assert len(judge.calls) == 2
    first_cases = judge.calls[0]
    assert isinstance(first_cases, tuple)
    assert first_cases[0].expected_evidence == ("doc-alpha", "doc-neither")
    assert first_cases[0].retrieved_evidence == ("doc-alpha", "doc-beta")
    models = cast(dict[str, Any], report["models"])
    assert models["primary"]["metrics"]["evidenceRecall"] == {
        "macro": {"value": 0.75},
        "micro": {"hits": 2, "totalExpected": 3, "value": 2 / 3},
    }


def test_primary_loss_is_recorded_as_a_valid_result() -> None:
    class LosingJudge(RecordingJudge):
        def evaluate_retrieval(
            self, cases: tuple[RetrievalJudgeCase, ...]
        ) -> RetrievalMetrics:
            result = super().evaluate_retrieval(cases)
            if len(self.calls) == 1:
                return RetrievalMetrics(
                    case_hit=CaseHitMetric(hits=0, total_cases=2, value=0.0),
                    evidence_recall=result.evidence_recall,
                    per_slice=result.per_slice,
                )
            return result

    report = run_benchmark(
        dataset=_dataset(),
        primary=SyntheticProvider(_identity("Qwen/Qwen3-Embedding-0.6B")),
        baseline=SyntheticProvider(_identity("intfloat/multilingual-e5-small")),
        judge=LosingJudge(),
        top_k=2,
        clock=lambda: 1.0,
    )

    assert cast(dict[str, Any], report["comparison"])[
        "primaryAgainstModelBaseline"
    ] == "lose"


@pytest.mark.parametrize(
    ("primary", "baseline", "expected"),
    (
        ((0.9, 0.8, 0.7), (0.8, 0.7, 0.6), "win"),
        ((0.8, 0.7, 0.6), (0.9, 0.8, 0.7), "lose"),
        ((0.8, 0.8, 0.8), (0.8, 0.8, 0.8), "tie"),
        ((0.9, 0.6, 0.8), (0.8, 0.7, 0.8), "inconclusive"),
    ),
)
def test_model_verdict_uses_pareto_dominance_across_all_retrieval_metrics(
    primary: tuple[float, float, float],
    baseline: tuple[float, float, float],
    expected: str,
) -> None:
    verdict = compare_model_metrics(
        _retrieval_metrics(*primary),
        _retrieval_metrics(*baseline),
    )

    assert verdict.outcome == expected
    assert verdict.deltas == {
        "caseHit": pytest.approx(primary[0] - baseline[0]),
        "macroEvidenceRecall": pytest.approx(primary[1] - baseline[1]),
        "microEvidenceRecall": pytest.approx(primary[2] - baseline[2]),
    }


def _retrieval_metrics(
    case_hit: float,
    macro: float,
    micro: float,
) -> RetrievalMetrics:
    return RetrievalMetrics(
        case_hit=CaseHitMetric(hits=0, total_cases=1, value=case_hit),
        evidence_recall=EvidenceRecallMetric(
            macro=MacroRecallMetric(value=macro),
            micro=MicroRecallMetric(hits=0, total_expected=1, value=micro),
        ),
        per_slice={
            "single_doc": SliceMetrics(
                case_hit=CaseHitMetric(hits=0, total_cases=1, value=case_hit),
                evidence_recall=EvidenceRecallMetric(
                    macro=MacroRecallMetric(value=macro),
                    micro=MicroRecallMetric(hits=0, total_expected=1, value=micro),
                ),
            )
        },
    )


def test_runner_requires_the_declared_384_dimension_reduction_per_model() -> None:
    primary = SyntheticProvider(
        replace(
            _identity("Qwen/Qwen3-Embedding-0.6B"),
            reduction="none_native_384",
        )
    )

    with pytest.raises(BenchmarkUnavailable, match="reduction is unavailable"):
        run_benchmark(
            dataset=_dataset(),
            primary=primary,
            baseline=SyntheticProvider(
                _identity("intfloat/multilingual-e5-small")
            ),
            judge=RecordingJudge(),
            top_k=2,
            clock=lambda: 1.0,
        )
