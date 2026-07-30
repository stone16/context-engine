from __future__ import annotations

import ast
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from adapters.connectors.file import FileConnectorProcessAdapter
from applications.connector_runner import ConnectorRunnerRequest
from engine.control import FileRootRef
from engine.supply import ConnectorCheckpointBinding, WorkerLeaseToken

REPOSITORY_ROOT = Path(__file__).parents[2]
RUNNER_PATHS = (
    REPOSITORY_ROOT / "applications/connector_runner.py",
    REPOSITORY_ROOT / "adapters/connectors/file.py",
)


def test_runner_request_is_one_explicit_job_without_ambient_connector_credentials() -> (
    None
):
    assert [item.name for item in fields(ConnectorRunnerRequest)] == [
        "organization_id",
        "source_version_id",
        "worker_job_id",
        "service_principal_id",
        "worker_lease",
        "policy_epoch",
        "idempotency_key",
        "service_actor_expires_at",
        "root_ref",
        "root_path",
        "opaque_checkpoint",
    ]

    rendered = "\n".join(path.read_text(encoding="utf-8") for path in RUNNER_PATHS)
    assert "CONTEXT_ENGINE_CONNECTOR_CREDENTIAL" not in rendered
    assert "CONTEXT_ENGINE_WORKER_FILE_ROOT_PATH" not in rendered
    assert "credential_cache" not in rendered


def test_runner_has_no_independent_database_index_or_write_surface() -> None:
    adapter_tree = ast.parse(
        RUNNER_PATHS[1].read_bytes(),
        filename=str(RUNNER_PATHS[1]),
    )
    imports: set[str] = set()
    forbidden_calls: set[str] = set()
    for node in ast.walk(adapter_tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            assert node.module is not None
            imports.add(node.module.partition(".")[0])
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr
            in {
                "mkdir",
                "open",
                "write_bytes",
                "write_text",
                "unlink",
            }
        ):
            forbidden_calls.add(node.func.attr)

    assert imports.isdisjoint({"alembic", "celery", "psycopg", "redis", "sqlalchemy"})
    assert not forbidden_calls
    assert not (REPOSITORY_ROOT / "contract_kit").exists()


def test_malformed_or_missing_runner_job_refuses_before_root_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accessed = False

    def forbidden_path(*_args: object, **_kwargs: object) -> None:
        nonlocal accessed
        accessed = True
        raise AssertionError("invalid runner request reached its root")

    monkeypatch.setattr(Path, "is_dir", forbidden_path)
    with pytest.raises(ValueError):
        ConnectorRunnerRequest.from_json(b"{}")
    assert not accessed


def test_process_adapter_rejects_malformed_child_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MalformedResult:
        returncode = 0
        stdout = b"not-a-page"
        stderr = b""

    monkeypatch.setattr(
        "adapters.connectors.file.subprocess.run",
        lambda *_args, **_kwargs: MalformedResult(),
    )
    adapter = FileConnectorProcessAdapter(
        FileRootRef("synthetic-root"),
        Path("/synthetic/root"),
        policy_epoch=1,
        worker_lease=WorkerLeaseToken("synthetic.opaque.lease"),
        service_principal_id=UUID("00000000-0000-4000-8000-000000000001"),
        idempotency_key="0" * 64,
        service_actor_expires_at=datetime(2026, 7, 30, 9, tzinfo=UTC),
    )
    adapter.load_checkpoint(None)

    with pytest.raises(RuntimeError, match="output is unavailable"):
        adapter.load(ConnectorCheckpointBinding(uuid4(), uuid4(), uuid4()))
