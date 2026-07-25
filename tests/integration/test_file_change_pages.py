from __future__ import annotations

import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, event, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from adapters.exact_phrase import PostgreSQLExactPhraseCandidateIndex
from adapters.file_source import FileChangeProvider, FileReadLimits, FileRootRegistry
from adapters.http.app import create_app
from adapters.parsers.markdown import compile_markdown as compile_markdown_original
from engine.control import (
    MAX_FILE_CHANGE_BASELINE_SIZE,
    ActivateFileChangeFeed,
    ActivateFileDeleteObservations,
    ChangeLimit,
    ContextControl,
    ControlOperation,
    ControlOperatorAuthority,
    ExecuteFileDeleteObservation,
    FileChangeBaseline,
    FileChangeBaselineEntry,
    FileChangeBaselineRef,
    FileChangeControlProofs,
    FileChangeKind,
    FileChangeProviderProofs,
    FileChangeSource,
    FileImportAudience,
    FileImportPath,
    FileImportReceiver,
    FileRootRef,
    FileSourceChangeKind,
    FileSourceOffboarding,
    FileSourceProgress,
    InitialScan,
    OffboardFileSource,
    PrepareFileImport,
    ProviderGenericDenied,
    ProviderInvalidCheckpoint,
    ProviderOk,
    RegisterFileSource,
    ScheduleFileChangePage,
    SourceControlUnavailable,
    SourceNotAvailable,
    SourceRef,
    TombstoneFileResource,
    TrustedControlCall,
    VerifiedControlOperatorIdentity,
)
from engine.persistence import (
    DatabaseConfiguration,
    FileImportLeaseRedemption,
    FileImportUnavailable,
    PostgreSQLControlStore,
    PostgreSQLFileImportWorker,
    PostgreSQLMembershipAuthority,
    PostgreSQLWorkerLeaseIssuer,
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
from tests.integration.test_file_import_tracer import (
    _ExactScopeAuthority,
    _OrganizationAuthority,
    _RuntimeAuthenticator,
)
from tests.support.migrations import HEAD_REVISION
from tests.support.releases import (
    clear_test_runtime_release,
    ensure_test_runtime_release,
)

pytestmark = pytest.mark.integration
ROOT = Path(__file__).parents[2]
NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
PROVIDER_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
CHECKPOINT_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32, 64)))


@pytest.fixture(autouse=True)
def cleanup_file_change_scenarios(
    migration_configuration: DatabaseConfiguration,
) -> Iterator[None]:
    """Remove only organizations created by this test module."""

    created_scenarios: list[tuple[UUID, UUID]] = []

    _SCENARIOS.append(created_scenarios)
    try:
        yield
    finally:
        _SCENARIOS.remove(created_scenarios)
        _delete_scenarios(migration_configuration, created_scenarios)


_SCENARIOS: list[list[tuple[UUID, UUID]]] = []


def _record_scenario(organization_id: UUID, user_id: UUID) -> None:
    if not _SCENARIOS:
        raise AssertionError("File change scenario cleanup fixture is unavailable")
    _SCENARIOS[-1].append((organization_id, user_id))


def _delete_scenarios(
    configuration: DatabaseConfiguration,
    scenarios: list[tuple[UUID, UUID]],
) -> None:
    if not scenarios:
        return

    engine = create_database_engine(configuration)
    immutable_tables = (
        (
            "file_delete_observation_execution",
            "file_delete_observation_execution_immutable",
        ),
        ("file_source_delete_observation_page", None),
        ("file_source_change", "file_source_change_immutable"),
        ("file_source_change_page", "file_source_change_page_immutable"),
        ("file_source_cleanup_intent", "file_source_cleanup_intent_immutable"),
        (
            "file_source_publish_watermark",
            "file_source_publish_watermark_immutable",
        ),
        (
            "file_source_acquisition_checkpoint",
            "file_source_acquisition_checkpoint_immutable",
        ),
        ("file_resource_cleanup_intent", "file_resource_cleanup_intent_immutable"),
        ("file_import_job_event", "file_import_job_event_immutable"),
        ("file_revision_supersession", "file_revision_supersession_immutable"),
        (
            "file_revision_replacement_plan",
            "file_revision_replacement_plan_immutable",
        ),
        ("exact_phrase_candidate", "exact_phrase_candidate_immutable"),
        ("revision_publication_event", "revision_publication_event_immutable"),
        ("context_fragment", "context_fragment_reject_mutation"),
        ("file_revision_snapshot", "file_revision_snapshot_immutable"),
        ("context_revision", "context_revision_reject_mutation"),
        ("file_acquisition_result", "file_acquisition_result_immutable"),
        (
            "file_resource_ingestion_guard",
            "file_resource_ingestion_guard_immutable",
        ),
        ("file_acquisition", "file_acquisition_immutable"),
        ("source_version", "source_version_immutable"),
    )
    try:
        with engine.begin() as connection:
            for table, trigger in immutable_tables:
                if trigger is not None:
                    connection.execute(
                        text(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}")
                    )
        try:
            with engine.begin() as connection:
                for organization_id, user_id in scenarios:
                    for table in (
                        "file_delete_observation_execution",
                        "file_source_publish_watermark",
                        "file_source_acquisition_checkpoint",
                        "file_import_job_event",
                        "file_publication_recovery",
                        "file_revision_supersession",
                        "file_revision_replacement_plan",
                        "file_acquisition_result",
                        "exact_phrase_candidate",
                        "revision_publication_event",
                        "membership_resource_field_right",
                        "resource_access_policy",
                        "context_fragment",
                        "file_revision_snapshot",
                        "file_resource_cleanup_intent",
                        "file_import_job",
                        "file_source_cleanup_intent",
                        "context_revision",
                        "context_resource",
                        "file_resource_ingestion_guard",
                        "file_acquisition",
                        "file_source_delete_observation_page",
                        "file_source_change",
                        "file_source_change_page",
                        "context_source",
                        "source_version",
                        "organization_policy_epoch",
                        "service_principal",
                        "membership",
                    ):
                        connection.execute(
                            text(
                                f"DELETE FROM {table} "  # noqa: S608
                                "WHERE organization_id = :organization_id"
                            ),
                            {"organization_id": organization_id},
                        )
                    connection.execute(
                        text(
                            "DELETE FROM organization "
                            "WHERE organization_id = :organization_id"
                        ),
                        {"organization_id": organization_id},
                    )
                    connection.execute(
                        text(
                            "DELETE FROM user_account WHERE user_id = :user_id "
                            "AND NOT EXISTS (SELECT 1 FROM membership "
                            "WHERE membership.user_id = user_account.user_id)"
                        ),
                        {"user_id": user_id},
                    )
        finally:
            with engine.begin() as connection:
                for table, trigger in reversed(immutable_tables):
                    if trigger is not None:
                        connection.execute(
                            text(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}")
                        )
    finally:
        engine.dispose()


class _Authenticator:
    def __init__(self, organization_id: UUID) -> None:
        self.organization_id = organization_id

    def authenticate(self, opaque_credential: str) -> VerifiedControlOperatorIdentity:
        if opaque_credential != f"credential:{self.organization_id}":
            raise AssertionError("unexpected credential")
        return VerifiedControlOperatorIdentity(
            organization_id=self.organization_id,
            operator_ref=f"operator:{self.organization_id}",
            authentication_binding_ref=f"binding:{self.organization_id}",
            authority_ref=f"authority:{self.organization_id}",
            allowed_operations=frozenset(
                {
                    ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
                    ControlOperation.ACTIVATE_FILE_CHANGE_FEED,
                    ControlOperation.ACTIVATE_FILE_DELETE_OBSERVATIONS,
                    ControlOperation.IMPORT_FILE,
                    ControlOperation.EXECUTE_FILE_DELETE_OBSERVATION,
                    ControlOperation.OFFBOARD_FILE_SOURCE,
                    ControlOperation.REGISTER_SOURCE,
                    ControlOperation.READ_SOURCE,
                    ControlOperation.READ_SOURCE_PROGRESS,
                    ControlOperation.SCHEDULE_FILE_CHANGE_PAGE,
                    ControlOperation.TOMBSTONE_FILE_RESOURCE,
                }
            ),
            valid_from=NOW - timedelta(minutes=1),
            expires_at=NOW + timedelta(hours=1),
        )


def _proofs() -> tuple[FileChangeProviderProofs, FileChangeControlProofs]:
    return (
        FileChangeProviderProofs(
            provider_signing_key=PROVIDER_KEY,
            checkpoint_verification_key=CHECKPOINT_KEY.public_key(),
        ),
        FileChangeControlProofs(
            provider_verification_key=PROVIDER_KEY.public_key(),
        ),
    )


def _authorize(
    authority: ControlOperatorAuthority,
    organization_id: UUID,
    operation: ControlOperation,
    request_id: str,
) -> AbstractContextManager[TrustedControlCall]:
    return authority.authorize(
        opaque_credential=f"credential:{organization_id}",
        operation=operation,
        request_id=request_id,
    )


def _seed_file_change_source(
    *,
    guarded_control_engine: Engine,
    migration_configuration: DatabaseConfiguration,
    organization_id: UUID,
    receiver: FileImportReceiver,
    root_ref: FileRootRef,
    control_proofs: FileChangeControlProofs,
) -> tuple[ContextControl, ControlOperatorAuthority, FileChangeSource]:
    user_id, membership_id = uuid4(), uuid4()
    _record_scenario(organization_id, user_id)
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text("INSERT INTO organization (organization_id) VALUES (:org)"),
                {"org": organization_id},
            )
            connection.execute(
                text("INSERT INTO user_account (user_id) VALUES (:user)"),
                {"user": user_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO membership (
                        organization_id, membership_id, user_id, status,
                        membership_version, valid_from
                    ) VALUES (:org, :membership, :user, 'active', 1, :now)
                    """
                ),
                {
                    "org": organization_id,
                    "membership": membership_id,
                    "user": user_id,
                    "now": NOW - timedelta(days=1),
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO service_principal (
                        organization_id, service_principal_id, workload,
                        worker_audience, operation, enabled
                    ) VALUES (:org, :receiver, 'supply.file-import',
                        'context-engine-worker', 'file.import', true)
                    """
                ),
                {"org": organization_id, "receiver": receiver.service_principal_id},
            )
    finally:
        migration_engine.dispose()
    authority = ControlOperatorAuthority(
        _Authenticator(organization_id),
        call_ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    )
    control = ContextControl(
        store=PostgreSQLControlStore(
            guarded_control_engine,
            clock=lambda: NOW,
            file_import_receiver=receiver,
            file_change_checkpoint_signing_key=CHECKPOINT_KEY,
        ),
        authority=authority,
        clock=lambda: NOW,
        file_change_proofs=control_proofs,
    )
    with _authorize(
        authority, organization_id, ControlOperation.REGISTER_SOURCE, "register"
    ) as call:
        registered = control.register_source(
            call,
            RegisterFileSource("Handbook", root_ref, "change-page-source"),
        )
    with _authorize(
        authority, organization_id, ControlOperation.IMPORT_FILE, "activate-v2"
    ) as call:
        control.prepare_file_import(
            call,
            PrepareFileImport(
                source_ref=registered.source_ref,
                path=FileImportPath("bootstrap.md"),
                audience=FileImportAudience(
                    principal_ref="principal:file-reader",
                    membership_id=membership_id,
                    membership_version=1,
                ),
                idempotency_key="activate-v2",
            ),
        )
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACTIVATE_FILE_CHANGE_FEED,
        "activate-v3",
    ) as call:
        activated = control.activate_file_change_feed(
            call, ActivateFileChangeFeed(registered.source_ref)
        )
    return (
        control,
        authority,
        FileChangeSource(organization_id, activated.active_version),
    )


def _activate_delete_observations(
    control: ContextControl,
    authority: ControlOperatorAuthority,
    organization_id: UUID,
    source: FileChangeSource,
) -> FileChangeSource:
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACTIVATE_FILE_DELETE_OBSERVATIONS,
        "activate-v4-delete-observations",
    ) as call:
        activated = control.activate_file_delete_observations(
            call,
            ActivateFileDeleteObservations(
                source.source_version.source_ref,
            ),
        )
    return FileChangeSource(organization_id, activated.active_version)


def _delete_observation_effect_snapshot(
    connection: Connection,
    organization_id: UUID,
) -> tuple[object, ...]:
    """Read every durable surface that delete-page acceptance must not affect."""

    row = connection.execute(
        text(
            """
            SELECT
              (SELECT count(*) FROM file_acquisition
               WHERE organization_id = :organization_id),
              (SELECT count(*) FROM file_import_job
               WHERE organization_id = :organization_id),
              (SELECT policy_epoch FROM organization_policy_epoch
               WHERE organization_id = :organization_id),
              (SELECT count(*) FROM file_resource_cleanup_intent
               WHERE organization_id = :organization_id),
              (SELECT count(*) FROM file_source_cleanup_intent
               WHERE organization_id = :organization_id),
              (SELECT count(*) FROM file_source_publish_watermark
               WHERE organization_id = :organization_id),
              (SELECT count(*) FROM context_revision
               WHERE organization_id = :organization_id),
              (SELECT count(*) FROM exact_phrase_candidate
               WHERE organization_id = :organization_id),
              (SELECT count(*) FROM context_run
               WHERE organization_id = :organization_id),
              (SELECT count(*) FROM context_resource
               WHERE organization_id = :organization_id
                 AND tombstoned IS TRUE)
            """
        ),
        {"organization_id": organization_id},
    ).one()
    return tuple(row)


def _file_delete_execution_effect_snapshot(
    connection: Connection,
    organization_id: UUID,
) -> tuple[object, ...]:
    row = connection.execute(
        text(
            """
            SELECT
              (SELECT policy_epoch FROM organization_policy_epoch
               WHERE organization_id = :organization_id),
              (SELECT count(*) FROM file_resource_cleanup_intent
               WHERE organization_id = :organization_id),
              (SELECT count(*) FROM file_delete_observation_execution
               WHERE organization_id = :organization_id),
              (SELECT count(*) FROM context_resource
               WHERE organization_id = :organization_id
                 AND tombstoned IS TRUE),
              (SELECT count(*) FROM context_revision
               WHERE organization_id = :organization_id),
              (SELECT count(*) FROM exact_phrase_candidate
               WHERE organization_id = :organization_id),
              (SELECT count(*) FROM file_source_publish_watermark
               WHERE organization_id = :organization_id)
            """
        ),
        {"organization_id": organization_id},
    ).one()
    return tuple(row)


@pytest.mark.security_evidence(id="PG-FILE-DELETE-EXECUTE-087", layer="postgres")
@pytest.mark.security_evidence(id="PG-FILE-DELETE-REPLAY-087", layer="postgres")
def test_control_executes_a_nonterminal_current_delete_observation(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.md").write_bytes(b"# A\n\nDelete this paragraph.\n")
    (root / "b.md").write_bytes(b"# B\n\nRetain this paragraph.\n")
    provider_proofs, control_proofs = _proofs()
    organization_id = uuid4()
    receiver = FileImportReceiver(uuid4())
    control, authority, source = _seed_file_change_source(
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
        organization_id=organization_id,
        receiver=receiver,
        root_ref=FileRootRef("delete-execution-root"),
        control_proofs=control_proofs,
    )
    stale_source_version_ref = source.source_version.version_ref
    source = _activate_delete_observations(
        control,
        authority,
        organization_id,
        source,
    )
    provider = FileChangeProvider(
        FileRootRegistry(
            {source.source_version.root_ref: root},
            limits=FileReadLimits(max_file_bytes=1_024),
        ),
        proofs=provider_proofs,
    )

    initial_first = provider.read_changes(source, InitialScan(), ChangeLimit(1))
    assert type(initial_first) is ProviderOk
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-delete-execution-baseline-first",
    ) as call:
        accepted_initial_first = control.accept_file_change_page(
            call,
            initial_first.value,
        )
    assert accepted_initial_first.next_cursor is not None
    source = replace(source, scan_head=accepted_initial_first.scan_head)
    initial_second = provider.read_changes(
        source,
        accepted_initial_first.next_cursor,
        ChangeLimit(1),
    )
    assert type(initial_second) is ProviderOk
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-delete-execution-baseline-second",
    ) as call:
        accepted_initial_second = control.accept_file_change_page(
            call,
            initial_second.value,
        )
    assert accepted_initial_second.complete is True

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
    scheduled_pages = []
    for request_id, accepted_page in (
        ("schedule-delete-execution-resource-a", accepted_initial_first),
        ("schedule-delete-execution-resource-b", accepted_initial_second),
    ):
        with _authorize(
            authority,
            organization_id,
            ControlOperation.SCHEDULE_FILE_CHANGE_PAGE,
            request_id,
        ) as call:
            scheduled_pages.append(
                control.schedule_file_change_page(
                    call,
                    ScheduleFileChangePage(
                        accepted_page.source_ref,
                        accepted_page.source_version_ref,
                        accepted_page.page_ref,
                        FileImportAudience("principal:file-reader", membership_id, 1),
                    ),
                )
            )
    prepared_imports = [page.changes[0].prepared_import for page in scheduled_pages]
    codec = WorkerLeaseCodec(
        WorkerLeaseKeyring(active_version=1, keys={1: bytes(range(64, 96))})
    )
    lease_issuer = PostgreSQLWorkerLeaseIssuer(
        guarded_control_engine,
        codec,
        lease_ttl_seconds=300,
    )
    tokens = [
        lease_issuer.issue_file_import_lease(prepared)
        for prepared in prepared_imports
    ]
    with FileRootRegistry(
        {source.source_version.root_ref: root},
        limits=FileReadLimits(max_file_bytes=1_024),
    ) as worker_roots:
        worker = PostgreSQLFileImportWorker(
            guarded_worker_engine,
            codec,
            receiver,
            worker_roots,
            MarkdownCompilerConfig("markdown-config-v1"),
            clock=lambda: datetime.now(UTC).replace(microsecond=0),
        )
        publications = [
            worker.run(
                FileImportLeaseRedemption(
                    token,
                    organization_id,
                    prepared.job_id,
                    accepted_initial_first.source_ref,
                )
            )
            for token, prepared in zip(tokens, prepared_imports, strict=True)
        ]
    assert [publication.outcome for publication in publications] == [
        "published",
        "published",
    ]
    published, published_b = publications

    with _authorize(
        authority,
        organization_id,
        ControlOperation.READ_SOURCE_PROGRESS,
        "read-delete-execution-baseline",
    ) as call:
        progress = control.read_file_source_progress(
            call,
            accepted_initial_second.source_ref,
        )
    assert progress.complete_change_baseline is not None
    (root / "a.md").unlink()
    (root / "b.md").unlink()
    changed_source = FileChangeSource(
        organization_id,
        source.source_version,
        scan_head=progress.change_scan_head,
        complete_baseline=progress.complete_change_baseline,
    )
    delete_first = provider.read_changes(
        changed_source,
        InitialScan(),
        ChangeLimit(1),
    )
    assert type(delete_first) is ProviderOk
    assert delete_first.value.changes[0].kind is FileChangeKind.DELETE
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-delete-execution-first",
    ) as call:
        accepted_delete_first = control.accept_file_change_page(
            call,
            delete_first.value,
        )
    assert accepted_delete_first.complete is False
    assert accepted_delete_first.next_cursor is not None
    upsert_command = ExecuteFileDeleteObservation(
        accepted_initial_first.source_ref,
        accepted_initial_first.source_version_ref,
        accepted_initial_first.page_ref,
        1,
    )
    incomplete_command = ExecuteFileDeleteObservation(
        accepted_delete_first.source_ref,
        accepted_delete_first.source_version_ref,
        accepted_delete_first.page_ref,
        1,
    )
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            before_refusals = _file_delete_execution_effect_snapshot(
                connection,
                organization_id,
            )
        for request_id, refused_command in (
            ("execute-upsert-observation", upsert_command),
            ("execute-incomplete-delete-observation", incomplete_command),
        ):
            with (
                _authorize(
                    authority,
                    organization_id,
                    ControlOperation.EXECUTE_FILE_DELETE_OBSERVATION,
                    request_id,
                ) as call,
                pytest.raises(SourceNotAvailable),
            ):
                control.execute_file_delete_observation(call, refused_command)
        with migration_engine.connect() as connection:
            assert (
                _file_delete_execution_effect_snapshot(
                    connection,
                    organization_id,
                )
                == before_refusals
            )
    finally:
        migration_engine.dispose()
    changed_source = replace(
        changed_source,
        scan_head=accepted_delete_first.scan_head,
    )
    delete_second = provider.read_changes(
        changed_source,
        accepted_delete_first.next_cursor,
        ChangeLimit(1),
    )
    assert type(delete_second) is ProviderOk
    assert delete_second.value.changes[0].kind is FileChangeKind.DELETE
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-delete-execution-second",
    ) as call:
        accepted_delete_second = control.accept_file_change_page(
            call,
            delete_second.value,
        )
    assert accepted_delete_second.complete is True

    execution_command = ExecuteFileDeleteObservation(
        accepted_delete_first.source_ref,
        accepted_delete_first.source_version_ref,
        accepted_delete_first.page_ref,
        1,
    )
    invalid_locators = (
        (
            "execute-missing-delete-page",
            replace(execution_command, page_ref="f" * 64),
        ),
        (
            "execute-stale-delete-version",
            replace(
                execution_command,
                source_version_ref=stale_source_version_ref,
            ),
        ),
        (
            "execute-forged-delete-source",
            replace(execution_command, source_ref=SourceRef(uuid4())),
        ),
    )
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            before_invalid_locators = _file_delete_execution_effect_snapshot(
                connection,
                organization_id,
            )
        for request_id, invalid_command in invalid_locators:
            with (
                _authorize(
                    authority,
                    organization_id,
                    ControlOperation.EXECUTE_FILE_DELETE_OBSERVATION,
                    request_id,
                ) as call,
                pytest.raises(SourceNotAvailable),
            ):
                control.execute_file_delete_observation(call, invalid_command)
        with migration_engine.connect() as connection:
            assert (
                _file_delete_execution_effect_snapshot(connection, organization_id)
                == before_invalid_locators
            )
    finally:
        migration_engine.dispose()
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE FUNCTION public.context_test_reject_file_delete_execution()
                    RETURNS trigger LANGUAGE plpgsql AS $function$
                    BEGIN
                        RAISE EXCEPTION USING ERRCODE = '55000',
                            MESSAGE = 'injected File delete binding refusal';
                    END;
                    $function$;
                    CREATE TRIGGER context_test_reject_file_delete_execution
                    BEFORE INSERT ON file_delete_observation_execution
                    FOR EACH ROW EXECUTE FUNCTION
                        public.context_test_reject_file_delete_execution()
                    """
                )
            )
        with (
            _authorize(
                authority,
                organization_id,
                ControlOperation.EXECUTE_FILE_DELETE_OBSERVATION,
                "execute-delete-observation-rollback",
            ) as call,
            pytest.raises(SourceControlUnavailable),
        ):
            control.execute_file_delete_observation(call, execution_command)
        with migration_engine.connect() as connection:
            rolled_back = connection.execute(
                text(
                    """
                    SELECT resource.tombstoned,
                           (SELECT count(*) FROM file_resource_cleanup_intent
                            WHERE organization_id = :organization_id),
                           (SELECT count(*)
                            FROM file_delete_observation_execution
                            WHERE organization_id = :organization_id),
                           (SELECT policy_epoch FROM organization_policy_epoch
                            WHERE organization_id = :organization_id)
                    FROM context_resource AS resource
                    WHERE resource.organization_id = :organization_id
                      AND resource.resource_ref = :resource_ref
                    """
                ),
                {
                    "organization_id": organization_id,
                    "resource_ref": published.candidate_ref.resource_ref,
                },
            ).one()
        assert tuple(rolled_back) == (False, 0, 0, 1)
    finally:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    DROP TRIGGER IF EXISTS context_test_reject_file_delete_execution
                        ON file_delete_observation_execution;
                    DROP FUNCTION IF EXISTS
                        public.context_test_reject_file_delete_execution()
                    """
                )
            )
        migration_engine.dispose()
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE FUNCTION public.context_test_mismatch_file_delete_effect()
                    RETURNS trigger LANGUAGE plpgsql AS $function$
                    BEGIN
                        NEW.event_ref := 'fault_' || NEW.event_ref;
                        RETURN NEW;
                    END;
                    $function$;
                    CREATE TRIGGER context_test_mismatch_file_delete_effect
                    BEFORE INSERT ON file_resource_cleanup_intent
                    FOR EACH ROW EXECUTE FUNCTION
                        public.context_test_mismatch_file_delete_effect()
                    """
                )
            )
        with migration_engine.connect() as connection:
            before_mismatched_effect = _file_delete_execution_effect_snapshot(
                connection,
                organization_id,
            )
        direct_cleanup_intent_id = uuid4()
        with guarded_control_engine.begin() as connection:
            connection.execute(
                text(
                    "SELECT pg_catalog.set_config("
                    "'app.organization_id', :organization_id, true)"
                ),
                {"organization_id": str(organization_id)},
            )
            assert (
                connection.execute(
                    text(
                        """
                        SELECT *
                        FROM public.context_control_execute_file_delete_observation(
                            :organization_id, :source_id, :source_version_id,
                            :page_ref, :change_ordinal, :cleanup_intent_id
                        )
                        """
                    ),
                    {
                        "organization_id": organization_id,
                        "source_id": execution_command.source_ref.value,
                        "source_version_id": execution_command.source_version_ref,
                        "page_ref": execution_command.page_ref,
                        "change_ordinal": execution_command.change_ordinal,
                        "cleanup_intent_id": direct_cleanup_intent_id,
                    },
                ).one_or_none()
                is None
            )
        with migration_engine.connect() as connection:
            assert (
                _file_delete_execution_effect_snapshot(connection, organization_id)
                == before_mismatched_effect
            )
    finally:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    DROP TRIGGER IF EXISTS context_test_mismatch_file_delete_effect
                        ON file_resource_cleanup_intent;
                    DROP FUNCTION IF EXISTS
                        public.context_test_mismatch_file_delete_effect()
                    """
                )
            )
        migration_engine.dispose()
    with _authorize(
        authority,
        organization_id,
        ControlOperation.EXECUTE_FILE_DELETE_OBSERVATION,
        "execute-current-delete-observation",
    ) as call:
        executed = control.execute_file_delete_observation(call, execution_command)
    with _authorize(
        authority,
        organization_id,
        ControlOperation.EXECUTE_FILE_DELETE_OBSERVATION,
        "replay-current-delete-observation",
    ) as call:
        replayed = control.execute_file_delete_observation(call, execution_command)

    assert replayed == executed
    assert executed.tombstone.resource_ref == published.candidate_ref.resource_ref
    expected_event_ref = "fdo_" + sha256(
        b"context-engine.file-delete-observation-event.v1\x00"
        + organization_id.bytes
        + execution_command.source_ref.value.bytes
        + execution_command.source_version_ref.bytes
        + bytes.fromhex(execution_command.page_ref)
        + execution_command.change_ordinal.to_bytes(2, "big", signed=True)
    ).hexdigest()
    assert executed.tombstone.event_ref == expected_event_ref
    assert executed.tombstone.event_sequence == accepted_delete_first.sequence
    assert executed.tombstone.policy_epoch == 2
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            effects = connection.execute(
                text(
                    """
                    SELECT resource.tombstoned,
                           (SELECT count(*) FROM file_resource_cleanup_intent
                            WHERE organization_id = :organization_id),
                           (SELECT count(*)
                            FROM file_delete_observation_execution
                            WHERE organization_id = :organization_id),
                           (SELECT policy_epoch FROM organization_policy_epoch
                            WHERE organization_id = :organization_id)
                    FROM context_resource AS resource
                    WHERE resource.organization_id = :organization_id
                      AND resource.resource_ref = :resource_ref
                    """
                ),
                {
                    "organization_id": organization_id,
                    "resource_ref": executed.tombstone.resource_ref,
                },
            ).one()
    finally:
        migration_engine.dispose()
    assert tuple(effects) == (True, 1, 1, 2)

    second_execution_command = ExecuteFileDeleteObservation(
        accepted_delete_second.source_ref,
        accepted_delete_second.source_version_ref,
        accepted_delete_second.page_ref,
        1,
    )
    with _authorize(
        authority,
        organization_id,
        ControlOperation.READ_SOURCE_PROGRESS,
        "read-delete-baseline-before-supersession",
    ) as call:
        delete_progress = control.read_file_source_progress(
            call,
            accepted_delete_second.source_ref,
        )
    assert delete_progress.complete_change_baseline is not None
    (root / "b.md").write_bytes(b"# B\n\nRecreated after delete observation.\n")
    next_scan = provider.read_changes(
        FileChangeSource(
            organization_id,
            source.source_version,
            scan_head=delete_progress.change_scan_head,
            complete_baseline=delete_progress.complete_change_baseline,
        ),
        InitialScan(),
        ChangeLimit(2),
    )
    assert type(next_scan) is ProviderOk
    assert next_scan.value.complete is True
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as publication_connection:
            publication_transaction = publication_connection.begin()
            publication_connection.execute(
                text(
                    """
                    SELECT pg_catalog.pg_advisory_xact_lock(
                        pg_catalog.hashtextextended(
                            'context-engine.file-publication:'
                            || CAST(:organization_id AS text), 0
                        )
                    )
                    """
                ),
                {"organization_id": organization_id},
            )

            def execute_second_delete() -> object:
                with _authorize(
                    authority,
                    organization_id,
                    ControlOperation.EXECUTE_FILE_DELETE_OBSERVATION,
                    "execute-delete-during-superseding-scan",
                ) as call:
                    return control.execute_file_delete_observation(
                        call,
                        second_execution_command,
                    )

            with ThreadPoolExecutor(max_workers=1) as executor:
                pending_execution = executor.submit(execute_second_delete)
                try:
                    deadline = monotonic() + 10
                    waiting = False
                    while monotonic() < deadline:
                        with migration_engine.connect() as observer:
                            waiting = bool(
                                observer.execute(
                                    text(
                                        """
                                        SELECT EXISTS (
                                            SELECT 1
                                            FROM pg_catalog.pg_stat_activity AS activity
                                            JOIN pg_catalog.pg_locks AS held_lock
                                              ON held_lock.pid = activity.pid
                                            WHERE activity.usename =
                                                  'context_engine_control'
                                              AND held_lock.locktype = 'advisory'
                                              AND held_lock.granted IS FALSE
                                        )
                                        """
                                    )
                                ).scalar_one()
                            )
                        if waiting:
                            break
                        sleep(0.01)
                    if not waiting:
                        pytest.fail(
                            "File delete execution did not wait for publication"
                        )

                    with _authorize(
                        authority,
                        organization_id,
                        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
                        "accept-concurrent-superseding-delete-scan",
                    ) as call:
                        accepted_newer = control.accept_file_change_page(
                            call,
                            next_scan.value,
                        )
                    assert accepted_newer.complete is True
                finally:
                    if publication_transaction.is_active:
                        publication_transaction.rollback()
                with pytest.raises(SourceNotAvailable):
                    pending_execution.result(timeout=5)
    finally:
        migration_engine.dispose()

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            superseded_effects = connection.execute(
                text(
                    """
                    SELECT resource.tombstoned,
                           (SELECT count(*) FROM file_resource_cleanup_intent
                            WHERE organization_id = :organization_id),
                           (SELECT count(*)
                            FROM file_delete_observation_execution
                            WHERE organization_id = :organization_id),
                           (SELECT policy_epoch FROM organization_policy_epoch
                            WHERE organization_id = :organization_id)
                    FROM context_resource AS resource
                    WHERE resource.organization_id = :organization_id
                      AND resource.resource_ref = :resource_ref
                    """
                ),
                {
                    "organization_id": organization_id,
                    "resource_ref": published_b.candidate_ref.resource_ref,
                },
            ).one()
    finally:
        migration_engine.dispose()
    assert tuple(superseded_effects) == (False, 1, 1, 2)

    with _authorize(
        authority,
        organization_id,
        ControlOperation.EXECUTE_FILE_DELETE_OBSERVATION,
        "replay-delete-after-newer-scan",
    ) as call:
        assert (
            control.execute_file_delete_observation(call, execution_command)
            == executed
        )

    with pytest.raises(SQLAlchemyError, match="cannot downgrade with File delete"):
        command.downgrade(Config(ROOT / "alembic.ini"), "20260725_0030")
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == HEAD_REVISION
    finally:
        migration_engine.dispose()



@pytest.mark.security_evidence(id="PG-FILE-DELETE-PAGE-085", layer="postgres")
@pytest.mark.security_evidence(id="PG-FILE-DELETE-NO-EFFECT-085", layer="postgres")
def test_control_accepts_delete_observations_without_visibility_effect(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_runtime_engine: Engine,
    guarded_worker_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.md").write_bytes(b"# A\n\nAlpha paragraph.\n")
    (root / "b.md").write_bytes(b"# B\n\nBeta paragraph.\n")
    (root / "c.md").write_bytes(b"# C\n\nGamma paragraph.\n")
    provider_proofs, control_proofs = _proofs()
    organization_id = uuid4()
    receiver = FileImportReceiver(uuid4())
    control, authority, source = _seed_file_change_source(
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
        organization_id=organization_id,
        receiver=receiver,
        root_ref=FileRootRef("delete-observation-root"),
        control_proofs=control_proofs,
    )
    source = _activate_delete_observations(
        control,
        authority,
        organization_id,
        source,
    )
    provider = FileChangeProvider(
        FileRootRegistry(
            {source.source_version.root_ref: root},
            limits=FileReadLimits(max_file_bytes=1_024),
        ),
        proofs=provider_proofs,
    )

    first = provider.read_changes(source, InitialScan(), ChangeLimit(3))
    assert type(first) is ProviderOk
    assert first.value.baseline_ref is None
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-v4-baseline",
    ) as call:
        accepted_first = control.accept_file_change_page(call, first.value)
    with _authorize(
        authority,
        organization_id,
        ControlOperation.READ_SOURCE_PROGRESS,
        "read-v4-baseline",
    ) as call:
        baseline_progress = control.read_file_source_progress(
            call,
            accepted_first.source_ref,
        )
    assert baseline_progress.complete_change_baseline is not None
    assert [
        entry.path.value
        for entry in baseline_progress.complete_change_baseline.entries
    ] == ["a.md", "b.md", "c.md"]

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
    prepared_imports = []
    for path in ("b.md", "c.md"):
        with _authorize(
            authority,
            organization_id,
            ControlOperation.IMPORT_FILE,
            f"prepare-published-delete-{path}",
        ) as call:
            prepared_imports.append(
                control.prepare_file_import(
                    call,
                    PrepareFileImport(
                        accepted_first.source_ref,
                        FileImportPath(path),
                        FileImportAudience("principal:file-reader", membership_id, 1),
                        f"published-delete-{path}",
                    ),
                )
            )
    codec = WorkerLeaseCodec(
        WorkerLeaseKeyring(active_version=1, keys={1: bytes(range(64, 96))})
    )
    issuer = PostgreSQLWorkerLeaseIssuer(
        guarded_control_engine,
        codec,
        lease_ttl_seconds=300,
    )
    tokens = [issuer.issue_file_import_lease(item) for item in prepared_imports]
    with FileRootRegistry(
        {source.source_version.root_ref: root},
        limits=FileReadLimits(max_file_bytes=1_024),
    ) as worker_roots:
        worker = PostgreSQLFileImportWorker(
            guarded_worker_engine,
            codec,
            receiver,
            worker_roots,
            MarkdownCompilerConfig("markdown-config-v1"),
            clock=lambda: datetime.now(UTC).replace(microsecond=0),
        )
        published_deletes = {
            path: worker.run(
                FileImportLeaseRedemption(
                    token,
                    organization_id,
                    prepared.job_id,
                    accepted_first.source_ref,
                )
            )
            for path, token, prepared in zip(
                ("b.md", "c.md"),
                tokens,
                prepared_imports,
                strict=True,
            )
        }
    assert all(item.outcome == "published" for item in published_deletes.values())

    (root / "a.md").unlink()
    (root / "b.md").unlink()
    (root / "c.md").unlink()
    source = FileChangeSource(
        organization_id=organization_id,
        source_version=source.source_version,
        scan_head=baseline_progress.change_scan_head,
        complete_baseline=baseline_progress.complete_change_baseline,
    )
    changed = provider.read_changes(source, InitialScan(), ChangeLimit(3))
    assert type(changed) is ProviderOk
    assert [
        (change.path.value, change.kind.value)
        for change in changed.value.changes
    ] == [("a.md", "delete"), ("b.md", "delete"), ("c.md", "delete")]
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            before = _delete_observation_effect_snapshot(
                connection,
                organization_id,
            )
    finally:
        migration_engine.dispose()
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-v4-delete",
    ) as call:
        accepted_delete = control.accept_file_change_page(call, changed.value)
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "replay-v4-delete",
    ) as call:
        assert control.accept_file_change_page(call, changed.value) == accepted_delete

    unpublished_delete = ExecuteFileDeleteObservation(
        accepted_delete.source_ref,
        accepted_delete.source_version_ref,
        accepted_delete.page_ref,
        1,
    )
    published_delete = replace(unpublished_delete, change_ordinal=2)
    mismatched_replay = replace(unpublished_delete, change_ordinal=3)
    with (
        _authorize(
            authority,
            organization_id,
            ControlOperation.EXECUTE_FILE_DELETE_OBSERVATION,
            "reject-unpublished-delete-observation",
        ) as call,
        pytest.raises(SourceNotAvailable),
    ):
        control.execute_file_delete_observation(call, unpublished_delete)
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT count(*) FROM file_delete_observation_execution "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": organization_id},
            ).scalar_one() == 0
    finally:
        migration_engine.dispose()

    with (
        _authorize(
            authority,
            organization_id,
            ControlOperation.SCHEDULE_FILE_CHANGE_PAGE,
            "reject-delete-page-scheduling",
        ) as call,
        pytest.raises(SourceNotAvailable),
    ):
        control.schedule_file_change_page(
            call,
            ScheduleFileChangePage(
                accepted_delete.source_ref,
                accepted_delete.source_version_ref,
                accepted_delete.page_ref,
                FileImportAudience(
                    "principal:file-reader",
                    membership_id,
                    1,
                ),
            ),
        )

    with _authorize(
        authority,
        organization_id,
        ControlOperation.READ_SOURCE_PROGRESS,
        "read-complete-delete-baseline",
    ) as call:
        final_progress = control.read_file_source_progress(
            call,
            accepted_delete.source_ref,
        )
    assert final_progress.complete_change_baseline is not None
    assert [
        (entry.path.value, entry.kind.value)
        for entry in final_progress.complete_change_baseline.entries
    ] == [("a.md", "delete"), ("b.md", "delete"), ("c.md", "delete")]

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            after = _delete_observation_effect_snapshot(
                connection,
                organization_id,
            )
    finally:
        migration_engine.dispose()
    assert after == before

    for nonowner_engine in (
        guarded_control_engine,
        guarded_runtime_engine,
        guarded_worker_engine,
    ):
        for table in (
            "file_source_delete_observation_page",
            "file_delete_observation_execution",
        ):
            with (
                nonowner_engine.connect() as connection,
                pytest.raises(DBAPIError),
            ):
                connection.execute(
                    text(f"SELECT count(*) FROM {table}")  # noqa: S608
                ).scalar_one()
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            privileges = connection.execute(
                text(
                    """
                    SELECT
                      has_function_privilege(
                        'context_engine_control',
                        'context_control_execute_file_delete_observation('
                        'uuid,uuid,uuid,text,smallint,uuid)',
                        'EXECUTE'
                      ),
                      has_function_privilege(
                        'context_engine_runtime',
                        'context_control_execute_file_delete_observation('
                        'uuid,uuid,uuid,text,smallint,uuid)',
                        'EXECUTE'
                      ),
                      has_function_privilege(
                        'context_engine_worker',
                        'context_control_execute_file_delete_observation('
                        'uuid,uuid,uuid,text,smallint,uuid)',
                        'EXECUTE'
                      )
                    """
                )
            ).one()
    finally:
        migration_engine.dispose()
    assert tuple(privileges) == (True, False, False)
    other_organization = uuid4()
    with guarded_control_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.organization_id', :org, true)"),
            {"org": str(other_organization)},
        )
        cross_organization_rows = connection.execute(
            text(
                "SELECT * FROM "
                "context_control_read_complete_file_change_baseline(:org, :source)"
            ),
            {
                "org": other_organization,
                "source": source.source_version.source_ref.value,
            },
        ).all()
    assert cross_organization_rows == []

    cross_organization_authority = ControlOperatorAuthority(
        _Authenticator(other_organization),
        call_ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    )
    cross_organization_control = ContextControl(
        store=PostgreSQLControlStore(
            guarded_control_engine,
            clock=lambda: NOW,
            file_import_receiver=receiver,
            file_change_checkpoint_signing_key=CHECKPOINT_KEY,
        ),
        authority=cross_organization_authority,
        clock=lambda: NOW,
        file_change_proofs=control_proofs,
    )
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            before_cross_execution = _file_delete_execution_effect_snapshot(
                connection,
                organization_id,
            )
    finally:
        migration_engine.dispose()
    with (
        _authorize(
            cross_organization_authority,
            other_organization,
            ControlOperation.EXECUTE_FILE_DELETE_OBSERVATION,
            "reject-cross-organization-delete-execution",
        ) as call,
        pytest.raises(SourceNotAvailable),
    ):
        cross_organization_control.execute_file_delete_observation(
            call,
            published_delete,
        )
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            assert (
                _file_delete_execution_effect_snapshot(connection, organization_id)
                == before_cross_execution
            )
    finally:
        migration_engine.dispose()
    cross_unsigned = replace(
        changed.value,
        organization_id=other_organization,
        changes=tuple(
            replace(change, organization_id=other_organization)
            for change in changed.value.changes
        ),
        provider_proof="A" * 86,
    )
    cross_page = replace(
        cross_unsigned,
        provider_proof=provider_proofs._seal_page(cross_unsigned),
    )
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            cross_before = tuple(
                connection.execute(
                    text(
                        """
                        SELECT
                          (SELECT count(*) FROM file_source_change_page),
                          (SELECT count(*) FROM file_source_change),
                          (SELECT count(*) FROM file_source_delete_observation_page),
                          (SELECT count(*) FROM file_source_acquisition_checkpoint)
                        """
                    )
                ).one()
            )
    finally:
        migration_engine.dispose()
    with (
        _authorize(
            cross_organization_authority,
            other_organization,
            ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
            "reject-cross-organization-delete-page",
        ) as call,
        pytest.raises(SourceNotAvailable),
    ):
        cross_organization_control.accept_file_change_page(call, cross_page)
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            cross_after = tuple(
                connection.execute(
                    text(
                        """
                        SELECT
                          (SELECT count(*) FROM file_source_change_page),
                          (SELECT count(*) FROM file_source_change),
                          (SELECT count(*) FROM file_source_delete_observation_page),
                          (SELECT count(*) FROM file_source_acquisition_checkpoint)
                        """
                    )
                ).one()
            )
    finally:
        migration_engine.dispose()
    assert cross_after == cross_before

    with _authorize(
        authority,
        organization_id,
        ControlOperation.TOMBSTONE_FILE_RESOURCE,
        "manual-tombstone-before-observation-execution",
    ) as call:
        manual_tombstone = control.tombstone_file_resource(
            call,
            TombstoneFileResource(
                accepted_delete.source_ref,
                published_deletes["c.md"].candidate_ref.resource_ref,
                "manual-delete-before-observation-execution",
                accepted_delete.sequence,
            ),
        )
    assert manual_tombstone.resource_ref == (
        published_deletes["c.md"].candidate_ref.resource_ref
    )
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            before_mismatched_replay = _file_delete_execution_effect_snapshot(
                connection,
                organization_id,
            )
    finally:
        migration_engine.dispose()
    with (
        _authorize(
            authority,
            organization_id,
            ControlOperation.EXECUTE_FILE_DELETE_OBSERVATION,
            "reject-manually-tombstoned-observation-execution",
        ) as call,
        pytest.raises(SourceNotAvailable),
    ):
        control.execute_file_delete_observation(call, mismatched_replay)
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            assert (
                _file_delete_execution_effect_snapshot(connection, organization_id)
                == before_mismatched_replay
            )
    finally:
        migration_engine.dispose()

    with _authorize(
        authority,
        organization_id,
        ControlOperation.OFFBOARD_FILE_SOURCE,
        "disable-before-delete-execution",
    ) as call:
        control.offboard_file_source(
            call,
            OffboardFileSource(accepted_delete.source_ref),
        )
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            before_disabled_execution = _file_delete_execution_effect_snapshot(
                connection,
                organization_id,
            )
    finally:
        migration_engine.dispose()
    with (
        _authorize(
            authority,
            organization_id,
            ControlOperation.EXECUTE_FILE_DELETE_OBSERVATION,
            "reject-disabled-delete-execution",
        ) as call,
        pytest.raises(SourceNotAvailable),
    ):
        control.execute_file_delete_observation(call, published_delete)
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            assert (
                _file_delete_execution_effect_snapshot(connection, organization_id)
                == before_disabled_execution
            )
    finally:
        migration_engine.dispose()


def test_progress_and_complete_baseline_share_one_statement_snapshot(
    tmp_path: Path,
    guarded_control_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    """A concurrent complete-page commit cannot tear the progress projection."""

    root = tmp_path / "root"
    root.mkdir()
    (root / "a.md").write_bytes(b"A")
    (root / "b.md").write_bytes(b"B")
    provider_proofs, control_proofs = _proofs()
    organization_id = uuid4()
    receiver = FileImportReceiver(uuid4())
    control, authority, source = _seed_file_change_source(
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
        organization_id=organization_id,
        receiver=receiver,
        root_ref=FileRootRef("delete-progress-snapshot-root"),
        control_proofs=control_proofs,
    )
    source = _activate_delete_observations(
        control,
        authority,
        organization_id,
        source,
    )
    provider = FileChangeProvider(
        FileRootRegistry(
            {source.source_version.root_ref: root},
            limits=FileReadLimits(max_file_bytes=1_024),
        ),
        proofs=provider_proofs,
    )
    first = provider.read_changes(source, InitialScan(), ChangeLimit(2))
    assert type(first) is ProviderOk
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-snapshot-baseline",
    ) as call:
        accepted_first = control.accept_file_change_page(call, first.value)
    with _authorize(
        authority,
        organization_id,
        ControlOperation.READ_SOURCE_PROGRESS,
        "read-snapshot-baseline",
    ) as call:
        initial_progress = control.read_file_source_progress(
            call,
            accepted_first.source_ref,
        )
    assert initial_progress.complete_change_baseline is not None

    (root / "b.md").unlink()
    changed = provider.read_changes(
        FileChangeSource(
            organization_id,
            source.source_version,
            scan_head=initial_progress.change_scan_head,
            complete_baseline=initial_progress.complete_change_baseline,
        ),
        InitialScan(),
        ChangeLimit(2),
    )
    assert type(changed) is ProviderOk

    reader_authority = ControlOperatorAuthority(
        _Authenticator(organization_id),
        call_ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    )
    reader_control = ContextControl(
        store=PostgreSQLControlStore(
            guarded_control_engine,
            clock=lambda: NOW,
            file_import_receiver=receiver,
            file_change_checkpoint_signing_key=CHECKPOINT_KEY,
        ),
        authority=reader_authority,
        clock=lambda: NOW,
        file_change_proofs=control_proofs,
    )
    snapshot_read = Event()
    release_reader = Event()

    def hold_after_progress_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if (
            "context_control_read_file_source_progress" in statement
            and not snapshot_read.is_set()
        ):
            snapshot_read.set()
            if not release_reader.wait(timeout=10):
                raise RuntimeError("concurrent progress reader was not released")

    def read_during_commit() -> FileSourceProgress:
        with _authorize(
            reader_authority,
            organization_id,
            ControlOperation.READ_SOURCE_PROGRESS,
            "read-during-complete-page-commit",
        ) as call:
            return reader_control.read_file_source_progress(
                call,
                accepted_first.source_ref,
            )

    event.listen(
        guarded_control_engine,
        "after_cursor_execute",
        hold_after_progress_statement,
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending_read = pool.submit(read_during_commit)
            assert snapshot_read.wait(timeout=10)
            with _authorize(
                authority,
                organization_id,
                ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
                "accept-while-progress-snapshot-is-open",
            ) as call:
                accepted_changed = control.accept_file_change_page(
                    call,
                    changed.value,
                )
            release_reader.set()
            observed = pending_read.result(timeout=10)
    finally:
        release_reader.set()
        event.remove(
            guarded_control_engine,
            "after_cursor_execute",
            hold_after_progress_statement,
        )

    assert observed.acquisition_checkpoint is not None
    assert observed.complete_change_baseline is not None
    assert observed.acquisition_checkpoint.sequence == accepted_first.sequence
    assert observed.complete_change_baseline.reference.page_ref == (
        accepted_first.page_ref
    )
    assert accepted_changed.sequence > observed.acquisition_checkpoint.sequence


def test_unchanged_files_recover_after_an_incomplete_superseding_scan(
    tmp_path: Path,
    guarded_control_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.md").write_bytes(b"A")
    (root / "b.md").write_bytes(b"B")
    provider_proofs, control_proofs = _proofs()
    organization_id = uuid4()
    control, authority, source = _seed_file_change_source(
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
        organization_id=organization_id,
        receiver=FileImportReceiver(uuid4()),
        root_ref=FileRootRef("incomplete-superseding-scan-root"),
        control_proofs=control_proofs,
    )
    source = _activate_delete_observations(
        control,
        authority,
        organization_id,
        source,
    )
    provider = FileChangeProvider(
        FileRootRegistry(
            {source.source_version.root_ref: root},
            limits=FileReadLimits(max_file_bytes=1_024),
        ),
        proofs=provider_proofs,
    )

    initial = provider.read_changes(source, InitialScan(), ChangeLimit(2))
    assert type(initial) is ProviderOk
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-recovery-baseline",
    ) as call:
        accepted_initial = control.accept_file_change_page(call, initial.value)
    with _authorize(
        authority,
        organization_id,
        ControlOperation.READ_SOURCE_PROGRESS,
        "read-recovery-baseline",
    ) as call:
        baseline_progress = control.read_file_source_progress(
            call,
            accepted_initial.source_ref,
        )
    baseline = baseline_progress.complete_change_baseline
    assert baseline is not None

    (root / "a.md").write_bytes(b"A2")
    changed = provider.read_changes(
        FileChangeSource(
            organization_id,
            source.source_version,
            scan_head=baseline_progress.change_scan_head,
            complete_baseline=baseline,
        ),
        InitialScan(),
        ChangeLimit(1),
    )
    assert type(changed) is ProviderOk
    assert changed.value.complete is False
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-incomplete-superseding-scan",
    ) as call:
        accepted_changed = control.accept_file_change_page(call, changed.value)
    assert accepted_changed.complete is False

    (root / "a.md").write_bytes(b"A")
    with _authorize(
        authority,
        organization_id,
        ControlOperation.READ_SOURCE_PROGRESS,
        "read-incomplete-superseding-scan",
    ) as call:
        incomplete_progress = control.read_file_source_progress(
            call,
            accepted_changed.source_ref,
        )
    assert incomplete_progress.change_scan_head == accepted_changed.scan_head
    assert incomplete_progress.complete_change_baseline == baseline
    recovered = provider.read_changes(
        FileChangeSource(
            organization_id,
            source.source_version,
            scan_head=incomplete_progress.change_scan_head,
            complete_baseline=incomplete_progress.complete_change_baseline,
        ),
        InitialScan(),
        ChangeLimit(2),
    )
    assert type(recovered) is ProviderOk
    assert recovered.value.baseline_ref == baseline.reference
    assert recovered.value.superseded_scan_epoch == accepted_changed.scan_epoch
    assert recovered.value.complete is True
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-recovered-scan",
    ) as call:
        accepted_recovered = control.accept_file_change_page(call, recovered.value)
    assert accepted_recovered.complete is True
    assert accepted_recovered.scan_epoch != accepted_initial.scan_epoch
    assert accepted_recovered.scan_epoch != accepted_changed.scan_epoch
    with _authorize(
        authority,
        organization_id,
        ControlOperation.READ_SOURCE_PROGRESS,
        "read-recovered-scan",
    ) as call:
        recovered_progress = control.read_file_source_progress(
            call,
            accepted_recovered.source_ref,
        )
    assert recovered_progress.change_scan_head == accepted_recovered.scan_head
    assert recovered_progress.complete_change_baseline is not None
    assert (
        recovered_progress.complete_change_baseline.reference.scan_epoch
        == accepted_recovered.scan_epoch
    )


def test_oversized_delete_diff_is_denied_before_durable_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    guarded_control_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    """The provider refuses an unfinishable scan before its first durable page."""

    root = tmp_path / "root"
    root.mkdir()
    provider_proofs, control_proofs = _proofs()
    organization_id = uuid4()
    control, authority, source = _seed_file_change_source(
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
        organization_id=organization_id,
        receiver=FileImportReceiver(uuid4()),
        root_ref=FileRootRef("oversized-delete-diff-root"),
        control_proofs=control_proofs,
    )
    source = _activate_delete_observations(
        control,
        authority,
        organization_id,
        source,
    )
    baseline = FileChangeBaseline(
        reference=FileChangeBaselineRef(
            source_version_ref=source.source_version.version_ref,
            scan_ref="1" * 64,
            scan_epoch=uuid4(),
            page_ref="2" * 64,
            checkpoint_ref="facp_" + "3" * 64,
            sequence=1,
        ),
        entries=tuple(
            FileChangeBaselineEntry(
                kind=FileChangeKind.UPSERT,
                path=FileImportPath(f"{index:05d}.md"),
                content_sha256="4" * 64,
                content_length=1,
            )
            for index in range(MAX_FILE_CHANGE_BASELINE_SIZE)
        ),
    )
    source = FileChangeSource(
        organization_id,
        source.source_version,
        complete_baseline=baseline,
    )
    observed = tuple(
        (FileImportPath(f"{index:05d}.md"), b"A")
        for index in range(1, MAX_FILE_CHANGE_BASELINE_SIZE)
    ) + ((FileImportPath("new.md"), b"N"),)
    monkeypatch.setattr(
        FileRootRegistry,
        "_observe_markdown_files",
        lambda _registry, _root_ref: observed,
    )
    provider = FileChangeProvider(
        FileRootRegistry(
            {source.source_version.root_ref: root},
            limits=FileReadLimits(max_file_bytes=1_024),
        ),
        proofs=provider_proofs,
    )
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            before = tuple(
                connection.execute(
                    text(
                        """
                        SELECT
                          (SELECT count(*) FROM file_source_change_page
                           WHERE organization_id = :organization_id),
                          (SELECT count(*) FROM file_source_change
                           WHERE organization_id = :organization_id),
                          (SELECT count(*) FROM file_source_delete_observation_page
                           WHERE organization_id = :organization_id),
                          (SELECT count(*) FROM file_source_acquisition_checkpoint
                           WHERE organization_id = :organization_id)
                        """
                    ),
                    {"organization_id": organization_id},
                ).one()
            )
    finally:
        migration_engine.dispose()

    outcome = provider.read_changes(source, InitialScan(), ChangeLimit(1))

    assert type(outcome) is ProviderGenericDenied
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            after = tuple(
                connection.execute(
                    text(
                        """
                        SELECT
                          (SELECT count(*) FROM file_source_change_page
                           WHERE organization_id = :organization_id),
                          (SELECT count(*) FROM file_source_change
                           WHERE organization_id = :organization_id),
                          (SELECT count(*) FROM file_source_delete_observation_page
                           WHERE organization_id = :organization_id),
                          (SELECT count(*) FROM file_source_acquisition_checkpoint
                           WHERE organization_id = :organization_id)
                        """
                    ),
                    {"organization_id": organization_id},
                ).one()
            )
    finally:
        migration_engine.dispose()
    assert after == before


@pytest.mark.security_evidence(id="PG-FILE-DELETE-DETECT-085", layer="postgres")
def test_delete_observation_refuses_forged_incomplete_and_stale_baselines(
    tmp_path: Path,
    guarded_control_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.md").write_bytes(b"A")
    (root / "b.md").write_bytes(b"B")
    provider_proofs, control_proofs = _proofs()
    organization_id = uuid4()
    control, authority, source = _seed_file_change_source(
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
        organization_id=organization_id,
        receiver=FileImportReceiver(uuid4()),
        root_ref=FileRootRef("delete-baseline-refusal-root"),
        control_proofs=control_proofs,
    )
    source = _activate_delete_observations(
        control,
        authority,
        organization_id,
        source,
    )
    provider = FileChangeProvider(
        FileRootRegistry(
            {source.source_version.root_ref: root},
            limits=FileReadLimits(max_file_bytes=1_024),
        ),
        proofs=provider_proofs,
    )
    baseline_page = provider.read_changes(source, InitialScan(), ChangeLimit(2))
    assert type(baseline_page) is ProviderOk
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-complete-baseline",
    ) as call:
        control.accept_file_change_page(call, baseline_page.value)
    with _authorize(
        authority,
        organization_id,
        ControlOperation.READ_SOURCE_PROGRESS,
        "read-complete-baseline-for-refusals",
    ) as call:
        baseline_progress = control.read_file_source_progress(
            call,
            source.source_version.source_ref,
        )
    baseline = baseline_progress.complete_change_baseline
    assert baseline is not None

    (root / "b.md").unlink()
    baseline_source = FileChangeSource(
        organization_id,
        source.source_version,
        scan_head=baseline_progress.change_scan_head,
        complete_baseline=baseline,
    )
    changed = provider.read_changes(
        baseline_source,
        InitialScan(),
        ChangeLimit(1),
    )
    assert type(changed) is ProviderOk
    assert changed.value.complete is False
    assert changed.value.changes[0].kind.value == "upsert"
    full_changed = provider.read_changes(
        baseline_source,
        InitialScan(),
        ChangeLimit(2),
    )
    assert type(full_changed) is ProviderOk
    delete_change = full_changed.value.changes[1]
    assert delete_change.kind.value == "delete"
    forged_unsigned = replace(
        full_changed.value,
        changes=(
            full_changed.value.changes[0],
            replace(delete_change, content_sha256="0" * 64),
        ),
        provider_proof="A" * 86,
    )
    forged = replace(
        forged_unsigned,
        provider_proof=provider_proofs._seal_page(forged_unsigned),
    )
    with (
        _authorize(
            authority,
            organization_id,
            ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
            "reject-forged-delete-lineage",
        ) as call,
        pytest.raises(SourceNotAvailable),
    ):
        control.accept_file_change_page(call, forged)

    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-incomplete-delete-scan-head",
    ) as call:
        accepted_incomplete = control.accept_file_change_page(call, changed.value)
    assert accepted_incomplete.complete is False
    assert accepted_incomplete.next_cursor is not None
    incomplete_reference = FileChangeBaselineRef(
        source_version_ref=accepted_incomplete.source_version_ref,
        scan_ref=accepted_incomplete.scan_ref,
        scan_epoch=accepted_incomplete.scan_epoch,
        page_ref=accepted_incomplete.page_ref,
        checkpoint_ref=accepted_incomplete.checkpoint_ref,
        sequence=accepted_incomplete.sequence,
        comparison_baseline_ref=baseline.reference,
    )
    incomplete_baseline = replace(baseline, reference=incomplete_reference)
    incomplete_baseline_page = provider.read_changes(
        FileChangeSource(
            organization_id,
            source.source_version,
            scan_head=accepted_incomplete.scan_head,
            complete_baseline=incomplete_baseline,
        ),
        InitialScan(),
        ChangeLimit(2),
    )
    assert type(incomplete_baseline_page) is ProviderOk
    with (
        _authorize(
            authority,
            organization_id,
            ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
            "reject-incomplete-delete-baseline",
        ) as call,
        pytest.raises(SourceNotAvailable),
    ):
        control.accept_file_change_page(call, incomplete_baseline_page.value)

    accepted_source = FileChangeSource(
        organization_id,
        source.source_version,
        scan_head=accepted_incomplete.scan_head,
        complete_baseline=baseline,
    )
    final_page = provider.read_changes(
        accepted_source,
        accepted_incomplete.next_cursor,
        ChangeLimit(1),
    )
    assert type(final_page) is ProviderOk
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "complete-delete-scan",
    ) as call:
        accepted_complete = control.accept_file_change_page(call, final_page.value)
    assert accepted_complete.complete is True

    (root / "a.md").write_bytes(b"A2")
    stale_page = provider.read_changes(
        FileChangeSource(
            organization_id,
            source.source_version,
            scan_head=accepted_complete.scan_head,
            complete_baseline=baseline,
        ),
        InitialScan(),
        ChangeLimit(2),
    )
    assert type(stale_page) is ProviderOk
    with (
        _authorize(
            authority,
            organization_id,
            ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
            "reject-stale-delete-baseline",
        ) as call,
        pytest.raises(SourceNotAvailable),
    ):
        control.accept_file_change_page(call, stale_page.value)

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            counts = connection.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*) FROM file_source_change_page
                       WHERE organization_id = :organization_id),
                      (SELECT count(*) FROM file_source_change
                       WHERE organization_id = :organization_id),
                      (SELECT count(*) FROM file_source_delete_observation_page
                       WHERE organization_id = :organization_id),
                      (SELECT count(*) FROM file_import_job
                       WHERE organization_id = :organization_id)
                    """
                ),
                {"organization_id": organization_id},
            ).one()
    finally:
        migration_engine.dispose()
    assert tuple(counts) == (3, 4, 3, 1)


@pytest.mark.security_evidence(id="PG-FILE-CHANGE-SCHEDULE-083", layer="postgres")
def test_control_atomically_schedules_exact_accepted_file_upserts(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    original_a = b"# A\n\nOriginal paragraph.\n"
    original_b = b"# B\n\nSecond paragraph.\n"
    (root / "a.md").write_bytes(original_a)
    (root / "b.md").write_bytes(original_b)
    provider_proofs, control_proofs = _proofs()
    organization_id = uuid4()
    receiver = FileImportReceiver(uuid4())
    control, authority, source = _seed_file_change_source(
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
        organization_id=organization_id,
        receiver=receiver,
        root_ref=FileRootRef("scheduled-change-root"),
        control_proofs=control_proofs,
    )
    provider = FileChangeProvider(
        FileRootRegistry(
            {source.source_version.root_ref: root},
            limits=FileReadLimits(max_file_bytes=1_024),
        ),
        proofs=provider_proofs,
    )
    page = provider.read_changes(source, InitialScan(), ChangeLimit(2))
    assert type(page) is ProviderOk
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-scheduled-page",
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
    command_to_schedule = ScheduleFileChangePage(
        source_ref=accepted.source_ref,
        source_version_ref=accepted.source_version_ref,
        page_ref=accepted.page_ref,
        audience=FileImportAudience(
            principal_ref="principal:file-reader",
            membership_id=membership_id,
            membership_version=1,
        ),
    )
    with guarded_control_engine.connect() as connection:
        interrupted = connection.begin()
        provisional = connection.execute(
            text(
                """
                SELECT *
                FROM public.context_control_schedule_file_change_page(
                    :organization_id, :source_id, :source_version_id,
                    :page_ref, :audience_principal_ref,
                    :audience_membership_id, :audience_membership_version,
                    :service_principal_id
                )
                """
            ),
            {
                "organization_id": organization_id,
                "source_id": accepted.source_ref.value,
                "source_version_id": accepted.source_version_ref,
                "page_ref": accepted.page_ref,
                "audience_principal_ref": command_to_schedule.audience.principal_ref,
                "audience_membership_id": command_to_schedule.audience.membership_id,
                "audience_membership_version": (
                    command_to_schedule.audience.membership_version
                ),
                "service_principal_id": receiver.service_principal_id,
            },
        ).all()
        assert len(provisional) == 2
        interrupted.rollback()
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            rolled_back_counts = connection.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*) FROM file_acquisition
                       WHERE organization_id = :organization_id
                         AND change_page_ref = :page_ref),
                      (SELECT count(*) FROM file_import_job
                       WHERE organization_id = :organization_id
                         AND source_id = :source_id),
                      (SELECT count(*) FROM file_source_acquisition_checkpoint
                       WHERE organization_id = :organization_id
                         AND source_id = :source_id),
                      (SELECT count(*) FROM file_source_publish_watermark
                       WHERE organization_id = :organization_id
                         AND source_id = :source_id)
                    """
                ),
                {
                    "organization_id": organization_id,
                    "source_id": accepted.source_ref.value,
                    "page_ref": accepted.page_ref,
                },
            ).one()
    finally:
        migration_engine.dispose()
    assert tuple(rolled_back_counts) == (0, 1, 2, 0)

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            stale_version_id = connection.execute(
                text(
                    """
                    SELECT version_id
                    FROM source_version
                    WHERE organization_id = :organization_id
                      AND source_id = :source_id
                      AND version_id <> :active_version_id
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ),
                {
                    "organization_id": organization_id,
                    "source_id": accepted.source_ref.value,
                    "active_version_id": accepted.source_version_ref,
                },
            ).scalar_one()
    finally:
        migration_engine.dispose()
    with (
        _authorize(
            authority,
            organization_id,
            ControlOperation.SCHEDULE_FILE_CHANGE_PAGE,
            "schedule-stale-version",
        ) as call,
        pytest.raises(SourceNotAvailable),
    ):
        control.schedule_file_change_page(
            call,
            replace(command_to_schedule, source_version_ref=stale_version_id),
        )

    foreign_organization_id = uuid4()
    foreign_control, foreign_authority, _foreign_source = _seed_file_change_source(
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
        organization_id=foreign_organization_id,
        receiver=FileImportReceiver(uuid4()),
        root_ref=FileRootRef("foreign-schedule-root"),
        control_proofs=control_proofs,
    )
    with (
        _authorize(
            foreign_authority,
            foreign_organization_id,
            ControlOperation.SCHEDULE_FILE_CHANGE_PAGE,
            "schedule-cross-organization",
        ) as call,
        pytest.raises(SourceNotAvailable),
    ):
        foreign_control.schedule_file_change_page(call, command_to_schedule)

    with (
        _authorize(
            authority,
            organization_id,
            ControlOperation.SCHEDULE_FILE_CHANGE_PAGE,
            "schedule-foreign-source",
        ) as call,
        pytest.raises(SourceNotAvailable),
    ):
        control.schedule_file_change_page(
            call,
            replace(command_to_schedule, source_ref=SourceRef(uuid4())),
        )

    with _authorize(
        authority,
        organization_id,
        ControlOperation.SCHEDULE_FILE_CHANGE_PAGE,
        "schedule-accepted-page",
    ) as call:
        scheduled = control.schedule_file_change_page(call, command_to_schedule)
    with _authorize(
        authority,
        organization_id,
        ControlOperation.SCHEDULE_FILE_CHANGE_PAGE,
        "replay-accepted-page",
    ) as call:
        replayed = control.schedule_file_change_page(call, command_to_schedule)

    assert scheduled == replayed
    assert [change.path.value for change in scheduled.changes] == ["a.md", "b.md"]
    assert [change.content_length for change in scheduled.changes] == [
        len(original_a),
        len(original_b),
    ]
    assert len({change.prepared_import.job_id for change in scheduled.changes}) == 2

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            lineage = connection.execute(
                text(
                    """
                    SELECT acquisition.change_ordinal,
                           acquisition.relative_path,
                           acquisition.expected_content_sha256,
                           acquisition.expected_content_length,
                           job.job_id, job.service_principal_id
                    FROM file_acquisition AS acquisition
                    JOIN file_import_job AS job
                      ON job.organization_id = acquisition.organization_id
                     AND job.acquisition_id = acquisition.acquisition_id
                    WHERE acquisition.organization_id = :organization_id
                      AND acquisition.source_id = :source_id
                      AND acquisition.change_page_ref = :page_ref
                    ORDER BY acquisition.change_ordinal
                    """
                ),
                {
                    "organization_id": organization_id,
                    "source_id": accepted.source_ref.value,
                    "page_ref": accepted.page_ref,
                },
            ).all()
            scheduled_progress = connection.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*) FROM file_source_acquisition_checkpoint
                       WHERE organization_id = :organization_id
                         AND source_id = :source_id),
                      (SELECT count(*) FROM file_source_publish_watermark
                       WHERE organization_id = :organization_id
                         AND source_id = :source_id)
                    """
                ),
                {
                    "organization_id": organization_id,
                    "source_id": accepted.source_ref.value,
                },
            ).one()
            schedule_definition = connection.execute(
                text(
                    "SELECT pg_get_functiondef("
                    "'public.context_control_schedule_file_change_page("
                    "uuid,uuid,uuid,text,text,uuid,bigint,uuid)'::regprocedure)"
                )
            ).scalar_one()
    finally:
        migration_engine.dispose()
    assert [tuple(row) for row in lineage] == [
        (
            change.ordinal,
            change.path.value,
            change.content_sha256,
            change.content_length,
            change.prepared_import.job_id,
            receiver.service_principal_id,
        )
        for change in scheduled.changes
    ]
    assert tuple(scheduled_progress) == (4, 0)
    assert "context-engine.file-source-progress:" in schedule_definition
    assert schedule_definition.index("pg_advisory_xact_lock") < (
        schedule_definition.index("FOR UPDATE OF source")
    )

    codec = WorkerLeaseCodec(
        WorkerLeaseKeyring(active_version=1, keys={1: bytes(range(64, 96))})
    )
    first_import = scheduled.changes[0].prepared_import
    first_token = PostgreSQLWorkerLeaseIssuer(
        guarded_control_engine,
        codec,
        lease_ttl_seconds=300,
    ).issue_file_import_lease(first_import)
    (root / "a.md").write_bytes(b"# A\n\nChanged after acceptance.\n")
    with (
        FileRootRegistry(
            {source.source_version.root_ref: root},
            limits=FileReadLimits(max_file_bytes=1_024),
        ) as worker_roots,
        pytest.raises(FileImportUnavailable),
    ):
        PostgreSQLFileImportWorker(
            guarded_worker_engine,
            codec,
            receiver,
            worker_roots,
            MarkdownCompilerConfig("markdown-config-v1"),
            clock=lambda: datetime.now(UTC).replace(microsecond=0),
        ).run(
            FileImportLeaseRedemption(
                first_token,
                organization_id,
                first_import.job_id,
                accepted.source_ref,
            )
        )

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            failed_effects = connection.execute(
                text(
                    """
                    SELECT job.state,
                           (SELECT count(*) FROM context_revision
                            WHERE organization_id = :organization_id),
                           (SELECT count(*) FROM exact_phrase_candidate
                            WHERE organization_id = :organization_id),
                           (SELECT count(*) FROM resource_access_policy
                            WHERE organization_id = :organization_id),
                           (SELECT count(*)
                            FROM file_source_acquisition_checkpoint
                            WHERE organization_id = :organization_id
                              AND source_id = :source_id),
                           (SELECT count(*) FROM file_source_publish_watermark
                            WHERE organization_id = :organization_id
                              AND source_id = :source_id)
                    FROM file_import_job AS job
                    WHERE job.organization_id = :organization_id
                      AND job.job_id = :job_id
                    """
                ),
                {
                    "organization_id": organization_id,
                    "source_id": accepted.source_ref.value,
                    "job_id": first_import.job_id,
                },
            ).one()
    finally:
        migration_engine.dispose()
    assert tuple(failed_effects) == ("failed", 0, 0, 0, 4, 0)

    second_import = scheduled.changes[1].prepared_import
    second_token = PostgreSQLWorkerLeaseIssuer(
        guarded_control_engine,
        codec,
        lease_ttl_seconds=300,
    ).issue_file_import_lease(second_import)
    with FileRootRegistry(
        {source.source_version.root_ref: root},
        limits=FileReadLimits(max_file_bytes=1_024),
    ) as worker_roots:
        published = PostgreSQLFileImportWorker(
            guarded_worker_engine,
            codec,
            receiver,
            worker_roots,
            MarkdownCompilerConfig("markdown-config-v1"),
            clock=lambda: datetime.now(UTC).replace(microsecond=0),
        ).run(
            FileImportLeaseRedemption(
                second_token,
                organization_id,
                second_import.job_id,
                accepted.source_ref,
            )
        )
    assert published.outcome == "published"
    assert published.candidate_refs

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            successful_effects = connection.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*) FROM context_revision
                       WHERE organization_id = :organization_id),
                      (SELECT count(*) FROM exact_phrase_candidate
                       WHERE organization_id = :organization_id),
                      (SELECT count(*) FROM file_source_publish_watermark
                       WHERE organization_id = :organization_id
                         AND source_id = :source_id)
                    """
                ),
                {
                    "organization_id": organization_id,
                    "source_id": accepted.source_ref.value,
                },
            ).one()
    finally:
        migration_engine.dispose()
    assert successful_effects[0] == 1
    assert successful_effects[1] > 0
    assert successful_effects[2] == 1

    with pytest.raises(
        RuntimeError,
        match="requires no retained accepted-change acquisition lineage",
    ):
        command.downgrade(Config(ROOT / "alembic.ini"), "20260725_0028")
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == HEAD_REVISION
            )
    finally:
        migration_engine.dispose()


def test_scheduled_file_missing_after_acceptance_fails_before_publication(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    migration_configuration: DatabaseConfiguration,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    path = root / "vanishing.md"
    path.write_bytes(b"# Present at acceptance\n")
    provider_proofs, control_proofs = _proofs()
    organization_id = uuid4()
    receiver = FileImportReceiver(uuid4())
    control, authority, source = _seed_file_change_source(
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
        organization_id=organization_id,
        receiver=receiver,
        root_ref=FileRootRef("scheduled-missing-root"),
        control_proofs=control_proofs,
    )
    provider = FileChangeProvider(
        FileRootRegistry(
            {source.source_version.root_ref: root},
            limits=FileReadLimits(max_file_bytes=1_024),
        ),
        proofs=provider_proofs,
    )
    page = provider.read_changes(source, InitialScan(), ChangeLimit(1))
    assert type(page) is ProviderOk
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-vanishing-page",
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
        "schedule-vanishing-page",
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

    prepared = scheduled.changes[0].prepared_import
    codec = WorkerLeaseCodec(
        WorkerLeaseKeyring(active_version=1, keys={1: bytes(range(64, 96))})
    )
    token = PostgreSQLWorkerLeaseIssuer(
        guarded_control_engine,
        codec,
        lease_ttl_seconds=300,
    ).issue_file_import_lease(prepared)
    original_read = FileRootRegistry.read

    def read_after_receiver_revocation(
        registry: FileRootRegistry,
        root_ref: FileRootRef,
        relative_path: FileImportPath,
    ) -> bytes:
        migration_engine = create_database_engine(migration_configuration)
        try:
            with migration_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE service_principal SET enabled = false "
                        "WHERE organization_id = :organization_id "
                        "AND service_principal_id = :receiver_id"
                    ),
                    {
                        "organization_id": organization_id,
                        "receiver_id": receiver.service_principal_id,
                    },
                )
        finally:
            migration_engine.dispose()
        return original_read(registry, root_ref, relative_path)

    monkeypatch.setattr(FileRootRegistry, "read", read_after_receiver_revocation)
    path.unlink()
    with (
        FileRootRegistry(
            {source.source_version.root_ref: root},
            limits=FileReadLimits(max_file_bytes=1_024),
        ) as worker_roots,
        pytest.raises(FileImportUnavailable),
    ):
        PostgreSQLFileImportWorker(
            guarded_worker_engine,
            codec,
            receiver,
            worker_roots,
            MarkdownCompilerConfig("markdown-config-v1"),
            clock=lambda: datetime.now(UTC).replace(microsecond=0),
        ).run(
            FileImportLeaseRedemption(
                token,
                organization_id,
                prepared.job_id,
                accepted.source_ref,
            )
        )

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            effects = connection.execute(
                text(
                    """
                    SELECT job.state,
                           (SELECT count(*) FROM context_revision
                            WHERE organization_id = :organization_id),
                           (SELECT count(*) FROM exact_phrase_candidate
                            WHERE organization_id = :organization_id),
                           (SELECT count(*) FROM resource_access_policy
                            WHERE organization_id = :organization_id),
                           (SELECT count(*) FROM file_source_publish_watermark
                            WHERE organization_id = :organization_id
                              AND source_id = :source_id)
                    FROM file_import_job AS job
                    WHERE job.organization_id = :organization_id
                      AND job.job_id = :job_id
                    """
                ),
                {
                    "organization_id": organization_id,
                    "source_id": accepted.source_ref.value,
                    "job_id": prepared.job_id,
                },
            ).one()
    finally:
        migration_engine.dispose()
    assert tuple(effects) == ("running", 0, 0, 0, 0)


def test_file_change_scheduling_rolls_back_an_injected_mid_page_conflict(
    tmp_path: Path,
    guarded_control_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.md").write_bytes(b"A")
    (root / "b.md").write_bytes(b"B")
    provider_proofs, control_proofs = _proofs()
    organization_id = uuid4()
    receiver = FileImportReceiver(uuid4())
    control, authority, source = _seed_file_change_source(
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
        organization_id=organization_id,
        receiver=receiver,
        root_ref=FileRootRef("schedule-conflict-root"),
        control_proofs=control_proofs,
    )
    provider = FileChangeProvider(
        FileRootRegistry(
            {source.source_version.root_ref: root},
            limits=FileReadLimits(max_file_bytes=1_024),
        ),
        proofs=provider_proofs,
    )
    page = provider.read_changes(source, InitialScan(), ChangeLimit(2))
    assert type(page) is ProviderOk
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-conflict-page",
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
    audience = FileImportAudience("principal:file-reader", membership_id, 1)
    with _authorize(
        authority,
        organization_id,
        ControlOperation.IMPORT_FILE,
        "prepare-scheduling-conflict",
    ) as call:
        conflicting_import = control.prepare_file_import(
            call,
            PrepareFileImport(
                accepted.source_ref,
                FileImportPath("collision.md"),
                audience,
                f"change:{accepted.page_ref}:2",
            ),
        )

    with (
        _authorize(
            authority,
            organization_id,
            ControlOperation.SCHEDULE_FILE_CHANGE_PAGE,
            "schedule-mid-page-conflict",
        ) as call,
        pytest.raises(SourceNotAvailable),
    ):
        control.schedule_file_change_page(
            call,
            ScheduleFileChangePage(
                accepted.source_ref,
                accepted.source_version_ref,
                accepted.page_ref,
                audience,
            ),
        )

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            effects = connection.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*) FROM file_acquisition
                       WHERE organization_id = :organization_id
                         AND source_id = :source_id
                         AND change_page_ref = :page_ref),
                      (SELECT count(*) FROM file_import_job
                       WHERE organization_id = :organization_id
                         AND source_id = :source_id),
                      (SELECT count(*) FROM file_source_acquisition_checkpoint
                       WHERE organization_id = :organization_id
                         AND source_id = :source_id),
                      (SELECT count(*) FROM file_source_publish_watermark
                       WHERE organization_id = :organization_id
                         AND source_id = :source_id)
                    """
                ),
                {
                    "organization_id": organization_id,
                    "source_id": accepted.source_ref.value,
                    "page_ref": accepted.page_ref,
                },
            ).one()
    finally:
        migration_engine.dispose()
    assert conflicting_import.job_id
    assert tuple(effects) == (0, 2, 3, 0)


@pytest.mark.security_evidence(
    id="PG-FILE-CHANGE-SUPERSESSION-083",
    layer="postgres",
)
def test_file_change_scheduling_allows_current_epoch_pages_and_refuses_superseded_scan(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    migration_configuration: DatabaseConfiguration,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.md").write_bytes(b"A")
    (root / "b.md").write_bytes(b"B")
    provider_proofs, control_proofs = _proofs()
    organization_id = uuid4()
    receiver = FileImportReceiver(uuid4())
    control, authority, source = _seed_file_change_source(
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
        organization_id=organization_id,
        receiver=receiver,
        root_ref=FileRootRef("schedule-current-scan-root"),
        control_proofs=control_proofs,
    )
    provider = FileChangeProvider(
        FileRootRegistry(
            {source.source_version.root_ref: root},
            limits=FileReadLimits(max_file_bytes=1_024),
        ),
        proofs=provider_proofs,
    )
    first = provider.read_changes(source, InitialScan(), ChangeLimit(1))
    assert type(first) is ProviderOk
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-current-epoch-first",
    ) as call:
        accepted_first = control.accept_file_change_page(call, first.value)
    assert accepted_first.next_cursor is not None
    source = replace(source, scan_head=accepted_first.scan_head)
    second = provider.read_changes(
        source,
        accepted_first.next_cursor,
        ChangeLimit(1),
    )
    assert type(second) is ProviderOk
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-current-epoch-second",
    ) as call:
        accepted_second = control.accept_file_change_page(call, second.value)

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
    audience = FileImportAudience("principal:file-reader", membership_id, 1)
    with _authorize(
        authority,
        organization_id,
        ControlOperation.SCHEDULE_FILE_CHANGE_PAGE,
        "schedule-earlier-current-epoch-page",
    ) as call:
        scheduled_first = control.schedule_file_change_page(
            call,
            ScheduleFileChangePage(
                accepted_first.source_ref,
                accepted_first.source_version_ref,
                accepted_first.page_ref,
                audience,
            ),
        )
    assert len(scheduled_first.changes) == 1

    source = replace(source, scan_head=accepted_second.scan_head)
    (root / "a.md").write_bytes(b"# A\n\nNew A.\n")
    newer = provider.read_changes(source, InitialScan(), ChangeLimit(2))
    assert type(newer) is ProviderOk
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-superseding-epoch",
    ) as call:
        accepted_newer = control.accept_file_change_page(call, newer.value)
    assert accepted_newer.scan_epoch != accepted_second.scan_epoch

    with (
        _authorize(
            authority,
            organization_id,
            ControlOperation.SCHEDULE_FILE_CHANGE_PAGE,
            "refuse-superseded-epoch-page",
        ) as call,
        pytest.raises(SourceNotAvailable),
    ):
        control.schedule_file_change_page(
            call,
            ScheduleFileChangePage(
                accepted_second.source_ref,
                accepted_second.source_version_ref,
                accepted_second.page_ref,
                audience,
            ),
        )

    with _authorize(
        authority,
        organization_id,
        ControlOperation.SCHEDULE_FILE_CHANGE_PAGE,
        "replay-scheduled-superseded-epoch-page",
    ) as call:
        replayed_first = control.schedule_file_change_page(
            call,
            ScheduleFileChangePage(
                accepted_first.source_ref,
                accepted_first.source_version_ref,
                accepted_first.page_ref,
                audience,
            ),
        )
    assert replayed_first == scheduled_first

    codec = WorkerLeaseCodec(
        WorkerLeaseKeyring(active_version=1, keys={1: bytes(range(64, 96))})
    )
    first_import = scheduled_first.changes[0].prepared_import
    first_token = PostgreSQLWorkerLeaseIssuer(
        guarded_control_engine,
        codec,
        lease_ttl_seconds=300,
    ).issue_file_import_lease(first_import)
    (root / "a.md").write_bytes(b"A")
    original_read = FileRootRegistry.read
    content_read_count = 0

    def track_content_read(
        registry: FileRootRegistry,
        root_ref: FileRootRef,
        relative_path: FileImportPath,
    ) -> bytes:
        nonlocal content_read_count
        content_read_count += 1
        return original_read(registry, root_ref, relative_path)

    monkeypatch.setattr(FileRootRegistry, "read", track_content_read)
    with (
        FileRootRegistry(
            {source.source_version.root_ref: root},
            limits=FileReadLimits(max_file_bytes=1_024),
        ) as worker_roots,
        pytest.raises(FileImportUnavailable),
    ):
        PostgreSQLFileImportWorker(
            guarded_worker_engine,
            codec,
            receiver,
            worker_roots,
            MarkdownCompilerConfig("markdown-config-v1"),
            clock=lambda: datetime.now(UTC).replace(microsecond=0),
        ).run(
            FileImportLeaseRedemption(
                first_token,
                organization_id,
                first_import.job_id,
                accepted_first.source_ref,
            )
        )
    assert content_read_count == 0

    (root / "a.md").write_bytes(b"# A\n\nNew A.\n")
    with _authorize(
        authority,
        organization_id,
        ControlOperation.SCHEDULE_FILE_CHANGE_PAGE,
        "schedule-current-superseding-page",
    ) as call:
        scheduled_newer = control.schedule_file_change_page(
            call,
            ScheduleFileChangePage(
                accepted_newer.source_ref,
                accepted_newer.source_version_ref,
                accepted_newer.page_ref,
                audience,
            ),
        )
    current_import = next(
        change.prepared_import
        for change in scheduled_newer.changes
        if change.path == FileImportPath("a.md")
    )
    current_token = PostgreSQLWorkerLeaseIssuer(
        guarded_control_engine,
        codec,
        lease_ttl_seconds=300,
    ).issue_file_import_lease(current_import)
    accepted_during_compile = []

    def supersede_during_compile(
        source_bytes: bytes,
        config: MarkdownCompilerConfig,
    ) -> object:
        (root / "a.md").write_bytes(b"# A\n\nNewest A.\n")
        current_source = replace(source, scan_head=accepted_newer.scan_head)
        latest = provider.read_changes(
            current_source,
            InitialScan(),
            ChangeLimit(2),
        )
        assert type(latest) is ProviderOk
        with _authorize(
            authority,
            organization_id,
            ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
            "accept-supersession-during-compile",
        ) as call:
            accepted_during_compile.append(
                control.accept_file_change_page(call, latest.value)
            )
        return compile_markdown_original(source_bytes, config)

    monkeypatch.setattr(
        "engine.persistence.file_imports.compile_markdown",
        supersede_during_compile,
    )
    content_read_count = 0
    with (
        FileRootRegistry(
            {source.source_version.root_ref: root},
            limits=FileReadLimits(max_file_bytes=1_024),
        ) as worker_roots,
        pytest.raises(FileImportUnavailable) as publication_failure,
    ):
        PostgreSQLFileImportWorker(
            guarded_worker_engine,
            codec,
            receiver,
            worker_roots,
            MarkdownCompilerConfig("markdown-config-v1"),
            clock=lambda: datetime.now(UTC).replace(microsecond=0),
        ).run(
            FileImportLeaseRedemption(
                current_token,
                organization_id,
                current_import.job_id,
                accepted_newer.source_ref,
            )
        )
    assert str(publication_failure.value) == "File publication is unavailable"
    assert content_read_count == 1
    assert len(accepted_during_compile) == 1
    assert accepted_during_compile[0].scan_epoch != accepted_newer.scan_epoch

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            superseded_effects = connection.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*) FROM file_acquisition
                       WHERE organization_id = :organization_id
                         AND change_page_ref = :page_ref),
                      (SELECT count(*) FROM context_resource
                       WHERE organization_id = :organization_id
                         AND active_revision_id IS NOT NULL),
                      (SELECT count(*) FROM revision_publication_event
                       WHERE organization_id = :organization_id
                         AND state = 'active'),
                      (SELECT count(*) FROM file_source_publish_watermark
                       WHERE organization_id = :organization_id
                         AND source_id = :source_id)
                    """
                ),
                {
                    "organization_id": organization_id,
                    "source_id": accepted_second.source_ref.value,
                    "page_ref": accepted_second.page_ref,
                },
            ).one()
    finally:
        migration_engine.dispose()
    assert tuple(superseded_effects) == (0, 0, 0, 0)


def test_scheduled_redeem_waits_for_progress_before_offboard_job_fence(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.md").write_bytes(b"# A\n\nScheduled.\n")
    (root / "manual.md").write_bytes(b"# Manual\n\nUnscheduled.\n")
    provider_proofs, control_proofs = _proofs()
    organization_id = uuid4()
    receiver = FileImportReceiver(uuid4())
    control, authority, source = _seed_file_change_source(
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
        organization_id=organization_id,
        receiver=receiver,
        root_ref=FileRootRef("scheduled-lock-order-root"),
        control_proofs=control_proofs,
    )
    provider = FileChangeProvider(
        FileRootRegistry(
            {source.source_version.root_ref: root},
            limits=FileReadLimits(max_file_bytes=1_024),
        ),
        proofs=provider_proofs,
    )
    page = provider.read_changes(source, InitialScan(), ChangeLimit(1))
    assert type(page) is ProviderOk
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-lock-order-page",
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
        with _authorize(
            authority,
            organization_id,
            ControlOperation.SCHEDULE_FILE_CHANGE_PAGE,
            "schedule-lock-order-page",
        ) as call:
            scheduled = control.schedule_file_change_page(
                call,
                ScheduleFileChangePage(
                    accepted.source_ref,
                    accepted.source_version_ref,
                    accepted.page_ref,
                    FileImportAudience(
                        "principal:file-reader",
                        membership_id,
                        1,
                    ),
                ),
            )
        prepared = scheduled.changes[0].prepared_import
        codec = WorkerLeaseCodec(
            WorkerLeaseKeyring(active_version=1, keys={1: bytes(range(64, 96))})
        )
        token = PostgreSQLWorkerLeaseIssuer(
            guarded_control_engine,
            codec,
            lease_ttl_seconds=300,
        ).issue_file_import_lease(prepared)
        with _authorize(
            authority,
            organization_id,
            ControlOperation.IMPORT_FILE,
            "prepare-manual-lock-order-import",
        ) as call:
            manual = control.prepare_file_import(
                call,
                PrepareFileImport(
                    accepted.source_ref,
                    FileImportPath("manual.md"),
                    FileImportAudience(
                        "principal:file-reader",
                        membership_id,
                        1,
                    ),
                    "manual-lock-order-import",
                ),
            )
        manual_token = PostgreSQLWorkerLeaseIssuer(
            guarded_control_engine,
            codec,
            lease_ttl_seconds=300,
        ).issue_file_import_lease(manual)

        with (
            FileRootRegistry(
                {source.source_version.root_ref: root},
                limits=FileReadLimits(max_file_bytes=1_024),
            ) as worker_roots,
            migration_engine.connect() as progress_connection,
        ):
            progress_transaction = progress_connection.begin()
            # Page acceptance uses this exact progress arbitration. Holding it here
            # lets production redemption and offboarding exercise their relative
            # lock order without adding a test-only hook to either operation.
            progress_connection.execute(
                text(
                    """
                    SELECT pg_catalog.pg_advisory_xact_lock(
                        pg_catalog.hashtextextended(
                            'context-engine.file-source-progress:'
                            || CAST(:organization_id AS text) || ':'
                            || CAST(:source_id AS text), 0
                        )
                    )
                    """
                ),
                {
                    "organization_id": organization_id,
                    "source_id": accepted.source_ref.value,
                },
            )
            worker = PostgreSQLFileImportWorker(
                guarded_worker_engine,
                codec,
                receiver,
                worker_roots,
                MarkdownCompilerConfig("markdown-config-v1"),
                clock=lambda: datetime.now(UTC).replace(microsecond=0),
            )

            def redeem() -> object:
                return worker.run(
                    FileImportLeaseRedemption(
                        token,
                        organization_id,
                        prepared.job_id,
                        accepted.source_ref,
                    )
                )

            def offboard() -> FileSourceOffboarding:
                with _authorize(
                    authority,
                    organization_id,
                    ControlOperation.OFFBOARD_FILE_SOURCE,
                    "offboard-during-scheduled-redemption",
                ) as call:
                    return control.offboard_file_source(
                        call,
                        OffboardFileSource(accepted.source_ref),
                    )

            with ThreadPoolExecutor(max_workers=2) as executor:
                manual_redemption = executor.submit(
                    worker.run,
                    FileImportLeaseRedemption(
                        manual_token,
                        organization_id,
                        manual.job_id,
                        accepted.source_ref,
                    ),
                )
                assert manual_redemption.result(timeout=5).effect_count == 1
                redemption = executor.submit(redeem)
                deadline = monotonic() + 10
                redemption_waiting = False
                while monotonic() < deadline:
                    with migration_engine.connect() as observer:
                        redemption_waiting = bool(
                            observer.execute(
                                text(
                                    """
                                    SELECT EXISTS (
                                        SELECT 1
                                        FROM pg_catalog.pg_stat_activity AS activity
                                        JOIN pg_catalog.pg_locks AS held_lock
                                          ON held_lock.pid = activity.pid
                                        WHERE activity.usename =
                                              'context_engine_worker'
                                          AND held_lock.locktype = 'advisory'
                                          AND held_lock.granted IS FALSE
                                    )
                                    """
                                )
                            ).scalar_one()
                        )
                    if redemption_waiting:
                        break
                    sleep(0.01)
                if not redemption_waiting:
                    progress_transaction.rollback()
                    redemption.result(timeout=5)
                    pytest.fail("scheduled redemption did not wait for progress")

                offboarding = executor.submit(offboard)
                try:
                    offboarded = offboarding.result(timeout=5)
                finally:
                    if progress_transaction.is_active:
                        progress_transaction.rollback()
                assert offboarded.cancelled_job_count >= 1
                with pytest.raises((FileImportUnavailable, WorkNotAvailable)):
                    redemption.result(timeout=5)
    finally:
        migration_engine.dispose()


def test_scheduled_redeem_rechecks_expiry_after_waiting_for_progress(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    migration_configuration: DatabaseConfiguration,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.md").write_bytes(b"# A\n\nExpires while waiting.\n")
    provider_proofs, control_proofs = _proofs()
    organization_id = uuid4()
    receiver = FileImportReceiver(uuid4())
    control, authority, source = _seed_file_change_source(
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
        organization_id=organization_id,
        receiver=receiver,
        root_ref=FileRootRef("scheduled-expiry-fence-root"),
        control_proofs=control_proofs,
    )
    provider = FileChangeProvider(
        FileRootRegistry(
            {source.source_version.root_ref: root},
            limits=FileReadLimits(max_file_bytes=1_024),
        ),
        proofs=provider_proofs,
    )
    page = provider.read_changes(source, InitialScan(), ChangeLimit(1))
    assert type(page) is ProviderOk
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-expiring-scheduled-page",
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
        with _authorize(
            authority,
            organization_id,
            ControlOperation.SCHEDULE_FILE_CHANGE_PAGE,
            "schedule-expiring-page",
        ) as call:
            prepared = (
                control.schedule_file_change_page(
                    call,
                    ScheduleFileChangePage(
                        accepted.source_ref,
                        accepted.source_version_ref,
                        accepted.page_ref,
                        FileImportAudience(
                            "principal:file-reader",
                            membership_id,
                            1,
                        ),
                    ),
                )
                .changes[0]
                .prepared_import
            )
        codec = WorkerLeaseCodec(
            WorkerLeaseKeyring(active_version=1, keys={1: bytes(range(64, 96))})
        )
        token = PostgreSQLWorkerLeaseIssuer(
            guarded_control_engine,
            codec,
            lease_ttl_seconds=2,
        ).issue_file_import_lease(prepared)
        content_read_count = 0
        original_read = FileRootRegistry.read

        def track_content_read(
            registry: FileRootRegistry,
            root_ref: FileRootRef,
            relative_path: FileImportPath,
        ) -> bytes:
            nonlocal content_read_count
            content_read_count += 1
            return original_read(registry, root_ref, relative_path)

        monkeypatch.setattr(FileRootRegistry, "read", track_content_read)
        with (
            FileRootRegistry(
                {source.source_version.root_ref: root},
                limits=FileReadLimits(max_file_bytes=1_024),
            ) as worker_roots,
            migration_engine.connect() as progress_connection,
        ):
            progress_transaction = progress_connection.begin()
            progress_connection.execute(
                text(
                    """
                    SELECT pg_catalog.pg_advisory_xact_lock(
                        pg_catalog.hashtextextended(
                            'context-engine.file-source-progress:'
                            || CAST(:organization_id AS text) || ':'
                            || CAST(:source_id AS text), 0
                        )
                    )
                    """
                ),
                {
                    "organization_id": organization_id,
                    "source_id": accepted.source_ref.value,
                },
            )
            worker = PostgreSQLFileImportWorker(
                guarded_worker_engine,
                codec,
                receiver,
                worker_roots,
                MarkdownCompilerConfig("markdown-config-v1"),
                clock=lambda: datetime.now(UTC).replace(microsecond=0),
            )
            with ThreadPoolExecutor(max_workers=1) as executor:
                redemption = executor.submit(
                    worker.run,
                    FileImportLeaseRedemption(
                        token,
                        organization_id,
                        prepared.job_id,
                        accepted.source_ref,
                    ),
                )
                deadline = monotonic() + 10
                waiting = False
                while monotonic() < deadline:
                    waiting = bool(
                        progress_connection.execute(
                            text(
                                """
                                SELECT EXISTS (
                                    SELECT 1
                                    FROM pg_catalog.pg_stat_activity AS activity
                                    JOIN pg_catalog.pg_locks AS held_lock
                                      ON held_lock.pid = activity.pid
                                    WHERE activity.usename = 'context_engine_worker'
                                      AND held_lock.locktype = 'advisory'
                                      AND held_lock.granted IS FALSE
                                )
                                """
                            )
                        ).scalar_one()
                    )
                    if waiting:
                        break
                    sleep(0.01)
                if not waiting:
                    progress_transaction.rollback()
                    redemption.result(timeout=5)
                    pytest.fail("scheduled redemption did not wait for progress")
                progress_connection.execute(
                    text(
                        """
                        SELECT pg_catalog.pg_sleep(
                            GREATEST(
                                EXTRACT(EPOCH FROM (
                                    lease_expires_at
                                    - pg_catalog.clock_timestamp()
                                )) + 0.05,
                                0
                            )::double precision
                        )
                        FROM public.file_import_job
                        WHERE organization_id = :organization_id
                          AND job_id = :job_id
                        """
                    ),
                    {
                        "organization_id": organization_id,
                        "job_id": prepared.job_id,
                    },
                ).scalar_one()
                progress_transaction.rollback()
                with pytest.raises(WorkNotAvailable):
                    redemption.result(timeout=5)

        with migration_engine.connect() as connection:
            state = connection.execute(
                text(
                    "SELECT state, lease_redeemed_at FROM file_import_job "
                    "WHERE organization_id = :organization_id AND job_id = :job_id"
                ),
                {
                    "organization_id": organization_id,
                    "job_id": prepared.job_id,
                },
            ).one()
        assert tuple(state) == ("leased", None)
        assert content_read_count == 0
    finally:
        migration_engine.dispose()


def test_scheduled_pages_reuse_unchanged_and_replaced_publication_paths(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    guarded_runtime_engine: Engine,
    migration_configuration: DatabaseConfiguration,
    query_digest_keyring: QueryDigestKeyring,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    path = root / "handbook.md"
    initial_bytes = b"# Handbook\n\nStable paragraph.\n"
    path.write_bytes(initial_bytes)
    provider_proofs, control_proofs = _proofs()
    organization_id = uuid4()
    receiver = FileImportReceiver(uuid4())
    control, authority, current_source = _seed_file_change_source(
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
        organization_id=organization_id,
        receiver=receiver,
        root_ref=FileRootRef("scheduled-outcomes-root"),
        control_proofs=control_proofs,
    )
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            membership_id, user_id = connection.execute(
                text(
                    "SELECT membership_id, user_id FROM membership "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": organization_id},
            ).one()
    finally:
        migration_engine.dispose()
    audience = FileImportAudience("principal:file-reader", membership_id, 1)
    codec = WorkerLeaseCodec(
        WorkerLeaseKeyring(active_version=1, keys={1: bytes(range(64, 96))})
    )

    outcomes = []
    job_ids = []
    candidate_sets = []
    for iteration, payload in enumerate(
        (
            initial_bytes,
            b"\xef\xbb\xbf# Handbook\r\n\r\nStable paragraph.\r\n",
            b"# Handbook\n\nChanged paragraph.\n",
        ),
        start=1,
    ):
        path.write_bytes(payload)
        provider = FileChangeProvider(
            FileRootRegistry(
                {current_source.source_version.root_ref: root},
                limits=FileReadLimits(max_file_bytes=1_024),
            ),
            proofs=provider_proofs,
        )
        page = provider.read_changes(current_source, InitialScan(), ChangeLimit(1))
        assert type(page) is ProviderOk
        with _authorize(
            authority,
            organization_id,
            ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
            f"accept-outcome-page-{iteration}",
        ) as call:
            accepted = control.accept_file_change_page(call, page.value)
        with _authorize(
            authority,
            organization_id,
            ControlOperation.SCHEDULE_FILE_CHANGE_PAGE,
            f"schedule-outcome-page-{iteration}",
        ) as call:
            scheduled = control.schedule_file_change_page(
                call,
                ScheduleFileChangePage(
                    accepted.source_ref,
                    accepted.source_version_ref,
                    accepted.page_ref,
                    audience,
                ),
            )
        prepared = scheduled.changes[0].prepared_import
        token = PostgreSQLWorkerLeaseIssuer(
            guarded_control_engine,
            codec,
            lease_ttl_seconds=300,
        ).issue_file_import_lease(prepared)
        with FileRootRegistry(
            {current_source.source_version.root_ref: root},
            limits=FileReadLimits(max_file_bytes=1_024),
        ) as worker_roots:
            result = PostgreSQLFileImportWorker(
                guarded_worker_engine,
                codec,
                receiver,
                worker_roots,
                MarkdownCompilerConfig("markdown-config-v1"),
                clock=lambda: datetime.now(UTC).replace(microsecond=0),
            ).run(
                FileImportLeaseRedemption(
                    token,
                    organization_id,
                    prepared.job_id,
                    accepted.source_ref,
                )
            )
        outcomes.append(result.outcome)
        job_ids.append(prepared.job_id)
        candidate_sets.append(result.candidate_refs)
        current_source = replace(current_source, scan_head=accepted.scan_head)

    assert outcomes == ["published", "unchanged", "replaced"]
    assert len(set(job_ids)) == 3
    assert candidate_sets[1] == candidate_sets[0]
    assert candidate_sets[2] != candidate_sets[1]

    active_candidate = candidate_sets[2][0]
    ensure_test_runtime_release(
        organization_id,
        active_revision_refs=(active_candidate.revision_ref,),
    )
    try:
        response = TestClient(
            create_app(
                authenticator=_RuntimeAuthenticator(
                    organization_id,
                    user_id,
                    membership_id,
                ),
                organization_authority=_OrganizationAuthority(),
                membership_authority=PostgreSQLMembershipAuthority(
                    guarded_runtime_engine
                ),
                scope_authority=_ExactScopeAuthority(
                    active_candidate.source_ref,
                    active_candidate.resource_ref,
                ),
                runtime=Runtime(
                    required_kernel_dependencies(),
                    candidate_index=PostgreSQLExactPhraseCandidateIndex(),
                    clock=lambda: NOW,
                    query_digest_keyring=query_digest_keyring,
                ),
                clock=lambda: NOW,
                request_id_factory=lambda: "scheduled-file-v0-resolve",
            )
        ).post(
            "/v0/resolve",
            headers={
                "Authorization": "Bearer runtime-secret",
                "X-Context-Request-Id": "scheduled-file-v0-resolve",
            },
            json={
                "kind": "acquire",
                "need": {"query": "Changed paragraph."},
            },
        )
        assert response.status_code == 200
        package = response.json()["package"]
        assert package["blocks"][0]["text"] == "Changed paragraph."
        assert package["evidence"][0]["revisionRef"] == (active_candidate.revision_ref)
    finally:
        clear_test_runtime_release(organization_id)

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            effects = connection.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*) FROM context_revision
                       WHERE organization_id = :organization_id),
                      (SELECT count(*) FROM file_acquisition_result
                       WHERE organization_id = :organization_id),
                      (SELECT count(*) FROM file_source_publish_watermark
                       WHERE organization_id = :organization_id
                         AND source_id = :source_id)
                    """
                ),
                {
                    "organization_id": organization_id,
                    "source_id": current_source.source_version.source_ref.value,
                },
            ).one()
    finally:
        migration_engine.dispose()
    assert tuple(effects) == (2, 1, 3)


def test_file_change_scheduling_refuses_incomplete_accepted_lineage(
    tmp_path: Path,
    guarded_control_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.md").write_bytes(b"A")
    (root / "b.md").write_bytes(b"B")
    provider_proofs, control_proofs = _proofs()
    organization_id = uuid4()
    receiver = FileImportReceiver(uuid4())
    control, authority, source = _seed_file_change_source(
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
        organization_id=organization_id,
        receiver=receiver,
        root_ref=FileRootRef("schedule-partial-root"),
        control_proofs=control_proofs,
    )
    provider = FileChangeProvider(
        FileRootRegistry(
            {source.source_version.root_ref: root},
            limits=FileReadLimits(max_file_bytes=1_024),
        ),
        proofs=provider_proofs,
    )
    page = provider.read_changes(source, InitialScan(), ChangeLimit(2))
    assert type(page) is ProviderOk
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-partial-page",
    ) as call:
        accepted = control.accept_file_change_page(call, page.value)

    partial_acquisition_id = uuid4()
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            membership_id = connection.execute(
                text(
                    "SELECT membership_id FROM membership "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": organization_id},
            ).scalar_one()
            first_change = connection.execute(
                text(
                    """
                    SELECT change_ordinal, relative_path, content_sha256,
                           content_length
                    FROM file_source_change
                    WHERE organization_id = :organization_id
                      AND source_id = :source_id
                      AND page_ref = :page_ref
                    ORDER BY change_ordinal
                    LIMIT 1
                    """
                ),
                {
                    "organization_id": organization_id,
                    "source_id": accepted.source_ref.value,
                    "page_ref": accepted.page_ref,
                },
            ).one()
            connection.execute(
                text(
                    """
                    INSERT INTO file_acquisition (
                        organization_id, acquisition_id, source_id,
                        source_version_id, relative_path,
                        audience_principal_ref, audience_membership_id,
                        audience_membership_version, idempotency_key,
                        request_digest, created_at, change_page_ref,
                        change_ordinal, expected_content_sha256,
                        expected_content_length
                    ) VALUES (
                        :organization_id, :acquisition_id, :source_id,
                        :source_version_id, :relative_path,
                        'principal:file-reader', :membership_id, 1,
                        'injected-partial-lineage', :request_digest,
                        statement_timestamp(), :page_ref, :change_ordinal,
                        :content_sha256, :content_length
                    )
                    """
                ),
                {
                    "organization_id": organization_id,
                    "acquisition_id": partial_acquisition_id,
                    "source_id": accepted.source_ref.value,
                    "source_version_id": accepted.source_version_ref,
                    "relative_path": first_change.relative_path,
                    "membership_id": membership_id,
                    "request_digest": "d" * 64,
                    "page_ref": accepted.page_ref,
                    "change_ordinal": first_change.change_ordinal,
                    "content_sha256": first_change.content_sha256,
                    "content_length": first_change.content_length,
                },
            )
    finally:
        migration_engine.dispose()

    command_to_schedule = ScheduleFileChangePage(
        accepted.source_ref,
        accepted.source_version_ref,
        accepted.page_ref,
        FileImportAudience("principal:file-reader", membership_id, 1),
    )
    with (
        _authorize(
            authority,
            organization_id,
            ControlOperation.SCHEDULE_FILE_CHANGE_PAGE,
            "schedule-partial-page",
        ) as call,
        pytest.raises(SourceNotAvailable),
    ):
        control.schedule_file_change_page(call, command_to_schedule)

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            effects = connection.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*) FROM file_acquisition
                       WHERE organization_id = :organization_id
                         AND change_page_ref = :page_ref),
                      (SELECT count(*) FROM file_import_job
                       WHERE organization_id = :organization_id
                         AND source_id = :source_id)
                    """
                ),
                {
                    "organization_id": organization_id,
                    "source_id": accepted.source_ref.value,
                    "page_ref": accepted.page_ref,
                },
            ).one()
    finally:
        migration_engine.dispose()
    assert tuple(effects) == (1, 1)


def test_file_change_scheduling_refuses_missing_generated_job_identity(
    tmp_path: Path,
    guarded_control_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.md").write_bytes(b"A")
    (root / "b.md").write_bytes(b"B")
    provider_proofs, control_proofs = _proofs()
    organization_id = uuid4()
    receiver = FileImportReceiver(uuid4())
    control, authority, source = _seed_file_change_source(
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
        organization_id=organization_id,
        receiver=receiver,
        root_ref=FileRootRef("schedule-missing-job-root"),
        control_proofs=control_proofs,
    )
    provider = FileChangeProvider(
        FileRootRegistry(
            {source.source_version.root_ref: root},
            limits=FileReadLimits(max_file_bytes=1_024),
        ),
        proofs=provider_proofs,
    )
    page = provider.read_changes(source, InitialScan(), ChangeLimit(2))
    assert type(page) is ProviderOk
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-missing-job-page",
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
    command_to_schedule = ScheduleFileChangePage(
        accepted.source_ref,
        accepted.source_version_ref,
        accepted.page_ref,
        FileImportAudience("principal:file-reader", membership_id, 1),
    )
    with _authorize(
        authority,
        organization_id,
        ControlOperation.SCHEDULE_FILE_CHANGE_PAGE,
        "schedule-before-missing-job",
    ) as call:
        scheduled = control.schedule_file_change_page(call, command_to_schedule)
    missing_job_id = scheduled.changes[1].prepared_import.job_id

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE file_source_acquisition_checkpoint DISABLE "
                    "TRIGGER file_source_acquisition_checkpoint_immutable"
                )
            )
        try:
            with migration_engine.begin() as connection:
                connection.execute(
                    text(
                        "DELETE FROM file_source_acquisition_checkpoint "
                        "WHERE organization_id = :organization_id "
                        "AND job_id = :job_id"
                    ),
                    {
                        "organization_id": organization_id,
                        "job_id": missing_job_id,
                    },
                )
                connection.execute(
                    text(
                        "DELETE FROM file_import_job "
                        "WHERE organization_id = :organization_id "
                        "AND job_id = :job_id"
                    ),
                    {
                        "organization_id": organization_id,
                        "job_id": missing_job_id,
                    },
                )
        finally:
            with migration_engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE file_source_acquisition_checkpoint ENABLE "
                        "TRIGGER file_source_acquisition_checkpoint_immutable"
                    )
                )
    finally:
        migration_engine.dispose()

    with (
        _authorize(
            authority,
            organization_id,
            ControlOperation.SCHEDULE_FILE_CHANGE_PAGE,
            "replay-missing-job",
        ) as call,
        pytest.raises(SourceNotAvailable),
    ):
        control.schedule_file_change_page(call, command_to_schedule)

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            effects = connection.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*) FROM file_acquisition
                       WHERE organization_id = :organization_id
                         AND change_page_ref = :page_ref),
                      (SELECT count(*) FROM file_import_job AS job
                       JOIN file_acquisition AS acquisition
                         ON acquisition.organization_id = job.organization_id
                        AND acquisition.acquisition_id = job.acquisition_id
                       WHERE acquisition.organization_id = :organization_id
                         AND acquisition.change_page_ref = :page_ref)
                    """
                ),
                {
                    "organization_id": organization_id,
                    "page_ref": accepted.page_ref,
                },
            ).one()
    finally:
        migration_engine.dispose()
    assert tuple(effects) == (2, 1)


@pytest.mark.security_evidence(
    id="PG-FILE-CHANGE-SCHEDULE-DENY-083",
    layer="postgres",
)
def test_file_change_scheduling_refuses_changed_or_stale_authority_atomically(
    tmp_path: Path,
    guarded_control_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.md").write_bytes(b"# A\n\nOriginal paragraph.\n")
    (root / "b.md").write_bytes(b"# B\n\nSecond paragraph.\n")
    provider_proofs, control_proofs = _proofs()
    organization_id = uuid4()
    receiver = FileImportReceiver(uuid4())
    control, authority, source = _seed_file_change_source(
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
        organization_id=organization_id,
        receiver=receiver,
        root_ref=FileRootRef("schedule-deny-root"),
        control_proofs=control_proofs,
    )
    provider = FileChangeProvider(
        FileRootRegistry(
            {source.source_version.root_ref: root},
            limits=FileReadLimits(max_file_bytes=1_024),
        ),
        proofs=provider_proofs,
    )
    page = provider.read_changes(source, InitialScan(), ChangeLimit(2))
    assert type(page) is ProviderOk
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-deny-page",
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
    command_to_schedule = ScheduleFileChangePage(
        accepted.source_ref,
        accepted.source_version_ref,
        accepted.page_ref,
        FileImportAudience(
            "principal:file-reader",
            membership_id,
            1,
        ),
    )
    with _authorize(
        authority,
        organization_id,
        ControlOperation.SCHEDULE_FILE_CHANGE_PAGE,
        "schedule-deny-baseline",
    ) as call:
        scheduled = control.schedule_file_change_page(call, command_to_schedule)

    changed_audience = replace(
        command_to_schedule,
        audience=FileImportAudience("principal:changed", membership_id, 1),
    )
    with (
        _authorize(
            authority,
            organization_id,
            ControlOperation.SCHEDULE_FILE_CHANGE_PAGE,
            "schedule-changed-audience",
        ) as call,
        pytest.raises(SourceNotAvailable),
    ):
        control.schedule_file_change_page(call, changed_audience)

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE service_principal SET enabled = false "
                    "WHERE organization_id = :organization_id "
                    "AND service_principal_id = :receiver_id"
                ),
                {
                    "organization_id": organization_id,
                    "receiver_id": receiver.service_principal_id,
                },
            )
    finally:
        migration_engine.dispose()
    with (
        _authorize(
            authority,
            organization_id,
            ControlOperation.SCHEDULE_FILE_CHANGE_PAGE,
            "schedule-disabled-receiver",
        ) as call,
        pytest.raises(SourceNotAvailable),
    ):
        control.schedule_file_change_page(call, command_to_schedule)

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE service_principal SET enabled = true "
                    "WHERE organization_id = :organization_id "
                    "AND service_principal_id = :receiver_id"
                ),
                {
                    "organization_id": organization_id,
                    "receiver_id": receiver.service_principal_id,
                },
            )
            connection.execute(
                text(
                    "UPDATE membership SET status = 'revoked' "
                    "WHERE organization_id = :organization_id "
                    "AND membership_id = :membership_id"
                ),
                {
                    "organization_id": organization_id,
                    "membership_id": membership_id,
                },
            )
    finally:
        migration_engine.dispose()
    with (
        _authorize(
            authority,
            organization_id,
            ControlOperation.SCHEDULE_FILE_CHANGE_PAGE,
            "schedule-stale-membership",
        ) as call,
        pytest.raises(SourceNotAvailable),
    ):
        control.schedule_file_change_page(call, command_to_schedule)

    with _authorize(
        authority,
        organization_id,
        ControlOperation.OFFBOARD_FILE_SOURCE,
        "offboard-before-schedule-replay",
    ) as call:
        control.offboard_file_source(
            call,
            OffboardFileSource(accepted.source_ref),
        )
    with (
        _authorize(
            authority,
            organization_id,
            ControlOperation.SCHEDULE_FILE_CHANGE_PAGE,
            "schedule-disabled-source",
        ) as call,
        pytest.raises(SourceNotAvailable),
    ):
        control.schedule_file_change_page(call, command_to_schedule)

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            counts = connection.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*) FROM file_acquisition
                       WHERE organization_id = :organization_id
                         AND source_id = :source_id
                         AND change_page_ref = :page_ref),
                      (SELECT count(*) FROM file_import_job
                       WHERE organization_id = :organization_id
                         AND job_id = ANY(CAST(:job_ids AS uuid[])))
                    """
                ),
                {
                    "organization_id": organization_id,
                    "source_id": accepted.source_ref.value,
                    "page_ref": accepted.page_ref,
                    "job_ids": [
                        change.prepared_import.job_id for change in scheduled.changes
                    ],
                },
            ).one()
    finally:
        migration_engine.dispose()
    assert tuple(counts) == (2, 2)


@pytest.mark.security_evidence(id="PG-FILE-CHANGE-PAGE-081", layer="postgres")
def test_control_atomically_accepts_ordered_file_pages_and_rejects_regression(
    tmp_path: Path,
    guarded_control_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.md").write_bytes(b"A")
    (root / "b.md").write_bytes(b"B")
    provider_proofs, control_proofs = _proofs()
    organization_id = uuid4()
    control, authority, source = _seed_file_change_source(
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
        organization_id=organization_id,
        receiver=FileImportReceiver(uuid4()),
        root_ref=FileRootRef("change-page-root"),
        control_proofs=control_proofs,
    )
    registry = FileRootRegistry(
        {source.source_version.root_ref: root},
        limits=FileReadLimits(max_file_bytes=1_024),
    )
    provider = FileChangeProvider(registry, proofs=provider_proofs)
    first = provider.read_changes(source, InitialScan(), ChangeLimit(1))
    assert type(first) is ProviderOk
    assert (
        type(
            provider.read_changes(source, first.value.next_cursor, ChangeLimit(1))  # type: ignore[arg-type]
        )
        is ProviderGenericDenied
    )

    verified_first = control_proofs.verify_page(first.value)
    assert verified_first is not None

    changes_document = [
        {
            "contentLength": change.content_length,
            "contentSha256": change.content_sha256,
            "kind": change.kind.value,
            "path": change.path.value,
        }
        for change in first.value.changes
    ]
    with guarded_control_engine.connect() as connection:
        interrupted = connection.begin()
        receipt = connection.execute(
            text(
                """
                SELECT *
                FROM public.context_control_accept_file_change_page(
                    :organization_id, :source_id, :source_version_id,
                    :scan_ref, :scan_epoch, :page_limit, :page_ref,
                        :predecessor_page_ref,
                        :predecessor_checkpoint_ref, :predecessor_sequence,
                        :superseded_scan_epoch,
                        CAST(:changes AS jsonb), :complete
                )
                """
            ),
            {
                "organization_id": organization_id,
                "source_id": first.value.source_ref,
                "source_version_id": first.value.source_version_ref,
                "scan_ref": first.value.scan_ref,
                "scan_epoch": first.value.scan_epoch,
                "page_limit": first.value.page_limit,
                "page_ref": verified_first.page_ref,
                "predecessor_page_ref": first.value.predecessor_page_ref,
                "predecessor_checkpoint_ref": (first.value.predecessor_checkpoint_ref),
                "predecessor_sequence": first.value.predecessor_sequence,
                "superseded_scan_epoch": first.value.superseded_scan_epoch,
                "changes": json.dumps(changes_document, separators=(",", ":")),
                "complete": first.value.complete,
            },
        ).one()
        assert receipt.page_ref == verified_first.page_ref
        interrupted.rollback()

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            rolled_back = connection.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*) FROM file_source_change_page
                       WHERE organization_id = :org AND source_id = :source),
                      (SELECT count(*) FROM file_source_change
                       WHERE organization_id = :org AND source_id = :source),
                      (SELECT count(*) FROM file_source_acquisition_checkpoint
                       WHERE organization_id = :org AND source_id = :source)
                    """
                ),
                {
                    "org": organization_id,
                    "source": source.source_version.source_ref.value,
                },
            ).one()
    finally:
        migration_engine.dispose()
    assert tuple(rolled_back) == (0, 0, 1)

    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-first",
    ) as call:
        accepted_first = control.accept_file_change_page(call, first.value)
    assert accepted_first.next_cursor is not None
    with _authorize(
        authority,
        organization_id,
        ControlOperation.READ_SOURCE_PROGRESS,
        "recover-scan-head-after-restart",
    ) as call:
        recovered = control.read_file_source_progress(
            call, source.source_version.source_ref
        )
    assert recovered.change_scan_head == accepted_first.scan_head
    source = replace(source, scan_head=recovered.change_scan_head)
    provider = FileChangeProvider(registry, proofs=provider_proofs)

    forged_predecessor = replace(
        first.value,
        predecessor_page_ref=accepted_first.page_ref,
        predecessor_checkpoint_ref="facp_" + "f" * 64,
        predecessor_sequence=accepted_first.sequence,
    )
    forged_verified = control_proofs.verify_page(forged_predecessor)
    assert forged_verified is None

    second = provider.read_changes(source, accepted_first.next_cursor, ChangeLimit(1))
    assert type(second) is ProviderOk
    second_changes_document = [
        {
            "contentLength": change.content_length,
            "contentSha256": change.content_sha256,
            "kind": change.kind.value,
            "path": change.path.value,
        }
        for change in second.value.changes
    ]
    second_verified = control_proofs.verify_page(second.value)
    assert second_verified is not None
    with guarded_control_engine.begin() as connection:
        out_of_order = connection.execute(
            text(
                """
                SELECT *
                FROM public.context_control_accept_file_change_page(
                    :organization_id, :source_id, :source_version_id,
                    :scan_ref, :scan_epoch, :page_limit, :page_ref,
                    :predecessor_page_ref, :predecessor_checkpoint_ref,
                    :predecessor_sequence, :superseded_scan_epoch,
                    CAST(:changes AS jsonb), :complete
                )
                """
            ),
            {
                "organization_id": organization_id,
                "source_id": second.value.source_ref,
                "source_version_id": second.value.source_version_ref,
                "scan_ref": second.value.scan_ref,
                "scan_epoch": second.value.scan_epoch,
                "page_limit": second.value.page_limit,
                "page_ref": second_verified.page_ref,
                "predecessor_page_ref": second.value.predecessor_page_ref,
                "predecessor_checkpoint_ref": "facp_" + "f" * 64,
                "predecessor_sequence": second.value.predecessor_sequence,
                "superseded_scan_epoch": second.value.superseded_scan_epoch,
                "changes": json.dumps(second_changes_document, separators=(",", ":")),
                "complete": second.value.complete,
            },
        ).one_or_none()
    assert out_of_order is None
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-second",
    ) as call:
        accepted_second = control.accept_file_change_page(call, second.value)
    source = replace(source, scan_head=accepted_second.scan_head)
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "replay-first",
    ) as call:
        replay_first = control.accept_file_change_page(call, first.value)

    assert accepted_second.complete is True
    assert accepted_second.next_cursor is None
    assert replay_first == accepted_first

    unchanged = provider.read_changes(source, InitialScan(), ChangeLimit(1))
    assert type(unchanged) is ProviderOk
    assert unchanged.value == first.value
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "unchanged-rescan",
    ) as call:
        assert control.accept_file_change_page(call, unchanged.value) == accepted_first
    changed_limit = provider.read_changes(source, InitialScan(), ChangeLimit(2))
    assert type(changed_limit) is ProviderOk
    assert changed_limit.value == first.value
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "unchanged-rescan-changed-limit",
    ) as call:
        assert control.accept_file_change_page(call, changed_limit.value) == (
            accepted_first
        )

    (root / "a.md").write_bytes(b"A2")
    changed_first = provider.read_changes(source, InitialScan(), ChangeLimit(1))
    assert type(changed_first) is ProviderOk
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-changed-first",
    ) as call:
        accepted_changed_first = control.accept_file_change_page(
            call, changed_first.value
        )
    source = replace(source, scan_head=accepted_changed_first.scan_head)
    assert accepted_changed_first.next_cursor is not None

    changed_second_before_aba = provider.read_changes(
        source, accepted_changed_first.next_cursor, ChangeLimit(1)
    )
    assert type(changed_second_before_aba) is ProviderOk
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-changed-second-before-aba",
    ) as call:
        accepted_changed_second_before_aba = control.accept_file_change_page(
            call, changed_second_before_aba.value
        )
    source = replace(source, scan_head=accepted_changed_second_before_aba.scan_head)
    assert accepted_changed_second_before_aba.complete is True

    (root / "a.md").write_bytes(b"A")
    stale_aba_second = provider.read_changes(
        source, accepted_first.next_cursor, ChangeLimit(1)
    )
    assert type(stale_aba_second) is ProviderInvalidCheckpoint

    (root / "a.md").write_bytes(b"A2")
    changed_first_after_aba = provider.read_changes(
        source, InitialScan(), ChangeLimit(1)
    )
    assert type(changed_first_after_aba) is ProviderOk
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-changed-first-after-aba",
    ) as call:
        accepted_changed_first_after_aba = control.accept_file_change_page(
            call, changed_first_after_aba.value
        )
    assert accepted_changed_first_after_aba == accepted_changed_first
    source = replace(source, scan_head=accepted_changed_first_after_aba.scan_head)
    assert accepted_changed_first_after_aba.next_cursor is not None
    changed_second = provider.read_changes(
        source,
        accepted_changed_first_after_aba.next_cursor,
        ChangeLimit(1),
    )
    assert type(changed_second) is ProviderOk
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-changed-second",
    ) as call:
        accepted_changed_second = control.accept_file_change_page(
            call, changed_second.value
        )
    assert accepted_changed_second == accepted_changed_second_before_aba
    assert accepted_changed_second.complete is True
    with _authorize(
        authority,
        organization_id,
        ControlOperation.READ_SOURCE_PROGRESS,
        "read-page-checkpoint",
    ) as call:
        progress = control.read_file_source_progress(
            call, source.source_version.source_ref
        )
    assert progress.acquisition_checkpoint is not None
    assert (
        progress.acquisition_checkpoint.change_kind
        is FileSourceChangeKind.FILE_CHANGE_PAGE
    )
    assert progress.acquisition_checkpoint.source_version_ref == (
        source.source_version.version_ref
    )
    assert (
        progress.acquisition_checkpoint.change_page_ref
        == accepted_changed_second.page_ref
    )
    assert progress.publish_watermark is None
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            counts = connection.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*) FROM file_source_change_page
                       WHERE organization_id = :org AND source_id = :source),
                      (SELECT count(*) FROM file_source_change
                       WHERE organization_id = :org AND source_id = :source),
                      (SELECT count(*) FROM file_source_acquisition_checkpoint
                       WHERE organization_id = :org AND source_id = :source),
                      (SELECT count(*) FROM file_import_job
                       WHERE organization_id = :org AND source_id = :source),
                      (SELECT count(*) FROM file_source_publish_watermark
                       WHERE organization_id = :org AND source_id = :source)
                    """
                ),
                {
                    "org": organization_id,
                    "source": source.source_version.source_ref.value,
                },
            ).one()
    finally:
        migration_engine.dispose()
    assert tuple(counts) == (4, 4, 5, 1, 0)


@pytest.mark.security_evidence(id="PG-FILE-CHANGE-DENY-081", layer="postgres")
def test_file_page_acceptance_fails_closed_after_disable_and_cross_organization(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_runtime_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.md").write_bytes(b"A")
    provider_proofs, control_proofs = _proofs()
    organization_a, organization_b = uuid4(), uuid4()
    control_a, authority_a, source = _seed_file_change_source(
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
        organization_id=organization_a,
        receiver=FileImportReceiver(uuid4()),
        root_ref=FileRootRef("disabled-change-root"),
        control_proofs=control_proofs,
    )
    control_b, authority_b, source_b = _seed_file_change_source(
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
        organization_id=organization_b,
        receiver=FileImportReceiver(uuid4()),
        root_ref=FileRootRef("independent-change-root-b"),
        control_proofs=control_proofs,
    )
    root_b = tmp_path / "root-b"
    root_b.mkdir()
    (root_b / "b.md").write_bytes(b"B")
    provider = FileChangeProvider(
        FileRootRegistry(
            {source.source_version.root_ref: root},
            limits=FileReadLimits(max_file_bytes=1_024),
        ),
        proofs=provider_proofs,
    )
    page = provider.read_changes(source, InitialScan(), ChangeLimit(1))
    assert type(page) is ProviderOk
    with _authorize(
        authority_a,
        organization_a,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-old-version-head",
    ) as call:
        accepted_old = control_a.accept_file_change_page(call, page.value)

    provider_b = FileChangeProvider(
        FileRootRegistry(
            {source_b.source_version.root_ref: root_b},
            limits=FileReadLimits(max_file_bytes=1_024),
        ),
        proofs=provider_proofs,
    )
    page_b = provider_b.read_changes(source_b, InitialScan(), ChangeLimit(1))
    assert type(page_b) is ProviderOk
    with _authorize(
        authority_b,
        organization_b,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-org-b-own-page",
    ) as call:
        accepted_b = control_b.accept_file_change_page(call, page_b.value)
    assert accepted_b.source_ref == source_b.source_version.source_ref

    with (
        _authorize(
            authority_b,
            organization_b,
            ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
            "cross-org",
        ) as call,
        pytest.raises(SourceNotAvailable),
    ):
        control_b.accept_file_change_page(call, page.value)

    page_verified = control_proofs.verify_page(page.value)
    assert page_verified is not None
    page_changes_document = [
        {
            "contentLength": change.content_length,
            "contentSha256": change.content_sha256,
            "kind": change.kind.value,
            "path": change.path.value,
        }
        for change in page.value.changes
    ]
    with guarded_control_engine.begin() as connection:
        cross_org_database = connection.execute(
            text(
                """
                SELECT *
                FROM public.context_control_accept_file_change_page(
                    :organization_id, :source_id, :source_version_id,
                    :scan_ref, :scan_epoch, :page_limit, :page_ref,
                    :predecessor_page_ref, :predecessor_checkpoint_ref,
                    :predecessor_sequence, :superseded_scan_epoch,
                    CAST(:changes AS jsonb), :complete
                )
                """
            ),
            {
                "organization_id": organization_b,
                "source_id": page.value.source_ref,
                "source_version_id": page.value.source_version_ref,
                "scan_ref": page.value.scan_ref,
                "scan_epoch": page.value.scan_epoch,
                "page_limit": page.value.page_limit,
                "page_ref": page_verified.page_ref,
                "predecessor_page_ref": page.value.predecessor_page_ref,
                "predecessor_checkpoint_ref": (page.value.predecessor_checkpoint_ref),
                "predecessor_sequence": page.value.predecessor_sequence,
                "superseded_scan_epoch": page.value.superseded_scan_epoch,
                "changes": json.dumps(page_changes_document, separators=(",", ":")),
                "complete": page.value.complete,
            },
        ).one_or_none()
    assert cross_org_database is None

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            isolated_counts = connection.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*) FROM file_source_change_page
                       WHERE organization_id = :org_a AND source_id = :source_a),
                      (SELECT count(*) FROM file_source_change_page
                       WHERE organization_id = :org_b AND source_id = :source_b),
                      (SELECT count(*) FROM file_source_change
                       WHERE organization_id = :org_b AND source_id = :source_b)
                    """
                ),
                {
                    "org_a": organization_a,
                    "source_a": source.source_version.source_ref.value,
                    "org_b": organization_b,
                    "source_b": source_b.source_version.source_ref.value,
                },
            ).one()
    finally:
        migration_engine.dispose()
    assert tuple(isolated_counts) == (1, 1, 1)

    replacement_version_id = uuid4()
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO source_version (
                        organization_id, source_id, version_id, source_kind,
                        root_ref, capability_manifest, created_at
                    )
                    SELECT organization_id, source_id, :replacement_version_id,
                           source_kind, root_ref, capability_manifest,
                           :created_at
                    FROM source_version
                    WHERE organization_id = :organization_id
                      AND source_id = :source_id
                      AND version_id = :old_version_id
                    """
                ),
                {
                    "organization_id": organization_a,
                    "source_id": source.source_version.source_ref.value,
                    "old_version_id": source.source_version.version_ref,
                    "replacement_version_id": replacement_version_id,
                    "created_at": NOW + timedelta(seconds=1),
                },
            )
            connection.execute(
                text(
                    """
                    UPDATE context_source
                    SET active_version_id = :replacement_version_id
                    WHERE organization_id = :organization_id
                      AND source_id = :source_id
                      AND active_version_id = :old_version_id
                    """
                ),
                {
                    "organization_id": organization_a,
                    "source_id": source.source_version.source_ref.value,
                    "old_version_id": source.source_version.version_ref,
                    "replacement_version_id": replacement_version_id,
                },
            )
    finally:
        migration_engine.dispose()
    with (
        _authorize(
            authority_a,
            organization_a,
            ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
            "after-version-change",
        ) as call,
        pytest.raises(SourceNotAvailable),
    ):
        control_a.accept_file_change_page(call, page.value)

    current_source = FileChangeSource(
        organization_a,
        replace(
            source.source_version,
            version_ref=replacement_version_id,
            created_at=NOW + timedelta(seconds=1),
        ),
        scan_head=accepted_old.scan_head,
    )
    current_page = provider.read_changes(
        current_source,
        InitialScan(),
        ChangeLimit(1),
    )
    assert type(current_page) is ProviderOk
    assert current_page.value.superseded_scan_epoch == accepted_old.scan_epoch
    with _authorize(
        authority_a,
        organization_a,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-current-version-page",
    ) as call:
        accepted_current = control_a.accept_file_change_page(call, current_page.value)
    assert accepted_current.source_version_ref == replacement_version_id
    with _authorize(
        authority_a,
        organization_a,
        ControlOperation.OFFBOARD_FILE_SOURCE,
        "disable-source",
    ) as call:
        control_a.offboard_file_source(
            call, OffboardFileSource(source.source_version.source_ref)
        )
    with (
        _authorize(
            authority_a,
            organization_a,
            ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
            "after-disable",
        ) as call,
        pytest.raises(SourceNotAvailable),
    ):
        control_a.accept_file_change_page(call, current_page.value)

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            effects = connection.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*) FROM file_source_change_page
                       WHERE organization_id = :org AND source_id = :source),
                      (SELECT count(*) FROM file_source_change
                       WHERE organization_id = :org AND source_id = :source),
                      (SELECT count(*) FROM file_source_acquisition_checkpoint
                       WHERE organization_id = :org AND source_id = :source),
                      (SELECT count(*) FROM file_import_job
                       WHERE organization_id = :org AND source_id = :source),
                      (SELECT count(*) FROM context_revision
                       WHERE organization_id = :org),
                      (SELECT count(*) FROM exact_phrase_candidate
                       WHERE organization_id = :org),
                      (SELECT count(*) FROM file_source_publish_watermark
                       WHERE organization_id = :org AND source_id = :source)
                    """
                ),
                {
                    "org": organization_a,
                    "source": source.source_version.source_ref.value,
                },
            ).one()
    finally:
        migration_engine.dispose()
    assert tuple(effects) == (2, 2, 3, 1, 0, 0, 0)

    for table in ("file_source_change_page", "file_source_change"):
        with pytest.raises(DBAPIError), guarded_runtime_engine.connect() as connection:
            connection.execute(text(f"SELECT count(*) FROM {table}"))  # noqa: S608


def test_runtime_role_cannot_consult_file_change_progress_as_authority(
    guarded_runtime_engine: Engine,
) -> None:
    for table in (
        "file_source_change_page",
        "file_source_change",
        "file_source_acquisition_checkpoint",
    ):
        with pytest.raises(DBAPIError), guarded_runtime_engine.connect() as connection:
            connection.execute(text(f"SELECT count(*) FROM {table}"))  # noqa: S608


def test_file_change_page_accepts_the_minimal_public_markdown_filename(
    tmp_path: Path,
    guarded_control_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / ".md").write_bytes(b"minimal")
    provider_proofs, control_proofs = _proofs()
    organization_id = uuid4()
    receiver = FileImportReceiver(uuid4())
    control, authority, source = _seed_file_change_source(
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
        organization_id=organization_id,
        receiver=receiver,
        root_ref=FileRootRef("minimal-markdown-root"),
        control_proofs=control_proofs,
    )
    provider = FileChangeProvider(
        FileRootRegistry(
            {source.source_version.root_ref: root},
            limits=FileReadLimits(max_file_bytes=1_024),
        ),
        proofs=provider_proofs,
    )

    page = provider.read_changes(source, InitialScan(), ChangeLimit(1))
    assert type(page) is ProviderOk
    assert [change.path.value for change in page.value.changes] == [".md"]
    with _authorize(
        authority,
        organization_id,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        "accept-minimal-markdown-name",
    ) as call:
        accepted = control.accept_file_change_page(call, page.value)

    assert accepted.complete is True

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
        "schedule-minimal-markdown-name",
    ) as call:
        scheduled = control.schedule_file_change_page(
            call,
            ScheduleFileChangePage(
                source_ref=accepted.source_ref,
                source_version_ref=accepted.source_version_ref,
                page_ref=accepted.page_ref,
                audience=FileImportAudience(
                    principal_ref="principal:file-reader",
                    membership_id=membership_id,
                    membership_version=1,
                ),
            ),
        )
    assert [change.path.value for change in scheduled.changes] == [".md"]
    assert scheduled.changes[0].prepared_import.service_principal_id == (
        receiver.service_principal_id
    )

    with pytest.raises(
        RuntimeError,
        match="requires no retained",
    ):
        command.downgrade(Config(ROOT / "alembic.ini"), "20260724_0027")
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == HEAD_REVISION
            )
    finally:
        migration_engine.dispose()


def test_file_change_page_database_rejects_paths_outside_public_contract(
    tmp_path: Path,
    guarded_control_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "valid.md").write_bytes(b"valid")
    provider_proofs, control_proofs = _proofs()
    organization_id = uuid4()
    _control, _authority, source = _seed_file_change_source(
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
        organization_id=organization_id,
        receiver=FileImportReceiver(uuid4()),
        root_ref=FileRootRef("invalid-markdown-path-root"),
        control_proofs=control_proofs,
    )
    provider = FileChangeProvider(
        FileRootRegistry(
            {source.source_version.root_ref: root},
            limits=FileReadLimits(max_file_bytes=1_024),
        ),
        proofs=provider_proofs,
    )
    page = provider.read_changes(source, InitialScan(), ChangeLimit(1))
    assert type(page) is ProviderOk
    verified = control_proofs.verify_page(page.value)
    assert verified is not None
    valid_change = page.value.changes[0]
    invalid_paths = (" valid.md", "valid\x01.md", "a" * 253 + ".md")

    for index, invalid_path in enumerate(invalid_paths):
        with guarded_control_engine.begin() as connection:
            denied = connection.execute(
                text(
                    """
                    SELECT *
                    FROM public.context_control_accept_file_change_page(
                        :organization_id, :source_id, :source_version_id,
                        :scan_ref, :scan_epoch, :page_limit, :page_ref,
                        NULL, NULL, NULL, NULL,
                        CAST(:changes AS jsonb), :complete
                    )
                    """
                ),
                {
                    "organization_id": organization_id,
                    "source_id": page.value.source_ref,
                    "source_version_id": page.value.source_version_ref,
                    "scan_ref": page.value.scan_ref,
                    "scan_epoch": page.value.scan_epoch,
                    "page_limit": page.value.page_limit,
                    "page_ref": f"{index + 1:064x}",
                    "changes": json.dumps(
                        [
                            {
                                "contentLength": valid_change.content_length,
                                "contentSha256": valid_change.content_sha256,
                                "kind": valid_change.kind.value,
                                "path": invalid_path,
                            }
                        ],
                        separators=(",", ":"),
                    ),
                    "complete": page.value.complete,
                },
            ).one_or_none()
        assert denied is None

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            counts = connection.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*) FROM file_source_change_page
                       WHERE organization_id = :org AND source_id = :source),
                      (SELECT count(*) FROM file_source_change
                       WHERE organization_id = :org AND source_id = :source),
                      (SELECT count(*) FROM file_source_acquisition_checkpoint
                       WHERE organization_id = :org AND source_id = :source)
                    """
                ),
                {
                    "org": organization_id,
                    "source": source.source_version.source_ref.value,
                },
            ).one()
    finally:
        migration_engine.dispose()
    assert tuple(counts) == (0, 0, 1)
