from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import rfc8785
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from adapters.file_source import FileChangeProvider, FileReadLimits, FileRootRegistry
from engine._opaque import encode_base64url
from engine.control import (
    FILE_CHANGE_CAPABILITY_MANIFEST,
    FILE_IMPORT_CAPABILITY_MANIFEST,
    ChangeCursor,
    ChangeLimit,
    FileChangeControlProofs,
    FileChangeKind,
    FileChangeProviderProofs,
    FileChangeScanHead,
    FileChangeSource,
    FileRootRef,
    InitialScan,
    PendingChangeCursor,
    ProviderGenericDenied,
    ProviderInvalidCheckpoint,
    ProviderOk,
    ProviderRetryableUnavailable,
    ProviderUnsupported,
    SourceManifest,
    SourceRef,
)
from engine.control.file_change_pages import _accepted_cursor_payload

ORGANIZATION_ID = UUID("cbbce347-023b-4190-bbda-97c958f8a6da")
SOURCE_ID = UUID("c5f5b7ec-70b5-4f9a-ac5e-a4694570407b")
SOURCE_VERSION_ID = UUID("be3dcdbf-dcff-4bb8-82c3-25a1bfae1c88")
NOW = datetime(2026, 7, 25, 9, 0, tzinfo=UTC)
PROVIDER_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
CHECKPOINT_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32, 64)))


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


def _source(*, scan_head: FileChangeScanHead | None = None) -> FileChangeSource:
    manifest = SourceManifest.registered_file(
        source_ref=SourceRef(SOURCE_ID),
        version_ref=SOURCE_VERSION_ID,
        display_name="Handbook",
        root_ref=FileRootRef("handbook-root"),
        created_at=NOW,
        capabilities=FILE_CHANGE_CAPABILITY_MANIFEST,
    )
    return FileChangeSource(
        organization_id=ORGANIZATION_ID,
        source_version=manifest.active_version,
        scan_head=scan_head,
    )


def test_initial_scan_returns_one_ordered_content_free_markdown_change(
    tmp_path: Path,
) -> None:
    root = tmp_path / "registered-root"
    root.mkdir()
    (root / "z-last.md").write_bytes(b"# Last\n")
    (root / "a-first.md").write_bytes(b"# First\n")
    (root / "ignored.txt").write_bytes(b"not markdown")
    (root / "nested").mkdir()
    outside = tmp_path / "outside.md"
    outside.write_bytes(b"outside")
    (root / "linked.md").symlink_to(outside)
    registry = FileRootRegistry(
        {FileRootRef("handbook-root"): root},
        limits=FileReadLimits(max_file_bytes=1_024),
    )
    provider_proofs, _ = _proofs()
    provider = FileChangeProvider(registry, proofs=provider_proofs)

    outcome = provider.read_changes(_source(), InitialScan(), ChangeLimit(1))

    assert type(outcome) is ProviderOk
    page = outcome.value
    assert len(page.changes) == 1
    change = page.changes[0]
    assert change.kind is FileChangeKind.UPSERT
    assert change.path.value == "a-first.md"
    assert change.content_length == 8
    assert change.content_sha256 == (
        "9deb94158e91742ee59a098729128779da85eef76b28890b6c0cb64401537a29"
    )
    assert page.next_cursor is not None
    assert type(page.next_cursor) is PendingChangeCursor
    assert page.complete is False
    rendered = repr(page)
    for forbidden in (str(tmp_path), "# First", "outside", "not markdown"):
        assert forbidden not in rendered


def test_scan_ignores_non_utf8_names_outside_the_file_import_domain(
    tmp_path: Path,
) -> None:
    root = tmp_path / "registered-root"
    root.mkdir()
    (root / "valid.md").write_bytes(b"valid")
    registry = FileRootRegistry(
        {FileRootRef("handbook-root"): root},
        limits=FileReadLimits(max_file_bytes=1_024),
    )
    provider_proofs, _ = _proofs()
    provider = FileChangeProvider(registry, proofs=provider_proofs)

    with patch(
        "adapters.file_source.os.listdir",
        return_value=["valid.md", os.fsdecode(b"\xff.txt")],
    ):
        outcome = provider.read_changes(_source(), InitialScan(), ChangeLimit(1))

    assert type(outcome) is ProviderOk
    assert [change.path.value for change in outcome.value.changes] == ["valid.md"]


def test_opaque_cursor_replays_one_snapshot_and_rejects_every_changed_binding(
    tmp_path: Path,
) -> None:
    root = tmp_path / "registered-root"
    root.mkdir()
    (root / "a.md").write_bytes(b"A")
    (root / "b.md").write_bytes(b"B")
    registry = FileRootRegistry(
        {FileRootRef("handbook-root"): root},
        limits=FileReadLimits(max_file_bytes=1_024),
    )
    provider_proofs, control_proofs = _proofs()
    provider = FileChangeProvider(registry, proofs=provider_proofs)
    source = _source()

    first = provider.read_changes(source, InitialScan(), ChangeLimit(1))
    assert type(first) is ProviderOk
    pending = first.value.next_cursor
    assert type(pending) is PendingChangeCursor
    assert type(
        provider.read_changes(source, pending, ChangeLimit(1))  # type: ignore[arg-type]
    ) is ProviderGenericDenied
    payload = _accepted_cursor_payload(
        organization_id=ORGANIZATION_ID,
        source_ref=SourceRef(SOURCE_ID),
        source_version_ref=SOURCE_VERSION_ID,
        scan_ref=first.value.scan_ref,
        scan_epoch=first.value.scan_epoch,
        page_ref="c" * 64,
        checkpoint_ref="facp_" + "b" * 64,
        sequence=1,
        pending_cursor=pending,
    )
    cursor = ChangeCursor(
        f"{encode_base64url(payload)}."
        f"{encode_base64url(CHECKPOINT_KEY.sign(payload))}"
    )
    assert type(cursor) is ChangeCursor
    source = replace(
        source,
        scan_head=FileChangeScanHead(
            source_version_ref=SOURCE_VERSION_ID,
            scan_ref=first.value.scan_ref,
            scan_epoch=first.value.scan_epoch,
            page_limit=1,
            page_ref="c" * 64,
            checkpoint_ref="facp_" + "b" * 64,
            sequence=1,
            complete=False,
        ),
    )
    replay = provider.read_changes(source, cursor, ChangeLimit(1))
    assert type(replay) is ProviderOk
    assert [change.path.value for change in replay.value.changes] == ["b.md"]
    assert replay.value.complete is True
    assert provider.read_changes(source, cursor, ChangeLimit(1)) == replay

    assert control_proofs.verify_page(
        replace(
            first.value,
            changes=(
                replace(first.value.changes[0], content_sha256="f" * 64),
            ),
        )
    ) is None

    prefix, signature = cursor.value.split(".")
    replacement = "A" if signature[0] != "A" else "B"
    tampered = ChangeCursor(f"{prefix}.{replacement}{signature[1:]}")
    assert type(
        provider.read_changes(source, tampered, ChangeLimit(1))
    ) is ProviderInvalidCheckpoint

    (root / "b.md").write_bytes(b"changed")
    assert type(
        provider.read_changes(source, cursor, ChangeLimit(1))
    ) is ProviderInvalidCheckpoint

    other_manifest = SourceManifest.registered_file(
        source_ref=source.source_version.source_ref,
        version_ref=UUID("fd0483cd-1f04-4694-a13b-5231ef0614c3"),
        display_name="Handbook",
        root_ref=source.source_version.root_ref,
        created_at=NOW,
        capabilities=FILE_CHANGE_CAPABILITY_MANIFEST,
    )
    other_source = FileChangeSource(
        organization_id=ORGANIZATION_ID,
        source_version=other_manifest.active_version,
        scan_head=source.scan_head,
    )
    assert type(
        provider.read_changes(other_source, cursor, ChangeLimit(1))
    ) is ProviderInvalidCheckpoint

    cross_source_manifest = SourceManifest.registered_file(
        source_ref=SourceRef(UUID("39c5b792-a10c-4266-9e12-1bb7fd1d5311")),
        version_ref=SOURCE_VERSION_ID,
        display_name="Another handbook",
        root_ref=source.source_version.root_ref,
        created_at=NOW,
        capabilities=FILE_CHANGE_CAPABILITY_MANIFEST,
    )
    cross_source = FileChangeSource(
        organization_id=ORGANIZATION_ID,
        source_version=cross_source_manifest.active_version,
    )
    assert type(
        provider.read_changes(cross_source, cursor, ChangeLimit(1))
    ) is ProviderInvalidCheckpoint

    malformed = ChangeCursor("A" * 128 + "." + "B" * 86)
    assert type(
        provider.read_changes(source, malformed, ChangeLimit(1))
    ) is ProviderInvalidCheckpoint

    accepted_document = json.loads(payload)
    for field_name, malformed_value in (
        ("scanEpoch", None),
        ("organizationId", 42),
        ("sourceId", []),
        ("sourceVersionId", None),
    ):
        malformed_document = {**accepted_document, field_name: malformed_value}
        malformed_payload = rfc8785.dumps(malformed_document)
        malformed_cursor = ChangeCursor(
            f"{encode_base64url(malformed_payload)}."
            f"{encode_base64url(CHECKPOINT_KEY.sign(malformed_payload))}"
        )
        assert type(
            provider.read_changes(source, malformed_cursor, ChangeLimit(1))
        ) is ProviderInvalidCheckpoint

    beyond_payload = _accepted_cursor_payload(
        organization_id=ORGANIZATION_ID,
        source_ref=SourceRef(SOURCE_ID),
        source_version_ref=SOURCE_VERSION_ID,
        scan_ref=first.value.scan_ref,
        scan_epoch=first.value.scan_epoch,
        page_ref="d" * 64,
        checkpoint_ref="facp_" + "e" * 64,
        sequence=2,
        pending_cursor=provider._encode_cursor(
            source=source,
            scan_ref=first.value.scan_ref,
            scan_epoch=first.value.scan_epoch,
            offset=2,
            limit=ChangeLimit(1),
        ),
    )
    beyond = ChangeCursor(
        f"{encode_base64url(beyond_payload)}."
        f"{encode_base64url(CHECKPOINT_KEY.sign(beyond_payload))}"
    )
    assert type(
        provider.read_changes(source, beyond, ChangeLimit(1))
    ) is ProviderInvalidCheckpoint


def test_restart_recovers_scan_epoch_from_durable_head_and_rejects_old_initial(
    tmp_path: Path,
) -> None:
    root = tmp_path / "registered-root"
    root.mkdir()
    (root / "a.md").write_bytes(b"A")
    (root / "b.md").write_bytes(b"B")
    registry = FileRootRegistry(
        {FileRootRef("handbook-root"): root},
        limits=FileReadLimits(max_file_bytes=1_024),
    )
    provider_proofs, _ = _proofs()
    first_provider = FileChangeProvider(registry, proofs=provider_proofs)
    initial = first_provider.read_changes(
        _source(), InitialScan(), ChangeLimit(1)
    )
    assert type(initial) is ProviderOk
    assert initial.value.next_cursor is not None
    payload = _accepted_cursor_payload(
        organization_id=ORGANIZATION_ID,
        source_ref=SourceRef(SOURCE_ID),
        source_version_ref=SOURCE_VERSION_ID,
        scan_ref=initial.value.scan_ref,
        scan_epoch=initial.value.scan_epoch,
        page_ref="c" * 64,
        checkpoint_ref="facp_" + "d" * 64,
        sequence=7,
        pending_cursor=initial.value.next_cursor,
    )
    cursor = ChangeCursor(
        f"{encode_base64url(payload)}."
        f"{encode_base64url(CHECKPOINT_KEY.sign(payload))}"
    )
    source = _source(
        scan_head=FileChangeScanHead(
            source_version_ref=SOURCE_VERSION_ID,
            scan_ref=initial.value.scan_ref,
            scan_epoch=initial.value.scan_epoch,
            page_limit=1,
            page_ref="c" * 64,
            checkpoint_ref="facp_" + "d" * 64,
            sequence=7,
            complete=False,
        )
    )

    restarted = FileChangeProvider(registry, proofs=provider_proofs)
    resumed = restarted.read_changes(source, cursor, ChangeLimit(1))
    assert type(resumed) is ProviderOk
    assert resumed.value.scan_epoch == initial.value.scan_epoch

    unchanged = restarted.read_changes(source, InitialScan(), ChangeLimit(1))
    assert type(unchanged) is ProviderOk
    assert unchanged.value.scan_epoch == initial.value.scan_epoch

    changed_limit = restarted.read_changes(
        source, InitialScan(), ChangeLimit(2)
    )
    assert type(changed_limit) is ProviderOk
    assert changed_limit.value == initial.value

    (root / "a.md").write_bytes(b"A2")
    changed = restarted.read_changes(source, InitialScan(), ChangeLimit(1))
    assert type(changed) is ProviderOk
    assert changed.value.scan_epoch != initial.value.scan_epoch
    assert changed.value.superseded_scan_epoch == initial.value.scan_epoch


def test_closed_provider_outcomes_never_turn_failure_into_empty_success(
    tmp_path: Path,
) -> None:
    root = tmp_path / "registered-root"
    root.mkdir()
    registry = FileRootRegistry(
        {FileRootRef("handbook-root"): root},
        limits=FileReadLimits(max_file_bytes=1_024),
    )
    provider_proofs, _ = _proofs()
    provider = FileChangeProvider(registry, proofs=provider_proofs)
    active = _source()
    described = provider.describe_capabilities(active)
    assert type(described) is ProviderOk
    assert described.value is FILE_CHANGE_CAPABILITY_MANIFEST
    assert described.value.document() == {
        "aclEvidenceMode": "mirrored",
        "authorizeAndProject": "unavailable",
        "batchLimits": "available",
        "checkpoint": "available",
        "checkpointSemantics": "available",
        "contentKinds": ["markdown"],
        "consistencyGuarantees": "unavailable",
        "cursorSemantics": "available",
        "declarationVersion": "file-capabilities-v3",
        "deletion": "unavailable",
        "describeCapabilities": "available",
        "discover": "unavailable",
        "fileSourceAccess": "available",
        "freshness": "unavailable",
        "ingestionJobs": "available",
        "projectionFields": [],
        "readChanges": "available",
        "resourceKinds": ["markdown_document"],
        "sourceMode": "materialized",
    }

    legacy_manifest = SourceManifest.registered_file(
        source_ref=active.source_version.source_ref,
        version_ref=active.source_version.version_ref,
        display_name="Handbook",
        root_ref=active.source_version.root_ref,
        created_at=NOW,
        capabilities=FILE_IMPORT_CAPABILITY_MANIFEST,
    )
    legacy = FileChangeSource(
        organization_id=active.organization_id,
        source_version=legacy_manifest.active_version,
    )
    unsupported = provider.read_changes(legacy, InitialScan(), ChangeLimit(1))
    assert unsupported == ProviderUnsupported("readChanges")

    missing_manifest = SourceManifest.registered_file(
        source_ref=SourceRef(UUID("45408edf-bf55-459e-81c8-567523de4ace")),
        version_ref=UUID("9f45d531-6cc4-4556-972e-7cadb1e89d5e"),
        display_name="Missing",
        root_ref=FileRootRef("missing-root"),
        created_at=NOW,
        capabilities=FILE_CHANGE_CAPABILITY_MANIFEST,
    )
    missing = FileChangeSource(
        organization_id=active.organization_id,
        source_version=missing_manifest.active_version,
    )
    assert type(
        provider.read_changes(missing, InitialScan(), ChangeLimit(1))
    ) is ProviderGenericDenied

    with patch("adapters.file_source.os.listdir", side_effect=OSError):
        unavailable = provider.read_changes(active, InitialScan(), ChangeLimit(1))
    assert type(unavailable) is ProviderRetryableUnavailable
    assert unavailable.retry_after.total_seconds() == 1


def test_scan_revalidates_earlier_files_after_reading_the_whole_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "registered-root"
    root.mkdir()
    first_path = root / "a.md"
    first_path.write_bytes(b"A")
    (root / "b.md").write_bytes(b"B")
    registry = FileRootRegistry(
        {FileRootRef("handbook-root"): root},
        limits=FileReadLimits(max_file_bytes=1_024),
    )
    provider_proofs, _ = _proofs()
    provider = FileChangeProvider(registry, proofs=provider_proofs)
    original_stat = os.stat
    changed = False

    def change_earlier_file(
        path: str | bytes | int,
        *args: object,
        **kwargs: object,
    ) -> os.stat_result:
        nonlocal changed
        if path == "b.md" and not changed:
            first_path.write_bytes(b"A2")
            changed = True
        return original_stat(path, *args, **kwargs)  # type: ignore[arg-type]

    with patch("adapters.file_source.os.stat", side_effect=change_earlier_file):
        outcome = provider.read_changes(
            _source(), InitialScan(), ChangeLimit(2)
        )

    assert changed is True
    assert type(outcome) is ProviderRetryableUnavailable


def test_scan_closes_when_file_disappears_before_post_read_stat(
    tmp_path: Path,
) -> None:
    root = tmp_path / "registered-root"
    root.mkdir()
    file_path = root / "a.md"
    file_path.write_bytes(b"A")
    registry = FileRootRegistry(
        {FileRootRef("handbook-root"): root},
        limits=FileReadLimits(max_file_bytes=1_024),
    )
    provider_proofs, _ = _proofs()
    provider = FileChangeProvider(registry, proofs=provider_proofs)
    original_read = FileRootRegistry._read_regular

    def remove_after_read(
        self: FileRootRegistry,
        root_ref: FileRootRef,
        path: object,
    ) -> tuple[bytes, os.stat_result]:
        payload, metadata = original_read(
            self, root_ref, path  # type: ignore[arg-type]
        )
        file_path.unlink()
        return payload, metadata

    with patch.object(FileRootRegistry, "_read_regular", remove_after_read):
        outcome = provider.read_changes(
            _source(), InitialScan(), ChangeLimit(1)
        )

    assert type(outcome) is ProviderRetryableUnavailable


def test_scan_revalidates_markdown_names_initially_skipped_as_non_regular(
    tmp_path: Path,
) -> None:
    root = tmp_path / "registered-root"
    root.mkdir()
    skipped_path = root / "a.md"
    skipped_path.mkdir()
    (root / "b.md").write_bytes(b"B")
    registry = FileRootRegistry(
        {FileRootRef("handbook-root"): root},
        limits=FileReadLimits(max_file_bytes=1_024),
    )
    provider_proofs, _ = _proofs()
    provider = FileChangeProvider(registry, proofs=provider_proofs)
    original_stat = os.stat
    changed = False

    def replace_skipped_entry(
        path: str | bytes | int,
        *args: object,
        **kwargs: object,
    ) -> os.stat_result:
        nonlocal changed
        if path == "b.md" and not changed:
            skipped_path.rmdir()
            skipped_path.write_bytes(b"A")
            changed = True
        return original_stat(path, *args, **kwargs)  # type: ignore[arg-type]

    with patch("adapters.file_source.os.stat", side_effect=replace_skipped_entry):
        outcome = provider.read_changes(
            _source(), InitialScan(), ChangeLimit(2)
        )

    assert changed is True
    assert type(outcome) is ProviderRetryableUnavailable


def test_scan_detects_same_size_rewrite_even_when_mtime_is_restored(
    tmp_path: Path,
) -> None:
    root = tmp_path / "registered-root"
    root.mkdir()
    first_path = root / "a.md"
    first_path.write_bytes(b"A1")
    (root / "b.md").write_bytes(b"B1")
    initial = first_path.stat()
    registry = FileRootRegistry(
        {FileRootRef("handbook-root"): root},
        limits=FileReadLimits(max_file_bytes=1_024),
    )
    provider_proofs, _ = _proofs()
    provider = FileChangeProvider(registry, proofs=provider_proofs)
    original_stat = os.stat
    changed = False

    def rewrite_and_restore_mtime(
        path: str | bytes | int,
        *args: object,
        **kwargs: object,
    ) -> os.stat_result:
        nonlocal changed
        if path == "b.md" and not changed:
            first_path.write_bytes(b"A2")
            os.utime(
                first_path,
                ns=(initial.st_atime_ns, initial.st_mtime_ns),
            )
            changed = True
        return original_stat(path, *args, **kwargs)  # type: ignore[arg-type]

    with patch("adapters.file_source.os.stat", side_effect=rewrite_and_restore_mtime):
        outcome = provider.read_changes(
            _source(), InitialScan(), ChangeLimit(2)
        )

    final = first_path.stat()
    assert changed is True
    assert final.st_size == initial.st_size
    assert final.st_mtime_ns == initial.st_mtime_ns
    assert final.st_ctime_ns != initial.st_ctime_ns
    assert type(outcome) is ProviderRetryableUnavailable
