from __future__ import annotations

import inspect
import re
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text

from engine.persistence import DatabaseConfiguration, create_database_engine
from tests.support.file_imports import (
    FileImportScenario,
    delete_file_import_scenario,
    prepare_file_import_scenario,
    run_file_import,
)
from tests.support.file_source_progress import clear_file_source_progress_projection

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "table_name",
    (
        "file_source_publish_watermark",
        "file_source_acquisition_checkpoint",
        "file_source_delete_observation_page",
        "file_source_change",
        "file_source_change_page",
    ),
)
def test_progress_cleanup_has_no_unscoped_delete(table_name: str) -> None:
    source = inspect.getsource(clear_file_source_progress_projection)
    assert source.count(f"DELETE FROM {table_name} ") == 1
    assert re.search(
        rf'"DELETE FROM {table_name} "\s*'
        r'"WHERE organization_id = :organization_id"',
        source,
    )


def _projection_counts(
    configuration: DatabaseConfiguration,
    organization_ids: tuple[UUID, ...],
) -> dict[UUID, tuple[int, ...]]:
    engine = create_database_engine(configuration)
    try:
        with engine.connect() as connection:
            return {
                organization_id: tuple(
                    connection.execute(
                        text(
                            f"SELECT count(*) FROM {table_name} "  # noqa: S608
                            "WHERE organization_id = :organization_id"
                        ),
                        {"organization_id": organization_id},
                    ).scalar_one()
                    for table_name in (
                        "file_source_publish_watermark",
                        "file_source_acquisition_checkpoint",
                        "file_source_change_page",
                        "file_source_change",
                        "file_source_delete_observation_page",
                    )
                )
                for organization_id in organization_ids
            }
    finally:
        engine.dispose()


def _seed_change_projection(
    configuration: DatabaseConfiguration,
    scenario: FileImportScenario,
) -> None:
    page_ref = sha256(scenario.organization_id.bytes + b"cleanup-page").hexdigest()
    scan_ref = sha256(scenario.organization_id.bytes + b"cleanup-scan").hexdigest()
    content_digest = sha256(
        scenario.organization_id.bytes + b"cleanup-content"
    ).hexdigest()
    engine = create_database_engine(configuration)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE file_source_change_page DISABLE TRIGGER "
                    "file_source_change_page_capability_binding"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE file_source_change DISABLE TRIGGER "
                    "file_source_change_capability_binding"
                )
            )
            source_version_id = connection.execute(
                text(
                    "SELECT active_version_id FROM context_source "
                    "WHERE organization_id = :organization_id "
                    "AND source_id = :source_id"
                ),
                {
                    "organization_id": scenario.organization_id,
                    "source_id": scenario.source_ref.value,
                },
            ).scalar_one()
            parameters = {
                "organization_id": scenario.organization_id,
                "source_id": scenario.source_ref.value,
                "source_version_id": source_version_id,
                "page_ref": page_ref,
                "scan_ref": scan_ref,
                "scan_epoch": uuid4(),
                "content_digest": content_digest,
            }
            connection.execute(
                text(
                    "INSERT INTO file_source_delete_observation_page ("
                    "organization_id, source_id, source_version_id, page_ref) "
                    "VALUES (:organization_id, :source_id, "
                    ":source_version_id, :page_ref)"
                ),
                parameters,
            )
            connection.execute(
                text(
                    "INSERT INTO file_source_change_page (organization_id, "
                    "source_id, source_version_id, page_ref, scan_ref, "
                    "scan_epoch, page_limit, page_ordinal, change_count, "
                    "complete, accepted_at) VALUES (:organization_id, "
                    ":source_id, :source_version_id, :page_ref, :scan_ref, "
                    ":scan_epoch, 1, 1, 1, true, CURRENT_TIMESTAMP)"
                ),
                parameters,
            )
            connection.execute(
                text(
                    "INSERT INTO file_source_change (organization_id, "
                    "source_id, source_version_id, scan_ref, page_ref, "
                    "change_ordinal, change_kind, relative_path, "
                    "content_sha256, content_length) VALUES ("
                    ":organization_id, :source_id, :source_version_id, "
                    ":scan_ref, :page_ref, 1, 'upsert', 'cleanup.md', "
                    ":content_digest, 1)"
                ),
                parameters,
            )
            connection.execute(
                text(
                    "ALTER TABLE file_source_change ENABLE TRIGGER "
                    "file_source_change_capability_binding"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE file_source_change_page ENABLE TRIGGER "
                    "file_source_change_page_capability_binding"
                )
            )
    finally:
        engine.dispose()


def test_progress_cleanup_never_deletes_another_organizations_rows(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenarios: list[FileImportScenario] = []
    try:
        for label in ("target", "retained"):
            scenario_root = tmp_path / label
            scenario_root.mkdir()
            scenario = prepare_file_import_scenario(
                scenario_root,
                migration_configuration,
                guarded_control_engine,
            )
            scenarios.append(scenario)
            assert scenario.token is not None
            run_file_import(
                scenario,
                scenario.prepared,
                scenario.token,
                guarded_worker_engine,
            )
            _seed_change_projection(migration_configuration, scenario)

        target, retained = (scenario.organization_id for scenario in scenarios)
        organization_ids = (target, retained)
        before = _projection_counts(migration_configuration, organization_ids)

        clear_file_source_progress_projection(migration_configuration, target)

        after = _projection_counts(migration_configuration, organization_ids)

        assert all(count > 0 for count in before[target])
        assert all(count > 0 for count in before[retained])
        assert all(count == 0 for count in after[target])
        assert after[retained] == before[retained]
    finally:
        for scenario in scenarios:
            clear_file_source_progress_projection(
                migration_configuration, scenario.organization_id
            )
            delete_file_import_scenario(
                migration_configuration, scenario.organization_id
            )
