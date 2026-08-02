"""Executed golden runs and the private security-observation constructor."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, cast

from adapters.http.dogfood_client import (
    DogfoodEvaluationUnavailable,
    DogfoodHttpConfiguration,
    DogfoodResolveClient,
)
from engine.learning.eval_report import (
    CaseSecurityObservation,
    CaseSecurityResult,
    CaseSecurityViolation,
    HarnessSecurityEvent,
    SecurityEventKind,
    SecurityObservationState,
    _CaseSecurityObservationInput,
    _CaseSecurityViolationInput,
)
from engine.learning.eval_run import (
    EvaluationCaseObservation,
    EvaluationRun,
    EvaluationRunUnavailable,
    ObservedClaim,
)
from engine.learning.eval_run import (
    build_evaluation_report as _build_evaluation_report,
)
from engine.learning.golden import EvidenceLineage, GoldenSet
from engine.learning.judges import AnswerJudgeProfile
from engine.learning.thresholds import EvaluationThresholds

TRACKED_RUN_SEAM_REF: Final = "dogfood-loopback-resolve-acquire-v1"
ANSWER_JUDGMENT_SCHEMA_VERSION: Final = "context-engine-eval-judgment-v1"
_LINEAGE_FIELDS: Final = ("fragmentRef", "resourceRef", "revisionRef", "sourceRef")
_DECISION_BINDING: Final = (
    ("authorizationAsOf", "asOf"),
    ("decisionRef", "decisionRef"),
    ("policyEpoch", "policyEpoch"),
    ("policySnapshotRef", "policySnapshotRef"),
    ("purpose", "purpose"),
    ("runRef", "runRef"),
)
_SOURCE_ACL_KINDS: Final = frozenset({"live", "mirrored", "weak"})
_COVERAGE_STATUSES: Final = frozenset({"empty", "sufficient"})
_EXECUTOR_OWNED_FIELDS: Final = frozenset(
    {
        "missingContextFallbackCount",
        "observedEvidence",
        "refused",
        "securityObservation",
        "unauthorizedEvidenceCount",
        "wrongOrganizationEffectCount",
    }
)


@dataclass(frozen=True, slots=True)
class CaseAnswerJudgment:
    """One blind judge verdict; it claims nothing the executed run observes."""

    case_ref: str
    blind_score: int
    critical_contradiction: bool
    claims: tuple[ObservedClaim, ...]

    def __post_init__(self) -> None:
        if type(self.blind_score) is not int or self.blind_score not in {0, 1, 2}:
            raise EvaluationRunUnavailable("judged blindScore must be 0, 1, or 2")
        if type(self.critical_contradiction) is not bool:
            raise EvaluationRunUnavailable(
                "judged criticalContradiction must be bool"
            )
        if type(self.claims) is not tuple or any(
            type(claim) is not ObservedClaim for claim in self.claims
        ):
            raise EvaluationRunUnavailable("judged claims are malformed")


@dataclass(frozen=True, slots=True)
class AnswerJudgments:
    """The complete blind-judge input for one executed evaluation run."""

    answer_judge_profile: AnswerJudgeProfile
    cases: tuple[CaseAnswerJudgment, ...]

    def __post_init__(self) -> None:
        if type(self.answer_judge_profile) is not AnswerJudgeProfile:
            raise EvaluationRunUnavailable("answer judge profile is unavailable")
        if type(self.cases) is not tuple or not self.cases or any(
            type(case) is not CaseAnswerJudgment for case in self.cases
        ):
            raise EvaluationRunUnavailable("answer judgments are malformed")
        refs = tuple(case.case_ref for case in self.cases)
        if len(refs) != len(set(refs)):
            raise EvaluationRunUnavailable("judged caseRef values must be unique")


def _text(field_name: str, value: object) -> str:
    if type(value) is not str or not value or value.isspace() or value != value.strip():
        raise EvaluationRunUnavailable(f"{field_name} is unavailable")
    return value


def _closed(value: object, name: str, fields: frozenset[str]) -> dict[str, object]:
    if type(value) is not dict:
        raise EvaluationRunUnavailable(f"{name} is malformed")
    document = cast(dict[str, object], value)
    if _EXECUTOR_OWNED_FIELDS & frozenset(document):
        raise EvaluationRunUnavailable(
            "answer judgments must not claim what the executed run observes"
        )
    if frozenset(document) != fields:
        raise EvaluationRunUnavailable(f"{name} is malformed")
    return document


def _judged_lineage(value: object) -> EvidenceLineage:
    document = _closed(value, "judged claim Evidence", frozenset(_LINEAGE_FIELDS))
    return EvidenceLineage(
        source_ref=_text("judged sourceRef", document["sourceRef"]),
        resource_ref=_text("judged resourceRef", document["resourceRef"]),
        revision_ref=_text("judged revisionRef", document["revisionRef"]),
        fragment_ref=_text("judged fragmentRef", document["fragmentRef"]),
    )


def _judged_claim(value: object) -> ObservedClaim:
    document = _closed(
        value,
        "judged claim",
        frozenset({"claimRef", "citedEvidence"}),
    )
    cited = document["citedEvidence"]
    if type(cited) is not list:
        raise EvaluationRunUnavailable("judged claim citations are malformed")
    evidence = tuple(_judged_lineage(item) for item in cited)
    if not evidence or len(evidence) != len(set(evidence)):
        raise EvaluationRunUnavailable(
            "judged claim citations must be nonempty and unique"
        )
    return ObservedClaim(
        claim_ref=_text("judged claimRef", document["claimRef"]),
        cited_evidence=evidence,
    )


def _judged_case(value: object) -> CaseAnswerJudgment:
    document = _closed(
        value,
        "answer judgment case",
        frozenset({"blindScore", "caseRef", "claims", "criticalContradiction"}),
    )
    claims = document["claims"]
    if type(claims) is not list:
        raise EvaluationRunUnavailable("judged claims are malformed")
    judged = tuple(_judged_claim(item) for item in claims)
    claim_refs = tuple(claim.claim_ref for claim in judged)
    if len(claim_refs) != len(set(claim_refs)):
        raise EvaluationRunUnavailable("judged claimRef values must be unique")
    return CaseAnswerJudgment(
        case_ref=_text("judged caseRef", document["caseRef"]),
        blind_score=cast(int, document["blindScore"]),
        critical_contradiction=cast(bool, document["criticalContradiction"]),
        claims=judged,
    )


def load_answer_judgments(path: Path) -> AnswerJudgments:
    """Load the exact closed blind-judge document or refuse the whole run."""

    if not isinstance(path, Path):
        raise TypeError("answer judgment path must be Path")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        raise EvaluationRunUnavailable("answer judgments are unavailable") from None
    document = _closed(
        raw,
        "answer judgments",
        frozenset({"answerJudge", "cases", "schemaVersion"}),
    )
    if document["schemaVersion"] != ANSWER_JUDGMENT_SCHEMA_VERSION:
        raise EvaluationRunUnavailable("answer judgment version is unavailable")
    judge = _closed(
        document["answerJudge"],
        "answer judge",
        frozenset({"modelRef", "profileRef"}),
    )
    cases = document["cases"]
    if type(cases) is not list:
        raise EvaluationRunUnavailable("answer judgment cases are malformed")
    return AnswerJudgments(
        answer_judge_profile=AnswerJudgeProfile(
            model_ref=_text("answer judge modelRef", judge["modelRef"]),
            profile_ref=_text("answer judge profileRef", judge["profileRef"]),
        ),
        cases=tuple(_judged_case(item) for item in cases),
    )


@dataclass(frozen=True, slots=True)
class _DeliveryBinding:
    """The audience and purpose one executed resolve delivered under."""

    purpose: str
    audience_digest: str


@dataclass(frozen=True, slots=True)
class _ObservedCase:
    """Everything one executed case establishes about itself."""

    binding: _DeliveryBinding | None
    observed_evidence: tuple[EvidenceLineage, ...]
    refused: bool
    security_result: CaseSecurityResult


def _opaque(value: object) -> str | None:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or any(character.isspace() for character in value)
    ):
        return None
    return value


def _object(value: object, name: str) -> dict[str, object]:
    if type(value) is not dict:
        raise EvaluationRunUnavailable(f"executed {name} is unusable")
    return cast(dict[str, object], value)


def _list(value: object, name: str) -> list[object]:
    if type(value) is not list:
        raise EvaluationRunUnavailable(f"executed {name} is unusable")
    return cast(list[object], value)


def _delivery_binding(package: dict[str, object]) -> _DeliveryBinding | None:
    purpose = _opaque(package.get("purpose"))
    audience_digest = _opaque(package.get("audienceDigest"))
    if purpose is None or audience_digest is None:
        return None
    return _DeliveryBinding(purpose=purpose, audience_digest=audience_digest)


def _decision_binding(package: dict[str, object]) -> dict[str, object] | None:
    binding: dict[str, object] = {}
    for evidence_field, package_field in _DECISION_BINDING:
        value = package.get(package_field)
        if package_field == "policyEpoch":
            if type(value) is not int or value < 1:
                return None
        elif _opaque(value) is None:
            return None
        binding[evidence_field] = value
    return binding


def _authorized_evidence(
    value: object,
    decision_binding: dict[str, object] | None,
) -> bool:
    """One delivered Evidence carries its complete enclosing decision binding."""

    if decision_binding is None or type(value) is not dict:
        return False
    evidence = cast(dict[str, object], value)
    if _opaque(evidence.get("evidenceRef")) is None or any(
        _opaque(evidence.get(name)) is None for name in _LINEAGE_FIELDS
    ):
        return False
    projected = evidence.get("projectedFields")
    if (
        type(projected) is not list
        or not projected
        or any(_opaque(item) is None for item in projected)
    ):
        return False
    acl = evidence.get("sourceAclEvidence")
    if type(acl) is not dict or acl.get("kind") not in _SOURCE_ACL_KINDS:
        return False
    if type(evidence.get("policyEpoch")) is not int:
        return False
    return all(
        evidence.get(name) == expected for name, expected in decision_binding.items()
    )


def _grounded_block(value: object, evidence_refs: frozenset[str]) -> bool:
    """One delivered block cites exactly one Evidence the package delivered."""

    block = _object(value, "ContextBlock")
    _text("executed ContextBlock text", block.get("text"))
    refs = block.get("evidenceRefs")
    if type(refs) is not list or len(refs) != 1:
        return False
    return _opaque(refs[0]) in evidence_refs


def _security_result(
    case_ref: str,
    events: tuple[HarnessSecurityEvent, ...],
) -> CaseSecurityResult:
    """Construct the sole authoritative result of one genuinely executed case."""

    if events:
        return CaseSecurityViolation(
            _CaseSecurityViolationInput(case_ref=case_ref, events=events)
        )
    return CaseSecurityObservation(
        _CaseSecurityObservationInput(
            case_ref=case_ref,
            state=SecurityObservationState.OBSERVED_CLEAN,
        )
    )


def _observe_case(
    case_ref: str,
    outcome: dict[str, object],
    established: _DeliveryBinding | None,
) -> _ObservedCase:
    """Observe one executed resolve against the invariants it must satisfy."""

    package = _object(outcome.get("package"), "ContextPackage")
    blocks = _list(package.get("blocks"), "ContextPackage blocks")
    delivered = _list(package.get("evidence"), "ContextPackage Evidence")
    coverage = _object(package.get("coverage"), "ContextPackage coverage")
    status = coverage.get("status")
    if status not in _COVERAGE_STATUSES:
        raise EvaluationRunUnavailable("executed ContextPackage coverage is unusable")
    binding = _delivery_binding(package)
    decision_binding = _decision_binding(package)
    kinds: list[SecurityEventKind] = []
    lineage: list[EvidenceLineage] = []
    for item in delivered:
        if not _authorized_evidence(item, decision_binding):
            kinds.append("unauthorized_evidence")
            continue
        evidence = cast(dict[str, object], item)
        lineage.append(
            EvidenceLineage(
                source_ref=cast(str, evidence["sourceRef"]),
                resource_ref=cast(str, evidence["resourceRef"]),
                revision_ref=cast(str, evidence["revisionRef"]),
                fragment_ref=cast(str, evidence["fragmentRef"]),
            )
        )
    evidence_refs = frozenset(
        ref
        for item in delivered
        if type(item) is dict
        and (ref := _opaque(cast(dict[str, object], item).get("evidenceRef")))
        is not None
    )
    has_content = bool(blocks) or bool(delivered)
    if has_content != (status == "sufficient"):
        kinds.append("missing_context_fallback")
    kinds.extend(
        "missing_context_fallback"
        for block in blocks
        if not _grounded_block(block, evidence_refs)
    )
    if binding is None or (established is not None and binding != established):
        kinds.append("wrong_organization_effect")
    events = tuple(
        HarnessSecurityEvent(kind=kind, observation_ref=f"observation-{ordinal:02d}")
        for ordinal, kind in enumerate(kinds)
    )
    return _ObservedCase(
        binding=binding,
        observed_evidence=tuple(dict.fromkeys(lineage)),
        refused=not delivered,
        security_result=_security_result(case_ref, events),
    )


def _reject_secret_retention(
    configuration: DogfoodHttpConfiguration,
    golden_set: GoldenSet,
) -> None:
    """Refuse the configured bearer value anywhere in the golden corpus."""

    secret = configuration.secret
    for case in golden_set.cases:
        values = [case.case_ref, case.query, case.expected_answer, case.topic_cluster]
        for expectation in case.expected_evidence:
            values.append(expectation.path)
            values.extend(expectation.lineage.document().values())
        for claim in case.required_claims:
            values.append(claim.claim)
            for cited in claim.expected_evidence:
                values.extend(cited.document().values())
        if any(secret in value for value in values):
            raise EvaluationRunUnavailable(
                "golden set contains configured secret material"
            )


def execute_evaluation_report(
    golden_set: GoldenSet,
    judgments: AnswerJudgments,
    thresholds: EvaluationThresholds,
    *,
    generated_at: datetime,
) -> dict[str, object]:
    """Execute every golden case through the tracked seam and report the run.

    The seam is composed here from the process environment, so no caller can
    supply a transport, a callback, a counter, or a security result. A clean
    security observation exists only as a byproduct of responses this function
    fetched itself.
    """

    if type(golden_set) is not GoldenSet:
        raise TypeError("golden_set must be GoldenSet")
    if type(judgments) is not AnswerJudgments:
        raise TypeError("judgments must be AnswerJudgments")
    golden_by_ref = {case.case_ref: case for case in golden_set.cases}
    judged_by_ref = {case.case_ref: case for case in judgments.cases}
    if frozenset(golden_by_ref) != frozenset(judged_by_ref):
        raise EvaluationRunUnavailable(
            "answer judgment caseRef set must exactly match the golden set"
        )
    try:
        configuration = DogfoodHttpConfiguration.load()
    except DogfoodEvaluationUnavailable:
        raise EvaluationRunUnavailable(
            "tracked evaluation run seam is unavailable"
        ) from None
    _reject_secret_retention(configuration, golden_set)
    client = DogfoodResolveClient(configuration)
    established: _DeliveryBinding | None = None
    cases: list[EvaluationCaseObservation] = []
    for case_ref in sorted(golden_by_ref):
        try:
            outcome = client.acquire(
                query=golden_by_ref[case_ref].query,
                request_id=f"eval-v1-{case_ref}",
            )
        except DogfoodEvaluationUnavailable:
            raise EvaluationRunUnavailable(
                "tracked evaluation run seam is unavailable"
            ) from None
        observed = _observe_case(case_ref, outcome, established)
        established = established or observed.binding
        judged = judged_by_ref[case_ref]
        cases.append(
            EvaluationCaseObservation(
                case_ref=case_ref,
                observed_evidence=observed.observed_evidence,
                claims=judged.claims,
                blind_score=judged.blind_score,
                refused=observed.refused,
                critical_contradiction=judged.critical_contradiction,
                security_observation=observed.security_result,
            )
        )
    return _build_evaluation_report(
        golden_set,
        EvaluationRun(
            answer_judge_profile=judgments.answer_judge_profile,
            cases=tuple(cases),
            executed_seam_ref=TRACKED_RUN_SEAM_REF,
        ),
        thresholds,
        generated_at=generated_at,
    )
