from __future__ import annotations

from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy import text

from applications.file_root_configuration import (
    WORKER_MAX_FILE_CHANGE_BASELINE_SIZE_ENV,
)
from engine.persistence import DatabaseConfiguration, create_database_engine
from tests.integration.test_file_scan_operator_process import (
    _register_activated_source,
    _scan,
    _status,
)
from tests.integration.test_file_scan_operator_process import (
    file_scan_scenario as _file_scan_scenario,
)

file_scan_scenario = _file_scan_scenario

pytestmark = pytest.mark.integration


def test_restart_read_retains_each_scan_bound_as_durable_provenance(
    migration_configuration: DatabaseConfiguration,
    file_scan_scenario: tuple[UUID, UUID, UUID, Path, dict[str, str]],
) -> None:
    organization_id, _membership_id, _receiver_id, root, environment = (
        file_scan_scenario
    )
    (root / "alpha.md").write_text("# Alpha\n", encoding="utf-8")
    source_ref = _register_activated_source(organization_id, environment)

    first = _scan(organization_id, source_ref, environment)
    assert first["scanBound"] == 10_000

    raised = environment | {WORKER_MAX_FILE_CHANGE_BASELINE_SIZE_ENV: "15000"}
    (root / "beta.md").write_text("# Beta\n", encoding="utf-8")
    second = _scan(organization_id, source_ref, raised)
    assert second["scanBound"] == 15_000
    restarted_status = _status(organization_id, source_ref, raised)
    assert restarted_status["completeChangeBaselineScanBound"] == 15_000
    restarted_head = cast(dict[str, object], restarted_status["changeScanHead"])
    assert restarted_head["scanBound"] == 15_000

    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            retained = tuple(
                connection.execute(
                    text(
                        """
                        SELECT scan_bound, count(*)
                        FROM file_source_change_page
                        WHERE organization_id = :organization_id
                          AND source_id = :source_id
                        GROUP BY scan_bound
                        ORDER BY scan_bound
                        """
                    ),
                    {
                        "organization_id": organization_id,
                        "source_id": source_ref,
                    },
                )
            )
    finally:
        engine.dispose()

    assert [tuple(row) for row in retained] == [
        (10_000, 1),
        (15_000, 2),
    ]
