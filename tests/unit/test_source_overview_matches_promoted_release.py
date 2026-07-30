from __future__ import annotations

from fastapi.testclient import TestClient

from adapters.http.app import create_app
from adapters.http.ui_api import RefusingUiApi, UiActor
from engine.control import ControlOperation, TrustedControlCall
from tests.integration.test_runtime_authorized_evidence_integration import (
    RECEIVED_AT,
    SeededAuthenticator,
    _new_fixture,
)
from tests.support.ui import authenticate_ui, ui_control_authority

CONTROL_TOKEN = "overview-control-token"


class _OverviewApi(RefusingUiApi):
    def __init__(self, promoted_generation: int) -> None:
        self.promoted_generation = promoted_generation

    def overview(
        self, actor: UiActor, control_call: TrustedControlCall
    ) -> dict[str, object]:
        del actor, control_call
        return {
            "releaseGeneration": self.promoted_generation,
            "releaseManifestRef": "release:promoted",
            "sources": [
                {
                    "activeResourceCount": 3,
                    "displayName": "Handbook",
                    "lastSuccessfulAcquisitionAgeSeconds": 17,
                    "refusalCategories": ["invalid_utf8"],
                    "sourceRef": "source:handbook",
                    "status": "refused",
                }
            ],
        }


def test_source_overview_matches_promoted_release_and_surfaces_refusal() -> None:
    promoted_generation = 11
    fixture = _new_fixture().org_a
    control_authority, _ = ui_control_authority(
        organization_id=fixture.organization_id,
        credential=CONTROL_TOKEN,
        operations=frozenset({ControlOperation.READ_SOURCE_PROGRESS}),
        clock=lambda: RECEIVED_AT,
    )
    client = TestClient(
        create_app(
            authenticator=SeededAuthenticator(fixture, token="overview-token"),
            ui_bearer_token="overview-token",
            ui_control_authority=control_authority,
            ui_api=_OverviewApi(promoted_generation),
        )
    )
    authenticate_ui(client, "overview-token")
    response = client.post(
        "/ui/overview",
        content=f"controlCredential={CONTROL_TOKEN}",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 200
    assert f"Release generation {promoted_generation}" in response.text
    assert f"<dt>Generation</dt><dd>{promoted_generation}</dd>" in response.text
    assert "Handbook" in response.text
    assert "<dt>Active Articles</dt><dd>3</dd>" in response.text
    assert "invalid_utf8" in response.text
    assert "No registered Sources" not in response.text
