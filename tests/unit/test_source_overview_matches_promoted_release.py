from __future__ import annotations

from fastapi.testclient import TestClient

from adapters.http.app import create_app
from adapters.http.ui_api import RefusingUiApi, UiActor
from tests.integration.test_runtime_authorized_evidence_integration import (
    SeededAuthenticator,
    _new_fixture,
)


class _OverviewApi(RefusingUiApi):
    def __init__(self, promoted_generation: int) -> None:
        self.promoted_generation = promoted_generation

    def overview(self, actor: UiActor) -> dict[str, object]:
        del actor
        return {
            "releaseGeneration": self.promoted_generation,
            "releaseManifestRef": "release:promoted",
            "sources": [
                {
                    "activeResourceCount": 3,
                    "displayName": "Handbook",
                    "refusalCategories": ["invalid_utf8"],
                    "sourceRef": "source:handbook",
                    "status": "refused",
                }
            ],
        }


def test_source_overview_matches_promoted_release_and_surfaces_refusal() -> None:
    promoted_generation = 11
    fixture = _new_fixture().org_a
    response = TestClient(
        create_app(
            authenticator=SeededAuthenticator(fixture, token="overview-token"),
            ui_bearer_token="overview-token",
            ui_api=_OverviewApi(promoted_generation),
        )
    ).get("/ui")

    assert response.status_code == 200
    assert f"Release generation {promoted_generation}" in response.text
    assert f"<dt>Generation</dt><dd>{promoted_generation}</dd>" in response.text
    assert "Handbook" in response.text
    assert "<dt>Active Articles</dt><dd>3</dd>" in response.text
    assert "invalid_utf8" in response.text
    assert "No registered Sources" not in response.text
