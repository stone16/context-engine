"""Lifetime-bound release-operator call and atomic promotion port contracts."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import NoReturn, Protocol, cast
from uuid import UUID

import rfc8785

from engine.learning.contracts import (
    CanonicalJsonValue,
    _canonical_bigint,
    _require_digest,
    _require_generation,
    _require_ref,
    _require_uuid,
)
from engine.learning.evaluation import (
    ReleaseCandidate,
    ReleaseEvaluation,
    ReleaseEvaluationKeyring,
    ReleaseEvaluationStorePort,
    _require_utc,
    _timestamp,
)

_AUDIT_REASON_DIGEST_DOMAIN = b"context-engine.release-audit-reason.v1\x00"
_PROMOTION_CALL_DIGEST_DOMAIN = b"context-engine.trusted-promotion-call.v1\x00"
_MAX_AUDIT_REASON_LENGTH = 4096


class ReleaseOperatorAuthenticationRejected(Exception):
    """Opaque operator credentials or scope did not establish authority."""

    def __init__(self) -> None:
        super().__init__("release operator authentication rejected")


class ReleaseOperatorAuthorityUnavailable(RuntimeError):
    """The release-operator authenticator could not complete safely."""


class ReleasePromotionRejected(Exception):
    """One generic zero-state-change promotion refusal."""

    def __init__(self) -> None:
        super().__init__("release promotion rejected")


class ReleasePromotionUnavailable(RuntimeError):
    """The atomic release transaction could not complete safely."""


@dataclass(frozen=True, slots=True)
class VerifiedReleaseOperatorIdentity:
    """Trusted identity facts returned by the configured authenticator."""

    organization_id: UUID = field(repr=False)
    operator_ref: str = field(repr=False)
    authentication_binding_ref: str = field(repr=False)
    authority_ref: str = field(repr=False)
    authority_digest: str = field(repr=False)
    valid_from: datetime = field(repr=False)
    expires_at: datetime = field(repr=False)

    def __post_init__(self) -> None:
        _require_uuid("verified release operator organization_id", self.organization_id)
        for field_name in (
            "operator_ref",
            "authentication_binding_ref",
            "authority_ref",
        ):
            _require_ref(
                f"verified release operator {field_name}",
                getattr(self, field_name),
            )
        _require_digest(
            "verified release operator authority_digest",
            self.authority_digest,
        )
        if self.authority_digest != release_authority_digest(
            organization_id=self.organization_id,
            operator_ref=self.operator_ref,
            authentication_binding_ref=self.authentication_binding_ref,
            authority_ref=self.authority_ref,
        ):
            raise ValueError(
                "verified release operator authority digest must bind its grant"
            )
        valid_from = _require_utc(
            "verified release operator valid_from",
            self.valid_from,
        )
        expires_at = _require_utc(
            "verified release operator expires_at",
            self.expires_at,
        )
        if expires_at <= valid_from:
            raise ValueError("verified release operator lifetime must be positive")

    def __reduce__(self) -> NoReturn:
        raise TypeError("verified release operator identity is not serializable")


class ReleaseOperatorAuthenticator(Protocol):
    """Verify one opaque credential against the trusted operator source."""

    def authenticate(
        self,
        opaque_credential: str,
    ) -> VerifiedReleaseOperatorIdentity: ...


@dataclass(frozen=True, slots=True)
class PromotionAuthorizationRequest:
    """Untrusted request for one exact promotion-call lifetime."""

    organization_id: UUID = field(repr=False)
    promotion_ref: str = field(repr=False)
    candidate: ReleaseCandidate = field(repr=False)
    evaluation: ReleaseEvaluation = field(repr=False)
    request_id: str = field(repr=False)
    audit_reason: str = field(repr=False)
    opaque_credential: str = field(repr=False)

    def __post_init__(self) -> None:
        _require_uuid("promotion request organization_id", self.organization_id)
        _require_ref("promotion request promotion_ref", self.promotion_ref)
        if type(self.candidate) is not ReleaseCandidate:
            raise TypeError("promotion request candidate must be ReleaseCandidate")
        if type(self.evaluation) is not ReleaseEvaluation:
            raise TypeError("promotion request evaluation must be ReleaseEvaluation")
        if (
            self.candidate.organization_id != self.organization_id
            or self.evaluation.organization_id != self.organization_id
        ):
            raise ValueError("promotion request must stay in Organization")
        _require_ref("promotion request request_id", self.request_id)
        if (
            type(self.audit_reason) is not str
            or not self.audit_reason
            or self.audit_reason.isspace()
            or self.audit_reason != self.audit_reason.strip()
            or len(self.audit_reason) > _MAX_AUDIT_REASON_LENGTH
            or any(
                0xD800 <= ord(character) <= 0xDFFF
                for character in self.audit_reason
            )
        ):
            raise ValueError("promotion audit reason must be bounded nonblank Unicode")
        if (
            type(self.opaque_credential) is not str
            or not self.opaque_credential
            or self.opaque_credential.isspace()
        ):
            raise ValueError("promotion credential must be nonblank")

    def __reduce__(self) -> NoReturn:
        raise TypeError("promotion authorization request is not serializable")


class PromotionCallProvenance(StrEnum):
    """Closed trusted origin for a promotion call."""

    RELEASE_OPERATOR_AUTHORITY = "release_operator_authority"


class _PromotionAuthorityScope:
    __slots__ = ("_active", "_consumed", "_issuer_seal", "_seal")
    _active: bool
    _consumed: bool
    _issuer_seal: object
    _seal: object

    def __init__(self) -> None:
        raise TypeError("promotion authority scopes are not constructible")

    def __reduce__(self) -> NoReturn:
        raise TypeError("promotion authority scopes are not serializable")


_PROMOTION_AUTHORITY_SCOPE_SEAL = object()


@dataclass(frozen=True, slots=True, init=False, repr=False)
class TrustedPromotionCall:
    """One construction-sealed exact promotion input with no ambient authority."""

    organization_id: UUID = field(repr=False)
    promotion_ref: str = field(repr=False)
    operator_ref: str = field(repr=False)
    authentication_binding_ref: str = field(repr=False)
    authority_ref: str = field(repr=False)
    authority_digest: str = field(repr=False)
    candidate: ReleaseCandidate = field(repr=False)
    candidate_ref: str = field(repr=False)
    candidate_digest: str = field(repr=False)
    manifest_ref: str = field(repr=False)
    manifest_digest: str = field(repr=False)
    evaluation: ReleaseEvaluation = field(repr=False)
    evaluation_ref: str = field(repr=False)
    evaluation_digest: str = field(repr=False)
    expected_active_generation: int = field(repr=False)
    expected_base_manifest_digest: str | None = field(repr=False)
    issued_at: datetime = field(repr=False)
    expires_at: datetime = field(repr=False)
    request_id: str = field(repr=False)
    audit_reason_digest: str = field(repr=False)
    promotion_call_digest: str = field(repr=False)
    provenance: PromotionCallProvenance
    _authority_scope: _PromotionAuthorityScope = field(repr=False)

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("TrustedPromotionCall is authority-constructed")

    def __repr__(self) -> str:
        return "TrustedPromotionCall(<redacted>)"

    def __reduce__(self) -> NoReturn:
        raise TypeError("TrustedPromotionCall is not serializable")


def _audit_reason_digest(audit_reason: str) -> str:
    canonical = rfc8785.dumps(audit_reason)
    return hashlib.sha256(_AUDIT_REASON_DIGEST_DOMAIN + canonical).hexdigest()


def release_authority_digest(
    *,
    organization_id: UUID,
    operator_ref: str,
    authentication_binding_ref: str,
    authority_ref: str,
) -> str:
    """Digest the exact durable grant identity under its own authority domain."""

    _require_uuid("release authority organization_id", organization_id)
    for field_name, value in (
        ("operator_ref", operator_ref),
        ("authentication_binding_ref", authentication_binding_ref),
        ("authority_ref", authority_ref),
    ):
        _require_ref(f"release authority {field_name}", value)
    document: dict[str, CanonicalJsonValue] = {
        "authentication_binding_ref": authentication_binding_ref,
        "authority_ref": authority_ref,
        "operator_ref": operator_ref,
        "organization_id": str(organization_id),
    }
    canonical = rfc8785.dumps(document)
    return hashlib.sha256(
        b"context-engine.release-operator-authority.v1\x00" + canonical
    ).hexdigest()


def promotion_call_document(call: TrustedPromotionCall) -> dict[str, object]:
    """Return exact non-secret call facts protected by the call digest."""

    if type(call) is not TrustedPromotionCall:
        raise TypeError("call must be TrustedPromotionCall")
    return {
        "audit_reason_digest": call.audit_reason_digest,
        "authentication_binding_ref": call.authentication_binding_ref,
        "authority_digest": call.authority_digest,
        "authority_ref": call.authority_ref,
        "candidate_digest": call.candidate_digest,
        "candidate_ref": call.candidate_ref,
        "evaluation_digest": call.evaluation_digest,
        "evaluation_ref": call.evaluation_ref,
        "evaluation_signature": call.evaluation.signature.hex(),
        "evaluation_signing_key_version": _canonical_bigint(
            call.evaluation.signing_key_version
        ),
        "expected_active_generation": _canonical_bigint(
            call.expected_active_generation
        ),
        "expected_base_manifest_digest": call.expected_base_manifest_digest,
        "expires_at": _timestamp(call.expires_at),
        "issued_at": _timestamp(call.issued_at),
        "manifest_digest": call.manifest_digest,
        "manifest_ref": call.manifest_ref,
        "operator_ref": call.operator_ref,
        "organization_id": str(call.organization_id),
        "promotion_ref": call.promotion_ref,
        "request_id": call.request_id,
    }


def _promotion_call_digest(call: TrustedPromotionCall) -> str:
    document = promotion_call_document(call)
    canonical_document = cast(dict[str, CanonicalJsonValue], document)
    canonical = rfc8785.dumps(canonical_document)
    return hashlib.sha256(_PROMOTION_CALL_DIGEST_DOMAIN + canonical).hexdigest()


class ReleaseOperatorAuthority:
    """Authenticate and construct one lifetime-bound promotion call."""

    __slots__ = ("_authenticator", "_call_ttl", "_clock", "_issuer_seal")

    def __init__(
        self,
        authenticator: ReleaseOperatorAuthenticator,
        *,
        call_ttl: timedelta,
        clock: Callable[[], datetime],
    ) -> None:
        if not callable(getattr(authenticator, "authenticate", None)):
            raise TypeError("release operator authenticator is incomplete")
        if type(call_ttl) is not timedelta or call_ttl <= timedelta(0):
            raise ValueError("promotion call TTL must be a positive timedelta")
        if not callable(clock):
            raise TypeError("promotion authority clock must be callable")
        self._authenticator = authenticator
        self._call_ttl = call_ttl
        self._clock = clock
        self._issuer_seal = object()

    def authorize(
        self,
        request: PromotionAuthorizationRequest,
    ) -> AbstractContextManager[TrustedPromotionCall]:
        """Authenticate and retain the exact call only for one context lifetime."""

        if type(request) is not PromotionAuthorizationRequest:
            raise TypeError(
                "promotion authorization requires PromotionAuthorizationRequest"
            )
        return self._authorized(request)

    @contextmanager
    def _authorized(
        self,
        request: PromotionAuthorizationRequest,
    ) -> Iterator[TrustedPromotionCall]:
        try:
            identity = self._authenticator.authenticate(request.opaque_credential)
        except ReleaseOperatorAuthenticationRejected:
            raise ReleaseOperatorAuthenticationRejected from None
        except Exception:
            raise ReleaseOperatorAuthorityUnavailable(
                "release operator authority is unavailable"
            ) from None
        if type(identity) is not VerifiedReleaseOperatorIdentity:
            raise ReleaseOperatorAuthorityUnavailable(
                "release operator authority is unavailable"
            )
        now = _require_utc("promotion authority clock", self._clock())
        if (
            identity.organization_id != request.organization_id
            or now < identity.valid_from
            or now >= identity.expires_at
        ):
            raise ReleaseOperatorAuthenticationRejected from None
        expires_at = min(identity.expires_at, now + self._call_ttl)
        if expires_at <= now:
            raise ReleaseOperatorAuthenticationRejected from None

        scope = object.__new__(_PromotionAuthorityScope)
        scope._active = True
        scope._consumed = False
        scope._issuer_seal = self._issuer_seal
        scope._seal = _PROMOTION_AUTHORITY_SCOPE_SEAL
        call = object.__new__(TrustedPromotionCall)
        candidate = request.candidate
        evaluation = request.evaluation
        if (
            evaluation.organization_id != candidate.organization_id
            or evaluation.candidate_ref != candidate.candidate_ref
            or evaluation.candidate_digest != candidate.candidate_digest
            or evaluation.manifest_ref != candidate.manifest.manifest_ref
            or evaluation.manifest_digest != candidate.manifest.manifest_digest
            or evaluation.expected_active_generation
            != candidate.expected_active_generation
            or evaluation.expected_base_manifest_digest
            != candidate.expected_base_manifest_digest
        ):
            raise ReleaseOperatorAuthenticationRejected from None
        values: dict[str, object] = {
            "organization_id": request.organization_id,
            "promotion_ref": request.promotion_ref,
            "operator_ref": identity.operator_ref,
            "authentication_binding_ref": identity.authentication_binding_ref,
            "authority_ref": identity.authority_ref,
            "authority_digest": identity.authority_digest,
            "candidate": candidate,
            "candidate_ref": candidate.candidate_ref,
            "candidate_digest": candidate.candidate_digest,
            "manifest_ref": candidate.manifest.manifest_ref,
            "manifest_digest": candidate.manifest.manifest_digest,
            "evaluation": evaluation,
            "evaluation_ref": evaluation.evaluation_ref,
            "evaluation_digest": evaluation.evaluation_digest,
            "expected_active_generation": candidate.expected_active_generation,
            "expected_base_manifest_digest": (
                candidate.expected_base_manifest_digest
            ),
            "issued_at": now,
            "expires_at": expires_at,
            "request_id": request.request_id,
            "audit_reason_digest": _audit_reason_digest(request.audit_reason),
            "promotion_call_digest": "0" * 64,
            "provenance": PromotionCallProvenance.RELEASE_OPERATOR_AUTHORITY,
            "_authority_scope": scope,
        }
        for field_name, value in values.items():
            object.__setattr__(call, field_name, value)
        object.__setattr__(call, "promotion_call_digest", _promotion_call_digest(call))
        try:
            yield call
        finally:
            scope._active = False


def _require_active_trusted_promotion_call(
    call: TrustedPromotionCall,
    *,
    authority: ReleaseOperatorAuthority,
) -> None:
    if type(call) is not TrustedPromotionCall:
        raise ReleasePromotionRejected
    scope = call._authority_scope
    if (
        call.provenance is not PromotionCallProvenance.RELEASE_OPERATOR_AUTHORITY
        or type(scope) is not _PromotionAuthorityScope
        or getattr(scope, "_seal", None) is not _PROMOTION_AUTHORITY_SCOPE_SEAL
        or not getattr(scope, "_active", False)
        or getattr(scope, "_issuer_seal", None) is not authority._issuer_seal
    ):
        raise ReleasePromotionRejected
    try:
        expected_digest = _promotion_call_digest(call)
    except (TypeError, ValueError, AttributeError):
        raise ReleasePromotionRejected from None
    if not hmac.compare_digest(call.promotion_call_digest, expected_digest):
        raise ReleasePromotionRejected


def _consume_trusted_promotion_call(call: TrustedPromotionCall) -> None:
    scope = call._authority_scope
    if getattr(scope, "_consumed", True):
        raise ReleasePromotionRejected
    scope._consumed = True


@dataclass(frozen=True, slots=True)
class PromotionCommit:
    """Database-owned exact outcome staged by the atomic promotion function."""

    organization_id: UUID = field(repr=False)
    promotion_ref: str
    active_generation: int
    manifest_ref: str
    manifest_digest: str = field(repr=False)
    promoted_at: datetime

    def __post_init__(self) -> None:
        _require_uuid("PromotionCommit organization_id", self.organization_id)
        _require_ref("PromotionCommit promotion_ref", self.promotion_ref)
        generation = _require_generation(
            "PromotionCommit active_generation",
            self.active_generation,
        )
        if generation == 0:
            raise ValueError("PromotionCommit generation must be positive")
        _require_ref("PromotionCommit manifest_ref", self.manifest_ref)
        _require_digest("PromotionCommit manifest_digest", self.manifest_digest)
        _require_utc("PromotionCommit promoted_at", self.promoted_at)


@dataclass(frozen=True, slots=True, init=False)
class PromotionReceipt:
    """Success receipt constructible only after the retained commit completes."""

    organization_id: UUID = field(repr=False)
    promotion_ref: str
    active_generation: int
    manifest_ref: str
    manifest_digest: str = field(repr=False)
    promoted_at: datetime

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("PromotionReceipt is commit-constructed")

    def __reduce__(self) -> NoReturn:
        raise TypeError("PromotionReceipt is not serializable")


def _construct_promotion_receipt(commit: PromotionCommit) -> PromotionReceipt:
    if type(commit) is not PromotionCommit:
        raise TypeError("promotion receipt requires PromotionCommit")
    receipt = object.__new__(PromotionReceipt)
    for field_name in (
        "organization_id",
        "promotion_ref",
        "active_generation",
        "manifest_ref",
        "manifest_digest",
        "promoted_at",
    ):
        object.__setattr__(receipt, field_name, getattr(commit, field_name))
    return receipt


class ReleasePromotionTransactionPort(Protocol):
    """One retained transaction; database time and durable rows are authority."""

    def revalidate_promotion(
        self,
        call: TrustedPromotionCall,
        *,
        evaluation_keyring: ReleaseEvaluationKeyring,
    ) -> None: ...

    def promote_atomically(self, call: TrustedPromotionCall) -> PromotionCommit: ...

    def commit(self) -> None: ...


class ReleaseStorePort(ReleaseEvaluationStorePort, Protocol):
    """Candidate/evaluation storage plus the sole narrow promotion transaction."""

    def transaction(
        self,
        organization_id: UUID,
    ) -> AbstractContextManager[ReleasePromotionTransactionPort]: ...
