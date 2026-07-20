import json
import socket
import subprocess
import time
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from engine import BUILD_IDENTIFIER

ROOT = Path(__file__).parents[2]


def _unused_port() -> int:
    with closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


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
        time.sleep(0.2)
        assert process.poll() is None
    finally:
        process.terminate()
        output, _ = process.communicate(timeout=5)

    assert json.loads(output) == {
        "status": "ready",
        "service": "context-engine-worker",
        "version": BUILD_IDENTIFIER,
        "job_behavior": "NOT_ACTIVE",
    }
