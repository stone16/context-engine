from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine

from engine.persistence import DatabaseConfiguration, create_database_engine
from tests.support.article_access_policy import (
    article_policy,
    fixed_policy_epoch,
    policy_epoch,
)
from tests.support.file_imports import (
    delete_file_import_scenario,
    prepare_file_import_scenario,
    prepare_repeat_file_import,
    run_file_import,
)

pytestmark = pytest.mark.integration


def test_reingestion_revision_replacement_and_default_changes_never_rewrite_policy(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
    )
    assert scenario.token is not None
    first = run_file_import(
        scenario,
        scenario.prepared,
        scenario.token,
        guarded_worker_engine,
    )
    resource_ref = first.candidate_refs[0].resource_ref
    engine = create_database_engine(migration_configuration)
    try:
        fixed = article_policy(engine, scenario.organization_id, resource_ref)
        epoch_before = policy_epoch(engine, scenario.organization_id)
        fixed_epoch = fixed_policy_epoch(engine, scenario.organization_id, resource_ref)

        repeat, repeat_token = prepare_repeat_file_import(
            scenario,
            guarded_control_engine,
            idempotency_key="policy-preserving-reingestion",
        )
        unchanged = run_file_import(
            scenario,
            repeat,
            repeat_token,
            guarded_worker_engine,
        )
        assert unchanged.outcome == "unchanged"

        (scenario.root / "handbook.md").write_bytes(
            b"# Handbook\n\nChanged representation; same Article policy.\n"
        )
        replacement, replacement_token = prepare_repeat_file_import(
            scenario,
            guarded_control_engine,
            idempotency_key="policy-preserving-replacement",
        )
        replaced = run_file_import(
            scenario,
            replacement,
            replacement_token,
            guarded_worker_engine,
        )
        assert replaced.outcome == "replaced"
        assert replaced.candidate_refs[0].resource_ref == resource_ref

        assert fixed == ("private", 1, "source_default")
        assert article_policy(engine, scenario.organization_id, resource_ref) == fixed
        assert (
            fixed_epoch
            == epoch_before
            == policy_epoch(engine, scenario.organization_id)
        )
    finally:
        engine.dispose()
        delete_file_import_scenario(
            migration_configuration,
            scenario.organization_id,
        )
