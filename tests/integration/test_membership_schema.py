from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from engine.persistence import DatabaseConfiguration, create_database_engine
from engine.persistence.configuration import RUNTIME_ROLE, WORKER_ROLE

pytestmark = pytest.mark.integration
CHECKED_AT = datetime(2026, 7, 21, 8, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class IdentityFixture:
    organization_a: UUID
    organization_b: UUID
    user_a: UUID
    user_b: UUID
    user_without_membership: UUID
    membership_a: UUID
    membership_b: UUID


@contextmanager
def user_actor_connection(
    engine: Engine,
    *,
    organization_id: UUID,
    user_id: UUID,
    membership_id: UUID,
    membership_version: int = 1,
    checked_at: datetime = CHECKED_AT,
) -> Iterator[Connection]:
    settings = {
        "app.organization_id": str(organization_id),
        "app.actor_kind": "user",
        "app.user_id": str(user_id),
        "app.membership_id": str(membership_id),
        "app.membership_version": str(membership_version),
        "app.principal_ref": f"principal:{user_id}",
        "app.request_id": f"request:{uuid4()}",
        "app.authentication_binding_ref": f"binding:{uuid4()}",
        "app.checked_at": checked_at.isoformat().replace("+00:00", "Z"),
    }
    with engine.begin() as connection:
        for setting_name, setting_value in settings.items():
            connection.execute(
                text("SELECT set_config(:name, :value, true)"),
                {"name": setting_name, "value": setting_value},
            )
        yield connection


@pytest.fixture
def identities(
    migration_configuration: DatabaseConfiguration,
) -> Iterator[IdentityFixture]:
    repeated_membership_id = uuid4()
    identity = IdentityFixture(
        organization_a=uuid4(),
        organization_b=uuid4(),
        user_a=uuid4(),
        user_b=uuid4(),
        user_without_membership=uuid4(),
        membership_a=repeated_membership_id,
        membership_b=repeated_membership_id,
    )
    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO organization (organization_id)
                    VALUES (:organization_a), (:organization_b)
                    """
                ),
                asdict(identity),
            )
            connection.execute(
                text(
                    """
                    INSERT INTO user_account (user_id)
                    VALUES (:user_a), (:user_b), (:user_without_membership)
                    """
                ),
                asdict(identity),
            )
            connection.execute(
                text(
                    """
                    INSERT INTO membership (
                        organization_id, membership_id, user_id, status,
                        membership_version, valid_from, valid_until
                    ) VALUES
                    (
                        :organization_a, :membership_a, :user_a, 'active',
                        1, :valid_from, NULL
                    ),
                    (
                        :organization_b, :membership_b, :user_b, 'active',
                        1, :valid_from, NULL
                    )
                    """
                ),
                {
                    **asdict(identity),
                    "valid_from": CHECKED_AT - timedelta(days=1),
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO organization_record (
                        organization_id, record_id, parent_record_id, payload
                    ) VALUES
                    (:organization_a, :record_a, NULL, 'organization-a'),
                    (:organization_b, :record_b, NULL, 'organization-b')
                    """
                ),
                {
                    **asdict(identity),
                    "record_a": uuid4(),
                    "record_b": uuid4(),
                },
            )
        yield identity
    finally:
        with engine.begin() as connection:
            parameters = asdict(identity)
            connection.execute(
                text(
                    """
                    DELETE FROM organization_record
                    WHERE organization_id IN (:organization_a, :organization_b)
                    """
                ),
                parameters,
            )
            connection.execute(
                text(
                    """
                    DELETE FROM membership
                    WHERE organization_id IN (:organization_a, :organization_b)
                    """
                ),
                parameters,
            )
            connection.execute(
                text(
                    """
                    DELETE FROM user_account
                    WHERE user_id IN (:user_a, :user_b, :user_without_membership)
                    """
                ),
                parameters,
            )
            connection.execute(
                text(
                    """
                    DELETE FROM organization
                    WHERE organization_id IN (:organization_a, :organization_b)
                    """
                ),
                parameters,
            )
        engine.dispose()


def membership_rows(connection: Connection) -> list[tuple[UUID, UUID, UUID]]:
    return [
        (row.organization_id, row.membership_id, row.user_id)
        for row in connection.execute(
            text(
                """
                SELECT organization_id, membership_id, user_id
                FROM membership
                ORDER BY organization_id
                """
            )
        )
    ]


@pytest.mark.security_evidence(id="PG-SCOPE-INTERSECTION-004", layer="postgres")
def test_runtime_membership_rls_is_bidirectional_and_exact(
    guarded_runtime_engine: Engine,
    identities: IdentityFixture,
) -> None:
    assert identities.membership_a == identities.membership_b
    directions = (
        (
            identities.organization_a,
            identities.user_a,
            identities.membership_a,
        ),
        (
            identities.organization_b,
            identities.user_b,
            identities.membership_b,
        ),
    )
    for organization_id, user_id, membership_id in directions:
        with user_actor_connection(
            guarded_runtime_engine,
            organization_id=organization_id,
            user_id=user_id,
            membership_id=membership_id,
        ) as connection:
            assert membership_rows(connection) == [
                (organization_id, membership_id, user_id)
            ]
            assert connection.execute(
                text("SELECT count(*) FROM organization_record")
            ).scalar_one() == 1

    with user_actor_connection(
        guarded_runtime_engine,
        organization_id=identities.organization_a,
        user_id=identities.user_b,
        membership_id=identities.membership_b,
    ) as connection:
        assert membership_rows(connection) == []
        assert connection.execute(
            text("SELECT count(*) FROM organization_record")
        ).scalar_one() == 0


@pytest.mark.parametrize(
    ("status", "version", "valid_from_delta", "valid_until_delta"),
    [
        ("inactive", 1, timedelta(days=-1), None),
        ("revoked", 1, timedelta(days=-1), None),
        ("active", 2, timedelta(days=-1), None),
        ("active", 1, timedelta(seconds=1), None),
        ("active", 1, timedelta(days=-2), timedelta(seconds=-1)),
        ("active", 1, timedelta(days=-2), timedelta(0)),
    ],
)
def test_status_version_and_validity_fail_closed_at_the_database_seam(
    guarded_runtime_engine: Engine,
    migration_configuration: DatabaseConfiguration,
    identities: IdentityFixture,
    status: str,
    version: int,
    valid_from_delta: timedelta,
    valid_until_delta: timedelta | None,
) -> None:
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE membership
                    SET status = :status,
                        membership_version = :version,
                        valid_from = :valid_from,
                        valid_until = :valid_until
                    WHERE organization_id = :organization_id
                      AND membership_id = :membership_id
                    """
                ),
                {
                    "status": status,
                    "version": version,
                    "valid_from": CHECKED_AT + valid_from_delta,
                    "valid_until": (
                        None
                        if valid_until_delta is None
                        else CHECKED_AT + valid_until_delta
                    ),
                    "organization_id": identities.organization_a,
                    "membership_id": identities.membership_a,
                },
            )
        with user_actor_connection(
            guarded_runtime_engine,
            organization_id=identities.organization_a,
            user_id=identities.user_a,
            membership_id=identities.membership_a,
        ) as connection:
            assert membership_rows(connection) == []
            assert connection.execute(
                text("SELECT count(*) FROM organization_record")
            ).scalar_one() == 0
    finally:
        migration_engine.dispose()


@pytest.mark.security_evidence(id="PG-TRANSPORT-UNTRUSTED-008", layer="postgres")
def test_organization_only_and_user_only_context_have_no_tenant_rights(
    guarded_runtime_engine: Engine,
    identities: IdentityFixture,
) -> None:
    with guarded_runtime_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.organization_id', :value, true)"),
            {"value": str(identities.organization_a)},
        )
        assert membership_rows(connection) == []
        assert connection.execute(
            text("SELECT count(*) FROM organization_record")
        ).scalar_one() == 0
        with pytest.raises(DBAPIError, match="current UserActor Membership"):
            connection.execute(
                text(
                    """
                    INSERT INTO organization_record (
                        organization_id, record_id, parent_record_id, payload
                    ) VALUES (:organization_id, :record_id, NULL, 'denied')
                    """
                ),
                {
                    "organization_id": identities.organization_a,
                    "record_id": uuid4(),
                },
            )

    with user_actor_connection(
        guarded_runtime_engine,
        organization_id=identities.organization_a,
        user_id=identities.user_without_membership,
        membership_id=uuid4(),
    ) as connection:
        assert membership_rows(connection) == []
        assert connection.execute(
            text("SELECT count(*) FROM organization_record")
        ).scalar_one() == 0
        with pytest.raises(DBAPIError, match="current UserActor Membership"):
            connection.execute(
                text(
                    """
                    INSERT INTO organization_record (
                        organization_id, record_id, parent_record_id, payload
                    ) VALUES (:organization_id, :record_id, NULL, 'denied')
                    """
                ),
                {
                    "organization_id": identities.organization_a,
                    "record_id": uuid4(),
                },
            )

    with guarded_runtime_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.actor_kind', 'user', true)")
        )
        connection.execute(
            text("SELECT set_config('app.user_id', :value, true)"),
            {"value": str(identities.user_a)},
        )
        assert membership_rows(connection) == []
        assert connection.execute(
            text("SELECT count(*) FROM organization_record")
        ).scalar_one() == 0


def test_global_user_and_membership_constraints_reject_orphans_and_invalid_rows(
    migration_configuration: DatabaseConfiguration,
    identities: IdentityFixture,
) -> None:
    engine = create_database_engine(migration_configuration)
    try:
        invalid_rows = (
            (
                {"organization": uuid4(), "user": identities.user_a},
                "fk_membership_organization",
            ),
            (
                {"organization": identities.organization_a, "user": uuid4()},
                "fk_membership_user_account",
            ),
        )
        for values, constraint in invalid_rows:
            with (
                pytest.raises(IntegrityError, match=constraint),
                engine.begin() as conn,
            ):
                conn.execute(
                    text(
                        """
                        INSERT INTO membership (
                            organization_id, membership_id, user_id, status,
                            membership_version, valid_from, valid_until
                        ) VALUES (
                            :organization, :membership, :user, 'active',
                            1, :valid_from, NULL
                        )
                        """
                    ),
                    {
                        **values,
                        "membership": uuid4(),
                        "valid_from": CHECKED_AT,
                    },
                )

        for overrides, constraint in (
            ({"status": "guest"}, "ck_membership_status"),
            ({"version": 0}, "ck_membership_version_positive"),
            ({"valid_until": CHECKED_AT}, "ck_membership_valid_interval"),
        ):
            constraint_values: dict[str, Any] = {
                "status": "active",
                "version": 1,
                "valid_from": CHECKED_AT,
                "valid_until": None,
                **overrides,
            }
            with (
                pytest.raises(IntegrityError, match=constraint),
                engine.begin() as conn,
            ):
                conn.execute(
                    text(
                        """
                        INSERT INTO membership (
                            organization_id, membership_id, user_id, status,
                            membership_version, valid_from, valid_until
                        ) VALUES (
                            :organization, :membership, :user, :status,
                            :version, :valid_from, :valid_until
                        )
                        """
                    ),
                    {
                        **constraint_values,
                        "organization": identities.organization_a,
                        "membership": uuid4(),
                        "user": identities.user_b,
                    },
                )
    finally:
        engine.dispose()


def test_runtime_worker_and_public_grants_are_least_privilege(
    migration_configuration: DatabaseConfiguration,
) -> None:
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            grants = {
                (row.grantee, row.table_name, row.privilege_type)
                for row in connection.execute(
                    text(
                        """
                        SELECT grantee, table_name, privilege_type
                        FROM information_schema.table_privileges
                        WHERE table_schema = 'public'
                          AND table_name IN ('user_account', 'membership')
                          AND grantee IN ('PUBLIC', :runtime_role, :worker_role)
                        """
                    ),
                    {"runtime_role": RUNTIME_ROLE, "worker_role": WORKER_ROLE},
                )
            }
            security = tuple(
                connection.execute(
                    text(
                        """
                        SELECT relrowsecurity, relforcerowsecurity
                        FROM pg_class
                        WHERE oid = 'public.membership'::regclass
                        """
                    )
                ).one()
            )
            policies = {
                row.policyname: (
                    row.permissive,
                    tuple(row.roles),
                    row.cmd,
                    row.qual,
                    row.with_check,
                )
                for row in connection.execute(
                    text(
                        """
                        SELECT policyname, permissive, roles, cmd, qual, with_check
                        FROM pg_policies
                        WHERE schemaname = 'public'
                          AND tablename = 'membership'
                        """
                    )
                )
            }
            constraints = {
                row.conname: row.definition
                for row in connection.execute(
                    text(
                        """
                        SELECT conname, pg_get_constraintdef(oid, true) AS definition
                        FROM pg_constraint
                        WHERE conrelid = 'public.membership'::regclass
                        """
                    )
                )
            }
        assert grants == {(RUNTIME_ROLE, "membership", "SELECT")}
        assert security == (True, True)
        assert set(policies) == {
            "membership_current_user_actor",
            "membership_migrator_administration",
        }
        runtime_policy = policies["membership_current_user_actor"]
        assert runtime_policy[:3] == (
            "PERMISSIVE",
            (RUNTIME_ROLE,),
            "SELECT",
        )
        assert runtime_policy[3] is not None
        assert runtime_policy[4] is None
        normalized_policy = str(runtime_policy[3]).lower()
        for required_fragment in (
            "app.organization_id",
            "app.actor_kind",
            "app.user_id",
            "app.membership_id",
            "app.membership_version",
            "app.principal_ref",
            "app.request_id",
            "app.authentication_binding_ref",
            "app.checked_at",
            "status",
            "valid_from",
            "valid_until",
        ):
            assert required_fragment in normalized_policy

        migrator_policy = policies["membership_migrator_administration"]
        assert migrator_policy[:3] == (
            "PERMISSIVE",
            ("context_engine_migrator",),
            "ALL",
        )
        assert str(migrator_policy[3]).lower() == "true"
        assert str(migrator_policy[4]).lower() == "true"

        assert set(constraints) == {
            "pk_membership",
            "uq_membership_organization_user",
            "uq_membership_organization_id_version",
            "fk_membership_organization",
            "fk_membership_user_account",
            "ck_membership_status",
            "ck_membership_version_positive",
            "ck_membership_valid_interval",
        }
        assert constraints["pk_membership"].lower() == (
            "primary key (organization_id, membership_id)"
        )
        assert constraints["uq_membership_organization_user"].lower() == (
            "unique (organization_id, user_id)"
        )
        assert constraints["uq_membership_organization_id_version"].lower() == (
            "unique (organization_id, membership_id, membership_version)"
        )
        assert constraints["fk_membership_organization"].lower().startswith(
            "foreign key (organization_id) references organization(organization_id)"
        )
        assert constraints["fk_membership_user_account"].lower().startswith(
            "foreign key (user_id) references user_account(user_id)"
        )
    finally:
        engine.dispose()


def test_invalid_actor_mutations_hide_existing_rows_and_produce_zero_effects(
    guarded_runtime_engine: Engine,
    identities: IdentityFixture,
) -> None:
    valid_settings = {
        "app.organization_id": str(identities.organization_a),
        "app.actor_kind": "user",
        "app.user_id": str(identities.user_a),
        "app.membership_id": str(identities.membership_a),
        "app.membership_version": "1",
        "app.principal_ref": f"principal:{identities.user_a}",
        "app.request_id": "request-valid",
        "app.authentication_binding_ref": "binding-valid",
        "app.checked_at": CHECKED_AT.isoformat().replace("+00:00", "Z"),
    }
    mutations = (
        ("app.actor_kind", "service"),
        ("app.user_id", str(identities.user_b)),
        ("app.membership_id", str(uuid4())),
        ("app.membership_version", "2"),
        ("app.principal_ref", ""),
        ("app.request_id", ""),
        ("app.authentication_binding_ref", ""),
        ("app.checked_at", ""),
    )
    for setting_name, setting_value in mutations:
        settings = {**valid_settings, setting_name: setting_value}
        with guarded_runtime_engine.begin() as connection:
            for name, value in settings.items():
                connection.execute(
                    text("SELECT set_config(:name, :value, true)"),
                    {"name": name, "value": value},
                )
            assert membership_rows(connection) == []
            assert connection.execute(
                text("SELECT count(*) FROM organization_record")
            ).scalar_one() == 0
            with pytest.raises(DBAPIError, match="current UserActor Membership"):
                connection.execute(
                    text(
                        """
                        UPDATE organization_record
                        SET payload = 'denied'
                        WHERE organization_id = :organization_id
                        """
                    ),
                    {"organization_id": identities.organization_a},
                )
