from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from engine.control import (
    ContextControl,
    ControlOperation,
    ControlOperatorAuthority,
    ExecutedFileDeleteObservation,
    ExecuteFileDeleteObservation,
    FileResourceTombstone,
    SourceControlUnavailable,
    SourceRef,
    TrustedControlCall,
    VerifiedControlOperatorIdentity,
)

ORGANIZATION_ID = UUID("529de9c1-9e8a-4c1b-95d7-da0cd82cf44d")
SOURCE_ID = UUID("43170ba6-008d-4411-8230-f617752e129d")
SOURCE_VERSION_ID = UUID("1f70c09e-0275-49e4-8354-b10b3d5655c9")
REVISION_ID = UUID("43bd57ab-b09e-44d0-a56f-26df00675790")
CLEANUP_ID = UUID("ba59136d-aae5-4402-85df-42cf72ded5d6")
PAGE_REF = "a" * 64
NOW = datetime(2026, 7, 25, 16, 30, tzinfo=UTC)


class _Authenticator:
    def authenticate(self, opaque_credential: str) -> VerifiedControlOperatorIdentity:
        if opaque_credential != "delete-execution-secret":
            raise AssertionError("unexpected credential")
        return VerifiedControlOperatorIdentity(
            organization_id=ORGANIZATION_ID,
            operator_ref="operator:file-delete-execution",
            authentication_binding_ref="binding:file-delete-execution",
            authority_ref="authority:file-delete-execution",
            allowed_operations=frozenset(
                {ControlOperation.EXECUTE_FILE_DELETE_OBSERVATION}
            ),
            valid_from=NOW - timedelta(minutes=1),
            expires_at=NOW + timedelta(minutes=10),
        )


def _result(command: ExecuteFileDeleteObservation) -> ExecutedFileDeleteObservation:
    return ExecutedFileDeleteObservation(
        organization_id=ORGANIZATION_ID,
        source_ref=command.source_ref,
        source_version_ref=command.source_version_ref,
        page_ref=command.page_ref,
        change_ordinal=command.change_ordinal,
        tombstone=FileResourceTombstone(
            organization_id=ORGANIZATION_ID,
            source_ref=command.source_ref,
            resource_ref="resource:file:" + "b" * 64,
            revision_ref=REVISION_ID,
            event_ref="file-delete-observation-" + "c" * 64,
            event_sequence=17,
            policy_epoch=5,
            cleanup_intent_ref=CLEANUP_ID,
            tombstoned_at=NOW,
        ),
    )


class _Store:
    def __getattr__(self, name: str) -> object:
        if name in {
            "activate_file_change_feed",
            "activate_file_delete_observations",
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

    def execute_file_delete_observation(
        self,
        call: TrustedControlCall,
        command: ExecuteFileDeleteObservation,
    ) -> ExecutedFileDeleteObservation:
        assert call.organization_id == ORGANIZATION_ID
        assert call.operation is ControlOperation.EXECUTE_FILE_DELETE_OBSERVATION
        return _result(command)


def _command() -> ExecuteFileDeleteObservation:
    return ExecuteFileDeleteObservation(
        source_ref=SourceRef(SOURCE_ID),
        source_version_ref=SOURCE_VERSION_ID,
        page_ref=PAGE_REF,
        change_ordinal=2,
    )


def test_command_contains_only_the_exact_delete_locator() -> None:
    command = _command()

    assert [field.name for field in fields(command)] == [
        "source_ref",
        "source_version_ref",
        "page_ref",
        "change_ordinal",
    ]
    assert "resource:file" not in repr(command)
    with pytest.raises(TypeError, match="not serializable"):
        command.__reduce__()


def test_authorized_operator_executes_one_exact_observation() -> None:
    authority = ControlOperatorAuthority(
        _Authenticator(),
        call_ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    )
    control = ContextControl(store=_Store(), authority=authority, clock=lambda: NOW)  # type: ignore[arg-type]
    command = _command()

    with authority.authorize(
        opaque_credential="delete-execution-secret",
        operation=ControlOperation.EXECUTE_FILE_DELETE_OBSERVATION,
        request_id="execute-delete-observation",
    ) as call:
        executed = control.execute_file_delete_observation(call, command)

    assert executed == _result(command)
    assert executed.tombstone.policy_epoch == 5
    assert "resource:file" not in repr(executed)


def test_module_rejects_a_mismatched_store_result() -> None:
    class _MismatchedStore(_Store):
        def execute_file_delete_observation(
            self,
            call: TrustedControlCall,
            command: ExecuteFileDeleteObservation,
        ) -> ExecutedFileDeleteObservation:
            return _result(
                ExecuteFileDeleteObservation(
                    source_ref=command.source_ref,
                    source_version_ref=command.source_version_ref,
                    page_ref="d" * 64,
                    change_ordinal=command.change_ordinal,
                )
            )

    authority = ControlOperatorAuthority(
        _Authenticator(),
        call_ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    )
    control = ContextControl(
        store=_MismatchedStore(),  # type: ignore[arg-type]
        authority=authority,
        clock=lambda: NOW,
    )

    with (
        authority.authorize(
            opaque_credential="delete-execution-secret",
            operation=ControlOperation.EXECUTE_FILE_DELETE_OBSERVATION,
            request_id="reject-mismatch",
        ) as call,
        pytest.raises(SourceControlUnavailable),
    ):
        control.execute_file_delete_observation(call, _command())
