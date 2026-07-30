from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from adapters.embeddings import DeterministicEmbeddingTwin
from applications.file_root_configuration import (
    WORKER_MAX_FILE_CHANGE_BASELINE_SIZE_ENV,
)
from applications.worker import dispatch_one_file_import
from applications.worker_progress import FileDispatchFailureCategory
from engine.control import FileImportReceiver, FileRootRef
from engine.persistence import (
    DatabaseConfiguration,
    FileDispatchLease,
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
from tests.integration.test_file_scan_operator_process import (
    file_scan_scenario as _file_scan_scenario,
)
from tests.support.worker_batch_progress import file_root_registry

file_scan_scenario = _file_scan_scenario
pytestmark = pytest.mark.integration
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
    raised = environment | {WORKER_MAX_FILE_CHANGE_BASELINE_SIZE_ENV: "15000"}
    assert _scan(organization_id, source_ref, raised)["scanBound"] == 15_000
    codec = WorkerLeaseCodec(WorkerLeaseKeyring(active_version=1, keys={1: WORKER_KEY}))
    authority = PostgreSQLFileDispatchAuthority(
        guarded_scheduler_engine,
        codec,
        configured_root_refs=("operator-scan-root",),
    )
    claim = authority.claim()
    assert type(claim) is FileDispatchLease
    exact_claim = claim
    assert claim.organization_id == organization_id
    assert claim.source_ref.value == source_ref
    with (
        guarded_worker_engine.connect() as connection,
        pytest.raises(DBAPIError),
    ):
        connection.execute(
            text(
                "SELECT count(*) FROM file_import_job "
                "WHERE organization_id = :organization_id "
                "AND job_id = :job_id"
            ),
            {"organization_id": organization_id, "job_id": claim.job_id},
        ).scalar_one()
    roots = file_root_registry(FileRootRef("operator-scan-root"), root)
    try:
        wrong_job_claim = FileDispatchLease(
            claim.token,
            claim.organization_id,
            uuid4(),
            claim.source_ref,
            claim.service_principal_id,
            claim.lease_generation,
            claim.issued_at,
            claim.expires_at,
        )

        class WrongJobAuthority:
            def claim(self) -> FileDispatchLease:
                return wrong_job_claim

        class WrongJobWorkerFactory:
            def __call__(
                self,
                receiver: FileImportReceiver,
            ) -> PostgreSQLFileImportWorker:
                assert receiver == FileImportReceiver(
                    exact_claim.service_principal_id
                )
                return PostgreSQLFileImportWorker(
                    guarded_worker_engine,
                    codec,
                    receiver,
                    roots,
                    MarkdownCompilerConfig("markdown-config-v1"),
                    embedding_provider=DeterministicEmbeddingTwin(),
                    clock=lambda: exact_claim.issued_at,
                )

        wrong_job = dispatch_one_file_import(
            WrongJobAuthority(),
            WrongJobWorkerFactory(),
        )
        assert wrong_job.outcome == "refused"
        assert (
            wrong_job.reason_category
            is FileDispatchFailureCategory.WORKER_LEASE_REFUSED
        )

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

    exact_roots = file_root_registry(FileRootRef("operator-scan-root"), root)

    class ExactClaimAuthority:
        def claim(self) -> FileDispatchLease:
            return exact_claim

    class ExactWorkerFactory:
        def __call__(
            self,
            receiver: FileImportReceiver,
        ) -> PostgreSQLFileImportWorker:
            assert receiver == FileImportReceiver(exact_claim.service_principal_id)
            return PostgreSQLFileImportWorker(
                guarded_worker_engine,
                codec,
                receiver,
                exact_roots,
                MarkdownCompilerConfig("markdown-config-v1"),
                embedding_provider=DeterministicEmbeddingTwin(),
                clock=lambda: exact_claim.issued_at,
            )

    try:
        exact = dispatch_one_file_import(
            ExactClaimAuthority(),
            ExactWorkerFactory(),
        )
    finally:
        exact_roots.close()
    assert exact.outcome == "dispatched"
    assert exact.reason_category is None

    final_engine = create_database_engine(migration_configuration)
    try:
        with final_engine.connect() as connection:
            final_snapshot = connection.execute(
                text(
                    "SELECT state, effect_count FROM file_import_job "
                    "WHERE organization_id = :organization_id AND job_id = :job_id"
                ),
                {"organization_id": organization_id, "job_id": claim.job_id},
            ).one()
    finally:
        final_engine.dispose()
    assert tuple(final_snapshot) == ("completed", 1)
