"""Closed pytest user-property contract for measured hard-oracle observations."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence

OBSERVATION_PROPERTY = "context_engine.security_gate.observation.v1"
SECURITY_EVIDENCE_MARKER = "security_evidence"
SECURITY_EVIDENCE_MARKER_VERSION = "1.0.0"
SECURITY_EVIDENCE_LAYERS = frozenset({"property", "postgres", "runtime"})
ORACLE_RESULT_KEYS: tuple[str, ...] = (
    "unauthorizedEvidenceCount",
    "wrongOrganizationEffectCount",
    "missingContextFallbackCount",
)
_FIXTURE_REF = re.compile(r"^ACCEPT-[0-9]{3}$")
_EVIDENCE_REF = re.compile(r"^[A-Z][A-Z0-9-]+$")


class ObservationValidationError(ValueError):
    """A fixture observation is incomplete, ambiguous, or noncanonical."""


class SecurityEvidenceMarkerValidationError(ValueError):
    """A collected test's execution-owned evidence identity is not canonical."""


def _count(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ObservationValidationError(
            f"{field} must be an integer greater than or equal to zero"
        )
    return value


def normalize_fixture_observation(value: object) -> dict[str, object]:
    """Validate and copy one exact, JSON-safe hard-oracle observation."""

    if not isinstance(value, Mapping):
        raise ObservationValidationError("observation must be an object")
    if set(value) != {"fixtureRef", "evidenceRef", "values"}:
        raise ObservationValidationError(
            "observation fields must be fixtureRef, evidenceRef, and values"
        )
    fixture_ref = value.get("fixtureRef")
    evidence_ref = value.get("evidenceRef")
    values = value.get("values")
    if not isinstance(fixture_ref, str) or _FIXTURE_REF.fullmatch(fixture_ref) is None:
        raise ObservationValidationError("fixtureRef must be an ACCEPT-NNN ID")
    if (
        not isinstance(evidence_ref, str)
        or _EVIDENCE_REF.fullmatch(evidence_ref) is None
    ):
        raise ObservationValidationError("evidenceRef must be a stable evidence ID")
    if not isinstance(values, Mapping) or set(values) != set(ORACLE_RESULT_KEYS):
        raise ObservationValidationError(
            "values must contain exactly the three canonical hard-oracle counts"
        )
    normalized_values = {
        key: _count(values.get(key), key) for key in ORACLE_RESULT_KEYS
    }
    return {
        "fixtureRef": fixture_ref,
        "evidenceRef": evidence_ref,
        "values": normalized_values,
    }


def normalize_security_evidence_marker(
    args: Sequence[object], kwargs: Mapping[str, object]
) -> dict[str, str]:
    """Validate one closed ``security_evidence`` v1 marker declaration."""

    if args or set(kwargs) != {"id", "layer"}:
        raise SecurityEvidenceMarkerValidationError(
            "security_evidence marker must declare exactly id and layer keywords"
        )
    evidence_id = kwargs.get("id")
    layer = kwargs.get("layer")
    if not isinstance(evidence_id, str) or _EVIDENCE_REF.fullmatch(evidence_id) is None:
        raise SecurityEvidenceMarkerValidationError(
            "security_evidence marker id must be a stable evidence ID"
        )
    if not isinstance(layer, str) or layer not in SECURITY_EVIDENCE_LAYERS:
        raise SecurityEvidenceMarkerValidationError(
            "security_evidence marker layer must be property, postgres, or runtime"
        )
    return {"id": evidence_id, "layer": layer}


def record_fixture_observation(
    record_property: Callable[[str, object], None],
    *,
    fixture_ref: str,
    evidence_ref: str,
    unauthorized_evidence_count: int,
    wrong_organization_effect_count: int,
    missing_context_fallback_count: int,
) -> None:
    """Publish one explicit observation after the fixture's assertions complete."""

    observation = normalize_fixture_observation(
        {
            "fixtureRef": fixture_ref,
            "evidenceRef": evidence_ref,
            "values": {
                "unauthorizedEvidenceCount": unauthorized_evidence_count,
                "wrongOrganizationEffectCount": wrong_organization_effect_count,
                "missingContextFallbackCount": missing_context_fallback_count,
            },
        }
    )
    record_property(OBSERVATION_PROPERTY, observation)
