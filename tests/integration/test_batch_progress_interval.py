from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine

from engine.control import FileRootRef
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


def test_progress_is_emitted_during_real_work_at_the_bounded_interval(
    guarded_scheduler_engine: Engine,
    guarded_worker_engine: Engine,
    file_scan_scenario: tuple[UUID, UUID, UUID, Path, dict[str, str]],
) -> None:
    organization_id, _membership_id, _receiver_id, root, environment = (
        file_scan_scenario
    )
    (root / "bounded.md").write_text(
        "# Bounded\n\nProgress must precede completion.\n",
        encoding="utf-8",
    )
    source_ref = _register_activated_source(organization_id, environment)
    assert _scan(organization_id, source_ref, environment)["importsScheduled"] == 1

    roots = file_root_registry(FileRootRef("operator-scan-root"), root)
    try:
        capture = run_scheduled_file_batch(
            scheduler_engine=guarded_scheduler_engine,
            worker_engine=guarded_worker_engine,
            root_ref=FileRootRef("operator-scan-root"),
            root_registry=roots,
            signing_key=WORKER_KEY,
            progress_interval_seconds=0.03,
            job_delay_seconds=0.14,
        )
    finally:
        roots.close()

    active_indices = [
        index
        for index, document in enumerate(capture.documents)
        if document["phase"] == "dispatching" and document["outcome"] is None
    ]
    completed_index = next(
        index
        for index, document in enumerate(capture.documents)
        if document["outcome"] == "dispatched"
    )
    assert len(active_indices) >= 3
    assert max(active_indices) < completed_index
    active_times = [capture.emitted_at[index] for index in active_indices]
    assert len(capture.job_completed_at) == 1
    assert active_times[0] < capture.job_completed_at[0]
    assert all(
        later - earlier <= 0.09
        for earlier, later in zip(active_times, active_times[1:], strict=False)
    )
    assert capture.documents[-1]["phase"] == "complete"
    assert sum(
        document["phase"] == "complete" for document in capture.documents
    ) == 1
