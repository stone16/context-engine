from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from adapters.file_source import FileChangeProvider
from applications.file_root_configuration import (
    WORKER_FILE_CURATED_SUBTREES_ENV,
    WORKER_MAX_FILE_CHANGE_BASELINE_SIZE_ENV,
    file_curated_subtrees,
    file_read_limits,
    file_root_bindings,
    file_roots,
)
from engine.control import (
    DEFAULT_FILE_CHANGE_BASELINE_SIZE,
    FILE_DELETE_OBSERVATION_CAPABILITY_MANIFEST,
    MAX_CONFIGURED_FILE_CHANGE_BASELINE_SIZE,
    ChangeLimit,
    FileChangeProviderProofs,
    FileChangeSource,
    FileRootRef,
    InitialScan,
    ProviderOk,
    SourceManifest,
    SourceRef,
)


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "ten-thousand", "", " 10000"])
def test_scan_bound_rejects_invalid_values_at_configuration_time(value: str) -> None:
    with pytest.raises(ValueError, match="configuration is not available"):
        file_read_limits({WORKER_MAX_FILE_CHANGE_BASELINE_SIZE_ENV: value})


def test_scan_bound_defaults_to_adr_0065_and_accepts_explicit_ceiling() -> None:
    assert file_read_limits({}).max_baseline_entries == (
        DEFAULT_FILE_CHANGE_BASELINE_SIZE
    )
    assert file_read_limits(
        {
            WORKER_MAX_FILE_CHANGE_BASELINE_SIZE_ENV: str(
                MAX_CONFIGURED_FILE_CHANGE_BASELINE_SIZE
            )
        }
    ).max_baseline_entries == MAX_CONFIGURED_FILE_CHANGE_BASELINE_SIZE
    with pytest.raises(ValueError, match="configuration is not available"):
        file_read_limits(
            {
                WORKER_MAX_FILE_CHANGE_BASELINE_SIZE_ENV: str(
                    MAX_CONFIGURED_FILE_CHANGE_BASELINE_SIZE + 1
                )
            }
        )


def test_curated_subtree_is_an_explicit_anchored_root_selection(tmp_path: Path) -> None:
    configured = tmp_path / "configured"
    curated = configured / "curated" / "notes"
    curated.mkdir(parents=True)
    environment = {
        "CONTEXT_ENGINE_WORKER_FILE_ROOTS_JSON": json.dumps(
            {"maintainer-notes": str(configured)}
        ),
        WORKER_FILE_CURATED_SUBTREES_ENV: json.dumps(
            {"maintainer-notes": "curated/notes"}
        ),
    }

    assert file_root_bindings(environment) == {
        FileRootRef("maintainer-notes"): configured
    }
    assert file_curated_subtrees(environment) == {
        FileRootRef("maintainer-notes"): "curated/notes"
    }


def test_curated_subtree_scan_preserves_registered_root_relative_paths(
    tmp_path: Path,
) -> None:
    configured = tmp_path / "configured"
    curated = configured / "curated" / "notes"
    curated.mkdir(parents=True)
    (curated / "inside.md").write_bytes(b"# Inside\n")
    (configured / "outside.md").write_bytes(b"# Outside\n")
    environment = {
        "CONTEXT_ENGINE_WORKER_FILE_ROOTS_JSON": json.dumps(
            {"maintainer-notes": str(configured)}
        ),
        WORKER_FILE_CURATED_SUBTREES_ENV: json.dumps(
            {"maintainer-notes": "curated/notes"}
        ),
    }
    roots = file_roots(environment)
    try:
        key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex("11" * 32))
        checkpoint_key = Ed25519PrivateKey.from_private_bytes(
            bytes.fromhex("22" * 32)
        )
        manifest = SourceManifest.registered_file(
            source_ref=SourceRef(UUID("efea559b-2aa7-4714-ac07-41e91b2a6f67")),
            version_ref=UUID("86a1088b-70f8-4423-8e43-f4baa4277948"),
            display_name="Synthetic curated source",
            root_ref=FileRootRef("maintainer-notes"),
            created_at=datetime(2026, 7, 30, tzinfo=UTC),
            capabilities=FILE_DELETE_OBSERVATION_CAPABILITY_MANIFEST,
        )
        provider = FileChangeProvider(
            roots,
            proofs=FileChangeProviderProofs(
                provider_signing_key=key,
                checkpoint_verification_key=checkpoint_key.public_key(),
            ),
        )
        outcome = provider.read_changes(
            FileChangeSource(
                UUID("ac6bbbf4-dc09-4197-862a-3c84d711a3cc"),
                manifest.active_version,
            ),
            InitialScan(),
            ChangeLimit(10),
        )
    finally:
        roots.close()

    assert type(outcome) is ProviderOk
    assert [change.path.value for change in outcome.value.changes] == [
        "curated/notes/inside.md"
    ]


@pytest.mark.parametrize(
    "selection",
    ["/absolute", "../escape", "curated/../escape", "curated\\notes", "", None],
)
def test_curated_subtree_rejects_noncanonical_selection(
    tmp_path: Path,
    selection: object,
) -> None:
    configured = tmp_path / "configured"
    configured.mkdir()
    environment = {
        "CONTEXT_ENGINE_WORKER_FILE_ROOTS_JSON": json.dumps(
            {"maintainer-notes": str(configured)}
        ),
        WORKER_FILE_CURATED_SUBTREES_ENV: json.dumps(
            {"maintainer-notes": selection}
        ),
    }

    with pytest.raises(ValueError, match="configuration is not available"):
        file_curated_subtrees(environment)
