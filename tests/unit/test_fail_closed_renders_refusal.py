from __future__ import annotations

from datetime import UTC, datetime
from importlib.metadata import version

import pytest
from fastapi.testclient import TestClient

from adapters.http.app import create_app
from tests.integration.test_runtime_authorized_evidence_integration import (
    SeededAuthenticator,
    _new_fixture,
)
from tests.support.ui import authenticate_ui
from ui.public_http import UI_SESSION_COOKIE, issue_ui_session


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


def test_rejected_public_identity_renders_the_same_non_enumerating_refusal() -> None:
    client = TestClient(create_app(ui_bearer_token="expired-session"))
    authenticate_ui(client, "expired-session")
    response = client.post(
        "/ui/ask",
        content="query=show+context",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 401
    assert "Request refused" in response.text
    assert "session_unavailable" in response.text
    assert "expired-session" not in response.text


def test_login_issues_only_a_short_lived_browser_proof() -> None:
    credential = "explicit-browser-login"
    fixture = _new_fixture().org_a
    client = TestClient(
        create_app(
            authenticator=SeededAuthenticator(fixture, token=credential),
            ui_bearer_token=credential,
        )
    )

    login = client.post(
        "/ui/login",
        content=f"credential={credential}&next=/ui",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )

    assert login.status_code == 303
    assert login.headers["location"] == "/ui"
    cookie = login.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert credential not in cookie
    assert client.get("/ui").status_code == 200


def test_expired_or_tampered_session_renders_a_non_enumerating_refusal() -> None:
    credential = "expired-browser-proof"
    client = TestClient(create_app(ui_bearer_token=credential))
    expired = issue_ui_session(
        credential,
        now=datetime(2020, 1, 1, tzinfo=UTC),
    )

    for proof in (expired, f"{expired}tampered"):
        client.cookies.set(UI_SESSION_COOKIE, proof, path="/ui")
        response = client.get("/ui")
        assert response.status_code == 401
        assert "session_unavailable" in response.text
        assert credential not in response.text
        assert proof not in response.text


def test_missing_session_refuses_every_route_load() -> None:
    client = TestClient(create_app(ui_bearer_token="configured-but-not-present"))

    for path in (
        "/ui",
        "/ui/ask",
        "/ui/import",
        "/ui/hit-test",
        "/ui/articles",
        "/ui/profiles",
        "/ui/feedback",
    ):
        response = client.get(path)
        assert response.status_code == 401, path
        assert "session_unavailable" in response.text, path
        assert "No authorized evidence" not in response.text, path


def test_application_shell_surfaces_the_package_build_identity() -> None:
    response = TestClient(create_app()).get("/ui/login")

    assert response.status_code == 200
    assert f"Build <code>{version('context-engine')}</code>" in response.text


@pytest.mark.parametrize(
    ("path", "content", "safe_values", "secret_values"),
    (
        (
            "/ui/import/preview",
            "sourceRef=11111111-1111-4111-8111-111111111111"
            "&path=docs%2Fguide.md&controlCredential=import-control-secret",
            ("Source", "11111111-1111-4111-8111-111111111111", "Path", "docs/guide.md"),
            ("import-control-secret",),
        ),
        (
            "/ui/articles/preview",
            "resourceRef=article%3Ahandbook&policyKind=groups"
            "&groupRefs=group%3Aeng%2Cgroup%3Aops"
            "&controlCredential=article-control-secret",
            (
                "Article",
                "article:handbook",
                "Policy",
                "groups",
                "Groups",
                "group:eng, group:ops",
            ),
            ("article-control-secret",),
        ),
        (
            "/ui/profiles",
            "profileRef=embedding%3Aproposed&digest=" + "a" * 64,
            ("Profile", "embedding:proposed", "Digest", "a" * 64),
            (),
        ),
        (
            "/ui/feedback",
            "runRef=run%3Aauthorized&rating=not_helpful&note=private+operator+note",
            ("ContextRun", "run:authorized", "Rating", "not helpful"),
            ("private operator note",),
        ),
    ),
)
def test_safe_non_secret_form_values_survive_refusal(
    path: str,
    content: str,
    safe_values: tuple[str, ...],
    secret_values: tuple[str, ...],
) -> None:
    credential = "configured-session"
    client = TestClient(create_app(ui_bearer_token=credential))
    authenticate_ui(client, credential)

    response = client.post(
        path,
        content=content,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code in {401, 403}
    assert "Submitted non-secret input" in response.text
    for value in safe_values:
        assert value in response.text
    for value in (*secret_values, credential):
        assert value not in response.text
