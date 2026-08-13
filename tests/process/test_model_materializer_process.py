from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import rfc8785

ROOT = Path(__file__).parents[2]


def _artifact_digest(artifacts: list[dict[str, str]]) -> str:
    return hashlib.sha256(rfc8785.dumps(artifacts)).hexdigest()


def _twin_run(
    tmp_path: Path,
    *,
    files: Mapping[str, bytes],
    handler_factory: type[BaseHTTPRequestHandler],
    destination: Path | None = None,
    artifact_overrides: Mapping[str, str] | None = None,
    manifest_overrides: Mapping[str, object] | None = None,
    staging_attack: str = "none",
) -> subprocess.CompletedProcess[str]:
    artifacts = [
        {
            "path": path,
            "sha256": (artifact_overrides or {}).get(
                path, hashlib.sha256(content).hexdigest()
            ),
        }
        for path, content in sorted(files.items())
    ]
    document: dict[str, object] = {
        "artifactDigest": _artifact_digest(artifacts),
        "artifacts": artifacts,
        "modelId": "synthetic/model",
        "revision": "a" * 40,
    }
    document.update(manifest_overrides or {})
    manifest = tmp_path / "synthetic-manifest.json"
    manifest.write_text(json.dumps(document), encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.process.model_materializer_twin_process",
                "--base-url",
                f"http://127.0.0.1:{server.server_port}",
                "--manifest",
                str(manifest),
                "--staging-attack",
                staging_attack,
                "--",
                "--role",
                "primary",
                "--destination",
                str(destination or tmp_path / "durable-model"),
            ],
            cwd=ROOT,
            env={
                name: value
                for name, value in os.environ.items()
                if not name.startswith("CONTEXT_ENGINE_")
            },
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _assert_closed_output(
    completed: subprocess.CompletedProcess[str],
    *,
    category: str,
    returncode: int,
    private_values: tuple[str, ...] = (),
) -> None:
    assert completed.returncode == returncode
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {
        "schemaVersion": "context-engine-model-materializer-v1",
        "service": "context-engine-model-materializer",
        "status": "refused",
        "category": category,
    }
    captured = completed.stdout + completed.stderr
    assert all(value not in captured for value in private_values)


def test_model_materializer_help_exposes_only_closed_operator_inputs() -> None:
    completed = subprocess.run(
        ["context-engine-model-materializer", "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stderr == ""
    assert "--role" in completed.stdout
    assert "--destination" in completed.stdout
    for prohibited in (
        "--model-id",
        "--revision",
        "--artifact",
        "--digest",
        "--url",
        "--host",
        "--token",
    ):
        assert prohibited not in completed.stdout


def test_model_materializer_help_imports_no_runtime_or_mutation_planes() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "from applications.model_materializer import main; "
                "\ntry: main(['--help'])\n"
                "except SystemExit as error:\n"
                " assert error.code == 0\n"
                "print('\\n'.join(sorted(sys.modules)))"
            ),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    imported = completed.stdout.splitlines()
    prohibited_prefixes = (
        "applications.control",
        "applications.preflight",
        "applications.release_promotion",
        "applications.worker",
        "engine.control",
        "engine.learning",
        "engine.persistence",
        "engine.runtime",
        "engine.supply",
        "sentence_transformers",
        "sqlalchemy",
        "alembic",
    )
    assert not any(
        module == prefix or module.startswith(prefix + ".")
        for module in imported
        for prefix in prohibited_prefixes
    )


def test_model_materializer_parser_refusal_never_echoes_supplied_values() -> None:
    private = "/private/model-destination/credential-canary"
    completed = subprocess.run(
        [
            "context-engine-model-materializer",
            "--role",
            private,
            "--destination",
            private,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {
        "schemaVersion": "context-engine-model-materializer-v1",
        "service": "context-engine-model-materializer",
        "status": "refused",
        "category": "operation_refused",
    }
    assert private not in completed.stdout + completed.stderr


def test_model_materializer_fetches_tiny_registered_twin_and_publishes_once(
    tmp_path: Path,
) -> None:
    files = {
        "1_Pooling/config.json": b"{}\n",
        "model.safetensors": b"tiny synthetic weights\n",
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            prefix = "/synthetic/model/resolve/" + "a" * 40 + "/"
            if not self.path.startswith(prefix):
                self.send_error(404)
                return
            relative_path = self.path.removeprefix(prefix)
            content = files.get(relative_path)
            if content is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, _format: str, *args: object) -> None:
            del args

    destination = tmp_path / "durable-model"
    completed = _twin_run(
        tmp_path,
        files=files,
        handler_factory=Handler,
        destination=destination,
    )

    assert completed.returncode == 0
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {
        "schemaVersion": "context-engine-model-materializer-v1",
        "service": "context-engine-model-materializer",
        "status": "materialized",
        "category": "ready",
    }
    assert {
        path.relative_to(destination).as_posix(): path.read_bytes()
        for path in destination.rglob("*")
        if path.is_file()
    } == files


def test_model_materializer_follows_only_same_twin_redirects(tmp_path: Path) -> None:
    files = {"model.safetensors": b"tiny synthetic weights\n"}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path.startswith("/synthetic/model/resolve/"):
                self.send_response(307)
                self.send_header("Location", "/allowed-cache/model.safetensors")
                self.end_headers()
                return
            content = files["model.safetensors"]
            self.send_response(200)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, _format: str, *args: object) -> None:
            del args

    completed = _twin_run(tmp_path, files=files, handler_factory=Handler)

    assert completed.returncode == 0
    assert json.loads(completed.stdout)["status"] == "materialized"


def test_model_materializer_refuses_cross_host_redirect_before_publication(
    tmp_path: Path,
) -> None:
    files = {"model.safetensors": b"tiny synthetic weights\n"}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(302)
            self.send_header("Location", "http://example.invalid/stolen")
            self.end_headers()

        def log_message(self, _format: str, *args: object) -> None:
            del args

    destination = tmp_path / "durable-model"
    completed = _twin_run(
        tmp_path,
        files=files,
        handler_factory=Handler,
        destination=destination,
    )

    _assert_closed_output(
        completed,
        category="transport_refused",
        returncode=11,
        private_values=(str(destination), "example.invalid"),
    )
    assert not destination.exists()


def test_model_materializer_refuses_short_read_and_interrupted_transfer(
    tmp_path: Path,
) -> None:
    files = {"model.safetensors": b"tiny synthetic weights\n"}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", str(len(files["model.safetensors"])))
            self.end_headers()
            self.wfile.write(b"short")
            self.wfile.flush()
            self.connection.shutdown(1)

        def log_message(self, _format: str, *args: object) -> None:
            del args

    destination = tmp_path / "durable-model"
    completed = _twin_run(
        tmp_path,
        files=files,
        handler_factory=Handler,
        destination=destination,
    )

    _assert_closed_output(completed, category="transport_refused", returncode=11)
    assert not destination.exists()
    assert not tuple(tmp_path.glob(".context-engine-model-work-*"))


def test_model_materializer_refuses_digest_mismatch_before_publication(
    tmp_path: Path,
) -> None:
    files = {"model.safetensors": b"tiny synthetic weights\n"}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            changed = b"changed synthetic bytes\n"
            self.send_response(200)
            self.send_header("Content-Length", str(len(changed)))
            self.end_headers()
            self.wfile.write(changed)

        def log_message(self, _format: str, *args: object) -> None:
            del args

    destination = tmp_path / "durable-model"
    completed = _twin_run(
        tmp_path,
        files=files,
        handler_factory=Handler,
        destination=destination,
    )

    _assert_closed_output(completed, category="verification_refused", returncode=12)
    assert not destination.exists()


def test_model_materializer_refuses_registry_traversal_before_transport(
    tmp_path: Path,
) -> None:
    files = {"model.safetensors": b"tiny synthetic weights\n"}

    class Handler(BaseHTTPRequestHandler):
        requests = 0

        def do_GET(self) -> None:
            type(self).requests += 1
            self.send_error(500)

        def log_message(self, _format: str, *args: object) -> None:
            del args

    completed = _twin_run(
        tmp_path,
        files=files,
        handler_factory=Handler,
        manifest_overrides={
            "artifacts": [{"path": "../escape", "sha256": "a" * 64}],
            "artifactDigest": _artifact_digest(
                [{"path": "../escape", "sha256": "a" * 64}]
            ),
        },
    )

    _assert_closed_output(completed, category="registry_unavailable", returncode=10)
    assert Handler.requests == 0
    assert not (tmp_path / "escape").exists()


def test_model_materializer_preserves_existing_destination_byte_for_byte(
    tmp_path: Path,
) -> None:
    files = {"model.safetensors": b"tiny synthetic weights\n"}

    class Handler(BaseHTTPRequestHandler):
        requests = 0

        def do_GET(self) -> None:
            type(self).requests += 1
            self.send_error(500)

        def log_message(self, _format: str, *args: object) -> None:
            del args

    destination = tmp_path / "durable-model"
    destination.mkdir()
    canary = destination / "existing.bin"
    original = b"existing destination must remain exact\n"
    canary.write_bytes(original)
    completed = _twin_run(
        tmp_path,
        files=files,
        handler_factory=Handler,
        destination=destination,
    )

    _assert_closed_output(completed, category="destination_exists", returncode=13)
    assert Handler.requests == 0
    assert canary.read_bytes() == original
    assert tuple(path.name for path in destination.iterdir()) == ("existing.bin",)


def test_model_materializer_preserves_symlink_destination_and_target(
    tmp_path: Path,
) -> None:
    files = {"model.safetensors": b"tiny synthetic weights\n"}

    class Handler(BaseHTTPRequestHandler):
        requests = 0

        def do_GET(self) -> None:
            type(self).requests += 1
            self.send_error(500)

        def log_message(self, _format: str, *args: object) -> None:
            del args

    target = tmp_path / "existing-target"
    target.mkdir()
    canary = target / "existing.bin"
    original = b"symlink target must remain exact\n"
    canary.write_bytes(original)
    destination = tmp_path / "durable-model"
    destination.symlink_to(target, target_is_directory=True)
    completed = _twin_run(
        tmp_path,
        files=files,
        handler_factory=Handler,
        destination=destination,
    )

    _assert_closed_output(completed, category="destination_exists", returncode=13)
    assert Handler.requests == 0
    assert destination.is_symlink()
    assert destination.readlink() == target
    assert canary.read_bytes() == original


def test_model_materializer_atomic_publish_preserves_destination_race(
    tmp_path: Path,
) -> None:
    files = {"model.safetensors": b"tiny synthetic weights\n"}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            content = files["model.safetensors"]
            self.send_response(200)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, _format: str, *args: object) -> None:
            del args

    destination = tmp_path / "durable-model"
    completed = _twin_run(
        tmp_path,
        files=files,
        handler_factory=Handler,
        destination=destination,
        staging_attack="destination-race",
    )

    _assert_closed_output(completed, category="destination_exists", returncode=13)
    assert (destination / "malicious.bin").read_bytes() == b"malicious-race-canary\n"
    assert tuple(path.name for path in destination.iterdir()) == ("malicious.bin",)


def test_model_materializer_refuses_replaced_destination_parent(
    tmp_path: Path,
) -> None:
    files = {"model.safetensors": b"tiny synthetic weights\n"}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            content = files["model.safetensors"]
            self.send_response(200)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, _format: str, *args: object) -> None:
            del args

    selected_parent = tmp_path / "selected-parent"
    selected_parent.mkdir()
    destination = selected_parent / "durable-model"
    completed = _twin_run(
        tmp_path,
        files=files,
        handler_factory=Handler,
        destination=destination,
        staging_attack="parent-swap",
    )

    _assert_closed_output(completed, category="publication_refused", returncode=13)
    assert not destination.exists()
    assert (selected_parent / "malicious.bin").read_bytes() == (
        b"malicious-parent-canary\n"
    )
    retained = tmp_path / "retained-parent" / "durable-model"
    assert not retained.exists()


def test_model_materializer_refuses_staging_name_swap_before_publication(
    tmp_path: Path,
) -> None:
    files = {"model.safetensors": b"tiny synthetic weights\n"}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            content = files["model.safetensors"]
            self.send_response(200)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, _format: str, *args: object) -> None:
            del args

    destination = tmp_path / "durable-model"
    completed = _twin_run(
        tmp_path,
        files=files,
        handler_factory=Handler,
        destination=destination,
        staging_attack="staging-swap",
    )

    _assert_closed_output(completed, category="publication_refused", returncode=13)
    assert not destination.exists()
    retained = tmp_path / "retained-verified-staging"
    assert (retained / "model.safetensors").read_bytes() == files["model.safetensors"]


def test_model_materializer_interrupt_is_closed_and_cleans_staging(
    tmp_path: Path,
) -> None:
    files = {"model.safetensors": b"tiny synthetic weights\n"}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            content = files["model.safetensors"]
            self.send_response(200)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, _format: str, *args: object) -> None:
            del args

    destination = tmp_path / "durable-model"
    completed = _twin_run(
        tmp_path,
        files=files,
        handler_factory=Handler,
        destination=destination,
        staging_attack="interrupt",
    )

    _assert_closed_output(completed, category="operation_refused", returncode=14)
    assert not destination.exists()
    assert not tuple(tmp_path.glob(".context-engine-model-work-*"))


def test_model_materializer_refuses_extra_staging_file_types(
    tmp_path: Path,
) -> None:
    files = {"model.safetensors": b"tiny synthetic weights\n"}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            content = files["model.safetensors"]
            self.send_response(200)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, _format: str, *args: object) -> None:
            del args

    for attack in ("directory", "symlink", "fifo"):
        case = tmp_path / attack
        case.mkdir()
        destination = case / "durable-model"
        completed = _twin_run(
            case,
            files=files,
            handler_factory=Handler,
            destination=destination,
            staging_attack=attack,
        )
        _assert_closed_output(
            completed,
            category="verification_refused",
            returncode=12,
        )
        assert not destination.exists()
