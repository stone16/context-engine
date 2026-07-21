"""Lifetime-bound Organization Policy Epoch verification and validation seam."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, NoReturn, Protocol
from uuid import UUID

MAX_POLICY_EPOCH: Final = (1 << 63) - 1


class PolicyEpochAuthorityUnavailable(RuntimeError):
    """The durable Organization Policy Epoch could not be established."""


class PolicyEpochVerificationProvenance(StrEnum):
    """Closed provenance for an epoch observed by a trusted transaction."""

    TRUSTED_POLICY_EPOCH_AUTHORITY = "trusted_policy_epoch_authority"


class PolicyEpochPort(Protocol):
    """Narrow current-epoch read owned by one retained trusted transaction."""

    def read_current_epoch(self, organization_id: UUID) -> object: ...


class _PolicyEpochAuthorityScope:
    """Private lifetime token owned by one retained Membership transaction."""

    __slots__ = ("_active", "_seal")
    _active: bool
    _seal: object

    def __init__(self) -> None:
        raise TypeError("Policy Epoch authority scopes are not constructible")

    def __reduce__(self) -> NoReturn:
        raise TypeError("Policy Epoch authority scopes are not serializable")


_POLICY_EPOCH_AUTHORITY_SCOPE_SEAL = object()


def _open_policy_epoch_authority_scope() -> _PolicyEpochAuthorityScope:
    scope = object.__new__(_PolicyEpochAuthorityScope)
    scope._active = True
    scope._seal = _POLICY_EPOCH_AUTHORITY_SCOPE_SEAL
    return scope


def _close_policy_epoch_authority_scope(
    scope: _PolicyEpochAuthorityScope,
) -> None:
    if (
        type(scope) is not _PolicyEpochAuthorityScope
        or getattr(scope, "_seal", None) is not _POLICY_EPOCH_AUTHORITY_SCOPE_SEAL
    ):
        raise TypeError("Policy Epoch authority scope has the wrong nominal type")
    scope._active = False


@dataclass(frozen=True, slots=True, init=False)
class PolicyEpochSession:
    """Exact current-epoch read capability for one Organization transaction."""

    organization_id: UUID = field(repr=False)
    _authority_scope: _PolicyEpochAuthorityScope = field(repr=False)
    _port: PolicyEpochPort = field(repr=False)

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError(
            "PolicyEpochSession can only be constructed by a trusted transaction"
        )

    def __reduce__(self) -> NoReturn:
        raise TypeError("PolicyEpochSession is not serializable")


@dataclass(frozen=True, slots=True, init=False)
class PolicyEpochVerification:
    """Observed Organization epoch bound to its exact validation session."""

    organization_id: UUID = field(repr=False)
    policy_epoch: int
    validation_session: PolicyEpochSession = field(repr=False)
    construction_provenance: PolicyEpochVerificationProvenance

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError(
            "PolicyEpochVerification can only be constructed by a trusted "
            "Policy Epoch authority"
        )

    def __reduce__(self) -> NoReturn:
        raise TypeError("PolicyEpochVerification is not serializable")


def _require_policy_epoch(value: object) -> int:
    if type(value) is not int or not 1 <= value <= MAX_POLICY_EPOCH:
        raise PolicyEpochAuthorityUnavailable(
            "current Organization Policy Epoch is unavailable"
        )
    return value


def _require_active_policy_epoch_session(session: PolicyEpochSession) -> None:
    if type(session) is not PolicyEpochSession:
        raise TypeError("Policy Epoch session has the wrong nominal type")
    if type(session.organization_id) is not UUID:
        raise ValueError("Policy Epoch session has an invalid Organization binding")
    scope = session._authority_scope
    if (
        type(scope) is not _PolicyEpochAuthorityScope
        or getattr(scope, "_seal", None) is not _POLICY_EPOCH_AUTHORITY_SCOPE_SEAL
        or not getattr(scope, "_active", False)
    ):
        raise ValueError("Policy Epoch session requires active Policy Epoch authority")
    if not callable(getattr(session._port, "read_current_epoch", None)):
        raise TypeError("Policy Epoch session port is incomplete")


def _construct_policy_epoch_session(
    *,
    authority_scope: _PolicyEpochAuthorityScope,
    organization_id: UUID,
    port: PolicyEpochPort,
) -> PolicyEpochSession:
    if type(organization_id) is not UUID:
        raise TypeError("Policy Epoch organization_id must be UUID")
    if not callable(getattr(port, "read_current_epoch", None)):
        raise TypeError("Policy Epoch port is incomplete")
    session = object.__new__(PolicyEpochSession)
    object.__setattr__(session, "organization_id", organization_id)
    object.__setattr__(session, "_authority_scope", authority_scope)
    object.__setattr__(session, "_port", port)
    _require_active_policy_epoch_session(session)
    return session


def _observe_current_policy_epoch(
    session: PolicyEpochSession,
) -> PolicyEpochVerification:
    _require_active_policy_epoch_session(session)
    epoch = _require_policy_epoch(
        session._port.read_current_epoch(session.organization_id)
    )
    verification = object.__new__(PolicyEpochVerification)
    object.__setattr__(verification, "organization_id", session.organization_id)
    object.__setattr__(verification, "policy_epoch", epoch)
    object.__setattr__(verification, "validation_session", session)
    object.__setattr__(
        verification,
        "construction_provenance",
        PolicyEpochVerificationProvenance.TRUSTED_POLICY_EPOCH_AUTHORITY,
    )
    return verification


def _require_active_policy_epoch_verification(
    verification: PolicyEpochVerification,
) -> None:
    if type(verification) is not PolicyEpochVerification:
        raise TypeError("Policy Epoch verification has the wrong nominal type")
    if (
        verification.construction_provenance
        is not PolicyEpochVerificationProvenance.TRUSTED_POLICY_EPOCH_AUTHORITY
    ):
        raise ValueError("Policy Epoch verification has invalid provenance")
    _require_policy_epoch(verification.policy_epoch)
    session = verification.validation_session
    _require_active_policy_epoch_session(session)
    if verification.organization_id != session.organization_id:
        raise ValueError(
            "Policy Epoch verification has an invalid Organization binding"
        )


def _policy_epoch_is_current(verification: PolicyEpochVerification) -> bool:
    """Re-read durable epoch; structural failures are authority unavailability."""

    _require_active_policy_epoch_verification(verification)
    observed = _require_policy_epoch(
        verification.validation_session._port.read_current_epoch(
            verification.organization_id
        )
    )
    return observed == verification.policy_epoch
