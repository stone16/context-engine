"""Opaque multi-use citation locators that carry no authorization."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from hashlib import sha256
from re import fullmatch
from secrets import token_hex
from typing import Final, NoReturn, Protocol
from uuid import UUID

from engine.runtime.contracts import CitationOpenRef
from engine.runtime.evidence import CandidateRef

CITATION_OPEN_REF_PREFIX: Final = "cor"
CITATION_OPEN_DIGEST_PROFILE: Final = "citation-open-ref-sha256-v1"
CITATION_OPEN_RETENTION_CLASS: Final = "restricted_citation_locator_lineage"


class CitationLocatorNotAvailable(Exception):
    """The locator does not name currently usable content-free lineage."""

    def __init__(self) -> None:
        super().__init__("citation not available")


class CitationAuthorityUnavailable(RuntimeError):
    """Citation persistence could not make a safe decision."""


def _require_nonblank(field_name: str, value: object) -> str:
    if type(value) is not str or not value or value.isspace():
        raise ValueError(f"citation {field_name} must be nonblank")
    return value


def _require_utc(field_name: str, value: object) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError(f"citation {field_name} must be aware UTC")
    return value


def _is_citation_open_ref(value: object) -> bool:
    return type(value) is str and fullmatch(r"cor_[0-9a-f]{64}", value) is not None


@dataclass(frozen=True, slots=True)
class CitationOpenProfile:
    """Server-owned locator lifetime and digest-retention policy."""

    profile_ref: str
    retention_policy_ref: str
    maximum_ttl: timedelta
    retention_period: timedelta
    retention_class: str = CITATION_OPEN_RETENTION_CLASS

    def __post_init__(self) -> None:
        _require_nonblank("profile_ref", self.profile_ref)
        _require_nonblank("retention_policy_ref", self.retention_policy_ref)
        if type(self.maximum_ttl) is not timedelta or self.maximum_ttl <= timedelta(0):
            raise ValueError("citation maximum_ttl must be positive")
        if (
            type(self.retention_period) is not timedelta
            or self.retention_period < self.maximum_ttl
        ):
            raise ValueError("citation retention must cover the locator lifetime")
        if self.retention_class != CITATION_OPEN_RETENTION_CLASS:
            raise ValueError("citation retention class is not active")


PRIVATE_FILE_CITATION_OPEN_PROFILE: Final = CitationOpenProfile(
    profile_ref="private-citation-open-v1",
    retention_policy_ref="citation-locator-retention-v1",
    maximum_ttl=timedelta(minutes=10),
    retention_period=timedelta(days=30),
)


@dataclass(frozen=True, slots=True)
class CitationOpenIssue:
    """Authorized Package/Evidence lineage eligible for locator issuance."""

    organization_id: UUID = field(repr=False)
    package_ref: str = field(repr=False)
    evidence_ref: str = field(repr=False)
    resource_ref: str = field(repr=False)
    revision_id: UUID = field(repr=False)
    fragment_ref: str = field(repr=False)
    issued_at: datetime = field(repr=False)
    expires_at: datetime = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.organization_id) is not UUID or type(self.revision_id) is not UUID:
            raise TypeError("citation Organization and Revision must be UUID")
        if fullmatch(r"pkg_[0-9a-f]{32}", self.package_ref) is None:
            raise ValueError("citation package_ref must use the closed format")
        if fullmatch(r"ev_[0-9a-f]{64}", self.evidence_ref) is None:
            raise ValueError("citation evidence_ref must use the closed format")
        _require_nonblank("resource_ref", self.resource_ref)
        _require_nonblank("fragment_ref", self.fragment_ref)
        _require_utc("issued_at", self.issued_at)
        _require_utc("expires_at", self.expires_at)
        if self.expires_at <= self.issued_at:
            raise ValueError("citation expiry must follow issuance")


@dataclass(frozen=True, slots=True)
class CitationOpenRedemption:
    """Current opener's content-free locator lookup request."""

    citation_open_ref: CitationOpenRef = field(repr=False)
    organization_id: UUID = field(repr=False)
    opened_at: datetime = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.citation_open_ref) is not CitationOpenRef:
            raise TypeError("citation redemption requires CitationOpenRef")
        if type(self.organization_id) is not UUID:
            raise TypeError("citation redemption Organization must be UUID")
        _require_utc("opened_at", self.opened_at)


@dataclass(frozen=True, slots=True)
class CitationOpenTargetLineage:
    """Prior Package/Evidence location only; never a prior authorization fact."""

    package_ref: str
    evidence_ref: str

    def __post_init__(self) -> None:
        if fullmatch(r"pkg_[0-9a-f]{32}", self.package_ref) is None:
            raise ValueError("citation target package_ref must use the closed format")
        if fullmatch(r"ev_[0-9a-f]{64}", self.evidence_ref) is None:
            raise ValueError("citation target evidence_ref must use the closed format")


@dataclass(frozen=True, slots=True)
class CitationOpenTarget:
    """Content-free candidate lineage returned before exact reauthorization."""

    candidate_ref: CandidateRef = field(repr=False)
    lineage: CitationOpenTargetLineage

    def __post_init__(self) -> None:
        if type(self.candidate_ref) is not CandidateRef:
            raise TypeError("citation target requires CandidateRef")
        if type(self.lineage) is not CitationOpenTargetLineage:
            raise TypeError("citation target lineage has the wrong type")


class CitationOpenPort(Protocol):
    """Digest-only issue and content-free multi-use lookup boundary."""

    def issue(
        self,
        *,
        request: CitationOpenIssue,
        locator_digest: bytes,
        digest_profile: str,
        profile: CitationOpenProfile,
        retain_until: datetime,
    ) -> bool: ...

    def redeem(self, request: CitationOpenRedemption) -> CitationOpenTarget | None: ...


class CitationOpenRetentionPort(Protocol):
    """Organization-scoped cleanup using authority-owned current time."""

    def delete_expired_lineage(self, organization_id: UUID) -> int: ...


class CitationOpenRetention:
    """Delete locator digests only after their versioned retention window."""

    def __init__(self, port: CitationOpenRetentionPort) -> None:
        if not callable(getattr(port, "delete_expired_lineage", None)):
            raise TypeError("citation retention port is incomplete")
        self._port = port

    def delete_expired(self, organization_id: UUID) -> int:
        if type(organization_id) is not UUID:
            raise TypeError("citation retention requires an Organization UUID")
        try:
            deleted = self._port.delete_expired_lineage(organization_id)
        except CitationAuthorityUnavailable:
            raise
        except Exception as error:
            raise CitationAuthorityUnavailable from error
        if type(deleted) is not int or deleted < 0:
            raise CitationAuthorityUnavailable
        return deleted


class _CitationAuthorityScope:
    __slots__ = ("_active", "_seal")
    _active: bool
    _seal: object

    def __init__(self) -> None:
        raise TypeError("citation authority scopes are not constructible")

    def __reduce__(self) -> NoReturn:
        raise TypeError("citation authority scopes are not serializable")


_CITATION_AUTHORITY_SCOPE_SEAL = object()


def _open_citation_authority_scope() -> _CitationAuthorityScope:
    scope = object.__new__(_CitationAuthorityScope)
    scope._active = True
    scope._seal = _CITATION_AUTHORITY_SCOPE_SEAL
    return scope


def _close_citation_authority_scope(scope: _CitationAuthorityScope) -> None:
    if (
        type(scope) is not _CitationAuthorityScope
        or getattr(scope, "_seal", None) is not _CITATION_AUTHORITY_SCOPE_SEAL
    ):
        raise TypeError("citation authority scope has the wrong nominal type")
    scope._active = False


@dataclass(frozen=True, slots=True, init=False)
class CitationOpenSession:
    """Current-UserActor citation authority valid only in its transaction."""

    _authority_scope: _CitationAuthorityScope = field(repr=False)
    _port: CitationOpenPort = field(repr=False)

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("CitationOpenSession is authority-constructed")

    def __reduce__(self) -> NoReturn:
        raise TypeError("CitationOpenSession is not serializable")


def _require_active_citation_open_session(session: CitationOpenSession) -> None:
    if type(session) is not CitationOpenSession:
        raise TypeError("citation session has the wrong nominal type")
    scope = session._authority_scope
    if (
        type(scope) is not _CitationAuthorityScope
        or getattr(scope, "_seal", None) is not _CITATION_AUTHORITY_SCOPE_SEAL
        or not getattr(scope, "_active", False)
    ):
        raise ValueError("citation session requires an active authority scope")


def _construct_citation_open_session(
    *,
    authority_scope: _CitationAuthorityScope,
    port: CitationOpenPort,
) -> CitationOpenSession:
    session = object.__new__(CitationOpenSession)
    object.__setattr__(session, "_authority_scope", authority_scope)
    object.__setattr__(session, "_port", port)
    _require_active_citation_open_session(session)
    if not callable(getattr(port, "issue", None)) or not callable(
        getattr(port, "redeem", None)
    ):
        raise TypeError("citation port is incomplete")
    return session


def issue_citation_open_ref(
    session: CitationOpenSession,
    request: CitationOpenIssue,
    *,
    profile: CitationOpenProfile,
    reference_factory: Callable[[], str] = lambda: f"cor_{token_hex(32)}",
) -> CitationOpenRef:
    """Issue one opaque locator after authorized Package assembly."""

    _require_active_citation_open_session(session)
    if (
        type(request) is not CitationOpenIssue
        or type(profile) is not CitationOpenProfile
    ):
        raise TypeError("citation issuance requires exact request and profile types")
    if request.expires_at - request.issued_at > profile.maximum_ttl:
        raise CitationLocatorNotAvailable
    try:
        reference = reference_factory()
    except Exception:
        raise CitationAuthorityUnavailable("citation authority unavailable") from None
    if not _is_citation_open_ref(reference):
        raise CitationAuthorityUnavailable("citation authority unavailable")
    locator = CitationOpenRef(reference)
    try:
        persisted = session._port.issue(
            request=request,
            locator_digest=sha256(reference.encode("utf-8")).digest(),
            digest_profile=CITATION_OPEN_DIGEST_PROFILE,
            profile=profile,
            retain_until=request.issued_at + profile.retention_period,
        )
    except CitationAuthorityUnavailable:
        raise
    except Exception:
        raise CitationAuthorityUnavailable("citation authority unavailable") from None
    if persisted is not True:
        raise CitationAuthorityUnavailable("citation authority unavailable")
    return locator


def redeem_citation_open_ref(
    session: CitationOpenSession,
    request: CitationOpenRedemption,
) -> CitationOpenTarget:
    """Resolve content-free lineage without consuming or refreshing the locator."""

    _require_active_citation_open_session(session)
    if type(request) is not CitationOpenRedemption:
        raise TypeError("citation redemption requires CitationOpenRedemption")
    if not _is_citation_open_ref(request.citation_open_ref.value):
        raise CitationLocatorNotAvailable
    try:
        target = session._port.redeem(request)
    except CitationLocatorNotAvailable:
        raise
    except CitationAuthorityUnavailable:
        raise
    except Exception:
        raise CitationAuthorityUnavailable("citation authority unavailable") from None
    if type(target) is not CitationOpenTarget:
        raise CitationLocatorNotAvailable
    if target.candidate_ref.organization_id != request.organization_id:
        raise CitationLocatorNotAvailable
    return target
