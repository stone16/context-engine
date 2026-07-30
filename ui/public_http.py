"""In-process HTTP client for the API's public wire seam."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final
from uuid import uuid4

import httpx
from fastapi import Request

PUBLIC_RESOLVE_PATH: Final = "/v0/resolve"
MAX_PUBLIC_RESPONSE_BYTES: Final = 1_048_576


@dataclass(frozen=True, slots=True)
class PublicHttpRefusal:
    """Tenant-safe failure category; response bodies are deliberately absent."""

    category: str
    status_code: int


type PublicHttpOutcome = dict[str, object] | PublicHttpRefusal


async def request_public_json(
    request: Request,
    *,
    bearer_token: str | None,
    method: str,
    path: str,
    body: dict[str, object] | None = None,
) -> PublicHttpOutcome:
    """Call one public JSON carrier through the mounted ASGI application."""

    if bearer_token is None:
        return PublicHttpRefusal("session_unavailable", 401)
    transport = httpx.ASGITransport(app=request.app)
    request_id = f"ui-{uuid4().hex}"
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url=str(request.base_url),
            timeout=10.0,
        ) as client:
            response = await client.request(
                method,
                path,
                headers={
                    "Authorization": f"Bearer {bearer_token}",
                    "Content-Type": "application/json",
                    "X-Context-Request-Id": request_id,
                },
                json=body,
            )
    except httpx.HTTPError:
        return PublicHttpRefusal("provider_unavailable", 503)
    if len(response.content) > MAX_PUBLIC_RESPONSE_BYTES:
        return PublicHttpRefusal("provider_unavailable", 503)
    if response.status_code == 401:
        return PublicHttpRefusal("session_unavailable", 401)
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
