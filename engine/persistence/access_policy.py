"""Trusted control-plane access revocation backed by one PostgreSQL transaction."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from engine.persistence.role_guard import assert_control_role

MAX_ACCESS_VERSION = 2**63 - 1
MAX_POLICY_EPOCH = 2**63 - 1


class AccessChangeRejected(Exception):
    """The exact currently allowed grant could not be safely revoked."""

    def __init__(self) -> None:
        super().__init__("access change was not accepted")


class AccessPolicyControlUnavailable(RuntimeError):
    """The trusted access-policy database authority could not complete."""


@dataclass(frozen=True, slots=True)
class ResourceAccessRevocation:
    """Exact optimistic-concurrency locator for one allowed Resource grant."""

    organization_id: UUID
    resource_ref: str
    principal_ref: str
    expected_access_version: int

    def __post_init__(self) -> None:
        if type(self.organization_id) is not UUID:
            raise TypeError("access revocation organization_id must be UUID")
        for field_name in ("resource_ref", "principal_ref"):
            value = getattr(self, field_name)
            if type(value) is not str or not value or value.isspace():
                raise ValueError(f"access revocation {field_name} must be nonblank")
        if (
            type(self.expected_access_version) is not int
            or not 1 <= self.expected_access_version <= MAX_ACCESS_VERSION
        ):
            raise ValueError(
                "expected access version must fit a positive signed 64-bit integer"
            )


@dataclass(frozen=True, slots=True)
class PolicyEpoch:
    """Current Organization-owned monotonic revocation value after a commit."""

    organization_id: UUID
    value: int

    def __post_init__(self) -> None:
        if type(self.organization_id) is not UUID:
            raise TypeError("Policy Epoch organization_id must be UUID")
        if type(self.value) is not int or not 1 <= self.value <= MAX_POLICY_EPOCH:
            raise ValueError("Policy Epoch must fit a positive signed 64-bit integer")


class PostgreSQLAccessPolicyControl:
    """Revoke one exact grant and advance its Organization epoch atomically."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def change_access(self, command: ResourceAccessRevocation) -> PolicyEpoch:
        """Commit one allowed-to-revoked transition or expose no state change."""

        if type(command) is not ResourceAccessRevocation:
            raise TypeError("access change requires ResourceAccessRevocation")
        try:
            with self._engine.begin() as connection:
                try:
                    assert_control_role(connection)
                except AssertionError as error:
                    raise AccessPolicyControlUnavailable(
                        "access-policy control is not the dedicated control role"
                    ) from error

                expected_organization = str(command.organization_id)
                connection.execute(
                    text(
                        "SELECT set_config("
                        "'app.organization_id', :organization_id, true"
                        ")"
                    ),
                    {"organization_id": expected_organization},
                )
                observed_organization = connection.execute(
                    text("SELECT current_setting('app.organization_id', true)")
                ).scalar_one()
                if observed_organization != expected_organization:
                    raise AccessPolicyControlUnavailable(
                        "control Organization context binding failed"
                    )

                next_epoch = connection.execute(
                    text(
                        """
                        SELECT public.context_control_revoke_resource_access(
                            :organization_id,
                            :resource_ref,
                            :principal_ref,
                            :expected_access_version
                        )
                        """
                    ),
                    {
                        "organization_id": command.organization_id,
                        "resource_ref": command.resource_ref,
                        "principal_ref": command.principal_ref,
                        "expected_access_version": command.expected_access_version,
                    },
                ).scalar_one()
                result = PolicyEpoch(
                    organization_id=command.organization_id,
                    value=next_epoch,
                )
            return result
        except (AccessChangeRejected, AccessPolicyControlUnavailable):
            raise
        except SQLAlchemyError as error:
            sqlstate = (
                getattr(error.orig, "sqlstate", None)
                if isinstance(error, DBAPIError)
                else None
            )
            if sqlstate == "P0001":
                raise AccessChangeRejected from None
            raise AccessPolicyControlUnavailable(
                "access-policy control database work failed"
            ) from error
