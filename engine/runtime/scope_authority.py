"""Nominal trusted-state snapshot for one EffectiveScope decision."""

from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta
from enum import StrEnum
from typing import NoReturn
from uuid import UUID

from engine.runtime.actor import MAX_MEMBERSHIP_VERSION
from engine.runtime.policy_epoch import MAX_POLICY_EPOCH
from engine.runtime.scope import (
    MISSING_TRUSTED_SCOPE,
    MissingTrustedScope,
    ScopeSet,
    TrustedScopeOperands,
)


class ScopeSnapshotProvenance(StrEnum):
    """Closed provenance for a trusted scope-state snapshot."""

    TRUSTED_SCOPE_AUTHORITY = "trusted_scope_authority"


class InvalidTrustedScopeSnapshot(ValueError):
    """Trusted scope proof is invalid, expired, or internally inconsistent."""


class _ScopeAuthorityScope:
    """Private lifetime token owned by one trusted scope authority operation."""

    __slots__ = ("_active", "_seal")
    _active: bool
    _seal: object

    def __init__(self) -> None:
        raise TypeError("trusted scope authority scopes are not constructible")

    def __reduce__(self) -> NoReturn:
        raise TypeError("trusted scope authority scopes are not serializable")


_SCOPE_AUTHORITY_SCOPE_SEAL = object()


def _open_scope_authority_scope() -> _ScopeAuthorityScope:
    scope = object.__new__(_ScopeAuthorityScope)
    scope._active = True
    scope._seal = _SCOPE_AUTHORITY_SCOPE_SEAL
    return scope


def _close_scope_authority_scope(scope: _ScopeAuthorityScope) -> None:
    if (
        type(scope) is not _ScopeAuthorityScope
        or getattr(scope, "_seal", None) is not _SCOPE_AUTHORITY_SCOPE_SEAL
    ):
        raise TypeError("trusted scope authority scope has the wrong nominal type")
    scope._active = False


@dataclass(frozen=True, slots=True, init=False)
class TrustedScopeSnapshot:
    """Request-bound proof carrying seven trusted EffectiveScope operands."""

    organization_id: UUID = field(repr=False)
    user_id: UUID = field(repr=False)
    membership_id: UUID = field(repr=False)
    membership_version: int = field(repr=False)
    policy_epoch: int = field(repr=False)
    principal_ref: str = field(repr=False)
    agent_version_ref: str = field(repr=False)
    purpose: str = field(repr=False)
    request_id: str = field(repr=False)
    authentication_binding_ref: str = field(repr=False)
    checked_at: datetime = field(repr=False)
    organization_boundary: ScopeSet | MissingTrustedScope = field(repr=False)
    membership_rights: ScopeSet | MissingTrustedScope = field(repr=False)
    principal_grants: ScopeSet | MissingTrustedScope = field(repr=False)
    agent_ceiling: ScopeSet | MissingTrustedScope = field(repr=False)
    source_native_acl: ScopeSet | MissingTrustedScope = field(repr=False)
    resource_acl: ScopeSet | MissingTrustedScope = field(repr=False)
    purpose_policy: ScopeSet | MissingTrustedScope = field(repr=False)
    construction_provenance: ScopeSnapshotProvenance
    _authority_scope: _ScopeAuthorityScope = field(repr=False)

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError(
            "TrustedScopeSnapshot can only be constructed by a trusted scope "
            "authority"
        )

    def __reduce__(self) -> NoReturn:
        raise TypeError("TrustedScopeSnapshot is not serializable")


def _construct_trusted_scope_snapshot(
    *,
    authority_scope: _ScopeAuthorityScope,
    organization_id: UUID,
    user_id: UUID,
    membership_id: UUID,
    membership_version: int,
    policy_epoch: int,
    principal_ref: str,
    agent_version_ref: str,
    purpose: str,
    request_id: str,
    authentication_binding_ref: str,
    checked_at: datetime,
    organization_boundary: ScopeSet | MissingTrustedScope,
    membership_rights: ScopeSet | MissingTrustedScope,
    principal_grants: ScopeSet | MissingTrustedScope,
    agent_ceiling: ScopeSet | MissingTrustedScope,
    source_native_acl: ScopeSet | MissingTrustedScope,
    resource_acl: ScopeSet | MissingTrustedScope,
    purpose_policy: ScopeSet | MissingTrustedScope,
) -> TrustedScopeSnapshot:
    """Construct a snapshot after one trusted authority resolves every operand."""

    if (
        type(authority_scope) is not _ScopeAuthorityScope
        or getattr(authority_scope, "_seal", None) is not _SCOPE_AUTHORITY_SCOPE_SEAL
        or not getattr(authority_scope, "_active", False)
    ):
        raise ValueError("trusted scope requires an active trusted scope authority")
    uuid_facts: tuple[tuple[str, object], ...] = (
        ("organization_id", organization_id),
        ("user_id", user_id),
        ("membership_id", membership_id),
    )
    for field_name, value in uuid_facts:
        if type(value) is not UUID:
            raise TypeError(f"trusted scope {field_name} must be UUID")
    if (
        type(membership_version) is not int
        or not 1 <= membership_version <= MAX_MEMBERSHIP_VERSION
    ):
        raise ValueError(
            "trusted scope Membership version must fit a positive signed 64-bit "
            "integer"
        )
    if type(policy_epoch) is not int or not 1 <= policy_epoch <= MAX_POLICY_EPOCH:
        raise ValueError(
            "trusted scope Policy Epoch must fit a positive signed 64-bit integer"
        )
    reference_facts: tuple[tuple[str, object], ...] = (
        ("principal_ref", principal_ref),
        ("agent_version_ref", agent_version_ref),
        ("purpose", purpose),
        ("request_id", request_id),
        ("authentication_binding_ref", authentication_binding_ref),
    )
    for field_name, value in reference_facts:
        if type(value) is not str or not value or value.isspace():
            raise ValueError(f"trusted scope {field_name} must be non-empty")
    if (
        type(checked_at) is not datetime
        or checked_at.tzinfo is None
        or checked_at.utcoffset() != timedelta(0)
    ):
        raise ValueError("trusted scope checked_at must be timezone-aware UTC")

    operands = TrustedScopeOperands(
        organization_boundary=organization_boundary,
        membership_rights=membership_rights,
        principal_grants=principal_grants,
        agent_ceiling=agent_ceiling,
        source_native_acl=source_native_acl,
        resource_acl=resource_acl,
        purpose_policy=purpose_policy,
    )
    for operand_field in fields(operands):
        operand = getattr(operands, operand_field.name)
        if operand is MISSING_TRUSTED_SCOPE:
            continue
        if any(
            target.organization_id != organization_id for target in operand.targets
        ):
            raise ValueError(
                f"trusted scope {operand_field.name} must stay in Organization"
            )

    snapshot = object.__new__(TrustedScopeSnapshot)
    values: dict[str, object] = {
        "organization_id": organization_id,
        "user_id": user_id,
        "membership_id": membership_id,
        "membership_version": membership_version,
        "policy_epoch": policy_epoch,
        "principal_ref": principal_ref,
        "agent_version_ref": agent_version_ref,
        "purpose": purpose,
        "request_id": request_id,
        "authentication_binding_ref": authentication_binding_ref,
        "checked_at": checked_at,
        "organization_boundary": organization_boundary,
        "membership_rights": membership_rights,
        "principal_grants": principal_grants,
        "agent_ceiling": agent_ceiling,
        "source_native_acl": source_native_acl,
        "resource_acl": resource_acl,
        "purpose_policy": purpose_policy,
        "construction_provenance": ScopeSnapshotProvenance.TRUSTED_SCOPE_AUTHORITY,
        "_authority_scope": authority_scope,
    }
    for field_name, value in values.items():
        object.__setattr__(snapshot, field_name, value)
    return snapshot


def _require_active_trusted_scope_snapshot(snapshot: TrustedScopeSnapshot) -> None:
    """Reject forged, mutated, or expired trusted scope snapshots."""

    if type(snapshot) is not TrustedScopeSnapshot:
        raise TypeError("trusted scope snapshot has the wrong nominal type")
    if (
        snapshot.construction_provenance
        is not ScopeSnapshotProvenance.TRUSTED_SCOPE_AUTHORITY
        or type(snapshot._authority_scope) is not _ScopeAuthorityScope
        or getattr(snapshot._authority_scope, "_seal", None)
        is not _SCOPE_AUTHORITY_SCOPE_SEAL
        or not getattr(snapshot._authority_scope, "_active", False)
    ):
        raise InvalidTrustedScopeSnapshot(
            "trusted scope snapshot requires active trusted scope authority"
        )
    if (
        type(snapshot.policy_epoch) is not int
        or not 1 <= snapshot.policy_epoch <= MAX_POLICY_EPOCH
    ):
        raise InvalidTrustedScopeSnapshot(
            "trusted scope snapshot requires a valid Policy Epoch"
        )


def _trusted_operands_from_snapshot(
    snapshot: TrustedScopeSnapshot,
) -> TrustedScopeOperands:
    """Return exact operands only while their nominal authority scope is active."""

    _require_active_trusted_scope_snapshot(snapshot)
    operands = TrustedScopeOperands(
        organization_boundary=snapshot.organization_boundary,
        membership_rights=snapshot.membership_rights,
        principal_grants=snapshot.principal_grants,
        agent_ceiling=snapshot.agent_ceiling,
        source_native_acl=snapshot.source_native_acl,
        resource_acl=snapshot.resource_acl,
        purpose_policy=snapshot.purpose_policy,
    )
    for operand_field in fields(operands):
        operand = getattr(operands, operand_field.name)
        if operand is MISSING_TRUSTED_SCOPE:
            continue
        if any(
            target.organization_id != snapshot.organization_id
            for target in operand.targets
        ):
            raise InvalidTrustedScopeSnapshot(
                f"trusted scope {operand_field.name} must stay in Organization"
            )
    return operands
