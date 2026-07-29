"""Report gates and the in-process security-observation construction seam."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, NoReturn

type SecurityEventKind = Literal[
    "unauthorized_evidence",
    "wrong_organization_effect",
    "missing_context_fallback",
]
type GateStatus = Literal[
    "pass", "fail", "insufficient_data", "pending_preregistration"
]


def _case_ref(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or value.isspace()
        or value != value.strip()
        or any(character.isspace() for character in value)
    ):
        raise ValueError("security observation case_ref is unavailable")
    return value


@dataclass(frozen=True, slots=True)
class HarnessSecurityEvent:
    """One content-free hard-oracle event emitted during harness execution."""

    kind: SecurityEventKind
    observation_ref: str

    def __post_init__(self) -> None:
        if self.kind not in {
            "unauthorized_evidence",
            "wrong_organization_effect",
            "missing_context_fallback",
        }:
            raise ValueError("harness security event kind is unavailable")
        _case_ref(self.observation_ref)


class SecurityObservationState(StrEnum):
    """Closed non-violation states; absence can never mean observed clean."""

    OBSERVED_CLEAN = "observed_clean"
    NOT_OBSERVED = "not_observed"
    MALFORMED = "malformed"


_SECURITY_HARNESS_SEAL = object()


@dataclass(frozen=True, slots=True, init=False)
class CaseSecurityObservation:
    """A sealed clean or unestablished observation constructed by the harness."""

    case_ref: str
    state: SecurityObservationState
    _seal: object = field(repr=False)

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("security observations are harness-constructed")

    def __reduce__(self) -> NoReturn:
        raise TypeError("security observations are not serializable")


@dataclass(frozen=True, slots=True, init=False)
class CaseSecurityViolation:
    """A sealed nonempty violation set produced during harness execution."""

    case_ref: str
    events: tuple[HarnessSecurityEvent, ...]
    _seal: object = field(repr=False)

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("security violations are harness-constructed")

    def __reduce__(self) -> NoReturn:
        raise TypeError("security violations are not serializable")


type CaseSecurityResult = CaseSecurityObservation | CaseSecurityViolation


def _observation(
    case_ref: str,
    state: SecurityObservationState,
) -> CaseSecurityObservation:
    result = object.__new__(CaseSecurityObservation)
    object.__setattr__(result, "case_ref", _case_ref(case_ref))
    object.__setattr__(result, "state", state)
    object.__setattr__(result, "_seal", _SECURITY_HARNESS_SEAL)
    return result


class SecurityHarness:
    """Construct trusted security results only from live in-process events.

    Serialized run input never invokes this seam. The run executor owns one
    instance and records typed events as its hard oracles execute; the harness,
    rather than caller-authored JSON, derives every report counter.
    """

    __slots__ = ()

    def observe(
        self,
        case_ref: str,
        events: tuple[HarnessSecurityEvent, ...],
    ) -> CaseSecurityResult:
        if type(events) is not tuple or any(
            type(event) is not HarnessSecurityEvent for event in events
        ):
            raise TypeError("harness security events must be a typed tuple")
        refs = tuple(event.observation_ref for event in events)
        if len(refs) != len(set(refs)):
            raise ValueError("harness security observation refs must be unique")
        if not events:
            return _observation(case_ref, SecurityObservationState.OBSERVED_CLEAN)
        result = object.__new__(CaseSecurityViolation)
        object.__setattr__(result, "case_ref", _case_ref(case_ref))
        object.__setattr__(result, "events", events)
        object.__setattr__(result, "_seal", _SECURITY_HARNESS_SEAL)
        return result

    def not_observed(self, case_ref: str) -> CaseSecurityObservation:
        return _observation(case_ref, SecurityObservationState.NOT_OBSERVED)

    def malformed(self, case_ref: str) -> CaseSecurityObservation:
        return _observation(case_ref, SecurityObservationState.MALFORMED)


@dataclass(frozen=True, slots=True)
class EvaluationGateStatuses:
    """Typed layer and gate states that preserve pending as a distinct value."""

    retrieval: Literal["measured"]
    citation: Literal["pass", "fail"]
    answer: GateStatus
    slice_statuses: tuple[GateStatus, ...]
    threshold_authority: Literal["tracked", "non_authoritative"]

    def __post_init__(self) -> None:
        if self.retrieval != "measured":
            raise ValueError("retrieval status is unavailable")
        if self.citation not in {"pass", "fail"}:
            raise ValueError("citation status is unavailable")
        allowed = {
            "pass",
            "fail",
            "insufficient_data",
            "pending_preregistration",
        }
        if self.answer not in allowed:
            raise ValueError("answer status is unavailable")
        if type(self.slice_statuses) is not tuple or not self.slice_statuses:
            raise ValueError("evaluation requires slice statuses")
        if any(status not in allowed for status in self.slice_statuses):
            raise ValueError("slice status is unavailable")
        if self.threshold_authority not in {"tracked", "non_authoritative"}:
            raise ValueError("threshold authority is unavailable")


def _require_harness_results(
    results: tuple[CaseSecurityResult, ...],
) -> None:
    if type(results) is not tuple or not results:
        raise ValueError("security observations are required")
    refs: list[str] = []
    for result in results:
        if type(result) not in {CaseSecurityObservation, CaseSecurityViolation}:
            raise TypeError("security results must be harness-constructed")
        if getattr(result, "_seal", None) is not _SECURITY_HARNESS_SEAL:
            raise TypeError("security results must be harness-constructed")
        refs.append(result.case_ref)
    if len(refs) != len(set(refs)):
        raise ValueError("security observation case refs must be unique")


def final_report_status(
    gates: EvaluationGateStatuses,
    security_results: tuple[CaseSecurityResult, ...],
) -> Literal[
    "PASS",
    "FAIL",
    "REFUSED",
    "NON_AUTHORITATIVE",
    "INSUFFICIENT_DATA",
    "PENDING_PREREGISTRATION",
]:
    """Render the whole report without collapsing any tri-state gate to a score."""

    if type(gates) is not EvaluationGateStatuses:
        raise TypeError("gates must be EvaluationGateStatuses")
    _require_harness_results(security_results)
    if any(type(result) is CaseSecurityViolation for result in security_results):
        return "FAIL"
    observations = tuple(
        result
        for result in security_results
        if type(result) is CaseSecurityObservation
    )
    if any(
        observation.state is not SecurityObservationState.OBSERVED_CLEAN
        for observation in observations
    ):
        return "REFUSED"
    if gates.threshold_authority != "tracked":
        return "NON_AUTHORITATIVE"
    all_quality_statuses = (gates.answer, *gates.slice_statuses)
    if any(status == "pending_preregistration" for status in all_quality_statuses):
        return "PENDING_PREREGISTRATION"
    if gates.citation == "fail" or any(
        status == "fail" for status in all_quality_statuses
    ):
        return "FAIL"
    if any(status == "insufficient_data" for status in all_quality_statuses):
        return "INSUFFICIENT_DATA"
    return "PASS"


def security_report(
    security_results: tuple[CaseSecurityResult, ...],
) -> dict[str, object]:
    """Render only harness-derived counts; unobserved counts remain absent/null."""

    _require_harness_results(security_results)
    violations = tuple(
        result
        for result in security_results
        if type(result) is CaseSecurityViolation
    )
    if violations:
        events = tuple(event for result in violations for event in result.events)
        return {
            "missingContextFallbackCount": sum(
                event.kind == "missing_context_fallback" for event in events
            ),
            "observationState": "observed_violation",
            "status": "fail",
            "unauthorizedEvidenceCount": sum(
                event.kind == "unauthorized_evidence" for event in events
            ),
            "wrongOrganizationEffectCount": sum(
                event.kind == "wrong_organization_effect" for event in events
            ),
        }
    observations = tuple(
        result
        for result in security_results
        if type(result) is CaseSecurityObservation
    )
    if any(
        observation.state is SecurityObservationState.MALFORMED
        for observation in observations
    ):
        state = SecurityObservationState.MALFORMED
    elif any(
        observation.state is SecurityObservationState.NOT_OBSERVED
        for observation in observations
    ):
        state = SecurityObservationState.NOT_OBSERVED
    else:
        state = SecurityObservationState.OBSERVED_CLEAN
    if state is not SecurityObservationState.OBSERVED_CLEAN:
        return {
            "missingContextFallbackCount": None,
            "observationState": state.value,
            "status": "refused",
            "unauthorizedEvidenceCount": None,
            "wrongOrganizationEffectCount": None,
        }
    return {
        "missingContextFallbackCount": 0,
        "observationState": state.value,
        "status": "pass",
        "unauthorizedEvidenceCount": 0,
        "wrongOrganizationEffectCount": 0,
    }
