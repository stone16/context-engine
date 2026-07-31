from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest

from engine.learning.feedback import (
    FeedbackBinding,
    FeedbackBindingUnavailable,
    FeedbackCitation,
    FeedbackEvidence,
    TriageCategory,
    load_feedback_evidence,
    triage_feedback,
)
from engine.learning.golden import EvidenceLineage


def _binding() -> FeedbackBinding:
    return FeedbackBinding(
        organization_id=UUID("00000000-0000-4000-8000-000000000152"),
        run_ref="run_" + "1" * 32,
        package_ref="pkg_" + "2" * 32,
        package_digest="3" * 64,
        release_ref="rel_" + "4" * 64,
        release_generation=7,
        citations=(
            FeedbackCitation(
                evidence_ref="ev_" + "6" * 64,
                lineage=EvidenceLineage(
                source_ref="synthetic-source-feedback",
                resource_ref="synthetic-resource-feedback",
                revision_ref="synthetic-revision-feedback",
                fragment_ref="synthetic-fragment-feedback",
                ),
            ),
        ),
    )


def _feedback() -> FeedbackEvidence:
    return FeedbackEvidence(
        feedback_ref="fb_" + "5" * 64,
        binding=_binding(),
        rating="not_helpful",
        note="synthetic-feedback-note",
        recorded_at=datetime(2026, 7, 31, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    "category",
    tuple(TriageCategory),
)
def test_triage_accepts_only_the_closed_categories(
    category: TriageCategory,
) -> None:
    item = triage_feedback(_feedback(), category)

    assert item.category is category
    assert item.binding == _binding()
    assert item.feedback_ref == "fb_" + "5" * 64


def test_unknown_triage_category_is_refused() -> None:
    with pytest.raises(FeedbackBindingUnavailable, match="category"):
        triage_feedback(_feedback(), "ranking")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("run_ref", ""),
        ("package_ref", "pkg-not-closed"),
        ("package_digest", "0" * 63),
        ("release_ref", "rel-not-closed"),
        ("release_generation", 0),
        ("citations", ()),
    ),
)
def test_partial_feedback_lineage_is_refused(
    field: str,
    replacement: object,
) -> None:
    with pytest.raises((FeedbackBindingUnavailable, ValueError)):
        replace(_binding(), **cast(Any, {field: replacement}))


def test_denied_details_cannot_enter_triage_fields() -> None:
    with pytest.raises(TypeError):
        FeedbackEvidence(  # type: ignore[call-arg]
            feedback_ref="fb_" + "5" * 64,
            binding=_binding(),
            rating="not_helpful",
            note="synthetic-feedback-note",
            recorded_at=datetime(2026, 7, 31, tzinfo=UTC),
            denied_details="synthetic-denied-resource",
        )


def test_closed_feedback_projection_loads_exact_delivery_identity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "feedback.json"
    path.write_text(
        json.dumps(
            {
                "citations": [
                    {
                        "evidenceRef": "ev_" + "6" * 64,
                        "fragmentRef": "synthetic-fragment-feedback",
                        "resourceRef": "synthetic-resource-feedback",
                        "revisionRef": "synthetic-revision-feedback",
                        "sourceRef": "synthetic-source-feedback",
                    }
                ],
                "feedbackRef": "fb_" + "5" * 64,
                "note": "synthetic-feedback-note",
                "organizationId": "00000000-0000-4000-8000-000000000152",
                "packageDigest": "3" * 64,
                "packageRef": "pkg_" + "2" * 32,
                "rating": "not_helpful",
                "recordedAt": "2026-07-31T00:00:00Z",
                "releaseGeneration": 7,
                "releaseRef": "rel_" + "4" * 64,
                "runRef": "run_" + "1" * 32,
                "schemaVersion": "context-engine-feedback-evidence-v1",
            }
        ),
        encoding="utf-8",
    )

    loaded = load_feedback_evidence(path)

    assert loaded == _feedback()


def test_feedback_projection_refuses_unknown_or_partial_fields(tmp_path: Path) -> None:
    path = tmp_path / "feedback.json"
    path.write_text(json.dumps({"feedbackRef": "fb_" + "5" * 64}), encoding="utf-8")

    with pytest.raises(FeedbackBindingUnavailable, match="malformed"):
        load_feedback_evidence(path)
