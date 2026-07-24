from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from adapters.file_source import FileChangeProvider, FileReadLimits, FileRootRegistry
from engine.control import (
    ActivateFileChangeFeed,
    ChangeLimit,
    ContextControl,
    ControlOperation,
    ControlOperatorAuthority,
    FileChangeControlProofs,
    FileChangeProviderProofs,
    FileChangeSource,
    FileImportAudience,
    FileImportPath,
    FileImportReceiver,
    FileRootRef,
    FileSourceChangeKind,
    InitialScan,
    OffboardFileSource,
    PrepareFileImport,
    ProviderGenericDenied,
    ProviderInvalidCheckpoint,
    ProviderOk,
    RegisterFileSource,
    SourceNotAvailable,
    TrustedControlCall,
    VerifiedControlOperatorIdentity,
)
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLControlStore,
    create_database_engine,
)
from tests.support.migrations import HEAD_REVISION

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
                connection.execute(
                    text(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}")
                )
        try:
            with engine.begin() as connection:
                for organization_id, user_id in scenarios:
                    for table in (
                        "file_source_acquisition_checkpoint",
                        "file_source_publish_watermark",
                        "file_source_change",
                        "file_source_change_page",
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
                        "context_revision",
                        "context_resource",
                        "file_resource_ingestion_guard",
                        "file_import_job",
                        "file_resource_cleanup_intent",
                        "file_source_cleanup_intent",
                        "file_acquisition",
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
                    ControlOperation.IMPORT_FILE,
                    ControlOperation.OFFBOARD_FILE_SOURCE,
                    ControlOperation.REGISTER_SOURCE,
                    ControlOperation.READ_SOURCE_PROGRESS,
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
    source = replace(
        source, scan_head=accepted_changed_second_before_aba.scan_head
    )
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
    source = replace(
        source, scan_head=accepted_changed_first_after_aba.scan_head
    )
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
        accepted_current = control_a.accept_file_change_page(
            call, current_page.value
        )
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
    control, authority, source = _seed_file_change_source(
        guarded_control_engine=guarded_control_engine,
        migration_configuration=migration_configuration,
        organization_id=organization_id,
        receiver=FileImportReceiver(uuid4()),
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

    with pytest.raises(
        RuntimeError,
        match="requires no retained accepted page stream",
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
