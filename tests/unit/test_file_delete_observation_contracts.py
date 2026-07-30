from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from adapters.file_source import FileChangeProvider, FileReadLimits, FileRootRegistry
from engine.control import (
    FILE_CHANGE_CAPABILITY_MANIFEST,
    FILE_DELETE_OBSERVATION_CAPABILITY_MANIFEST,
    MAX_FILE_CHANGE_BASELINE_SIZE,
    CapabilityStatus,
    ChangeLimit,
    FileCapabilityManifest,
    FileChangeBaseline,
    FileChangeBaselineEntry,
    FileChangeBaselineRef,
    FileChangeKind,
    FileChangeProviderProofs,
    FileChangeScanHead,
    FileChangeSource,
    FileImportPath,
    FileRootRef,
    InitialScan,
    ProviderOk,
    ProviderScanBoundExceeded,
    SourceChange,
    SourceManifest,
    SourceRef,
)

ORGANIZATION_ID = UUID("cbbce347-023b-4190-bbda-97c958f8a6da")
SOURCE_ID = UUID("c5f5b7ec-70b5-4f9a-ac5e-a4694570407b")
SOURCE_VERSION_ID = UUID("be3dcdbf-dcff-4bb8-82c3-25a1bfae1c88")
SCAN_EPOCH = UUID("54bf4f86-a7b8-4829-babe-583f4672cb8c")
NOW = datetime(2026, 7, 25, 9, 0, tzinfo=UTC)
PROVIDER_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
CHECKPOINT_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32, 64)))


def _baseline_entry(
    path: str = "handbook.md",
    *,
    kind: FileChangeKind = FileChangeKind.UPSERT,
) -> FileChangeBaselineEntry:
    return FileChangeBaselineEntry(
        kind=kind,
        path=FileImportPath(path),
        content_sha256="a" * 64,
        content_length=12,
    )


def _baseline() -> FileChangeBaseline:
    return FileChangeBaseline(
        reference=FileChangeBaselineRef(
            source_version_ref=SOURCE_VERSION_ID,
            scan_ref="b" * 64,
            scan_epoch=SCAN_EPOCH,
            page_ref="c" * 64,
            checkpoint_ref="facp_" + "d" * 64,
            sequence=7,
        ),
        entries=(_baseline_entry(),),
    )


def _manifest(*, delete_observations: bool) -> SourceManifest:
    return SourceManifest.registered_file(
        source_ref=SourceRef(SOURCE_ID),
        version_ref=SOURCE_VERSION_ID,
        display_name="Handbook",
        root_ref=FileRootRef("handbook-root"),
        created_at=NOW,
        capabilities=(
            FILE_DELETE_OBSERVATION_CAPABILITY_MANIFEST
            if delete_observations
            else FILE_CHANGE_CAPABILITY_MANIFEST
        ),
    )


def _provider(root: Path) -> FileChangeProvider:
    return FileChangeProvider(
        FileRootRegistry(
            {FileRootRef("handbook-root"): root},
            limits=FileReadLimits(max_file_bytes=1_024),
        ),
        proofs=FileChangeProviderProofs(
            provider_signing_key=PROVIDER_KEY,
            checkpoint_verification_key=CHECKPOINT_KEY.public_key(),
        ),
    )


def test_v4_declares_delete_observation_without_deletion_execution() -> None:
    document = FILE_DELETE_OBSERVATION_CAPABILITY_MANIFEST.document()

    assert document["declarationVersion"] == "file-capabilities-v4"
    assert document["deleteObservations"] == "available"
    assert document["deletion"] == "unavailable"
    assert document["discover"] == "unavailable"
    assert document["authorizeAndProject"] == "unavailable"
    assert "deleteObservations" not in FILE_CHANGE_CAPABILITY_MANIFEST.document()


@pytest.mark.parametrize(
    ("declaration_version", "file_access", "ingestion_jobs"),
    [
        (
            "file-capabilities-v1",
            CapabilityStatus.UNAVAILABLE,
            CapabilityStatus.UNAVAILABLE,
        ),
        (
            "file-capabilities-v2",
            CapabilityStatus.AVAILABLE,
            CapabilityStatus.AVAILABLE,
        ),
    ],
)
def test_pre_v4_manifests_cannot_claim_delete_observations(
    declaration_version: str,
    file_access: CapabilityStatus,
    ingestion_jobs: CapabilityStatus,
) -> None:
    with pytest.raises(ValueError, match="recognized snapshot"):
        FileCapabilityManifest(
            declaration_version=declaration_version,
            file_source_access=file_access,
            ingestion_jobs=ingestion_jobs,
            delete_observations=CapabilityStatus.AVAILABLE,
        )


def test_complete_baseline_is_bounded_canonical_and_source_version_bound() -> None:
    baseline = _baseline()
    source = FileChangeSource(
        organization_id=ORGANIZATION_ID,
        source_version=_manifest(delete_observations=True).active_version,
        complete_baseline=baseline,
    )

    assert source.complete_baseline == baseline
    assert baseline.reference.source_version_ref == SOURCE_VERSION_ID
    assert "handbook.md" not in repr(baseline)

    with pytest.raises(ValueError, match="canonical order"):
        replace(
            baseline,
            entries=(
                _baseline_entry("z.md"),
                _baseline_entry("a.md"),
            ),
        )
    with pytest.raises(ValueError, match="unique"):
        replace(
            baseline,
            entries=(
                _baseline_entry("a.md"),
                _baseline_entry("a.md", kind=FileChangeKind.DELETE),
            ),
        )
    with pytest.raises(ValueError, match="does not belong"):
        FileChangeSource(
            organization_id=ORGANIZATION_ID,
            source_version=replace(
                _manifest(delete_observations=True).active_version,
                version_ref=UUID("d5d9035a-1712-442f-a735-4ff736462691"),
            ),
            complete_baseline=baseline,
        )
    with pytest.raises(ValueError, match="not active"):
        FileChangeSource(
            organization_id=ORGANIZATION_ID,
            source_version=_manifest(delete_observations=False).active_version,
            complete_baseline=baseline,
        )
    parent = baseline.reference
    with pytest.raises(ValueError, match="one level"):
        FileChangeBaselineRef(
            source_version_ref=SOURCE_VERSION_ID,
            scan_ref="6" * 64,
            scan_epoch=UUID("823e2982-6bf2-47d0-ab73-9d54a5571e4b"),
            page_ref="7" * 64,
            checkpoint_ref="facp_" + "8" * 64,
            sequence=9,
            comparison_baseline_ref=replace(
                parent,
                sequence=8,
                comparison_baseline_ref=replace(parent, sequence=7),
            ),
        )


def test_delete_change_retains_only_prior_content_identity() -> None:
    change = SourceChange(
        organization_id=ORGANIZATION_ID,
        source_ref=SOURCE_ID,
        source_version_ref=SOURCE_VERSION_ID,
        scan_ref="e" * 64,
        kind=FileChangeKind.DELETE,
        path=FileImportPath("removed.md"),
        content_sha256="f" * 64,
        content_length=19,
    )

    assert change.kind is FileChangeKind.DELETE
    assert change.content_sha256 == "f" * 64
    assert change.content_length == 19
    for forbidden in ("removed.md", "f" * 64):
        assert forbidden not in repr(change)

    with pytest.raises(ValueError, match="kind is not active"):
        SourceChange(
            organization_id=ORGANIZATION_ID,
            source_ref=SOURCE_ID,
            source_version_ref=SOURCE_VERSION_ID,
            scan_ref="e" * 64,
            kind="delete",  # type: ignore[arg-type]
            path=FileImportPath("removed.md"),
            content_sha256="f" * 64,
            content_length=19,
        )


def test_initial_v4_scan_emits_ordered_upserts_and_prior_path_deletes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "registered-root"
    root.mkdir()
    (root / "a.md").write_bytes(b"A2")
    provider = _provider(root)
    baseline = FileChangeBaseline(
        reference=FileChangeBaselineRef(
            source_version_ref=SOURCE_VERSION_ID,
            scan_ref="1" * 64,
            scan_epoch=SCAN_EPOCH,
            page_ref="2" * 64,
            checkpoint_ref="facp_" + "3" * 64,
            sequence=9,
        ),
        entries=(
            FileChangeBaselineEntry(
                kind=FileChangeKind.UPSERT,
                path=FileImportPath("a.md"),
                content_sha256="4" * 64,
                content_length=1,
            ),
            FileChangeBaselineEntry(
                kind=FileChangeKind.UPSERT,
                path=FileImportPath("b.md"),
                content_sha256="5" * 64,
                content_length=1,
            ),
        ),
    )
    source = FileChangeSource(
        organization_id=ORGANIZATION_ID,
        source_version=_manifest(delete_observations=True).active_version,
        complete_baseline=baseline,
    )

    outcome = provider.read_changes(source, InitialScan(), ChangeLimit(2))

    assert type(outcome) is ProviderOk
    assert [
        (change.path.value, change.kind, change.content_sha256)
        for change in outcome.value.changes
    ] == [
        (
            "a.md",
            FileChangeKind.UPSERT,
            "c8361f9b468e68c86da024270e0949ce139cb704b8d7cce586681b99f3a7ea56",
        ),
        ("b.md", FileChangeKind.DELETE, "5" * 64),
    ]
    assert outcome.value.baseline_ref is not None
    assert outcome.value.capability_version == "file-capabilities-v4"
    assert outcome.value.complete is True


def test_v4_without_complete_baseline_never_invents_delete(
    tmp_path: Path,
) -> None:
    root = tmp_path / "registered-root"
    root.mkdir()
    provider = _provider(root)
    source = FileChangeSource(
        organization_id=ORGANIZATION_ID,
        source_version=_manifest(delete_observations=True).active_version,
    )

    outcome = provider.read_changes(source, InitialScan(), ChangeLimit(1))

    assert type(outcome) is ProviderOk
    assert outcome.value.changes == ()
    assert outcome.value.baseline_ref is None
    assert outcome.value.complete is True


def test_oversized_mixed_diff_is_denied_before_first_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "registered-root"
    root.mkdir()
    provider = _provider(root)
    baseline = FileChangeBaseline(
        reference=FileChangeBaselineRef(
            source_version_ref=SOURCE_VERSION_ID,
            scan_ref="1" * 64,
            scan_epoch=SCAN_EPOCH,
            page_ref="2" * 64,
            checkpoint_ref="facp_" + "3" * 64,
            sequence=9,
        ),
        entries=tuple(
            FileChangeBaselineEntry(
                kind=FileChangeKind.UPSERT,
                path=FileImportPath(f"{index:05d}.md"),
                content_sha256="4" * 64,
                content_length=1,
            )
            for index in range(MAX_FILE_CHANGE_BASELINE_SIZE)
        ),
    )
    observed = tuple(
        (
            FileImportPath(f"{index:05d}.md"),
            b"A",
        )
        for index in range(1, MAX_FILE_CHANGE_BASELINE_SIZE)
    ) + ((FileImportPath("new.md"), b"N"),)
    monkeypatch.setattr(
        FileRootRegistry,
        "_observe_markdown_files",
        lambda _registry, _root_ref: observed,
    )
    source = FileChangeSource(
        organization_id=ORGANIZATION_ID,
        source_version=_manifest(delete_observations=True).active_version,
        complete_baseline=baseline,
    )

    outcome = provider.read_changes(source, InitialScan(), ChangeLimit(1))

    assert outcome == ProviderScanBoundExceeded(
        scan_bound=MAX_FILE_CHANGE_BASELINE_SIZE
    )


def test_completed_delete_scan_replays_its_original_parent_baseline(
    tmp_path: Path,
) -> None:
    root = tmp_path / "registered-root"
    root.mkdir()
    (root / "a.md").write_bytes(b"A2")
    provider = _provider(root)
    prior = FileChangeBaseline(
        reference=FileChangeBaselineRef(
            source_version_ref=SOURCE_VERSION_ID,
            scan_ref="1" * 64,
            scan_epoch=SCAN_EPOCH,
            page_ref="2" * 64,
            checkpoint_ref="facp_" + "3" * 64,
            sequence=9,
        ),
        entries=(
            FileChangeBaselineEntry(
                kind=FileChangeKind.UPSERT,
                path=FileImportPath("a.md"),
                content_sha256="4" * 64,
                content_length=1,
            ),
            FileChangeBaselineEntry(
                kind=FileChangeKind.UPSERT,
                path=FileImportPath("b.md"),
                content_sha256="5" * 64,
                content_length=1,
            ),
        ),
    )
    source = FileChangeSource(
        organization_id=ORGANIZATION_ID,
        source_version=_manifest(delete_observations=True).active_version,
        complete_baseline=prior,
    )
    first = provider.read_changes(source, InitialScan(), ChangeLimit(2))
    assert type(first) is ProviderOk
    assert first.value.baseline_ref is not None
    completed = FileChangeBaseline(
        reference=FileChangeBaselineRef(
            source_version_ref=SOURCE_VERSION_ID,
            scan_ref=first.value.scan_ref,
            scan_epoch=first.value.scan_epoch,
            page_ref="6" * 64,
            checkpoint_ref="facp_" + "7" * 64,
            sequence=10,
            comparison_baseline_ref=first.value.baseline_ref,
        ),
        entries=tuple(
            FileChangeBaselineEntry(
                kind=change.kind,
                path=change.path,
                content_sha256=change.content_sha256,
                content_length=change.content_length,
            )
            for change in first.value.changes
        ),
    )
    replay_source = FileChangeSource(
        organization_id=ORGANIZATION_ID,
        source_version=source.source_version,
        scan_head=FileChangeScanHead(
            source_version_ref=SOURCE_VERSION_ID,
            scan_ref=completed.reference.scan_ref,
            scan_epoch=completed.reference.scan_epoch,
            page_limit=2,
            page_ref=completed.reference.page_ref,
            checkpoint_ref=completed.reference.checkpoint_ref,
            sequence=completed.reference.sequence,
            complete=True,
        ),
        complete_baseline=completed,
    )

    replay = provider.read_changes(replay_source, InitialScan(), ChangeLimit(2))

    assert type(replay) is ProviderOk
    assert replay.value.changes == first.value.changes
    assert replay.value.scan_ref == first.value.scan_ref
    assert replay.value.baseline_ref == first.value.baseline_ref


def test_unchanged_files_rebase_after_an_incomplete_superseding_scan(
    tmp_path: Path,
) -> None:
    root = tmp_path / "registered-root"
    root.mkdir()
    (root / "handbook.md").write_bytes(b"handbook-data")
    provider = _provider(root)
    baseline = replace(
        _baseline(),
        entries=(
            replace(
                _baseline_entry(),
                content_sha256=sha256(b"handbook-data").hexdigest(),
                content_length=len(b"handbook-data"),
            ),
        ),
    )
    incomplete_epoch = UUID("52f47dce-5188-4133-b604-c28cb0604c88")
    source = FileChangeSource(
        organization_id=ORGANIZATION_ID,
        source_version=_manifest(delete_observations=True).active_version,
        scan_head=FileChangeScanHead(
            source_version_ref=SOURCE_VERSION_ID,
            scan_ref="8" * 64,
            scan_epoch=incomplete_epoch,
            page_limit=1,
            page_ref="9" * 64,
            checkpoint_ref="facp_" + "0" * 64,
            sequence=8,
            complete=False,
            superseded_scan_epoch=baseline.reference.scan_epoch,
        ),
        complete_baseline=baseline,
    )

    outcome = provider.read_changes(source, InitialScan(), ChangeLimit(1))

    assert type(outcome) is ProviderOk
    assert outcome.value.baseline_ref == baseline.reference
    assert outcome.value.superseded_scan_epoch == incomplete_epoch
    assert outcome.value.scan_ref != baseline.reference.scan_ref
