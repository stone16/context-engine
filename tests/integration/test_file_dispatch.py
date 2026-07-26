from __future__ import annotations

import json
import os
import queue
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC
from hashlib import sha256
from pathlib import Path
from threading import Thread
from time import monotonic, sleep
from typing import TextIO, cast
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError
from uvicorn import Config, Server

from adapters.exact_phrase import PostgreSQLExactPhraseCandidateIndex
from adapters.file_source import FileChangeProvider, FileReadLimits, FileRootRegistry
from adapters.http.app import create_app
from applications.worker import _worker_database_time
from engine.control import (
    ChangeLimit,
    ControlOperation,
    FileChangeSource,
    FileImportAudience,
    FileImportReceiver,
    FileRootRef,
    InitialScan,
    ProviderOk,
    ScheduleFileChangePage,
)
from engine.persistence import (
    DatabaseConfiguration,
    FileDispatchLease,
    FileDispatchNoWork,
    FileImportInterrupted,
    FilePublicationBoundary,
    PostgreSQLFileDispatchAuthority,
    PostgreSQLFileImportWorker,
    PostgreSQLMembershipAuthority,
    create_database_engine,
)
from engine.runtime.construction import Runtime, required_kernel_dependencies
from engine.runtime.package_digest import QueryDigestKeyring
from engine.supply import (
    MarkdownCompilerConfig,
    WorkerLeaseCodec,
    WorkerLeaseKeyring,
    WorkNotAvailable,
)
from engine.supply.jobs import FILE_IMPORT_WORKER_LEASE_OPERATION
from tests.integration.test_file_change_pages import (
    _SCENARIOS,
    _activate_delete_observations,
    _authorize,
    _delete_observation_effect_snapshot,
    _delete_scenarios,
    _proofs,
    _seed_file_change_source,
)
from tests.integration.test_file_import_tracer import (
    _ExactScopeAuthority,
    _OrganizationAuthority,
    _RuntimeAuthenticator,
)
from tests.integration.test_z_egress_grant_file import (
    _pack_and_install_resolve_sdk,
    _run_installed_empty_consumer,
    _unused_port,
    _wait_for_tcp,
)
from tests.support.migrations import HEAD_REVISION
from tests.support.releases import (
    clear_test_runtime_release,
    ensure_test_runtime_release,
)

pytestmark = pytest.mark.integration
ROOT = Path(__file__).parents[2]
SIGNING_KEY = b"issue-91-file-dispatch-key-00001"
ALL_TEST_ROOTS = ("dispatch-root",)


def _drain_text_stream(stream: TextIO, lines: queue.Queue[str]) -> None:
    for line in stream:
        lines.put(line)


def _dispatch_authority(
    engine: Engine,
    codec: WorkerLeaseCodec | None = None,
) -> PostgreSQLFileDispatchAuthority:
    return PostgreSQLFileDispatchAuthority(
        engine,
        codec
        or WorkerLeaseCodec(
            WorkerLeaseKeyring(active_version=1, keys={1: SIGNING_KEY})
        ),
        configured_root_refs=ALL_TEST_ROOTS,
    )


@pytest.fixture(autouse=True)
def _enable_shared_scenario_tracking(
    migration_configuration: DatabaseConfiguration,
) -> object:
    scenarios: list[tuple[UUID, UUID]] = []
    _SCENARIOS.append(scenarios)
    try:
        yield
    finally:
        _SCENARIOS.remove(scenarios)
        _delete_scenarios(migration_configuration, scenarios)


def _schedule_one(
    *,
    root: Path,
    guarded_control_engine: Engine,
    migration_configuration: DatabaseConfiguration,
    root_ref: FileRootRef | None = None,
) -> tuple[UUID, UUID, FileRootRef]:
    root_ref = root_ref or FileRootRef("dispatch-root")
    provider_proofs, control_proofs = _proofs()
    organization_id = uuid4()
    receiver = FileImportReceiver(uuid4())
    control, authority, source = _seed_file_change_source(
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
        organization_id=organization_id,
        receiver=receiver,
        root_ref=root_ref,
        control_proofs=control_proofs,
    )
    source = _activate_delete_observations(control, authority, organization_id, source)
    provider = FileChangeProvider(
        FileRootRegistry(
            {source.source_version.root_ref: root},
            limits=FileReadLimits(max_file_bytes=4_096),
        ),
        proofs=provider_proofs,
    )
    page = provider.read_changes(
        FileChangeSource(organization_id, source.source_version),
        InitialScan(),
        ChangeLimit(10),
    )
    assert type(page) is ProviderOk
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        f"accept-dispatch-{organization_id}",
    ) as call:
        accepted = control.accept_file_change_page(call, page.value)
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            membership_id = connection.execute(
                text(
                    "SELECT membership_id FROM membership "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": organization_id},
            ).scalar_one()
    finally:
        migration_engine.dispose()
    with _authorize(
        authority,
        organization_id,
        ControlOperation.SCHEDULE_FILE_CHANGE_PAGE,
        f"schedule-dispatch-{organization_id}",
    ) as call:
        scheduled = control.schedule_file_change_page(
            call,
            ScheduleFileChangePage(
                accepted.source_ref,
                accepted.source_version_ref,
                accepted.page_ref,
                FileImportAudience("principal:file-reader", membership_id, 1),
            ),
        )
    return organization_id, scheduled.changes[0].prepared_import.job_id, root_ref


@pytest.mark.security_evidence(id="PG-FILE-DISPATCH-091", layer="postgres")
def test_scheduler_claims_only_current_page_scheduled_upsert(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_scheduler_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "handbook.md").write_text("# Current\n", encoding="utf-8")
    organization_id, job_id, _root_ref = _schedule_one(
        root=root,
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
    )
    codec = WorkerLeaseCodec(
        WorkerLeaseKeyring(active_version=1, keys={1: SIGNING_KEY})
    )
    authority = _dispatch_authority(guarded_scheduler_engine, codec)

    claim = authority.claim()
    assert type(claim) is FileDispatchLease
    assert claim.organization_id == organization_id
    assert claim.job_id == job_id
    assert claim.lease_generation == 1
    claims = codec.verify(
        claim.token,
        expected_organization_id=claim.organization_id,
        expected_job_id=claim.job_id,
        expected_service_principal_id=claim.service_principal_id,
        expected_workload="supply.file-import",
        expected_operation=FILE_IMPORT_WORKER_LEASE_OPERATION,
        expected_worker_audience="context-engine-worker",
        expected_source_ref=str(claim.source_ref.value),
        now=claim.issued_at,
    )
    assert claims.issued_at == claim.issued_at
    assert claims.expires_at == claim.expires_at
    assert claims.lease_generation == claim.lease_generation
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            state = connection.execute(
                text(
                    "SELECT state, lease_generation, dispatch_claimed, "
                    "(SELECT count(*) FROM context_revision "
                    "WHERE organization_id = :organization_id) AS revisions "
                    "FROM file_import_job WHERE organization_id = :organization_id "
                    "AND job_id = :job_id"
                ),
                {"organization_id": organization_id, "job_id": job_id},
            ).one()
    finally:
        migration_engine.dispose()
    assert tuple(state) == ("leased", 1, True, 0)
    assert type(authority.claim()) is FileDispatchNoWork

    with pytest.raises(DBAPIError), guarded_scheduler_engine.connect() as connection:
        connection.execute(text("SELECT count(*) FROM file_import_job"))


def test_dispatch_normalizes_non_utc_database_sessions(
    tmp_path: Path,
    guarded_control_engine: Engine,
    scheduler_configuration: DatabaseConfiguration,
    worker_configuration: DatabaseConfiguration,
    migration_configuration: DatabaseConfiguration,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "offset.md").write_text("# Offset\n", encoding="utf-8")
    _schedule_one(
        root=root,
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
    )
    session_options = {"options": "-c timezone=Asia/Shanghai"}
    scheduler_engine = create_engine(
        scheduler_configuration.url,
        connect_args=session_options,
    )
    worker_engine = create_engine(
        worker_configuration.url,
        connect_args=session_options,
    )
    try:
        claim = _dispatch_authority(scheduler_engine).claim()
        assert type(claim) is FileDispatchLease
        assert claim.issued_at.utcoffset() == UTC.utcoffset(claim.issued_at)
        assert claim.expires_at.utcoffset() == UTC.utcoffset(claim.expires_at)
        checked_at = _worker_database_time(worker_engine)
        assert checked_at.utcoffset() == UTC.utcoffset(checked_at)
    finally:
        scheduler_engine.dispose()
        worker_engine.dispose()


def test_revoked_audience_returns_content_free_no_work(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_scheduler_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "revoked.md").write_text("# Revoked\n", encoding="utf-8")
    organization_id, _job_id, _root_ref = _schedule_one(
        root=root,
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
    )
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE membership SET status = 'revoked' "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": organization_id},
            )
    finally:
        migration_engine.dispose()
    authority = _dispatch_authority(guarded_scheduler_engine)

    assert authority.claim() == FileDispatchNoWork()


def test_missing_server_root_capability_leases_nothing(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_scheduler_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    root = tmp_path / "missing-registry-root"
    root.mkdir()
    (root / "missing.md").write_text("# Missing\n", encoding="utf-8")
    organization_id, job_id, _root_ref = _schedule_one(
        root=root,
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
    )
    authority = PostgreSQLFileDispatchAuthority(
        guarded_scheduler_engine,
        WorkerLeaseCodec(WorkerLeaseKeyring(active_version=1, keys={1: SIGNING_KEY})),
        configured_root_refs=("another-root",),
    )

    assert authority.claim() == FileDispatchNoWork()

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            state = connection.execute(
                text(
                    "SELECT state, lease_generation, dispatch_claimed "
                    "FROM file_import_job WHERE organization_id = :organization_id "
                    "AND job_id = :job_id"
                ),
                {"organization_id": organization_id, "job_id": job_id},
            ).one()
    finally:
        migration_engine.dispose()
    assert tuple(state) == ("available", 0, False)


def test_scheduler_null_inputs_lease_nothing(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_scheduler_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    root = tmp_path / "null-registry-root"
    root.mkdir()
    (root / "null.md").write_text("# Null\n", encoding="utf-8")
    organization_id, job_id, _root_ref = _schedule_one(
        root=root,
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
    )

    with guarded_scheduler_engine.begin() as connection:
        statements = (
            "SELECT * FROM public.context_scheduler_claim_file_import("
            "NULL::bigint, :nonce, ARRAY['dispatch-root']::text[])",
            "SELECT * FROM public.context_scheduler_claim_file_import("
            ":key_version, NULL::bytea, ARRAY['dispatch-root']::text[])",
            "SELECT * FROM public.context_scheduler_claim_file_import("
            ":key_version, :nonce, NULL::text[])",
        )
        for statement in statements:
            assert (
                connection.execute(
                    text(statement),
                    {"key_version": 1, "nonce": b"n" * 32},
                ).all()
                == []
            )

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            state = connection.execute(
                text(
                    "SELECT state, lease_generation, dispatch_claimed "
                    "FROM file_import_job WHERE organization_id = :organization_id "
                    "AND job_id = :job_id"
                ),
                {"organization_id": organization_id, "job_id": job_id},
            ).one()
    finally:
        migration_engine.dispose()
    assert tuple(state) == ("available", 0, False)


def test_scheduler_root_subset_cannot_redirect_global_oldest_selection(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_scheduler_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    scheduled: list[tuple[UUID, UUID, FileRootRef]] = []
    for name in ("oldest-root", "newer-root"):
        root = tmp_path / name
        root.mkdir()
        (root / f"{name}.md").write_text(f"# {name}\n", encoding="utf-8")
        scheduled.append(
            _schedule_one(
                root=root,
                guarded_control_engine=guarded_control_engine,
                migration_configuration=migration_configuration,
                root_ref=FileRootRef(name),
            )
        )
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE file_source_acquisition_checkpoint DISABLE TRIGGER "
                    "file_source_acquisition_checkpoint_immutable"
                )
            )
            for offset, (_organization_id, job_id, _root_ref) in enumerate(scheduled):
                connection.execute(
                    text(
                        "UPDATE file_source_acquisition_checkpoint SET accepted_at = "
                        "TIMESTAMPTZ '2026-07-25 12:00:00+00' + "
                        ":offset * interval '1 second' WHERE job_id = :job_id"
                    ),
                    {"offset": offset, "job_id": job_id},
                )
            connection.execute(
                text(
                    "ALTER TABLE file_source_acquisition_checkpoint ENABLE TRIGGER "
                    "file_source_acquisition_checkpoint_immutable"
                )
            )
    finally:
        migration_engine.dispose()
    subset = PostgreSQLFileDispatchAuthority(
        guarded_scheduler_engine,
        WorkerLeaseCodec(WorkerLeaseKeyring(active_version=1, keys={1: SIGNING_KEY})),
        configured_root_refs=("newer-root",),
    )
    assert subset.claim() == FileDispatchNoWork()

    complete = PostgreSQLFileDispatchAuthority(
        guarded_scheduler_engine,
        WorkerLeaseCodec(WorkerLeaseKeyring(active_version=1, keys={1: SIGNING_KEY})),
        configured_root_refs=("oldest-root", "newer-root"),
    )
    claim = complete.claim()
    assert type(claim) is FileDispatchLease
    assert claim.job_id == scheduled[0][1]


def test_scheduler_root_subset_cannot_redirect_oldest_reclaim(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_scheduler_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    scheduled: list[tuple[UUID, UUID, FileRootRef]] = []
    for name in ("oldest-reclaim-root", "newer-reclaim-root"):
        root = tmp_path / name
        root.mkdir()
        (root / "reclaim.md").write_text(f"# {name}\n", encoding="utf-8")
        scheduled.append(
            _schedule_one(
                root=root,
                guarded_control_engine=guarded_control_engine,
                migration_configuration=migration_configuration,
                root_ref=FileRootRef(name),
            )
        )
    complete = PostgreSQLFileDispatchAuthority(
        guarded_scheduler_engine,
        WorkerLeaseCodec(WorkerLeaseKeyring(active_version=1, keys={1: SIGNING_KEY})),
        configured_root_refs=("oldest-reclaim-root", "newer-reclaim-root"),
    )
    first = complete.claim()
    second = complete.claim()
    assert type(first) is FileDispatchLease
    assert type(second) is FileDispatchLease
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE file_import_job SET lease_issued_at = "
                    "clock_timestamp() - interval '10 minutes', "
                    "lease_expires_at = clock_timestamp() - interval '31 seconds' "
                    "WHERE job_id = ANY(:job_ids)"
                ),
                {"job_ids": [first.job_id, second.job_id]},
            )
    finally:
        migration_engine.dispose()
    subset = PostgreSQLFileDispatchAuthority(
        guarded_scheduler_engine,
        WorkerLeaseCodec(WorkerLeaseKeyring(active_version=1, keys={1: SIGNING_KEY})),
        configured_root_refs=("newer-reclaim-root",),
    )
    assert subset.claim() == FileDispatchNoWork()
    reclaimed = complete.claim()
    assert type(reclaimed) is FileDispatchLease
    assert reclaimed.job_id == first.job_id


@pytest.mark.parametrize(
    ("mutation", "parameters"),
    [
        (
            "UPDATE context_source SET lifecycle_state = 'disabled', "
            "disabled_version_id = active_version_id, disabled_at = now() "
            "WHERE organization_id = :organization_id",
            {},
        ),
        (
            "UPDATE service_principal SET enabled = false "
            "WHERE organization_id = :organization_id",
            {},
        ),
        (
            "UPDATE membership SET valid_until = now() - interval '1 second' "
            "WHERE organization_id = :organization_id",
            {},
        ),
    ],
)
def test_current_authority_filter_leaves_ineligible_job_untouched(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_scheduler_engine: Engine,
    migration_configuration: DatabaseConfiguration,
    mutation: str,
    parameters: dict[str, object],
) -> None:
    root = tmp_path / "filtered-root"
    root.mkdir()
    (root / "filtered.md").write_text("# Filtered\n", encoding="utf-8")
    organization_id, job_id, _root_ref = _schedule_one(
        root=root,
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
    )
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(mutation),
                {"organization_id": organization_id, **parameters},
            )
    finally:
        migration_engine.dispose()

    authority = _dispatch_authority(guarded_scheduler_engine)
    assert authority.claim() == FileDispatchNoWork()

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            state = connection.execute(
                text(
                    "SELECT state, lease_generation, dispatch_claimed "
                    "FROM file_import_job WHERE organization_id = :organization_id "
                    "AND job_id = :job_id"
                ),
                {"organization_id": organization_id, "job_id": job_id},
            ).one()
    finally:
        migration_engine.dispose()
    assert tuple(state) == ("available", 0, False)


def test_non_available_and_later_generation_jobs_are_not_claimed(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_scheduler_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    root = tmp_path / "leased-root"
    root.mkdir()
    (root / "leased.md").write_text("# Leased\n", encoding="utf-8")
    organization_id, job_id, _root_ref = _schedule_one(
        root=root,
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
    )
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE file_import_job SET state = 'leased', "
                    "signing_key_version = 1, "
                    "lease_nonce_digest = digest('x', 'sha256'), "
                    "lease_issued_at = now(), "
                    "lease_expires_at = now() + interval '5 min', "
                    "lease_generation = 2 WHERE organization_id = :organization_id "
                    "AND job_id = :job_id"
                ),
                {"organization_id": organization_id, "job_id": job_id},
            )
    finally:
        migration_engine.dispose()
    authority = _dispatch_authority(guarded_scheduler_engine)

    assert authority.claim() == FileDispatchNoWork()


def test_superseded_scan_page_job_is_not_claimed(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_scheduler_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    first_root = tmp_path / "first-scan"
    first_root.mkdir()
    path = first_root / "scan.md"
    path.write_text("# First\n", encoding="utf-8")
    organization_id, first_job_id, root_ref = _schedule_one(
        root=first_root,
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
    )
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "SELECT set_config('app.organization_id', "
                    "CAST(:organization_id AS text), true)"
                ),
                {"organization_id": organization_id},
            )
            source_id, source_version_id = connection.execute(
                text(
                    "SELECT source_id, active_version_id FROM context_source "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": organization_id},
            ).one()
            latest_page = connection.execute(
                text(
                    "SELECT page_ref, scan_epoch, accepted_at "
                    "FROM file_source_change_page "
                    "WHERE organization_id = :organization_id "
                    "AND source_id = :source_id ORDER BY accepted_at DESC LIMIT 1"
                ),
                {"organization_id": organization_id, "source_id": source_id},
            ).one()
            new_page_ref = sha256(
                organization_id.bytes + b"issue-91-new-scan-page"
            ).hexdigest()
            connection.execute(
                text(
                    "SET CONSTRAINTS "
                    "fk_file_source_delete_observation_page_exact DEFERRED"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO file_source_delete_observation_page ("
                    "organization_id, source_id, source_version_id, page_ref) VALUES ("
                    ":organization_id, :source_id, :version_id, :page_ref)"
                ),
                {
                    "organization_id": organization_id,
                    "source_id": source_id,
                    "version_id": source_version_id,
                    "page_ref": new_page_ref,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO file_source_change_page (organization_id, source_id, "
                    "source_version_id, page_ref, scan_ref, scan_epoch, page_limit, "
                    "page_ordinal, change_count, complete, accepted_at) VALUES ("
                    ":organization_id, :source_id, :version_id, :page_ref, :scan_ref, "
                    ":scan_epoch, 1, 1, 0, true, :accepted_at + interval '1 second')"
                ),
                {
                    "organization_id": organization_id,
                    "source_id": source_id,
                    "version_id": source_version_id,
                    "page_ref": new_page_ref,
                    "scan_ref": sha256(
                        organization_id.bytes + b"issue-91-new-scan"
                    ).hexdigest(),
                    "scan_epoch": uuid4(),
                    "accepted_at": latest_page.accepted_at,
                },
            )
            max_sequence = connection.execute(
                text(
                    "SELECT max(sequence) FROM file_source_acquisition_checkpoint "
                    "WHERE organization_id = :organization_id "
                    "AND source_id = :source_id"
                ),
                {"organization_id": organization_id, "source_id": source_id},
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO file_source_acquisition_checkpoint ("
                    "organization_id, source_id, sequence, checkpoint_ref, "
                    "change_kind, "
                    "accepted_at, source_version_id, change_page_ref) VALUES ("
                    ":organization_id, :source_id, :sequence, :checkpoint_ref, "
                    "'file_change_page', :accepted_at + interval '1 second', "
                    ":version_id, :page_ref)"
                ),
                {
                    "organization_id": organization_id,
                    "source_id": source_id,
                    "sequence": max_sequence + 1,
                    "checkpoint_ref": "facp_"
                    + sha256(
                        organization_id.bytes + b"issue-91-checkpoint"
                    ).hexdigest(),
                    "accepted_at": latest_page.accepted_at,
                    "version_id": source_version_id,
                    "page_ref": new_page_ref,
                },
            )
    finally:
        migration_engine.dispose()
    assert root_ref.value
    authority = _dispatch_authority(guarded_scheduler_engine)

    assert authority.claim() == FileDispatchNoWork()
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            state = connection.execute(
                text(
                    "SELECT state, dispatch_claimed FROM file_import_job "
                    "WHERE organization_id = :organization_id AND job_id = :job_id"
                ),
                {"organization_id": organization_id, "job_id": first_job_id},
            ).one()
    finally:
        migration_engine.dispose()
    assert tuple(state) == ("available", False)


@pytest.mark.security_evidence(id="PG-FILE-DISPATCH-CONCURRENCY-091", layer="postgres")
def test_concurrent_dispatchers_never_claim_the_same_job(
    tmp_path: Path,
    guarded_control_engine: Engine,
    scheduler_configuration: DatabaseConfiguration,
    migration_configuration: DatabaseConfiguration,
) -> None:
    roots = []
    expected_jobs = set()
    for name in ("first", "second"):
        root = tmp_path / name
        root.mkdir()
        (root / f"{name}.md").write_text(f"# {name}\n", encoding="utf-8")
        roots.append(root)
        _organization_id, job_id, _root_ref = _schedule_one(
            root=root,
            guarded_control_engine=guarded_control_engine,
            migration_configuration=migration_configuration,
        )
        expected_jobs.add(job_id)

    def claim() -> FileDispatchLease | FileDispatchNoWork:
        engine = create_database_engine(scheduler_configuration)
        try:
            return _dispatch_authority(
                engine,
                WorkerLeaseCodec(
                    WorkerLeaseKeyring(active_version=1, keys={1: SIGNING_KEY})
                ),
            ).claim()
        finally:
            engine.dispose()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _index: claim(), range(2)))

    claims = tuple(result for result in results if type(result) is FileDispatchLease)
    assert {claim.job_id for claim in claims} == expected_jobs
    assert len({claim.job_id for claim in claims}) == len(claims) == 2


@pytest.mark.security_evidence(id="PG-FILE-RECLAIM-093", layer="postgres")
def test_scheduler_reclaims_one_expired_dispatch_after_database_backoff(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_scheduler_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    root = tmp_path / "reclaim"
    root.mkdir()
    (root / "reclaim.md").write_text("# Reclaim\n", encoding="utf-8")
    organization_id, job_id, _root_ref = _schedule_one(
        root=root,
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
    )
    authority = _dispatch_authority(guarded_scheduler_engine)
    first = authority.claim()
    assert type(first) is FileDispatchLease
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE file_import_job SET "
                    "lease_issued_at = clock_timestamp() - interval '10 minutes', "
                    "lease_expires_at = clock_timestamp() - interval '29 seconds' "
                    "WHERE organization_id = :organization_id AND job_id = :job_id"
                ),
                {"organization_id": organization_id, "job_id": job_id},
            )
        assert authority.claim() == FileDispatchNoWork()
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE file_import_job SET "
                    "lease_expires_at = clock_timestamp() - interval '31 seconds' "
                    "WHERE organization_id = :organization_id AND job_id = :job_id"
                ),
                {"organization_id": organization_id, "job_id": job_id},
            )
        reclaimed = authority.claim()
        assert type(reclaimed) is FileDispatchLease
        assert reclaimed.job_id == job_id
        assert reclaimed.lease_generation == 2
        with migration_engine.connect() as connection:
            event = connection.execute(
                text(
                    "SELECT ordinal, event_type, boundary, lease_generation, "
                    "state_at_event, reason_digest ~ '^[0-9a-f]{64}$' "
                    "FROM file_import_job_event "
                    "WHERE organization_id = :organization_id AND job_id = :job_id"
                ),
                {"organization_id": organization_id, "job_id": job_id},
            ).one()
    finally:
        migration_engine.dispose()
    assert tuple(event) == (0, "reclaimed", "acquired", 2, "leased", True)


@pytest.mark.parametrize(
    ("generation", "before_seconds", "eligible_seconds"),
    [(2, 59, 61), (3, 119, 121)],
)
def test_scheduler_uses_generation_derived_database_backoff(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_scheduler_engine: Engine,
    migration_configuration: DatabaseConfiguration,
    generation: int,
    before_seconds: int,
    eligible_seconds: int,
) -> None:
    root = tmp_path / f"backoff-{generation}"
    root.mkdir()
    (root / "backoff.md").write_text("# Backoff\n", encoding="utf-8")
    organization_id, job_id, _root_ref = _schedule_one(
        root=root,
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
    )
    authority = _dispatch_authority(guarded_scheduler_engine)
    assert type(authority.claim()) is FileDispatchLease
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE file_import_job SET lease_generation = :generation, "
                    "lease_issued_at = clock_timestamp() - interval '20 minutes', "
                    "lease_expires_at = clock_timestamp() - "
                    "make_interval(secs => :before_seconds) WHERE "
                    "organization_id = :organization_id AND job_id = :job_id"
                ),
                {
                    "generation": generation,
                    "before_seconds": before_seconds,
                    "organization_id": organization_id,
                    "job_id": job_id,
                },
            )
        assert authority.claim() == FileDispatchNoWork()
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE file_import_job SET lease_expires_at = "
                    "clock_timestamp() - make_interval(secs => :eligible_seconds) "
                    "WHERE organization_id = :organization_id AND job_id = :job_id"
                ),
                {
                    "eligible_seconds": eligible_seconds,
                    "organization_id": organization_id,
                    "job_id": job_id,
                },
            )
        reclaimed = authority.claim()
    finally:
        migration_engine.dispose()
    assert type(reclaimed) is FileDispatchLease
    assert reclaimed.lease_generation == generation + 1


def test_scheduler_does_not_reclaim_after_automatic_generation_budget(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_scheduler_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    root = tmp_path / "exhausted"
    root.mkdir()
    (root / "exhausted.md").write_text("# Exhausted\n", encoding="utf-8")
    organization_id, job_id, _root_ref = _schedule_one(
        root=root,
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
    )
    authority = _dispatch_authority(guarded_scheduler_engine)
    assert type(authority.claim()) is FileDispatchLease
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE file_import_job SET lease_generation = 4, "
                    "lease_issued_at = clock_timestamp() - interval '20 minutes', "
                    "lease_expires_at = clock_timestamp() - interval '10 minutes' "
                    "WHERE organization_id = :organization_id AND job_id = :job_id"
                ),
                {"organization_id": organization_id, "job_id": job_id},
            )
        assert authority.claim() == FileDispatchNoWork()
        with migration_engine.connect() as connection:
            snapshot = connection.execute(
                text(
                    "SELECT state, lease_generation, "
                    "(SELECT count(*) FROM file_import_job_event WHERE "
                    "organization_id = :organization_id AND job_id = :job_id) "
                    "FROM file_import_job WHERE organization_id = :organization_id "
                    "AND job_id = :job_id"
                ),
                {"organization_id": organization_id, "job_id": job_id},
            ).one()
    finally:
        migration_engine.dispose()
    assert tuple(snapshot) == ("leased", 4, 0)


@pytest.mark.security_evidence(id="PG-FILE-RECLAIM-CONCURRENCY-093", layer="postgres")
def test_concurrent_schedulers_reclaim_one_expired_generation_once(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_scheduler_engine: Engine,
    scheduler_configuration: DatabaseConfiguration,
    migration_configuration: DatabaseConfiguration,
) -> None:
    root = tmp_path / "reclaim-concurrent"
    root.mkdir()
    (root / "concurrent.md").write_text("# Concurrent\n", encoding="utf-8")
    organization_id, job_id, _root_ref = _schedule_one(
        root=root,
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
    )
    first = _dispatch_authority(guarded_scheduler_engine).claim()
    assert type(first) is FileDispatchLease
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE file_import_job SET "
                    "lease_issued_at = clock_timestamp() - interval '10 minutes', "
                    "lease_expires_at = clock_timestamp() - interval '31 seconds' "
                    "WHERE organization_id = :organization_id AND job_id = :job_id"
                ),
                {"organization_id": organization_id, "job_id": job_id},
            )

        def claim() -> FileDispatchLease | FileDispatchNoWork:
            engine = create_database_engine(scheduler_configuration)
            try:
                return _dispatch_authority(engine).claim()
            finally:
                engine.dispose()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(lambda _index: claim(), range(2)))
        assert sum(type(result) is FileDispatchLease for result in results) == 1
        assert sum(type(result) is FileDispatchNoWork for result in results) == 1
        claimed = next(
            result for result in results if type(result) is FileDispatchLease
        )
        assert claimed.job_id == job_id
        assert claimed.lease_generation == 2
        with migration_engine.connect() as connection:
            snapshot = connection.execute(
                text(
                    "SELECT lease_generation, (SELECT count(*) FROM "
                    "file_import_job_event WHERE organization_id = :organization_id "
                    "AND job_id = :job_id AND event_type = 'reclaimed') "
                    "FROM file_import_job WHERE organization_id = :organization_id "
                    "AND job_id = :job_id"
                ),
                {"organization_id": organization_id, "job_id": job_id},
            ).one()
    finally:
        migration_engine.dispose()
    assert tuple(snapshot) == (2, 1)


def test_scheduler_reclaim_revalidates_current_membership_without_mutation(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_scheduler_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    root = tmp_path / "reclaim-revoked"
    root.mkdir()
    (root / "revoked.md").write_text("# Revoked\n", encoding="utf-8")
    organization_id, job_id, _root_ref = _schedule_one(
        root=root,
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
    )
    authority = _dispatch_authority(guarded_scheduler_engine)
    assert type(authority.claim()) is FileDispatchLease
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE file_import_job SET "
                    "lease_issued_at = clock_timestamp() - interval '10 minutes', "
                    "lease_expires_at = clock_timestamp() - interval '31 seconds' "
                    "WHERE organization_id = :organization_id AND job_id = :job_id"
                ),
                {"organization_id": organization_id, "job_id": job_id},
            )
            connection.execute(
                text(
                    "UPDATE membership SET status = 'revoked' "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": organization_id},
            )
        assert authority.claim() == FileDispatchNoWork()
        with migration_engine.connect() as connection:
            snapshot = connection.execute(
                text(
                    "SELECT state, lease_generation, (SELECT count(*) FROM "
                    "file_import_job_event WHERE organization_id = :organization_id "
                    "AND job_id = :job_id) FROM file_import_job "
                    "WHERE organization_id = :organization_id AND job_id = :job_id"
                ),
                {"organization_id": organization_id, "job_id": job_id},
            ).one()
    finally:
        migration_engine.dispose()
    assert tuple(snapshot) == ("leased", 1, 0)


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE context_source SET lifecycle_state = 'disabled', "
        "disabled_version_id = active_version_id, disabled_at = now() "
        "WHERE organization_id = :organization_id",
        "UPDATE service_principal SET enabled = false "
        "WHERE organization_id = :organization_id",
    ],
)
def test_scheduler_reclaim_revalidates_source_and_receiver_without_mutation(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_scheduler_engine: Engine,
    migration_configuration: DatabaseConfiguration,
    mutation: str,
) -> None:
    root = tmp_path / "reclaim-authority"
    root.mkdir()
    (root / "authority.md").write_text("# Authority\n", encoding="utf-8")
    organization_id, job_id, _root_ref = _schedule_one(
        root=root,
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
    )
    authority = _dispatch_authority(guarded_scheduler_engine)
    assert type(authority.claim()) is FileDispatchLease
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE file_import_job SET lease_issued_at = "
                    "clock_timestamp() - interval '10 minutes', "
                    "lease_expires_at = clock_timestamp() - interval '31 seconds' "
                    "WHERE organization_id = :organization_id AND job_id = :job_id"
                ),
                {"organization_id": organization_id, "job_id": job_id},
            )
            connection.execute(text(mutation), {"organization_id": organization_id})
        assert authority.claim() == FileDispatchNoWork()
        with migration_engine.connect() as connection:
            snapshot = connection.execute(
                text(
                    "SELECT state, lease_generation, (SELECT count(*) FROM "
                    "file_import_job_event WHERE organization_id = :organization_id "
                    "AND job_id = :job_id) FROM file_import_job WHERE "
                    "organization_id = :organization_id AND job_id = :job_id"
                ),
                {"organization_id": organization_id, "job_id": job_id},
            ).one()
    finally:
        migration_engine.dispose()
    assert tuple(snapshot) == ("leased", 1, 0)


def test_scheduler_reclaim_refuses_a_superseded_scan_without_mutation(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_scheduler_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    root = tmp_path / "reclaim-stale-scan"
    root.mkdir()
    (root / "stale.md").write_text("# Stale\n", encoding="utf-8")
    organization_id, job_id, _root_ref = _schedule_one(
        root=root,
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
    )
    authority = _dispatch_authority(guarded_scheduler_engine)
    assert type(authority.claim()) is FileDispatchLease
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            source_id, version_id, accepted_at, sequence = connection.execute(
                text(
                    "SELECT source.source_id, source.active_version_id, "
                    "page.accepted_at, max(checkpoint.sequence) OVER () FROM "
                    "context_source AS source JOIN file_source_change_page AS page "
                    "ON page.organization_id = source.organization_id AND "
                    "page.source_id = source.source_id JOIN "
                    "file_source_acquisition_checkpoint AS checkpoint ON "
                    "checkpoint.organization_id = page.organization_id AND "
                    "checkpoint.source_id = page.source_id WHERE "
                    "source.organization_id = :organization_id ORDER BY "
                    "checkpoint.sequence DESC LIMIT 1"
                ),
                {"organization_id": organization_id},
            ).one()
            connection.execute(
                text(
                    "SELECT set_config('app.organization_id', "
                    "CAST(:organization_id AS text), true)"
                ),
                {"organization_id": organization_id},
            )
            connection.execute(
                text(
                    "SELECT set_config('app.file_source_id', "
                    "CAST(:source_id AS text), true)"
                ),
                {"source_id": source_id},
            )
            connection.execute(
                text(
                    "SELECT set_config('app.file_source_version_id', "
                    "CAST(:version_id AS text), true)"
                ),
                {"version_id": version_id},
            )
            page_ref = sha256(organization_id.bytes + b"reclaim-stale-page").hexdigest()
            connection.execute(
                text(
                    "INSERT INTO file_source_delete_observation_page ("
                    "organization_id, source_id, source_version_id, page_ref) "
                    "VALUES (:organization_id, :source_id, :version_id, :page_ref)"
                ),
                {
                    "organization_id": organization_id,
                    "source_id": source_id,
                    "version_id": version_id,
                    "page_ref": page_ref,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO file_source_change_page (organization_id, "
                    "source_id, source_version_id, page_ref, scan_ref, scan_epoch, "
                    "page_limit, page_ordinal, change_count, complete, accepted_at) "
                    "VALUES (:organization_id, :source_id, :version_id, :page_ref, "
                    ":scan_ref, :scan_epoch, 1, 1, 0, true, "
                    ":accepted_at + interval '1 second')"
                ),
                {
                    "organization_id": organization_id,
                    "source_id": source_id,
                    "version_id": version_id,
                    "page_ref": page_ref,
                    "scan_ref": sha256(
                        organization_id.bytes + b"reclaim-stale-scan"
                    ).hexdigest(),
                    "scan_epoch": uuid4(),
                    "accepted_at": accepted_at,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO file_source_acquisition_checkpoint ("
                    "organization_id, source_id, sequence, checkpoint_ref, "
                    "change_kind, accepted_at, source_version_id, change_page_ref) "
                    "VALUES (:organization_id, :source_id, :sequence, "
                    ":checkpoint_ref, 'file_change_page', "
                    ":accepted_at + interval '1 second', :version_id, :page_ref)"
                ),
                {
                    "organization_id": organization_id,
                    "source_id": source_id,
                    "sequence": sequence + 1,
                    "checkpoint_ref": "facp_"
                    + sha256(
                        organization_id.bytes + b"reclaim-stale-checkpoint"
                    ).hexdigest(),
                    "accepted_at": accepted_at,
                    "version_id": version_id,
                    "page_ref": page_ref,
                },
            )
            connection.execute(
                text(
                    "UPDATE file_import_job SET lease_issued_at = "
                    "clock_timestamp() - interval '10 minutes', "
                    "lease_expires_at = clock_timestamp() - interval '31 seconds' "
                    "WHERE organization_id = :organization_id AND job_id = :job_id"
                ),
                {"organization_id": organization_id, "job_id": job_id},
            )
        assert authority.claim() == FileDispatchNoWork()
        with migration_engine.connect() as connection:
            snapshot = connection.execute(
                text(
                    "SELECT state, lease_generation, (SELECT count(*) FROM "
                    "file_import_job_event WHERE organization_id = :organization_id "
                    "AND job_id = :job_id) FROM file_import_job WHERE "
                    "organization_id = :organization_id AND job_id = :job_id"
                ),
                {"organization_id": organization_id, "job_id": job_id},
            ).one()
    finally:
        migration_engine.dispose()
    assert tuple(snapshot) == ("leased", 1, 0)


@pytest.mark.security_evidence(id="PROC-FILE-RECLAIM-093", layer="runtime")
@pytest.mark.parametrize(
    "boundary",
    [
        None,
        FilePublicationBoundary.ACQUIRED,
        FilePublicationBoundary.PREPARED,
        FilePublicationBoundary.INDEXED,
    ],
)
def test_scheduler_recovers_interrupted_publication_and_stales_old_lease(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_scheduler_engine: Engine,
    guarded_worker_engine: Engine,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
    migration_configuration: DatabaseConfiguration,
    boundary: FilePublicationBoundary | None,
) -> None:
    root = tmp_path / "resume"
    root.mkdir()
    (root / "resume.md").write_text(
        "# Resume\n\nContextEngine delivers context.\n",
        encoding="utf-8",
    )
    organization_id, job_id, root_ref = _schedule_one(
        root=root,
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
    )
    codec = WorkerLeaseCodec(
        WorkerLeaseKeyring(active_version=1, keys={1: SIGNING_KEY})
    )
    authority = _dispatch_authority(guarded_scheduler_engine, codec)
    first = authority.claim()
    assert type(first) is FileDispatchLease
    roots = FileRootRegistry(
        {root_ref: root}, limits=FileReadLimits(max_file_bytes=4_096)
    )
    if boundary is not None:
        interrupted_worker = PostgreSQLFileImportWorker(
            guarded_worker_engine,
            codec,
            FileImportReceiver(first.service_principal_id),
            roots,
            MarkdownCompilerConfig("markdown-config-v1"),
            clock=lambda: first.issued_at,
            interrupt_after=boundary,
        )
        with pytest.raises(FileImportInterrupted):
            interrupted_worker.run(first.redemption)
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE file_import_job SET "
                    "lease_issued_at = clock_timestamp() - interval '10 minutes', "
                    "lease_expires_at = clock_timestamp() - interval '31 seconds' "
                    "WHERE organization_id = :organization_id AND job_id = :job_id"
                ),
                {"organization_id": organization_id, "job_id": job_id},
            )
        completed = subprocess.run(
            ["context-engine-worker", "--dispatch-file-once"],
            env={
                **os.environ,
                "CONTEXT_ENGINE_WORKER_LEASE_SIGNING_KEY_HEX": SIGNING_KEY.hex(),
                "CONTEXT_ENGINE_WORKER_FILE_ROOTS_JSON": json.dumps(
                    {root_ref.value: str(root)}
                ),
            },
            check=True,
            capture_output=True,
            text=True,
        )
        assert json.loads(completed.stdout) == {
            "dispatch": "file.import",
            "outcome": "dispatched",
            "service": "context-engine-worker",
            "status": "complete",
        }
        forbidden = (str(root), str(organization_id), str(job_id), SIGNING_KEY.hex())
        assert all(value not in completed.stdout for value in forbidden)
        stale_worker = PostgreSQLFileImportWorker(
            guarded_worker_engine,
            codec,
            FileImportReceiver(first.service_principal_id),
            roots,
            MarkdownCompilerConfig("markdown-config-v1"),
            clock=lambda: first.issued_at,
        )
        with pytest.raises(WorkNotAvailable):
            stale_worker.run(first.redemption)
        with migration_engine.connect() as connection:
            snapshot = connection.execute(
                text(
                    "SELECT job.state, job.lease_generation, job.effect_count, "
                    "(SELECT count(*) FROM context_revision WHERE "
                    "organization_id = :organization_id), "
                    "(SELECT count(*) FROM file_import_job_event WHERE "
                    "organization_id = :organization_id AND job_id = :job_id "
                    "AND event_type = 'reclaimed') FROM file_import_job AS job "
                    "WHERE job.organization_id = :organization_id "
                    "AND job.job_id = :job_id"
                ),
                {"organization_id": organization_id, "job_id": job_id},
            ).one()
            candidate = connection.execute(
                text(
                    "SELECT source_ref, resource_ref, CAST(revision_id AS text) "
                    "AS revision_ref FROM exact_phrase_candidate WHERE "
                    "organization_id = :organization_id"
                ),
                {"organization_id": organization_id},
            ).one()
            membership_id, user_id = connection.execute(
                text(
                    "SELECT membership_id, user_id FROM membership WHERE "
                    "organization_id = :organization_id"
                ),
                {"organization_id": organization_id},
            ).one()
    finally:
        migration_engine.dispose()
    assert tuple(snapshot) == ("completed", 2, 1, 1, 1)
    if boundary is None:
        ensure_test_runtime_release(
            organization_id,
            active_revision_refs=(candidate.revision_ref,),
        )
        consumer_root = tmp_path / "reclaim-sdk-consumer"
        consumer_root.mkdir()
        _pack_and_install_resolve_sdk(consumer_root)
        application = create_app(
            authenticator=_RuntimeAuthenticator(
                organization_id,
                user_id,
                membership_id,
            ),
            organization_authority=_OrganizationAuthority(),
            membership_authority=PostgreSQLMembershipAuthority(guarded_runtime_engine),
            scope_authority=_ExactScopeAuthority(
                candidate.source_ref,
                candidate.resource_ref,
            ),
            runtime=Runtime(
                required_kernel_dependencies(),
                candidate_index=PostgreSQLExactPhraseCandidateIndex(),
                query_digest_keyring=query_digest_keyring,
            ),
        )
        port = _unused_port()
        server = Server(
            Config(
                application,
                host="127.0.0.1",
                port=port,
                log_level="warning",
                lifespan="off",
            )
        )
        thread = Thread(target=server.run, daemon=True)
        thread.start()
        try:
            _wait_for_tcp(port)
            resolved = _run_installed_empty_consumer(
                consumer_root,
                base_url=f"http://127.0.0.1:{port}",
            )
            package = cast(dict[str, object], resolved["package"])
            blocks = cast(list[dict[str, object]], package["blocks"])
            evidence = cast(list[dict[str, object]], package["evidence"])
            assert [block["text"] for block in blocks] == [
                "ContextEngine delivers context."
            ]
            assert len(evidence) == 1
            assert evidence[0]["revisionRef"] == candidate.revision_ref
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            clear_test_runtime_release(organization_id)
            assert not thread.is_alive()


def test_autonomous_replacement_reclaim_is_all_old_then_all_new_over_sdk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    guarded_control_engine: Engine,
    guarded_scheduler_engine: Engine,
    guarded_worker_engine: Engine,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
    migration_configuration: DatabaseConfiguration,
) -> None:
    root = tmp_path / "replacement-reclaim"
    root.mkdir()
    path = root / "handbook.md"
    path.write_text("# Handbook\n\nOLD marker.\n", encoding="utf-8")
    provider_proofs, control_proofs = _proofs()
    organization_id = uuid4()
    receiver = FileImportReceiver(uuid4())
    control, authority, source = _seed_file_change_source(
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
        organization_id=organization_id,
        receiver=receiver,
        root_ref=FileRootRef("dispatch-root"),
        control_proofs=control_proofs,
    )
    source = _activate_delete_observations(control, authority, organization_id, source)
    provider = FileChangeProvider(
        FileRootRegistry(
            {source.source_version.root_ref: root},
            limits=FileReadLimits(max_file_bytes=4_096),
        ),
        proofs=provider_proofs,
    )
    initial = provider.read_changes(source, InitialScan(), ChangeLimit(1))
    assert type(initial) is ProviderOk
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-reclaim-old",
    ) as call:
        accepted_old = control.accept_file_change_page(call, initial.value)
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            membership_id, user_id = connection.execute(
                text(
                    "SELECT membership_id, user_id FROM membership WHERE "
                    "organization_id = :organization_id"
                ),
                {"organization_id": organization_id},
            ).one()
    finally:
        migration_engine.dispose()
    audience = FileImportAudience("principal:file-reader", membership_id, 1)
    with _authorize(
        authority,
        organization_id,
        ControlOperation.SCHEDULE_FILE_CHANGE_PAGE,
        "schedule-reclaim-old",
    ) as call:
        control.schedule_file_change_page(
            call,
            ScheduleFileChangePage(
                accepted_old.source_ref,
                accepted_old.source_version_ref,
                accepted_old.page_ref,
                audience,
            ),
        )
    codec = WorkerLeaseCodec(
        WorkerLeaseKeyring(active_version=1, keys={1: SIGNING_KEY})
    )
    dispatch = _dispatch_authority(guarded_scheduler_engine, codec)
    old_claim = dispatch.claim()
    assert type(old_claim) is FileDispatchLease
    roots = FileRootRegistry(
        {source.source_version.root_ref: root},
        limits=FileReadLimits(max_file_bytes=4_096),
    )
    old_result = PostgreSQLFileImportWorker(
        guarded_worker_engine,
        codec,
        receiver,
        roots,
        MarkdownCompilerConfig("markdown-config-v1"),
        clock=lambda: old_claim.issued_at,
    ).run(old_claim.redemption)
    with _authorize(
        authority,
        organization_id,
        ControlOperation.READ_SOURCE_PROGRESS,
        "read-reclaim-old",
    ) as call:
        progress = control.read_file_source_progress(call, accepted_old.source_ref)
    assert progress.complete_change_baseline is not None
    path.write_text("# Handbook\n\nNEW marker.\n", encoding="utf-8")
    changed = provider.read_changes(
        FileChangeSource(
            organization_id,
            source.source_version,
            scan_head=progress.change_scan_head,
            complete_baseline=progress.complete_change_baseline,
        ),
        InitialScan(),
        ChangeLimit(1),
    )
    assert type(changed) is ProviderOk
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-reclaim-new",
    ) as call:
        accepted_new = control.accept_file_change_page(call, changed.value)
    with _authorize(
        authority,
        organization_id,
        ControlOperation.SCHEDULE_FILE_CHANGE_PAGE,
        "schedule-reclaim-new",
    ) as call:
        scheduled_new = control.schedule_file_change_page(
            call,
            ScheduleFileChangePage(
                accepted_new.source_ref,
                accepted_new.source_version_ref,
                accepted_new.page_ref,
                audience,
            ),
        )
    new_claim = dispatch.claim()
    assert type(new_claim) is FileDispatchLease
    with pytest.raises(FileImportInterrupted):
        PostgreSQLFileImportWorker(
            guarded_worker_engine,
            codec,
            receiver,
            roots,
            MarkdownCompilerConfig("markdown-config-v1"),
            clock=lambda: new_claim.issued_at,
            interrupt_after=FilePublicationBoundary.INDEXED,
        ).run(new_claim.redemption)
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            pending_revision = connection.execute(
                text(
                    "SELECT CAST(revision_id AS text) FROM file_import_job WHERE "
                    "organization_id = :organization_id AND job_id = :job_id"
                ),
                {
                    "organization_id": organization_id,
                    "job_id": scheduled_new.changes[0].prepared_import.job_id,
                },
            ).scalar_one()
            candidate = connection.execute(
                text(
                    "SELECT source_ref, resource_ref FROM exact_phrase_candidate "
                    "WHERE organization_id = :organization_id AND "
                    "revision_id = :revision_id"
                ),
                {
                    "organization_id": organization_id,
                    "revision_id": pending_revision,
                },
            ).one()
    finally:
        migration_engine.dispose()
    ensure_test_runtime_release(
        organization_id,
        active_revision_refs=(
            old_result.candidate_refs[0].revision_ref,
            pending_revision,
        ),
    )
    consumer_root = tmp_path / "replacement-sdk-consumer"
    consumer_root.mkdir()
    _pack_and_install_resolve_sdk(consumer_root)
    application = create_app(
        authenticator=_RuntimeAuthenticator(
            organization_id,
            user_id,
            membership_id,
        ),
        organization_authority=_OrganizationAuthority(),
        membership_authority=PostgreSQLMembershipAuthority(guarded_runtime_engine),
        scope_authority=_ExactScopeAuthority(
            candidate.source_ref,
            candidate.resource_ref,
        ),
        runtime=Runtime(
            required_kernel_dependencies(),
            candidate_index=PostgreSQLExactPhraseCandidateIndex(),
            query_digest_keyring=query_digest_keyring,
        ),
    )
    port = _unused_port()
    server = Server(
        Config(
            application,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            lifespan="off",
        )
    )
    thread = Thread(target=server.run, daemon=True)
    thread.start()
    try:
        _wait_for_tcp(port)
        base_url = f"http://127.0.0.1:{port}"
        monkeypatch.setenv("CONTEXT_ENGINE_SDK_QUERY", "OLD marker.")
        old = _run_installed_empty_consumer(
            consumer_root,
            base_url=base_url,
        )
        monkeypatch.setenv("CONTEXT_ENGINE_SDK_QUERY", "NEW marker.")
        hidden_new = _run_installed_empty_consumer(
            consumer_root,
            base_url=base_url,
        )
        old_package = cast(dict[str, object], old["package"])
        assert len(cast(list[object], old_package["evidence"])) == 1
        assert cast(dict[str, object], hidden_new["package"])["evidence"] == []
        migration_engine = create_database_engine(migration_configuration)
        try:
            with migration_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE file_import_job SET lease_issued_at = "
                        "clock_timestamp() - interval '10 minutes', "
                        "lease_expires_at = "
                        "clock_timestamp() - interval '31 seconds' WHERE "
                        "organization_id = :organization_id AND job_id = :job_id"
                    ),
                    {
                        "organization_id": organization_id,
                        "job_id": scheduled_new.changes[0].prepared_import.job_id,
                    },
                )
        finally:
            migration_engine.dispose()
        completed = subprocess.run(
            ["context-engine-worker", "--dispatch-file-once"],
            env={
                **os.environ,
                "CONTEXT_ENGINE_WORKER_LEASE_SIGNING_KEY_HEX": SIGNING_KEY.hex(),
                "CONTEXT_ENGINE_WORKER_FILE_ROOTS_JSON": json.dumps(
                    {source.source_version.root_ref.value: str(root)}
                ),
            },
            check=True,
            capture_output=True,
            text=True,
        )
        assert json.loads(completed.stdout)["outcome"] == "dispatched"
        monkeypatch.setenv("CONTEXT_ENGINE_SDK_QUERY", "OLD marker.")
        hidden_old = _run_installed_empty_consumer(
            consumer_root,
            base_url=base_url,
        )
        monkeypatch.setenv("CONTEXT_ENGINE_SDK_QUERY", "NEW marker.")
        new = _run_installed_empty_consumer(
            consumer_root,
            base_url=base_url,
        )
        assert cast(dict[str, object], hidden_old["package"])["evidence"] == []
        new_package = cast(dict[str, object], new["package"])
        assert len(cast(list[object], new_package["evidence"])) == 1
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        clear_test_runtime_release(organization_id)
        roots.close()
        assert not thread.is_alive()


def test_dispatch_order_is_global_and_deterministic_across_organizations(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_scheduler_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    scheduled_jobs: list[tuple[UUID, UUID]] = []
    for name in (
        "sequence-first",
        "page-first",
        "change-first",
        "stable-first",
        "stable-last",
    ):
        root = tmp_path / name
        root.mkdir()
        (root / f"{name}.md").write_text(f"# {name}\n", encoding="utf-8")
        organization_id, job_id, _root_ref = _schedule_one(
            root=root,
            guarded_control_engine=guarded_control_engine,
            migration_configuration=migration_configuration,
        )
        scheduled_jobs.append((organization_id, job_id))
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE file_acquisition DROP CONSTRAINT "
                    "fk_file_acquisition_change_observation_exact"
                )
            )
            for table in (
                "file_source_acquisition_checkpoint",
                "file_source_change_page",
                "file_source_change",
                "file_acquisition",
            ):
                connection.execute(
                    text(f"ALTER TABLE {table} DISABLE TRIGGER {table}_immutable")
                )
            positions = {
                scheduled_jobs[0][1]: (10, 9, 9),
                scheduled_jobs[1][1]: (20, 1, 9),
                scheduled_jobs[2][1]: (20, 2, 1),
                scheduled_jobs[3][1]: (20, 2, 9),
                scheduled_jobs[4][1]: (20, 2, 9),
            }
            for job_id, (sequence, page_ordinal, change_ordinal) in positions.items():
                lineage = connection.execute(
                    text(
                        "SELECT acquisition.organization_id, acquisition.source_id, "
                        "acquisition.change_page_ref, acquisition.change_ordinal "
                        "FROM file_import_job AS job JOIN file_acquisition "
                        "AS acquisition "
                        "ON acquisition.organization_id = job.organization_id "
                        "AND acquisition.acquisition_id = job.acquisition_id "
                        "WHERE job.job_id = :job_id"
                    ),
                    {"job_id": job_id},
                ).one()
                connection.execute(
                    text(
                        "UPDATE file_source_acquisition_checkpoint SET "
                        "accepted_at = TIMESTAMPTZ '2026-07-25 12:00:00+00', "
                        "sequence = :sequence WHERE job_id = :job_id"
                    ),
                    {"job_id": job_id, "sequence": sequence},
                )
                connection.execute(
                    text(
                        "UPDATE file_source_change_page SET page_ordinal = :ordinal "
                        "WHERE organization_id = :organization_id "
                        "AND source_id = :source_id AND page_ref = :page_ref"
                    ),
                    {
                        "organization_id": lineage.organization_id,
                        "source_id": lineage.source_id,
                        "page_ref": lineage.change_page_ref,
                        "ordinal": page_ordinal,
                    },
                )
                connection.execute(
                    text(
                        "UPDATE file_source_change SET change_ordinal = :new_ordinal "
                        "WHERE organization_id = :organization_id "
                        "AND source_id = :source_id AND page_ref = :page_ref "
                        "AND change_ordinal = :old_ordinal"
                    ),
                    {
                        "organization_id": lineage.organization_id,
                        "source_id": lineage.source_id,
                        "page_ref": lineage.change_page_ref,
                        "old_ordinal": lineage.change_ordinal,
                        "new_ordinal": change_ordinal,
                    },
                )
                connection.execute(
                    text(
                        "UPDATE file_acquisition SET change_ordinal = :new_ordinal "
                        "WHERE organization_id = :organization_id "
                        "AND source_id = :source_id AND change_page_ref = :page_ref"
                    ),
                    {
                        "organization_id": lineage.organization_id,
                        "source_id": lineage.source_id,
                        "page_ref": lineage.change_page_ref,
                        "new_ordinal": change_ordinal,
                    },
                )
            connection.execute(
                text(
                    "ALTER TABLE file_acquisition ADD CONSTRAINT "
                    "fk_file_acquisition_change_observation_exact FOREIGN KEY ("
                    "organization_id, source_id, source_version_id, change_page_ref, "
                    "change_ordinal, relative_path, expected_content_sha256, "
                    "expected_content_length) REFERENCES file_source_change ("
                    "organization_id, source_id, source_version_id, page_ref, "
                    "change_ordinal, relative_path, content_sha256, content_length)"
                )
            )
            for table in (
                "file_source_acquisition_checkpoint",
                "file_source_change_page",
                "file_source_change",
                "file_acquisition",
            ):
                connection.execute(
                    text(f"ALTER TABLE {table} ENABLE TRIGGER {table}_immutable")
                )
    finally:
        migration_engine.dispose()
    authority = _dispatch_authority(guarded_scheduler_engine)

    claims = tuple(authority.claim() for _index in range(5))

    assert all(type(claim) is FileDispatchLease for claim in claims)
    stable_tie = sorted(scheduled_jobs[3:])
    expected = [
        scheduled_jobs[0][1],
        scheduled_jobs[1][1],
        scheduled_jobs[2][1],
        *(job_id for _organization_id, job_id in stable_tie),
    ]
    assert [
        claim.job_id for claim in claims if type(claim) is FileDispatchLease
    ] == expected


def test_post_claim_mint_failure_leaves_one_expiring_lease_and_zero_effect(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_scheduler_engine: Engine,
    migration_configuration: DatabaseConfiguration,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "crash-root"
    root.mkdir()
    (root / "crash.md").write_text("# Crash\n", encoding="utf-8")
    organization_id, job_id, _root_ref = _schedule_one(
        root=root,
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
    )
    codec = WorkerLeaseCodec(
        WorkerLeaseKeyring(active_version=1, keys={1: SIGNING_KEY})
    )
    monkeypatch.setattr(
        WorkerLeaseCodec,
        "mint",
        lambda _self, _claims: (_ for _ in ()).throw(RuntimeError("signing failed")),
    )
    authority = _dispatch_authority(guarded_scheduler_engine, codec)

    with pytest.raises(RuntimeError, match="signing failed"):
        authority.claim()

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            snapshot = connection.execute(
                text(
                    "SELECT job.state, job.lease_generation, job.dispatch_claimed, "
                    "job.lease_expires_at > job.lease_issued_at, "
                    "(SELECT count(*) FROM context_revision "
                    " WHERE organization_id = :organization_id), "
                    "(SELECT count(*) FROM exact_phrase_candidate "
                    " WHERE organization_id = :organization_id), "
                    "(SELECT count(*) FROM file_source_publish_watermark "
                    " WHERE organization_id = :organization_id) "
                    "FROM file_import_job AS job "
                    "WHERE job.organization_id = :organization_id "
                    "AND job.job_id = :job_id"
                ),
                {"organization_id": organization_id, "job_id": job_id},
            ).one()
    finally:
        migration_engine.dispose()
    assert tuple(snapshot) == ("leased", 1, True, True, 0, 0, 0)

    monkeypatch.undo()
    assert (
        _dispatch_authority(
            guarded_scheduler_engine,
            codec,
        ).claim()
        == FileDispatchNoWork()
    )


def test_claim_skips_authority_row_while_revocation_is_in_flight(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_scheduler_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    root = tmp_path / "revocation-race-root"
    root.mkdir()
    (root / "race.md").write_text("# Race\n", encoding="utf-8")
    organization_id, job_id, _root_ref = _schedule_one(
        root=root,
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
    )
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as revoker:
            transaction = revoker.begin()
            revoker.execute(
                text(
                    "UPDATE membership SET status = 'revoked' "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": organization_id},
            )
            authority = _dispatch_authority(guarded_scheduler_engine)
            assert authority.claim() == FileDispatchNoWork()
            transaction.commit()
        with migration_engine.connect() as connection:
            state = connection.execute(
                text(
                    "SELECT state, lease_generation, dispatch_claimed "
                    "FROM file_import_job WHERE organization_id = :organization_id "
                    "AND job_id = :job_id"
                ),
                {"organization_id": organization_id, "job_id": job_id},
            ).one()
    finally:
        migration_engine.dispose()
    assert tuple(state) == ("available", 0, False)


def test_claim_refreshes_latest_scan_after_waiting_for_source_progress(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_scheduler_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    root = tmp_path / "scan-race-root"
    root.mkdir()
    (root / "race.md").write_text("# Old scan\n", encoding="utf-8")
    organization_id, job_id, _root_ref = _schedule_one(
        root=root,
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
    )
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as accepter:
            transaction = accepter.begin()
            source_id, version_id = accepter.execute(
                text(
                    "SELECT source_id, active_version_id FROM context_source "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": organization_id},
            ).one()
            accepter.execute(
                text(
                    "SELECT pg_catalog.pg_advisory_xact_lock("
                    "pg_catalog.hashtextextended("
                    "'context-engine.file-source-progress:' || "
                    "CAST(:organization_id AS text) || ':' || "
                    "CAST(:source_id AS text), 0))"
                ),
                {"organization_id": organization_id, "source_id": source_id},
            )
            accepter.execute(
                text(
                    "SELECT pg_catalog.set_config('app.organization_id', "
                    "CAST(:organization_id AS text), true)"
                ),
                {"organization_id": organization_id},
            )

            with ThreadPoolExecutor(max_workers=1) as executor:
                pending_claim = executor.submit(
                    _dispatch_authority(guarded_scheduler_engine).claim
                )
                deadline = monotonic() + 10
                waiting = False
                while monotonic() < deadline:
                    with migration_engine.connect() as observer:
                        waiting = bool(
                            observer.execute(
                                text(
                                    "SELECT EXISTS (SELECT 1 FROM "
                                    "pg_catalog.pg_stat_activity AS activity JOIN "
                                    "pg_catalog.pg_locks AS held_lock ON "
                                    "held_lock.pid = activity.pid WHERE "
                                    "activity.usename = "
                                    "'context_engine_scheduler' AND "
                                    "held_lock.locktype = "
                                    "'advisory' AND held_lock.granted IS FALSE)"
                                )
                            ).scalar_one()
                        )
                    if waiting:
                        break
                    sleep(0.01)
                if not waiting:
                    pytest.fail("File dispatch did not wait for Source progress")

                latest = accepter.execute(
                    text(
                        "SELECT page.accepted_at, (SELECT max(sequence) FROM "
                        "file_source_acquisition_checkpoint WHERE "
                        "organization_id = :organization_id AND "
                        "source_id = :source_id) AS sequence FROM "
                        "file_source_change_page AS page JOIN "
                        "file_source_acquisition_checkpoint AS checkpoint ON "
                        "checkpoint.organization_id = page.organization_id AND "
                        "checkpoint.source_id = page.source_id AND "
                        "checkpoint.change_page_ref = page.page_ref WHERE "
                        "page.organization_id = :organization_id AND "
                        "page.source_id = :source_id ORDER BY "
                        "checkpoint.sequence DESC LIMIT 1"
                    ),
                    {"organization_id": organization_id, "source_id": source_id},
                ).one()
                page_ref = sha256(organization_id.bytes + b"scan-race-page").hexdigest()
                scan_ref = sha256(organization_id.bytes + b"scan-race-scan").hexdigest()
                accepter.execute(
                    text(
                        "INSERT INTO file_source_delete_observation_page ("
                        "organization_id, source_id, source_version_id, page_ref) "
                        "VALUES (:organization_id, :source_id, :version_id, :page_ref)"
                    ),
                    {
                        "organization_id": organization_id,
                        "source_id": source_id,
                        "version_id": version_id,
                        "page_ref": page_ref,
                    },
                )
                accepter.execute(
                    text(
                        "INSERT INTO file_source_change_page (organization_id, "
                        "source_id, source_version_id, page_ref, scan_ref, scan_epoch, "
                        "page_limit, "
                        "page_ordinal, change_count, complete, accepted_at) VALUES ("
                        ":organization_id, :source_id, :version_id, :page_ref, "
                        ":scan_ref, :scan_epoch, 1, 1, 0, true, "
                        ":accepted_at + interval '1 second')"
                    ),
                    {
                        "organization_id": organization_id,
                        "source_id": source_id,
                        "version_id": version_id,
                        "page_ref": page_ref,
                        "scan_ref": scan_ref,
                        "scan_epoch": uuid4(),
                        "accepted_at": latest.accepted_at,
                    },
                )
                accepter.execute(
                    text(
                        "INSERT INTO file_source_acquisition_checkpoint ("
                        "organization_id, source_id, sequence, checkpoint_ref, "
                        "change_kind, accepted_at, source_version_id, change_page_ref) "
                        "VALUES (:organization_id, :source_id, :sequence, "
                        ":checkpoint_ref, 'file_change_page', "
                        ":accepted_at + interval '1 second', :version_id, :page_ref)"
                    ),
                    {
                        "organization_id": organization_id,
                        "source_id": source_id,
                        "sequence": latest.sequence + 1,
                        "checkpoint_ref": "facp_"
                        + sha256(
                            organization_id.bytes + b"scan-race-checkpoint"
                        ).hexdigest(),
                        "accepted_at": latest.accepted_at,
                        "version_id": version_id,
                        "page_ref": page_ref,
                    },
                )
                transaction.commit()
                assert pending_claim.result(timeout=5) == FileDispatchNoWork()
        with migration_engine.connect() as connection:
            state = connection.execute(
                text(
                    "SELECT state, lease_generation, dispatch_claimed "
                    "FROM file_import_job WHERE organization_id = "
                    ":organization_id AND job_id = :job_id"
                ),
                {"organization_id": organization_id, "job_id": job_id},
            ).one()
    finally:
        migration_engine.dispose()
    assert tuple(state) == ("available", 0, False)


def test_file_reclaim_downgrade_waits_for_real_in_flight_scheduler(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_scheduler_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    """A real scheduler claim holds the 0034 fence until it finishes."""

    root = tmp_path / "reclaim-migration-fence"
    root.mkdir()
    (root / "fence.md").write_text("# Fence\n", encoding="utf-8")
    organization_id, job_id, _root_ref = _schedule_one(
        root=root,
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
    )
    migration_engine = create_database_engine(migration_configuration)
    alembic_configuration = AlembicConfig(ROOT / "alembic.ini")
    migration_fence_key = "context-engine.file-dispatch-migration-fence"
    try:
        with migration_engine.connect() as blocker:
            blocker_transaction = blocker.begin()
            source_id = blocker.execute(
                text(
                    "SELECT source_id FROM context_source WHERE "
                    "organization_id = :organization_id"
                ),
                {"organization_id": organization_id},
            ).scalar_one()
            source_lock_key = (
                f"context-engine.file-source-progress:{organization_id}:{source_id}"
            )
            blocker.execute(
                text(
                    "SELECT pg_catalog.pg_advisory_xact_lock("
                    "pg_catalog.hashtextextended(:source_lock_key, 0))"
                ),
                {"source_lock_key": source_lock_key},
            )
            with ThreadPoolExecutor(max_workers=2) as executor:
                pending_claim = executor.submit(
                    _dispatch_authority(guarded_scheduler_engine).claim
                )
                try:
                    with migration_engine.connect() as observer:
                        deadline = monotonic() + 10
                        scheduler_waiting = False
                        while monotonic() < deadline:
                            scheduler_waiting = observer.execute(
                                text(
                                    """
                                    SELECT EXISTS (
                                        SELECT 1
                                        FROM pg_catalog.pg_locks AS waiting
                                        JOIN pg_catalog.pg_locks AS held
                                          ON held.locktype = waiting.locktype
                                         AND held.database = waiting.database
                                         AND held.classid = waiting.classid
                                         AND held.objid = waiting.objid
                                         AND held.objsubid = waiting.objsubid
                                        WHERE waiting.locktype = 'advisory'
                                          AND waiting.mode = 'ExclusiveLock'
                                          AND waiting.granted IS FALSE
                                          AND waiting.database = (
                                            SELECT database.oid
                                            FROM pg_catalog.pg_database AS database
                                            WHERE database.datname =
                                              pg_catalog.current_database()
                                          )
                                          AND waiting.classid = (
                                            (pg_catalog.hashtextextended(
                                                :lock_key, 0
                                             ) >> 32) & 4294967295
                                          )::oid
                                          AND waiting.objid = (
                                            pg_catalog.hashtextextended(:lock_key, 0)
                                              & 4294967295
                                          )::oid
                                          AND waiting.objsubid = 1
                                          AND held.mode = 'ExclusiveLock'
                                          AND held.granted IS TRUE
                                    )
                                    """
                                ),
                                {"lock_key": source_lock_key},
                            ).scalar_one()
                            if scheduler_waiting:
                                break
                            sleep(0.01)
                    assert scheduler_waiting
                    pending_downgrade = executor.submit(
                        command.downgrade,
                        alembic_configuration,
                        "20260725_0033",
                    )
                    with migration_engine.connect() as observer:
                        deadline = monotonic() + 10
                        downgrade_waiting = False
                        while monotonic() < deadline:
                            downgrade_waiting = observer.execute(
                                text(
                                    """
                                    SELECT EXISTS (
                                        SELECT 1
                                        FROM pg_catalog.pg_locks AS waiting
                                        JOIN pg_catalog.pg_locks AS held
                                          ON held.locktype = waiting.locktype
                                         AND held.database = waiting.database
                                         AND held.classid = waiting.classid
                                         AND held.objid = waiting.objid
                                         AND held.objsubid = waiting.objsubid
                                        WHERE waiting.locktype = 'advisory'
                                          AND waiting.mode = 'ExclusiveLock'
                                          AND waiting.granted IS FALSE
                                          AND waiting.database = (
                                            SELECT database.oid
                                            FROM pg_catalog.pg_database AS database
                                            WHERE database.datname =
                                              pg_catalog.current_database()
                                          )
                                          AND waiting.classid = (
                                            (pg_catalog.hashtextextended(
                                                :lock_key, 0
                                             ) >> 32) & 4294967295
                                          )::oid
                                          AND waiting.objid = (
                                            pg_catalog.hashtextextended(:lock_key, 0)
                                              & 4294967295
                                          )::oid
                                          AND waiting.objsubid = 1
                                          AND held.mode = 'ShareLock'
                                          AND held.granted IS TRUE
                                    )
                                    """
                                ),
                                {"lock_key": migration_fence_key},
                            ).scalar_one()
                            if downgrade_waiting:
                                break
                            sleep(0.01)
                    assert downgrade_waiting
                finally:
                    blocker_transaction.commit()
                assert type(pending_claim.result(timeout=10)) is FileDispatchLease
                pending_downgrade.result(timeout=10)
        with migration_engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT lease_generation, dispatch_claimed FROM "
                    "file_import_job WHERE organization_id = :organization_id "
                    "AND job_id = :job_id"
                ),
                {"organization_id": organization_id, "job_id": job_id},
            ).one() == (1, True)
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == "20260725_0033"
            )
    finally:
        command.upgrade(alembic_configuration, "head")
        with migration_engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == HEAD_REVISION
            )
        migration_engine.dispose()


def test_claim_refreshes_membership_expiry_after_waiting_for_source_progress(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_scheduler_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    root = tmp_path / "expiry-race-root"
    root.mkdir()
    (root / "expiry.md").write_text("# Expiry\n", encoding="utf-8")
    organization_id, job_id, _root_ref = _schedule_one(
        root=root,
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
    )
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as blocker:
            transaction = blocker.begin()
            source_id = blocker.execute(
                text(
                    "SELECT source_id FROM context_source "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": organization_id},
            ).scalar_one()
            blocker.execute(
                text(
                    "UPDATE membership SET valid_until = "
                    "pg_catalog.clock_timestamp() + interval '250 milliseconds' "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": organization_id},
            )
            blocker.execute(
                text(
                    "SELECT pg_catalog.pg_advisory_xact_lock("
                    "pg_catalog.hashtextextended("
                    "'context-engine.file-source-progress:' || "
                    "CAST(:organization_id AS text) || ':' || "
                    "CAST(:source_id AS text), 0))"
                ),
                {"organization_id": organization_id, "source_id": source_id},
            )
            transaction.commit()

        with migration_engine.connect() as blocker:
            transaction = blocker.begin()
            blocker.execute(
                text(
                    "SELECT pg_catalog.pg_advisory_xact_lock("
                    "pg_catalog.hashtextextended("
                    "'context-engine.file-source-progress:' || "
                    "CAST(:organization_id AS text) || ':' || "
                    "CAST(:source_id AS text), 0))"
                ),
                {"organization_id": organization_id, "source_id": source_id},
            )
            with ThreadPoolExecutor(max_workers=1) as executor:
                pending_claim = executor.submit(
                    _dispatch_authority(guarded_scheduler_engine).claim
                )
                deadline = monotonic() + 5
                waiting = False
                while monotonic() < deadline:
                    with migration_engine.connect() as observer:
                        waiting = bool(
                            observer.execute(
                                text(
                                    "SELECT EXISTS (SELECT 1 FROM "
                                    "pg_catalog.pg_stat_activity AS activity JOIN "
                                    "pg_catalog.pg_locks AS held_lock ON "
                                    "held_lock.pid = activity.pid WHERE "
                                    "activity.usename = "
                                    "'context_engine_scheduler' AND "
                                    "held_lock.locktype = "
                                    "'advisory' AND held_lock.granted IS FALSE)"
                                )
                            ).scalar_one()
                        )
                    if waiting:
                        break
                    sleep(0.01)
                if not waiting:
                    pytest.fail("File dispatch did not wait for Source progress")
                sleep(0.4)
                transaction.commit()
                assert pending_claim.result(timeout=5) == FileDispatchNoWork()
        with migration_engine.connect() as connection:
            state = connection.execute(
                text(
                    "SELECT state, lease_generation, dispatch_claimed "
                    "FROM file_import_job WHERE organization_id = :organization_id "
                    "AND job_id = :job_id"
                ),
                {"organization_id": organization_id, "job_id": job_id},
            ).one()
    finally:
        migration_engine.dispose()
    assert tuple(state) == ("available", 0, False)


def test_autonomous_dispatch_projects_mixed_upserts_but_never_delete_effects(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_scheduler_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    root = tmp_path / "mixed-dispatch-root"
    root.mkdir()
    for path in ("a.md", "b.md", "c.md"):
        (root / path).write_text(f"# {path}\n", encoding="utf-8")
    provider_proofs, control_proofs = _proofs()
    organization_id = uuid4()
    receiver = FileImportReceiver(uuid4())
    control, authority, source = _seed_file_change_source(
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
        organization_id=organization_id,
        receiver=receiver,
        root_ref=FileRootRef("dispatch-root"),
        control_proofs=control_proofs,
    )
    source = _activate_delete_observations(control, authority, organization_id, source)
    provider = FileChangeProvider(
        FileRootRegistry(
            {source.source_version.root_ref: root},
            limits=FileReadLimits(max_file_bytes=4_096),
        ),
        proofs=provider_proofs,
    )
    baseline = provider.read_changes(source, InitialScan(), ChangeLimit(3))
    assert type(baseline) is ProviderOk
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-dispatch-mixed-baseline",
    ) as call:
        accepted = control.accept_file_change_page(call, baseline.value)
    with _authorize(
        authority,
        organization_id,
        ControlOperation.READ_SOURCE_PROGRESS,
        "read-dispatch-mixed-baseline",
    ) as call:
        progress = control.read_file_source_progress(call, accepted.source_ref)
    assert progress.complete_change_baseline is not None
    (root / "a.md").write_text("# changed a\n", encoding="utf-8")
    (root / "b.md").unlink()
    (root / "c.md").write_text("# changed c\n", encoding="utf-8")
    mixed = provider.read_changes(
        FileChangeSource(
            organization_id,
            source.source_version,
            scan_head=progress.change_scan_head,
            complete_baseline=progress.complete_change_baseline,
        ),
        InitialScan(),
        ChangeLimit(3),
    )
    assert type(mixed) is ProviderOk
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-dispatch-current-mixed",
    ) as call:
        accepted_mixed = control.accept_file_change_page(call, mixed.value)
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            membership_id = connection.execute(
                text(
                    "SELECT membership_id FROM membership "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": organization_id},
            ).scalar_one()
    finally:
        migration_engine.dispose()
    with _authorize(
        authority,
        organization_id,
        ControlOperation.SCHEDULE_FILE_CHANGE_PAGE,
        "schedule-dispatch-current-mixed",
    ) as call:
        scheduled = control.schedule_file_change_page(
            call,
            ScheduleFileChangePage(
                accepted_mixed.source_ref,
                accepted_mixed.source_version_ref,
                accepted_mixed.page_ref,
                FileImportAudience("principal:file-reader", membership_id, 1),
            ),
        )
    assert [change.ordinal for change in scheduled.changes] == [1, 3]
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            before = _delete_observation_effect_snapshot(connection, organization_id)
        dispatch = _dispatch_authority(guarded_scheduler_engine)
        assert type(dispatch.claim()) is FileDispatchLease
        assert type(dispatch.claim()) is FileDispatchLease
        assert dispatch.claim() == FileDispatchNoWork()
        with migration_engine.connect() as connection:
            after = _delete_observation_effect_snapshot(connection, organization_id)
            states = connection.execute(
                text(
                    "SELECT job.state, job.lease_generation FROM file_import_job "
                    "AS job JOIN file_acquisition AS acquisition ON "
                    "acquisition.organization_id = job.organization_id AND "
                    "acquisition.acquisition_id = job.acquisition_id WHERE "
                    "job.organization_id = :organization_id AND "
                    "acquisition.change_page_ref = :page_ref ORDER BY job.job_id"
                ),
                {
                    "organization_id": organization_id,
                    "page_ref": accepted_mixed.page_ref,
                },
            ).all()
    finally:
        migration_engine.dispose()
    assert after == before
    assert [tuple(state) for state in states] == [("leased", 1), ("leased", 1)]


@pytest.mark.security_evidence(id="PROC-FILE-DISPATCH-091", layer="runtime")
def test_independent_worker_process_dispatches_and_publishes_one_job(
    tmp_path: Path,
    guarded_control_engine: Engine,
    migration_configuration: DatabaseConfiguration,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
) -> None:
    root = tmp_path / "process-root"
    root.mkdir()
    (root / "process.md").write_text(
        "# Process\n\nContextEngine delivers context.\n", encoding="utf-8"
    )
    organization_id, job_id, root_ref = _schedule_one(
        root=root,
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
    )

    completed = subprocess.run(
        ["context-engine-worker", "--dispatch-file-once"],
        env={
            **os.environ,
            "CONTEXT_ENGINE_WORKER_LEASE_SIGNING_KEY_HEX": SIGNING_KEY.hex(),
            "CONTEXT_ENGINE_WORKER_FILE_ROOTS_JSON": json.dumps(
                {root_ref.value: str(root)}
            ),
        },
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == {
        "dispatch": "file.import",
        "outcome": "dispatched",
        "service": "context-engine-worker",
        "status": "complete",
    }
    forbidden = (str(root), str(organization_id), str(job_id), SIGNING_KEY.hex())
    assert all(value not in completed.stdout for value in forbidden)
    no_work = subprocess.run(
        ["context-engine-worker", "--dispatch-file-once"],
        env={
            **os.environ,
            "CONTEXT_ENGINE_WORKER_LEASE_SIGNING_KEY_HEX": SIGNING_KEY.hex(),
            "CONTEXT_ENGINE_WORKER_FILE_ROOTS_JSON": json.dumps(
                {root_ref.value: str(root)}
            ),
        },
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(no_work.stdout) == {
        "dispatch": "file.import",
        "outcome": "no_work",
        "service": "context-engine-worker",
        "status": "complete",
    }
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            state = connection.execute(
                text(
                    "SELECT state, effect_count, dispatch_claimed "
                    "FROM file_import_job WHERE organization_id = :organization_id "
                    "AND job_id = :job_id"
                ),
                {"organization_id": organization_id, "job_id": job_id},
            ).one()
            candidate = connection.execute(
                text(
                    "SELECT source_ref, resource_ref, "
                    "CAST(revision_id AS text) AS revision_ref "
                    "FROM exact_phrase_candidate "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": organization_id},
            ).one()
            membership_id, user_id = connection.execute(
                text(
                    "SELECT membership_id, user_id FROM membership "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": organization_id},
            ).one()
    finally:
        migration_engine.dispose()
    assert tuple(state) == ("completed", 1, True)

    ensure_test_runtime_release(
        organization_id,
        active_revision_refs=(candidate.revision_ref,),
    )
    consumer_root = tmp_path / "dispatch-sdk-consumer"
    consumer_root.mkdir()
    _pack_and_install_resolve_sdk(consumer_root)

    def resolve_for(
        requested_organization_id: UUID,
        requested_user_id: UUID,
        requested_membership_id: UUID,
    ) -> dict[str, object]:
        application = create_app(
            authenticator=_RuntimeAuthenticator(
                requested_organization_id,
                requested_user_id,
                requested_membership_id,
            ),
            organization_authority=_OrganizationAuthority(),
            membership_authority=PostgreSQLMembershipAuthority(guarded_runtime_engine),
            scope_authority=_ExactScopeAuthority(
                candidate.source_ref,
                candidate.resource_ref,
            ),
            runtime=Runtime(
                required_kernel_dependencies(),
                candidate_index=PostgreSQLExactPhraseCandidateIndex(),
                query_digest_keyring=query_digest_keyring,
            ),
        )
        port = _unused_port()
        server = Server(
            Config(
                application,
                host="127.0.0.1",
                port=port,
                log_level="warning",
                lifespan="off",
            )
        )
        thread = Thread(target=server.run, daemon=True)
        thread.start()
        try:
            _wait_for_tcp(port)
            return _run_installed_empty_consumer(
                consumer_root,
                base_url=f"http://127.0.0.1:{port}",
            )
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive()

    try:
        authorized = resolve_for(organization_id, user_id, membership_id)
        authorized_package = cast(dict[str, object], authorized["package"])
        authorized_blocks = cast(list[dict[str, object]], authorized_package["blocks"])
        assert authorized_blocks[0]["text"] == ("ContextEngine delivers context.")

        other_root = tmp_path / "other-root"
        other_root.mkdir()
        (other_root / "other.md").write_text("# Other\n", encoding="utf-8")
        other_organization_id, _other_job_id, _other_root_ref = _schedule_one(
            root=other_root,
            guarded_control_engine=guarded_control_engine,
            migration_configuration=migration_configuration,
        )
        other_engine = create_database_engine(migration_configuration)
        try:
            with other_engine.connect() as connection:
                other_membership_id, other_user_id = connection.execute(
                    text(
                        "SELECT membership_id, user_id FROM membership "
                        "WHERE organization_id = :organization_id"
                    ),
                    {"organization_id": other_organization_id},
                ).one()
        finally:
            other_engine.dispose()
        ensure_test_runtime_release(other_organization_id)
        try:
            denied = resolve_for(
                other_organization_id,
                other_user_id,
                other_membership_id,
            )
            denied_package = cast(dict[str, object], denied["package"])
            assert denied_package["blocks"] == []
            assert denied_package["evidence"] == []
        finally:
            clear_test_runtime_release(other_organization_id)
    finally:
        clear_test_runtime_release(organization_id)


def test_long_running_dispatch_process_exits_cleanly_on_sigterm(
    tmp_path: Path,
) -> None:
    root = tmp_path / "empty-process-root"
    root.mkdir()
    process = subprocess.Popen(
        ["context-engine-worker", "--dispatch-files"],
        env={
            **os.environ,
            "CONTEXT_ENGINE_WORKER_LEASE_SIGNING_KEY_HEX": SIGNING_KEY.hex(),
            "CONTEXT_ENGINE_WORKER_FILE_ROOTS_JSON": json.dumps(
                {"empty-process-root": str(root)}
            ),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    stdout_lines: queue.Queue[str] = queue.Queue()
    stderr_lines: queue.Queue[str] = queue.Queue()
    stdout_reader = Thread(
        target=_drain_text_stream,
        args=(process.stdout, stdout_lines),
        daemon=True,
    )
    stderr_reader = Thread(
        target=_drain_text_stream,
        args=(process.stderr, stderr_lines),
        daemon=True,
    )
    stdout_reader.start()
    stderr_reader.start()
    try:
        assert json.loads(stdout_lines.get(timeout=10)) == {
            "dispatch": "file.import",
            "service": "context-engine-worker",
            "status": "ready",
        }
        assert json.loads(stdout_lines.get(timeout=10)) == {
            "dispatch": "file.import",
            "outcome": "no_work",
            "service": "context-engine-worker",
            "status": "complete",
        }
        process.terminate()
        process.wait(timeout=3)
        stdout_reader.join(timeout=1)
        stderr_reader.join(timeout=1)
        assert process.returncode == 0
        assert not stdout_reader.is_alive()
        assert not stderr_reader.is_alive()
        assert stderr_lines.empty()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
