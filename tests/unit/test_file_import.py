from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from adapters.file_source import FileReadLimits, FileRootRegistry
from engine.control import (
    FileImportAudience,
    FileImportPath,
    FileRootRef,
    PrepareFileImport,
    SourceRef,
)

ORGANIZATION_ID = UUID("62f7e3b4-e7cf-44c5-afaf-2f58032801e0")
MEMBERSHIP_ID = UUID("48e1ab62-8f7f-44c2-9d38-4a918d315f07")
SOURCE_ID = UUID("1a4743a4-747e-423f-8dd9-7cccfb5c1d3c")
NOW = datetime(2026, 7, 22, 22, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "value",
    (
        "",
        ".",
        "..",
        "../handbook.md",
        "/tmp/handbook.md",
        "folder/handbook.md",
        "folder\\handbook.md",
        "handbook.txt",
        " handbook.md",
        "handbook.md ",
    ),
)
def test_manual_import_path_is_one_bounded_markdown_filename(value: str) -> None:
    with pytest.raises(ValueError, match="Markdown filename"):
        FileImportPath(value)


def test_manual_import_command_contains_no_host_path_or_credentials() -> None:
    command = PrepareFileImport(
        source_ref=SourceRef(SOURCE_ID),
        path=FileImportPath("handbook.md"),
        audience=FileImportAudience(
            principal_ref="principal:handbook-reader",
            membership_id=MEMBERSHIP_ID,
            membership_version=3,
        ),
        idempotency_key="handbook-import-v1",
    )

    assert [field.name for field in fields(command)] == [
        "source_ref",
        "path",
        "audience",
        "idempotency_key",
    ]
    rendered = repr(command).casefold()
    for forbidden in ("credential", "password", "token", "/tmp", "file://"):
        assert forbidden not in rendered


def test_root_registry_resolves_only_a_registered_logical_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "registered-root"
    root.mkdir()
    registry = FileRootRegistry(
        {FileRootRef("handbook-root"): root},
        limits=FileReadLimits(max_file_bytes=1024),
    )

    target = registry.resolve(
        FileRootRef("handbook-root"),
        FileImportPath("handbook.md"),
    )

    assert target == root / "handbook.md"
    with pytest.raises(LookupError, match="File root is not configured"):
        registry.resolve(
            FileRootRef("unknown-root"),
            FileImportPath("handbook.md"),
        )


def test_root_registry_reads_one_regular_file_and_rejects_symlinks(
    tmp_path: Path,
) -> None:
    root = tmp_path / "registered-root"
    root.mkdir()
    expected = b"# Handbook\n\nContextEngine delivers context.\n"
    (root / "handbook.md").write_bytes(expected)
    outside = tmp_path / "outside.md"
    outside.write_bytes(b"outside")
    (root / "linked.md").symlink_to(outside)
    registry = FileRootRegistry(
        {FileRootRef("handbook-root"): root},
        limits=FileReadLimits(max_file_bytes=1024),
    )

    assert registry.read(
        FileRootRef("handbook-root"), FileImportPath("handbook.md")
    ) == expected
    with pytest.raises(LookupError, match="regular configured-root file"):
        registry.read(
            FileRootRef("handbook-root"), FileImportPath("linked.md")
        )


def test_root_registry_anchors_directory_before_a_path_swap(tmp_path: Path) -> None:
    root = tmp_path / "registered-root"
    outside = tmp_path / "outside-root"
    root.mkdir()
    outside.mkdir()
    (root / "handbook.md").write_bytes(b"inside")
    (outside / "handbook.md").write_bytes(b"outside")
    registry = FileRootRegistry(
        {FileRootRef("handbook-root"): root},
        limits=FileReadLimits(max_file_bytes=1024),
    )
    anchored = tmp_path / "anchored-root"
    root.rename(anchored)
    root.symlink_to(outside, target_is_directory=True)

    assert registry.read(
        FileRootRef("handbook-root"), FileImportPath("handbook.md")
    ) == b"inside"


def test_root_registry_rejects_symlinked_roots_and_oversized_files(
    tmp_path: Path,
) -> None:
    root = tmp_path / "registered-root"
    root.mkdir()
    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="non-symlink directory"):
        FileRootRegistry(
            {FileRootRef("handbook-root"): linked_root},
            limits=FileReadLimits(max_file_bytes=4),
        )

    (root / "handbook.md").write_bytes(b"12345")
    registry = FileRootRegistry(
        {FileRootRef("handbook-root"): root},
        limits=FileReadLimits(max_file_bytes=4),
    )
    with pytest.raises(LookupError, match="regular configured-root file"):
        registry.read(
            FileRootRef("handbook-root"), FileImportPath("handbook.md")
        )
