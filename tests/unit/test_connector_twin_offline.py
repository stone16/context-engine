from __future__ import annotations

import socket

import pytest

from adapters.connectors.file import FileConnectorAdapter, decode_file_checkpoint
from engine.supply import ConnectorCheckpointBinding
from tests.support.file_connector_twin import SyntheticVaultTwin


def test_synthetic_vault_twin_runs_offline_without_files_or_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("offline connector twin attempted network access")

    monkeypatch.setattr(socket, "create_connection", forbidden_network)
    twin = SyntheticVaultTwin(
        {
            "alpha.md": b"# Alpha\n",
            "nested/bravo.md": b"# Bravo\n",
        }
    )
    adapter = FileConnectorAdapter.from_twin(twin)
    binding = ConnectorCheckpointBinding(
        organization_id=twin.organization_id,
        source_version_id=twin.source_version_id,
        worker_job_id=twin.worker_job_id,
    )
    adapter.load_checkpoint(None)

    page = adapter.load(binding)

    assert len(page.documents) == 2
    assert decode_file_checkpoint(page.checkpoint_proposal).paths == (
        "alpha.md",
        "nested/bravo.md",
    )
    assert twin.filesystem_accesses == 0
    assert twin.credential_accesses == 0


def test_connector_batches_at_most_one_hundred_changes_per_page() -> None:
    twin = SyntheticVaultTwin(
        {f"note-{index:03}.md": f"# Note {index}\n".encode() for index in range(101)}
    )
    adapter = FileConnectorAdapter.from_twin(twin)
    binding = ConnectorCheckpointBinding(
        organization_id=twin.organization_id,
        source_version_id=twin.source_version_id,
        worker_job_id=twin.worker_job_id,
    )
    adapter.load_checkpoint(None)

    first = adapter.load(binding)
    adapter.load_checkpoint(first.checkpoint_proposal)
    second = adapter.poll(binding)
    adapter.load_checkpoint(second.checkpoint_proposal)
    terminal = adapter.poll(binding)

    assert len(first.documents) == 100
    assert len(second.documents) == 1
    assert terminal.documents == ()
    assert terminal.terminal is True
