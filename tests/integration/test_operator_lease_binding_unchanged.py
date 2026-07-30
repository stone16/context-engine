from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text

from adapters.embeddings import DeterministicEmbeddingTwin
from engine.control import FileImportReceiver, FileRootRef
from engine.persistence import (
    DatabaseConfiguration,
    FileDispatchLease,
    FileImportLeaseRedemption,
    PostgreSQLFileDispatchAuthority,
    PostgreSQLFileImportWorker,
    create_database_engine,
)
from engine.supply import (
    MarkdownCompilerConfig,
    WorkerLeaseCodec,
    WorkerLeaseKeyring,
    WorkNotAvailable,
)
from tests.integration.test_file_scan_operator_process import (
    _register_activated_source,
    _scan,
)
from tests.support.worker_batch_progress import file_root_registry

pytestmark = pytest.mark.integration
pytest_plugins = ("tests.integration.test_file_scan_operator_process",)
WORKER_KEY = bytes.fromhex("ab" * 32)


def test_batch_convenience_preserves_exact_wrong_job_and_expiry_rejection(
    migration_configuration: DatabaseConfiguration,
    guarded_scheduler_engine: Engine,
    guarded_worker_engine: Engine,
    file_scan_scenario: tuple[UUID, UUID, UUID, Path, dict[str, str]],
) -> None:
    organization_id, _membership_id, _receiver_id, root, environment = (
        file_scan_scenario
    )
    (root / "lease.md").write_text(
        "# Exact lease\n\nConvenience cannot widen this binding.\n",
        encoding="utf-8",
    )
    source_ref = _register_activated_source(organization_id, environment)
    _scan(organization_id, source_ref, environment)
    codec = WorkerLeaseCodec(
        WorkerLeaseKeyring(active_version=1, keys={1: WORKER_KEY})
    )
    authority = PostgreSQLFileDispatchAuthority(
        guarded_scheduler_engine,
        codec,
        configured_root_refs=("operator-scan-root",),
    )
    claim = authority.claim()
    assert type(claim) is FileDispatchLease
    assert claim.organization_id == organization_id
    assert claim.source_ref.value == source_ref
    roots = file_root_registry(FileRootRef("operator-scan-root"), root)
    try:
        wrong_job_worker = PostgreSQLFileImportWorker(
            guarded_worker_engine,
            codec,
            FileImportReceiver(claim.service_principal_id),
            roots,
            MarkdownCompilerConfig("markdown-config-v1"),
            embedding_provider=DeterministicEmbeddingTwin(),
            clock=lambda: claim.issued_at,
        )
        wrong_job = FileImportLeaseRedemption(
            claim.token,
            claim.organization_id,
            uuid4(),
            claim.source_ref,
        )
        with pytest.raises(WorkNotAvailable):
            wrong_job_worker.run(wrong_job)

        expired_worker = PostgreSQLFileImportWorker(
            guarded_worker_engine,
            codec,
            FileImportReceiver(claim.service_principal_id),
            roots,
            MarkdownCompilerConfig("markdown-config-v1"),
            embedding_provider=DeterministicEmbeddingTwin(),
            clock=lambda: claim.expires_at + timedelta(seconds=1),
        )
        with pytest.raises(WorkNotAvailable):
            expired_worker.run(claim.redemption)
    finally:
        roots.close()

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            snapshot = connection.execute(
                text(
                    "SELECT state, effect_count FROM file_import_job "
                    "WHERE organization_id = :organization_id AND job_id = :job_id"
                ),
                {"organization_id": organization_id, "job_id": claim.job_id},
            ).one()
    finally:
        migration_engine.dispose()
    assert tuple(snapshot) == ("leased", 0)
