"""Current UserActor transaction boundary backed by PostgreSQL authority."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy import Connection, Engine, text
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
from engine.runtime.evidence import CandidateRef
from engine.runtime.materialized import (
    MaterializedFragmentLocator,
    _close_materialized_projection_scope,
    _construct_materialized_projection_session,
    _open_materialized_projection_scope,
)
from engine.runtime.policy_epoch import (
    PolicyEpochAuthorityUnavailable,
    PolicyEpochPortFailure,
    _close_policy_epoch_authority_scope,
    _construct_policy_epoch_session,
    _observe_current_policy_epoch,
    _open_policy_epoch_authority_scope,
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


def _canonical_candidate_revision(value: str) -> UUID | None:
    try:
        parsed = UUID(value)
    except ValueError:
        return None
    if str(parsed) != value:
        return None
    return parsed


class _PostgreSQLMaterializedProjectionPort:
    """Two-stage Fragment reads on the owning current-UserActor transaction."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def locate(
        self,
        candidate_ref: CandidateRef,
    ) -> MaterializedFragmentLocator | None:
        revision_id = _canonical_candidate_revision(candidate_ref.revision_ref)
        if revision_id is None:
            return None
        row = self._connection.execute(
            text(
                """
                SELECT
                    resource.organization_id,
                    resource.source_ref,
                    resource.resource_ref,
                    revision.revision_id,
                    fragment.fragment_ref
                FROM context_resource AS resource
                JOIN context_revision AS revision
                  ON revision.organization_id = resource.organization_id
                 AND revision.resource_ref = resource.resource_ref
                 AND revision.revision_id = resource.active_revision_id
                JOIN context_fragment AS fragment
                  ON fragment.organization_id = revision.organization_id
                 AND fragment.resource_ref = revision.resource_ref
                 AND fragment.revision_id = revision.revision_id
                JOIN resource_access_policy AS access_policy
                  ON access_policy.organization_id = resource.organization_id
                 AND access_policy.resource_ref = resource.resource_ref
                 AND access_policy.principal_ref = current_setting(
                     'app.principal_ref'
                 )
                 AND access_policy.access_state = 'allowed'
                WHERE resource.organization_id = :organization_id
                  AND resource.source_ref = :source_ref
                  AND resource.resource_ref = :resource_ref
                  AND resource.active_revision_id = :revision_id
                  AND resource.tombstoned IS FALSE
                  AND fragment.fragment_ref = :fragment_ref
                """
            ),
            {
                "organization_id": candidate_ref.organization_id,
                "source_ref": candidate_ref.source_ref,
                "resource_ref": candidate_ref.resource_ref,
                "revision_id": revision_id,
                "fragment_ref": candidate_ref.fragment_ref,
            },
        ).one_or_none()
        if row is None:
            return None
        return MaterializedFragmentLocator(
            organization_id=row.organization_id,
            source_ref=row.source_ref,
            resource_ref=row.resource_ref,
            revision_ref=str(row.revision_id),
            fragment_ref=row.fragment_ref,
        )

    def project_body(
        self,
        locator: MaterializedFragmentLocator,
    ) -> str | None:
        revision_id = _canonical_candidate_revision(locator.revision_ref)
        if revision_id is None:
            return None
        return self._connection.execute(
            text(
                """
                SELECT fragment.content
                FROM context_resource AS resource
                JOIN context_revision AS revision
                  ON revision.organization_id = resource.organization_id
                 AND revision.resource_ref = resource.resource_ref
                 AND revision.revision_id = resource.active_revision_id
                JOIN context_fragment AS fragment
                  ON fragment.organization_id = revision.organization_id
                 AND fragment.resource_ref = revision.resource_ref
                 AND fragment.revision_id = revision.revision_id
                JOIN resource_access_policy AS access_policy
                  ON access_policy.organization_id = resource.organization_id
                 AND access_policy.resource_ref = resource.resource_ref
                 AND access_policy.principal_ref = current_setting(
                     'app.principal_ref'
                 )
                 AND access_policy.access_state = 'allowed'
                WHERE resource.organization_id = :organization_id
                  AND resource.source_ref = :source_ref
                  AND resource.resource_ref = :resource_ref
                  AND resource.active_revision_id = :revision_id
                  AND resource.tombstoned IS FALSE
                  AND fragment.fragment_ref = :fragment_ref
                """
            ),
            {
                "organization_id": locator.organization_id,
                "source_ref": locator.source_ref,
                "resource_ref": locator.resource_ref,
                "revision_id": revision_id,
                "fragment_ref": locator.fragment_ref,
            },
        ).scalar_one_or_none()


class _PostgreSQLPolicyEpochPort:
    """Current Organization epoch reads on the retained Membership transaction."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def read_current_epoch(self, organization_id: UUID) -> object:
        try:
            return self._connection.execute(
                text(
                    """
                    SELECT policy_epoch
                    FROM organization_policy_epoch
                    WHERE organization_id = :organization_id
                    """
                ),
                {"organization_id": organization_id},
            ).scalar_one_or_none()
        except SQLAlchemyError:
            raise PolicyEpochPortFailure(
                "Policy Epoch backend is unavailable"
            ) from None


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
            with self._engine.connect() as raw_connection:
                connection = raw_connection.execution_options(
                    isolation_level="READ COMMITTED"
                )
                observed_isolation = connection.get_isolation_level()
                if observed_isolation != "READ COMMITTED":
                    raise MembershipAuthorityUnavailable(
                        "current Membership authority requires READ COMMITTED"
                    )
                with connection.begin():
                    yield from self._current_user_actor_transaction(
                        connection,
                        identity,
                    )
        except (MembershipNotCurrent, MembershipAuthorityUnavailable):
            raise
        except PolicyEpochAuthorityUnavailable as error:
            raise MembershipAuthorityUnavailable(
                "current Membership Policy Epoch unavailable"
            ) from error
        except SQLAlchemyError as error:
            raise MembershipAuthorityUnavailable(
                "current Membership authority unavailable"
            ) from error

    def _current_user_actor_transaction(
        self,
        connection: Connection,
        identity: MembershipIdentity,
    ) -> Iterator[CurrentMembershipVerification]:
        """Verify one UserActor inside an already-pinned current-read transaction."""

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
        projection_scope = _open_materialized_projection_scope()
        policy_epoch_scope = _open_policy_epoch_authority_scope()
        try:
            projection_session = _construct_materialized_projection_session(
                authority_scope=projection_scope,
                port=_PostgreSQLMaterializedProjectionPort(connection),
            )
            policy_epoch_session = _construct_policy_epoch_session(
                authority_scope=policy_epoch_scope,
                organization_id=identity.organization_id,
                port=_PostgreSQLPolicyEpochPort(connection),
            )
            policy_epoch_verification = _observe_current_policy_epoch(
                policy_epoch_session
            )
            yield _construct_current_membership_verification(
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
                policy_epoch_verification=policy_epoch_verification,
                authority_scope=scope,
                materialized_projection_session=projection_session,
            )
        finally:
            _close_policy_epoch_authority_scope(policy_epoch_scope)
            _close_materialized_projection_scope(projection_scope)
            _close_membership_authority_scope(scope)
