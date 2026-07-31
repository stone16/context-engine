"""Shared integration support for one authorized File import scenario.

The helpers in this module deliberately exercise the public Control and worker
seams.  Only the initial Organization, User, Membership, and ServiceActor seed
uses the migration owner because those rows are prerequisites for the
non-owner production paths under test.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import Engine, text

from adapters.embeddings import DeterministicEmbeddingTwin
from adapters.file_source import FileReadLimits, FileRootRegistry
from engine.control import (
    ContextControl,
    ControlOperation,
    ControlOperatorAuthority,
    FileImportAudience,
    FileImportPath,
    FileImportReceiver,
    FileRootRef,
    PreparedFileImport,
    PrepareFileImport,
    RegisterFileSource,
    SourceRef,
    VerifiedControlOperatorIdentity,
)
from engine.persistence import (
    DatabaseConfiguration,
    FileImportLeaseRedemption,
    PostgreSQLControlStore,
    PostgreSQLFileImportWorker,
    PostgreSQLWorkerLeaseIssuer,
    PublishedFileImport,
    create_database_engine,
)
from engine.supply import (
    MarkdownCompilerConfig,
    WorkerLeaseClaims,
    WorkerLeaseCodec,
    WorkerLeaseKeyring,
    WorkerLeaseToken,
)

NOW = datetime.now(UTC).replace(microsecond=0)
SIGNING_KEY = bytes(range(32))


class ControlAuthenticator:
    """Exact test-only Control authenticator for File integration scenarios."""

    def __init__(self, organization_id: UUID) -> None:
        self.organization_id = organization_id

    def authenticate(self, opaque_credential: str) -> VerifiedControlOperatorIdentity:
        if opaque_credential != "control-secret":
            raise AssertionError("unexpected Control credential")
        return VerifiedControlOperatorIdentity(
            organization_id=self.organization_id,
            operator_ref="operator:file-import",
            authentication_binding_ref="binding:file-import",
            authority_ref="authority:file-import",
            allowed_operations=frozenset(
                {
                    ControlOperation.REGISTER_SOURCE,
                    ControlOperation.PREVIEW_BULK_ARTICLE_POLICY_CHANGE,
                    ControlOperation.COMMIT_BULK_ARTICLE_POLICY_CHANGE,
                    ControlOperation.READ_SOURCE,
                    ControlOperation.READ_SOURCE_PROGRESS,
                    ControlOperation.ACTIVATE_FILE_CHANGE_FEED,
                    ControlOperation.ACTIVATE_FILE_DELETE_OBSERVATIONS,
                    ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
                    ControlOperation.IMPORT_FILE,
                    ControlOperation.SET_SOURCE_ARTICLE_POLICY_DEFAULT,
                    ControlOperation.SET_TENANT_ARTICLE_POLICY_DEFAULT,
                    ControlOperation.EXECUTE_FILE_DELETE_OBSERVATION,
                    ControlOperation.OFFBOARD_FILE_SOURCE,
                    ControlOperation.SCHEDULE_FILE_CHANGE_PAGE,
                    ControlOperation.TOMBSTONE_FILE_RESOURCE,
                }
            ),
            valid_from=NOW - timedelta(minutes=1),
            expires_at=NOW + timedelta(hours=1),
        )


@dataclass(frozen=True, slots=True)
class FileImportScenario:
    """Organization-bound inputs and capabilities for one File import."""

    organization_id: UUID
    membership_id: UUID
    receiver: FileImportReceiver
    source_ref: SourceRef
    prepared: PreparedFileImport
    codec: WorkerLeaseCodec
    token: WorkerLeaseToken | None
    root_ref: FileRootRef
    root: Path


def prepare_file_import_scenario(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    *,
    payload: bytes | None = b"# Handbook\n\nContextEngine delivers context.\n",
    issue_lease: bool = True,
    lease_ttl_seconds: int = 300,
) -> FileImportScenario:
    """Seed one Organization and prepare its File import through Control."""

    organization_id = uuid4()
    user_id = uuid4()
    membership_id = uuid4()
    receiver = FileImportReceiver(uuid4())
    root_ref = FileRootRef(f"root-{organization_id.hex}")
    root = tmp_path / root_ref.value
    root.mkdir()
    if payload is not None:
        (root / "handbook.md").write_bytes(payload)

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text("INSERT INTO organization (organization_id) VALUES (:org)"),
                {"org": organization_id},
            )
            connection.execute(
                text("INSERT INTO user_account (user_id) VALUES (:user_id)"),
                {"user_id": user_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO membership (
                        organization_id, membership_id, user_id, status,
                        membership_version, valid_from
                    ) VALUES (:org, :membership_id, :user_id, 'active', 1, :now)
                    """
                ),
                {
                    "org": organization_id,
                    "membership_id": membership_id,
                    "user_id": user_id,
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
                {
                    "org": organization_id,
                    "receiver": receiver.service_principal_id,
                },
            )
    finally:
        migration_engine.dispose()

    authority = ControlOperatorAuthority(
        ControlAuthenticator(organization_id),
        call_ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    )
    control = ContextControl(
        store=PostgreSQLControlStore(
            guarded_control_engine,
            clock=lambda: NOW,
            file_import_receiver=receiver,
        ),
        authority=authority,
        clock=lambda: NOW,
    )
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.REGISTER_SOURCE,
        request_id="register-file-security-scenario",
    ) as call:
        source = control.register_source(
            call,
            RegisterFileSource("Handbook", root_ref, organization_id.hex),
        )
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.IMPORT_FILE,
        request_id="prepare-file-security-scenario",
    ) as call:
        prepared = control.prepare_file_import(
            call,
            PrepareFileImport(
                source_ref=source.source_ref,
                path=FileImportPath("handbook.md"),
                audience=FileImportAudience(
                    principal_ref="principal:file-reader",
                    membership_id=membership_id,
                    membership_version=1,
                ),
                idempotency_key="file-security-scenario",
            ),
        )
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.IMPORT_FILE,
        request_id="retry-import-after-lost-response",
    ) as call:
        prepared_retry = control.prepare_file_import(
            call,
            PrepareFileImport(
                source_ref=source.source_ref,
                path=FileImportPath("handbook.md"),
                audience=FileImportAudience(
                    principal_ref="principal:file-reader",
                    membership_id=membership_id,
                    membership_version=1,
                ),
                idempotency_key="file-security-scenario",
            ),
        )
    assert prepared_retry == prepared
    codec = WorkerLeaseCodec(
        WorkerLeaseKeyring(active_version=1, keys={1: SIGNING_KEY})
    )
    token = (
        PostgreSQLWorkerLeaseIssuer(
            guarded_control_engine,
            codec,
            lease_ttl_seconds=lease_ttl_seconds,
        ).issue_file_import_lease(prepared)
        if issue_lease
        else None
    )
    return FileImportScenario(
        organization_id=organization_id,
        membership_id=membership_id,
        receiver=receiver,
        source_ref=source.source_ref,
        prepared=prepared,
        codec=codec,
        token=token,
        root_ref=root_ref,
        root=root,
    )


def scenario_claims(scenario: FileImportScenario) -> WorkerLeaseClaims:
    """Verify and return the exact claims for a scenario's issued lease."""

    assert scenario.token is not None
    return scenario.codec.verify(
        scenario.token,
        expected_organization_id=scenario.organization_id,
        expected_job_id=scenario.prepared.job_id,
        expected_service_principal_id=scenario.receiver.service_principal_id,
        expected_workload=scenario.receiver.workload,
        expected_operation=scenario.receiver.operation,
        expected_worker_audience=scenario.receiver.worker_audience,
        expected_source_ref=str(scenario.source_ref.value),
        now=datetime.now(UTC).replace(microsecond=0),
    )


def prepare_repeat_file_import(
    scenario: FileImportScenario,
    guarded_control_engine: Engine,
    *,
    idempotency_key: str,
    path: FileImportPath | None = None,
    lease_ttl_seconds: int = 300,
) -> tuple[PreparedFileImport, WorkerLeaseToken]:
    """Prepare and lease another import for the scenario's exact source."""

    authority = ControlOperatorAuthority(
        ControlAuthenticator(scenario.organization_id),
        call_ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    )
    control = ContextControl(
        store=PostgreSQLControlStore(
            guarded_control_engine,
            clock=lambda: NOW,
            file_import_receiver=scenario.receiver,
        ),
        authority=authority,
        clock=lambda: NOW,
    )
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.IMPORT_FILE,
        request_id=f"repeat-{idempotency_key}",
    ) as call:
        prepared = control.prepare_file_import(
            call,
            PrepareFileImport(
                source_ref=scenario.source_ref,
                path=path or FileImportPath("handbook.md"),
                audience=FileImportAudience(
                    principal_ref="principal:file-reader",
                    membership_id=scenario.membership_id,
                    membership_version=1,
                ),
                idempotency_key=idempotency_key,
            ),
        )
    token = PostgreSQLWorkerLeaseIssuer(
        guarded_control_engine,
        scenario.codec,
        lease_ttl_seconds=lease_ttl_seconds,
    ).issue_file_import_lease(prepared)
    return prepared, token


def run_file_import(
    scenario: FileImportScenario,
    prepared: PreparedFileImport,
    token: WorkerLeaseToken,
    guarded_worker_engine: Engine,
    *,
    config_version: str = "markdown-config-v1",
) -> PublishedFileImport:
    """Run one scenario import through the real non-owner worker seam."""

    return PostgreSQLFileImportWorker(
        guarded_worker_engine,
        scenario.codec,
        scenario.receiver,
        FileRootRegistry(
            {scenario.root_ref: scenario.root},
            limits=FileReadLimits(max_file_bytes=4096),
        ),
        MarkdownCompilerConfig(config_version),
        embedding_provider=DeterministicEmbeddingTwin(),
        clock=lambda: datetime.now(UTC).replace(microsecond=0),
    ).run(
        FileImportLeaseRedemption(
            token,
            prepared.organization_id,
            prepared.job_id,
            prepared.source_ref,
        )
    )


def delete_file_import_scenario(
    configuration: DatabaseConfiguration,
    organization_id: UUID,
) -> None:
    """Remove one disposable File scenario without touching sibling tenants."""

    engine = create_database_engine(configuration)
    immutable_tables = (
        ("revision_link_edge", "revision_link_edge_immutable"),
        ("file_source_publish_watermark", "file_source_publish_watermark_immutable"),
        (
            "file_source_acquisition_checkpoint",
            "file_source_acquisition_checkpoint_immutable",
        ),
        ("file_import_job_event", "file_import_job_event_immutable"),
        ("file_revision_supersession", "file_revision_supersession_immutable"),
        ("file_revision_replacement_plan", "file_revision_replacement_plan_immutable"),
        ("exact_phrase_candidate", "exact_phrase_candidate_immutable"),
        ("revision_publication_event", "revision_publication_event_immutable"),
        ("context_fragment", "context_fragment_reject_mutation"),
        ("file_revision_snapshot", "file_revision_snapshot_immutable"),
        ("context_revision", "context_revision_reject_mutation"),
        ("file_acquisition_result", "file_acquisition_result_immutable"),
        ("file_resource_ingestion_guard", "file_resource_ingestion_guard_immutable"),
        ("file_acquisition", "file_acquisition_immutable"),
        ("source_version", "source_version_immutable"),
    )
    try:
        with engine.begin() as connection:
            existing_tables = frozenset(
                connection.execute(
                    text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'public'"
                    )
                ).scalars()
            )
            active_immutable_tables = tuple(
                (table, trigger)
                for table, trigger in immutable_tables
                if table in existing_tables
            )
            for table, trigger in active_immutable_tables:
                connection.execute(
                    text(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}")
                )
        try:
            with engine.begin() as connection:
                user_ids = tuple(
                    connection.execute(
                        text(
                            "SELECT user_id FROM membership "
                            "WHERE organization_id = :organization_id"
                        ),
                        {"organization_id": organization_id},
                    ).scalars()
                )
                for table in (
                    "decision_audit",
                    "context_run",
                    "article_source_acl_observation",
                    "article_access_policy",
                    "article_explicit_policy_setting",
                    "article_access_group_membership",
                    "article_access_group",
                    "source_article_policy_default",
                    "organization_article_policy_default",
                    "file_source_publish_watermark",
                    "file_source_acquisition_checkpoint",
                    "file_import_job_event",
                    "file_publication_recovery",
                    "file_revision_supersession",
                    "file_revision_replacement_plan",
                    "file_acquisition_result",
                    "revision_link_edge",
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
                    "file_acquisition",
                    "context_source",
                    "source_version",
                    "service_principal",
                    "membership",
                ):
                    if table not in existing_tables:
                        continue
                    connection.execute(
                        text(
                            f"DELETE FROM {table} "  # noqa: S608
                            "WHERE organization_id = :organization_id"
                        ),
                        {"organization_id": organization_id},
                    )
                for user_id in user_ids:
                    connection.execute(
                        text(
                            "DELETE FROM user_account "
                            "WHERE user_id = :user_id AND NOT EXISTS ("
                            "SELECT 1 FROM membership "
                            "WHERE membership.user_id = user_account.user_id)"
                        ),
                        {"user_id": user_id},
                    )
                connection.execute(
                    text(
                        "DELETE FROM organization "
                        "WHERE organization_id = :organization_id"
                    ),
                    {"organization_id": organization_id},
                )
        finally:
            with engine.begin() as connection:
                for table, trigger in reversed(active_immutable_tables):
                    connection.execute(
                        text(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}")
                    )
    finally:
        engine.dispose()


def redeem_file_import_direct(
    guarded_worker_engine: Engine,
    claims: WorkerLeaseClaims,
    *,
    organization_id: UUID | None = None,
    job_id: UUID | None = None,
    service_principal_id: UUID | None = None,
    source_ref: str | None = None,
) -> object | None:
    """Exercise the exact PostgreSQL redemption function for negative tests."""

    with guarded_worker_engine.begin() as connection:
        return connection.execute(
            text(
                """
                SELECT * FROM public.context_worker_redeem_file_import(
                    :organization_id, :job_id, :service_principal_id,
                    :source_ref, :lease_generation,
                    :signing_key_version, :nonce,
                    :issued_at, :expires_at
                )
                """
            ),
            {
                "organization_id": organization_id or claims.organization_id,
                "job_id": job_id or claims.job_id,
                "service_principal_id": (
                    service_principal_id or claims.service_principal_id
                ),
                "source_ref": source_ref or claims.source_ref,
                "lease_generation": claims.lease_generation,
                "signing_key_version": claims.signing_key_version,
                "nonce": claims.nonce,
                "issued_at": claims.issued_at,
                "expires_at": claims.expires_at,
            },
        ).one_or_none()
