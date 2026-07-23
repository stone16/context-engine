from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from engine.control import (
    ContextControl,
    ControlOperation,
    ControlOperatorAuthority,
    FileSourceChangeKind,
    FileSourceProgress,
    FileSourcePublishOutcome,
    SourceNotAvailable,
)
from engine.persistence import (
    DatabaseConfiguration,
    FileImportInterrupted,
    FilePublicationBoundary,
    PostgreSQLControlStore,
    PostgreSQLWorkerLeaseIssuer,
    create_database_engine,
)
from engine.runtime import QueryDigestKeyring
from engine.supply import FileImportPath
from tests.integration.test_file_import_tracer import (
    NOW,
    _ControlAuthenticator,
    _FileImportScenario,
    _prepare_file_import_scenario,
    _prepare_repeat_file_import,
    _run_file_import,
)
from tests.integration.test_zz_file_publication_recovery import (
    _run,
    _wait_for_expiry,
    _worker,
)
from tests.integration.test_zz_file_resource_tombstone import _tombstone
from tests.integration.test_zz_file_revision_replacement import (
    NEW_MARKDOWN,
    OLD_MARKDOWN,
    _resolve,
    _scenario_user_id,
)

pytestmark = pytest.mark.integration


def _read_progress(
    scenario: _FileImportScenario,
    guarded_control_engine: Engine,
    *,
    request_id: str,
) -> FileSourceProgress:
    authority = ControlOperatorAuthority(
        _ControlAuthenticator(scenario.organization_id),
        call_ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    )
    control = ContextControl(
        store=PostgreSQLControlStore(guarded_control_engine, clock=lambda: NOW),
        authority=authority,
        clock=lambda: NOW,
    )
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.READ_SOURCE_PROGRESS,
        request_id=request_id,
    ) as call:
        return control.read_file_source_progress(call, scenario.source_ref)


def _visibility_state(
    configuration: DatabaseConfiguration,
    scenario: _FileImportScenario,
    *,
    resource_ref: str,
    job_id: UUID,
) -> tuple[UUID, bool, str]:
    database_engine = create_database_engine(configuration)
    try:
        with database_engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT resource.active_revision_id, resource.tombstoned,
                           job.state
                    FROM context_resource AS resource
                    JOIN file_import_job AS job
                      ON job.organization_id = resource.organization_id
                    WHERE resource.organization_id = :organization_id
                      AND resource.resource_ref = :resource_ref
                      AND job.job_id = :job_id
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "resource_ref": resource_ref,
                    "job_id": job_id,
                },
            ).one()
        return cast(tuple[UUID, bool, str], tuple(row))
    finally:
        database_engine.dispose()


@pytest.mark.parametrize(
    ("boundary", "interrupted_state"),
    [
        (FilePublicationBoundary.ACQUIRED, "running"),
        (FilePublicationBoundary.PREPARED, "prepared"),
        (FilePublicationBoundary.INDEXED, "ready"),
    ],
)
def test_each_publication_barrier_keeps_watermark_and_runtime_on_old_visibility(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
    boundary: FilePublicationBoundary,
    interrupted_state: str,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=OLD_MARKDOWN,
        lease_ttl_seconds=2,
    )
    assert scenario.token is not None
    initial = _run_file_import(
        scenario,
        scenario.prepared,
        scenario.token,
        guarded_worker_engine,
        config_version="markdown-config-v2",
    )
    initial_revision = UUID(initial.candidate_ref.revision_ref)
    user_id = _scenario_user_id(scenario, migration_configuration)

    (scenario.root / "handbook.md").write_bytes(NEW_MARKDOWN)
    changed, token = _prepare_repeat_file_import(
        scenario,
        guarded_control_engine,
        idempotency_key=f"progress-barrier-{boundary.value}",
        lease_ttl_seconds=2,
    )
    interrupted_scenario = replace(scenario, prepared=changed, token=token)
    with pytest.raises(FileImportInterrupted):
        _run(
            _worker(
                interrupted_scenario,
                guarded_worker_engine,
                config_version="markdown-config-v2",
                interrupt_after=boundary,
            ),
            interrupted_scenario,
            token,
        )

    paused = _read_progress(
        scenario,
        guarded_control_engine,
        request_id=f"progress-barrier-paused-{boundary.value}",
    )
    assert paused.acquisition_checkpoint is not None
    assert paused.publish_watermark is not None
    assert paused.acquisition_checkpoint.sequence == 2
    assert paused.acquisition_checkpoint.job_ref == changed.job_id
    assert paused.publish_watermark.sequence == 1
    assert _visibility_state(
        migration_configuration,
        scenario,
        resource_ref=initial.candidate_ref.resource_ref,
        job_id=changed.job_id,
    ) == (initial_revision, False, interrupted_state)
    old_package = _resolve(
        scenario,
        guarded_runtime_engine,
        query_digest_keyring,
        user_id=user_id,
        query="OLD marker.",
        request_id=f"progress-barrier-old-{boundary.value}",
    )
    hidden_new = _resolve(
        scenario,
        guarded_runtime_engine,
        query_digest_keyring,
        user_id=user_id,
        query="NEW marker.",
        request_id=f"progress-barrier-new-hidden-{boundary.value}",
    )
    assert [block["text"] for block in old_package["blocks"]] == [
        "# Handbook\n\nOLD marker."
    ]
    assert hidden_new["blocks"] == []
    assert hidden_new["evidence"] == []

    _wait_for_expiry(migration_configuration)
    recovered_token = PostgreSQLWorkerLeaseIssuer(
        guarded_control_engine, scenario.codec
    ).issue_file_import_lease(changed)
    recovered = _run_file_import(
        scenario,
        changed,
        recovered_token,
        guarded_worker_engine,
        config_version="markdown-config-v2",
    )
    recovered_revision = UUID(recovered.candidate_ref.revision_ref)
    caught_up = _read_progress(
        scenario,
        guarded_control_engine,
        request_id=f"progress-barrier-recovered-{boundary.value}",
    )
    assert caught_up.acquisition_checkpoint is not None
    assert caught_up.publish_watermark is not None
    assert caught_up.acquisition_checkpoint.sequence == 2
    assert caught_up.publish_watermark.sequence == 2
    assert _visibility_state(
        migration_configuration,
        scenario,
        resource_ref=initial.candidate_ref.resource_ref,
        job_id=changed.job_id,
    ) == (recovered_revision, False, "completed")
    visible_new = _resolve(
        scenario,
        guarded_runtime_engine,
        query_digest_keyring,
        user_id=user_id,
        query="NEW marker.",
        request_id=f"progress-barrier-new-visible-{boundary.value}",
    )
    assert [block["text"] for block in visible_new["blocks"]] == [
        "# Handbook\n\nNEW marker."
    ]

    _tombstone(
        scenario,
        guarded_control_engine,
        resource_ref=initial.candidate_ref.resource_ref,
        event_ref=f"progress-barrier-delete-{boundary.value}",
        event_sequence=3,
    )
    tombstoned = _read_progress(
        scenario,
        guarded_control_engine,
        request_id=f"progress-barrier-tombstoned-{boundary.value}",
    )
    assert tombstoned.acquisition_checkpoint is not None
    assert tombstoned.publish_watermark is not None
    assert tombstoned.acquisition_checkpoint.sequence == 3
    assert tombstoned.publish_watermark.sequence == 3
    assert _visibility_state(
        migration_configuration,
        scenario,
        resource_ref=initial.candidate_ref.resource_ref,
        job_id=changed.job_id,
    ) == (recovered_revision, True, "completed")
    hidden_deleted = _resolve(
        scenario,
        guarded_runtime_engine,
        query_digest_keyring,
        user_id=user_id,
        query="NEW marker.",
        request_id=f"progress-barrier-deleted-hidden-{boundary.value}",
    )
    assert hidden_deleted["blocks"] == []
    assert hidden_deleted["evidence"] == []


def test_checkpoint_can_lead_watermark_and_recovery_closes_only_contiguous_gap(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        lease_ttl_seconds=2,
    )
    assert scenario.token is not None

    accepted = _read_progress(
        scenario, guarded_control_engine, request_id="progress-initial-accepted"
    )
    assert accepted.acquisition_checkpoint is not None
    assert accepted.acquisition_checkpoint.sequence == 1
    assert accepted.publish_watermark is None

    initial = _run_file_import(
        scenario,
        scenario.prepared,
        scenario.token,
        guarded_worker_engine,
    )
    first_visible = _read_progress(
        scenario, guarded_control_engine, request_id="progress-initial-visible"
    )
    assert first_visible.acquisition_checkpoint is not None
    assert first_visible.publish_watermark is not None
    assert first_visible.acquisition_checkpoint.sequence == 1
    assert first_visible.publish_watermark.sequence == 1
    assert first_visible.publish_watermark.outcome is FileSourcePublishOutcome.PUBLISHED

    (scenario.root / "handbook.md").write_bytes(NEW_MARKDOWN)
    changed, changed_token = _prepare_repeat_file_import(
        scenario,
        guarded_control_engine,
        idempotency_key="progress-changed-paused",
        lease_ttl_seconds=2,
    )
    changed_scenario = replace(scenario, prepared=changed, token=changed_token)
    with pytest.raises(FileImportInterrupted):
        _run(
            _worker(
                changed_scenario,
                guarded_worker_engine,
                config_version="markdown-config-v2",
                interrupt_after=FilePublicationBoundary.INDEXED,
            ),
            changed_scenario,
            changed_token,
        )

    paused = _read_progress(
        scenario, guarded_control_engine, request_id="progress-changed-paused-read"
    )
    assert paused.acquisition_checkpoint is not None
    assert paused.publish_watermark is not None
    assert paused.acquisition_checkpoint.sequence == 2
    assert paused.acquisition_checkpoint.job_ref == changed.job_id
    assert paused.publish_watermark.sequence == 1

    (scenario.root / "appendix.md").write_bytes(b"# Appendix\n\nLater change.\n")
    later, later_token = _prepare_repeat_file_import(
        scenario,
        guarded_control_engine,
        idempotency_key="progress-later-first",
        path=FileImportPath("appendix.md"),
    )
    later_published = _run_file_import(
        scenario, later, later_token, guarded_worker_engine
    )
    out_of_order = _read_progress(
        scenario, guarded_control_engine, request_id="progress-out-of-order"
    )
    assert out_of_order.acquisition_checkpoint is not None
    assert out_of_order.publish_watermark is not None
    assert out_of_order.acquisition_checkpoint.sequence == 3
    assert out_of_order.acquisition_checkpoint.job_ref == later.job_id
    assert out_of_order.publish_watermark.sequence == 1

    _wait_for_expiry(migration_configuration)
    recovered_token = PostgreSQLWorkerLeaseIssuer(
        guarded_control_engine, scenario.codec
    ).issue_file_import_lease(changed)
    recovered = _run_file_import(
        scenario,
        changed,
        recovered_token,
        guarded_worker_engine,
        config_version="markdown-config-v2",
    )
    assert recovered.outcome == "replaced"
    caught_up = _read_progress(
        scenario, guarded_control_engine, request_id="progress-recovered"
    )
    assert caught_up.acquisition_checkpoint is not None
    assert caught_up.publish_watermark is not None
    assert caught_up.acquisition_checkpoint.sequence == 3
    assert caught_up.publish_watermark.sequence == 3
    assert caught_up.publish_watermark.resource_ref == (
        later_published.candidate_ref.resource_ref
    )

    deleted = _tombstone(
        scenario,
        guarded_control_engine,
        resource_ref=initial.candidate_ref.resource_ref,
        event_ref="progress-delete-4",
        event_sequence=4,
    )
    tombstoned = _read_progress(
        scenario, guarded_control_engine, request_id="progress-tombstoned"
    )
    assert tombstoned.acquisition_checkpoint is not None
    assert tombstoned.publish_watermark is not None
    assert tombstoned.acquisition_checkpoint.sequence == 4
    assert tombstoned.publish_watermark.sequence == 4
    assert (
        tombstoned.acquisition_checkpoint.change_kind
        is FileSourceChangeKind.FILE_TOMBSTONE
    )
    assert tombstoned.publish_watermark.outcome is FileSourcePublishOutcome.TOMBSTONED
    assert tombstoned.publish_watermark.cleanup_intent_ref == (
        deleted.cleanup_intent_ref
    )

    replay = _tombstone(
        scenario,
        guarded_control_engine,
        resource_ref=initial.candidate_ref.resource_ref,
        event_ref="progress-delete-4",
        event_sequence=4,
    )
    assert replay == deleted
    replayed = _read_progress(
        scenario, guarded_control_engine, request_id="progress-replayed-delete"
    )
    assert replayed.acquisition_checkpoint is not None
    assert replayed.publish_watermark is not None
    assert replayed.acquisition_checkpoint.sequence == 4
    assert replayed.publish_watermark.sequence == 4


@pytest.mark.security_evidence(id="PG-FILE-PROGRESS-029", layer="postgres")
def test_progress_lineage_denies_direct_nonowner_access_and_cross_org_reads(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_runtime_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    protected = _prepare_file_import_scenario(
        tmp_path, migration_configuration, guarded_control_engine
    )
    other = _prepare_file_import_scenario(
        tmp_path, migration_configuration, guarded_control_engine
    )
    authority = ControlOperatorAuthority(
        _ControlAuthenticator(other.organization_id),
        call_ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    )
    control = ContextControl(
        store=PostgreSQLControlStore(guarded_control_engine, clock=lambda: NOW),
        authority=authority,
        clock=lambda: NOW,
    )
    with (
        authority.authorize(
            opaque_credential="control-secret",
            operation=ControlOperation.READ_SOURCE_PROGRESS,
            request_id="cross-organization-progress-read",
        ) as call,
        pytest.raises(SourceNotAvailable),
    ):
        control.read_file_source_progress(call, protected.source_ref)

    statements = (
        "SELECT count(*) FROM {table}",
        "INSERT INTO {table} DEFAULT VALUES",
        "UPDATE {table} SET organization_id = organization_id WHERE false",
        "DELETE FROM {table} WHERE false",
    )
    for engine in (
        guarded_control_engine,
        guarded_runtime_engine,
        guarded_worker_engine,
    ):
        for table in (
            "file_source_acquisition_checkpoint",
            "file_source_publish_watermark",
        ):
            for statement in statements:
                with engine.connect() as connection:
                    transaction = connection.begin()
                    try:
                        with pytest.raises(SQLAlchemyError):
                            connection.execute(text(statement.format(table=table)))
                    finally:
                        transaction.rollback()
