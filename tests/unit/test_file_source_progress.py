from __future__ import annotations

from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from engine.control import (
    ActivateFileChangeFeed,
    ContextControl,
    ControlOperation,
    ControlOperatorAuthenticationRejected,
    ControlOperatorAuthority,
    ExecutedFileDeleteObservation,
    ExecuteFileDeleteObservation,
    FileChangeBaseline,
    FileChangeBaselineEntry,
    FileChangeBaselineRef,
    FileChangeKind,
    FileChangeScanHead,
    FileImportPath,
    FileResourceTombstone,
    FileSourceAcquisitionCheckpoint,
    FileSourceChangeKind,
    FileSourceOffboarding,
    FileSourceProgress,
    FileSourcePublishOutcome,
    FileSourcePublishWatermark,
    OffboardFileSource,
    RegisterFileSource,
    ScheduledFileChangePage,
    ScheduleFileChangePage,
    SourceManifest,
    SourceNotAvailable,
    SourceRef,
    TrustedControlCall,
    VerifiedControlOperatorIdentity,
)
from engine.control.file_deletions import TombstoneFileResource
from engine.control.file_imports import PrepareFileImport
from engine.persistence import PostgreSQLControlStore
from engine.supply import PreparedFileImport

ORGANIZATION_ID = UUID("f0381079-a64d-4984-977e-cd1654c049ed")
SOURCE_REF = SourceRef(UUID("e11d54e9-2ba6-4812-a215-794509bd1f4f"))
ACQUISITION_ID = UUID("61ca7538-e645-4d2a-a199-00dbf4960728")
JOB_ID = UUID("de2d89f2-042a-4fbe-b90b-6db335d02655")
REVISION_ID = UUID("270cd450-2b9f-4d66-997d-b47c85517031")
NOW = datetime(2026, 7, 23, 8, 0, tzinfo=UTC)
RESOURCE_REF = "resource:file:" + "a" * 64


class _Authenticator:
    def authenticate(self, opaque_credential: str) -> VerifiedControlOperatorIdentity:
        if opaque_credential != "progress-reader":
            raise ControlOperatorAuthenticationRejected
        return VerifiedControlOperatorIdentity(
            organization_id=ORGANIZATION_ID,
            operator_ref="operator:progress-reader",
            authentication_binding_ref="binding:progress-reader",
            authority_ref="authority:source-progress",
            allowed_operations=frozenset({ControlOperation.READ_SOURCE_PROGRESS}),
            valid_from=NOW - timedelta(minutes=1),
            expires_at=NOW + timedelta(minutes=10),
        )


class _Store:
    def activate_file_change_feed(
        self, call: TrustedControlCall, command: ActivateFileChangeFeed
    ) -> SourceManifest:
        raise AssertionError("unexpected File change activation")

    def activate_file_delete_observations(self, *args: object) -> SourceManifest:
        raise AssertionError("unexpected File delete observation activation")

    def offboard_file_source(
        self, call: TrustedControlCall, command: OffboardFileSource
    ) -> FileSourceOffboarding:
        raise AssertionError("unexpected offboarding")

    def register_file_source(
        self, call: TrustedControlCall, command: RegisterFileSource
    ) -> SourceManifest:
        raise AssertionError("unexpected registration")

    def read_source(
        self, call: TrustedControlCall, source_ref: SourceRef
    ) -> SourceManifest:
        raise AssertionError("unexpected source read")

    def prepare_file_import(
        self, call: TrustedControlCall, command: PrepareFileImport
    ) -> PreparedFileImport:
        raise AssertionError("unexpected import")

    def schedule_file_change_page(
        self, call: TrustedControlCall, command: ScheduleFileChangePage
    ) -> ScheduledFileChangePage:
        raise AssertionError("unexpected File change scheduling")

    def tombstone_file_resource(
        self, call: TrustedControlCall, command: TombstoneFileResource
    ) -> FileResourceTombstone:
        raise AssertionError("unexpected tombstone")

    def execute_file_delete_observation(
        self,
        call: TrustedControlCall,
        command: ExecuteFileDeleteObservation,
    ) -> ExecutedFileDeleteObservation:
        raise AssertionError("unexpected File delete execution")

    def read_file_source_progress(
        self,
        call: TrustedControlCall,
        source_ref: SourceRef,
    ) -> FileSourceProgress:
        assert call.organization_id == ORGANIZATION_ID
        assert call.operation is ControlOperation.READ_SOURCE_PROGRESS
        if source_ref != SOURCE_REF:
            raise SourceNotAvailable
        checkpoint = FileSourceAcquisitionCheckpoint(
            sequence=2,
            checkpoint_ref="facp_" + "b" * 64,
            change_kind=FileSourceChangeKind.FILE_IMPORT,
            acquisition_ref=ACQUISITION_ID,
            job_ref=JOB_ID,
            cleanup_intent_ref=None,
            resource_ref=None,
            revision_ref=None,
            event_ref=None,
            event_sequence=None,
            accepted_at=NOW,
        )
        watermark = FileSourcePublishWatermark(
            sequence=1,
            watermark_ref="fpwm_" + "c" * 64,
            checkpoint_ref="facp_" + "d" * 64,
            change_kind=FileSourceChangeKind.FILE_IMPORT,
            outcome=FileSourcePublishOutcome.PUBLISHED,
            acquisition_ref=UUID("d9999beb-f185-454f-882b-6a2ca973d3ac"),
            job_ref=UUID("ab87e202-42d8-49e9-87b0-a5317933bb07"),
            cleanup_intent_ref=None,
            resource_ref=RESOURCE_REF,
            revision_ref=REVISION_ID,
            event_ref=None,
            event_sequence=None,
            published_at=NOW - timedelta(seconds=1),
        )
        return FileSourceProgress(
            organization_id=ORGANIZATION_ID,
            source_ref=source_ref,
            acquisition_checkpoint=checkpoint,
            publish_watermark=watermark,
        )


def test_control_reads_distinct_source_checkpoint_and_publish_watermark() -> None:
    authority = ControlOperatorAuthority(
        _Authenticator(), call_ttl=timedelta(minutes=5), clock=lambda: NOW
    )
    control = ContextControl(store=_Store(), authority=authority, clock=lambda: NOW)

    with authority.authorize(
        opaque_credential="progress-reader",
        operation=ControlOperation.READ_SOURCE_PROGRESS,
        request_id="read-source-progress",
    ) as call:
        progress = control.read_file_source_progress(call, SOURCE_REF)

    assert progress.organization_id == ORGANIZATION_ID
    assert progress.source_ref == SOURCE_REF
    assert progress.acquisition_checkpoint is not None
    assert progress.publish_watermark is not None
    assert progress.acquisition_checkpoint.sequence == 2
    assert progress.publish_watermark.sequence == 1
    assert progress.acquisition_checkpoint.checkpoint_ref.startswith("facp_")
    assert progress.publish_watermark.watermark_ref.startswith("fpwm_")


def test_progress_contracts_keep_checkpoint_and_watermark_semantics_separate() -> None:
    assert [field.name for field in fields(FileSourceProgress)] == [
        "organization_id",
        "source_ref",
        "acquisition_checkpoint",
        "publish_watermark",
        "change_scan_head",
        "complete_change_baseline",
        "pending_change_schedules",
    ]
    assert FileSourceChangeKind.FILE_IMPORT.value == "file_import"
    assert FileSourceChangeKind.FILE_TOMBSTONE.value == "file_tombstone"
    assert FileSourcePublishOutcome.TOMBSTONED.value == "tombstoned"

    with pytest.raises(ValueError, match="cannot exceed"):
        FileSourceProgress(
            organization_id=ORGANIZATION_ID,
            source_ref=SOURCE_REF,
            acquisition_checkpoint=FileSourceAcquisitionCheckpoint(
                sequence=1,
                checkpoint_ref="facp_" + "a" * 64,
                change_kind=FileSourceChangeKind.FILE_IMPORT,
                acquisition_ref=ACQUISITION_ID,
                job_ref=JOB_ID,
                cleanup_intent_ref=None,
                resource_ref=None,
                revision_ref=None,
                event_ref=None,
                event_sequence=None,
                accepted_at=NOW,
            ),
            publish_watermark=FileSourcePublishWatermark(
                sequence=2,
                watermark_ref="fpwm_" + "b" * 64,
                checkpoint_ref="facp_" + "c" * 64,
                change_kind=FileSourceChangeKind.FILE_IMPORT,
                outcome=FileSourcePublishOutcome.PUBLISHED,
                acquisition_ref=ACQUISITION_ID,
                job_ref=JOB_ID,
                cleanup_intent_ref=None,
                resource_ref=RESOURCE_REF,
                revision_ref=REVISION_ID,
                event_ref=None,
                event_sequence=None,
                published_at=NOW,
            ),
        )


def test_equal_progress_sequences_require_exact_checkpoint_lineage() -> None:
    checkpoint_ref = "facp_" + "a" * 64
    checkpoint = FileSourceAcquisitionCheckpoint(
        sequence=1,
        checkpoint_ref=checkpoint_ref,
        change_kind=FileSourceChangeKind.FILE_IMPORT,
        acquisition_ref=ACQUISITION_ID,
        job_ref=JOB_ID,
        cleanup_intent_ref=None,
        resource_ref=RESOURCE_REF,
        revision_ref=REVISION_ID,
        event_ref=None,
        event_sequence=None,
        accepted_at=NOW,
    )
    watermark = FileSourcePublishWatermark(
        sequence=1,
        watermark_ref="fpwm_" + "b" * 64,
        checkpoint_ref=checkpoint_ref,
        change_kind=FileSourceChangeKind.FILE_IMPORT,
        outcome=FileSourcePublishOutcome.PUBLISHED,
        acquisition_ref=ACQUISITION_ID,
        job_ref=JOB_ID,
        cleanup_intent_ref=None,
        resource_ref=RESOURCE_REF,
        revision_ref=REVISION_ID,
        event_ref=None,
        event_sequence=None,
        published_at=NOW,
    )

    progress = FileSourceProgress(
        organization_id=ORGANIZATION_ID,
        source_ref=SOURCE_REF,
        acquisition_checkpoint=checkpoint,
        publish_watermark=watermark,
    )
    assert progress.publish_watermark == watermark

    with pytest.raises(ValueError, match="watermark lineage is invalid"):
        FileSourceProgress(
            organization_id=ORGANIZATION_ID,
            source_ref=SOURCE_REF,
            acquisition_checkpoint=checkpoint,
            publish_watermark=replace(
                watermark,
                checkpoint_ref="facp_" + "c" * 64,
            ),
        )


def test_change_scan_head_requires_matching_durable_checkpoint_lineage() -> None:
    checkpoint = FileSourceAcquisitionCheckpoint(
        sequence=2,
        checkpoint_ref="facp_" + "a" * 64,
        change_kind=FileSourceChangeKind.FILE_CHANGE_PAGE,
        acquisition_ref=None,
        job_ref=None,
        cleanup_intent_ref=None,
        resource_ref=None,
        revision_ref=None,
        event_ref=None,
        event_sequence=None,
        accepted_at=NOW,
        source_version_ref=REVISION_ID,
        change_page_ref="b" * 64,
    )
    head = FileChangeScanHead(
        source_version_ref=REVISION_ID,
        scan_ref="c" * 64,
        scan_epoch=UUID("903c6391-8d91-412d-a329-031a642c3359"),
        page_limit=1,
        page_ref="b" * 64,
        checkpoint_ref=checkpoint.checkpoint_ref,
        sequence=checkpoint.sequence,
        complete=True,
    )
    progress = FileSourceProgress(
        organization_id=ORGANIZATION_ID,
        source_ref=SOURCE_REF,
        acquisition_checkpoint=checkpoint,
        publish_watermark=None,
        change_scan_head=head,
    )
    assert progress.change_scan_head == head

    with pytest.raises(ValueError, match="requires a checkpoint"):
        FileSourceProgress(
            organization_id=ORGANIZATION_ID,
            source_ref=SOURCE_REF,
            acquisition_checkpoint=None,
            publish_watermark=None,
            change_scan_head=head,
        )
    with pytest.raises(ValueError, match="exceeds its checkpoint"):
        FileSourceProgress(
            organization_id=ORGANIZATION_ID,
            source_ref=SOURCE_REF,
            acquisition_checkpoint=replace(checkpoint, sequence=1),
            publish_watermark=None,
            change_scan_head=head,
        )
    with pytest.raises(ValueError, match="head lineage is invalid"):
        FileSourceProgress(
            organization_id=ORGANIZATION_ID,
            source_ref=SOURCE_REF,
            acquisition_checkpoint=replace(
                checkpoint,
                checkpoint_ref="facp_" + "d" * 64,
            ),
            publish_watermark=None,
            change_scan_head=head,
        )
    with pytest.raises(ValueError, match="head lineage is invalid"):
        FileSourceProgress(
            organization_id=ORGANIZATION_ID,
            source_ref=SOURCE_REF,
            acquisition_checkpoint=replace(
                checkpoint,
                change_page_ref="e" * 64,
            ),
            publish_watermark=None,
            change_scan_head=head,
        )


def test_complete_change_baseline_is_distinct_from_an_incomplete_head() -> None:
    checkpoint = FileSourceAcquisitionCheckpoint(
        sequence=3,
        checkpoint_ref="facp_" + "1" * 64,
        change_kind=FileSourceChangeKind.FILE_CHANGE_PAGE,
        acquisition_ref=None,
        job_ref=None,
        cleanup_intent_ref=None,
        resource_ref=None,
        revision_ref=None,
        event_ref=None,
        event_sequence=None,
        accepted_at=NOW,
        source_version_ref=REVISION_ID,
        change_page_ref="2" * 64,
    )
    complete_reference = FileChangeBaselineRef(
        source_version_ref=REVISION_ID,
        scan_ref="3" * 64,
        scan_epoch=UUID("a8dc4a16-c0e4-4c4c-8a95-208c4d2acd23"),
        page_ref="4" * 64,
        checkpoint_ref="facp_" + "5" * 64,
        sequence=2,
    )
    baseline = FileChangeBaseline(
        reference=complete_reference,
        entries=(
            FileChangeBaselineEntry(
                kind=FileChangeKind.UPSERT,
                path=FileImportPath("a.md"),
                content_sha256="6" * 64,
                content_length=1,
            ),
        ),
    )
    incomplete_head = FileChangeScanHead(
        source_version_ref=REVISION_ID,
        scan_ref="7" * 64,
        scan_epoch=UUID("c4d7d954-b5fb-4785-a93a-b6278860c434"),
        page_limit=1,
        page_ref=checkpoint.change_page_ref or "",
        checkpoint_ref=checkpoint.checkpoint_ref,
        sequence=checkpoint.sequence,
        complete=False,
        superseded_scan_epoch=complete_reference.scan_epoch,
    )

    progress = FileSourceProgress(
        organization_id=ORGANIZATION_ID,
        source_ref=SOURCE_REF,
        acquisition_checkpoint=checkpoint,
        publish_watermark=None,
        change_scan_head=incomplete_head,
        complete_change_baseline=baseline,
    )

    assert progress.change_scan_head == incomplete_head
    assert progress.complete_change_baseline == baseline

    with pytest.raises(ValueError, match="baseline exceeds"):
        replace(
            progress,
            complete_change_baseline=FileChangeBaseline(
                reference=replace(complete_reference, sequence=4),
                entries=baseline.entries,
            ),
        )
    with pytest.raises(ValueError, match="another SourceVersion"):
        replace(
            progress,
            complete_change_baseline=FileChangeBaseline(
                reference=replace(
                    complete_reference,
                    source_version_ref=UUID("3ea05cf1-29d9-46c8-a082-0798dc46cdfd"),
                ),
                entries=baseline.entries,
            ),
        )


def test_database_baseline_projection_restores_global_canonical_path_order() -> None:
    common: dict[str, object] = {
        "baseline_source_version_id": REVISION_ID,
        "baseline_scan_ref": "3" * 64,
        "baseline_scan_epoch": UUID("a8dc4a16-c0e4-4c4c-8a95-208c4d2acd23"),
        "baseline_page_ref": "4" * 64,
        "baseline_checkpoint_ref": "facp_" + "5" * 64,
        "baseline_sequence": 2,
        "baseline_parent_scan_epoch": None,
    }
    rows = tuple(
        {
            **common,
            "baseline_entry_kind": "upsert",
            "baseline_entry_path": path,
            "baseline_entry_content_sha256": digest * 64,
            "baseline_entry_content_length": 1,
        }
        for path, digest in (("z.md", "7"), ("a.md", "6"))
    )

    baseline = PostgreSQLControlStore._complete_change_baseline(rows)

    assert baseline is not None
    assert [entry.path.value for entry in baseline.entries] == ["a.md", "z.md"]


def test_page_checkpoint_cannot_carry_publication_lineage_or_watermark() -> None:
    with pytest.raises(ValueError, match="publication lineage"):
        FileSourceAcquisitionCheckpoint(
            sequence=1,
            checkpoint_ref="facp_" + "a" * 64,
            change_kind=FileSourceChangeKind.FILE_CHANGE_PAGE,
            acquisition_ref=ACQUISITION_ID,
            job_ref=None,
            cleanup_intent_ref=None,
            resource_ref=None,
            revision_ref=None,
            event_ref=None,
            event_sequence=None,
            accepted_at=NOW,
            source_version_ref=REVISION_ID,
            change_page_ref="b" * 64,
        )
    with pytest.raises(ValueError, match="cannot advance a publish watermark"):
        FileSourcePublishWatermark(
            sequence=1,
            watermark_ref="fpwm_" + "b" * 64,
            checkpoint_ref="facp_" + "a" * 64,
            change_kind=FileSourceChangeKind.FILE_CHANGE_PAGE,
            outcome=FileSourcePublishOutcome.PUBLISHED,
            acquisition_ref=None,
            job_ref=None,
            cleanup_intent_ref=None,
            resource_ref=RESOURCE_REF,
            revision_ref=REVISION_ID,
            event_ref=None,
            event_sequence=None,
            published_at=NOW,
        )


def test_progress_read_is_operation_bound_and_mismatch_fails_closed() -> None:
    authority = ControlOperatorAuthority(
        _Authenticator(), call_ttl=timedelta(minutes=5), clock=lambda: NOW
    )
    control = ContextControl(store=_Store(), authority=authority, clock=lambda: NOW)
    with (
        authority.authorize(
            opaque_credential="progress-reader",
            operation=ControlOperation.READ_SOURCE_PROGRESS,
            request_id="read-source-progress-mismatch",
        ) as call,
        pytest.raises(SourceNotAvailable),
    ):
        control.read_file_source_progress(
            call,
            SourceRef(UUID("0cd79e42-04b3-4146-929e-72c316171c99")),
        )
