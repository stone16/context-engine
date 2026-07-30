from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from applications.worker import dispatch_one_file_import
from applications.worker_progress import (
    FileBatchProgressReporter,
    FileDispatchCycleResult,
    FileDispatchFailureCategory,
)
from engine.control import SourceRef
from engine.persistence import (
    FileDispatchLease,
    FileImportRefused,
)
from engine.supply import WorkerLeaseToken


def test_credentials_and_trusted_routing_values_never_reach_progress_or_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    credential = "fake-worker-credential-never-render"
    private_path = "/private/vault/board-notes.md"
    organization_id = UUID("da31d6d2-4742-4213-a941-1a15228181d3")
    job_id = UUID("2508974a-714c-4cd7-bff4-f9fe98551b13")
    source_id = UUID("4cce80f8-0ddd-434d-b05d-4e6703c77927")
    service_principal_id = UUID("1121f2ff-eb40-4ea0-ac3e-92b18717ed5e")
    issued_at = datetime(2026, 7, 30, 9, tzinfo=UTC)
    claim = FileDispatchLease(
        token=WorkerLeaseToken(credential),
        organization_id=organization_id,
        job_id=job_id,
        source_ref=SourceRef(source_id),
        service_principal_id=service_principal_id,
        lease_generation=1,
        issued_at=issued_at,
        expires_at=issued_at + timedelta(minutes=5),
    )

    class Authority:
        def claim(self) -> FileDispatchLease:
            return claim

    class RefusingWorker:
        def run(self, _redemption: object) -> object:
            raise FileImportRefused(f"{credential}:{private_path}")

    class WorkerFactory:
        def __call__(self, _receiver: object) -> RefusingWorker:
            return RefusingWorker()

    rendered: list[str] = []
    reporter = FileBatchProgressReporter(
        rendered.append,
        batch_ref_factory=lambda: "f" * 64,
    )
    caplog.set_level(logging.DEBUG)
    result = dispatch_one_file_import(
        Authority(),
        WorkerFactory(),  # type: ignore[arg-type]
        active_job_observer=reporter.job_active,
        progress_interval_seconds=0.01,
    )
    reporter.observe_cycle(result)
    reporter.observe_cycle(FileDispatchCycleResult("no_work"))

    assert result.reason_category is FileDispatchFailureCategory.FILE_IMPORT_REFUSED
    output = "\n".join(rendered)
    logs = caplog.text
    for forbidden in (
        credential,
        private_path,
        str(organization_id),
        str(job_id),
        str(source_id),
        str(service_principal_id),
    ):
        assert forbidden not in output
        assert forbidden not in logs
    for line in rendered:
        assert set(json.loads(line)) == {
            "batchRef",
            "currentJobRef",
            "failed",
            "outcome",
            "phase",
            "processed",
            "reasonCategory",
            "schemaVersion",
            "total",
        }
    assert not any(
        thread.name.startswith("file-dispatch-progress")
        for thread in threading.enumerate()
    )
