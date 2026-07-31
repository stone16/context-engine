"""Exact authorized lineage for feedback triage inputs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from re import fullmatch
from typing import TYPE_CHECKING, Literal, cast
from uuid import UUID

from engine.learning.golden import EvidenceLineage

if TYPE_CHECKING:
    from engine.persistence.context_runs import ContextRunView

_MAX_SIGNED_BIGINT = (1 << 63) - 1


class FeedbackBindingUnavailable(RuntimeError):
    """Captured feedback cannot be bound to one exact delivered Package."""


class TriageCategory(StrEnum):
    """Closed operator diagnosis assigned after exact lineage binding."""

    SOURCE = "source"
    VISIBILITY = "visibility"
    RETRIEVAL = "retrieval"
    ASSEMBLY = "assembly"
    EVALUATION = "evaluation"


def _opaque(field_name: str, value: object, pattern: str) -> str:
    if type(value) is not str or fullmatch(pattern, value) is None:
        raise FeedbackBindingUnavailable(f"feedback {field_name} is unavailable")
    return value


def _instant(value: object) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError("feedback recorded_at must be aware UTC")
    return value


@dataclass(frozen=True, slots=True)
class FeedbackCitation:
    """One exact delivered Evidence identity and content-free lineage."""

    evidence_ref: str
    lineage: EvidenceLineage

    def __post_init__(self) -> None:
        _opaque("citation evidence_ref", self.evidence_ref, r"ev_[0-9a-f]{64}")
        if type(self.lineage) is not EvidenceLineage:
            raise TypeError("feedback citation requires EvidenceLineage")


@dataclass(frozen=True, slots=True)
class FeedbackBinding:
    """Exact run, Package, Release generation, and citation lineage."""

    organization_id: UUID = field(repr=False)
    run_ref: str
    package_ref: str
    package_digest: str = field(repr=False)
    release_ref: str
    release_generation: int
    citations: tuple[FeedbackCitation, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.organization_id) is not UUID:
            raise TypeError("feedback Organization must be UUID")
        _opaque("run_ref", self.run_ref, r"run_[0-9a-f]{32}")
        _opaque("package_ref", self.package_ref, r"pkg_[0-9a-f]{32}")
        _opaque("package_digest", self.package_digest, r"[0-9a-f]{64}")
        _opaque("release_ref", self.release_ref, r"rel_[0-9a-f]{64}")
        if (
            type(self.release_generation) is not int
            or not 1 <= self.release_generation <= _MAX_SIGNED_BIGINT
        ):
            raise FeedbackBindingUnavailable(
                "feedback Release generation is unavailable"
            )
        if (
            type(self.citations) is not tuple
            or not self.citations
            or any(type(value) is not FeedbackCitation for value in self.citations)
            or len(self.citations) != len(set(self.citations))
            or len({value.evidence_ref for value in self.citations})
            != len(self.citations)
            or len({value.lineage for value in self.citations})
            != len(self.citations)
        ):
            raise FeedbackBindingUnavailable(
                "feedback citation lineage is unavailable"
            )


@dataclass(frozen=True, slots=True)
class FeedbackEvidence:
    """Captured feedback plus its exact authorized-only delivery binding."""

    feedback_ref: str
    binding: FeedbackBinding = field(repr=False)
    rating: Literal["helpful", "not_helpful"]
    recorded_at: datetime = field(repr=False)
    note: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _opaque("feedback_ref", self.feedback_ref, r"fb_[0-9a-f]{64}")
        if type(self.binding) is not FeedbackBinding:
            raise TypeError("feedback requires an exact binding")
        if self.rating not in {"helpful", "not_helpful"}:
            raise ValueError("feedback rating is unavailable")
        if self.note is not None and (
            type(self.note) is not str
            or not self.note
            or self.note.isspace()
            or len(self.note) > 1_000
        ):
            raise ValueError("feedback note is unavailable")
        _instant(self.recorded_at)


@dataclass(frozen=True, slots=True)
class TriagedFeedback:
    """One accepted triage classification with unchanged exact lineage."""

    feedback_ref: str
    binding: FeedbackBinding = field(repr=False)
    rating: Literal["helpful", "not_helpful"]
    note: str | None = field(repr=False)
    recorded_at: datetime = field(repr=False)
    category: TriageCategory

    def __post_init__(self) -> None:
        _opaque("feedback_ref", self.feedback_ref, r"fb_[0-9a-f]{64}")
        if type(self.binding) is not FeedbackBinding:
            raise TypeError("triaged feedback requires an exact binding")
        if self.rating not in {"helpful", "not_helpful"}:
            raise ValueError("triaged feedback rating is unavailable")
        if self.note is not None and (
            type(self.note) is not str
            or not self.note
            or self.note.isspace()
            or len(self.note) > 1_000
        ):
            raise ValueError("triaged feedback note is unavailable")
        _instant(self.recorded_at)
        if type(self.category) is not TriageCategory:
            raise FeedbackBindingUnavailable(
                "feedback triage category is unavailable"
            )


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value or value.isspace():
        raise FeedbackBindingUnavailable(f"feedback {name} is unavailable")
    return value


def _timestamp(value: object) -> datetime:
    if type(value) is not str:
        raise FeedbackBindingUnavailable("feedback recordedAt is unavailable")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return _instant(parsed)
    except (ValueError, FeedbackBindingUnavailable):
        raise FeedbackBindingUnavailable(
            "feedback recordedAt is unavailable"
        ) from None


def feedback_evidence_from_document(value: object) -> FeedbackEvidence:
    """Validate one trusted persistence projection as a closed whole."""

    fields = frozenset(
        {
            "citations",
            "feedbackRef",
            "note",
            "organizationId",
            "packageDigest",
            "packageRef",
            "rating",
            "recordedAt",
            "releaseGeneration",
            "releaseRef",
            "runRef",
            "schemaVersion",
        }
    )
    if type(value) is not dict or frozenset(value) != fields:
        raise FeedbackBindingUnavailable("feedback evidence is malformed")
    document = value
    if document["schemaVersion"] != "context-engine-feedback-evidence-v1":
        raise FeedbackBindingUnavailable("feedback evidence version is unavailable")
    citations = document["citations"]
    if type(citations) is not list:
        raise FeedbackBindingUnavailable("feedback citations are unavailable")
    projected: list[FeedbackCitation] = []
    for citation in citations:
        citation_fields = frozenset(
            {
                "evidenceRef",
                "fragmentRef",
                "resourceRef",
                "revisionRef",
                "sourceRef",
            }
        )
        if type(citation) is not dict or frozenset(citation) != citation_fields:
            raise FeedbackBindingUnavailable("feedback citation is malformed")
        projected.append(
            FeedbackCitation(
                evidence_ref=_text(citation["evidenceRef"], "citation evidenceRef"),
                lineage=EvidenceLineage(
                    source_ref=_text(citation["sourceRef"], "citation sourceRef"),
                    resource_ref=_text(
                        citation["resourceRef"], "citation resourceRef"
                    ),
                    revision_ref=_text(
                        citation["revisionRef"], "citation revisionRef"
                    ),
                    fragment_ref=_text(
                        citation["fragmentRef"], "citation fragmentRef"
                    ),
                ),
            )
        )
    try:
        organization_id = UUID(_text(document["organizationId"], "organizationId"))
    except ValueError:
        raise FeedbackBindingUnavailable(
            "feedback organizationId is unavailable"
        ) from None
    rating = document["rating"]
    if rating not in {"helpful", "not_helpful"}:
        raise FeedbackBindingUnavailable("feedback rating is unavailable")
    note = document["note"]
    if note is not None and type(note) is not str:
        raise FeedbackBindingUnavailable("feedback note is unavailable")
    return FeedbackEvidence(
        feedback_ref=_text(document["feedbackRef"], "feedbackRef"),
        binding=FeedbackBinding(
            organization_id=organization_id,
            run_ref=_text(document["runRef"], "runRef"),
            package_ref=_text(document["packageRef"], "packageRef"),
            package_digest=_text(document["packageDigest"], "packageDigest"),
            release_ref=_text(document["releaseRef"], "releaseRef"),
            release_generation=cast(int, document["releaseGeneration"]),
            citations=tuple(projected),
        ),
        rating=cast(Literal["helpful", "not_helpful"], rating),
        recorded_at=_timestamp(document["recordedAt"]),
        note=note,
    )


def load_feedback_evidence(path: Path) -> FeedbackEvidence:
    """Load one closed captured-feedback projection or refuse it as a whole."""

    if not isinstance(path, Path):
        raise TypeError("feedback evidence path must be Path")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        raise FeedbackBindingUnavailable("feedback evidence is unavailable") from None
    return feedback_evidence_from_document(value)


def bind_captured_feedback(
    *,
    feedback_ref: str,
    rating: Literal["helpful", "not_helpful"],
    recorded_at: datetime,
    run: ContextRunView,
    note: str | None = None,
) -> FeedbackEvidence:
    """Bind captured evidence to one authorized current ContextRun projection."""

    from engine.persistence.context_runs import ContextRunView

    if type(run) is not ContextRunView:
        raise TypeError("feedback binding requires ContextRunView")
    if run.outcome.value != "delivered_authorized":
        raise FeedbackBindingUnavailable(
            "feedback citations are unavailable for an empty ContextRun"
        )
    if (
        run.package_ref is None
        or run.release_ref is None
        or run.release_generation is None
        or run.authorized_citation_lineage is None
    ):
        raise FeedbackBindingUnavailable(
            "feedback exact binding is unavailable for a legacy ContextRun"
        )
    return FeedbackEvidence(
        feedback_ref=feedback_ref,
        binding=FeedbackBinding(
            organization_id=run.organization_id,
            run_ref=run.run_ref,
            package_ref=run.package_ref,
            package_digest=run.package_digest,
            release_ref=run.release_ref,
            release_generation=run.release_generation,
            citations=tuple(
                FeedbackCitation(
                    evidence_ref=value["evidenceRef"],
                    lineage=EvidenceLineage(
                        source_ref=value["sourceRef"],
                        resource_ref=value["resourceRef"],
                        revision_ref=value["revisionRef"],
                        fragment_ref=value["fragmentRef"],
                    ),
                )
                for value in run.authorized_citation_lineage
            ),
        ),
        rating=rating,
        recorded_at=recorded_at,
        note=note,
    )


def triage_feedback(
    feedback: FeedbackEvidence,
    category: TriageCategory,
) -> TriagedFeedback:
    """Classify one exactly bound item or refuse the whole item."""

    if type(feedback) is not FeedbackEvidence:
        raise TypeError("triage requires FeedbackEvidence")
    if type(category) is not TriageCategory:
        raise FeedbackBindingUnavailable("feedback triage category is unavailable")
    return TriagedFeedback(
        feedback_ref=feedback.feedback_ref,
        binding=feedback.binding,
        rating=feedback.rating,
        note=feedback.note,
        recorded_at=feedback.recorded_at,
        category=category,
    )
