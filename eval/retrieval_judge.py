"""Adapter from the embedding benchmark seam to the canonical retrieval judge."""

from __future__ import annotations

from collections.abc import Iterable

from engine.learning.judges import (
    RetrievalCaseInput,
    RetrievalReport,
    judge_retrieval,
)
from eval.embedding_benchmark import (
    CaseHitMetric,
    EvidenceRecallMetric,
    MacroRecallMetric,
    MicroRecallMetric,
    RetrievalJudgeCase,
    RetrievalMetrics,
    SliceMetrics,
)


class RetrievalJudgeAdapter:
    """Translate the offline benchmark contract to issue #129's fixed judge."""

    def evaluate_retrieval(
        self,
        cases: tuple[RetrievalJudgeCase, ...],
    ) -> RetrievalMetrics:
        aggregate = _judge(cases)
        slices = {
            slice_name: _slice_metrics(
                _judge(case for case in cases if case.slice_name == slice_name)
            )
            for slice_name in sorted({case.slice_name for case in cases})
        }
        return RetrievalMetrics(
            case_hit=_case_hit(aggregate),
            evidence_recall=_evidence_recall(aggregate),
            per_slice=slices,
        )


def create_judge() -> RetrievalJudgeAdapter:
    """Create the sole benchmark adapter for the canonical retrieval judge."""

    return RetrievalJudgeAdapter()


def _judge(cases: Iterable[RetrievalJudgeCase]) -> RetrievalReport:
    return judge_retrieval(
        tuple(
            RetrievalCaseInput(
                case_ref=case.case_ref,
                expected_evidence=frozenset(case.expected_evidence),
                observed_evidence=frozenset(case.retrieved_evidence),
            )
            for case in cases
        )
    )


def _slice_metrics(report: RetrievalReport) -> SliceMetrics:
    return SliceMetrics(
        case_hit=_case_hit(report),
        evidence_recall=_evidence_recall(report),
    )


def _case_hit(report: RetrievalReport) -> CaseHitMetric:
    return CaseHitMetric(
        hits=report.hit_cases,
        total_cases=report.total_cases,
        value=report.case_hit,
    )


def _evidence_recall(report: RetrievalReport) -> EvidenceRecallMetric:
    return EvidenceRecallMetric(
        macro=MacroRecallMetric(value=report.macro_evidence_recall),
        micro=MicroRecallMetric(
            hits=report.evidence_hits,
            total_expected=report.total_expected_evidence,
            value=report.micro_evidence_recall,
        ),
    )
