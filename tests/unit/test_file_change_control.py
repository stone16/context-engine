from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from engine.control import (
    AcceptedChangePage,
    ActivateFileChangeFeed,
    ChangeCursor,
    ChangePage,
    ContextControl,
    ControlOperation,
    ControlOperatorAuthority,
    FileChangeControlProofs,
    FileChangeKind,
    FileChangeProviderProofs,
    FileResourceTombstone,
    FileSourceOffboarding,
    FileSourceProgress,
    OffboardFileSource,
    PendingChangeCursor,
    RegisterFileSource,
    ScheduledFileChangePage,
    ScheduleFileChangePage,
    SourceChange,
    SourceManifest,
    SourceNotAvailable,
    SourceRef,
    TombstoneFileResource,
    TrustedControlCall,
    VerifiedChangePage,
    VerifiedControlOperatorIdentity,
)
from engine.control.file_imports import FileImportPath, PrepareFileImport
from engine.supply import PreparedFileImport

ORGANIZATION_ID = UUID("52d0f7ef-aa39-4af4-8b40-2cabf016a08e")
SOURCE_ID = UUID("93fb9ca3-eeb1-4eb6-8c4a-89887a3ac753")
VERSION_ID = UUID("eeb86af0-247e-4c41-b4ee-8c4b7aac7a66")
NOW = datetime(2026, 7, 25, 11, 0, tzinfo=UTC)
PROVIDER_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
CHECKPOINT_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32, 64)))


class _Authenticator:
    def authenticate(self, opaque_credential: str) -> VerifiedControlOperatorIdentity:
        if opaque_credential != "file-change-control":
            raise AssertionError("unexpected credential")
        return VerifiedControlOperatorIdentity(
            organization_id=ORGANIZATION_ID,
            operator_ref="operator:file-change-control",
            authentication_binding_ref="binding:file-change-control",
            authority_ref="authority:file-change-control",
            allowed_operations=frozenset(
                {ControlOperation.ACCEPT_FILE_CHANGE_PAGE}
            ),
            valid_from=NOW - timedelta(minutes=1),
            expires_at=NOW + timedelta(minutes=10),
        )


class _Store:
    def __init__(self) -> None:
        self.accepted: list[VerifiedChangePage] = []

    def accept_file_change_page(
        self, call: TrustedControlCall, page: VerifiedChangePage
    ) -> AcceptedChangePage:
        assert call.organization_id == ORGANIZATION_ID
        assert call.operation is ControlOperation.ACCEPT_FILE_CHANGE_PAGE
        self.accepted.append(page)
        return AcceptedChangePage(
            source_ref=SourceRef(SOURCE_ID),
            source_version_ref=VERSION_ID,
            scan_ref=page.page.scan_ref,
            scan_epoch=page.page.scan_epoch,
            page_limit=page.page.page_limit,
            superseded_scan_epoch=page.page.superseded_scan_epoch,
            page_ref=page.page_ref,
            checkpoint_ref="facp_" + "b" * 64,
            sequence=7,
            change_count=1,
            complete=False,
            next_cursor=ChangeCursor("A" * 128 + "." + "B" * 86),
            accepted_at=NOW,
        )

    def activate_file_change_feed(
        self, call: TrustedControlCall, command: ActivateFileChangeFeed
    ) -> SourceManifest:
        raise AssertionError("unexpected Control operation")

    def activate_file_delete_observations(self, *args: object) -> SourceManifest:
        raise AssertionError("unexpected Control operation")

    def offboard_file_source(
        self, call: TrustedControlCall, command: OffboardFileSource
    ) -> FileSourceOffboarding:
        raise AssertionError("unexpected Control operation")

    def prepare_file_import(
        self, call: TrustedControlCall, command: PrepareFileImport
    ) -> PreparedFileImport:
        raise AssertionError("unexpected Control operation")

    def read_file_source_progress(
        self, call: TrustedControlCall, source_ref: SourceRef
    ) -> FileSourceProgress:
        raise AssertionError("unexpected Control operation")

    def schedule_file_change_page(
        self, call: TrustedControlCall, command: ScheduleFileChangePage
    ) -> ScheduledFileChangePage:
        raise AssertionError("unexpected Control operation")

    def read_source(
        self, call: TrustedControlCall, source_ref: SourceRef
    ) -> SourceManifest:
        raise AssertionError("unexpected Control operation")

    def register_file_source(
        self, call: TrustedControlCall, command: RegisterFileSource
    ) -> SourceManifest:
        raise AssertionError("unexpected Control operation")

    def tombstone_file_resource(
        self, call: TrustedControlCall, command: TombstoneFileResource
    ) -> FileResourceTombstone:
        raise AssertionError("unexpected Control operation")


def _page() -> ChangePage:
    provider = FileChangeProviderProofs(
        provider_signing_key=PROVIDER_KEY,
        checkpoint_verification_key=CHECKPOINT_KEY.public_key(),
    )
    unsigned = ChangePage(
        organization_id=ORGANIZATION_ID,
        source_ref=SOURCE_ID,
        source_version_ref=VERSION_ID,
        scan_ref="a" * 64,
        scan_epoch=UUID("18542e25-51a5-424f-92e8-a79332ea9609"),
        page_limit=1,
        predecessor_page_ref=None,
        predecessor_checkpoint_ref=None,
        predecessor_sequence=None,
        superseded_scan_epoch=None,
        changes=(
            SourceChange(
                organization_id=ORGANIZATION_ID,
                source_ref=SOURCE_ID,
                source_version_ref=VERSION_ID,
                scan_ref="a" * 64,
                kind=FileChangeKind.UPSERT,
                path=FileImportPath("handbook.md"),
                content_sha256="c" * 64,
                content_length=12,
            ),
        ),
        next_cursor=PendingChangeCursor("A" * 64 + "." + "B" * 43),
        complete=False,
        provider_proof="A" * 86,
    )
    return replace(unsigned, provider_proof=provider._seal_page(unsigned))


def test_context_control_accepts_verified_whole_page_before_issuing_cursor() -> None:
    store = _Store()
    authority = ControlOperatorAuthority(
        _Authenticator(), call_ttl=timedelta(minutes=5), clock=lambda: NOW
    )
    control = ContextControl(
        store=store,
        authority=authority,
        clock=lambda: NOW,
        file_change_proofs=FileChangeControlProofs(
            provider_verification_key=PROVIDER_KEY.public_key(),
        ),
    )
    page = _page()

    with authority.authorize(
        opaque_credential="file-change-control",
        operation=ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        request_id="accept-page",
    ) as call:
        accepted = control.accept_file_change_page(call, page)

    assert type(accepted) is AcceptedChangePage
    assert accepted.page_ref == store.accepted[0].page_ref
    assert accepted.sequence == 7
    assert accepted.next_cursor is not None
    assert type(page.next_cursor) is PendingChangeCursor
    assert page.next_cursor.value not in repr(accepted.next_cursor)


def test_context_control_rejects_modified_provider_page_before_store() -> None:
    store = _Store()
    authority = ControlOperatorAuthority(
        _Authenticator(), call_ttl=timedelta(minutes=5), clock=lambda: NOW
    )
    control = ContextControl(
        store=store,
        authority=authority,
        clock=lambda: NOW,
        file_change_proofs=FileChangeControlProofs(
            provider_verification_key=PROVIDER_KEY.public_key(),
        ),
    )
    page = _page()
    tampered = replace(
        page,
        changes=(replace(page.changes[0], content_length=13),),
    )

    with authority.authorize(
        opaque_credential="file-change-control",
        operation=ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        request_id="reject-page",
    ) as call, pytest.raises(SourceNotAvailable):
        control.accept_file_change_page(call, tampered)

    assert store.accepted == []


def test_context_control_fails_closed_without_proofs_or_for_foreign_page() -> None:
    store = _Store()
    authority = ControlOperatorAuthority(
        _Authenticator(), call_ttl=timedelta(minutes=5), clock=lambda: NOW
    )
    page = _page()
    unverified_control = ContextControl(
        store=store,
        authority=authority,
        clock=lambda: NOW,
    )
    with authority.authorize(
        opaque_credential="file-change-control",
        operation=ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        request_id="missing-page-proofs",
    ) as call, pytest.raises(SourceNotAvailable):
        unverified_control.accept_file_change_page(call, page)

    provider = FileChangeProviderProofs(
        provider_signing_key=PROVIDER_KEY,
        checkpoint_verification_key=CHECKPOINT_KEY.public_key(),
    )
    foreign_organization = UUID("9d700aa9-5a72-4301-a40b-14016b91846b")
    unsigned_foreign = replace(
        page,
        organization_id=foreign_organization,
        changes=(
            replace(page.changes[0], organization_id=foreign_organization),
        ),
        provider_proof="A" * 86,
    )
    foreign = replace(
        unsigned_foreign,
        provider_proof=provider._seal_page(unsigned_foreign),
    )
    verified_control = ContextControl(
        store=store,
        authority=authority,
        clock=lambda: NOW,
        file_change_proofs=FileChangeControlProofs(
            provider_verification_key=PROVIDER_KEY.public_key(),
        ),
    )
    with authority.authorize(
        opaque_credential="file-change-control",
        operation=ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        request_id="foreign-page",
    ) as call, pytest.raises(SourceNotAvailable):
        verified_control.accept_file_change_page(call, foreign)

    assert store.accepted == []


def test_change_page_requires_complete_predecessor_and_page_bound() -> None:
    page = _page()
    for partial in (
        {"predecessor_page_ref": "d" * 64},
        {"predecessor_checkpoint_ref": "facp_" + "e" * 64},
        {"predecessor_sequence": 1},
        {
            "predecessor_page_ref": "d" * 64,
            "predecessor_checkpoint_ref": "facp_" + "e" * 64,
        },
        {
            "predecessor_page_ref": "d" * 64,
            "predecessor_sequence": 1,
        },
        {
            "predecessor_checkpoint_ref": "facp_" + "e" * 64,
            "predecessor_sequence": 1,
        },
    ):
        with pytest.raises(ValueError, match="binding is incomplete"):
            replace(page, **partial)

    second_change = replace(
        page.changes[0],
        path=FileImportPath("second.md"),
        content_sha256="d" * 64,
    )
    with pytest.raises(TypeError, match="bounded SourceChange tuple"):
        replace(page, changes=(*page.changes, second_change))
