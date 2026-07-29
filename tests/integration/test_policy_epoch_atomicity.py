from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from engine.persistence import DatabaseConfiguration, create_database_engine
from engine.persistence.access_policy import (
    PostgreSQLAccessPolicyControl,
    ResourceAccessRevocation,
)
from tests.support.article_access_policy import (
    article_policy,
    fixed_policy_epoch,
    policy_epoch,
)
from tests.support.file_imports import (
    FileImportScenario,
    delete_file_import_scenario,
    prepare_file_import_scenario,
    run_file_import,
)

pytestmark = pytest.mark.integration


def _published_file(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> tuple[FileImportScenario, str]:
    scenario = prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
    )
    assert scenario.token is not None
    published = run_file_import(
        scenario,
        scenario.prepared,
        scenario.token,
        guarded_worker_engine,
    )
    return scenario, published.candidate_refs[0].resource_ref


def test_first_ingest_records_current_epoch_without_advancing_it(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario, resource_ref = _published_file(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        guarded_worker_engine,
    )
    engine = create_database_engine(migration_configuration)
    try:
        assert policy_epoch(engine, scenario.organization_id) == 1
        assert fixed_policy_epoch(engine, scenario.organization_id, resource_ref) == 1
    finally:
        engine.dispose()
        delete_file_import_scenario(migration_configuration, scenario.organization_id)


def test_existing_effective_policy_change_and_epoch_commit_atomically(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario, resource_ref = _published_file(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        guarded_worker_engine,
    )
    engine = create_database_engine(migration_configuration)
    try:
        before = article_policy(engine, scenario.organization_id, resource_ref)
        assert before[:2] == ("private", 1)
        result = PostgreSQLAccessPolicyControl(guarded_control_engine).change_access(
            ResourceAccessRevocation(
                organization_id=scenario.organization_id,
                resource_ref=resource_ref,
                principal_ref="principal:file-reader",
                expected_access_version=1,
            )
        )
        assert result.value == 2
        revoked = article_policy(engine, scenario.organization_id, resource_ref)
        assert revoked[0] is None
        assert revoked[1] == 2
        assert policy_epoch(engine, scenario.organization_id) == 2
        assert fixed_policy_epoch(engine, scenario.organization_id, resource_ref) == 2
    finally:
        engine.dispose()
        delete_file_import_scenario(migration_configuration, scenario.organization_id)


def test_policy_and_epoch_rollback_together_after_injected_failure(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario, resource_ref = _published_file(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        guarded_worker_engine,
    )
    engine = create_database_engine(migration_configuration)
    try:
        before_policy = article_policy(engine, scenario.organization_id, resource_ref)
        before_epoch = policy_epoch(engine, scenario.organization_id)
        with (
            pytest.raises(Exception, match="division by zero"),
            guarded_control_engine.begin() as connection,
        ):
            connection.execute(
                text("SELECT set_config('app.organization_id', :org, true)"),
                {"org": str(scenario.organization_id)},
            )
            connection.execute(
                text(
                    "SELECT public.context_control_revoke_resource_access("
                    ":org, :resource, 'principal:file-reader', 1)"
                ),
                {"org": scenario.organization_id, "resource": resource_ref},
            ).scalar_one()
            connection.execute(text("SELECT 1 / 0")).scalar_one()

        assert (
            article_policy(engine, scenario.organization_id, resource_ref)
            == before_policy
        )
        assert policy_epoch(engine, scenario.organization_id) == before_epoch
    finally:
        engine.dispose()
        delete_file_import_scenario(migration_configuration, scenario.organization_id)
