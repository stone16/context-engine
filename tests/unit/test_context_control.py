from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest

from engine.control import (
    FILE_CAPABILITY_MANIFEST,
    CapabilityStatus,
    ContextControl,
    ControlOperation,
    ControlOperatorAuthenticationRejected,
    ControlOperatorAuthority,
    ControlStorePort,
    FileRootRef,
    RegisterFileSource,
    SourceManifest,
    SourceNotAvailable,
    SourceRef,
    TrustedControlCall,
    VerifiedControlOperatorIdentity,
)
from engine.supply import (
    FileImportAudience,
    FileImportPath,
    PreparedFileImport,
    PrepareFileImport,
)

ORGANIZATION_ID = UUID("a6776454-3a24-4c1c-998c-3a69a1d3de23")
NOW = datetime(2026, 7, 22, 18, 50, tzinfo=UTC)


class _Authenticator:
    def authenticate(self, opaque_credential: str) -> VerifiedControlOperatorIdentity:
        if opaque_credential != "control-credential-a":
            raise ControlOperatorAuthenticationRejected
        return VerifiedControlOperatorIdentity(
            organization_id=ORGANIZATION_ID,
            operator_ref="control-operator-a",
            authentication_binding_ref="control-binding-a",
            authority_ref="source-admin-a",
            allowed_operations=frozenset(
                {
                    ControlOperation.IMPORT_FILE,
                    ControlOperation.REGISTER_SOURCE,
                    ControlOperation.READ_SOURCE,
                }
            ),
            valid_from=NOW - timedelta(minutes=1),
            expires_at=NOW + timedelta(hours=1),
        )


class _Store(ControlStorePort):
    def __init__(self) -> None:
        self.manifest: SourceManifest | None = None

    def register_file_source(
        self, call: TrustedControlCall, command: RegisterFileSource
    ) -> SourceManifest:
        assert call.organization_id == ORGANIZATION_ID
        assert call.operation is ControlOperation.REGISTER_SOURCE
        self.manifest = SourceManifest.issue_21_file(
            source_ref=SourceRef(
                UUID("5d37f20a-6a2b-4534-8909-e0118bbc4b47")
            ),
            version_ref=UUID("54ae2c20-02a1-44e7-98bf-4034841fb7ac"),
            display_name=command.display_name,
            root_ref=command.root_ref,
            created_at=NOW,
        )
        return self.manifest

    def read_source(
        self, call: TrustedControlCall, source_ref: SourceRef
    ) -> SourceManifest:
        assert call.organization_id == ORGANIZATION_ID
        assert call.operation is ControlOperation.READ_SOURCE
        if self.manifest is None or source_ref != self.manifest.source_ref:
            raise SourceNotAvailable
        return self.manifest

    def prepare_file_import(
        self, call: TrustedControlCall, command: PrepareFileImport
    ) -> PreparedFileImport:
        assert call.organization_id == ORGANIZATION_ID
        assert call.operation is ControlOperation.IMPORT_FILE
        assert self.manifest is not None
        assert command.source_ref == self.manifest.source_ref
        return PreparedFileImport(
            organization_id=ORGANIZATION_ID,
            job_id=UUID("9de5b515-540b-4c9c-b1d3-f9b691dfbb7a"),
            source_ref=command.source_ref,
            service_principal_id=UUID("0f7bc78d-a76a-477c-b097-ce557b7844b9"),
        )


def _authority() -> ControlOperatorAuthority:
    return ControlOperatorAuthority(
        _Authenticator(),
        call_ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    )


def test_file_registration_command_has_no_identity_mode_or_host_path_input() -> None:
    command = RegisterFileSource(
        display_name="Engineering handbook",
        root_ref=FileRootRef("engineering-handbook"),
        idempotency_key="register-handbook-v1",
    )

    assert [field.name for field in fields(command)] == [
        "display_name",
        "root_ref",
        "idempotency_key",
    ]
    assert command.root_ref == FileRootRef("engineering-handbook")

    for host_path in (
        "/srv/knowledge",
        "../knowledge",
        "~/knowledge",
        "C:\\knowledge",
        "file:///srv/knowledge",
        "folder/knowledge",
        "folder\\knowledge",
        "知识库",
    ):
        with pytest.raises(ValueError, match="logical File root reference"):
            FileRootRef(host_path)


def test_authorized_operator_registers_and_reads_one_honest_file_manifest() -> None:
    store = _Store()
    authority = _authority()
    control = ContextControl(store=store, authority=authority, clock=lambda: NOW)
    command = RegisterFileSource(
        display_name="Engineering handbook",
        root_ref=FileRootRef("engineering-handbook"),
        idempotency_key="register-handbook-v1",
    )

    with authority.authorize(
        opaque_credential="control-credential-a",
        operation=ControlOperation.REGISTER_SOURCE,
        request_id="register-request-a",
    ) as call:
        registered = control.register_source(call, command)
        with pytest.raises(SourceNotAvailable):
            control.register_source(call, command)

    assert registered.active_version.capabilities == FILE_CAPABILITY_MANIFEST
    assert registered.active_version.capabilities.source_mode.value == "materialized"
    assert registered.active_version.capabilities.content_kinds[0].value == "markdown"
    assert registered.active_version.capabilities.acl_evidence_mode.value == "mirrored"
    assert all(
        status is CapabilityStatus.UNAVAILABLE
        for status in (
            registered.active_version.capabilities.cursor_semantics,
            registered.active_version.capabilities.checkpoint_semantics,
            registered.active_version.capabilities.batch_limits,
            registered.active_version.capabilities.freshness,
            registered.active_version.capabilities.consistency_guarantees,
            registered.active_version.capabilities.describe_capabilities,
            registered.active_version.capabilities.read_changes,
            registered.active_version.capabilities.discover,
            registered.active_version.capabilities.authorize_and_project,
            registered.active_version.capabilities.checkpoint,
            registered.active_version.capabilities.deletion,
            registered.active_version.capabilities.file_source_access,
            registered.active_version.capabilities.ingestion_jobs,
        )
    )
    assert registered.active_version.capabilities.document() == {
        "aclEvidenceMode": "mirrored",
        "authorizeAndProject": "unavailable",
        "batchLimits": "unavailable",
        "checkpoint": "unavailable",
        "checkpointSemantics": "unavailable",
        "contentKinds": ["markdown"],
        "consistencyGuarantees": "unavailable",
        "cursorSemantics": "unavailable",
        "declarationVersion": "file-capabilities-v1",
        "deletion": "unavailable",
        "describeCapabilities": "unavailable",
        "discover": "unavailable",
        "fileSourceAccess": "unavailable",
        "freshness": "unavailable",
        "ingestionJobs": "unavailable",
        "projectionFields": [],
        "readChanges": "unavailable",
        "resourceKinds": ["markdown_document"],
        "sourceMode": "materialized",
    }

    with authority.authorize(
        opaque_credential="control-credential-a",
        operation=ControlOperation.READ_SOURCE,
        request_id="read-request-a",
    ) as call:
        assert control.read_source(call, registered.source_ref) == registered


def test_authorized_operator_prepares_one_credential_free_file_import() -> None:
    store = _Store()
    authority = _authority()
    control = ContextControl(store=store, authority=authority, clock=lambda: NOW)
    with authority.authorize(
        opaque_credential="control-credential-a",
        operation=ControlOperation.REGISTER_SOURCE,
        request_id="register-for-import",
    ) as call:
        source = control.register_source(
            call,
            RegisterFileSource("Handbook", FileRootRef("handbook"), "handbook"),
        )
    command = PrepareFileImport(
        source_ref=source.source_ref,
        path=FileImportPath("handbook.md"),
        audience=FileImportAudience(
            principal_ref="principal:handbook-reader",
            membership_id=UUID("82a11990-7a87-4693-a3de-c3cab5fab7aa"),
            membership_version=1,
        ),
        idempotency_key="handbook-import-v1",
    )

    with authority.authorize(
        opaque_credential="control-credential-a",
        operation=ControlOperation.IMPORT_FILE,
        request_id="prepare-import",
    ) as call:
        prepared = control.prepare_file_import(call, command)

    assert prepared.organization_id == ORGANIZATION_ID
    assert prepared.source_ref == source.source_ref
    assert prepared.workload == "supply.file-import"
    assert prepared.operation == "file.import"
    assert "credential" not in repr(prepared).casefold()


def test_source_ref_and_forged_or_wrong_operation_calls_never_authorize_control() -> (
    None
):
    store = _Store()
    authority = _authority()
    control = ContextControl(store=store, authority=authority, clock=lambda: NOW)
    command = RegisterFileSource(
        display_name="Engineering handbook",
        root_ref=FileRootRef("engineering-handbook"),
        idempotency_key="register-handbook-v1",
    )

    with pytest.raises(TypeError, match="authority-constructed"):
        TrustedControlCall()
    with pytest.raises(SourceNotAvailable):
        control.register_source(cast(TrustedControlCall, object()), command)
    with pytest.raises(SourceNotAvailable):
        control.register_source(
            cast(TrustedControlCall, SourceRef(ORGANIZATION_ID)), command
        )

    with authority.authorize(
        opaque_credential="control-credential-a",
        operation=ControlOperation.READ_SOURCE,
        request_id="wrong-operation-request",
    ) as read_call, pytest.raises(SourceNotAvailable):
        control.register_source(read_call, command)

    with authority.authorize(
        opaque_credential="control-credential-a",
        operation=ControlOperation.REGISTER_SOURCE,
        request_id="scope-state-request",
    ) as scoped_call:
        scope = object.__getattribute__(scoped_call, "_scope")
        assert not hasattr(scope, "active")
        assert not hasattr(scope, "consumed")
        control.register_source(scoped_call, command)
        with pytest.raises(SourceNotAvailable):
            control.register_source(scoped_call, command)


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("organization_id", UUID("629a286b-34b5-41f4-a7d4-7793b3b5b013")),
        ("operator_ref", "substituted-operator"),
        ("request_id", "substituted-request"),
        ("expires_at", NOW + timedelta(days=1)),
    ],
)
def test_trusted_control_call_rejects_claim_tampering(
    field_name: str,
    replacement: object,
) -> None:
    store = _Store()
    authority = _authority()
    control = ContextControl(store=store, authority=authority, clock=lambda: NOW)
    command = RegisterFileSource(
        "Handbook", FileRootRef("handbook"), "handbook-v1"
    )

    with authority.authorize(
        opaque_credential="control-credential-a",
        operation=ControlOperation.REGISTER_SOURCE,
        request_id="register-request-a",
    ) as call:
        object.__setattr__(call, field_name, replacement)
        with pytest.raises(SourceNotAvailable):
            control.register_source(call, command)
