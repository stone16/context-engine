import json
import socket
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from urllib.request import urlopen

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
            sys.executable,
            "-m",
            "uvicorn",
            "adapters.http.app:app",
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
                    output = process.stdout.read() if process.stdout else ""
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
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_worker_completes_test_lifecycle() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "engine.supply_worker_main", "--test-mode"],
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
