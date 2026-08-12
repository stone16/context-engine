from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import Engine, text

from applications import preflight
from engine.embedding_profiles import QWEN3_EMBEDDING_PROFILE
from engine.persistence import DatabaseConfiguration, create_database_engine
from engine.release_profiles import (
    QWEN_VECTOR_INDEX_PROFILE_DIGEST_V1,
    QWEN_VECTOR_INDEX_PROFILE_REF_V1,
    RUNTIME_PROFILE_REF_V1,
    RUNTIME_TOKENIZER_REF_V1,
)
from tests.support.migrations import isolated_revision_database
from tests.support.releases import ensure_test_runtime_release
from tests.unit.test_preflight import valid_environment

pytestmark = pytest.mark.integration


def _database_environment() -> dict[str, str]:
    environment: dict[str, str] = {}
    for row in (
        Path(".context-engine/database.env").read_text(encoding="utf-8").splitlines()
    ):
        name, value = row.split("=", maxsplit=1)
        environment[name] = value
    return environment


def _runtime_environment(
    runtime_configuration: DatabaseConfiguration,
    *,
    organization_id: object,
    user_id: object,
    membership_id: object,
) -> dict[str, str]:
    environment = valid_environment()
    database = _database_environment()
    environment.update(
        {
            "CONTEXT_ENGINE_RUNTIME_ROLE": runtime_configuration.expected_role,
            "CONTEXT_ENGINE_RUNTIME_DATABASE_URL": database[
                "CONTEXT_ENGINE_RUNTIME_DATABASE_URL"
            ],
            "CONTEXT_ENGINE_DOGFOOD_ORGANIZATION_ID": str(organization_id),
            "CONTEXT_ENGINE_DOGFOOD_USER_ID": str(user_id),
            "CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_ID": str(membership_id),
        }
    )
    return environment


def test_real_postgres_schema_probe_is_read_only_and_at_head(
    migration_configuration: DatabaseConfiguration,
) -> None:
    environment = _database_environment()
    configuration = preflight.load_preflight_configuration(
        environment,
        selected_planes=("migration",),
    )
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            before = connection.execute(
                text("SELECT xmin::text, version_num FROM alembic_version")
            ).one()

        assert preflight.probe_schema_readiness(configuration) == "ready"

        with engine.connect() as connection:
            after = connection.execute(
                text("SELECT xmin::text, version_num FROM alembic_version")
            ).one()
        assert after == before
    finally:
        engine.dispose()


def test_real_postgres_schema_probe_classifies_behind_and_unavailable() -> None:
    with isolated_revision_database("20260803_0055") as configurations:
        environment = _database_environment()
        environment.update(
            {
                "CONTEXT_ENGINE_MIGRATOR_ROLE": (
                    configurations.migration.expected_role
                ),
                "CONTEXT_ENGINE_MIGRATION_DATABASE_URL": (
                    configurations.migration.url.render_as_string(hide_password=False)
                ),
            }
        )
        configuration = preflight.load_preflight_configuration(
            environment, selected_planes=("migration",)
        )
        assert preflight.probe_schema_readiness(configuration) == "schema_not_at_head"

    environment["CONTEXT_ENGINE_MIGRATION_DATABASE_URL"] = (
        "postgresql+psycopg://context_engine_migrator:synthetic@127.0.0.1:1/"
        "context_engine"
    )
    unavailable = preflight.load_preflight_configuration(
        environment, selected_planes=("migration",)
    )
    assert preflight.probe_schema_readiness(unavailable) == "schema_unreachable"


def test_real_postgres_release_probe_is_force_rls_read_only_and_non_mutating(
    migration_configuration: DatabaseConfiguration,
    runtime_configuration: DatabaseConfiguration,
    guarded_runtime_engine: Engine,
) -> None:
    organization_id = uuid4()
    user_id = uuid4()
    membership_id = uuid4()
    migration_engine = create_database_engine(migration_configuration)
    checked_at = datetime.now(UTC).replace(microsecond=0)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO organization (organization_id) "
                    "VALUES (:organization_id)"
                ),
                {"organization_id": organization_id},
            )
            connection.execute(
                text("INSERT INTO user_account (user_id) VALUES (:user_id)"),
                {"user_id": user_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO membership (
                        organization_id, membership_id, user_id, status,
                        membership_version, valid_from, valid_until
                    ) VALUES (
                        :organization_id, :membership_id, :user_id,
                        'active', 1, :valid_from, NULL
                    )
                    """
                ),
                {
                    "organization_id": organization_id,
                    "membership_id": membership_id,
                    "user_id": user_id,
                    "valid_from": checked_at,
                },
            )
        ensure_test_runtime_release(
            organization_id,
            index_profile_ref=QWEN_VECTOR_INDEX_PROFILE_REF_V1,
            index_profile_digest=QWEN_VECTOR_INDEX_PROFILE_DIGEST_V1,
            runtime_profile_ref=RUNTIME_PROFILE_REF_V1,
            tokenizer_ref=RUNTIME_TOKENIZER_REF_V1,
            package_schema_ref="context-package-openapi-v1",
            embedding_provider_profile=QWEN3_EMBEDDING_PROFILE,
        )
        environment = _runtime_environment(
            runtime_configuration,
            organization_id=organization_id,
            user_id=user_id,
            membership_id=membership_id,
        )
        configuration = preflight.load_preflight_configuration(
            environment, selected_planes=("runtime",)
        )
        with migration_engine.connect() as connection:
            before = connection.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*) FROM context_run WHERE organization_id=:org),
                      (SELECT count(*) FROM decision_audit WHERE organization_id=:org),
                      (SELECT xmin::text FROM active_release_manifest
                       WHERE organization_id=:org)
                    """
                ),
                {"org": organization_id},
            ).one()

        assert preflight.probe_release_readiness(configuration) == "ready"

        with migration_engine.connect() as connection:
            after = connection.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*) FROM context_run WHERE organization_id=:org),
                      (SELECT count(*) FROM decision_audit WHERE organization_id=:org),
                      (SELECT xmin::text FROM active_release_manifest
                       WHERE organization_id=:org)
                    """
                ),
                {"org": organization_id},
            ).one()
        assert after == before

        absent = _runtime_environment(
            runtime_configuration,
            organization_id=uuid4(),
            user_id=user_id,
            membership_id=membership_id,
        )
        absent_configuration = preflight.load_preflight_configuration(
            absent, selected_planes=("runtime",)
        )
        assert (
            preflight.probe_release_readiness(absent_configuration)
            == "runtime_identity_not_current"
        )
    finally:
        migration_engine.dispose()
