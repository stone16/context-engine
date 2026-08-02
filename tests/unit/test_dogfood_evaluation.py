from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any, ClassVar, cast

import pytest

import applications.dogfood_evaluation as dogfood_evaluation
from adapters.http.dogfood_client import (
    DOGFOOD_SECRET_ENV,
    DogfoodSecretExclusionUnavailable,
)
from applications.dogfood_evaluation import (
    DEFAULT_GOLDEN_SET_FILENAME,
    DOGFOOD_BASE_URL_ENV,
    GOLDEN_SET_SCHEMA_VERSION,
    DogfoodEvaluationUnavailable,
    DogfoodHttpConfiguration,
    DogfoodResolveClient,
    EvidenceIdentity,
    GoldenSet,
    evaluate_golden_set,
    load_golden_set,
    main,
    reject_secret_retention,
    render_resolve,
)
from engine.learning.golden_storage import GOLDEN_ROOT_ENV

SECRET = "dogfood-secret-with-at-least-thirty-two-bytes"


def _entries() -> list[dict[str, object]]:
    return [
        {
            "caseRef": f"maintainer-{index:02d}",
            "query": f"Maintainer-provided query {index}",
            "expectedEvidence": [
                {
                    "path": f"notes/entry-{index:02d}.md",
                    "sourceRef": "source:file:maintainer",
                    "resourceRef": f"resource:file:{index:064x}",
                    "revisionRef": f"revision:entry-{index:02d}",
                    "fragmentRef": f"fragment:entry-{index:02d}",
                }
            ],
        }
        for index in range(20)
    ]


def _write_golden(path: Path, entries: list[dict[str, object]] | None = None) -> None:
    path.write_text(
        json.dumps(
            {
                "schemaVersion": GOLDEN_SET_SCHEMA_VERSION,
                "name": "maintainer-notes-v0",
                "entries": _entries() if entries is None else entries,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _resolved(identity: EvidenceIdentity) -> dict[str, object]:
    return {
        "kind": "resolved",
        "package": {
            "blocks": [
                {
                    "blockId": "block-1",
                    "text": "Authorized note excerpt",
                    "evidenceRefs": ["ev-1"],
                }
            ],
            "coverage": {"status": "sufficient", "reason": None},
            "evidence": [
                {
                    "evidenceRef": "ev-1",
                    "sourceRef": identity.source_ref,
                    "resourceRef": identity.resource_ref,
                    "revisionRef": identity.revision_ref,
                    "fragmentRef": identity.fragment_ref,
                }
            ],
        },
    }


class RecordingCaller:
    def __init__(self, cases: GoldenSet, missing: str | None = None) -> None:
        self._by_query = {
            case.query: case.expected_evidence[0].identity for case in cases.cases
        }
        self._missing = missing
        self.calls: list[tuple[str, str]] = []

    def acquire(self, *, query: str, request_id: str) -> dict[str, object]:
        self.calls.append((query, request_id))
        identity = self._by_query[query]
        if request_id == self._missing:
            identity = EvidenceIdentity(
                source_ref="source:file:removed",
                resource_ref="resource:file:removed",
                revision_ref="revision:removed",
                fragment_ref="fragment:removed",
            )
        return _resolved(identity)


def test_golden_set_schema_loads_exactly_twenty_maintainer_entries(
    tmp_path: Path,
) -> None:
    path = tmp_path / "golden.json"
    _write_golden(path)

    golden_set = load_golden_set(path)

    assert golden_set.name == "maintainer-notes-v0"
    assert len(golden_set.cases) == 20
    assert len(golden_set.digest) == 64
    assert golden_set.cases[0].expected_evidence[0].path == "notes/entry-00.md"


def test_cli_run_refuses_an_unconfigured_durable_golden_root_without_a_path(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv(GOLDEN_ROOT_ENV, raising=False)

    with pytest.raises(SystemExit) as error:
        main(["run"])

    assert error.value.code == 1
    assert capsys.readouterr().err == (
        "dogfood evaluation unavailable: durable golden set is unavailable\n"
    )


def test_cli_run_resolves_the_default_set_from_the_durable_golden_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    durable_root = tmp_path / "durable"
    durable_root.mkdir()
    golden_path = durable_root / DEFAULT_GOLDEN_SET_FILENAME
    output_path = tmp_path / ".context-engine/dogfood/report.json"
    _write_golden(golden_path)
    golden_set = load_golden_set(golden_path)
    expected_by_query = {
        case.query: case.expected_evidence[0].identity
        for case in golden_set.cases
    }
    monkeypatch.setenv(GOLDEN_ROOT_ENV, str(durable_root))
    monkeypatch.setenv(DOGFOOD_BASE_URL_ENV, "http://127.0.0.1:8000")
    monkeypatch.setenv(DOGFOOD_SECRET_ENV, SECRET)

    def acquire(
        self: DogfoodResolveClient,
        *,
        query: str,
        request_id: str,
    ) -> dict[str, object]:
        del self, request_id
        return _resolved(expected_by_query[query])

    monkeypatch.setattr(dogfood_evaluation.DogfoodResolveClient, "acquire", acquire)

    main(["run", "--output", str(output_path)])

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["goldenSet"]["caseCount"] == 20
    assert report["quality"]["evidenceRecall"] == {
        "hits": 20,
        "totalExpected": 20,
        "value": 1.0,
    }


def test_cli_run_refuses_an_explicit_set_outside_the_durable_golden_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    durable_root = tmp_path / "durable"
    outside_root = tmp_path / "outside"
    durable_root.mkdir()
    outside_root.mkdir()
    outside_path = outside_root / "golden.json"
    _write_golden(outside_path)
    monkeypatch.setenv(GOLDEN_ROOT_ENV, str(durable_root))

    with pytest.raises(SystemExit) as error:
        main(["run", "--golden-set", str(outside_path)])

    assert error.value.code == 1
    assert capsys.readouterr().err == (
        "dogfood evaluation unavailable: durable golden set is unavailable\n"
    )


@pytest.mark.parametrize(
    "mutate",
    (
        lambda document: document.update({"unknown": True}),
        lambda document: document["entries"].pop(),
        lambda document: document["entries"][0].update(
            {"query": "invented", "unknown": True}
        ),
        lambda document: document["entries"][0]["expectedEvidence"][0].update(
            {"path": "../secret.md"}
        ),
    ),
)
def test_golden_set_rejects_partial_or_open_documents(
    tmp_path: Path,
    mutate: object,
) -> None:
    document: dict[str, object] = {
        "schemaVersion": GOLDEN_SET_SCHEMA_VERSION,
        "name": "maintainer-notes-v0",
        "entries": _entries(),
    }
    mutate(document)  # type: ignore[operator]
    path = tmp_path / "golden.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(DogfoodEvaluationUnavailable):
        load_golden_set(path)


def test_golden_set_rejects_duplicate_lineage_with_a_different_path(
    tmp_path: Path,
) -> None:
    entries = _entries()
    expected = cast(list[dict[str, object]], entries[0]["expectedEvidence"])
    duplicate = dict(expected[0])
    duplicate["path"] = "notes/duplicate-human-label.md"
    expected.append(duplicate)
    path = tmp_path / "golden.json"
    _write_golden(path, entries)

    with pytest.raises(DogfoodEvaluationUnavailable, match="must be unique"):
        load_golden_set(path)


def test_runner_reports_recall_and_visible_removed_source_regression(
    tmp_path: Path,
) -> None:
    path = tmp_path / "golden.json"
    _write_golden(path)
    golden_set = load_golden_set(path)
    healthy = RecordingCaller(golden_set)

    healthy_report = evaluate_golden_set(golden_set, healthy)
    repeated_report = evaluate_golden_set(
        golden_set,
        RecordingCaller(golden_set),
    )

    assert healthy_report["quality"] == {
        "casePassRate": 1.0,
        "measured": True,
        "evidenceRecall": {
            "hits": 20,
            "totalExpected": 20,
            "value": 1.0,
        },
        "status": "measured",
    }
    assert repeated_report == healthy_report
    assert healthy_report["reliability"] == {"status": "not-evaluated"}
    assert healthy_report["budget"] == {"status": "not-evaluated"}
    assert healthy.calls[0][1] == "dogfood-eval-maintainer-00"

    regressed = RecordingCaller(
        golden_set,
        missing="dogfood-eval-maintainer-07",
    )
    regressed_report = evaluate_golden_set(golden_set, regressed)
    quality = regressed_report["quality"]
    assert isinstance(quality, dict)
    assert quality["casePassRate"] == 0.95
    recall = quality["evidenceRecall"]
    assert isinstance(recall, dict)
    assert recall["value"] == 0.95
    cases = regressed_report["cases"]
    assert isinstance(cases, list)
    assert cases[7]["status"] == "miss"


def test_evidence_recall_uses_the_complete_unranked_package(tmp_path: Path) -> None:
    path = tmp_path / "golden.json"
    _write_golden(path)
    golden_set = load_golden_set(path)
    by_query = {
        case.query: case.expected_evidence[0].identity for case in golden_set.cases
    }

    class CompletePackageCaller:
        def acquire(self, *, query: str, request_id: str) -> dict[str, object]:
            del request_id
            expected = by_query[query]
            distractors = [
                EvidenceIdentity(
                    source_ref="source:file:distractor",
                    resource_ref=f"resource:file:distractor-{index:02d}",
                    revision_ref=f"revision:distractor-{index:02d}",
                    fragment_ref=f"fragment:distractor-{index:02d}",
                )
                for index in range(10)
            ]
            identities = [*distractors, expected]
            return {
                "kind": "resolved",
                "package": {
                    "blocks": [],
                    "coverage": {"status": "sufficient", "reason": None},
                    "evidence": [
                        {
                            "evidenceRef": f"ev-{index:02d}",
                            **identity.public_document(),
                        }
                        for index, identity in enumerate(identities)
                    ],
                },
            }

    report = evaluate_golden_set(golden_set, CompletePackageCaller())

    quality = report["quality"]
    assert isinstance(quality, dict)
    assert quality["evidenceRecall"] == {
        "hits": 20,
        "totalExpected": 20,
        "value": 1.0,
    }


def test_real_caller_render_consumes_only_context_package_evidence() -> None:
    outcome = _resolved(
        EvidenceIdentity(
            source_ref="source:file:maintainer",
            resource_ref="resource:file:authorized",
            revision_ref="revision:current",
            fragment_ref="fragment:authorized",
        )
    )

    rendered = render_resolve(outcome)

    assert "Authorized note excerpt" in rendered
    assert "resource=resource:file:authorized" in rendered
    assert "fragment=fragment:authorized" in rendered


def test_caller_is_loopback_only_and_redacts_secret() -> None:
    configuration = DogfoodHttpConfiguration.load(
        {
            DOGFOOD_BASE_URL_ENV: "http://127.0.0.1:8000",
            DOGFOOD_SECRET_ENV: SECRET,
        }
    )
    caller = DogfoodResolveClient(configuration)

    assert SECRET not in repr(configuration)
    assert SECRET not in repr(caller)
    with pytest.raises(DogfoodEvaluationUnavailable, match="loopback"):
        DogfoodHttpConfiguration(
            base_url="https://dogfood.example.com:443",
            secret=SECRET,
        )
    with pytest.raises(DogfoodEvaluationUnavailable, match="secret"):
        DogfoodHttpConfiguration(
            base_url="http://127.0.0.1:8000",
            secret="too-short",
        )


def test_configured_secret_is_refused_from_golden_input(tmp_path: Path) -> None:
    entries = _entries()
    entries[0]["query"] = f"Never retain {SECRET} in evaluation input"
    path = tmp_path / "golden.json"
    _write_golden(path, entries)
    golden_set = load_golden_set(path)
    configuration = DogfoodHttpConfiguration(
        base_url="http://127.0.0.1:8000",
        secret=SECRET,
    )

    with pytest.raises(DogfoodEvaluationUnavailable, match="secret material"):
        reject_secret_retention(configuration, golden_set)


def test_configured_secret_is_refused_from_golden_case_ref(tmp_path: Path) -> None:
    entries = _entries()
    entries[0]["caseRef"] = SECRET
    path = tmp_path / "golden.json"
    _write_golden(path, entries)
    golden_set = load_golden_set(path)
    configuration = DogfoodHttpConfiguration(
        base_url="http://127.0.0.1:8000",
        secret=SECRET,
    )

    with pytest.raises(DogfoodEvaluationUnavailable, match="secret material"):
        reject_secret_retention(configuration, golden_set)


@pytest.mark.security_evidence(id="MCP-HTTP-PROXY-215", layer="runtime")
def test_plain_http_caller_uses_only_frozen_resolve_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("no_proxy", "")
    monkeypatch.setenv("NO_PROXY", "")
    observed: dict[str, object] = {}
    response = _resolved(
        EvidenceIdentity(
            source_ref="source:file:maintainer",
            resource_ref="resource:file:authorized",
            revision_ref="revision:current",
            fragment_ref="fragment:authorized",
        )
    )

    class Handler(BaseHTTPRequestHandler):
        response_document: ClassVar[dict[str, object]] = response

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers["Content-Length"])
            observed.update(
                {
                    "authorization": self.headers["Authorization"],
                    "body": json.loads(self.rfile.read(length)),
                    "path": self.path,
                    "request_id": self.headers["X-Context-Request-Id"],
                }
            )
            body = json.dumps(self.response_document).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        caller = DogfoodResolveClient(
            DogfoodHttpConfiguration(
                base_url=f"http://127.0.0.1:{server.server_port}",
                secret=SECRET,
            )
        )

        outcome = caller.acquire(
            query="A maintainer-provided query",
            request_id="maintainer-query-1",
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    assert outcome == response
    assert observed == {
        "authorization": f"Bearer {SECRET}",
        "body": {
            "kind": "acquire",
            "need": {"query": "A maintainer-provided query"},
        },
        "path": "/v0/resolve",
        "request_id": "maintainer-query-1",
    }


@pytest.mark.security_evidence(id="MCP-HTTP-REDIRECT-215", layer="runtime")
def test_plain_http_caller_refuses_redirect_before_forwarding_secret() -> None:
    observed: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            observed.append(self.path)
            self.send_response(307)
            self.send_header("Location", "/credential-leak-target")
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        caller = DogfoodResolveClient(
            DogfoodHttpConfiguration(
                base_url=f"http://127.0.0.1:{server.server_port}",
                secret=SECRET,
            )
        )
        with pytest.raises(DogfoodEvaluationUnavailable, match="resolve"):
            caller.resolve_acquire(query="redirect probe", request_id="redirect-1")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    assert observed == ["/v0/resolve"]


@pytest.mark.security_evidence(id="MCP-HTTP-SECRET-215", layer="runtime")
def test_plain_http_caller_refuses_secret_material_in_raw_response() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            body = json.dumps(
                {
                    "kind": "request_not_available",
                    "retryable": False,
                    "unexpected": SECRET,
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        caller = DogfoodResolveClient(
            DogfoodHttpConfiguration(
                base_url=f"http://127.0.0.1:{server.server_port}",
                secret=SECRET,
            )
        )
        with pytest.raises(DogfoodSecretExclusionUnavailable):
            caller.resolve_acquire(query="secret probe", request_id="secret-1")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
