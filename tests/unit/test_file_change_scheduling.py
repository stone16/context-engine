from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime, timedelta
from uuid import UUID

from engine.control import (
    ContextControl,
    ControlOperation,
    ControlOperatorAuthority,
    FileImportAudience,
    FileImportPath,
    ScheduledFileChange,
    ScheduledFileChangePage,
    ScheduleFileChangePage,
    SourceRef,
    TrustedControlCall,
    VerifiedControlOperatorIdentity,
)
from engine.supply import PreparedFileImport

ORGANIZATION_ID = UUID("ce204f61-b29b-451e-bbc8-cc81385cc742")
SOURCE_ID = UUID("99261811-3186-45fd-904e-152d28388d3d")
SOURCE_VERSION_ID = UUID("d9b37181-0605-4f4e-9ee0-cdef4894012b")
MEMBERSHIP_ID = UUID("5074e7a4-58b4-441e-a6c9-39fb4a579a3d")
JOB_ID = UUID("ce0a82df-c33f-42eb-a868-0a4062f95065")
RECEIVER_ID = UUID("3847ae02-8a57-4fd1-9587-99c5e2bcf785")
PAGE_REF = "a" * 64
NOW = datetime(2026, 7, 25, 10, 0, tzinfo=UTC)


class _Authenticator:
    def authenticate(self, opaque_credential: str) -> VerifiedControlOperatorIdentity:
        assert opaque_credential == "schedule-secret"
        return VerifiedControlOperatorIdentity(
            organization_id=ORGANIZATION_ID,
            operator_ref="operator:file-scheduler",
            authentication_binding_ref="binding:file-scheduler",
            authority_ref="authority:file-scheduler",
            allowed_operations=frozenset(
                {ControlOperation.SCHEDULE_FILE_CHANGE_PAGE}
            ),
            valid_from=NOW - timedelta(minutes=1),
            expires_at=NOW + timedelta(minutes=10),
        )


class _Store:
    def __getattr__(self, name: str) -> object:
        if name in {
            "activate_file_change_feed",
            "offboard_file_source",
            "prepare_file_import",
            "register_file_source",
            "read_source",
            "read_file_source_progress",
            "schedule_file_change_page",
            "tombstone_file_resource",
        }:
            return lambda *args: None
        raise AttributeError(name)

    def schedule_file_change_page(
        self,
        call: TrustedControlCall,
        command: ScheduleFileChangePage,
    ) -> ScheduledFileChangePage:
        assert call.organization_id == ORGANIZATION_ID
        assert call.operation is ControlOperation.SCHEDULE_FILE_CHANGE_PAGE
        prepared = PreparedFileImport(
            organization_id=ORGANIZATION_ID,
            job_id=JOB_ID,
            source_ref=command.source_ref,
            service_principal_id=RECEIVER_ID,
        )
        return ScheduledFileChangePage(
            organization_id=ORGANIZATION_ID,
            source_ref=command.source_ref,
            source_version_ref=command.source_version_ref,
            page_ref=command.page_ref,
            changes=(
                ScheduledFileChange(
                    ordinal=1,
                    path=FileImportPath("handbook.md"),
                    content_sha256="b" * 64,
                    content_length=17,
                    prepared_import=prepared,
                ),
            ),
        )


def test_authorized_operator_schedules_one_page_without_supplying_tenant_or_jobs() -> (
    None
):
    authority = ControlOperatorAuthority(
        _Authenticator(),
        call_ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    )
    control = ContextControl(store=_Store(), authority=authority, clock=lambda: NOW)  # type: ignore[arg-type]
    command = ScheduleFileChangePage(
        source_ref=SourceRef(SOURCE_ID),
        source_version_ref=SOURCE_VERSION_ID,
        page_ref=PAGE_REF,
        audience=FileImportAudience(
            principal_ref="principal:file-reader",
            membership_id=MEMBERSHIP_ID,
            membership_version=1,
        ),
    )

    assert [field.name for field in fields(command)] == [
        "source_ref",
        "source_version_ref",
        "page_ref",
        "audience",
    ]
    with authority.authorize(
        opaque_credential="schedule-secret",
        operation=ControlOperation.SCHEDULE_FILE_CHANGE_PAGE,
        request_id="schedule-page",
    ) as call:
        scheduled = control.schedule_file_change_page(call, command)

    assert scheduled.organization_id == ORGANIZATION_ID
    assert scheduled.source_ref == command.source_ref
    assert scheduled.source_version_ref == command.source_version_ref
    assert scheduled.page_ref == PAGE_REF
    assert tuple(change.ordinal for change in scheduled.changes) == (1,)
    assert scheduled.changes[0].prepared_import.job_id == JOB_ID
    assert "principal:file-reader" not in repr(scheduled)
