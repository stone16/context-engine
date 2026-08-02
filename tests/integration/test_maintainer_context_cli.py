from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Thread
from uuid import UUID

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine
from uvicorn import Config, Server

from adapters.http.dogfood import DOGFOOD_SECRET_ENV, create_dogfood_app
from adapters.http.dogfood_client import DOGFOOD_BASE_URL_ENV
from engine.persistence import DatabaseConfiguration
from tests.integration.test_dogfood_runtime_activation import (
    QUERY,
    TARGET_TEXT,
    # The helpers below compose the real dogfood app against that module's
    # embedding twin. pytest applies an autouse fixture only to the module
    # holding it, so this module must import the twin with them.
    _compose_qwen_test_twin,  # noqa: F401
    _configuration,
    _environment,
    _publish,
)

pytestmark = pytest.mark.integration


def _run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["context-engine-context", *arguments],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        timeout=30,
    )


@contextmanager
def _served_composition(
    app: FastAPI,
    *,
    secret: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = Server(Config(app, log_level="critical", lifespan="off"))
    thread = Thread(target=server.run, kwargs={"sockets": [listener]})
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        raise RuntimeError("local CLI session server did not start")
    monkeypatch.setenv(DOGFOOD_SECRET_ENV, secret)
    monkeypatch.setenv(DOGFOOD_BASE_URL_ENV, f"http://127.0.0.1:{port}")
    try:
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        if thread.is_alive():
            raise RuntimeError("local CLI session server did not stop")


def _sanitized_human(output: str) -> str:
    redacted_fields = {
        "packageId",
        "packageDigest",
        "blockId",
        "evidenceRef",
        "sourceRef",
        "resourceRef",
        "revisionRef",
        "fragmentRef",
        "runRef",
        "decisionRef",
        "policySnapshotRef",
        "sourceAclEvidence",
        "citationOpenRef",
    }
    lines: list[str] = []
    for line in output.splitlines():
        indentation = line[: len(line) - len(line.lstrip())]
        field_name, separator, _ = line.strip().partition(": ")
        if separator and field_name in redacted_fields:
            lines.append(f"{indentation}{field_name}: <{field_name}>")
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def test_installed_cli_queries_inspects_and_refuses_real_composition(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    runtime_configuration: DatabaseConfiguration,
    control_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario, user_id, _ = _publish(
        request,
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        guarded_worker_engine,
    )
    assert isinstance(user_id, UUID)
    configuration = _configuration(scenario, user_id)
    app = create_dogfood_app(
        configuration,
        _environment(
            configuration,
            runtime_configuration,
            control_configuration,
        ),
        host="127.0.0.1",
    )
    with _served_composition(
        app,
        secret=configuration.secret,
        monkeypatch=monkeypatch,
    ):
        machine = _run_cli(
            "query",
            QUERY,
            "--format",
            "json",
        )
        assert machine.returncode == 0, machine.stderr
        envelope = json.loads(machine.stdout)
        assert isinstance(envelope, dict)
        package = envelope["package"]
        assert isinstance(package, dict)
        capture = tmp_path / "untrusted-package.json"
        capture.write_text(machine.stdout, encoding="utf-8")
        captured_envelope = json.loads(capture.read_text(encoding="utf-8"))
        assert isinstance(captured_envelope, dict)
        inspected = _run_cli("inspect", str(capture))
        invalid = json.loads(machine.stdout)
        assert isinstance(invalid, dict)
        invalid_package = invalid["package"]
        assert isinstance(invalid_package, dict)
        invalid_package["packageDigest"] = "0" * 64
        invalid_capture = tmp_path / "tampered-untrusted-package.json"
        invalid_capture.write_text(json.dumps(invalid), encoding="utf-8")
        refused = _run_cli("inspect", str(invalid_capture))

    assert captured_envelope == envelope
    grant = envelope["egressGrant"]
    assert grant is None or (
        isinstance(grant, dict)
        and grant["kind"] in {"model", "channel"}
        and grant["value"] == "REDACTED-EGRESS-GRANT"
    )
    persisted = capture.read_text(encoding="utf-8")
    for redeemable in ("egrm_", "egrc_"):
        assert redeemable not in machine.stdout
        assert redeemable not in persisted
    assert inspected.returncode == 0, inspected.stderr
    assert TARGET_TEXT in inspected.stdout
    assert "citationOpen: NOT_ACTIVE" in inspected.stdout
    assert refused.returncode == 12
    assert refused.stdout == ""
    assert refused.stderr == "context-engine-context: malformed_package\n"
    assert configuration.secret not in machine.stdout
    assert configuration.secret not in inspected.stdout + refused.stderr

    print("=== fresh strict-JSON query ===")
    print("validated exact ResolutionOutcome; saved as untrusted local capture")
    print("=== package inspection (sanitized) ===")
    print(_sanitized_human(inspected.stdout), end="")
    print("=== tampered-capture refusal ===")
    print(refused.stderr, end="")
