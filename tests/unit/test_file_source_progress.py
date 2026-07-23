from __future__ import annotations

from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from engine.control import (
    ContextControl,
    ControlOperation,
    ControlOperatorAuthenticationRejected,
    ControlOperatorAuthority,
    FileResourceTombstone,
    FileSourceAcquisitionCheckpoint,
    FileSourceChangeKind,
    FileSourceProgress,
    FileSourcePublishOutcome,
    FileSourcePublishWatermark,
    RegisterFileSource,
    SourceManifest,
    SourceNotAvailable,
    SourceRef,
    TrustedControlCall,
    VerifiedControlOperatorIdentity,
)
from engine.control.file_deletions import TombstoneFileResource
from engine.control.file_imports import PrepareFileImport
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

    def tombstone_file_resource(
        self, call: TrustedControlCall, command: TombstoneFileResource
    ) -> FileResourceTombstone:
        raise AssertionError("unexpected tombstone")

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


def test_progress_read_is_operation_bound_and_mismatch_fails_closed() -> None:
    authority = ControlOperatorAuthority(
        _Authenticator(), call_ttl=timedelta(minutes=5), clock=lambda: NOW
    )
    control = ContextControl(store=_Store(), authority=authority, clock=lambda: NOW)
    with authority.authorize(
        opaque_credential="progress-reader",
        operation=ControlOperation.READ_SOURCE_PROGRESS,
        request_id="read-source-progress-mismatch",
    ) as call, pytest.raises(SourceNotAvailable):
        control.read_file_source_progress(
            call,
            SourceRef(UUID("0cd79e42-04b3-4146-929e-72c316171c99")),
        )
