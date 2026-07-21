"""Current UserActor transaction boundary backed by PostgreSQL authority."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from engine.persistence.role_guard import assert_runtime_role
from engine.runtime.actor import (
    MAX_MEMBERSHIP_VERSION,
    CurrentMembershipVerification,
    MembershipRejectionAuditReceipt,
    _close_membership_authority_scope,
    _construct_current_membership_verification,
    _open_membership_authority_scope,
)


class MembershipNotCurrent(Exception):
    """Trusted identity did not map to one current Membership."""

    def __init__(self) -> None:
        super().__init__("current Membership is not available")
        self.audit_receipt = MembershipRejectionAuditReceipt()


class MembershipAuthorityUnavailable(RuntimeError):
    """The current-Membership authority could not complete its database work."""


@dataclass(frozen=True, slots=True)
class MembershipIdentity:
    """Trusted identity locators used for one exact current-Membership check."""

    organization_id: UUID
    user_id: UUID
    membership_id: UUID
    membership_version: int
    principal_ref: str
    request_id: str
    authentication_binding_ref: str
    checked_at: datetime

    def __post_init__(self) -> None:
        for field_name in ("organization_id", "user_id", "membership_id"):
            if type(getattr(self, field_name)) is not UUID:
                raise TypeError(f"Membership {field_name} must be UUID")
        if (
            type(self.membership_version) is not int
            or not 1 <= self.membership_version <= MAX_MEMBERSHIP_VERSION
        ):
            raise ValueError(
                "Membership version must fit a positive signed 64-bit integer"
            )
        for field_name in (
            "principal_ref",
            "request_id",
            "authentication_binding_ref",
        ):
            value = getattr(self, field_name)
            if type(value) is not str or not value or value.isspace():
                raise ValueError(f"Membership {field_name} must be non-empty")
        if (
            type(self.checked_at) is not datetime
            or self.checked_at.tzinfo is None
            or self.checked_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("Membership checked_at must be an aware UTC datetime")


class _MembershipIdentityValue(Protocol):
    def __call__(self, identity: MembershipIdentity) -> str: ...


_ACTOR_SETTINGS: dict[str, _MembershipIdentityValue] = {
    "app.actor_kind": lambda identity: "user",
    "app.authentication_binding_ref": lambda identity: (
        identity.authentication_binding_ref
    ),
    "app.checked_at": lambda identity: (
        identity.checked_at.isoformat().replace("+00:00", "Z")
    ),
    "app.membership_id": lambda identity: str(identity.membership_id),
    "app.membership_version": lambda identity: str(identity.membership_version),
    "app.organization_id": lambda identity: str(identity.organization_id),
    "app.principal_ref": lambda identity: identity.principal_ref,
    "app.request_id": lambda identity: identity.request_id,
    "app.user_id": lambda identity: str(identity.user_id),
}


class PostgreSQLMembershipAuthority:
    """Open and retain the exact UserActor transaction through Runtime work."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @contextmanager
    def current_user_actor(
        self,
        identity: MembershipIdentity,
    ) -> Iterator[CurrentMembershipVerification]:
        """Bind, verify, and hold one current Membership until caller exit."""

        if type(identity) is not MembershipIdentity:
            raise TypeError("Membership identity must be MembershipIdentity")
        try:
            with self._engine.begin() as connection:
                try:
                    assert_runtime_role(connection)
                except AssertionError as error:
                    raise MembershipAuthorityUnavailable(
                        "current Membership authority is not the Runtime role"
                    ) from error
                for setting_name, value_factory in _ACTOR_SETTINGS.items():
                    expected = value_factory(identity)
                    connection.execute(
                        text(
                            "SELECT set_config("
                            ":setting_name, :setting_value, true"
                            ")"
                        ),
                        {
                            "setting_name": setting_name,
                            "setting_value": expected,
                        },
                    )
                    observed = connection.execute(
                        text("SELECT current_setting(:setting_name, true)"),
                        {"setting_name": setting_name},
                    ).scalar_one()
                    if observed != expected:
                        raise MembershipAuthorityUnavailable(
                            "UserActor context binding failed"
                        )

                row = connection.execute(
                    text(
                        """
                        SELECT user_id
                        FROM membership
                        WHERE organization_id = :organization_id
                          AND membership_id = :membership_id
                          AND user_id = :user_id
                          AND membership_version = :membership_version
                          AND status = 'active'
                          AND valid_from <= :checked_at
                          AND (
                              valid_until IS NULL
                              OR :checked_at < valid_until
                          )
                        """
                    ),
                    {
                        "organization_id": identity.organization_id,
                        "membership_id": identity.membership_id,
                        "user_id": identity.user_id,
                        "membership_version": identity.membership_version,
                        "checked_at": identity.checked_at,
                    },
                ).one_or_none()
                if row is None or row.user_id != identity.user_id:
                    raise MembershipNotCurrent

                scope = _open_membership_authority_scope()
                try:
                    verification = _construct_current_membership_verification(
                        organization_id=identity.organization_id,
                        user_id=identity.user_id,
                        membership_id=identity.membership_id,
                        membership_version=identity.membership_version,
                        principal_ref=identity.principal_ref,
                        request_id=identity.request_id,
                        authentication_binding_ref=(
                            identity.authentication_binding_ref
                        ),
                        checked_at=identity.checked_at,
                        authority_scope=scope,
                    )
                    yield verification
                finally:
                    _close_membership_authority_scope(scope)
        except (MembershipNotCurrent, MembershipAuthorityUnavailable):
            raise
        except SQLAlchemyError as error:
            raise MembershipAuthorityUnavailable(
                "current Membership authority unavailable"
            ) from error
