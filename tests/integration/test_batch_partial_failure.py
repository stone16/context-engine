from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine, text

from engine.control import FileRootRef
from engine.persistence import DatabaseConfiguration, create_database_engine
from tests.integration.test_file_scan_operator_process import (
    _register_activated_source,
    _scan,
)
from tests.support.worker_batch_progress import (
    file_root_registry,
    run_scheduled_file_batch,
)

pytestmark = pytest.mark.integration
pytest_plugins = ("tests.integration.test_file_scan_operator_process",)
WORKER_KEY = bytes.fromhex("ab" * 32)


def test_one_failed_job_is_attributed_and_the_batch_publishes_the_next_job(
    migration_configuration: DatabaseConfiguration,
    guarded_scheduler_engine: Engine,
    guarded_worker_engine: Engine,
    file_scan_scenario: tuple[UUID, UUID, UUID, Path, dict[str, str]],
) -> None:
    organization_id, _membership_id, _receiver_id, root, environment = (
        file_scan_scenario
    )
    (root / "a-refused.md").write_bytes(b"\xff\xfe private invalid bytes")
    (root / "b-published.md").write_text(
        "# Published\n\nThe second job must still publish.\n",
        encoding="utf-8",
    )
    source_ref = _register_activated_source(organization_id, environment)
    assert _scan(organization_id, source_ref, environment)["importsScheduled"] == 2

    roots = file_root_registry(FileRootRef("operator-scan-root"), root)
    try:
        capture = run_scheduled_file_batch(
            scheduler_engine=guarded_scheduler_engine,
            worker_engine=guarded_worker_engine,
            root_ref=FileRootRef("operator-scan-root"),
            root_registry=roots,
            signing_key=WORKER_KEY,
            progress_interval_seconds=0.05,
        )
    finally:
        roots.close()

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            jobs = tuple(
                connection.execute(
                    text(
                        "SELECT job_id, state, effect_count "
                        "FROM file_import_job "
                        "WHERE organization_id = :organization_id "
                        "ORDER BY job_id"
                    ),
                    {"organization_id": organization_id},
                )
            )
    finally:
        migration_engine.dispose()

    assert sorted((row.state, row.effect_count) for row in jobs) == [
        ("completed", 1),
        ("failed", 0),
    ]
    completed = [
        document
        for document in capture.documents
        if document["outcome"] in {"dispatched", "refused"}
    ]
    assert [document["outcome"] for document in completed].count("dispatched") == 1
    assert [document["outcome"] for document in completed].count("refused") == 1
    refusal = next(
        document for document in completed if document["outcome"] == "refused"
    )
    assert refusal["reasonCategory"] == "file_import_refused"
    assert isinstance(refusal["currentJobRef"], str)
    assert len(refusal["currentJobRef"]) == 64
    rendered = str(capture.documents)
    assert all(str(row.job_id) not in rendered for row in jobs)
    assert str(organization_id) not in rendered
    assert str(source_ref) not in rendered
    assert capture.documents[-1] | {"batchRef": "a" * 64} == {
        "batchRef": "a" * 64,
        "currentJobRef": None,
        "failed": 1,
        "outcome": None,
        "phase": "complete",
        "processed": 2,
        "reasonCategory": None,
        "schemaVersion": "context-engine-worker-batch-progress-v1",
        "total": 2,
    }
