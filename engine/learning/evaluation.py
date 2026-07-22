"""Canonical candidate and signed four-veto release evaluation contracts."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Final, NoReturn, Protocol, cast
from uuid import UUID

import rfc8785

from engine.learning.contracts import (
    MAX_SIGNED_BIGINT,
    CanonicalJsonValue,
    CurationMode,
    ReleaseManifest,
    _canonical_bigint,
    _require_digest,
    _require_generation,
    _require_ref,
    _require_uuid,
    verify_release_manifest,
)

RELEASE_EVALUATION_DIGEST_PROFILE: Final = (
    "release-evaluation-rfc8785-sha256-v1"
)
RELEASE_EVALUATION_SIGNATURE_PROFILE: Final = (
    "release-evaluation-hmac-sha256-v1"
)
_CANDIDATE_DIGEST_DOMAIN: Final = b"context-engine.release-candidate.v1\x00"
_COMPATIBILITY_DIGEST_DOMAIN: Final = (
    b"context-engine.release-compatibility.v1\x00"
)
_EVALUATION_DIGEST_DOMAIN: Final = b"context-engine.release-evaluation.v1\x00"
_EVALUATION_SIGNATURE_DOMAIN: Final = (
    b"context-engine.release-evaluation-signature.v1\x00"
)
_MINIMUM_SIGNING_KEY_BYTES: Final = 32
_MAX_COMMAND_LENGTH: Final = 2048


class Gate(StrEnum):
    """Four independent release veto classes, never a blended score."""

    SECURITY = "security"
    RELIABILITY = "reliability"
    QUALITY = "quality"
    BUDGET = "budget"


class GateStatus(StrEnum):
    """Closed outcome for one required gate."""

    PASS = "pass"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class GateEvidence:
    """One independent gate result bound to immutable evidence."""

    gate: Gate
    status: GateStatus
    evidence_digest: str = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.gate) is not Gate:
            raise TypeError("release gate must be Gate")
        if type(self.status) is not GateStatus:
            raise TypeError("release gate status must be GateStatus")
        _require_digest("release gate evidence_digest", self.evidence_digest)


def _gate_document(evidence: GateEvidence) -> dict[str, object]:
    if type(evidence) is not GateEvidence:
        raise TypeError("gate evidence must be GateEvidence")
    return {
        "evidence_digest": evidence.evidence_digest,
        "gate": evidence.gate.value,
        "status": evidence.status.value,
    }


def _require_gate_set(value: object) -> tuple[GateEvidence, ...]:
    if type(value) is not tuple or len(value) != len(Gate):
        raise ValueError("release candidate requires exactly four gate results")
    evidence = value
    if any(type(item) is not GateEvidence for item in evidence):
        raise TypeError("release candidate gates must be GateEvidence")
    if tuple(item.gate for item in evidence) != tuple(Gate):
        raise ValueError(
            "release candidate gates must use Security/Reliability/Quality/Budget "
            "canonical order"
        )
    return evidence


def _require_commands(value: object) -> tuple[str, ...]:
    if type(value) is not tuple or not value:
        raise ValueError("release verification commands must be a nonempty tuple")
    commands = value
    for command in commands:
        if (
            type(command) is not str
            or not command
            or command.isspace()
            or command != command.strip()
            or len(command) > _MAX_COMMAND_LENGTH
            or any(0xD800 <= ord(character) <= 0xDFFF for character in command)
        ):
            raise ValueError(
                "release verification command must be bounded nonblank Unicode"
            )
    if len(set(commands)) != len(commands):
        raise ValueError("release verification commands must be unique")
    return commands


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _require_utc(field_name: str, value: object) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError(f"{field_name} must be aware UTC")
    return value


def _domain_digest(domain: bytes, document: dict[str, object]) -> str:
    canonical_document = cast(dict[str, CanonicalJsonValue], document)
    return hashlib.sha256(domain + rfc8785.dumps(canonical_document)).hexdigest()


@dataclass(frozen=True, slots=True)
class ReleaseCandidateRef:
    """Opaque exact locator for an immutable Organization-owned candidate."""

    organization_id: UUID = field(repr=False)
    candidate_ref: str = field(repr=False)
    candidate_digest: str = field(repr=False)

    def __post_init__(self) -> None:
        _require_uuid("ReleaseCandidateRef organization_id", self.organization_id)
        _require_ref("ReleaseCandidateRef candidate_ref", self.candidate_ref)
        _require_digest("ReleaseCandidateRef candidate_digest", self.candidate_digest)

    def __reduce__(self) -> NoReturn:
        raise TypeError("ReleaseCandidateRef is not serializable")


@dataclass(frozen=True, slots=True)
class ReleaseCandidate:
    """Immutable release proposal bound to exact active base and gate evidence."""

    organization_id: UUID = field(repr=False)
    candidate_ref: str
    manifest: ReleaseManifest = field(repr=False)
    expected_active_generation: int
    expected_base_manifest_digest: str | None = field(repr=False)
    gate_evidence: tuple[GateEvidence, ...] = field(repr=False)
    capability_coverage_digest: str = field(repr=False)
    fixture_digest: str = field(repr=False)
    verification_commands: tuple[str, ...] = field(repr=False)
    candidate_digest: str = field(init=False)

    def __post_init__(self) -> None:
        _require_uuid("ReleaseCandidate organization_id", self.organization_id)
        _require_ref("ReleaseCandidate candidate_ref", self.candidate_ref)
        if type(self.manifest) is not ReleaseManifest:
            raise TypeError("ReleaseCandidate manifest must be ReleaseManifest")
        if self.manifest.organization_id != self.organization_id:
            raise ValueError("ReleaseCandidate manifest must stay in Organization")
        if not verify_release_manifest(self.manifest):
            raise ValueError("ReleaseCandidate manifest digest is invalid")
        generation = _require_generation(
            "ReleaseCandidate expected_active_generation",
            self.expected_active_generation,
        )
        if generation >= MAX_SIGNED_BIGINT:
            raise ValueError("ReleaseCandidate generation cannot be incremented")
        if generation == 0:
            if self.expected_base_manifest_digest is not None:
                raise ValueError("initial ReleaseCandidate requires an absent base")
        else:
            if self.expected_base_manifest_digest is None:
                raise ValueError("noninitial ReleaseCandidate requires an exact base")
            _require_digest(
                "ReleaseCandidate expected_base_manifest_digest",
                self.expected_base_manifest_digest,
            )
        _require_gate_set(self.gate_evidence)
        _require_digest(
            "ReleaseCandidate capability_coverage_digest",
            self.capability_coverage_digest,
        )
        _require_digest("ReleaseCandidate fixture_digest", self.fixture_digest)
        _require_commands(self.verification_commands)
        object.__setattr__(
            self,
            "candidate_digest",
            _domain_digest(_CANDIDATE_DIGEST_DOMAIN, candidate_document(self)),
        )

    def reference(self) -> ReleaseCandidateRef:
        return ReleaseCandidateRef(
            organization_id=self.organization_id,
            candidate_ref=self.candidate_ref,
            candidate_digest=self.candidate_digest,
        )

    def __reduce__(self) -> NoReturn:
        raise TypeError("ReleaseCandidate is not serializable")


def candidate_document(candidate: ReleaseCandidate) -> dict[str, object]:
    """Return the exact canonical candidate facts protected by its digest."""

    if type(candidate) is not ReleaseCandidate:
        raise TypeError("candidate must be ReleaseCandidate")
    return {
        "candidate_ref": candidate.candidate_ref,
        "capability_coverage_digest": candidate.capability_coverage_digest,
        "expected_active_generation": _canonical_bigint(
            candidate.expected_active_generation
        ),
        "expected_base_manifest_digest": candidate.expected_base_manifest_digest,
        "fixture_digest": candidate.fixture_digest,
        "gate_evidence": [_gate_document(item) for item in candidate.gate_evidence],
        "manifest_digest": candidate.manifest.manifest_digest,
        "manifest_ref": candidate.manifest.manifest_ref,
        "organization_id": str(candidate.organization_id),
        "verification_commands": list(candidate.verification_commands),
    }


def verify_release_candidate(candidate: ReleaseCandidate) -> bool:
    """Detect mutation or digest substitution in candidate or manifest lineage."""

    if type(candidate) is not ReleaseCandidate or not verify_release_manifest(
        candidate.manifest
    ):
        return False
    try:
        expected = _domain_digest(
            _CANDIDATE_DIGEST_DOMAIN,
            candidate_document(candidate),
        )
        _require_gate_set(candidate.gate_evidence)
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(candidate.candidate_digest, expected)


def _compatibility(candidate: ReleaseCandidate) -> tuple[bool, str]:
    """Return M0 compatibility; successful curation-on remains inactive."""

    passed = candidate.manifest.curation_profile.mode is CurationMode.OFF
    document: dict[str, object] = {
        "active_revision_refs": list(candidate.manifest.active_revision_refs),
        "candidate_digest": candidate.candidate_digest,
        "curation_mode": candidate.manifest.curation_profile.mode.value,
        "curation_snapshot_ref": (
            candidate.manifest.curation_profile.curation_snapshot_ref
        ),
        "manifest_digest": candidate.manifest.manifest_digest,
        "passed": passed,
    }
    return passed, _domain_digest(_COMPATIBILITY_DIGEST_DOMAIN, document)


def _evaluation_document(
    *,
    candidate: ReleaseCandidate,
    compatibility_passed: bool,
    compatibility_evidence_digest: str,
    evaluated_at: datetime,
) -> dict[str, object]:
    return {
        "candidate_digest": candidate.candidate_digest,
        "candidate_ref": candidate.candidate_ref,
        "capability_coverage_digest": candidate.capability_coverage_digest,
        "compatibility_evidence_digest": compatibility_evidence_digest,
        "compatibility_passed": compatibility_passed,
        "evaluated_at": _timestamp(evaluated_at),
        "expected_active_generation": _canonical_bigint(
            candidate.expected_active_generation
        ),
        "expected_base_manifest_digest": candidate.expected_base_manifest_digest,
        "fixture_digest": candidate.fixture_digest,
        "gate_evidence": [_gate_document(item) for item in candidate.gate_evidence],
        "manifest_digest": candidate.manifest.manifest_digest,
        "manifest_ref": candidate.manifest.manifest_ref,
        "organization_id": str(candidate.organization_id),
        "verification_commands": list(candidate.verification_commands),
    }


@dataclass(frozen=True, slots=True, repr=False)
class ReleaseEvaluation:
    """Immutable signed evaluation over four independent veto gates."""

    organization_id: UUID = field(repr=False)
    candidate_ref: str
    candidate_digest: str = field(repr=False)
    manifest_ref: str
    manifest_digest: str = field(repr=False)
    expected_active_generation: int
    expected_base_manifest_digest: str | None = field(repr=False)
    gate_evidence: tuple[GateEvidence, ...] = field(repr=False)
    compatibility_passed: bool
    compatibility_evidence_digest: str = field(repr=False)
    capability_coverage_digest: str = field(repr=False)
    fixture_digest: str = field(repr=False)
    verification_commands: tuple[str, ...] = field(repr=False)
    evaluated_at: datetime
    signing_key_version: int = field(repr=False)
    signature: bytes = field(repr=False)
    digest_profile: str = field(
        default=RELEASE_EVALUATION_DIGEST_PROFILE,
        init=False,
    )
    signature_profile: str = field(
        default=RELEASE_EVALUATION_SIGNATURE_PROFILE,
        init=False,
    )
    evaluation_ref: str = field(init=False)
    evaluation_digest: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        _require_uuid("ReleaseEvaluation organization_id", self.organization_id)
        _require_ref("ReleaseEvaluation candidate_ref", self.candidate_ref)
        _require_digest("ReleaseEvaluation candidate_digest", self.candidate_digest)
        _require_ref("ReleaseEvaluation manifest_ref", self.manifest_ref)
        _require_digest("ReleaseEvaluation manifest_digest", self.manifest_digest)
        generation = _require_generation(
            "ReleaseEvaluation expected_active_generation",
            self.expected_active_generation,
        )
        if generation == 0:
            if self.expected_base_manifest_digest is not None:
                raise ValueError("initial ReleaseEvaluation requires an absent base")
        elif self.expected_base_manifest_digest is None:
            raise ValueError("noninitial ReleaseEvaluation requires an exact base")
        else:
            _require_digest(
                "ReleaseEvaluation expected_base_manifest_digest",
                self.expected_base_manifest_digest,
            )
        _require_gate_set(self.gate_evidence)
        if type(self.compatibility_passed) is not bool:
            raise TypeError("ReleaseEvaluation compatibility must be bool")
        _require_digest(
            "ReleaseEvaluation compatibility_evidence_digest",
            self.compatibility_evidence_digest,
        )
        _require_digest(
            "ReleaseEvaluation capability_coverage_digest",
            self.capability_coverage_digest,
        )
        _require_digest("ReleaseEvaluation fixture_digest", self.fixture_digest)
        _require_commands(self.verification_commands)
        _require_utc("ReleaseEvaluation evaluated_at", self.evaluated_at)
        if (
            type(self.signing_key_version) is not int
            or not 1 <= self.signing_key_version <= MAX_SIGNED_BIGINT
        ):
            raise ValueError(
                "ReleaseEvaluation signing key version must be positive bigint"
            )
        if type(self.signature) is not bytes or len(self.signature) != 32:
            raise ValueError("ReleaseEvaluation signature must be 32-byte HMAC-SHA256")
        document = evaluation_document(self)
        digest = _domain_digest(_EVALUATION_DIGEST_DOMAIN, document)
        object.__setattr__(self, "evaluation_digest", digest)
        object.__setattr__(self, "evaluation_ref", f"evaluation_{digest}")

    def __reduce__(self) -> NoReturn:
        raise TypeError("ReleaseEvaluation is not serializable")

    def __repr__(self) -> str:
        return "ReleaseEvaluation(<redacted>)"


def evaluation_document(evaluation: ReleaseEvaluation) -> dict[str, object]:
    """Return the exact signed evaluation facts, excluding signature metadata."""

    if type(evaluation) is not ReleaseEvaluation:
        raise TypeError("evaluation must be ReleaseEvaluation")
    return {
        "candidate_digest": evaluation.candidate_digest,
        "candidate_ref": evaluation.candidate_ref,
        "capability_coverage_digest": evaluation.capability_coverage_digest,
        "compatibility_evidence_digest": (
            evaluation.compatibility_evidence_digest
        ),
        "compatibility_passed": evaluation.compatibility_passed,
        "evaluated_at": _timestamp(evaluation.evaluated_at),
        "expected_active_generation": _canonical_bigint(
            evaluation.expected_active_generation
        ),
        "expected_base_manifest_digest": (
            evaluation.expected_base_manifest_digest
        ),
        "fixture_digest": evaluation.fixture_digest,
        "gate_evidence": [_gate_document(item) for item in evaluation.gate_evidence],
        "manifest_digest": evaluation.manifest_digest,
        "manifest_ref": evaluation.manifest_ref,
        "organization_id": str(evaluation.organization_id),
        "verification_commands": list(evaluation.verification_commands),
    }


class ReleaseEvaluationKeyring:
    """Dedicated versioned evaluation signer with no ambient/default key."""

    __slots__ = ("_active_version", "_keys")

    def __init__(self, *, active_version: int, keys: Mapping[int, bytes]) -> None:
        if (
            type(active_version) is not int
            or not 1 <= active_version <= MAX_SIGNED_BIGINT
        ):
            raise ValueError("active evaluation signing version must be positive")
        if not isinstance(keys, Mapping) or not keys:
            raise ValueError("evaluation keyring requires explicit versioned keys")
        copied: dict[int, bytes] = {}
        for version, key in keys.items():
            if type(version) is not int or not 1 <= version <= MAX_SIGNED_BIGINT:
                raise ValueError("evaluation signing key version must be positive")
            if type(key) is not bytes or len(key) < _MINIMUM_SIGNING_KEY_BYTES:
                raise ValueError("evaluation signing keys require at least 256 bits")
            copied[version] = bytes(key)
        if active_version not in copied:
            raise ValueError("active evaluation signing version must exist")
        self._active_version = active_version
        self._keys = MappingProxyType(copied)

    @property
    def active_version(self) -> int:
        return self._active_version

    def _sign(self, evaluation_digest: str) -> bytes:
        return hmac.digest(
            self._keys[self._active_version],
            _EVALUATION_SIGNATURE_DOMAIN + bytes.fromhex(evaluation_digest),
            "sha256",
        )

    def _verify(self, evaluation: ReleaseEvaluation) -> bool:
        key = self._keys.get(evaluation.signing_key_version)
        if key is None:
            return False
        expected = hmac.digest(
            key,
            _EVALUATION_SIGNATURE_DOMAIN
            + bytes.fromhex(evaluation.evaluation_digest),
            "sha256",
        )
        return hmac.compare_digest(evaluation.signature, expected)

    def __repr__(self) -> str:
        return "ReleaseEvaluationKeyring(<redacted>)"

    def __reduce__(self) -> NoReturn:
        raise TypeError("ReleaseEvaluationKeyring is not serializable")


def evaluate_candidate(
    candidate: ReleaseCandidate,
    *,
    keyring: ReleaseEvaluationKeyring,
    evaluated_at: datetime,
) -> ReleaseEvaluation:
    """Create one signed immutable evaluation without publication authority."""

    if type(candidate) is not ReleaseCandidate or not verify_release_candidate(
        candidate
    ):
        raise ValueError("release candidate digest is invalid")
    if type(keyring) is not ReleaseEvaluationKeyring:
        raise TypeError("evaluation keyring must be ReleaseEvaluationKeyring")
    checked_at = _require_utc("release evaluation time", evaluated_at)
    compatibility_passed, compatibility_digest = _compatibility(candidate)
    document = _evaluation_document(
        candidate=candidate,
        compatibility_passed=compatibility_passed,
        compatibility_evidence_digest=compatibility_digest,
        evaluated_at=checked_at,
    )
    digest = _domain_digest(_EVALUATION_DIGEST_DOMAIN, document)
    return ReleaseEvaluation(
        organization_id=candidate.organization_id,
        candidate_ref=candidate.candidate_ref,
        candidate_digest=candidate.candidate_digest,
        manifest_ref=candidate.manifest.manifest_ref,
        manifest_digest=candidate.manifest.manifest_digest,
        expected_active_generation=candidate.expected_active_generation,
        expected_base_manifest_digest=candidate.expected_base_manifest_digest,
        gate_evidence=candidate.gate_evidence,
        compatibility_passed=compatibility_passed,
        compatibility_evidence_digest=compatibility_digest,
        capability_coverage_digest=candidate.capability_coverage_digest,
        fixture_digest=candidate.fixture_digest,
        verification_commands=candidate.verification_commands,
        evaluated_at=checked_at,
        signing_key_version=keyring.active_version,
        signature=keyring._sign(digest),
    )


def verify_release_evaluation(
    evaluation: ReleaseEvaluation,
    *,
    candidate: ReleaseCandidate,
    keyring: ReleaseEvaluationKeyring,
) -> bool:
    """Verify canonical digest, signature, candidate binding, and compatibility."""

    if (
        type(evaluation) is not ReleaseEvaluation
        or type(candidate) is not ReleaseCandidate
        or type(keyring) is not ReleaseEvaluationKeyring
        or not verify_release_candidate(candidate)
    ):
        return False
    try:
        expected_digest = _domain_digest(
            _EVALUATION_DIGEST_DOMAIN,
            evaluation_document(evaluation),
        )
        compatibility_passed, compatibility_digest = _compatibility(candidate)
    except (TypeError, ValueError):
        return False
    exact_binding = (
        evaluation.organization_id == candidate.organization_id
        and evaluation.candidate_ref == candidate.candidate_ref
        and evaluation.candidate_digest == candidate.candidate_digest
        and evaluation.manifest_ref == candidate.manifest.manifest_ref
        and evaluation.manifest_digest == candidate.manifest.manifest_digest
        and evaluation.expected_active_generation
        == candidate.expected_active_generation
        and evaluation.expected_base_manifest_digest
        == candidate.expected_base_manifest_digest
        and evaluation.gate_evidence == candidate.gate_evidence
        and evaluation.compatibility_passed is compatibility_passed
        and evaluation.compatibility_evidence_digest == compatibility_digest
        and evaluation.capability_coverage_digest
        == candidate.capability_coverage_digest
        and evaluation.fixture_digest == candidate.fixture_digest
        and evaluation.verification_commands == candidate.verification_commands
        and evaluation.evaluation_ref
        == f"evaluation_{evaluation.evaluation_digest}"
        and evaluation.digest_profile == RELEASE_EVALUATION_DIGEST_PROFILE
        and evaluation.signature_profile == RELEASE_EVALUATION_SIGNATURE_PROFILE
    )
    return (
        exact_binding
        and hmac.compare_digest(evaluation.evaluation_digest, expected_digest)
        and keyring._verify(evaluation)
    )


class ReleaseEvaluationStorePort(Protocol):
    """Persistence boundary that has no active-pointer publication operation."""

    def load_candidate(
        self,
        candidate_ref: ReleaseCandidateRef,
    ) -> ReleaseCandidate: ...

    def persist_evaluation(self, evaluation: ReleaseEvaluation) -> None: ...


class ReleaseEvaluationUnavailable(RuntimeError):
    """Candidate/evaluation persistence could not complete safely."""


type ReleaseClock = Callable[[], datetime]
