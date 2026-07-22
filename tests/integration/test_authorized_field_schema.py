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
from engine.persistence.configuration import MIGRATOR_ROLE, RUNTIME_ROLE, WORKER_ROLE

pytestmark = pytest.mark.integration
CHECKED_AT = datetime(2026, 7, 22, 10, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class FieldProjectionFixture:
    organization_id: UUID
    full_user_id: UUID
    full_membership_id: UUID
    limited_user_id: UUID
    limited_membership_id: UUID
    resource_ref: str
    revision_id: UUID
    fields_fragment_ref: str
    body_fragment_ref: str


@contextmanager
def user_actor_connection(
    engine: Engine,
    fixture: FieldProjectionFixture,
    *,
    full: bool,
    membership_version: int = 7,
    membership_id: UUID | None = None,
) -> Iterator[Connection]:
    user_id = fixture.full_user_id if full else fixture.limited_user_id
    expected_membership_id = (
        fixture.full_membership_id if full else fixture.limited_membership_id
    )
    settings = {
        "app.organization_id": str(fixture.organization_id),
        "app.actor_kind": "user",
        "app.user_id": str(user_id),
        "app.membership_id": str(membership_id or expected_membership_id),
        "app.membership_version": str(membership_version),
        "app.principal_ref": f"principal:{user_id}",
        "app.request_id": f"request:{uuid4()}",
        "app.authentication_binding_ref": f"binding:{uuid4()}",
        "app.checked_at": CHECKED_AT.isoformat().replace("+00:00", "Z"),
    }
    with engine.begin() as connection:
        for setting_name, setting_value in settings.items():
            connection.execute(
                text("SELECT set_config(:name, :value, true)"),
                {"name": setting_name, "value": setting_value},
            )
        yield connection


@pytest.fixture(scope="module")
def field_projection_fixture(
    migration_configuration: DatabaseConfiguration,
) -> Iterator[FieldProjectionFixture]:
    fixture = FieldProjectionFixture(
        organization_id=uuid4(),
        full_user_id=uuid4(),
        full_membership_id=uuid4(),
        limited_user_id=uuid4(),
        limited_membership_id=uuid4(),
        resource_ref=f"resource:{uuid4()}",
        revision_id=uuid4(),
        fields_fragment_ref=f"fragment:{uuid4()}",
        body_fragment_ref=f"fragment:{uuid4()}",
    )
    engine = create_database_engine(migration_configuration)
    parameters = asdict(fixture)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO organization (organization_id) "
                    "VALUES (:organization_id)"
                ),
                parameters,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO user_account (user_id)
                    VALUES (:full_user_id), (:limited_user_id)
                    """
                ),
                parameters,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO membership (
                        organization_id, membership_id, user_id, status,
                        membership_version, valid_from, valid_until
                    ) VALUES
                    (
                        :organization_id, :full_membership_id, :full_user_id,
                        'active', 7, :valid_from, NULL
                    ),
                    (
                        :organization_id, :limited_membership_id,
                        :limited_user_id, 'active', 7, :valid_from, NULL
                    )
                    """
                ),
                {
                    **parameters,
                    "valid_from": CHECKED_AT - timedelta(days=1),
                },
            )
            connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            connection.execute(
                text(
                    """
                    INSERT INTO context_resource (
                        organization_id, resource_ref, source_ref,
                        active_revision_id, tombstoned
                    ) VALUES (
                        :organization_id, :resource_ref, 'source:fields',
                        :revision_id, false
                    )
                    """
                ),
                parameters,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO context_revision (
                        organization_id, resource_ref, revision_id
                    ) VALUES (
                        :organization_id, :resource_ref, :revision_id
                    )
                    """
                ),
                parameters,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO context_fragment (
                        organization_id, resource_ref, revision_id,
                        fragment_ref, ordinal, projection_kind, content
                    ) VALUES
                    (
                        :organization_id, :resource_ref, :revision_id,
                        :fields_fragment_ref, 0, 'fields', NULL
                    ),
                    (
                        :organization_id, :resource_ref, :revision_id,
                        :body_fragment_ref, 1, 'body', 'private-body=secret'
                    )
                    """
                ),
                parameters,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO context_fragment_field (
                        organization_id, resource_ref, revision_id,
                        fragment_ref, field_ref, ordinal, field_value
                    ) VALUES
                    (
                        :organization_id, :resource_ref, :revision_id,
                        :fields_fragment_ref, 'status', 0, 'open'
                    ),
                    (
                        :organization_id, :resource_ref, :revision_id,
                        :fields_fragment_ref, 'private_note', 1, 'secret'
                    )
                    """
                ),
                parameters,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO membership_resource_field_right (
                        organization_id, membership_id, membership_version,
                        resource_ref, field_ref
                    ) VALUES
                    (
                        :organization_id, :full_membership_id, 7,
                        :resource_ref, 'status'
                    ),
                    (
                        :organization_id, :full_membership_id, 7,
                        :resource_ref, 'private_note'
                    ),
                    (
                        :organization_id, :full_membership_id, 7,
                        :resource_ref, 'body'
                    ),
                    (
                        :organization_id, :limited_membership_id, 7,
                        :resource_ref, 'status'
                    )
                    """
                ),
                parameters,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO resource_access_policy (
                        organization_id, resource_ref, principal_ref,
                        access_version, access_state, revoked_at
                    ) VALUES
                    (
                        :organization_id, :resource_ref, :full_principal_ref,
                        1, 'allowed', NULL
                    ),
                    (
                        :organization_id, :resource_ref, :limited_principal_ref,
                        1, 'allowed', NULL
                    )
                    """
                ),
                {
                    **parameters,
                    "full_principal_ref": f"principal:{fixture.full_user_id}",
                    "limited_principal_ref": (f"principal:{fixture.limited_user_id}"),
                },
            )
        yield fixture
    finally:
        with engine.begin() as connection:
            for table_name, trigger_name in (
                (
                    "context_fragment_field",
                    "context_fragment_field_reject_mutation",
                ),
                ("context_fragment", "context_fragment_reject_mutation"),
                ("context_revision", "context_revision_reject_mutation"),
            ):
                connection.execute(
                    text(f"ALTER TABLE {table_name} DISABLE TRIGGER {trigger_name}")
                )
        try:
            with engine.begin() as connection:
                for statement in (
                    "DELETE FROM resource_access_policy "
                    "WHERE organization_id = :organization_id",
                    "DELETE FROM membership_resource_field_right "
                    "WHERE organization_id = :organization_id",
                    "DELETE FROM context_fragment_field "
                    "WHERE organization_id = :organization_id",
                    "DELETE FROM context_fragment "
                    "WHERE organization_id = :organization_id",
                    "DELETE FROM context_revision "
                    "WHERE organization_id = :organization_id",
                    "DELETE FROM context_resource "
                    "WHERE organization_id = :organization_id",
                    "DELETE FROM membership WHERE organization_id = :organization_id",
                    "DELETE FROM user_account "
                    "WHERE user_id IN (:full_user_id, :limited_user_id)",
                    "DELETE FROM organization WHERE organization_id = :organization_id",
                ):
                    connection.execute(text(statement), parameters)
        finally:
            with engine.begin() as connection:
                for table_name, trigger_name in (
                    ("context_revision", "context_revision_reject_mutation"),
                    ("context_fragment", "context_fragment_reject_mutation"),
                    (
                        "context_fragment_field",
                        "context_fragment_field_reject_mutation",
                    ),
                ):
                    connection.execute(
                        text(f"ALTER TABLE {table_name} ENABLE TRIGGER {trigger_name}")
                    )
            engine.dispose()


def _visible_projection(
    connection: Connection,
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    fragments = [
        tuple(row)
        for row in connection.execute(
            text(
                """
                SELECT fragment_ref, projection_kind, content
                FROM context_fragment
                ORDER BY ordinal
                """
            )
        )
    ]
    fields = [
        tuple(row)
        for row in connection.execute(
            text(
                """
                SELECT fragment_ref, field_ref, field_value
                FROM context_fragment_field
                ORDER BY ordinal
                """
            )
        )
    ]
    return fragments, fields


def test_runtime_field_rows_and_legacy_body_are_filtered_by_exact_right(
    guarded_runtime_engine: Engine,
    field_projection_fixture: FieldProjectionFixture,
) -> None:
    with user_actor_connection(
        guarded_runtime_engine, field_projection_fixture, full=True
    ) as connection:
        full_fragments, full_fields = _visible_projection(connection)
    assert full_fragments == [
        (field_projection_fixture.fields_fragment_ref, "fields", None),
        (
            field_projection_fixture.body_fragment_ref,
            "body",
            "private-body=secret",
        ),
    ]
    assert full_fields == [
        (field_projection_fixture.fields_fragment_ref, "status", "open"),
        (field_projection_fixture.fields_fragment_ref, "private_note", "secret"),
    ]

    with user_actor_connection(
        guarded_runtime_engine, field_projection_fixture, full=False
    ) as connection:
        limited_fragments, limited_fields = _visible_projection(connection)
        limited_rights = (
            connection.execute(
                text(
                    """
                SELECT field_ref
                FROM membership_resource_field_right
                ORDER BY field_ref
                """
                )
            )
            .scalars()
            .all()
        )
    assert limited_fragments == [
        (field_projection_fixture.fields_fragment_ref, "fields", None)
    ]
    assert limited_fields == [
        (field_projection_fixture.fields_fragment_ref, "status", "open")
    ]
    assert limited_rights == ["status"]
    assert "secret" not in repr((limited_fragments, limited_fields, limited_rights))


def test_revoked_resource_acl_filters_every_projection_row_and_right(
    guarded_runtime_engine: Engine,
    migration_configuration: DatabaseConfiguration,
    field_projection_fixture: FieldProjectionFixture,
) -> None:
    migration_engine = create_database_engine(migration_configuration)
    principal_ref = f"principal:{field_projection_fixture.full_user_id}"
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE resource_access_policy
                    SET access_version = access_version + 1,
                        access_state = 'revoked',
                        revoked_at = :revoked_at
                    WHERE organization_id = :organization_id
                      AND resource_ref = :resource_ref
                      AND principal_ref = :principal_ref
                    """
                ),
                {
                    **asdict(field_projection_fixture),
                    "principal_ref": principal_ref,
                    "revoked_at": CHECKED_AT,
                },
            )
        with user_actor_connection(
            guarded_runtime_engine,
            field_projection_fixture,
            full=True,
        ) as connection:
            rights = (
                connection.execute(
                    text(
                        """
                    SELECT field_ref
                    FROM membership_resource_field_right
                    ORDER BY field_ref
                    """
                    )
                )
                .scalars()
                .all()
            )
            fields = connection.execute(
                text(
                    """
                    SELECT field_ref, field_value
                    FROM context_fragment_field
                    ORDER BY ordinal
                    """
                )
            ).all()
            fragments = connection.execute(
                text(
                    """
                    SELECT projection_kind, content
                    FROM context_fragment
                    ORDER BY ordinal
                    """
                )
            ).all()
        assert rights == []
        assert fields == []
        assert fragments == []
        assert "private-body=secret" not in repr((rights, fields, fragments))
    finally:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE resource_access_policy
                    SET access_version = access_version + 1,
                        access_state = 'allowed',
                        revoked_at = NULL
                    WHERE organization_id = :organization_id
                      AND resource_ref = :resource_ref
                      AND principal_ref = :principal_ref
                    """
                ),
                {
                    **asdict(field_projection_fixture),
                    "principal_ref": principal_ref,
                },
            )
        migration_engine.dispose()


@pytest.mark.security_evidence(id="PG-FIELD-PROJECTION-RLS-048", layer="postgres")
def test_cross_organization_field_authority_and_values_fail_closed(
    guarded_runtime_engine: Engine,
    migration_configuration: DatabaseConfiguration,
    field_projection_fixture: FieldProjectionFixture,
) -> None:
    other = {
        "other_organization_id": uuid4(),
        "other_user_id": uuid4(),
        "other_membership_id": uuid4(),
        "other_resource_ref": f"resource:cross:{uuid4()}",
        "other_revision_id": uuid4(),
        "other_fragment_ref": f"fragment:cross:{uuid4()}",
        "other_principal_ref": f"principal:cross:{uuid4()}",
        "valid_from": CHECKED_AT - timedelta(days=1),
    }
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO organization (organization_id) "
                    "VALUES (:other_organization_id)"
                ),
                other,
            )
            connection.execute(
                text("INSERT INTO user_account (user_id) VALUES (:other_user_id)"),
                other,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO membership (
                        organization_id, membership_id, user_id, status,
                        membership_version, valid_from, valid_until
                    ) VALUES (
                        :other_organization_id, :other_membership_id,
                        :other_user_id, 'active', 7, :valid_from, NULL
                    )
                    """
                ),
                other,
            )
            connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            connection.execute(
                text(
                    """
                    INSERT INTO context_resource (
                        organization_id, resource_ref, source_ref,
                        active_revision_id, tombstoned
                    ) VALUES (
                        :other_organization_id, :other_resource_ref,
                        'source:cross-fields', :other_revision_id, false
                    )
                    """
                ),
                other,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO context_revision (
                        organization_id, resource_ref, revision_id
                    ) VALUES (
                        :other_organization_id, :other_resource_ref,
                        :other_revision_id
                    )
                    """
                ),
                other,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO context_fragment (
                        organization_id, resource_ref, revision_id,
                        fragment_ref, ordinal, projection_kind, content
                    ) VALUES (
                        :other_organization_id, :other_resource_ref,
                        :other_revision_id, :other_fragment_ref,
                        0, 'fields', NULL
                    )
                    """
                ),
                other,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO context_fragment_field (
                        organization_id, resource_ref, revision_id,
                        fragment_ref, field_ref, ordinal, field_value
                    ) VALUES (
                        :other_organization_id, :other_resource_ref,
                        :other_revision_id, :other_fragment_ref,
                        'private_note', 0, 'cross-organization-secret'
                    )
                    """
                ),
                other,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO membership_resource_field_right (
                        organization_id, membership_id, membership_version,
                        resource_ref, field_ref
                    ) VALUES (
                        :other_organization_id, :other_membership_id, 7,
                        :other_resource_ref, 'private_note'
                    )
                    """
                ),
                other,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO resource_access_policy (
                        organization_id, resource_ref, principal_ref,
                        access_version, access_state, revoked_at
                    ) VALUES (
                        :other_organization_id, :other_resource_ref,
                        :other_principal_ref, 1, 'allowed', NULL
                    )
                    """
                ),
                other,
            )

        with user_actor_connection(
            guarded_runtime_engine,
            field_projection_fixture,
            full=True,
        ) as connection:
            cross_fields = connection.execute(
                text(
                    """
                    SELECT field_ref, field_value
                    FROM context_fragment_field
                    WHERE organization_id = :other_organization_id
                    """
                ),
                other,
            ).all()
            cross_rights = connection.execute(
                text(
                    """
                    SELECT field_ref
                    FROM membership_resource_field_right
                    WHERE organization_id = :other_organization_id
                    """
                ),
                other,
            ).all()
        assert cross_fields == []
        assert cross_rights == []
        assert "cross-organization-secret" not in repr((cross_fields, cross_rights))

        with (
            pytest.raises(IntegrityError) as wrong_resource,
            migration_engine.begin() as connection,
        ):
            connection.execute(
                text(
                    """
                    INSERT INTO membership_resource_field_right (
                        organization_id, membership_id, membership_version,
                        resource_ref, field_ref
                    ) VALUES (
                        :organization_id, :full_membership_id, 7,
                        :other_resource_ref, 'private_note'
                    )
                    """
                ),
                {**asdict(field_projection_fixture), **other},
            )
        assert (
            getattr(
                getattr(wrong_resource.value.orig, "diag", None),
                "constraint_name",
                None,
            )
            == "fk_membership_field_right_resource_same_organization"
        )
    finally:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE context_fragment_field DISABLE TRIGGER "
                    "context_fragment_field_reject_mutation"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE context_fragment DISABLE TRIGGER "
                    "context_fragment_reject_mutation"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE context_revision DISABLE TRIGGER "
                    "context_revision_reject_mutation"
                )
            )
        try:
            with migration_engine.begin() as connection:
                for statement in (
                    "DELETE FROM resource_access_policy "
                    "WHERE organization_id = :other_organization_id",
                    "DELETE FROM membership_resource_field_right "
                    "WHERE organization_id = :other_organization_id",
                    "DELETE FROM context_fragment_field "
                    "WHERE organization_id = :other_organization_id",
                    "DELETE FROM context_fragment "
                    "WHERE organization_id = :other_organization_id",
                    "DELETE FROM context_revision "
                    "WHERE organization_id = :other_organization_id",
                    "DELETE FROM context_resource "
                    "WHERE organization_id = :other_organization_id",
                    "DELETE FROM membership "
                    "WHERE organization_id = :other_organization_id",
                    "DELETE FROM user_account WHERE user_id = :other_user_id",
                    "DELETE FROM organization "
                    "WHERE organization_id = :other_organization_id",
                ):
                    connection.execute(text(statement), other)
        finally:
            with migration_engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE context_revision ENABLE TRIGGER "
                        "context_revision_reject_mutation"
                    )
                )
                connection.execute(
                    text(
                        "ALTER TABLE context_fragment ENABLE TRIGGER "
                        "context_fragment_reject_mutation"
                    )
                )
                connection.execute(
                    text(
                        "ALTER TABLE context_fragment_field ENABLE TRIGGER "
                        "context_fragment_field_reject_mutation"
                    )
                )
            migration_engine.dispose()


@pytest.mark.parametrize(
    ("full", "membership_version", "membership_id"),
    [
        pytest.param(False, 6, None, id="stale-membership-version"),
        pytest.param(False, 7, "full", id="forged-membership-id"),
    ],
)
def test_stale_or_forged_membership_context_exposes_zero_field_content(
    guarded_runtime_engine: Engine,
    field_projection_fixture: FieldProjectionFixture,
    full: bool,
    membership_version: int,
    membership_id: str | None,
) -> None:
    forged_membership_id = (
        field_projection_fixture.full_membership_id if membership_id == "full" else None
    )
    with user_actor_connection(
        guarded_runtime_engine,
        field_projection_fixture,
        full=full,
        membership_version=membership_version,
        membership_id=forged_membership_id,
    ) as connection:
        fragments, fields = _visible_projection(connection)
        rights = connection.execute(
            text("SELECT field_ref FROM membership_resource_field_right")
        ).all()
    assert fragments == []
    assert fields == []
    assert rights == []


@pytest.mark.parametrize(
    ("projection_kind", "content", "constraint_name"),
    [
        pytest.param(
            "body", None, "ck_context_fragment_projection_payload", id="body-null"
        ),
        pytest.param(
            "body", "   ", "ck_context_fragment_projection_payload", id="body-blank"
        ),
        pytest.param(
            "fields",
            "secret",
            "ck_context_fragment_projection_payload",
            id="fields-with-body",
        ),
        pytest.param(
            "other", None, "ck_context_fragment_projection_kind", id="unknown-kind"
        ),
    ],
)
def test_fragment_projection_mode_has_an_exact_payload_shape(
    migration_configuration: DatabaseConfiguration,
    field_projection_fixture: FieldProjectionFixture,
    projection_kind: str,
    content: str | None,
    constraint_name: str,
) -> None:
    engine = create_database_engine(migration_configuration)
    try:
        with pytest.raises(IntegrityError) as error, engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO context_fragment (
                        organization_id, resource_ref, revision_id,
                        fragment_ref, ordinal, projection_kind, content
                    ) VALUES (
                        :organization_id, :resource_ref, :revision_id,
                        :fragment_ref, 999, :projection_kind, :content
                    )
                    """
                ),
                {
                    **asdict(field_projection_fixture),
                    "fragment_ref": f"fragment:{uuid4()}",
                    "projection_kind": projection_kind,
                    "content": content,
                },
            )
        assert (
            getattr(
                getattr(error.value.orig, "diag", None),
                "constraint_name",
                None,
            )
            == constraint_name
        )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "table_name", ["context_fragment_field", "membership_resource_field_right"]
)
@pytest.mark.parametrize(
    "field_ref",
    ["", " ", "PrivateNote", "private-note", "private.note", "私密", "a" * 65],
)
def test_field_refs_are_closed_bounded_ascii_identifiers(
    migration_configuration: DatabaseConfiguration,
    field_projection_fixture: FieldProjectionFixture,
    table_name: str,
    field_ref: str,
) -> None:
    parameters = {**asdict(field_projection_fixture), "field_ref": field_ref}
    if table_name == "context_fragment_field":
        statement = """
            INSERT INTO context_fragment_field (
                organization_id, resource_ref, revision_id, fragment_ref,
                field_ref, ordinal, field_value
            ) VALUES (
                :organization_id, :resource_ref, :revision_id,
                :fields_fragment_ref, :field_ref, 63, 'value'
            )
        """
    else:
        statement = """
            INSERT INTO membership_resource_field_right (
                organization_id, membership_id, membership_version,
                resource_ref, field_ref
            ) VALUES (
                :organization_id, :full_membership_id, 7,
                :resource_ref, :field_ref
            )
        """
    engine = create_database_engine(migration_configuration)
    try:
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text(statement), parameters)
    finally:
        engine.dispose()


def test_body_field_ref_is_reserved_for_legacy_projection_only(
    migration_configuration: DatabaseConfiguration,
    field_projection_fixture: FieldProjectionFixture,
) -> None:
    engine = create_database_engine(migration_configuration)
    try:
        with pytest.raises(IntegrityError) as error, engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO context_fragment_field (
                        organization_id, resource_ref, revision_id, fragment_ref,
                        field_ref, ordinal, field_value
                    ) VALUES (
                        :organization_id, :resource_ref, :revision_id,
                        :fields_fragment_ref, 'body', 2, 'not-a-legacy-body'
                    )
                    """
                ),
                asdict(field_projection_fixture),
            )
        assert (
            getattr(
                getattr(error.value.orig, "diag", None),
                "constraint_name",
                None,
            )
            == "ck_context_fragment_field_ref"
        )
    finally:
        engine.dispose()


def test_field_refs_accept_the_closed_64_ascii_character_ceiling(
    migration_configuration: DatabaseConfiguration,
    field_projection_fixture: FieldProjectionFixture,
) -> None:
    engine = create_database_engine(migration_configuration)
    connection = engine.connect()
    transaction = connection.begin()
    try:
        field_ref = "a" * 64
        connection.execute(
            text(
                """
                INSERT INTO context_fragment_field (
                    organization_id, resource_ref, revision_id, fragment_ref,
                    field_ref, ordinal, field_value
                ) VALUES (
                    :organization_id, :resource_ref, :revision_id,
                        :fields_fragment_ref, :field_ref, 63, 'value'
                )
                """
            ),
            {**asdict(field_projection_fixture), "field_ref": field_ref},
        )
        connection.execute(
            text(
                """
                INSERT INTO membership_resource_field_right (
                    organization_id, membership_id, membership_version,
                    resource_ref, field_ref
                ) VALUES (
                    :organization_id, :full_membership_id, 7,
                    :resource_ref, :field_ref
                )
                """
            ),
            {**asdict(field_projection_fixture), "field_ref": field_ref},
        )
    finally:
        transaction.rollback()
        connection.close()
        engine.dispose()


def test_context_fragment_field_rejects_an_ordinal_outside_public_field_bound(
    migration_configuration: DatabaseConfiguration,
    field_projection_fixture: FieldProjectionFixture,
) -> None:
    engine = create_database_engine(migration_configuration)
    try:
        with pytest.raises(IntegrityError) as error, engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO context_fragment_field (
                        organization_id, resource_ref, revision_id, fragment_ref,
                        field_ref, ordinal, field_value
                    ) VALUES (
                        :organization_id, :resource_ref, :revision_id,
                        :fields_fragment_ref, 'field_64', 64, 'value'
                    )
                    """
                ),
                asdict(field_projection_fixture),
            )
        assert (
            getattr(
                getattr(error.value.orig, "diag", None),
                "constraint_name",
                None,
            )
            == "ck_context_fragment_field_ordinal_bounded"
        )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "field_value",
    [
        pytest.param("", id="empty"),
        pytest.param("   ", id="spaces"),
        pytest.param("\t\n\r", id="control-whitespace"),
        pytest.param("\x1c\x1d\x1e\x1f", id="python-control-whitespace"),
        pytest.param("\u0085", id="next-line"),
        pytest.param("\u00a0", id="no-break-space"),
        pytest.param("\u1680", id="ogham-space-mark"),
        pytest.param(
            "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a",
            id="unicode-quad-and-width-spaces",
        ),
        pytest.param("\u2028\u2029", id="unicode-line-separators"),
        pytest.param("\u202f\u205f\u3000", id="remaining-unicode-spaces"),
    ],
)
def test_context_fragment_field_rejects_blank_values(
    migration_configuration: DatabaseConfiguration,
    field_projection_fixture: FieldProjectionFixture,
    field_value: str,
) -> None:
    engine = create_database_engine(migration_configuration)
    try:
        with pytest.raises(IntegrityError) as error, engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO context_fragment_field (
                        organization_id, resource_ref, revision_id, fragment_ref,
                        field_ref, ordinal, field_value
                    ) VALUES (
                        :organization_id, :resource_ref, :revision_id,
                        :fields_fragment_ref, 'blank_probe', 63, :field_value
                    )
                    """
                ),
                {**asdict(field_projection_fixture), "field_value": field_value},
            )
        assert (
            getattr(
                getattr(error.value.orig, "diag", None),
                "constraint_name",
                None,
            )
            == "ck_context_fragment_field_value_nonblank"
        )
    finally:
        engine.dispose()


def test_field_row_requires_a_fields_parent_and_exact_same_org_parents(
    migration_configuration: DatabaseConfiguration,
    field_projection_fixture: FieldProjectionFixture,
) -> None:
    engine = create_database_engine(migration_configuration)
    try:
        with pytest.raises(DBAPIError) as wrong_mode, engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO context_fragment_field (
                        organization_id, resource_ref, revision_id, fragment_ref,
                        field_ref, ordinal, field_value
                    ) VALUES (
                        :organization_id, :resource_ref, :revision_id,
                        :body_fragment_ref, 'status', 999, 'open'
                    )
                    """
                ),
                asdict(field_projection_fixture),
            )
        assert getattr(wrong_mode.value.orig, "sqlstate", None) == "23514"

        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO membership_resource_field_right (
                        organization_id, membership_id, membership_version,
                        resource_ref, field_ref
                    ) VALUES (
                        :organization_id, :full_membership_id, 8,
                        :resource_ref, 'status'
                    )
                    """
                ),
                asdict(field_projection_fixture),
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_fragment_field_values_are_immutable(
    migration_configuration: DatabaseConfiguration,
    field_projection_fixture: FieldProjectionFixture,
    operation: str,
) -> None:
    statement = (
        "UPDATE context_fragment_field SET field_value = 'changed' "
        "WHERE organization_id = :organization_id AND field_ref = 'status'"
        if operation == "UPDATE"
        else "DELETE FROM context_fragment_field "
        "WHERE organization_id = :organization_id AND field_ref = 'status'"
    )
    engine = create_database_engine(migration_configuration)
    try:
        with pytest.raises(DBAPIError) as error, engine.begin() as connection:
            connection.execute(text(statement), asdict(field_projection_fixture))
        assert getattr(error.value.orig, "sqlstate", None) == "55000"
    finally:
        engine.dispose()


def test_field_authority_tables_have_force_rls_and_least_privilege_grants(
    migration_configuration: DatabaseConfiguration,
) -> None:
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            security = {
                row.relname: (
                    row.relrowsecurity,
                    row.relforcerowsecurity,
                    row.owner,
                )
                for row in connection.execute(
                    text(
                        """
                        SELECT relname, relrowsecurity, relforcerowsecurity,
                               pg_get_userbyid(relowner) AS owner
                        FROM pg_class
                        WHERE oid IN (
                            'public.context_fragment_field'::regclass,
                            'public.membership_resource_field_right'::regclass
                        )
                        """
                    )
                )
            }
            grants = {
                (row.grantee, row.table_name, row.privilege_type)
                for row in connection.execute(
                    text(
                        """
                        SELECT grantee, table_name, privilege_type
                        FROM information_schema.table_privileges
                        WHERE table_schema = 'public'
                          AND table_name IN (
                              'context_fragment_field',
                              'membership_resource_field_right'
                          )
                          AND grantee <> :migrator_role
                        """
                    ),
                    {"migrator_role": MIGRATOR_ROLE},
                )
            }
            policies = {
                (row.tablename, row.policyname): (
                    row.cmd,
                    tuple(row.roles),
                    row.qual,
                    row.with_check,
                )
                for row in connection.execute(
                    text(
                        """
                        SELECT tablename, policyname, cmd, roles, qual, with_check
                        FROM pg_policies
                            WHERE schemaname = 'public'
                              AND tablename IN (
                                  'context_fragment',
                                  'context_fragment_field',
                                  'membership_resource_field_right'
                              )
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
                        WHERE conrelid IN (
                            'public.context_fragment_field'::regclass,
                            'public.membership_resource_field_right'::regclass,
                            'public.membership'::regclass
                        )
                        """
                    )
                )
            }
            guard_function = tuple(
                connection.execute(
                    text(
                        """
                        SELECT function_record.prosecdef,
                               function_record.proconfig,
                               language_record.lanname,
                               pg_get_userbyid(function_record.proowner)
                        FROM pg_proc AS function_record
                        JOIN pg_namespace AS namespace
                          ON namespace.oid = function_record.pronamespace
                        JOIN pg_language AS language_record
                          ON language_record.oid = function_record.prolang
                        WHERE namespace.nspname = 'public'
                          AND function_record.proname =
                              'context_fragment_field_require_fields_parent'
                        """
                    )
                ).one()
            )
            guard_function_grants = {
                (row.grantee, row.privilege_type)
                for row in connection.execute(
                    text(
                        """
                        SELECT grantee, privilege_type
                        FROM information_schema.routine_privileges
                        WHERE routine_schema = 'public'
                          AND routine_name =
                              'context_fragment_field_require_fields_parent'
                        """
                    )
                )
            }
            mutation_lock_function = tuple(
                connection.execute(
                    text(
                        """
                        SELECT function_record.prosecdef,
                               function_record.proconfig,
                               language_record.lanname,
                               pg_get_userbyid(function_record.proowner)
                        FROM pg_proc AS function_record
                        JOIN pg_namespace AS namespace
                          ON namespace.oid = function_record.pronamespace
                        JOIN pg_language AS language_record
                          ON language_record.oid = function_record.prolang
                        WHERE namespace.nspname = 'public'
                          AND function_record.proname =
                              'membership_resource_field_right_lock_mutation'
                        """
                    )
                ).one()
            )
            mutation_lock_function_grants = {
                (row.grantee, row.privilege_type)
                for row in connection.execute(
                    text(
                        """
                        SELECT grantee, privilege_type
                        FROM information_schema.routine_privileges
                        WHERE routine_schema = 'public'
                          AND routine_name =
                              'membership_resource_field_right_lock_mutation'
                        """
                    )
                )
            }
            field_triggers = {
                row.tgname: (row.tgenabled, row.tgisinternal)
                for row in connection.execute(
                    text(
                        """
                        SELECT trigger_record.tgname,
                               trigger_record.tgenabled,
                               trigger_record.tgisinternal
                        FROM pg_trigger AS trigger_record
                        WHERE trigger_record.tgrelid =
                              'public.context_fragment_field'::regclass
                          AND NOT trigger_record.tgisinternal
                        """
                    )
                )
            }
            right_triggers = {
                row.tgname: (row.tgenabled, row.tgisinternal)
                for row in connection.execute(
                    text(
                        """
                        SELECT trigger_record.tgname,
                               trigger_record.tgenabled,
                               trigger_record.tgisinternal
                        FROM pg_trigger AS trigger_record
                        WHERE trigger_record.tgrelid =
                              'public.membership_resource_field_right'::regclass
                          AND NOT trigger_record.tgisinternal
                        """
                    )
                )
            }
    finally:
        engine.dispose()

    assert security == {
        "context_fragment_field": (True, True, MIGRATOR_ROLE),
        "membership_resource_field_right": (True, True, MIGRATOR_ROLE),
    }
    assert grants == {
        (RUNTIME_ROLE, "context_fragment_field", "SELECT"),
        (RUNTIME_ROLE, "membership_resource_field_right", "SELECT"),
    }
    assert {
        "pk_context_fragment_field",
        "uq_context_fragment_field_parent_ordinal",
        "fk_context_fragment_field_parent_same_organization",
        "ck_context_fragment_field_ordinal_bounded",
        "ck_context_fragment_field_ref",
        "ck_context_fragment_field_value_nonblank",
        "pk_membership_resource_field_right",
        "fk_membership_field_right_membership_version",
        "fk_membership_field_right_resource_same_organization",
        "ck_membership_resource_field_right_version_positive",
        "ck_membership_resource_field_right_field_ref",
        "uq_membership_organization_id_version",
    } <= constraints.keys()
    assert "{0,63}" in constraints["ck_context_fragment_field_ref"]
    assert "<> 'body'" in constraints["ck_context_fragment_field_ref"]
    assert (
        ">= 0" in constraints["ck_context_fragment_field_ordinal_bounded"]
        and "<= 63" in constraints["ck_context_fragment_field_ordinal_bounded"]
    )
    assert (
        "translate(field_value"
        in constraints["ck_context_fragment_field_value_nonblank"]
    )
    assert "{0,63}" in constraints["ck_membership_resource_field_right_field_ref"]
    assert guard_function == (
        False,
        ["search_path=pg_catalog"],
        "plpgsql",
        MIGRATOR_ROLE,
    )
    assert guard_function_grants == {(MIGRATOR_ROLE, "EXECUTE")}
    assert mutation_lock_function == (
        False,
        ["search_path=pg_catalog"],
        "plpgsql",
        MIGRATOR_ROLE,
    )
    assert mutation_lock_function_grants == {(MIGRATOR_ROLE, "EXECUTE")}
    assert field_triggers == {
        "context_fragment_field_fields_parent_guard": ("O", False),
        "context_fragment_field_reject_mutation": ("O", False),
    }
    assert right_triggers == {
        "membership_resource_field_right_mutation_lock": ("O", False)
    }
    for table_name in security:
        runtime = policies[(table_name, f"{table_name}_current_user_actor")]
        assert runtime[0:2] == ("SELECT", (RUNTIME_ROLE,))
        normalized = str(runtime[2]).lower()
        for required in (
            "app.organization_id",
            "app.user_id",
            "app.membership_id",
            "app.membership_version",
            "app.checked_at",
        ):
            assert required in normalized
        migrator = policies[(table_name, f"{table_name}_migrator_administration")]
        assert migrator[0:2] == ("ALL", (MIGRATOR_ROLE,))
        assert str(migrator[2]).lower() == "true"
        assert str(migrator[3]).lower() == "true"
    assert (
        "membership_resource_field_right"
        in str(
            policies[
                ("context_fragment_field", "context_fragment_field_current_user_actor")
            ][2]
        ).lower()
    )
    field_policy = str(
        policies[
            ("context_fragment_field", "context_fragment_field_current_user_actor")
        ][2]
    ).lower()
    assert "resource_access_policy" in field_policy
    assert "current_access.principal_ref" in field_policy
    assert "current_access.access_state = 'allowed'" in field_policy
    fragment_policy = str(
        policies[("context_fragment", "context_fragment_current_user_actor")][2]
    ).lower()
    assert "resource_access_policy" in fragment_policy
    assert "current_access.access_state = 'allowed'" in fragment_policy
    right_policy = str(
        policies[
            (
                "membership_resource_field_right",
                "membership_resource_field_right_current_user_actor",
            )
        ][2]
    ).lower()
    assert "context_resource" in right_policy
    assert "tombstoned is false" in right_policy
    assert "resource_access_policy" in right_policy
    assert "current_access.access_state = 'allowed'" in right_policy
    assert WORKER_ROLE not in {grant[0] for grant in grants}


def test_context_run_accepts_historical_and_structured_package_digest_profiles(
    migration_configuration: DatabaseConfiguration,
) -> None:
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            definition = connection.execute(
                text(
                    """
                    SELECT pg_get_constraintdef(oid, true)
                    FROM pg_constraint
                    WHERE conrelid = 'public.context_run'::regclass
                      AND conname = 'ck_context_run_package_digest_profile'
                    """
                )
            ).scalar_one()
    finally:
        engine.dispose()
    normalized = str(definition).lower()
    assert "context-package-canonical-json-v1" in normalized
    assert "context-package-canonical-json-v2" in normalized
