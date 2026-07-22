from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from engine.persistence import DatabaseConfiguration, create_database_engine
from tests.integration.test_context_run_schema import (
    LineageIdentity,
    insert_context_run,
)

pytestmark = pytest.mark.integration
ROOT = Path(__file__).parents[2]
HEAD_TABLES = [
    "active_release_manifest",
    "alembic_version",
    "context_fragment",
    "context_fragment_field",
    "context_resource",
    "context_revision",
    "context_run",
    "context_run_operator_read_ticket",
    "context_source",
    "decision_audit",
    "exact_phrase_candidate",
    "file_acquisition",
    "file_import_job",
    "file_revision_snapshot",
    "membership",
    "membership_resource_field_right",
    "organization",
    "organization_policy_epoch",
    "organization_record",
    "release_candidate",
    "release_evaluation",
    "release_manifest",
    "release_operator_grant",
    "release_promotion_audit",
    "resource_access_policy",
    "revision_publication_event",
    "service_principal",
    "source_version",
    "user_account",
    "worker_noop_job",
]


def _revision_rows(configuration: DatabaseConfiguration) -> list[str]:
    engine = create_database_engine(configuration)
    try:
        with engine.connect() as connection:
            return list(
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalars()
            )
    finally:
        engine.dispose()


def _application_tables(configuration: DatabaseConfiguration) -> list[str]:
    engine = create_database_engine(configuration)
    try:
        with engine.connect() as connection:
            return list(
                connection.execute(
                    text(
                        """
                        SELECT tablename
                        FROM pg_tables
                        WHERE schemaname = 'public'
                        ORDER BY tablename
                        """
                    )
                ).scalars()
            )
    finally:
        engine.dispose()


def test_empty_baseline_remains_a_reversible_historical_revision(
    migration_configuration: DatabaseConfiguration,
) -> None:
    alembic_configuration = Config(ROOT / "alembic.ini")

    try:
        command.downgrade(alembic_configuration, "base")
        assert _revision_rows(migration_configuration) == []
        command.upgrade(alembic_configuration, "20260720_0001")
        assert _revision_rows(migration_configuration) == ["20260720_0001"]
        assert _application_tables(migration_configuration) == ["alembic_version"]
    finally:
        command.upgrade(alembic_configuration, "head")
    assert _revision_rows(migration_configuration) == ["20260723_0012"]


def test_organization_isolation_revision_downgrades_and_reapplies_cleanly(
    migration_configuration: DatabaseConfiguration,
) -> None:
    alembic_configuration = Config(ROOT / "alembic.ini")

    try:
        command.downgrade(alembic_configuration, "20260720_0001")
        assert _revision_rows(migration_configuration) == ["20260720_0001"]
        assert _application_tables(migration_configuration) == ["alembic_version"]
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == ["20260723_0012"]
    assert _application_tables(migration_configuration) == HEAD_TABLES


def test_membership_revision_downgrades_to_issue_8_and_reapplies_cleanly(
    migration_configuration: DatabaseConfiguration,
) -> None:
    alembic_configuration = Config(ROOT / "alembic.ini")

    try:
        command.downgrade(alembic_configuration, "20260720_0002")
        assert _revision_rows(migration_configuration) == ["20260720_0002"]
        assert _application_tables(migration_configuration) == [
            "alembic_version",
            "organization",
            "organization_record",
        ]
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == ["20260723_0012"]


def test_content_schema_revision_downgrades_to_membership_and_reapplies_cleanly(
    migration_configuration: DatabaseConfiguration,
) -> None:
    alembic_configuration = Config(ROOT / "alembic.ini")

    try:
        command.downgrade(alembic_configuration, "20260721_0003")
        assert _revision_rows(migration_configuration) == ["20260721_0003"]
        assert _application_tables(migration_configuration) == [
            "alembic_version",
            "membership",
            "organization",
            "organization_record",
            "user_account",
        ]
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == ["20260723_0012"]
    assert _application_tables(migration_configuration) == HEAD_TABLES


def test_policy_epoch_revision_downgrades_to_content_and_reapplies_cleanly(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """PG-REVOCATION-006: the epoch/access boundary is one reversible revision."""

    alembic_configuration = Config(ROOT / "alembic.ini")

    try:
        command.downgrade(alembic_configuration, "20260721_0004")
        assert _revision_rows(migration_configuration) == ["20260721_0004"]
        assert _application_tables(migration_configuration) == [
            "alembic_version",
            "context_fragment",
            "context_resource",
            "context_revision",
            "membership",
            "organization",
            "organization_record",
            "user_account",
        ]
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == ["20260723_0012"]
    assert _application_tables(migration_configuration) == HEAD_TABLES


def test_worker_lease_revision_downgrades_to_policy_epoch_and_reapplies_cleanly(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Issue #17 worker authority is one reversible schema revision."""

    alembic_configuration = Config(ROOT / "alembic.ini")

    try:
        command.downgrade(alembic_configuration, "20260721_0005")
        assert _revision_rows(migration_configuration) == ["20260721_0005"]
        assert _application_tables(migration_configuration) == [
            "alembic_version",
            "context_fragment",
            "context_resource",
            "context_revision",
            "membership",
            "organization",
            "organization_policy_epoch",
            "organization_record",
            "resource_access_policy",
            "user_account",
        ]
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == ["20260723_0012"]
    assert _application_tables(migration_configuration) == HEAD_TABLES


def test_decision_lineage_revision_downgrades_to_worker_lease_and_reapplies_cleanly(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Issue #19 durable decision lineage is one reversible schema revision."""

    alembic_configuration = Config(ROOT / "alembic.ini")

    try:
        command.downgrade(alembic_configuration, "20260722_0006")
        assert _revision_rows(migration_configuration) == ["20260722_0006"]
        assert "context_run" not in _application_tables(migration_configuration)
        assert "context_run_operator_read_ticket" not in _application_tables(
            migration_configuration
        )
        assert "decision_audit" not in _application_tables(migration_configuration)
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == ["20260723_0012"]
    assert _application_tables(migration_configuration) == HEAD_TABLES


def test_field_projection_revision_downgrades_to_decision_lineage_and_reapplies_cleanly(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Issue #48 supports only the proven empty-content schema rollback."""

    alembic_configuration = Config(ROOT / "alembic.ini")

    try:
        command.downgrade(alembic_configuration, "20260722_0007")
        assert _revision_rows(migration_configuration) == ["20260722_0007"]
        tables = _application_tables(migration_configuration)
        assert "context_fragment_field" not in tables
        assert "membership_resource_field_right" not in tables
        engine = create_database_engine(migration_configuration)
        try:
            with engine.connect() as connection:
                fragment_columns = set(
                    connection.execute(
                        text(
                            """
                            SELECT column_name
                            FROM information_schema.columns
                            WHERE table_schema = 'public'
                              AND table_name = 'context_fragment'
                            """
                        )
                    ).scalars()
                )
            assert "projection_kind" not in fragment_columns
        finally:
            engine.dispose()
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == ["20260723_0012"]
    assert "context_fragment_field" in _application_tables(migration_configuration)
    assert "membership_resource_field_right" in _application_tables(
        migration_configuration
    )


def test_file_source_revision_downgrades_to_learning_release_and_reapplies_cleanly(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Issue #21 source registration is one reversible schema revision."""

    alembic_configuration = Config(ROOT / "alembic.ini")

    try:
        command.downgrade(alembic_configuration, "20260722_0009")
        assert _revision_rows(migration_configuration) == ["20260722_0009"]
        tables = _application_tables(migration_configuration)
        assert "context_source" not in tables
        assert "source_version" not in tables
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == ["20260723_0012"]
    tables = _application_tables(migration_configuration)
    assert "context_source" in tables
    assert "source_version" in tables


def test_structural_markdown_revision_downgrades_and_reapplies_cleanly(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Issue #24 owns one explicit, reversible compiler-v2 schema boundary."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    engine = create_database_engine(migration_configuration)
    try:
        command.downgrade(alembic_configuration, "20260722_0011")
        assert _revision_rows(migration_configuration) == ["20260722_0011"]
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'file_revision_snapshot'
                      AND column_name = 'compilation_document'
                    """
                )
            ).scalar_one() == 0
            assert connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM pg_proc AS procedure
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = procedure.pronamespace
                    WHERE namespace.nspname = 'public'
                      AND procedure.proname =
                          'context_worker_publish_structural_file_import'
                    """
                )
            ).scalar_one() == 0
    finally:
        command.upgrade(alembic_configuration, "head")
        engine.dispose()

    assert _revision_rows(migration_configuration) == ["20260723_0012"]
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'file_revision_snapshot'
                      AND column_name = 'compilation_document'
                    """
                )
            ).scalar_one() == 1
            assert connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM pg_proc AS procedure
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = procedure.pronamespace
                    WHERE namespace.nspname = 'public'
                      AND procedure.proname =
                          'context_worker_publish_structural_file_import'
                    """
                )
            ).scalar_one() == 1
    finally:
        engine.dispose()


def test_empty_content_downgrade_preserves_v2_context_run_history(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Schema rollback does not invalidate already-retained v2 lineage."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    identity = LineageIdentity(
        organization_id=uuid4(),
        user_id=uuid4(),
        membership_id=uuid4(),
        run_ref="run_" + "9" * 32,
        decision_ref="dec_" + "a" * 32,
    )
    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO organization (organization_id) "
                    "VALUES (:organization_id)"
                ),
                {"organization_id": identity.organization_id},
            )
            connection.execute(
                text("INSERT INTO user_account (user_id) VALUES (:user_id)"),
                {"user_id": identity.user_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO membership (
                        organization_id, membership_id, user_id, status,
                        membership_version, valid_from, valid_until
                    ) VALUES (
                        :organization_id, :membership_id, :user_id, 'active',
                        1, statement_timestamp() - interval '1 day', NULL
                    )
                    """
                ),
                {
                    "organization_id": identity.organization_id,
                    "membership_id": identity.membership_id,
                    "user_id": identity.user_id,
                },
            )
            insert_context_run(connection, identity)
            connection.execute(
                text(
                    """
                    UPDATE context_run
                    SET package_digest_profile =
                        'context-package-canonical-json-v2'
                    WHERE organization_id = :organization_id
                      AND run_ref = :run_ref
                    """
                ),
                {
                    "organization_id": identity.organization_id,
                    "run_ref": identity.run_ref,
                },
            )

        command.downgrade(alembic_configuration, "20260722_0007")
        assert _revision_rows(migration_configuration) == ["20260722_0007"]
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT package_digest_profile
                    FROM context_run
                    WHERE organization_id = :organization_id
                      AND run_ref = :run_ref
                    """
                ),
                {
                    "organization_id": identity.organization_id,
                    "run_ref": identity.run_ref,
                },
            ).scalar_one() == "context-package-canonical-json-v2"
            profile_constraint = connection.execute(
                text(
                    """
                    SELECT pg_get_constraintdef(oid, true)
                    FROM pg_constraint
                    WHERE conrelid = 'public.context_run'::regclass
                      AND conname = 'ck_context_run_package_digest_profile'
                    """
                )
            ).scalar_one()
        assert "context-package-canonical-json-v1" in profile_constraint
        assert "context-package-canonical-json-v2" in profile_constraint
    finally:
        command.upgrade(alembic_configuration, "head")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM context_run "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": identity.organization_id},
            )
            connection.execute(
                text(
                    "DELETE FROM membership "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": identity.organization_id},
            )
            connection.execute(
                text("DELETE FROM user_account WHERE user_id = :user_id"),
                {"user_id": identity.user_id},
            )
            connection.execute(
                text(
                    "DELETE FROM organization "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": identity.organization_id},
            )
        engine.dispose()


def test_field_projection_downgrade_refuses_populated_content_atomically(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Stored Fragments retain Issue #48's explicit-right default denial."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    organization_id = uuid4()
    user_id = uuid4()
    membership_id = uuid4()
    revision_id = uuid4()
    resource_ref = f"resource:downgrade:{uuid4()}"
    fragment_ref = f"fragment:downgrade:{uuid4()}"
    engine = create_database_engine(migration_configuration)
    parameters = {
        "organization_id": organization_id,
        "user_id": user_id,
        "membership_id": membership_id,
        "revision_id": revision_id,
        "resource_ref": resource_ref,
        "fragment_ref": fragment_ref,
    }
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
                text("INSERT INTO user_account (user_id) VALUES (:user_id)"),
                parameters,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO membership (
                        organization_id, membership_id, user_id, status,
                        membership_version, valid_from, valid_until
                    ) VALUES (
                        :organization_id, :membership_id, :user_id, 'active',
                        1, statement_timestamp(), NULL
                    )
                    """
                ),
                parameters,
            )
            connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            connection.execute(
                text(
                    """
                    INSERT INTO context_resource (
                        organization_id, resource_ref, source_ref,
                        active_revision_id, tombstoned
                    ) VALUES (
                        :organization_id, :resource_ref, 'source:downgrade',
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
                    ) VALUES (
                        :organization_id, :resource_ref, :revision_id,
                        :fragment_ref, 0, 'body', 'private-body'
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
                    ) VALUES (
                        :organization_id, :membership_id, 1,
                        :resource_ref, 'body'
                    )
                    """
                ),
                parameters,
            )

        with pytest.raises(
            RuntimeError,
            match="downgrade requires an empty content schema",
        ):
            command.downgrade(alembic_configuration, "20260722_0007")

        assert _revision_rows(migration_configuration) == ["20260723_0012"]
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT content FROM context_fragment "
                    "WHERE organization_id = :organization_id"
                ),
                parameters,
            ).scalar_one() == "private-body"
            assert connection.execute(
                text(
                    "SELECT field_ref FROM membership_resource_field_right "
                    "WHERE organization_id = :organization_id"
                ),
                parameters,
            ).scalar_one() == "body"
            policy = connection.execute(
                text(
                    """
                    SELECT qual
                    FROM pg_policies
                    WHERE schemaname = 'public'
                      AND tablename = 'context_fragment'
                      AND policyname = 'context_fragment_current_user_actor'
                    """
                )
            ).scalar_one()
        assert "resource_access_policy" in str(policy)
        assert "membership_resource_field_right" in str(policy)
    finally:
        try:
            with engine.begin() as connection:
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
            with engine.begin() as connection:
                for statement in (
                    "DELETE FROM membership_resource_field_right "
                    "WHERE organization_id = :organization_id",
                    "DELETE FROM context_fragment "
                    "WHERE organization_id = :organization_id",
                    "DELETE FROM context_revision "
                    "WHERE organization_id = :organization_id",
                    "DELETE FROM context_resource "
                    "WHERE organization_id = :organization_id",
                    "DELETE FROM membership "
                    "WHERE organization_id = :organization_id",
                    "DELETE FROM user_account WHERE user_id = :user_id",
                    "DELETE FROM organization "
                    "WHERE organization_id = :organization_id",
                ):
                    connection.execute(text(statement), parameters)
        except SQLAlchemyError:
            if _revision_rows(migration_configuration) != ["20260723_0012"]:
                command.upgrade(alembic_configuration, "head")
            raise
        finally:
            with engine.begin() as connection:
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
            engine.dispose()


def test_field_projection_downgrade_serializes_with_concurrent_fragment_insert(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """The empty-schema decision cannot race an in-flight publisher commit."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    organization_id = uuid4()
    revision_id = uuid4()
    resource_ref = f"resource:downgrade-race:{uuid4()}"
    fragment_ref = f"fragment:downgrade-race:{uuid4()}"
    parameters = {
        "organization_id": organization_id,
        "revision_id": revision_id,
        "resource_ref": resource_ref,
        "fragment_ref": fragment_ref,
    }
    # Exercise the Issue #48 downgrade directly. Later reversible revisions
    # have their own lock graphs and must not obscure the lock being observed.
    command.downgrade(alembic_configuration, "20260722_0008")
    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO organization (organization_id) "
                    "VALUES (:organization_id)"
                ),
                parameters,
            )
            connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            connection.execute(
                text(
                    """
                    INSERT INTO context_resource (
                        organization_id, resource_ref, source_ref,
                        active_revision_id, tombstoned
                    ) VALUES (
                        :organization_id, :resource_ref,
                        'source:downgrade-race', :revision_id, false
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

        with engine.connect() as publisher:
            publisher_transaction = publisher.begin()
            try:
                publisher.execute(
                    text(
                        """
                        INSERT INTO context_fragment (
                            organization_id, resource_ref, revision_id,
                            fragment_ref, ordinal, projection_kind, content
                        ) VALUES (
                            :organization_id, :resource_ref, :revision_id,
                            :fragment_ref, 0, 'body', 'concurrent-private-body'
                        )
                        """
                    ),
                    parameters,
                )
                with ThreadPoolExecutor(max_workers=1) as executor:
                    pending_downgrade = executor.submit(
                        command.downgrade,
                        alembic_configuration,
                        "20260722_0007",
                    )
                    downgrade_waiting = False
                    try:
                        with engine.connect() as observer:
                            deadline = monotonic() + 10
                            while monotonic() < deadline:
                                downgrade_waiting = observer.execute(
                                    text(
                                        """
                                        SELECT EXISTS (
                                            SELECT 1
                                            FROM pg_locks
                                            WHERE database = (
                                                SELECT oid
                                                FROM pg_database
                                                WHERE datname = current_database()
                                            )
                                              AND relation = (
                                                  'public.context_fragment'::regclass
                                              )
                                              AND mode = 'AccessExclusiveLock'
                                              AND granted IS FALSE
                                        )
                                        """
                                    )
                                ).scalar_one()
                                if downgrade_waiting:
                                    break
                                sleep(0.01)
                    finally:
                        if publisher_transaction.is_active:
                            publisher_transaction.commit()
                    assert downgrade_waiting
                    with pytest.raises(
                        RuntimeError,
                        match="downgrade requires an empty content schema",
                    ):
                        pending_downgrade.result(timeout=10)
            finally:
                if publisher_transaction.is_active:
                    publisher_transaction.rollback()

        assert _revision_rows(migration_configuration) == ["20260722_0008"]
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT content FROM context_fragment "
                    "WHERE organization_id = :organization_id"
                ),
                parameters,
            ).scalar_one() == "concurrent-private-body"
    finally:
        if _revision_rows(migration_configuration) != ["20260723_0012"]:
            command.upgrade(alembic_configuration, "head")
        with engine.begin() as connection:
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
            with engine.begin() as connection:
                for statement in (
                    "DELETE FROM context_fragment "
                    "WHERE organization_id = :organization_id",
                    "DELETE FROM context_revision "
                    "WHERE organization_id = :organization_id",
                    "DELETE FROM context_resource "
                    "WHERE organization_id = :organization_id",
                    "DELETE FROM organization "
                    "WHERE organization_id = :organization_id",
                ):
                    connection.execute(text(statement), parameters)
        finally:
            with engine.begin() as connection:
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
            engine.dispose()
