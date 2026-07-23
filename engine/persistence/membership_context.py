"""Current UserActor transaction boundary backed by PostgreSQL authority."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy import Connection, Engine, bindparam, text
from sqlalchemy.dialects import postgresql
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
from engine.runtime.context_run import (
    ContextRunRecord,
    DecisionAuditRecord,
    _close_context_run_persistence_scope,
    _construct_context_run_persistence_session,
    _open_context_run_persistence_scope,
)
from engine.runtime.evidence import CandidateRef
from engine.runtime.materialized import (
    MaterializedFieldValue,
    MaterializedFragmentLocator,
    MaterializedFragmentProjection,
    MaterializedProjectionKind,
    MaterializedProjectionSession,
    MaterializedPublicationTrace,
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

    def discover_exact_phrase(
        self,
        phrase_digest: str,
    ) -> tuple[CandidateRef, ...]:
        rows = self._connection.execute(
            text(
                """
                SELECT
                    organization_id,
                    source_ref,
                    resource_ref,
                    revision_id,
                    fragment_ref
                FROM exact_phrase_candidate
                WHERE phrase_digest = :phrase_digest
                ORDER BY resource_ref, revision_id, fragment_ref
                """
            ),
            {"phrase_digest": phrase_digest},
        )
        return tuple(
            CandidateRef(
                organization_id=row.organization_id,
                source_ref=row.source_ref,
                resource_ref=row.resource_ref,
                revision_ref=str(row.revision_id),
                fragment_ref=row.fragment_ref,
            )
            for row in rows
        )

    def observe_publication(
        self,
        candidate_ref: CandidateRef,
    ) -> MaterializedPublicationTrace | None:
        revision_id = _canonical_candidate_revision(candidate_ref.revision_ref)
        if revision_id is None:
            return None
        row = self._connection.execute(
            text(
                """
                SELECT
                    resource.active_revision_id,
                    array_agg(event.state ORDER BY event.ordinal) AS states
                FROM context_resource AS resource
                JOIN revision_publication_event AS event
                  ON event.organization_id = resource.organization_id
                 AND event.resource_ref = resource.resource_ref
                 AND event.revision_id = resource.active_revision_id
                WHERE resource.organization_id = :organization_id
                  AND resource.source_ref = :source_ref
                  AND resource.resource_ref = :resource_ref
                  AND resource.active_revision_id = :revision_id
                GROUP BY resource.active_revision_id
                """
            ),
            {
                "organization_id": candidate_ref.organization_id,
                "source_ref": candidate_ref.source_ref,
                "resource_ref": candidate_ref.resource_ref,
                "revision_id": revision_id,
            },
        ).one_or_none()
        if row is None:
            return None
        return MaterializedPublicationTrace(
            states=tuple(row.states),
            active_revision_ref=str(row.active_revision_id),
        )

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

    def project(
        self,
        locator: MaterializedFragmentLocator,
    ) -> MaterializedFragmentProjection | None:
        revision_id = _canonical_candidate_revision(locator.revision_ref)
        if revision_id is None:
            return None
        self._connection.execute(
            text(
                """
                SELECT pg_catalog.pg_advisory_xact_lock_shared(
                    pg_catalog.hashtextextended(
                        'context-engine.field-rights:' ||
                        CAST(:organization_id AS text),
                        0
                    )
                )
                """
            ),
            {"organization_id": locator.organization_id},
        )
        rows = self._connection.execute(
            text(
                """
                SELECT
                    fragment.projection_kind,
                    fragment.content,
                    field.field_ref,
                    field.field_value,
                    field.ordinal
                FROM context_resource AS resource
                JOIN context_revision AS revision
                  ON revision.organization_id = resource.organization_id
                 AND revision.resource_ref = resource.resource_ref
                 AND revision.revision_id = resource.active_revision_id
                JOIN context_fragment AS fragment
                  ON fragment.organization_id = revision.organization_id
                 AND fragment.resource_ref = revision.resource_ref
                 AND fragment.revision_id = revision.revision_id
                LEFT JOIN context_fragment_field AS field
                  ON field.organization_id = fragment.organization_id
                 AND field.resource_ref = fragment.resource_ref
                 AND field.revision_id = fragment.revision_id
                 AND field.fragment_ref = fragment.fragment_ref
                 AND fragment.projection_kind = 'fields'
                JOIN membership AS actor_membership
                  ON actor_membership.organization_id = resource.organization_id
                 AND actor_membership.user_id = NULLIF(
                     current_setting('app.user_id'), ''
                 )::uuid
                 AND actor_membership.membership_id = NULLIF(
                     current_setting('app.membership_id'), ''
                 )::uuid
                 AND actor_membership.membership_version = NULLIF(
                     current_setting('app.membership_version'), ''
                 )::bigint
                 AND actor_membership.status = 'active'
                 AND actor_membership.valid_from <= NULLIF(
                     current_setting('app.checked_at'), ''
                 )::timestamptz
                 AND (
                     actor_membership.valid_until IS NULL
                     OR actor_membership.valid_until > NULLIF(
                         current_setting('app.checked_at'), ''
                     )::timestamptz
                 )
                JOIN resource_access_policy AS access_policy
                  ON access_policy.organization_id = resource.organization_id
                 AND access_policy.resource_ref = resource.resource_ref
                 AND access_policy.principal_ref = current_setting(
                     'app.principal_ref'
                 )
                 AND access_policy.access_state = 'allowed'
                JOIN membership_resource_field_right AS field_right
                  ON field_right.organization_id = fragment.organization_id
                 AND field_right.membership_id = actor_membership.membership_id
                 AND field_right.membership_version =
                     actor_membership.membership_version
                 AND field_right.resource_ref = fragment.resource_ref
                 AND field_right.field_ref = CASE
                     WHEN fragment.projection_kind = 'body' THEN 'body'
                     ELSE field.field_ref
                 END
                WHERE resource.organization_id = :organization_id
                  AND resource.source_ref = :source_ref
                  AND resource.resource_ref = :resource_ref
                  AND resource.active_revision_id = :revision_id
                  AND resource.tombstoned IS FALSE
                  AND fragment.fragment_ref = :fragment_ref
                ORDER BY field.ordinal NULLS LAST, field.field_ref
                """
            ),
            {
                "organization_id": locator.organization_id,
                "source_ref": locator.source_ref,
                "resource_ref": locator.resource_ref,
                "revision_id": revision_id,
                "fragment_ref": locator.fragment_ref,
            },
        ).all()
        if not rows:
            return None
        try:
            kind = MaterializedProjectionKind(rows[0].projection_kind)
        except (TypeError, ValueError):
            return None
        if kind is MaterializedProjectionKind.LEGACY_BODY:
            content = rows[0].content
            if (
                len(rows) != 1
                or type(content) is not str
                or not content
                or content.isspace()
            ):
                return None
            return MaterializedFragmentProjection(
                kind=kind,
                fields=(
                    MaterializedFieldValue(
                        field_ref="body",
                        field_value=content,
                        ordinal=0,
                    ),
                ),
                projection_ceiling=frozenset({"body"}),
            )

        authorized_rows = tuple(row for row in rows if row.field_ref is not None)
        if not authorized_rows:
            return None
        if any(
            type(row.field_value) is not str
            or not row.field_value
            or row.field_value.isspace()
            for row in authorized_rows
        ):
            return None
        fields = tuple(
            MaterializedFieldValue(
                field_ref=row.field_ref,
                field_value=row.field_value,
                ordinal=ordinal,
            )
            for ordinal, row in enumerate(authorized_rows)
        )
        return MaterializedFragmentProjection(
            kind=kind,
            fields=fields,
            projection_ceiling=frozenset(field.field_ref for field in fields),
        )


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


class _PostgreSQLContextRunPersistencePort:
    """Final run/audit inserts on the retained current-UserActor transaction."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def persist(
        self,
        run: ContextRunRecord,
        audit: DecisionAuditRecord | None,
    ) -> None:
        values = {
            field_name: getattr(run, field_name)
            for field_name in run.__dataclass_fields__
        }
        values["outcome"] = run.outcome.value
        values["authorized_evidence_refs"] = list(run.authorized_evidence_refs)
        insert_run = text(
            """
            INSERT INTO context_run (
                organization_id, run_ref, decision_ref,
                user_id, membership_id, membership_version,
                principal_ref, agent_version_ref,
                authenticated_application_ref, authentication_binding_ref,
                request_id, purpose, policy_snapshot_ref, policy_epoch,
                effective_scope_digest, query_digest_profile,
                query_digest_key_version, query_digest, outcome,
                package_digest_profile, package_digest,
                package_retention_mode, authorized_evidence_refs,
                effective_max_tokens, effective_max_provider_calls,
                effective_max_cost_microunits, effective_max_elapsed_ms,
                usage_tokens, usage_provider_calls,
                usage_cost_microunits, usage_elapsed_ms,
                accepted_at, finalized_at, package_as_of, package_expires_at
            ) VALUES (
                :organization_id, :run_ref, :decision_ref,
                :user_id, :membership_id, :membership_version,
                :principal_ref, :agent_version_ref,
                :authenticated_application_ref, :authentication_binding_ref,
                :request_id, :purpose, :policy_snapshot_ref, :policy_epoch,
                :effective_scope_digest, :query_digest_profile,
                :query_digest_key_version, :query_digest, :outcome,
                :package_digest_profile, :package_digest,
                :package_retention_mode, :authorized_evidence_refs,
                :effective_max_tokens, :effective_max_provider_calls,
                :effective_max_cost_microunits, :effective_max_elapsed_ms,
                :usage_tokens, :usage_provider_calls,
                :usage_cost_microunits, :usage_elapsed_ms,
                :accepted_at, :finalized_at, :package_as_of,
                :package_expires_at
            )
            """
        ).bindparams(
            bindparam(
                "authorized_evidence_refs",
                type_=postgresql.JSONB(),
            )
        )
        self._connection.execute(
            insert_run,
            values,
        )
        if audit is None:
            return
        self._connection.execute(
            text(
                """
                INSERT INTO decision_audit (
                    organization_id, run_ref, decision_ref,
                    policy_snapshot_ref, policy_epoch, category, recorded_at
                ) VALUES (
                    :organization_id, :run_ref, :decision_ref,
                    :policy_snapshot_ref, :policy_epoch, :category, :recorded_at
                )
                """
            ),
            {
                "organization_id": audit.organization_id,
                "run_ref": audit.run_ref,
                "decision_ref": audit.decision_ref,
                "policy_snapshot_ref": audit.policy_snapshot_ref,
                "policy_epoch": audit.policy_epoch,
                "category": audit.category.value,
                "recorded_at": audit.recorded_at,
            },
        )


class PostgreSQLMembershipAuthority:
    """Open and retain the exact UserActor transaction through Runtime work."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @contextmanager
    def current_projection_session(
        self,
        identity: MembershipIdentity,
    ) -> Iterator[MaterializedProjectionSession]:
        """Expose the retained authorized projection seam to trusted callers."""

        with self.current_user_actor(identity) as verification:
            session = verification.materialized_projection_session
            if session is None:  # pragma: no cover - closed PostgreSQL composition
                raise MembershipAuthorityUnavailable(
                    "materialized projection session is unavailable"
                )
            yield session

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
                text("SELECT set_config(:setting_name, :setting_value, true)"),
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
                raise MembershipAuthorityUnavailable("UserActor context binding failed")

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
        connection.execute(
            text(
                """
                SELECT pg_catalog.pg_advisory_xact_lock_shared(
                    pg_catalog.hashtextextended(
                        'context-engine.file-publication:'
                        || CAST(:organization_id AS text),
                        0
                    )
                )
                """
            ),
            {"organization_id": identity.organization_id},
        )

        scope = _open_membership_authority_scope()
        projection_scope = _open_materialized_projection_scope()
        policy_epoch_scope = _open_policy_epoch_authority_scope()
        context_run_scope = _open_context_run_persistence_scope()
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
            context_run_session = _construct_context_run_persistence_session(
                authority_scope=context_run_scope,
                port=_PostgreSQLContextRunPersistencePort(connection),
            )
            yield _construct_current_membership_verification(
                organization_id=identity.organization_id,
                user_id=identity.user_id,
                membership_id=identity.membership_id,
                membership_version=identity.membership_version,
                principal_ref=identity.principal_ref,
                request_id=identity.request_id,
                authentication_binding_ref=(identity.authentication_binding_ref),
                checked_at=identity.checked_at,
                policy_epoch_verification=policy_epoch_verification,
                authority_scope=scope,
                materialized_projection_session=projection_session,
                context_run_persistence_session=context_run_session,
            )
        finally:
            _close_context_run_persistence_scope(context_run_scope)
            _close_policy_epoch_authority_scope(policy_epoch_scope)
            _close_materialized_projection_scope(projection_scope)
            _close_membership_authority_scope(scope)
