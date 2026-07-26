from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any, ClassVar, cast

import pytest

from adapters.http.dogfood import DOGFOOD_SECRET_ENV
from applications.dogfood_evaluation import (
    DOGFOOD_BASE_URL_ENV,
    GOLDEN_SET_SCHEMA_VERSION,
    DogfoodEvaluationUnavailable,
    DogfoodHttpConfiguration,
    DogfoodResolveClient,
    EvidenceIdentity,
    GoldenSet,
    evaluate_golden_set,
    load_golden_set,
    render_resolve,
)

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
        configuration.reject_secret_retention(golden_set)


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
