from __future__ import annotations

import json
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy import Engine, text

from engine.control import FileRootRef
from engine.persistence import DatabaseConfiguration, create_database_engine
from tests.integration.test_file_scan_operator_process import (
    _control,
    _register_activated_source,
)
from tests.support.worker_batch_progress import (
    file_root_registry,
    run_scheduled_file_batch,
)

pytestmark = pytest.mark.integration
pytest_plugins = ("tests.integration.test_file_scan_operator_process",)
WORKER_KEY = bytes.fromhex("ab" * 32)


def _release_counts(
    configuration: DatabaseConfiguration,
    organization_id: UUID,
) -> tuple[int, int, int, int]:
    engine = create_database_engine(configuration)
    try:
        with engine.connect() as connection:
            return cast(
                tuple[int, int, int, int],
                tuple(
                    connection.execute(
                        text(
                            "SELECT "
                            "(SELECT count(*) FROM release_manifest "
                            " WHERE organization_id = :organization_id), "
                            "(SELECT count(*) FROM release_candidate "
                            " WHERE organization_id = :organization_id), "
                            "(SELECT count(*) FROM release_evaluation "
                            " WHERE organization_id = :organization_id), "
                            "(SELECT count(*) FROM active_release_manifest "
                            " WHERE organization_id = :organization_id)"
                        ),
                        {"organization_id": organization_id},
                    ).one()
                ),
            )
    finally:
        engine.dispose()


def test_scan_batch_and_status_never_promote_or_activate_a_release(
    migration_configuration: DatabaseConfiguration,
    guarded_scheduler_engine: Engine,
    guarded_worker_engine: Engine,
    file_scan_scenario: tuple[UUID, UUID, UUID, Path, dict[str, str]],
) -> None:
    organization_id, _membership_id, _receiver_id, root, environment = (
        file_scan_scenario
    )
    (root / "explicit.md").write_text(
        "# Explicit\n\nRelease promotion remains a separate command.\n",
        encoding="utf-8",
    )
    _register_activated_source(organization_id, environment)
    before = _release_counts(migration_configuration, organization_id)

    scan = _control(
        ["scan-all", "--organization-id", str(organization_id)],
        environment=environment,
    )
    assert cast(dict[str, object], json.loads(scan.stdout))["summary"]
    roots = file_root_registry(FileRootRef("operator-scan-root"), root)
    try:
        run_scheduled_file_batch(
            scheduler_engine=guarded_scheduler_engine,
            worker_engine=guarded_worker_engine,
            root_ref=FileRootRef("operator-scan-root"),
            root_registry=roots,
            signing_key=WORKER_KEY,
            progress_interval_seconds=0.05,
        )
    finally:
        roots.close()
    status = _control(
        ["status", "--organization-id", str(organization_id)],
        environment=environment,
    )
    assert cast(dict[str, object], json.loads(status.stdout))["summary"]

    after = _release_counts(migration_configuration, organization_id)
    assert before == after == (0, 0, 0, 0)
