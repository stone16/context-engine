from __future__ import annotations

from fastapi.testclient import TestClient

from adapters.http.app import create_app
from adapters.http.ui_api import RefusingUiApi, UiActor
from tests.integration.test_runtime_authorized_evidence_integration import (
    SeededAuthenticator,
    _new_fixture,
)
from tests.support.ui import authenticate_ui


class _ProfileApi(RefusingUiApi):
    def profiles(self, actor: UiActor) -> dict[str, object]:
        del actor
        return {
            "releaseGeneration": 7,
            "releaseManifestRef": "release:current",
            "contentProfile": {"profileRef": "content-v3", "digest": "1" * 64},
            "indexProfile": {"profileRef": "embedding-v2", "digest": "2" * 64},
            "runtimeProfile": {"profileRef": "runtime-v4", "digest": "3" * 64},
        }


def test_profile_change_surfaces_reembed() -> None:
    fixture = _new_fixture().org_a
    client = TestClient(
        create_app(
            authenticator=SeededAuthenticator(fixture, token="profile-token"),
            ui_bearer_token="profile-token",
            ui_api=_ProfileApi(),
        )
    )
    authenticate_ui(client, "profile-token")

    response = client.post(
        "/ui/profiles",
        content=(
            "profileRef=embedding-v3&"
            f"digest={'4' * 64}"
        ),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 200
    assert "Re-embed required" in response.text
    assert "No profile change was applied" in response.text
    assert "embedding-v2" in response.text
    assert "embedding-v3" in response.text
    assert "Apply" not in response.text
    assert "Confirm" not in response.text
