"""Governed evaluation-case candidates produced from triaged feedback."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from re import fullmatch
from typing import Final, cast
from uuid import UUID

from engine.learning.feedback import (
    FeedbackBinding,
    FeedbackCitation,
    TriageCategory,
    TriagedFeedback,
)
from engine.learning.golden import EvidenceLineage, GoldenCase, load_golden_case

CURATION_CANDIDATE_VERSION: Final = "context-engine-curation-candidate-v1"
_PLACEHOLDER_PREFIXES: Final = (
    "synthetic-",
    "synthetic/",
    "placeholder-",
    "placeholder/",
)


class CurationCandidateUnavailable(RuntimeError):
    """A candidate cannot cross the exact-lineage or privacy boundary."""


def _instant(value: object) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError("curation candidate time must be aware UTC")
    return value


def _timestamp(value: datetime) -> str:
    return _instant(value).astimezone(UTC).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _digest(document: object) -> str:
    return sha256(
        json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _placeholder(field_name: str, value: object) -> None:
    if type(value) is not str or not value.startswith(_PLACEHOLDER_PREFIXES):
        raise CurationCandidateUnavailable(
            f"evaluation case {field_name} requires placeholder content"
        )


def _require_synthetic_case(case: GoldenCase) -> None:
    _placeholder("caseRef", case.case_ref)
    _placeholder("query", case.query)
    _placeholder("expectedAnswer", case.expected_answer)
    _placeholder("topicCluster", case.topic_cluster)
    for expectation in case.expected_evidence:
        _placeholder("path", expectation.path)
        for field_name, value in expectation.lineage.document().items():
            _placeholder(field_name, value)
    for claim in case.required_claims:
        _placeholder("claimRef", claim.claim_ref)
        _placeholder("claim", claim.claim)
        for lineage in claim.expected_evidence:
            for field_name, value in lineage.document().items():
                _placeholder(field_name, value)
    for negative in case.hard_negative_evidence:
        _placeholder("path", negative.path)
        _placeholder("topicCluster", negative.topic_cluster)
        for field_name, value in negative.lineage.document().items():
            _placeholder(field_name, value)


@dataclass(frozen=True, slots=True)
class EvaluationCaseIntake:
    """A private or explicit-placeholder v1 case proposed for governed intake."""

    case: GoldenCase = field(repr=False)
    synthetic: bool

    def __post_init__(self) -> None:
        if type(self.case) is not GoldenCase:
            raise TypeError("evaluation intake requires GoldenCase")
        if type(self.synthetic) is not bool:
            raise TypeError("evaluation case synthetic marker must be bool")
        if self.synthetic:
            _require_synthetic_case(self.case)


@dataclass(frozen=True, slots=True)
class CurationCandidate:
    """Immutable proposal; it has no ReleaseManifest publication operation."""

    feedback_ref: str
    category: TriageCategory
    feedback_binding: FeedbackBinding = field(repr=False)
    evaluation_case: GoldenCase = field(repr=False)
    proposed_at: datetime
    candidate_ref: str = field(init=False)
    candidate_digest: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if type(self.category) is not TriageCategory:
            raise TypeError("curation candidate category is unavailable")
        if type(self.feedback_binding) is not FeedbackBinding:
            raise TypeError("curation candidate feedback binding is unavailable")
        _instant(self.proposed_at)
        document = curation_candidate_document(self, include_identity=False)
        digest = _digest(document)
        object.__setattr__(self, "candidate_digest", digest)
        object.__setattr__(self, "candidate_ref", f"cur_{digest}")

    @property
    def base_release_ref(self) -> str:
        return self.feedback_binding.release_ref

    @property
    def base_release_generation(self) -> int:
        return self.feedback_binding.release_generation


def curation_candidate_document(
    candidate: CurationCandidate,
    *,
    include_identity: bool = True,
) -> dict[str, object]:
    """Render the closed candidate document with no denied object details."""

    if type(candidate) is not CurationCandidate:
        raise TypeError("candidate must be CurationCandidate")
    document: dict[str, object] = {
        "baseReleaseGeneration": candidate.base_release_generation,
        "baseReleaseRef": candidate.base_release_ref,
        "category": candidate.category.value,
        "evaluationCase": candidate.evaluation_case.document(),
        "feedbackBinding": {
            "citations": [
                {
                    "evidenceRef": citation.evidence_ref,
                    **citation.lineage.document(),
                }
                for citation in candidate.feedback_binding.citations
            ],
            "organizationId": str(candidate.feedback_binding.organization_id),
            "packageDigest": candidate.feedback_binding.package_digest,
            "packageRef": candidate.feedback_binding.package_ref,
            "releaseGeneration": candidate.feedback_binding.release_generation,
            "releaseRef": candidate.feedback_binding.release_ref,
            "runRef": candidate.feedback_binding.run_ref,
        },
        "feedbackRef": candidate.feedback_ref,
        "proposedAt": _timestamp(candidate.proposed_at),
        "schemaVersion": CURATION_CANDIDATE_VERSION,
    }
    if include_identity:
        document["candidateDigest"] = candidate.candidate_digest
        document["candidateRef"] = candidate.candidate_ref
    return document


def curation_candidate_case(document: object) -> GoldenCase:
    """Verify one immutable candidate document and return its proposed case."""

    expected_fields = frozenset(
        {
            "baseReleaseGeneration",
            "baseReleaseRef",
            "candidateDigest",
            "candidateRef",
            "category",
            "evaluationCase",
            "feedbackBinding",
            "feedbackRef",
            "proposedAt",
            "schemaVersion",
        }
    )
    if type(document) is not dict or frozenset(document) != expected_fields:
        raise CurationCandidateUnavailable("curation candidate is malformed")
    candidate_document = cast(dict[str, object], document)
    if candidate_document["schemaVersion"] != CURATION_CANDIDATE_VERSION:
        raise CurationCandidateUnavailable("curation candidate version is unavailable")
    candidate_digest = candidate_document["candidateDigest"]
    candidate_ref = candidate_document["candidateRef"]
    digest_input = {
        key: value
        for key, value in candidate_document.items()
        if key not in {"candidateDigest", "candidateRef"}
    }
    if (
        type(candidate_digest) is not str
        or candidate_digest != _digest(digest_input)
        or candidate_ref != f"cur_{candidate_digest}"
    ):
        raise CurationCandidateUnavailable("curation candidate identity is unavailable")
    binding = candidate_document["feedbackBinding"]
    binding_fields = frozenset(
        {
            "citations",
            "organizationId",
            "packageDigest",
            "packageRef",
            "releaseGeneration",
            "releaseRef",
            "runRef",
        }
    )
    if type(binding) is not dict or frozenset(binding) != binding_fields:
        raise CurationCandidateUnavailable("curation candidate binding is unavailable")
    binding_document = cast(dict[str, object], binding)
    citations = binding_document["citations"]
    if type(citations) is not list:
        raise CurationCandidateUnavailable(
            "curation candidate citations are unavailable"
        )
    projected: list[FeedbackCitation] = []
    citation_fields = frozenset(
        {
            "evidenceRef",
            "fragmentRef",
            "resourceRef",
            "revisionRef",
            "sourceRef",
        }
    )
    try:
        for value in cast(list[object], citations):
            if type(value) is not dict or frozenset(value) != citation_fields:
                raise ValueError
            citation = cast(dict[str, object], value)
            projected.append(
                FeedbackCitation(
                    evidence_ref=cast(str, citation["evidenceRef"]),
                    lineage=EvidenceLineage(
                        source_ref=cast(str, citation["sourceRef"]),
                        resource_ref=cast(str, citation["resourceRef"]),
                        revision_ref=cast(str, citation["revisionRef"]),
                        fragment_ref=cast(str, citation["fragmentRef"]),
                    ),
                )
            )
        feedback_binding = FeedbackBinding(
            organization_id=UUID(cast(str, binding_document["organizationId"])),
            run_ref=cast(str, binding_document["runRef"]),
            package_ref=cast(str, binding_document["packageRef"]),
            package_digest=cast(str, binding_document["packageDigest"]),
            release_ref=cast(str, binding_document["releaseRef"]),
            release_generation=cast(int, binding_document["releaseGeneration"]),
            citations=tuple(projected),
        )
        feedback_ref = candidate_document["feedbackRef"]
        if (
            type(feedback_ref) is not str
            or fullmatch(r"fb_[0-9a-f]{64}", feedback_ref) is None
        ):
            raise ValueError
        TriageCategory(cast(str, candidate_document["category"]))
        proposed_at = candidate_document["proposedAt"]
        if type(proposed_at) is not str:
            raise ValueError
        parsed_at = datetime.fromisoformat(proposed_at.replace("Z", "+00:00"))
        if _timestamp(parsed_at) != proposed_at:
            raise ValueError
    except (AttributeError, TypeError, ValueError, RuntimeError):
        raise CurationCandidateUnavailable(
            "curation candidate fields are unavailable"
        ) from None
    if (
        feedback_binding.release_ref != candidate_document["baseReleaseRef"]
        or feedback_binding.release_generation
        != candidate_document["baseReleaseGeneration"]
    ):
        raise CurationCandidateUnavailable("curation candidate binding is inconsistent")
    case = load_golden_case(candidate_document["evaluationCase"])
    _require_case_binding(case, feedback_binding)
    return case


def _require_case_binding(case: GoldenCase, binding: FeedbackBinding) -> None:
    expected_lineage = frozenset(
        expectation.lineage for expectation in case.expected_evidence
    )
    claim_lineage = frozenset(
        lineage
        for claim in case.required_claims
        for lineage in claim.expected_evidence
    )
    bound = frozenset(citation.lineage for citation in binding.citations)
    if case.answerability == "answerable" and (
        not expected_lineage or not case.required_claims
    ):
        raise CurationCandidateUnavailable(
            "answerable evaluation case requires expected citation lineage"
        )
    if not expected_lineage.issubset(bound) or not claim_lineage.issubset(bound):
        raise CurationCandidateUnavailable(
            "evaluation case citation lineage is outside the feedback binding"
        )


def build_curation_candidate(
    feedback: TriagedFeedback,
    intake: EvaluationCaseIntake,
    *,
    proposed_at: datetime,
) -> CurationCandidate:
    """Propose one case without changing golden locks or a Release pointer."""

    if type(feedback) is not TriagedFeedback:
        raise TypeError("curation requires TriagedFeedback")
    if type(intake) is not EvaluationCaseIntake:
        raise TypeError("curation requires EvaluationCaseIntake")
    _require_case_binding(intake.case, feedback.binding)
    return CurationCandidate(
        feedback_ref=feedback.feedback_ref,
        category=feedback.category,
        feedback_binding=feedback.binding,
        evaluation_case=intake.case,
        proposed_at=proposed_at,
    )
