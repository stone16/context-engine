from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from engine.learning.curation_candidate import (
    CurationCandidateUnavailable,
    EvaluationCaseIntake,
    build_curation_candidate,
)
from engine.learning.feedback import (
    FeedbackBinding,
    FeedbackCitation,
    FeedbackEvidence,
    TriageCategory,
    TriagedFeedback,
    triage_feedback,
)
from engine.learning.golden import (
    EvidenceExpectation,
    EvidenceLineage,
    GoldenCase,
    GoldenSetUnavailable,
)


def _lineage() -> EvidenceLineage:
    return EvidenceLineage(
        source_ref="synthetic-source-feedback",
        resource_ref="synthetic-resource-feedback",
        revision_ref="synthetic-revision-feedback",
        fragment_ref="synthetic-fragment-feedback",
    )


def _triaged_feedback() -> TriagedFeedback:
    return triage_feedback(
        FeedbackEvidence(
            feedback_ref="fb_" + "5" * 64,
            binding=FeedbackBinding(
                organization_id=UUID("00000000-0000-4000-8000-000000000152"),
                run_ref="run_" + "1" * 32,
                package_ref="pkg_" + "2" * 32,
                package_digest="3" * 64,
                release_ref="rel_" + "4" * 64,
                release_generation=7,
                citations=(
                    FeedbackCitation(
                        evidence_ref="ev_" + "6" * 64,
                        lineage=_lineage(),
                    ),
                ),
            ),
            rating="not_helpful",
            note="synthetic-feedback-note",
            recorded_at=datetime(2026, 7, 31, tzinfo=UTC),
        ),
        TriageCategory.RETRIEVAL,
    )


def _case() -> GoldenCase:
    return GoldenCase(
        case_ref="synthetic-feedback-case",
        query="synthetic-feedback-query",
        expected_evidence=(),
        expected_answer="synthetic-feedback-answer",
        required_claims=(),
        answerability="unanswerable",
        slice_name="single_doc",
        partition="dev",
        topic_cluster="synthetic-feedback-topic",
        hard_negative_evidence=(),
    )


def test_triaged_feedback_produces_only_an_immutable_curation_candidate() -> None:
    candidate = build_curation_candidate(
        _triaged_feedback(),
        EvaluationCaseIntake(case=_case(), synthetic=True),
        proposed_at=datetime(2026, 7, 31, 1, tzinfo=UTC),
    )

    assert candidate.feedback_ref == "fb_" + "5" * 64
    assert candidate.category is TriageCategory.RETRIEVAL
    assert candidate.base_release_ref == "rel_" + "4" * 64
    assert candidate.base_release_generation == 7
    assert candidate.feedback_binding.run_ref == "run_" + "1" * 32
    assert candidate.feedback_binding.package_ref == "pkg_" + "2" * 32
    assert candidate.evaluation_case == _case()
    assert candidate.candidate_ref.startswith("cur_")
    assert len(candidate.candidate_digest) == 64


def test_private_evaluation_case_intake_does_not_claim_to_be_synthetic() -> None:
    private = GoldenCase(
        case_ref="private-feedback-case",
        query="Where is my real roadmap?",
        expected_evidence=(),
        expected_answer="It is in the private corpus.",
        required_claims=(),
        answerability="unanswerable",
        slice_name="single_doc",
        partition="dev",
        topic_cluster="private-feedback-topic",
        hard_negative_evidence=(),
    )

    assert EvaluationCaseIntake(case=private, synthetic=False).case is private

    personal = GoldenCase(
        case_ref="synthetic-feedback-case",
        query="Where is my real roadmap?",
        expected_evidence=(),
        expected_answer="synthetic-feedback-answer",
        required_claims=(),
        answerability="unanswerable",
        slice_name="single_doc",
        partition="dev",
        topic_cluster="synthetic-feedback-topic",
        hard_negative_evidence=(),
    )
    with pytest.raises(CurationCandidateUnavailable, match="placeholder"):
        EvaluationCaseIntake(case=personal, synthetic=True)


def test_evaluation_case_must_bind_feedback_citation_lineage() -> None:
    foreign = EvidenceLineage(
        source_ref="synthetic-source-foreign",
        resource_ref="synthetic-resource-foreign",
        revision_ref="synthetic-revision-foreign",
        fragment_ref="synthetic-fragment-foreign",
    )
    answerable = GoldenCase(
        case_ref="synthetic-feedback-case",
        query="synthetic-feedback-query",
        expected_evidence=(
            EvidenceExpectation(
                path="synthetic/foreign.md",
                lineage=foreign,
            ),
        ),
        expected_answer="synthetic-feedback-answer",
        required_claims=(),
        answerability="answerable",
        slice_name="single_doc",
        partition="dev",
        topic_cluster="synthetic-feedback-topic",
        hard_negative_evidence=(),
    )

    with pytest.raises((CurationCandidateUnavailable, GoldenSetUnavailable)):
        build_curation_candidate(
            _triaged_feedback(),
            EvaluationCaseIntake(case=answerable, synthetic=True),
            proposed_at=datetime(2026, 7, 31, 1, tzinfo=UTC),
        )

    assert foreign not in {
        citation.lineage for citation in _triaged_feedback().binding.citations
    }
