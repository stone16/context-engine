"""Pure layered quality judges and preregistered slice-floor evaluation."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import sqrt
from typing import Literal


def _case_ref(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or value.isspace()
        or value != value.strip()
        or len(value) > 128
    ):
        raise ValueError("judge case_ref must be bounded nonblank text")
    return value


def _refs(field_name: str, value: object) -> frozenset[str]:
    if type(value) is not frozenset:
        raise TypeError(f"{field_name} must be frozenset")
    references = value
    if any(
        type(reference) is not str
        or not reference
        or reference.isspace()
        or reference != reference.strip()
        for reference in references
    ):
        raise ValueError(f"{field_name} must contain nonblank references")
    return references


@dataclass(frozen=True, slots=True)
class RetrievalCaseInput:
    """Expected and observed content-free Evidence identities for one case."""

    case_ref: str
    expected_evidence: frozenset[str]
    observed_evidence: frozenset[str]

    def __post_init__(self) -> None:
        _case_ref(self.case_ref)
        _refs("expected_evidence", self.expected_evidence)
        _refs("observed_evidence", self.observed_evidence)


@dataclass(frozen=True, slots=True)
class RetrievalCaseResult:
    case_ref: str
    expected_count: int
    hit_count: int
    case_hit: bool
    evidence_recall: float


@dataclass(frozen=True, slots=True)
class RetrievalReport:
    case_hit: float
    macro_evidence_recall: float
    micro_evidence_recall: float
    hit_cases: int
    total_cases: int
    evidence_hits: int
    total_expected_evidence: int
    cases: tuple[RetrievalCaseResult, ...]


def judge_retrieval(cases: tuple[RetrievalCaseInput, ...]) -> RetrievalReport:
    """Compute exact hit rate plus macro/micro Evidence recall."""

    if type(cases) is not tuple or not cases:
        raise ValueError("retrieval judge requires a nonempty tuple")
    results: list[RetrievalCaseResult] = []
    for case in cases:
        if type(case) is not RetrievalCaseInput:
            raise TypeError("retrieval cases must be RetrievalCaseInput")
        expected_count = len(case.expected_evidence)
        hit_count = len(case.expected_evidence & case.observed_evidence)
        if expected_count == 0:
            case_hit = not case.observed_evidence
            evidence_recall = 1.0 if case_hit else 0.0
        else:
            case_hit = hit_count == expected_count
            evidence_recall = hit_count / expected_count
        results.append(
            RetrievalCaseResult(
                case_ref=case.case_ref,
                expected_count=expected_count,
                hit_count=hit_count,
                case_hit=case_hit,
                evidence_recall=evidence_recall,
            )
        )
    ordered = tuple(sorted(results, key=lambda result: result.case_ref))
    hit_cases = sum(result.case_hit for result in ordered)
    evidence_hits = sum(result.hit_count for result in ordered)
    expected = sum(result.expected_count for result in ordered)
    micro_recall = (
        evidence_hits / expected
        if expected
        else sum(result.evidence_recall for result in ordered) / len(ordered)
    )
    return RetrievalReport(
        case_hit=hit_cases / len(ordered),
        macro_evidence_recall=sum(result.evidence_recall for result in ordered)
        / len(ordered),
        micro_evidence_recall=micro_recall,
        hit_cases=hit_cases,
        total_cases=len(ordered),
        evidence_hits=evidence_hits,
        total_expected_evidence=expected,
        cases=ordered,
    )


@dataclass(frozen=True, slots=True)
class CitationClaim:
    """One produced claim's deterministic citation outcome."""

    claim_ref: str
    cited_evidence: frozenset[str]

    def __post_init__(self) -> None:
        _case_ref(self.claim_ref)
        if not _refs("cited_evidence", self.cited_evidence):
            raise ValueError("citation claim requires cited Evidence")


@dataclass(frozen=True, slots=True)
class CitationCaseInput:
    """Required claims and produced claim/citation judgments for one case."""

    case_ref: str
    required_claim_refs: frozenset[str]
    claims: tuple[CitationClaim, ...]
    expected_evidence_by_claim: tuple[tuple[str, frozenset[str]], ...]
    resolvable_evidence: frozenset[str]

    def __post_init__(self) -> None:
        _case_ref(self.case_ref)
        if not _refs("required_claim_refs", self.required_claim_refs):
            raise ValueError("citation judge requires required claims")
        if type(self.claims) is not tuple:
            raise TypeError("citation claims must be a tuple")
        if any(type(claim) is not CitationClaim for claim in self.claims):
            raise TypeError("citation claims must be CitationClaim")
        refs = tuple(claim.claim_ref for claim in self.claims)
        if len(refs) != len(set(refs)):
            raise ValueError("citation claim_ref values must be unique")
        if type(self.expected_evidence_by_claim) is not tuple:
            raise TypeError("expected evidence mapping must be a tuple")
        expected_claims: set[str] = set()
        for item in self.expected_evidence_by_claim:
            if type(item) is not tuple or len(item) != 2:
                raise TypeError("expected evidence mapping entry is malformed")
            claim_ref, evidence_refs = item
            _case_ref(claim_ref)
            if not _refs("expected claim Evidence", evidence_refs):
                raise ValueError("expected claim Evidence must be nonempty")
            expected_claims.add(claim_ref)
        if expected_claims != set(self.required_claim_refs):
            raise ValueError("expected evidence mapping must cover required claims")
        _refs("resolvable_evidence", self.resolvable_evidence)


@dataclass(frozen=True, slots=True)
class CitationCaseResult:
    case_ref: str
    lineage_resolvability: float
    claim_support: float
    completeness: float


@dataclass(frozen=True, slots=True)
class CitationReport:
    lineage_resolvability: float
    claim_support: float
    completeness: float
    status: Literal["pass", "fail"]
    cases: tuple[CitationCaseResult, ...]


def judge_citations(cases: tuple[CitationCaseInput, ...]) -> CitationReport:
    """Evaluate lineage, citation precision, and required-claim completeness."""

    if type(cases) is not tuple or not cases:
        raise ValueError("citation judge requires a nonempty tuple")
    results: list[CitationCaseResult] = []
    for case in cases:
        if type(case) is not CitationCaseInput:
            raise TypeError("citation cases must be CitationCaseInput")
        expected_by_claim = dict(case.expected_evidence_by_claim)
        resolvable = tuple(
            bool(claim.cited_evidence)
            and claim.cited_evidence <= case.resolvable_evidence
            for claim in case.claims
        )
        supported = tuple(
            claim.claim_ref in expected_by_claim
            and bool(claim.cited_evidence & expected_by_claim[claim.claim_ref])
            and claim.cited_evidence <= case.resolvable_evidence
            for claim in case.claims
        )
        supported_required = {
            claim.claim_ref
            for claim, is_supported in zip(case.claims, supported, strict=True)
            if is_supported
        }
        if case.claims:
            lineage_score = sum(resolvable) / len(resolvable)
            support_score = sum(supported) / len(supported)
        else:
            lineage_score = 0.0
            support_score = 0.0
        results.append(
            CitationCaseResult(
                case_ref=case.case_ref,
                lineage_resolvability=lineage_score,
                claim_support=support_score,
                completeness=len(supported_required) / len(case.required_claim_refs),
            )
        )
    ordered = tuple(sorted(results, key=lambda result: result.case_ref))
    lineage = sum(result.lineage_resolvability for result in ordered) / len(ordered)
    return CitationReport(
        lineage_resolvability=lineage,
        claim_support=sum(result.claim_support for result in ordered) / len(ordered),
        completeness=sum(result.completeness for result in ordered) / len(ordered),
        status="pass" if lineage == 1.0 else "fail",
        cases=ordered,
    )


@dataclass(frozen=True, slots=True)
class AnswerJudgeProfile:
    """Attributable identity for the nondeterministic blind answer layer."""

    model_ref: str
    profile_ref: str

    def __post_init__(self) -> None:
        _case_ref(self.model_ref)
        _case_ref(self.profile_ref)


@dataclass(frozen=True, slots=True)
class AnswerCaseInput:
    case_ref: str
    answerability: Literal["answerable", "unanswerable"]
    blind_score: int
    critical_contradiction: bool = False
    refused: bool = False

    def __post_init__(self) -> None:
        _case_ref(self.case_ref)
        if self.answerability not in {"answerable", "unanswerable"}:
            raise ValueError("answerability is unavailable")
        if type(self.blind_score) is not int or self.blind_score not in {0, 1, 2}:
            raise ValueError("blind answer score must be 0, 1, or 2")
        if (
            type(self.critical_contradiction) is not bool
            or type(self.refused) is not bool
        ):
            raise TypeError("answer judge flags must be bool")


@dataclass(frozen=True, slots=True)
class AnswerCaseResult:
    case_ref: str
    normalized_score: float
    critical_contradiction: bool
    refused: bool


@dataclass(frozen=True, slots=True)
class AnswerReport:
    normalized_score: float
    refusal_accuracy: float | None
    critical_contradictions: int
    model_ref: str
    profile_ref: str
    cases: tuple[AnswerCaseResult, ...]


def judge_answers(
    cases: tuple[AnswerCaseInput, ...],
    profile: AnswerJudgeProfile,
) -> AnswerReport:
    """Normalize attributable blind judgments with contradiction/refusal vetoes."""

    if type(cases) is not tuple or not cases:
        raise ValueError("answer judge requires a nonempty tuple")
    if type(profile) is not AnswerJudgeProfile:
        raise TypeError("answer judge profile is required")
    results: list[AnswerCaseResult] = []
    refusal_correct: list[bool] = []
    for case in cases:
        if type(case) is not AnswerCaseInput:
            raise TypeError("answer cases must be AnswerCaseInput")
        score = case.blind_score / 2
        if case.critical_contradiction:
            score = 0.0
        elif case.refused:
            correct_refusal = case.answerability == "unanswerable"
            score = 1.0 if correct_refusal else 0.0
            refusal_correct.append(correct_refusal)
        elif case.answerability == "unanswerable":
            refusal_correct.append(False)
            score = 0.0
        results.append(
            AnswerCaseResult(
                case_ref=case.case_ref,
                normalized_score=score,
                critical_contradiction=case.critical_contradiction,
                refused=case.refused,
            )
        )
    ordered = tuple(sorted(results, key=lambda result: result.case_ref))
    return AnswerReport(
        normalized_score=sum(result.normalized_score for result in ordered)
        / len(ordered),
        refusal_accuracy=(
            sum(refusal_correct) / len(refusal_correct) if refusal_correct else None
        ),
        critical_contradictions=sum(
            result.critical_contradiction for result in ordered
        ),
        model_ref=profile.model_ref,
        profile_ref=profile.profile_ref,
        cases=ordered,
    )


@dataclass(frozen=True, slots=True)
class SliceCaseScore:
    case_ref: str
    slice_name: Literal["single_doc", "cross_doc", "temporal"]
    score: float

    def __post_init__(self) -> None:
        _case_ref(self.case_ref)
        if self.slice_name not in {"single_doc", "cross_doc", "temporal"}:
            raise ValueError("slice_name is unavailable")
        if type(self.score) is not float or not 0.0 <= self.score <= 1.0:
            raise ValueError("slice score must be a fraction")


@dataclass(frozen=True, slots=True)
class SliceFloor:
    slice_name: Literal["single_doc", "cross_doc", "temporal"]
    minimum_cases: int
    minimum_score: float

    def __post_init__(self) -> None:
        if self.slice_name not in {"single_doc", "cross_doc", "temporal"}:
            raise ValueError("slice floor name is unavailable")
        if type(self.minimum_cases) is not int or self.minimum_cases <= 0:
            raise ValueError("slice minimum_cases must be positive")
        if (
            type(self.minimum_score) is not float
            or not 0.0 <= self.minimum_score <= 1.0
        ):
            raise ValueError("slice minimum_score must be a fraction")


@dataclass(frozen=True, slots=True)
class SliceFloorResult:
    slice_name: str
    case_count: int
    score: float | None
    wilson_95_low: float | None
    wilson_95_high: float | None
    status: Literal[
        "pass", "fail", "insufficient_data", "pending_preregistration"
    ]


def _wilson_95(successes: float, total: int) -> tuple[float, float]:
    """Wilson score interval for a bounded success fraction at z=1.96."""

    if total <= 0:
        raise ValueError("Wilson interval requires observations")
    z = 1.96
    proportion = successes / total
    denominator = 1 + (z * z / total)
    center = (proportion + z * z / (2 * total)) / denominator
    margin = (
        z
        * sqrt((proportion * (1 - proportion) / total) + z * z / (4 * total * total))
        / denominator
    )
    return max(0.0, center - margin), min(1.0, center + margin)


def evaluate_slice_floors(
    cases: tuple[SliceCaseScore, ...],
    floors: tuple[SliceFloor, ...],
) -> tuple[SliceFloorResult, ...]:
    """Evaluate every preregistered slice independently without false passes."""

    if type(cases) is not tuple or any(
        type(case) is not SliceCaseScore for case in cases
    ):
        raise TypeError("slice cases must be a tuple of SliceCaseScore")
    if type(floors) is not tuple or not floors or any(
        type(floor) is not SliceFloor for floor in floors
    ):
        raise TypeError("slice floors must be a nonempty tuple of SliceFloor")
    if len({floor.slice_name for floor in floors}) != len(floors):
        raise ValueError("slice floors must be unique")
    by_slice: dict[str, list[float]] = defaultdict(list)
    for case in cases:
        by_slice[case.slice_name].append(case.score)
    results: list[SliceFloorResult] = []
    for floor in floors:
        scores = by_slice[floor.slice_name]
        if len(scores) < floor.minimum_cases:
            if scores:
                score = sum(scores) / len(scores)
                low, high = _wilson_95(sum(scores), len(scores))
            else:
                score = None
                low = None
                high = None
            results.append(
                SliceFloorResult(
                    slice_name=floor.slice_name,
                    case_count=len(scores),
                    score=score,
                    wilson_95_low=low,
                    wilson_95_high=high,
                    status="insufficient_data",
                )
            )
            continue
        score = sum(scores) / len(scores)
        low, high = _wilson_95(sum(scores), len(scores))
        results.append(
            SliceFloorResult(
                slice_name=floor.slice_name,
                case_count=len(scores),
                score=score,
                wilson_95_low=low,
                wilson_95_high=high,
                status="pass" if score >= floor.minimum_score else "fail",
            )
        )
    return tuple(sorted(results, key=lambda result: result.slice_name))


def evaluate_pending_slice_floors(
    cases: tuple[SliceCaseScore, ...],
) -> tuple[SliceFloorResult, ...]:
    """Report measured slice precision while preregistration remains pending."""

    if type(cases) is not tuple or any(
        type(case) is not SliceCaseScore for case in cases
    ):
        raise TypeError("slice cases must be a tuple of SliceCaseScore")
    by_slice: dict[str, list[float]] = defaultdict(list)
    for case in cases:
        by_slice[case.slice_name].append(case.score)
    results: list[SliceFloorResult] = []
    for slice_name in ("cross_doc", "single_doc", "temporal"):
        scores = by_slice[slice_name]
        if scores:
            score = sum(scores) / len(scores)
            low, high = _wilson_95(sum(scores), len(scores))
        else:
            score = None
            low = None
            high = None
        results.append(
            SliceFloorResult(
                slice_name=slice_name,
                case_count=len(scores),
                score=score,
                wilson_95_low=low,
                wilson_95_high=high,
                status="pending_preregistration",
            )
        )
    return tuple(results)
