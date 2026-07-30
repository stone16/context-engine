"""In-process HTTP client for the API's public wire seam."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, cast
from uuid import uuid4

import httpx
from fastapi import Request

PUBLIC_RESOLVE_PATH: Final = "/v0/resolve"
MAX_PUBLIC_RESPONSE_BYTES: Final = 1_048_576
UI_SESSION_COOKIE: Final = "context_engine_ui_session"
UI_SESSION_TTL: Final = timedelta(minutes=15)
_UI_SESSION_DOMAIN: Final = b"context-engine.ui-session.v1\x00"


@dataclass(frozen=True, slots=True)
class PublicHttpRefusal:
    """Tenant-safe failure category; response bodies are deliberately absent."""

    category: str
    status_code: int


type PublicHttpOutcome = dict[str, object] | PublicHttpRefusal


def issue_ui_session(
    bearer_token: str,
    *,
    now: datetime | None = None,
) -> str:
    """Issue a short-lived browser proof only after explicit credential entry."""

    issued_at = _utc_now(now)
    payload = json.dumps(
        {
            "expiresAt": int((issued_at + UI_SESSION_TTL).timestamp()),
            "issuedAt": int(issued_at.timestamp()),
            "nonce": uuid4().hex,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    encoded = _encode(payload)
    signature = hmac.new(_session_key(bearer_token), payload, hashlib.sha256).digest()
    return f"{encoded}.{_encode(signature)}"


def ui_session_is_active(
    request: Request,
    *,
    bearer_token: str | None,
    now: datetime | None = None,
) -> bool:
    """Validate the request-scoped browser proof without exposing the credential."""

    if bearer_token is None:
        return False
    value = request.cookies.get(UI_SESSION_COOKIE)
    if type(value) is not str:
        return False
    try:
        encoded_payload, encoded_signature = value.split(".", 1)
        payload = _decode(encoded_payload)
        signature = _decode(encoded_signature)
        expected = hmac.new(
            _session_key(bearer_token), payload, hashlib.sha256
        ).digest()
        document = json.loads(payload)
        current = int(_utc_now(now).timestamp())
        if (
            not hmac.compare_digest(signature, expected)
            or type(document) is not dict
            or type(document.get("issuedAt")) is not int
            or type(document.get("expiresAt")) is not int
            or type(document.get("nonce")) is not str
            or len(cast(str, document["nonce"])) != 32
            or cast(int, document["issuedAt"]) > current
            or current >= cast(int, document["expiresAt"])
            or cast(int, document["expiresAt"])
            - cast(int, document["issuedAt"])
            != int(UI_SESSION_TTL.total_seconds())
        ):
            return False
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False
    return True


async def request_public_json(
    request: Request,
    *,
    bearer_token: str | None,
    method: str,
    path: str,
    body: dict[str, object] | None = None,
    control_credential: str | None = None,
) -> PublicHttpOutcome:
    """Call one public JSON carrier through the mounted ASGI application."""

    if not ui_session_is_active(request, bearer_token=bearer_token):
        return PublicHttpRefusal("session_unavailable", 401)
    transport = httpx.ASGITransport(app=request.app)
    request_id = f"ui-{uuid4().hex}"
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url=str(request.base_url),
            timeout=10.0,
        ) as client:
            headers = {
                "Authorization": f"Bearer {bearer_token}",
                "Content-Type": "application/json",
                "X-Context-Request-Id": request_id,
            }
            if control_credential is not None:
                headers["X-Context-Control-Credential"] = control_credential
            response = await client.request(
                method,
                path,
                headers=headers,
                json=body,
            )
    except httpx.HTTPError:
        return PublicHttpRefusal("provider_unavailable", 503)
    if len(response.content) > MAX_PUBLIC_RESPONSE_BYTES:
        return PublicHttpRefusal("provider_unavailable", 503)
    if response.status_code == 401:
        return PublicHttpRefusal(
            (
                "control_authority_unavailable"
                if control_credential is not None
                else "session_unavailable"
            ),
            401,
        )
    if response.status_code == 503:
        return PublicHttpRefusal("provider_unavailable", 503)
    if response.status_code != 200:
        return PublicHttpRefusal("request_unavailable", 503)
    try:
        document = response.json()
    except ValueError:
        return PublicHttpRefusal("provider_unavailable", 503)
    if type(document) is not dict:
        return PublicHttpRefusal("provider_unavailable", 503)
    return document


async def resolve_query(
    request: Request,
    *,
    bearer_token: str | None,
    query: str,
) -> PublicHttpOutcome:
    """Resolve through the mounted app's public HTTP carrier, never internals."""

    return await request_public_json(
        request,
        bearer_token=bearer_token,
        method="POST",
        path=PUBLIC_RESOLVE_PATH,
        body={"kind": "acquire", "need": {"query": query}},
    )


async def open_citation(
    request: Request,
    *,
    bearer_token: str | None,
    citation_open_ref: str,
) -> PublicHttpOutcome:
    """Resolve one citation locator through the same authenticated HTTP carrier."""

    return await request_public_json(
        request,
        bearer_token=bearer_token,
        method="POST",
        path=PUBLIC_RESOLVE_PATH,
        body={"kind": "open_citation", "citationOpenRef": citation_open_ref},
    )


def _session_key(bearer_token: str) -> bytes:
    if (
        type(bearer_token) is not str
        or not bearer_token
        or bearer_token.isspace()
    ):
        raise ValueError("UI session credential is invalid")
    return hashlib.sha256(
        _UI_SESSION_DOMAIN + bearer_token.encode("utf-8")
    ).digest()


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    try:
        padding = "=" * (-len(value) % 4)
        return base64.b64decode(
            value + padding,
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, UnicodeEncodeError):
        raise ValueError from None


def _utc_now(value: datetime | None) -> datetime:
    current = datetime.now(UTC) if value is None else value
    if (
        type(current) is not datetime
        or current.tzinfo is None
        or current.utcoffset() != timedelta(0)
    ):
        raise ValueError("UI session clock is invalid")
    return current
