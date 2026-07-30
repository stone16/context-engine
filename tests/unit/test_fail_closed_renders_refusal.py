from __future__ import annotations

from fastapi.testclient import TestClient

from adapters.http.app import create_app


def test_fail_closed_renders_refusal() -> None:
    response = TestClient(create_app()).post(
        "/ui/ask",
        content="query=show+context",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 401
    assert "Request refused" in response.text
    assert "session_unavailable" in response.text
    assert "No answer" not in response.text
    assert "No authorized evidence" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_expired_session_renders_the_same_non_enumerating_refusal() -> None:
    response = TestClient(create_app(ui_bearer_token="expired-session")).post(
        "/ui/ask",
        content="query=show+context",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 401
    assert "Request refused" in response.text
    assert "session_unavailable" in response.text
    assert "expired-session" not in response.text
