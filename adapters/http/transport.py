"""Versioned HTTP resource limits enforced before resolve body parsing."""

from dataclasses import dataclass
from typing import Final

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestBodyLimitExceeded(Exception):
    """The resolve body exceeded its active transport profile."""


class JsonNestingLimitExceeded(ValueError):
    """The parsed JSON document exceeded its active nesting profile."""


@dataclass(frozen=True, slots=True)
class HttpTransportProfile:
    """Versioned ceilings for work performed before authentication."""

    max_resolve_body_bytes: int
    max_json_nesting_depth: int

    def __post_init__(self) -> None:
        limits = (self.max_resolve_body_bytes, self.max_json_nesting_depth)
        if any(type(limit) is not int or limit <= 0 for limit in limits):
            raise ValueError("HTTP transport limits must be positive integers")


HTTP_TRANSPORT_PROFILE_V1: Final = HttpTransportProfile(
    max_resolve_body_bytes=64 * 1024,
    max_json_nesting_depth=16,
)


class ResolveBodyLimitMiddleware:
    """Bound resolve buffering at the ASGI receive boundary."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        profile: HttpTransportProfile,
        resolve_path: str,
        invalid_response: dict[str, str],
    ) -> None:
        self._app = app
        self._profile = profile
        self._resolve_path = resolve_path
        self._invalid_response = invalid_response

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http" or not self._is_resolve_request(scope):
            await self._app(scope, receive, send)
            return

        content_lengths = [
            value
            for name, value in scope["headers"]
            if name.lower() == b"content-length"
        ]
        if content_lengths:
            if len(content_lengths) != 1:
                await self._reject(scope, receive, send)
                return
            try:
                declared_length = int(content_lengths[0])
            except ValueError:
                await self._reject(scope, receive, send)
                return
            if (
                declared_length < 0
                or declared_length > self._profile.max_resolve_body_bytes
            ):
                await self._reject(scope, receive, send)
                return

        received_bytes = 0
        response_started = False

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        async def bounded_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self._profile.max_resolve_body_bytes:
                    raise RequestBodyLimitExceeded
            return message

        try:
            await self._app(scope, bounded_receive, tracked_send)
        except RequestBodyLimitExceeded:
            if response_started:
                raise
            await self._reject(scope, receive, send)

    def _is_resolve_request(self, scope: Scope) -> bool:
        return (
            scope.get("method") == "POST"
            and _route_relative_path(scope) == self._resolve_path
        )

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        await JSONResponse(self._invalid_response, status_code=400)(
            scope,
            receive,
            send,
        )


def _route_relative_path(scope: Scope) -> str:
    """Remove a valid ASGI root path using Starlette routing semantics."""

    path: str = scope["path"]
    root_path_value = scope.get("root_path")
    root_path = root_path_value if isinstance(root_path_value, str) else ""
    if not root_path or not path.startswith(root_path):
        return path
    if path == root_path:
        return ""
    if path[len(root_path)] == "/":
        return path[len(root_path) :]
    return path


def enforce_json_nesting(document: object, *, maximum_depth: int) -> None:
    """Reject container nesting beyond a profile without recursive traversal."""

    if not isinstance(document, dict | list):
        return
    pending: list[tuple[object, int]] = [(document, 1)]
    while pending:
        value, depth = pending.pop()
        if depth > maximum_depth:
            raise JsonNestingLimitExceeded
        children: object
        if isinstance(value, dict):
            children = value.values()
        elif isinstance(value, list):
            children = value
        else:
            continue
        pending.extend(
            (child, depth + 1)
            for child in children
            if isinstance(child, dict | list)
        )
