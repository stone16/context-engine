from __future__ import annotations

import pytest

from engine.learning.judges import CitationCaseInput, CitationClaim, judge_citations


def test_unresolvable_lineage_fails_citation_layer() -> None:
    report = judge_citations(
        (
            CitationCaseInput(
                case_ref="synthetic-unresolvable",
                required_claim_refs=frozenset({"claim-a"}),
                claims=(
                    CitationClaim(
                        claim_ref="claim-a",
                        cited_evidence=frozenset({"evidence-a"}),
                    ),
                ),
                expected_evidence_by_claim=(("claim-a", frozenset({"evidence-a"})),),
                resolvable_evidence=frozenset(),
            ),
        )
    )

    assert report.lineage_resolvability == 0.0
    assert report.status == "fail"


def test_claim_support_is_partial_when_only_some_citations_are_supported() -> None:
    report = judge_citations(
        (
            CitationCaseInput(
                case_ref="synthetic-partial-support",
                required_claim_refs=frozenset({"claim-a"}),
                claims=(
                    CitationClaim("claim-a", frozenset({"evidence-a"})),
                    CitationClaim(
                        "claim-extra",
                        frozenset({"evidence-unexpected"}),
                    ),
                ),
                expected_evidence_by_claim=(("claim-a", frozenset({"evidence-a"})),),
                resolvable_evidence=frozenset(
                    {"evidence-a", "evidence-unexpected"}
                ),
            ),
        )
    )

    assert report.claim_support == pytest.approx(0.5)


def test_partial_and_extra_evidence_are_hand_scored_and_fail_the_layer() -> None:
    report = judge_citations(
        (
            CitationCaseInput(
                case_ref="synthetic-hand-checked-support",
                required_claim_refs=frozenset({"claim-a", "claim-b"}),
                claims=(
                    CitationClaim("claim-a", frozenset({"evidence-a"})),
                    CitationClaim(
                        "claim-b",
                        frozenset({"evidence-b", "evidence-extra"}),
                    ),
                ),
                expected_evidence_by_claim=(
                    (
                        "claim-a",
                        frozenset({"evidence-a", "evidence-a-second"}),
                    ),
                    ("claim-b", frozenset({"evidence-b"})),
                ),
                resolvable_evidence=frozenset(
                    {
                        "evidence-a",
                        "evidence-a-second",
                        "evidence-b",
                        "evidence-extra",
                    }
                ),
            ),
        )
    )

    # Jaccard support is hand-checkable: each claim has 1 overlap / 2 union.
    assert report.lineage_resolvability == 1.0
    assert report.claim_support == pytest.approx(0.5)
    assert report.completeness == 0.0
    assert report.status == "fail"


def test_completeness_distinguishes_support_from_required_claim_coverage() -> None:
    report = judge_citations(
        (
            CitationCaseInput(
                case_ref="synthetic-incomplete",
                required_claim_refs=frozenset({"claim-a", "claim-b"}),
                claims=(
                    CitationClaim("claim-a", frozenset({"evidence-a"})),
                ),
                expected_evidence_by_claim=(
                    ("claim-a", frozenset({"evidence-a"})),
                    ("claim-b", frozenset({"evidence-b"})),
                ),
                resolvable_evidence=frozenset({"evidence-a", "evidence-b"}),
            ),
        )
    )

    assert report.claim_support == 1.0
    assert report.completeness == pytest.approx(0.5)


def test_missing_produced_claims_score_zero_instead_of_being_dropped() -> None:
    report = judge_citations(
        (
            CitationCaseInput(
                case_ref="synthetic-no-claims",
                required_claim_refs=frozenset({"claim-a"}),
                claims=(),
                expected_evidence_by_claim=(("claim-a", frozenset({"evidence-a"})),),
                resolvable_evidence=frozenset({"evidence-a"}),
            ),
        )
    )

    assert report.lineage_resolvability == 0.0
    assert report.claim_support == 0.0
    assert report.completeness == 0.0


def test_unanswerable_case_has_deterministic_no_citation_success_semantics() -> None:
    report = judge_citations(
        (
            CitationCaseInput(
                case_ref="synthetic-unanswerable",
                required_claim_refs=frozenset(),
                claims=(),
                expected_evidence_by_claim=(),
                resolvable_evidence=frozenset(),
            ),
        )
    )

    assert report.lineage_resolvability == 1.0
    assert report.claim_support == 1.0
    assert report.completeness == 1.0
    assert report.status == "pass"


def test_unanswerable_case_with_a_produced_citation_fails() -> None:
    report = judge_citations(
        (
            CitationCaseInput(
                case_ref="synthetic-unanswerable-with-citation",
                required_claim_refs=frozenset(),
                claims=(CitationClaim("claim-extra", frozenset({"evidence-a"})),),
                expected_evidence_by_claim=(),
                resolvable_evidence=frozenset({"evidence-a"}),
            ),
        )
    )

    assert report.status == "fail"
    assert report.completeness == 0.0
