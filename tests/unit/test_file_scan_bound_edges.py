from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from adapters.file_source import FileChangeProvider, FileReadLimits, FileRootRegistry
from engine.control import (
    FILE_DELETE_OBSERVATION_CAPABILITY_MANIFEST,
    ChangeLimit,
    FileChangeBaseline,
    FileChangeBaselineEntry,
    FileChangeBaselineRef,
    FileChangeKind,
    FileChangeProviderProofs,
    FileChangeSource,
    FileImportPath,
    FileRootRef,
    InitialScan,
    ProviderGenericDenied,
    ProviderOk,
    ProviderScanBoundExceeded,
    SourceManifest,
    SourceRef,
)

ORGANIZATION_ID = UUID("7fbe09ee-ef27-4d8d-af10-d676fe72c740")
SOURCE_ID = UUID("d5f943d7-cab2-4f30-a12c-75c402fb5683")
SOURCE_VERSION_ID = UUID("37b78436-46d5-4a61-a2df-130c90868c32")


def _provider(
    tmp_path: Path,
    *,
    bound: int,
) -> tuple[FileChangeProvider, FileRootRegistry]:
    root = tmp_path / "root"
    root.mkdir()
    registry = FileRootRegistry(
        {FileRootRef("synthetic-root"): root},
        limits=FileReadLimits(max_file_bytes=1_024, max_baseline_entries=bound),
    )
    provider_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex("11" * 32))
    checkpoint_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex("22" * 32))
    return (
        FileChangeProvider(
            registry,
            proofs=FileChangeProviderProofs(
                provider_signing_key=provider_key,
                checkpoint_verification_key=checkpoint_key.public_key(),
            ),
        ),
        registry,
    )


def _source() -> FileChangeSource:
    manifest = SourceManifest.registered_file(
        source_ref=SourceRef(SOURCE_ID),
        version_ref=SOURCE_VERSION_ID,
        display_name="Synthetic bound fixture",
        root_ref=FileRootRef("synthetic-root"),
        created_at=datetime(2026, 7, 30, tzinfo=UTC),
        capabilities=FILE_DELETE_OBSERVATION_CAPABILITY_MANIFEST,
    )
    return FileChangeSource(ORGANIZATION_ID, manifest.active_version)


@pytest.mark.parametrize(
    ("observed_count", "expected_type"),
    [
        (10, ProviderOk),
        (11, ProviderScanBoundExceeded),
        (101, ProviderScanBoundExceeded),
    ],
)
def test_scan_bound_has_defined_exact_and_oversized_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    observed_count: int,
    expected_type: type[object],
) -> None:
    provider, registry = _provider(tmp_path, bound=10)
    observed = tuple(
        (FileImportPath(f"synthetic-{index:05d}.md"), b"synthetic")
        for index in range(observed_count)
    )
    monkeypatch.setattr(
        FileRootRegistry,
        "_observe_markdown_files",
        lambda _registry, _root_ref: observed,
    )

    outcome = provider.read_changes(_source(), InitialScan(), ChangeLimit(10))

    assert type(outcome) is expected_type
    if type(outcome) is ProviderOk:
        assert outcome.value.complete is True
        assert len(outcome.value.changes) == 10
        assert outcome.value.scan_bound == 10
    else:
        assert outcome == ProviderScanBoundExceeded(scan_bound=10)
    registry.close()


@pytest.mark.parametrize(
    ("observed_count", "expected_type"),
    [
        (10, ProviderOk),
        (11, ProviderScanBoundExceeded),
        (101, ProviderScanBoundExceeded),
    ],
)
def test_actual_traversal_has_defined_exact_and_oversized_outcomes(
    tmp_path: Path,
    observed_count: int,
    expected_type: type[object],
) -> None:
    provider, registry = _provider(tmp_path, bound=10)
    root = registry.resolve(
        FileRootRef("synthetic-root"), FileImportPath("seed.md")
    ).parent
    for index in range(observed_count):
        (root / f"actual-{index:05d}.md").write_bytes(b"synthetic")

    outcome = provider.read_changes(_source(), InitialScan(), ChangeLimit(10))

    assert type(outcome) is expected_type
    if type(outcome) is ProviderOk:
        assert outcome.value.complete is True
        assert len(outcome.value.changes) == 10
    else:
        assert outcome == ProviderScanBoundExceeded(scan_bound=10)
    registry.close()


def test_curated_selection_refuses_incompatible_whole_root_baseline_before_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    (root / "curated").mkdir(parents=True)
    registry = FileRootRegistry(
        {FileRootRef("synthetic-root"): root},
        limits=FileReadLimits(max_file_bytes=1_024, max_baseline_entries=10),
        curated_subtrees={FileRootRef("synthetic-root"): "curated"},
    )
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex("11" * 32))
    checkpoint_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex("22" * 32))
    provider = FileChangeProvider(
        registry,
        proofs=FileChangeProviderProofs(
            provider_signing_key=key,
            checkpoint_verification_key=checkpoint_key.public_key(),
        ),
    )
    baseline = FileChangeBaseline(
        FileChangeBaselineRef(
            source_version_ref=SOURCE_VERSION_ID,
            scan_ref="a" * 64,
            scan_epoch=UUID("390adef6-f348-47c9-9807-078460819635"),
            page_ref="b" * 64,
            checkpoint_ref="facp_" + "c" * 64,
            sequence=1,
            scan_bound=10,
        ),
        (
            FileChangeBaselineEntry(
                FileChangeKind.UPSERT,
                FileImportPath("outside.md"),
                "d" * 64,
                1,
            ),
        ),
    )
    source = FileChangeSource(
        ORGANIZATION_ID,
        _source().source_version,
        complete_baseline=baseline,
    )
    traversed = False

    def observe(_registry: FileRootRegistry, _root_ref: FileRootRef) -> object:
        nonlocal traversed
        traversed = True
        return ()

    monkeypatch.setattr(FileRootRegistry, "_observe_markdown_files", observe)

    outcome = provider.read_changes(source, InitialScan(), ChangeLimit(10))

    assert type(outcome) is ProviderGenericDenied
    assert traversed is False
    registry.close()
