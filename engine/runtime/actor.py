"""Nominal current-Membership and online UserActor authority contracts."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final, Literal, NoReturn
from uuid import UUID

from engine.runtime.context_run import (
    ContextRunPersistenceSession,
    _require_active_context_run_persistence_session,
)
from engine.runtime.materialized import (
    MaterializedProjectionSession,
    _require_active_materialized_projection_session,
)
from engine.runtime.policy_epoch import (
    PolicyEpochVerification,
    _require_active_policy_epoch_verification,
)

MAX_MEMBERSHIP_VERSION: Final = (1 << 63) - 1


class MembershipVerificationProvenance(StrEnum):
    """Closed provenance for a current Membership authority decision."""

    TRUSTED_MEMBERSHIP_AUTHORITY = "trusted_membership_authority"


class UserActorConstructionProvenance(StrEnum):
    """Closed provenance for an online actor derived from current Membership."""

    TRUSTED_MEMBERSHIP_AUTHORITY = "trusted_membership_authority"


class MembershipRejectionCategory(StrEnum):
    """Sole restricted category for every non-current Membership decision."""

    NOT_CURRENT = "membership_not_current"


@dataclass(frozen=True, slots=True)
class MembershipRejectionAuditReceipt:
    """Safe in-memory audit carrier with no denied identity detail."""

    category: MembershipRejectionCategory = MembershipRejectionCategory.NOT_CURRENT
    denied_detail_count: Literal[0] = 0


class _MembershipAuthorityScope:
    """Private lifetime token owned by one trusted authority operation."""

    __slots__ = ("_active", "_seal")
    _active: bool
    _seal: object

    def __init__(self) -> None:
        raise TypeError("Membership authority scopes are not publicly constructible")

    def __reduce__(self) -> NoReturn:
        raise TypeError("Membership authority scopes are not serializable")


_MEMBERSHIP_AUTHORITY_SCOPE_SEAL = object()


def _open_membership_authority_scope() -> _MembershipAuthorityScope:
    scope = object.__new__(_MembershipAuthorityScope)
    scope._active = True
    scope._seal = _MEMBERSHIP_AUTHORITY_SCOPE_SEAL
    return scope


def _close_membership_authority_scope(scope: _MembershipAuthorityScope) -> None:
    if (
        type(scope) is not _MembershipAuthorityScope
        or getattr(scope, "_seal", None) is not _MEMBERSHIP_AUTHORITY_SCOPE_SEAL
    ):
        raise TypeError("Membership authority scope has the wrong nominal type")
    scope._active = False


@dataclass(frozen=True, slots=True, init=False)
class CurrentMembershipVerification:
    """Request-bound proof that one Membership was current when checked."""

    organization_id: UUID
    user_id: UUID
    membership_id: UUID
    membership_version: int
    principal_ref: str
    request_id: str
    authentication_binding_ref: str
    checked_at: datetime
    policy_epoch: int
    policy_epoch_verification: PolicyEpochVerification = field(repr=False)
    materialized_projection_session: MaterializedProjectionSession | None = field(
        repr=False
    )
    context_run_persistence_session: ContextRunPersistenceSession | None = field(
        repr=False
    )
    construction_provenance: MembershipVerificationProvenance
    _authority_scope: _MembershipAuthorityScope = field(repr=False)

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError(
            "CurrentMembershipVerification can only be constructed by a trusted "
            "Membership authority"
        )

    def __reduce__(self) -> NoReturn:
        raise TypeError("CurrentMembershipVerification is not serializable")


def _construct_current_membership_verification(
    *,
    authority_scope: _MembershipAuthorityScope,
    organization_id: UUID,
    user_id: UUID,
    membership_id: UUID,
    membership_version: int,
    principal_ref: str,
    request_id: str,
    authentication_binding_ref: str,
    checked_at: datetime,
    policy_epoch_verification: PolicyEpochVerification,
    materialized_projection_session: MaterializedProjectionSession | None = None,
    context_run_persistence_session: ContextRunPersistenceSession | None = None,
) -> CurrentMembershipVerification:
    """Construct proof after the trusted authority verifies the durable row."""

    if (
        type(authority_scope) is not _MembershipAuthorityScope
        or getattr(authority_scope, "_seal", None)
        is not _MEMBERSHIP_AUTHORITY_SCOPE_SEAL
        or not getattr(authority_scope, "_active", False)
    ):
        raise ValueError(
            "current Membership requires an active Membership authority scope"
        )
    uuid_facts: tuple[tuple[str, object], ...] = (
        ("organization_id", organization_id),
        ("user_id", user_id),
        ("membership_id", membership_id),
    )
    for field_name, value in uuid_facts:
        if type(value) is not UUID:
            raise TypeError(f"current Membership {field_name} must be UUID")
    if (
        type(membership_version) is not int
        or not 1 <= membership_version <= MAX_MEMBERSHIP_VERSION
    ):
        raise ValueError(
            "current Membership version must fit a positive signed 64-bit integer"
        )
    for field_name, value in (
        ("principal_ref", principal_ref),
        ("request_id", request_id),
        ("authentication_binding_ref", authentication_binding_ref),
    ):
        if type(value) is not str or not value or value.isspace():
            raise ValueError(f"current Membership {field_name} must be non-empty")
    if (
        type(checked_at) is not datetime
        or checked_at.tzinfo is None
        or checked_at.utcoffset() != timedelta(0)
    ):
        raise ValueError("current Membership checked_at must be timezone-aware UTC")
    if materialized_projection_session is not None:
        _require_active_materialized_projection_session(materialized_projection_session)
    if context_run_persistence_session is not None:
        _require_active_context_run_persistence_session(context_run_persistence_session)
    _require_active_policy_epoch_verification(policy_epoch_verification)
    if policy_epoch_verification.organization_id != organization_id:
        raise ValueError("current Membership Policy Epoch must stay in Organization")

    verification = object.__new__(CurrentMembershipVerification)
    object.__setattr__(verification, "organization_id", organization_id)
    object.__setattr__(verification, "user_id", user_id)
    object.__setattr__(verification, "membership_id", membership_id)
    object.__setattr__(verification, "membership_version", membership_version)
    object.__setattr__(verification, "principal_ref", principal_ref)
    object.__setattr__(verification, "request_id", request_id)
    object.__setattr__(
        verification,
        "authentication_binding_ref",
        authentication_binding_ref,
    )
    object.__setattr__(verification, "checked_at", checked_at)
    object.__setattr__(
        verification,
        "policy_epoch",
        policy_epoch_verification.policy_epoch,
    )
    object.__setattr__(
        verification,
        "policy_epoch_verification",
        policy_epoch_verification,
    )
    object.__setattr__(
        verification,
        "materialized_projection_session",
        materialized_projection_session,
    )
    object.__setattr__(
        verification,
        "context_run_persistence_session",
        context_run_persistence_session,
    )
    object.__setattr__(
        verification,
        "construction_provenance",
        MembershipVerificationProvenance.TRUSTED_MEMBERSHIP_AUTHORITY,
    )
    object.__setattr__(verification, "_authority_scope", authority_scope)
    return verification


def _require_active_current_membership_verification(
    verification: CurrentMembershipVerification,
) -> None:
    """Reject proofs used outside their exact trusted authority operation."""

    if type(verification) is not CurrentMembershipVerification:
        raise TypeError("current Membership proof has the wrong nominal type")
    if (
        verification.construction_provenance
        is not MembershipVerificationProvenance.TRUSTED_MEMBERSHIP_AUTHORITY
        or type(verification._authority_scope) is not _MembershipAuthorityScope
        or getattr(verification._authority_scope, "_seal", None)
        is not _MEMBERSHIP_AUTHORITY_SCOPE_SEAL
        or not getattr(verification._authority_scope, "_active", False)
    ):
        raise ValueError(
            "current Membership proof requires active Membership authority scope"
        )
    if verification.materialized_projection_session is not None:
        _require_active_materialized_projection_session(
            verification.materialized_projection_session
        )
    if verification.context_run_persistence_session is not None:
        _require_active_context_run_persistence_session(
            verification.context_run_persistence_session
        )
    _require_active_policy_epoch_verification(verification.policy_epoch_verification)
    if (
        verification.policy_epoch != verification.policy_epoch_verification.policy_epoch
        or verification.organization_id
        != verification.policy_epoch_verification.organization_id
    ):
        raise ValueError(
            "current Membership does not match its Policy Epoch verification"
        )


@dataclass(frozen=True, slots=True, init=False)
class UserActor:
    """Online actor bound to one current User/Membership authority decision."""

    organization_id: UUID
    user_id: UUID
    membership_id: UUID
    membership_version: int
    principal_ref: str
    request_id: str
    authentication_binding_ref: str
    checked_at: datetime
    policy_epoch: int
    policy_epoch_verification: PolicyEpochVerification = field(repr=False)
    materialized_projection_session: MaterializedProjectionSession | None = field(
        repr=False
    )
    context_run_persistence_session: ContextRunPersistenceSession | None = field(
        repr=False
    )
    current_membership_verification: CurrentMembershipVerification = field(repr=False)
    construction_provenance: UserActorConstructionProvenance

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError(
            "UserActor can only be constructed by a trusted Membership authority"
        )

    def __reduce__(self) -> NoReturn:
        raise TypeError("UserActor is not serializable")


def _construct_user_actor(
    verification: CurrentMembershipVerification,
) -> UserActor:
    """Construct the online actor from one active nominal authority proof."""

    _require_active_current_membership_verification(verification)
    actor = object.__new__(UserActor)
    for field_name, value in (
        ("organization_id", verification.organization_id),
        ("user_id", verification.user_id),
        ("membership_id", verification.membership_id),
        ("membership_version", verification.membership_version),
        ("principal_ref", verification.principal_ref),
        ("request_id", verification.request_id),
        (
            "authentication_binding_ref",
            verification.authentication_binding_ref,
        ),
        ("checked_at", verification.checked_at),
        ("policy_epoch", verification.policy_epoch),
        (
            "policy_epoch_verification",
            verification.policy_epoch_verification,
        ),
        (
            "materialized_projection_session",
            verification.materialized_projection_session,
        ),
        (
            "context_run_persistence_session",
            verification.context_run_persistence_session,
        ),
        ("current_membership_verification", verification),
        (
            "construction_provenance",
            UserActorConstructionProvenance.TRUSTED_MEMBERSHIP_AUTHORITY,
        ),
    ):
        object.__setattr__(actor, field_name, value)
    return actor


def _require_active_user_actor(actor: UserActor) -> None:
    """Validate actor nominality, binding integrity, and authority lifetime."""

    if type(actor) is not UserActor:
        raise TypeError("UserActor has the wrong nominal type")
    if (
        actor.construction_provenance
        is not UserActorConstructionProvenance.TRUSTED_MEMBERSHIP_AUTHORITY
    ):
        raise ValueError("UserActor has invalid construction provenance")
    verification = actor.current_membership_verification
    _require_active_current_membership_verification(verification)
    if (
        actor.organization_id != verification.organization_id
        or actor.user_id != verification.user_id
        or actor.membership_id != verification.membership_id
        or actor.membership_version != verification.membership_version
        or actor.principal_ref != verification.principal_ref
        or actor.request_id != verification.request_id
        or actor.authentication_binding_ref != verification.authentication_binding_ref
        or actor.checked_at != verification.checked_at
        or actor.policy_epoch != verification.policy_epoch
        or actor.policy_epoch_verification is not verification.policy_epoch_verification
        or actor.materialized_projection_session
        is not verification.materialized_projection_session
        or actor.context_run_persistence_session
        is not verification.context_run_persistence_session
    ):
        raise ValueError("UserActor does not match its current Membership proof")
