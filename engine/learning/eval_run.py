"""Strict offline observation loading and layered v1 report assembly."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Final, Literal, cast

from engine.learning.eval_report import (
    CaseSecurityObservation,
    CaseSecurityResult,
    CaseSecurityViolation,
    EvaluationGateStatuses,
    SecurityObservationState,
    final_report_status,
    refused_security_observation,
    security_report,
)
from engine.learning.golden import EvidenceLineage, GoldenSet
from engine.learning.judges import (
    AnswerCaseInput,
    AnswerJudgeProfile,
    CitationCaseInput,
    CitationClaim,
    RetrievalCaseInput,
    SliceCaseScore,
    judge_answers,
    judge_citations,
    judge_retrieval,
)
from engine.learning.lineage import LineageResolutionReport
from engine.learning.thresholds import (
    EvaluationThresholds,
    PendingValue,
    evaluate_layer_slice_thresholds,
    require_loaded_thresholds,
    threshold_report_document,
)

EVAL_RUN_SCHEMA_VERSION: Final = "context-engine-eval-run-v1"
EVAL_REPORT_VERSION: Final = "context-engine-eval-report-v1"


class EvaluationRunUnavailable(RuntimeError):
    """A partial or malformed observation set refuses the complete run."""


def _text(field_name: str, value: object) -> str:
    if type(value) is not str or not value or value.isspace() or value != value.strip():
        raise EvaluationRunUnavailable(f"{field_name} is unavailable")
    return value


def _object(
    value: object,
    name: str,
    fields: frozenset[str],
) -> dict[str, object]:
    if type(value) is not dict or frozenset(value) != fields:
        raise EvaluationRunUnavailable(f"{name} is malformed")
    return cast(dict[str, object], value)


def _list(value: object, name: str) -> list[object]:
    if type(value) is not list:
        raise EvaluationRunUnavailable(f"{name} is malformed")
    return cast(list[object], value)


def _lineage(value: object) -> EvidenceLineage:
    document = _object(
        value,
        "observed Evidence",
        frozenset({"sourceRef", "resourceRef", "revisionRef", "fragmentRef"}),
    )
    return EvidenceLineage(
        source_ref=_text("observed sourceRef", document["sourceRef"]),
        resource_ref=_text("observed resourceRef", document["resourceRef"]),
        revision_ref=_text("observed revisionRef", document["revisionRef"]),
        fragment_ref=_text("observed fragmentRef", document["fragmentRef"]),
    )


def _lineage_ref(lineage: EvidenceLineage) -> str:
    return sha256(
        json.dumps(
            lineage.document(), separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class ObservedClaim:
    claim_ref: str
    cited_evidence: tuple[EvidenceLineage, ...]


@dataclass(frozen=True, slots=True)
class EvaluationCaseObservation:
    case_ref: str
    observed_evidence: tuple[EvidenceLineage, ...]
    claims: tuple[ObservedClaim, ...]
    blind_score: int
    refused: bool
    critical_contradiction: bool
    security_observation: CaseSecurityResult

    def __post_init__(self) -> None:
        if type(self.security_observation) not in {
            CaseSecurityObservation,
            CaseSecurityViolation,
        }:
            raise TypeError("evaluation case requires typed security observation")


@dataclass(frozen=True, slots=True)
class EvaluationRun:
    answer_judge_profile: AnswerJudgeProfile
    cases: tuple[EvaluationCaseObservation, ...]
    executed_seam_ref: str | None = None

    def __post_init__(self) -> None:
        if type(self.answer_judge_profile) is not AnswerJudgeProfile:
            raise TypeError("evaluation run requires answer judge profile")
        if type(self.cases) is not tuple or not self.cases or any(
            type(case) is not EvaluationCaseObservation for case in self.cases
        ):
            raise TypeError("evaluation run requires typed case observations")
        if self.executed_seam_ref is not None and (
            type(self.executed_seam_ref) is not str
            or not self.executed_seam_ref
            or self.executed_seam_ref != self.executed_seam_ref.strip()
            or any(character.isspace() for character in self.executed_seam_ref)
        ):
            raise TypeError("evaluation run seam ref must be an opaque ref")


def _security_observation(value: object, case_ref: str) -> CaseSecurityObservation:
    """Classify serialized security claims; never elevate them to observed clean."""

    if value == {"status": "not_observed"}:
        return refused_security_observation(
            case_ref,
            SecurityObservationState.NOT_OBSERVED,
        )
    return refused_security_observation(case_ref, SecurityObservationState.MALFORMED)


def _observed_claim(value: object) -> ObservedClaim:
    document = _object(
        value,
        "observed claim",
        frozenset({"claimRef", "citedEvidence"}),
    )
    evidence = tuple(
        _lineage(item)
        for item in _list(document["citedEvidence"], "observed claim citations")
    )
    if not evidence or len(evidence) != len(set(evidence)):
        raise EvaluationRunUnavailable(
            "observed claim citations must be nonempty and unique"
        )
    return ObservedClaim(
        claim_ref=_text("observed claimRef", document["claimRef"]),
        cited_evidence=evidence,
    )


def _case_observation(value: object) -> EvaluationCaseObservation:
    required_fields = frozenset(
        {
            "blindScore",
            "caseRef",
            "claims",
            "criticalContradiction",
            "observedEvidence",
            "refused",
        }
    )
    security_fields = frozenset(
        {
            "securityObservation",
            "unauthorizedEvidenceCount",
            "wrongOrganizationEffectCount",
            "missingContextFallbackCount",
        }
    )
    if type(value) is not dict:
        raise EvaluationRunUnavailable("evaluation case observation is malformed")
    document = cast(dict[str, object], value)
    actual_fields = frozenset(document)
    if not required_fields <= actual_fields or actual_fields - (
        required_fields | security_fields
    ):
        raise EvaluationRunUnavailable("evaluation case observation is malformed")
    case_ref = _text("observed caseRef", document["caseRef"])
    security_value = document.get("securityObservation")
    if actual_fields & (security_fields - {"securityObservation"}):
        security_value = None
    observed = tuple(
        _lineage(item)
        for item in _list(document["observedEvidence"], "observed Evidence")
    )
    if len(observed) != len(set(observed)):
        raise EvaluationRunUnavailable("observed Evidence must be unique")
    claims = tuple(
        _observed_claim(item)
        for item in _list(document["claims"], "observed claims")
    )
    claim_refs = tuple(claim.claim_ref for claim in claims)
    if len(claim_refs) != len(set(claim_refs)):
        raise EvaluationRunUnavailable("observed claimRef values must be unique")
    blind_score = document["blindScore"]
    if type(blind_score) is not int or blind_score not in {0, 1, 2}:
        raise EvaluationRunUnavailable("blindScore must be 0, 1, or 2")
    refused = document["refused"]
    contradiction = document["criticalContradiction"]
    if type(refused) is not bool or type(contradiction) is not bool:
        raise EvaluationRunUnavailable("answer observation flags must be bool")
    return EvaluationCaseObservation(
        case_ref=case_ref,
        observed_evidence=observed,
        claims=claims,
        blind_score=blind_score,
        refused=refused,
        critical_contradiction=contradiction,
        security_observation=_security_observation(security_value, case_ref),
    )


def load_evaluation_run(path: Path) -> EvaluationRun:
    """Load the exact closed observation set or refuse the whole run."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        raise EvaluationRunUnavailable("evaluation run is unavailable") from None
    document = _object(
        raw,
        "evaluation run",
        frozenset({"answerJudge", "cases", "schemaVersion"}),
    )
    if document["schemaVersion"] != EVAL_RUN_SCHEMA_VERSION:
        raise EvaluationRunUnavailable("evaluation run version is unavailable")
    answer_judge = _object(
        document["answerJudge"],
        "answer judge",
        frozenset({"modelRef", "profileRef"}),
    )
    cases = tuple(
        _case_observation(item)
        for item in _list(document["cases"], "evaluation cases")
    )
    refs = tuple(case.case_ref for case in cases)
    if not cases or len(refs) != len(set(refs)):
        raise EvaluationRunUnavailable(
            "evaluation cases must be nonempty with unique caseRef values"
        )
    return EvaluationRun(
        answer_judge_profile=AnswerJudgeProfile(
            model_ref=_text("answer judge modelRef", answer_judge["modelRef"]),
            profile_ref=_text("answer judge profileRef", answer_judge["profileRef"]),
        ),
        cases=cases,
    )


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("report time must be aware UTC")
    rendered = value.astimezone(UTC).isoformat(timespec="microseconds")
    return rendered.replace("+00:00", "Z")


def _report_digest(report: dict[str, object]) -> str:
    return sha256(
        json.dumps(
            report,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def record_lineage_check(
    report: dict[str, object],
    lineage_check: LineageResolutionReport,
) -> dict[str, object]:
    """Bind a completed lineage check into an evaluation report digest."""

    if type(report) is not dict or type(lineage_check) is not LineageResolutionReport:
        raise TypeError("report requires a typed lineage resolution")
    golden_set = report.get("goldenSet")
    if (
        type(golden_set) is not dict
        or golden_set.get("digest") != lineage_check.golden_digest
    ):
        raise EvaluationRunUnavailable(
            "lineage resolution belongs to a different set"
        )
    updated = dict(report)
    updated["lineageCheck"] = {
        "ran": True,
        "staleCaseCount": len(lineage_check.stale_cases),
        "totalCaseCount": lineage_check.total_cases,
    }
    updated.pop("reportDigest", None)
    updated["reportDigest"] = _report_digest(updated)
    return updated


def bind_evaluation_report_to_release(
    report: dict[str, object],
    lineage_check: LineageResolutionReport,
) -> dict[str, object]:
    """Bind an evaluated corpus to the exact Release its lineage map names."""

    updated = record_lineage_check(report, lineage_check)
    updated["release"] = {"releaseRef": lineage_check.release_ref}
    updated.pop("reportDigest", None)
    updated["reportDigest"] = _report_digest(updated)
    return updated


def build_evaluation_report(
    golden_set: GoldenSet,
    run: EvaluationRun,
    thresholds: EvaluationThresholds,
    *,
    generated_at: datetime,
) -> dict[str, object]:
    """Assemble independent layers and apply thresholds plus hard vetoes."""

    if type(golden_set) is not GoldenSet:
        raise TypeError("golden_set must be GoldenSet")
    if type(run) is not EvaluationRun:
        raise TypeError("run must be EvaluationRun")
    require_loaded_thresholds(thresholds)
    if thresholds.recorded_calibration_events and (
        thresholds.recorded_calibration_events[-1]["pilotDigest"]
        != golden_set.pilot_digest
    ):
        raise EvaluationRunUnavailable(
            "threshold calibration pilot digest does not match the golden set"
        )
    golden_by_ref = {case.case_ref: case for case in golden_set.cases}
    observed_by_ref = {case.case_ref: case for case in run.cases}
    if frozenset(golden_by_ref) != frozenset(observed_by_ref):
        raise EvaluationRunUnavailable(
            "evaluation run caseRef set must exactly match the golden set"
        )
    retrieval_inputs: list[RetrievalCaseInput] = []
    citation_inputs: list[CitationCaseInput] = []
    answer_inputs: list[AnswerCaseInput] = []
    security_inputs: list[CaseSecurityResult] = []
    for case_ref in sorted(golden_by_ref):
        golden = golden_by_ref[case_ref]
        observed = observed_by_ref[case_ref]
        security_observation = observed.security_observation
        if security_observation.case_ref != case_ref:
            raise EvaluationRunUnavailable(
                "security observation caseRef does not match evaluation case"
            )
        expected_refs = frozenset(
            _lineage_ref(item.lineage) for item in golden.expected_evidence
        )
        observed_refs = frozenset(
            _lineage_ref(item) for item in observed.observed_evidence
        )
        retrieval_inputs.append(
            RetrievalCaseInput(case_ref, expected_refs, observed_refs)
        )
        expected_by_claim = tuple(
            (
                claim.claim_ref,
                frozenset(_lineage_ref(item) for item in claim.expected_evidence),
            )
            for claim in golden.required_claims
        )
        citation_inputs.append(
            CitationCaseInput(
                case_ref,
                frozenset(claim.claim_ref for claim in golden.required_claims),
                tuple(
                    CitationClaim(
                        claim.claim_ref,
                        frozenset(_lineage_ref(item) for item in claim.cited_evidence),
                    )
                    for claim in observed.claims
                ),
                expected_by_claim,
                observed_refs,
            )
        )
        answer_inputs.append(
            AnswerCaseInput(
                case_ref,
                golden.answerability,
                observed.blind_score,
                observed.critical_contradiction,
                observed.refused,
            )
        )
        security_inputs.append(security_observation)
    retrieval = judge_retrieval(tuple(retrieval_inputs))
    citation = judge_citations(tuple(citation_inputs))
    answer = judge_answers(tuple(answer_inputs), run.answer_judge_profile)
    retrieval_by_ref = {
        case.case_ref: case.evidence_recall for case in retrieval.cases
    }
    citation_by_ref = {case.case_ref: case.completeness for case in citation.cases}
    answer_by_ref = {case.case_ref: case.normalized_score for case in answer.cases}
    score_by_layer = {
        "answer": answer_by_ref,
        "citation": citation_by_ref,
        "retrieval": retrieval_by_ref,
    }
    slice_reports: dict[str, object] = {}
    flat_slice_statuses: list[
        Literal["pass", "fail", "insufficient_data", "pending_preregistration"]
    ] = []
    for layer in ("answer", "citation", "retrieval"):
        case_scores = tuple(
            SliceCaseScore(case_ref, golden_by_ref[case_ref].slice_name, score)
            for case_ref, score in score_by_layer[layer].items()
        )
        layer_report = evaluate_layer_slice_thresholds(
            case_scores,
            thresholds,
            layer,
        )
        slice_reports[layer] = [asdict(result) for result in layer_report]
        flat_slice_statuses.extend(result.status for result in layer_report)
    retrieval_status = "measured"
    citation_status = citation.status
    answer_status: Literal[
        "pass", "fail", "insufficient_data", "pending_preregistration"
    ]
    if isinstance(thresholds.minimum_answer_score, PendingValue) or isinstance(
        thresholds.minimum_refusal_accuracy, PendingValue
    ):
        answer_status = "pending_preregistration"
    else:
        answer_status = (
            "pass"
            if answer.normalized_score
            >= thresholds.minimum_answer_score.value
            and (
                answer.refusal_accuracy is None
                or answer.refusal_accuracy
                >= thresholds.minimum_refusal_accuracy.value
            )
            else "fail"
        )
    status = final_report_status(
        EvaluationGateStatuses(
            retrieval="measured",
            citation=citation.status,
            answer=answer_status,
            slice_statuses=tuple(flat_slice_statuses),
            threshold_authority=thresholds.source_authority,
        ),
        tuple(security_inputs),
    )
    report: dict[str, object] = {
        "answer": {**asdict(answer), "status": answer_status},
        "citation": {**asdict(citation), "status": citation_status},
        "generatedAt": _timestamp(generated_at),
        "goldenSet": {
            "caseCount": len(golden_set.cases),
            "digest": golden_set.digest,
            "name": golden_set.name,
            "pilotDigest": golden_set.pilot_digest,
            "schemaVersion": "context-engine-golden-set-v1",
        },
        "lineageCheck": {
            "ran": False,
            "staleCaseCount": None,
            "totalCaseCount": len(golden_set.cases),
        },
        "reportVersion": EVAL_REPORT_VERSION,
        "retrieval": {**asdict(retrieval), "status": retrieval_status},
        "run": {"executedSeamRef": run.executed_seam_ref},
        "security": security_report(tuple(security_inputs)),
        "slices": slice_reports,
        "status": status,
        "thresholdAuthority": thresholds.source_authority,
        "thresholds": threshold_report_document(thresholds),
    }
    report["reportDigest"] = _report_digest(report)
    return report
