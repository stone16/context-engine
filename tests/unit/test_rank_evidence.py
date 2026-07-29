from __future__ import annotations

from dataclasses import fields
from uuid import UUID

import pytest

from engine.runtime.candidate_ranking import CandidateRankEvidence, RankerEvidence
from engine.runtime.evidence import CandidateRef


def _candidate() -> CandidateRef:
    return CandidateRef(
        organization_id=UUID("81e18bca-86a1-478a-937d-7675c6fe69b0"),
        source_ref="source:ranked",
        resource_ref="resource:ranked",
        revision_ref="05b82c43-4e8f-49ae-a286-a40289a3413e",
        fragment_ref="fragment:ranked",
    )


def test_rank_evidence_is_a_closed_content_free_non_authority_record() -> None:
    evidence = CandidateRankEvidence(
        candidate_ref=_candidate(),
        per_ranker=(
            RankerEvidence(ranker_ref="lexical", position=1, score=0.75),
            RankerEvidence(ranker_ref="vector", position=3, score=None),
        ),
        fused_rank=2,
    )

    assert tuple(field.name for field in fields(CandidateRankEvidence)) == (
        "candidate_ref",
        "per_ranker",
        "fused_rank",
    )
    assert tuple(field.name for field in fields(RankerEvidence)) == (
        "ranker_ref",
        "position",
        "score",
    )
    assert evidence.candidate_ref == _candidate()
    all_field_names = {
        field.name
        for record_type in (CandidateRankEvidence, RankerEvidence)
        for field in fields(record_type)
    }
    assert all(
        forbidden not in all_field_names
        for forbidden in (
            "body",
            "content",
            "text",
            "snippet",
            "acl",
            "authority",
            "authorized",
            "grant",
        )
    )


def test_rank_evidence_refuses_content_acl_and_authority_payloads() -> None:
    with pytest.raises(TypeError):
        CandidateRankEvidence(  # type: ignore[call-arg]
            candidate_ref=_candidate(),
            per_ranker=(),
            fused_rank=1,
            content="forbidden",
        )
    with pytest.raises(TypeError):
        RankerEvidence(  # type: ignore[call-arg]
            ranker_ref="lexical",
            position=1,
            score=1.0,
            acl_claim=True,
        )
    with pytest.raises(TypeError):
        CandidateRankEvidence(  # type: ignore[call-arg]
            candidate_ref=_candidate(),
            per_ranker=(),
            fused_rank=1,
            authority=object(),
        )
