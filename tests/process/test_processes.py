import json
import os
import selectors
import socket
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from typing import cast
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import UUID

import pytest

from engine import BUILD_IDENTIFIER
from tests.process.conformance_app import (
    PROCESS_ORGANIZATION_REF,
    PROCESS_VALID_TOKEN,
)
from tests.support.releases import active_runtime_release

ROOT = Path(__file__).parents[2]


def _wait_until_ready(process: subprocess.Popen[str], port: int) -> None:
    deadline = time.monotonic() + 10
    while True:
        try:
            with urlopen(f"http://127.0.0.1:{port}/health", timeout=1):
                return
        except OSError:
            if process.poll() is not None or time.monotonic() >= deadline:
                process.terminate()
                output, _ = process.communicate(timeout=5)
                raise AssertionError(f"API failed to become ready:\n{output}") from None
            time.sleep(0.05)


def _unused_port() -> int:
    with closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_worker_readiness(
    process: subprocess.Popen[str],
    *,
    timeout_seconds: float = 10,
) -> dict[str, object]:
    assert process.stdout is not None
    deadline = time.monotonic() + timeout_seconds
    output = bytearray()

    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if not selector.select(timeout=max(remaining, 0)):
                continue

            chunk = os.read(process.stdout.fileno(), 4096)
            if not chunk:
                break
            output.extend(chunk)
            if b"\n" not in output:
                continue

            readiness_line, _, _ = output.partition(b"\n")
            try:
                payload: object = json.loads(readiness_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise AssertionError(
                    f"Worker emitted invalid readiness JSON: {readiness_line!r}"
                ) from error
            if not isinstance(payload, dict) or not all(
                isinstance(key, str) for key in payload
            ):
                raise AssertionError(
                    f"Worker emitted a non-object readiness payload: {payload!r}"
                )
            return cast(dict[str, object], payload)

    captured = output.decode("utf-8", errors="replace")
    if process.poll() is None:
        raise AssertionError(
            "Worker did not emit readiness JSON within "
            f"{timeout_seconds}s: {captured!r}"
        )
    raise AssertionError(
        f"Worker exited with code {process.returncode} before readiness: {captured!r}"
    )


def test_api_boots_and_reports_readiness() -> None:
    port = _unused_port()
    process = subprocess.Popen(
        [
            "context-engine-api",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                with urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                    payload = json.load(response)
                break
            except OSError:
                if process.poll() is not None or time.monotonic() >= deadline:
                    process.terminate()
                    output, _ = process.communicate(timeout=5)
                    raise AssertionError(
                        f"API failed to become ready:\n{output}"
                    ) from None
                time.sleep(0.05)

        assert payload == {
            "status": "ready",
            "service": "context-engine-api",
            "version": BUILD_IDENTIFIER,
            "runtime_delivery": "NOT_ACTIVE",
        }

        request = Request(
            f"http://127.0.0.1:{port}/v1/context:resolve",
            data=b'{"kind":"acquire","need":{"query":"probe"}}',
            headers={
                "Authorization": "Bearer unconfigured-production-credential",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with pytest.raises(HTTPError) as authentication_error:
            urlopen(request, timeout=1)
        assert authentication_error.value.code == 401
        assert json.load(authentication_error.value) == {
            "code": "authentication_failed"
        }
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_http_acquire_smoke_returns_the_empty_package_contract() -> None:
    port = _unused_port()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "tests.process.conformance_app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_until_ready(process, port)
        request = Request(
            f"http://127.0.0.1:{port}/v0/resolve",
            data=b'{"kind":"acquire","need":{"query":"process smoke"}}',
            headers={
                "Authorization": f"Bearer {PROCESS_VALID_TOKEN}",
                "Content-Type": "application/json",
                "X-Context-Request-Id": "process-v0-smoke",
            },
            method="POST",
        )
        with urlopen(request, timeout=1) as response:
            payload = json.load(response)
            assert response.status == 200
            assert response.headers["Cache-Control"] == "no-store"

        assert payload["kind"] == "resolved"
        package = payload["package"]
        assert package["packageId"].startswith("pkg_")
        assert PROCESS_ORGANIZATION_REF not in json.dumps(payload)
        assert package["purpose"] == "context.answer"
        release = active_runtime_release(UUID(PROCESS_ORGANIZATION_REF))
        assert package["releaseManifestRef"] == release.manifest_ref
        assert package["tokenizerRef"] == release.tokenizer_ref
        assert package["packageSchemaRef"] == release.package_schema_ref
        assert package["blocks"] == []
        assert package["evidence"] == []
        assert package["gaps"] == []
        assert package["coverage"] == {
            "status": "empty",
            "reason": "no_authorized_evidence",
        }
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_worker_completes_test_lifecycle() -> None:
    completed = subprocess.run(
        ["context-engine-worker", "--test-mode"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == {
        "status": "test-complete",
        "service": "context-engine-worker",
        "version": BUILD_IDENTIFIER,
        "job_behavior": "NOT_ACTIVE",
    }


def test_worker_stays_alive_until_terminated_in_normal_mode() -> None:
    process = subprocess.Popen(
        ["context-engine-worker"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        payload = _wait_for_worker_readiness(process)
        assert process.poll() is None
    finally:
        process.terminate()
        process.communicate(timeout=5)

    assert payload == {
        "status": "ready",
        "service": "context-engine-worker",
        "version": BUILD_IDENTIFIER,
        "job_behavior": "NOT_ACTIVE",
    }
