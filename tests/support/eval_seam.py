"""Test-private loopback stand-in for the tracked evaluation run seam."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any, cast

import pytest

from adapters.http.dogfood import DOGFOOD_SECRET_ENV
from applications.dogfood_evaluation import DOGFOOD_BASE_URL_ENV
from applications.eval_executor import ANSWER_JUDGMENT_SCHEMA_VERSION

SECRET = "dogfood-secret-with-at-least-thirty-two-bytes"
QUERY_PREFIX = "synthetic-query-"
PACKAGE_BINDING: dict[str, object] = {
    "asOf": "2026-07-30T11:59:00Z",
    "audienceDigest": "a" * 64,
    "decisionRef": "dec_synthetic-executed-run",
    "policyEpoch": 3,
    "policySnapshotRef": "policy-snapshot-synthetic",
    "purpose": "context.answer",
    "runRef": "run-synthetic-executed",
}


def case_ref_of(query: str) -> str:
    """Recover the synthetic case ref one fixture query was generated from."""

    return query.removeprefix(QUERY_PREFIX)


def judgment_document(entries: list[dict[str, object]]) -> dict[str, object]:
    """Render blind-judge verdicts that claim nothing the run observes."""

    return {
        "answerJudge": {
            "modelRef": "synthetic-blind-judge-model",
            "profileRef": "synthetic-answer-judge-v1",
        },
        "cases": [
            {
                "blindScore": 2,
                "caseRef": entry["caseRef"],
                "claims": [
                    {
                        "claimRef": claim["claimRef"],
                        "citedEvidence": claim["expectedEvidence"],
                    }
                    for claim in cast(
                        list[dict[str, object]], entry["requiredClaims"]
                    )
                ],
                "criticalContradiction": False,
            }
            for entry in entries
        ],
        "schemaVersion": ANSWER_JUDGMENT_SCHEMA_VERSION,
    }


def evidence(case_ref: str, **overrides: object) -> dict[str, object]:
    """Build one delivered Evidence carrying its complete decision binding."""

    document: dict[str, object] = {
        "evidenceRef": f"ev-{case_ref}",
        "sourceRef": f"synthetic-source-{case_ref}",
        "resourceRef": f"synthetic-resource-{case_ref}",
        "revisionRef": f"synthetic-revision-{case_ref}",
        "fragmentRef": f"synthetic-fragment-{case_ref}",
        "projectedFields": ["body"],
        "sourceAclEvidence": {
            "kind": "mirrored",
            "projectionRef": "projection-synthetic",
            "aclAsOf": "2026-07-30T11:59:00Z",
            "freshnessProfileRef": "freshness-synthetic",
        },
        "citationOpenRef": None,
        "authorizationAsOf": PACKAGE_BINDING["asOf"],
        "decisionRef": PACKAGE_BINDING["decisionRef"],
        "policyEpoch": PACKAGE_BINDING["policyEpoch"],
        "policySnapshotRef": PACKAGE_BINDING["policySnapshotRef"],
        "purpose": PACKAGE_BINDING["purpose"],
        "runRef": PACKAGE_BINDING["runRef"],
    }
    document.update(overrides)
    return document


def block(evidence_ref: str) -> dict[str, object]:
    """Build one authorized block bound to exactly one Evidence ref."""

    return {
        "blockId": f"block-{evidence_ref}",
        "text": "synthetic authorized block text",
        "evidenceRefs": [evidence_ref],
    }


def resolved(
    delivered: list[dict[str, object]],
    blocks: list[dict[str, object]],
    **binding: object,
) -> dict[str, object]:
    """Build one resolve envelope whose coverage matches its delivery."""

    package: dict[str, object] = {
        **PACKAGE_BINDING,
        **binding,
        "blocks": blocks,
        "evidence": delivered,
        "coverage": (
            {"status": "sufficient"}
            if delivered or blocks
            else {"status": "empty", "reason": "no_authorized_evidence"}
        ),
    }
    return {"kind": "resolved", "package": package, "egressGrant": None}


def clean_case(case_ref: str) -> dict[str, object]:
    """Answer one case with exactly its expected Evidence and one block."""

    return resolved([evidence(case_ref)], [block(f"ev-{case_ref}")])


def clean_responder(
    entries: list[dict[str, object]],
) -> Callable[[str], dict[str, object]]:
    """Answer every answerable case cleanly and refuse the unanswerable ones."""

    answerable = {
        cast(str, entry["caseRef"])
        for entry in entries
        if entry["answerability"] == "answerable"
    }

    def respond(query: str) -> dict[str, object]:
        case_ref = case_ref_of(query)
        if case_ref not in answerable:
            return resolved([], [])
        return clean_case(case_ref)

    return respond


@contextmanager
def serving(
    responder: Callable[[str], dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Serve the frozen resolve operation over a real loopback socket."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers["Content-Length"])
            request = json.loads(self.rfile.read(length))
            body = json.dumps(responder(request["need"]["query"])).encode("utf-8")
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
    monkeypatch.setenv(DOGFOOD_SECRET_ENV, SECRET)
    monkeypatch.setenv(
        DOGFOOD_BASE_URL_ENV,
        f"http://127.0.0.1:{server.server_port}",
    )
    try:
        yield
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
