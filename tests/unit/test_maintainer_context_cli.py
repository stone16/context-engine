from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar, cast

from engine.runtime.package_digest import context_package_digest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SECRET = "maintainer-cli-dogfood-secret-at-least-32-bytes"
EVIDENCE_REF = "ev_" + "a" * 64
LIVE_EGRESS_GRANT = "egrm_" + "e" * 64
REDACTED_EGRESS_GRANT = "REDACTED-EGRESS-GRANT"


def _package_document(
    text: str = "Authorized maintainer excerpt",
) -> dict[str, object]:
    package: dict[str, object] = {
        "packageId": "pkg_" + "b" * 32,
        "purpose": "context.answer",
        "audienceDigest": "c" * 64,
        "policyEpoch": 7,
        "policySnapshotRef": "policy-snapshot-current",
        "decisionRef": "dec_" + "d" * 32,
        "runRef": "run-maintainer-context",
        "releaseManifestRef": "release-current",
        "retentionPolicyRef": "package-digest-only-v1",
        "asOf": "2099-08-02T12:00:00Z",
        "expiresAt": "2099-08-02T12:05:00Z",
        "ttlSeconds": 300,
        "tokenizerRef": "utf8-byte-v1",
        "packageSchemaRef": "context-package-openapi-v0",
        "blocks": [
            {
                "blockId": "block_" + "a" * 64,
                "text": text,
                "evidenceRefs": [EVIDENCE_REF],
            }
        ],
        "evidence": [
            {
                "evidenceRef": EVIDENCE_REF,
                "sourceRef": "source:file:maintainer",
                "resourceRef": "resource:file:authorized",
                "revisionRef": "revision:current",
                "fragmentRef": "fragment:authorized",
                "projectedFields": ["body"],
                "runRef": "run-maintainer-context",
                "purpose": "context.answer",
                "authorizationAsOf": "2099-08-02T12:00:00Z",
                "decisionRef": "dec_" + "d" * 32,
                "policySnapshotRef": "policy-snapshot-current",
                "policyEpoch": 7,
                "sourceAclEvidence": {
                    "kind": "mirrored",
                    "projectionRef": "source-acl-current",
                    "aclAsOf": "2099-08-02T12:00:00Z",
                    "freshnessProfileRef": "file-source-current-v1",
                },
                "citationOpenRef": "citation-display-only",
            }
        ],
        "gaps": [],
        "coverage": {"status": "sufficient"},
        "budgetUsage": {
            "tokens": len(text.encode("utf-8")),
            "providerCalls": 0,
            "costMicrounits": 0,
            "elapsedMs": 1,
        },
        "continuation": None,
    }
    package["packageDigest"] = context_package_digest(package)
    return package


class _ResolveHandler(BaseHTTPRequestHandler):
    outcome: ClassVar[dict[str, object]]
    requests: ClassVar[
        list[tuple[str, str | None, str | None, dict[str, object]]]
    ]
    status_code: ClassVar[int]

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers["Content-Length"])
        body = json.loads(self.rfile.read(length))
        _ResolveHandler.requests.append(
            (
                self.path,
                self.headers.get("Authorization"),
                self.headers.get("X-Context-Request-Id"),
                body,
            )
        )
        encoded = json.dumps(_ResolveHandler.outcome).encode("utf-8")
        self.send_response(_ResolveHandler.status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


@contextmanager
def _resolve_server(
    outcome: dict[str, object],
    *,
    status_code: int = 200,
) -> Iterator[
    tuple[
        str,
        list[tuple[str, str | None, str | None, dict[str, object]]],
    ]
]:
    _ResolveHandler.outcome = outcome
    _ResolveHandler.requests = []
    _ResolveHandler.status_code = status_code
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ResolveHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", _ResolveHandler.requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _command(
    *arguments: str,
    environment: dict[str, str],
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "applications.maintainer_context", *arguments],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        input=input_text,
        timeout=10,
    )


def test_query_json_sends_only_closed_acquire_and_returns_exact_wire() -> None:
    outcome: dict[str, object] = {
        "kind": "resolved",
        "package": _package_document(),
        "egressGrant": None,
    }
    with _resolve_server(outcome) as (base_url, requests):
        environment = {
            **os.environ,
            "CONTEXT_ENGINE_DOGFOOD_BASE_URL": base_url,
            "CONTEXT_ENGINE_DOGFOOD_SECRET": SECRET,
        }

        completed = _command(
            "query",
            "What context is authorized?",
            "--format",
            "json",
            "--max-tokens",
            "123",
            "--source-ref",
            "source:file:maintainer",
            environment=environment,
        )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == outcome
    assert completed.stderr == ""
    assert len(requests) == 1
    path, authorization, request_id, body = requests[0]
    assert path == "/v0/resolve"
    assert authorization == f"Bearer {SECRET}"
    assert request_id is not None
    assert request_id.startswith("maintainer-context-")
    assert body == {
        "kind": "acquire",
        "need": {"query": "What context is authorized?"},
        "packageBudget": {"maxTokens": 123},
        "requestNarrowing": {"sourceRefs": ["source:file:maintainer"]},
    }


def test_separate_questions_use_distinct_fresh_request_ids() -> None:
    outcome: dict[str, object] = {
        "kind": "resolved",
        "package": _package_document(),
        "egressGrant": None,
    }
    with _resolve_server(outcome) as (base_url, requests):
        environment = {
            **os.environ,
            "CONTEXT_ENGINE_DOGFOOD_BASE_URL": base_url,
            "CONTEXT_ENGINE_DOGFOOD_SECRET": SECRET,
        }
        first = _command("query", "First question", environment=environment)
        second = _command("query", "Second question", environment=environment)

    assert first.returncode == second.returncode == 0
    request_ids = [request_id for _, _, request_id, _ in requests]
    assert len(request_ids) == 2
    assert all(
        request_id is not None
        and request_id.startswith("maintainer-context-")
        for request_id in request_ids
    )
    assert request_ids[0] != request_ids[1]


def test_inspect_json_validates_untrusted_package_from_stdin() -> None:
    package = _package_document()

    completed = _command(
        "inspect",
        "-",
        "--format",
        "json",
        environment=os.environ.copy(),
        input_text=json.dumps(package),
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == package
    assert completed.stderr == ""


def test_inspect_accepts_query_capture_and_emits_exact_package_json() -> None:
    package = _package_document()
    envelope = {"kind": "resolved", "package": package, "egressGrant": None}

    completed = _command(
        "inspect",
        "-",
        "--format",
        "json",
        environment=os.environ.copy(),
        input_text=json.dumps(envelope),
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == package
    assert completed.stderr == ""


def test_human_query_renders_package_budget_coverage_and_citation_lineage() -> None:
    outcome: dict[str, object] = {
        "kind": "resolved",
        "package": _package_document(),
        "egressGrant": None,
    }
    with _resolve_server(outcome) as (base_url, _):
        completed = _command(
            "query",
            "Show the authorized context",
            environment={
                **os.environ,
                "CONTEXT_ENGINE_DOGFOOD_BASE_URL": base_url,
                "CONTEXT_ENGINE_DOGFOOD_SECRET": SECRET,
            },
        )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert "purpose: context.answer" in completed.stdout
    assert "asOf: 2099-08-02T12:00:00Z" in completed.stdout
    assert "expiresAt: 2099-08-02T12:05:00Z (current)" in completed.stdout
    assert "coverage: sufficient" in completed.stdout
    assert "tokens: 29" in completed.stdout
    assert "text: Authorized maintainer excerpt" in completed.stdout
    assert f"evidenceRef: {EVIDENCE_REF}" in completed.stdout
    assert "sourceRef: source:file:maintainer" in completed.stdout
    assert "resourceRef: resource:file:authorized" in completed.stdout
    assert "revisionRef: revision:current" in completed.stdout
    assert "fragmentRef: fragment:authorized" in completed.stdout
    assert 'projectedFields: ["body"]' in completed.stdout
    assert "runRef: run-maintainer-context" in completed.stdout
    assert "purpose: context.answer" in completed.stdout
    assert "authorizationAsOf: 2099-08-02T12:00:00Z" in completed.stdout
    assert f"decisionRef: {'dec_' + 'd' * 32}" in completed.stdout
    assert "policySnapshotRef: policy-snapshot-current" in completed.stdout
    assert "policyEpoch: 7" in completed.stdout
    assert 'sourceAclEvidence: {"kind":"mirrored"' in completed.stdout
    assert '"projectionRef":"source-acl-current"' in completed.stdout
    assert '"aclAsOf":"2099-08-02T12:00:00Z"' in completed.stdout
    assert '"freshnessProfileRef":"file-source-current-v1"' in completed.stdout
    assert "citationOpenRef: citation-display-only" in completed.stdout
    assert "citationOpen: NOT_ACTIVE" in completed.stdout


def test_expired_package_is_content_free_and_has_stable_exit_class() -> None:
    package = _package_document()
    package["asOf"] = "2026-08-02T12:00:00Z"
    package["expiresAt"] = "2026-08-02T12:05:00Z"
    evidence = cast(list[dict[str, object]], package["evidence"])[0]
    evidence["authorizationAsOf"] = "2026-08-02T12:00:00Z"
    source_acl = cast(dict[str, object], evidence["sourceAclEvidence"])
    source_acl["aclAsOf"] = "2026-08-02T12:00:00Z"
    package.pop("packageDigest")
    package["packageDigest"] = context_package_digest(package)
    outcome: dict[str, object] = {
        "kind": "resolved",
        "package": package,
        "egressGrant": None,
    }
    with _resolve_server(outcome) as (base_url, _):
        completed = _command(
            "query",
            "Do not render expired content",
            environment={
                **os.environ,
                "CONTEXT_ENGINE_DOGFOOD_BASE_URL": base_url,
                "CONTEXT_ENGINE_DOGFOOD_SECRET": SECRET,
            },
        )

    assert completed.returncode == 13
    assert completed.stdout == ""
    assert completed.stderr == "context-engine-context: expired_package\n"
    assert "Authorized maintainer excerpt" not in completed.stderr


def test_request_refusal_is_content_free_and_has_stable_exit_class() -> None:
    with _resolve_server(
        {"kind": "request_not_available", "retryable": False}
    ) as (base_url, _):
        completed = _command(
            "query",
            "Try an unavailable capability",
            "--format",
            "json",
            environment={
                **os.environ,
                "CONTEXT_ENGINE_DOGFOOD_BASE_URL": base_url,
                "CONTEXT_ENGINE_DOGFOOD_SECRET": SECRET,
            },
        )

    assert completed.returncode == 10
    assert completed.stdout == '{"kind":"request_not_available","retryable":false}\n'
    assert completed.stderr == ""


def test_invalid_inspection_refuses_before_partial_render(tmp_path: Path) -> None:
    package = _package_document()
    package["unexpectedAuthority"] = "must-not-render"
    capture = tmp_path / "invalid-package.json"
    capture.write_text(json.dumps(package), encoding="utf-8")

    completed = _command(
        "inspect",
        str(capture),
        environment=os.environ.copy(),
    )

    assert completed.returncode == 12
    assert completed.stdout == ""
    assert completed.stderr == "context-engine-context: malformed_package\n"
    assert "must-not-render" not in completed.stderr


def test_missing_or_non_loopback_configuration_is_content_free() -> None:
    missing = os.environ.copy()
    missing.pop("CONTEXT_ENGINE_DOGFOOD_BASE_URL", None)
    missing.pop("CONTEXT_ENGINE_DOGFOOD_SECRET", None)
    invalid = {
        **os.environ,
        "CONTEXT_ENGINE_DOGFOOD_BASE_URL": "https://example.com:443",
        "CONTEXT_ENGINE_DOGFOOD_SECRET": SECRET,
    }

    for environment in (missing, invalid):
        completed = _command(
            "query",
            "Do not leak configuration",
            environment=environment,
        )
        assert completed.returncode == 14
        assert completed.stdout == ""
        assert completed.stderr == (
            "context-engine-context: invalid_configuration\n"
        )
        assert SECRET not in completed.stderr


def test_nonpositive_budget_ceiling_refuses_before_any_request() -> None:
    outcome: dict[str, object] = {
        "kind": "resolved",
        "package": _package_document(),
        "egressGrant": None,
    }
    with _resolve_server(outcome) as (base_url, requests):
        completed = _command(
            "query",
            "Which ceiling does the caller accept?",
            "--max-provider-calls",
            "0",
            environment={
                **os.environ,
                "CONTEXT_ENGINE_DOGFOOD_BASE_URL": base_url,
                "CONTEXT_ENGINE_DOGFOOD_SECRET": SECRET,
            },
        )

    assert completed.returncode == 14
    assert completed.stdout == ""
    assert completed.stderr == "context-engine-context: invalid_configuration\n"
    assert requests == []


def test_capture_beyond_the_input_bound_refuses_an_otherwise_valid_package(
    tmp_path: Path,
) -> None:
    """Only the byte bound can refuse this: the Package itself is valid."""

    from applications.maintainer_context import MAX_CAPTURE_BYTES

    excerpt = "a" * MAX_CAPTURE_BYTES
    capture = tmp_path / "oversized-package.json"
    capture.write_text(
        json.dumps(_package_document(text=excerpt)),
        encoding="utf-8",
    )
    assert capture.stat().st_size > MAX_CAPTURE_BYTES

    completed = _command(
        "inspect",
        str(capture),
        environment=os.environ.copy(),
    )

    assert completed.returncode == 12
    assert completed.stdout == ""
    assert completed.stderr == "context-engine-context: malformed_package\n"


def test_unavailable_service_is_content_free_and_has_stable_exit_class() -> None:
    with _resolve_server(
        {"code": "service_unavailable"},
        status_code=503,
    ) as (base_url, _):
        completed = _command(
            "query",
            "Do not expose transport details",
            environment={
                **os.environ,
                "CONTEXT_ENGINE_DOGFOOD_BASE_URL": base_url,
                "CONTEXT_ENGINE_DOGFOOD_SECRET": SECRET,
            },
        )

    assert completed.returncode == 11
    assert completed.stdout == ""
    assert completed.stderr == "context-engine-context: service_unavailable\n"
    assert SECRET not in completed.stderr


def test_served_rejection_status_classes_map_to_stable_exit_classes() -> None:
    from adapters.http.dogfood_client import CALLER_REJECTED_STATUSES

    assert sorted(CALLER_REJECTED_STATUSES) == [400, 401, 422]

    for status_code, code, expected_exit, expected_class in (
        (401, "authentication_failed", 14, "invalid_configuration"),
        (400, "invalid_request", 14, "invalid_configuration"),
        (422, "invalid_request", 14, "invalid_configuration"),
        (403, "application_forbidden", 11, "service_unavailable"),
        (429, "rate_limited", 11, "service_unavailable"),
        (503, "service_unavailable", 11, "service_unavailable"),
    ):
        with _resolve_server({"code": code}, status_code=status_code) as (
            base_url,
            _,
        ):
            completed = _command(
                "query",
                "Classify one served rejection",
                environment={
                    **os.environ,
                    "CONTEXT_ENGINE_DOGFOOD_BASE_URL": base_url,
                    "CONTEXT_ENGINE_DOGFOOD_SECRET": SECRET,
                },
            )

        assert completed.returncode == expected_exit, completed.stderr
        assert completed.stdout == ""
        assert completed.stderr == f"context-engine-context: {expected_class}\n"
        assert SECRET not in completed.stderr


def test_unreachable_loopback_transport_stays_service_unavailable() -> None:
    completed = _command(
        "query",
        "Classify one transport failure",
        environment={
            **os.environ,
            "CONTEXT_ENGINE_DOGFOOD_BASE_URL": "http://127.0.0.1:1",
            "CONTEXT_ENGINE_DOGFOOD_SECRET": SECRET,
        },
    )

    assert completed.returncode == 11
    assert completed.stdout == ""
    assert completed.stderr == "context-engine-context: service_unavailable\n"
    assert SECRET not in completed.stderr


def test_secret_is_refused_from_query_without_echo() -> None:
    completed = _command(
        "query",
        SECRET,
        environment={
            **os.environ,
            "CONTEXT_ENGINE_DOGFOOD_BASE_URL": "http://127.0.0.1:1",
            "CONTEXT_ENGINE_DOGFOOD_SECRET": SECRET,
        },
    )

    assert completed.returncode == 14
    assert completed.stdout == ""
    assert completed.stderr == "context-engine-context: invalid_configuration\n"
    assert SECRET not in completed.stderr


def test_secret_is_refused_from_inspection_without_echo() -> None:
    package = _package_document()
    blocks = cast(list[dict[str, object]], package["blocks"])
    blocks[0]["text"] = SECRET
    package["budgetUsage"] = {
        "tokens": len(SECRET.encode("utf-8")),
        "providerCalls": 0,
        "costMicrounits": 0,
        "elapsedMs": 1,
    }
    package.pop("packageDigest")
    package["packageDigest"] = context_package_digest(package)

    for format_name in ("human", "json"):
        completed = _command(
            "inspect",
            "-",
            "--format",
            format_name,
            environment={
                **os.environ,
                "CONTEXT_ENGINE_DOGFOOD_SECRET": SECRET,
            },
            input_text=json.dumps(package),
        )

        assert completed.returncode == 14
        assert completed.stdout == ""
        assert completed.stderr == (
            "context-engine-context: invalid_configuration\n"
        )
        assert SECRET not in completed.stderr


def test_cli_imports_no_runtime_control_action_learning_or_model_modules() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json,sys; import applications.maintainer_context; "
                "print(json.dumps(sorted(sys.modules)))"
            ),
        ],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    imported = cast(list[str], json.loads(completed.stdout))

    assert all(not module.startswith("engine.runtime") for module in imported)
    assert all(not module.startswith("engine.control") for module in imported)
    assert all(not module.startswith("engine.learning") for module in imported)
    assert all(not module.startswith("engine.persistence") for module in imported)
    assert all(not module.startswith("engine.supply") for module in imported)
    assert all(not module.startswith("applications.control") for module in imported)
    assert all(not module.startswith("action_plane") for module in imported)
    assert all(not module.startswith("bot_delivery") for module in imported)


def test_cli_help_exposes_only_read_subcommands_and_no_secret_argument() -> None:
    completed = _command("--help", environment=os.environ.copy())

    assert completed.returncode == 0
    assert "{query,inspect}" in completed.stdout
    assert "open-citation" not in completed.stdout
    assert "evaluate" not in completed.stdout
    assert "control" not in completed.stdout.casefold()
    assert "action" not in completed.stdout.casefold()
    assert "secret" not in completed.stdout.casefold()


def test_cli_contract_records_stable_exit_classes() -> None:
    from applications.maintainer_context import (
        EXIT_EXPIRED_PACKAGE,
        EXIT_EXPLICIT_REFUSAL,
        EXIT_INVALID_CONFIGURATION,
        EXIT_MALFORMED_PACKAGE,
        EXIT_SERVICE_UNAVAILABLE,
        EXIT_SUCCESS,
    )

    assert {
        "success": EXIT_SUCCESS,
        "explicit_refusal": EXIT_EXPLICIT_REFUSAL,
        "service_unavailable": EXIT_SERVICE_UNAVAILABLE,
        "malformed_package": EXIT_MALFORMED_PACKAGE,
        "expired_package": EXIT_EXPIRED_PACKAGE,
        "invalid_configuration": EXIT_INVALID_CONFIGURATION,
    } == {
        "success": 0,
        "explicit_refusal": 10,
        "service_unavailable": 11,
        "malformed_package": 12,
        "expired_package": 13,
        "invalid_configuration": 14,
    }


def test_package_digest_and_orphaned_citation_ref_refuse_before_render() -> None:
    packages = [_package_document(), _package_document()]
    packages[0]["packageDigest"] = "0" * 64
    orphaned_blocks = cast(list[dict[str, object]], packages[1]["blocks"])
    orphaned_blocks[0]["evidenceRefs"] = ["ev_" + "f" * 64]
    for package in packages:
        completed = _command(
            "inspect",
            "-",
            environment=os.environ.copy(),
            input_text=json.dumps(package),
        )
        assert completed.returncode == 12
        assert completed.stdout == ""
        assert completed.stderr == "context-engine-context: malformed_package\n"


def test_lexically_changed_package_digest_input_refuses_before_render() -> None:
    package = _package_document()
    package["asOf"] = "2099-08-02T12:00:00+00:00"
    package["expiresAt"] = "2099-08-02T12:05:00+00:00"
    evidence = cast(list[dict[str, object]], package["evidence"])[0]
    evidence["authorizationAsOf"] = "2099-08-02T12:00:00+00:00"
    source_acl = cast(dict[str, object], evidence["sourceAclEvidence"])
    source_acl["aclAsOf"] = "2099-08-02T12:00:00+00:00"

    completed = _command(
        "inspect",
        "-",
        "--format",
        "json",
        environment=os.environ.copy(),
        input_text=json.dumps(package),
    )

    assert completed.returncode == 12
    assert completed.stdout == ""
    assert completed.stderr == "context-engine-context: malformed_package\n"


def test_inconsistent_package_lifetime_refuses_before_render() -> None:
    package = _package_document()
    package["ttlSeconds"] = 301
    package.pop("packageDigest")
    package["packageDigest"] = context_package_digest(package)

    completed = _command(
        "inspect",
        "-",
        environment=os.environ.copy(),
        input_text=json.dumps(package),
    )

    assert completed.returncode == 12
    assert completed.stdout == ""
    assert completed.stderr == "context-engine-context: malformed_package\n"


def test_json_empty_refusal_preserves_exact_content_free_package() -> None:
    package = _package_document()
    package["blocks"] = []
    package["evidence"] = []
    package["coverage"] = {
        "status": "empty",
        "reason": "no_authorized_evidence",
    }
    package["budgetUsage"] = {
        "tokens": 0,
        "providerCalls": 0,
        "costMicrounits": 0,
        "elapsedMs": 0,
    }
    package.pop("packageDigest")
    package["packageDigest"] = context_package_digest(package)

    completed = _command(
        "inspect",
        "-",
        "--format",
        "json",
        environment=os.environ.copy(),
        input_text=json.dumps(package),
    )

    assert completed.returncode == 10
    assert json.loads(completed.stdout) == package
    assert completed.stderr == ""


def test_empty_authorized_set_is_explicit_and_not_a_corpus_answer() -> None:
    package = _package_document()
    package["blocks"] = []
    package["evidence"] = []
    package["coverage"] = {
        "status": "empty",
        "reason": "no_authorized_evidence",
    }
    package["budgetUsage"] = {
        "tokens": 0,
        "providerCalls": 0,
        "costMicrounits": 0,
        "elapsedMs": 0,
    }
    package.pop("packageDigest")
    package["packageDigest"] = context_package_digest(package)
    outcome: dict[str, object] = {
        "kind": "resolved",
        "package": package,
        "egressGrant": None,
    }
    with _resolve_server(outcome) as (base_url, _):
        completed = _command(
            "query",
            "Is anything authorized?",
            environment={
                **os.environ,
                "CONTEXT_ENGINE_DOGFOOD_BASE_URL": base_url,
                "CONTEXT_ENGINE_DOGFOOD_SECRET": SECRET,
            },
        )

    assert completed.returncode == 10
    assert completed.stdout == ""
    assert completed.stderr == (
        "context-engine-context: empty_authorized_set\n"
    )


def test_query_json_redacts_live_egress_grant_but_keeps_its_structure(
    tmp_path: Path,
) -> None:
    package = _package_document()
    outcome: dict[str, object] = {
        "kind": "resolved",
        "package": package,
        "egressGrant": {"kind": "model", "value": LIVE_EGRESS_GRANT},
    }
    with _resolve_server(outcome) as (base_url, _):
        completed = _command(
            "query",
            "Do not persist a redeemable capability",
            "--format",
            "json",
            environment={
                **os.environ,
                "CONTEXT_ENGINE_DOGFOOD_BASE_URL": base_url,
                "CONTEXT_ENGINE_DOGFOOD_SECRET": SECRET,
            },
        )

    assert completed.returncode == 0, completed.stderr
    assert LIVE_EGRESS_GRANT not in completed.stdout
    assert LIVE_EGRESS_GRANT not in completed.stderr
    emitted = json.loads(completed.stdout)
    assert emitted["egressGrant"] == {
        "kind": "model",
        "value": REDACTED_EGRESS_GRANT,
    }
    assert emitted["kind"] == "resolved"
    assert emitted["package"] == package

    capture = tmp_path / "redacted-capture.json"
    capture.write_text(completed.stdout, encoding="utf-8")
    assert LIVE_EGRESS_GRANT not in capture.read_text(encoding="utf-8")

    inspected = _command(
        "inspect",
        str(capture),
        "--format",
        "json",
        environment=os.environ.copy(),
    )

    assert inspected.returncode == 0, inspected.stderr
    assert json.loads(inspected.stdout) == package


def test_coverage_refusal_json_never_emits_the_live_egress_grant() -> None:
    package = _package_document()
    package["blocks"] = []
    package["evidence"] = []
    package["coverage"] = {
        "status": "empty",
        "reason": "no_authorized_evidence",
    }
    package["budgetUsage"] = {
        "tokens": 0,
        "providerCalls": 0,
        "costMicrounits": 0,
        "elapsedMs": 0,
    }
    package.pop("packageDigest")
    package["packageDigest"] = context_package_digest(package)
    outcome: dict[str, object] = {
        "kind": "resolved",
        "package": package,
        "egressGrant": {"kind": "channel", "value": "egrc_" + "c" * 64},
    }
    with _resolve_server(outcome) as (base_url, _):
        completed = _command(
            "query",
            "Refuse without handing over a capability",
            "--format",
            "json",
            environment={
                **os.environ,
                "CONTEXT_ENGINE_DOGFOOD_BASE_URL": base_url,
                "CONTEXT_ENGINE_DOGFOOD_SECRET": SECRET,
            },
        )

    assert completed.returncode == 10
    assert "egrc_" + "c" * 64 not in completed.stdout
    emitted = json.loads(completed.stdout)
    assert emitted["egressGrant"] == {
        "kind": "channel",
        "value": REDACTED_EGRESS_GRANT,
    }


def test_captured_request_refusal_inspects_as_the_same_explicit_refusal() -> None:
    refusal = {"kind": "request_not_available", "retryable": False}

    human = _command(
        "inspect",
        "-",
        environment=os.environ.copy(),
        input_text=json.dumps(refusal),
    )
    machine = _command(
        "inspect",
        "-",
        "--format",
        "json",
        environment=os.environ.copy(),
        input_text=json.dumps(refusal),
    )

    assert human.returncode == 10
    assert human.stdout == ""
    assert human.stderr == "context-engine-context: request_not_available\n"
    assert machine.returncode == 10
    assert json.loads(machine.stdout) == refusal
    assert machine.stderr == ""


def test_unknown_capture_envelope_stays_malformed() -> None:
    for capture in (
        {"kind": "not_a_public_outcome"},
        {"kind": "request_not_available", "retryable": True},
        {"kind": "resolved"},
    ):
        completed = _command(
            "inspect",
            "-",
            environment=os.environ.copy(),
            input_text=json.dumps(capture),
        )

        assert completed.returncode == 12
        assert completed.stdout == ""
        assert completed.stderr == "context-engine-context: malformed_package\n"


def test_only_the_exact_redacted_grant_bypasses_the_live_grant_schema() -> None:
    package = _package_document()
    for grant in (
        {"value": REDACTED_EGRESS_GRANT},
        {
            "kind": "model",
            "value": REDACTED_EGRESS_GRANT,
            "leftover": LIVE_EGRESS_GRANT,
        },
        {"kind": "unknown", "value": REDACTED_EGRESS_GRANT},
    ):
        completed = _command(
            "inspect",
            "-",
            environment=os.environ.copy(),
            input_text=json.dumps(
                {"kind": "resolved", "package": package, "egressGrant": grant}
            ),
        )

        assert completed.returncode == 12
        assert completed.stdout == ""
        assert completed.stderr == "context-engine-context: malformed_package\n"
        assert LIVE_EGRESS_GRANT not in completed.stdout + completed.stderr

    accepted = _command(
        "inspect",
        "-",
        "--format",
        "json",
        environment=os.environ.copy(),
        input_text=json.dumps(
            {
                "kind": "resolved",
                "package": package,
                "egressGrant": {
                    "kind": "model",
                    "value": REDACTED_EGRESS_GRANT,
                },
            }
        ),
    )

    assert accepted.returncode == 0, accepted.stderr
    assert json.loads(accepted.stdout) == package


def test_naive_package_instants_refuse_before_any_render() -> None:
    package = _package_document()
    package["asOf"] = "2099-08-02T12:00:00"
    package["expiresAt"] = "2099-08-02T12:05:00"
    evidence = cast(list[dict[str, object]], package["evidence"])[0]
    evidence["authorizationAsOf"] = "2099-08-02T12:00:00"
    package.pop("packageDigest")
    package["packageDigest"] = context_package_digest(package)

    inspected = _command(
        "inspect",
        "-",
        environment=os.environ.copy(),
        input_text=json.dumps(package),
    )
    outcome: dict[str, object] = {
        "kind": "resolved",
        "package": package,
        "egressGrant": None,
    }
    with _resolve_server(outcome) as (base_url, _):
        queried = _command(
            "query",
            "Never reinterpret an instant in the local zone",
            environment={
                **os.environ,
                "CONTEXT_ENGINE_DOGFOOD_BASE_URL": base_url,
                "CONTEXT_ENGINE_DOGFOOD_SECRET": SECRET,
            },
        )

    for completed in (inspected, queried):
        assert completed.returncode == 12
        assert completed.stdout == ""
        assert completed.stderr == "context-engine-context: malformed_package\n"
        assert "Authorized maintainer excerpt" not in completed.stderr


def test_unrepresentable_package_lifetime_refuses_without_traceback() -> None:
    package = _package_document()
    package["ttlSeconds"] = 10**20
    package.pop("packageDigest")
    package["packageDigest"] = context_package_digest(package)

    completed = _command(
        "inspect",
        "-",
        environment=os.environ.copy(),
        input_text=json.dumps(package),
    )

    assert completed.returncode == 12
    assert completed.stdout == ""
    assert completed.stderr == "context-engine-context: malformed_package\n"


def test_deeply_nested_capture_is_malformed_without_traceback() -> None:
    completed = _command(
        "inspect",
        "-",
        environment=os.environ.copy(),
        input_text="[" * 2_000 + "]" * 2_000,
    )

    assert completed.returncode == 12
    assert completed.stdout == ""
    assert completed.stderr == "context-engine-context: malformed_package\n"
    assert "Traceback" not in completed.stderr


def test_inspection_secret_scan_needs_no_transport_configuration() -> None:
    short_secret = "short-local-secret"
    environment = os.environ.copy()
    environment.pop("CONTEXT_ENGINE_DOGFOOD_BASE_URL", None)
    environment["CONTEXT_ENGINE_DOGFOOD_SECRET"] = short_secret
    package = _package_document()

    rendered = _command(
        "inspect",
        "-",
        "--format",
        "json",
        environment=environment,
        input_text=json.dumps(package),
    )

    leaking = _package_document()
    blocks = cast(list[dict[str, object]], leaking["blocks"])
    blocks[0]["text"] = short_secret
    leaking["budgetUsage"] = {
        "tokens": len(short_secret.encode("utf-8")),
        "providerCalls": 0,
        "costMicrounits": 0,
        "elapsedMs": 1,
    }
    leaking.pop("packageDigest")
    leaking["packageDigest"] = context_package_digest(leaking)
    refused = _command(
        "inspect",
        "-",
        environment=environment,
        input_text=json.dumps(leaking),
    )

    assert rendered.returncode == 0, rendered.stderr
    assert json.loads(rendered.stdout) == package
    assert refused.returncode == 14
    assert refused.stdout == ""
    assert refused.stderr == "context-engine-context: invalid_configuration\n"
    assert short_secret not in refused.stderr


def test_partial_stale_or_unavailable_coverage_is_distinguishable() -> None:
    for reason, expected in (
        ("stale_evidence", "stale_evidence"),
        ("source_unavailable", "source_unavailable"),
        ("capability_unsupported", "capability_unsupported"),
    ):
        package = _package_document()
        package["blocks"] = []
        package["evidence"] = []
        package["coverage"] = {"status": "partial", "reason": reason}
        package["gaps"] = [{"category": reason, "retryable": False}]
        package["budgetUsage"] = {
            "tokens": 0,
            "providerCalls": 0,
            "costMicrounits": 0,
            "elapsedMs": 0,
        }
        package.pop("packageDigest")
        package["packageDigest"] = context_package_digest(package)

        completed = _command(
            "inspect",
            "-",
            environment=os.environ.copy(),
            input_text=json.dumps(package),
        )

        assert completed.returncode == 10
        assert completed.stdout == ""
        assert completed.stderr == f"context-engine-context: {expected}\n"
