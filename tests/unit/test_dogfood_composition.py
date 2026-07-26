from __future__ import annotations

import logging
from collections.abc import Mapping
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from adapters.http.app import app as default_served_app
from adapters.http.authentication import AuthenticationRejected, DogfoodAuthenticator
from adapters.http.dogfood import (
    DOGFOOD_AGENT_ENV,
    DOGFOOD_APPLICATION_ENV,
    DOGFOOD_BINDING_ENV,
    DOGFOOD_COMPOSITION_ENV,
    DOGFOOD_COMPOSITION_VALUE,
    DOGFOOD_EMBEDDING_PROVIDER_ENV,
    DOGFOOD_EMBEDDING_PROVIDER_VALUE,
    DOGFOOD_MEMBERSHIP_ENV,
    DOGFOOD_MEMBERSHIP_VERSION_ENV,
    DOGFOOD_ORGANIZATION_ENV,
    DOGFOOD_PRINCIPAL_ENV,
    DOGFOOD_SECRET_ENV,
    DOGFOOD_USER_ENV,
    DogfoodConfiguration,
    DogfoodConfigurationUnavailable,
    create_served_app,
)
from applications.api import main as api_main

SECRET = "dogfood-secret-with-at-least-thirty-two-bytes"
ORGANIZATION_ID = UUID("81e18bca-86a1-478a-937d-7675c6fe69b0")
USER_ID = UUID("d3d9893f-82d2-4890-8cb2-4c7e57a56f16")
MEMBERSHIP_ID = UUID("9c9e9f4c-a5ec-4417-9408-0346e1c6c998")


def environment() -> dict[str, str]:
    return {
        DOGFOOD_COMPOSITION_ENV: DOGFOOD_COMPOSITION_VALUE,
        DOGFOOD_SECRET_ENV: SECRET,
        DOGFOOD_ORGANIZATION_ENV: str(ORGANIZATION_ID),
        DOGFOOD_USER_ENV: str(USER_ID),
        DOGFOOD_MEMBERSHIP_ENV: str(MEMBERSHIP_ID),
        DOGFOOD_MEMBERSHIP_VERSION_ENV: "1",
        DOGFOOD_PRINCIPAL_ENV: "principal:file-reader",
        DOGFOOD_AGENT_ENV: "agent:dogfood-local:v1",
        DOGFOOD_APPLICATION_ENV: "application:dogfood-local:v1",
        DOGFOOD_BINDING_ENV: "binding:dogfood-local:v1",
        DOGFOOD_EMBEDDING_PROVIDER_ENV: DOGFOOD_EMBEDDING_PROVIDER_VALUE,
    }


def _load(source: Mapping[str, str] | None = None) -> DogfoodConfiguration:
    return DogfoodConfiguration.load(environment() if source is None else source)


def test_absent_configuration_preserves_the_reject_all_served_composition() -> None:
    client = TestClient(create_served_app({}))

    assert client.get("/health").json()["runtime_delivery"] == "NOT_ACTIVE"
    response = client.post(
        "/v0/resolve",
        headers={
            "Authorization": f"Bearer {SECRET}",
            "X-Context-Request-Id": "dogfood-default-rejects",
        },
        json={"kind": "acquire", "need": {"query": "probe"}},
    )
    assert response.status_code == 401
    assert response.json() == {"code": "authentication_failed"}


def test_module_level_asgi_app_is_always_reject_all() -> None:
    client = TestClient(default_served_app)

    assert client.get("/health").json()["runtime_delivery"] == "NOT_ACTIVE"
    response = client.post(
        "/v0/resolve",
        headers={
            "Authorization": f"Bearer {SECRET}",
            "X-Context-Request-Id": "direct-uvicorn-must-reject",
        },
        json={"kind": "acquire", "need": {"query": "probe"}},
    )
    assert response.status_code == 401


@pytest.mark.parametrize(
    "credential",
    ("wrong-dogfood-secret", SECRET[:-1]),
)
def test_dogfood_secret_rejections_are_generic_and_redacted(
    credential: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    authenticator = DogfoodAuthenticator(
        secret=SECRET,
        authentication=_load().authentication(),
    )

    with caplog.at_level(logging.DEBUG), pytest.raises(AuthenticationRejected):
        authenticator.authenticate(credential)

    assert SECRET not in caplog.text
    assert SECRET not in repr(authenticator)


def test_dogfood_secret_authenticates_only_the_fixed_identity() -> None:
    configuration = _load()
    context = DogfoodAuthenticator(
        secret=SECRET,
        authentication=configuration.authentication(),
    ).authenticate(SECRET)

    assert context.organization_ref == str(ORGANIZATION_ID)
    assert context.user_ref == str(USER_ID)
    assert context.membership_ref == str(MEMBERSHIP_ID)
    assert context.principal_ref == "principal:file-reader"


@pytest.mark.parametrize(
    ("changed_name", "changed_value"),
    (
        (DOGFOOD_SECRET_ENV, "short"),
        (DOGFOOD_MEMBERSHIP_VERSION_ENV, "0"),
        (DOGFOOD_MEMBERSHIP_VERSION_ENV, "not-an-integer"),
        (DOGFOOD_ORGANIZATION_ENV, "not-a-uuid"),
        (DOGFOOD_EMBEDDING_PROVIDER_ENV, "external"),
    ),
)
def test_partial_or_widening_dogfood_configuration_fails_closed(
    changed_name: str,
    changed_value: str,
) -> None:
    source = environment()
    source[changed_name] = changed_value

    with pytest.raises(DogfoodConfigurationUnavailable):
        DogfoodConfiguration.load(source)


def test_missing_required_dogfood_configuration_fails_closed() -> None:
    source = environment()
    del source[DOGFOOD_SECRET_ENV]

    with pytest.raises(DogfoodConfigurationUnavailable):
        DogfoodConfiguration.load(source)


def test_query_digest_key_is_derived_without_exposing_the_dogfood_secret() -> None:
    configuration = _load()

    assert repr(configuration.query_digest_keyring()) == (
        "QueryDigestKeyring(<redacted>)"
    )
    assert SECRET not in repr(configuration)


def test_dogfood_api_refuses_non_loopback_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(DOGFOOD_COMPOSITION_ENV, DOGFOOD_COMPOSITION_VALUE)

    with pytest.raises(SystemExit) as failure:
        api_main(["--host", "0.0.0.0"])

    assert failure.value.code == 2


def test_default_api_host_policy_is_not_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(DOGFOOD_COMPOSITION_ENV, raising=False)
    called: dict[str, object] = {}

    def observe(*args: object, **kwargs: object) -> None:
        called.update(kwargs)

    monkeypatch.setattr("applications.api.uvicorn.run", observe)
    api_main(["--host", "0.0.0.0"])

    assert called["host"] == "0.0.0.0"
