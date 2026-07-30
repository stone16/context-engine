from __future__ import annotations

import ast
from pathlib import Path

from fastapi.testclient import TestClient

from adapters.http.app import create_app
from adapters.http.ui_api import FeedbackCapture, RefusingUiApi, UiActor
from tests.integration.test_runtime_authorized_evidence_integration import (
    SeededAuthenticator,
    _new_fixture,
)
from tests.support.ui import authenticate_ui

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class _FeedbackApi(RefusingUiApi):
    def __init__(self) -> None:
        self.captured: list[FeedbackCapture] = []

    def capture_feedback(
        self,
        actor: UiActor,
        feedback: FeedbackCapture,
    ) -> dict[str, object]:
        del actor
        self.captured.append(feedback)
        return {"feedbackRef": "feedback_" + "5" * 64, "state": "recorded"}


def test_feedback_has_no_publication_authority() -> None:
    fixture = _new_fixture().org_a
    api = _FeedbackApi()
    client = TestClient(
        create_app(
            authenticator=SeededAuthenticator(fixture, token="feedback-token"),
            ui_bearer_token="feedback-token",
            ui_api=api,
        )
    )
    authenticate_ui(client, "feedback-token")
    response = client.post(
        "/ui/feedback",
        content="runRef=run_authorized&rating=helpful&note=Clear+lineage",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 200
    assert "Feedback recorded" in response.text
    assert api.captured == [
        FeedbackCapture(
            run_ref="run_authorized",
            rating="helpful",
            note="Clear lineage",
        )
    ]

    forbidden = {"activate", "promote", "publish", "rollback"}
    for root in (REPOSITORY_ROOT / "ui", REPOSITORY_ROOT / "adapters" / "http"):
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            feedback_owners = {
                node.name.casefold()
                for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                and "feedback" in node.name.casefold()
            }
            assert not feedback_owners.intersection(forbidden), path
