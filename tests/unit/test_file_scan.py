from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from adapters.file_source import FileReadLimits, FileRootRegistry
from applications.file_scan import _compilation_refused
from engine.control import FileRootRef, SourceManifest, SourceNotAvailable, SourceRef


def _manifest(root_ref: FileRootRef) -> SourceManifest:
    now = datetime(2026, 7, 27, tzinfo=UTC)
    return SourceManifest.registered_file(
        source_ref=SourceRef(uuid4()),
        version_ref=uuid4(),
        display_name="scan preflight",
        root_ref=root_ref,
        created_at=now,
    )


def test_scan_preflight_compiles_only_the_exact_accepted_file_identity(
    tmp_path: Path,
) -> None:
    root_ref = FileRootRef("scan-preflight-root")
    root = tmp_path / "root"
    root.mkdir()
    target = root / "note.md"
    accepted = b"# Accepted\n\nStable note.\n"
    target.write_bytes(accepted)

    with FileRootRegistry(
        {root_ref: root},
        limits=FileReadLimits(max_file_bytes=1_048_576),
    ) as roots:
        assert (
            _compilation_refused(
                roots,
                _manifest(root_ref),
                "note.md",
                hashlib.sha256(accepted).hexdigest(),
                len(accepted),
            )
            == 0
        )

        target.write_bytes(b"# Changed\n\nDifferent bytes.\n")
        with pytest.raises(SourceNotAvailable):
            _compilation_refused(
                roots,
                _manifest(root_ref),
                "note.md",
                hashlib.sha256(accepted).hexdigest(),
                len(accepted),
            )
