from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from adapters.connectors.file import FileConnectorProcessAdapter
from engine.control import FileRootRef
from engine.supply import ConnectorCheckpointBinding, WorkerLeaseToken


def test_process_adapter_scans_in_independent_runner(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "alpha.md").write_bytes(b"# Alpha\n")
    adapter = FileConnectorProcessAdapter(
        FileRootRef("synthetic-root"),
        root,
        policy_epoch=7,
        worker_lease=WorkerLeaseToken("synthetic.opaque.lease"),
        service_principal_id=UUID("00000000-0000-4000-8000-000000000001"),
        idempotency_key="0" * 64,
        service_actor_expires_at=datetime(2026, 7, 30, 9, tzinfo=UTC),
    )
    binding = ConnectorCheckpointBinding(uuid4(), uuid4(), uuid4())
    adapter.load_checkpoint(None)

    first = adapter.load(binding)
    adapter.load_checkpoint(first.checkpoint_proposal)
    second = adapter.poll(binding)

    assert len(first.documents) == 1
    assert first.terminal is False
    assert second.documents == ()
    assert second.deleted_document_refs == ()
    assert second.terminal is True
