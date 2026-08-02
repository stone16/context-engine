"""Redacted loopback caller for the frozen dogfood HTTP resolve carrier."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, cast
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from adapters.http.contracts import AcquireWire

DOGFOOD_BASE_URL_ENV: Final = "CONTEXT_ENGINE_DOGFOOD_BASE_URL"
DOGFOOD_SECRET_ENV: Final = "CONTEXT_ENGINE_DOGFOOD_SECRET"
MAX_QUERY_CHARACTERS: Final = 4_096
MAX_RESPONSE_BYTES: Final = 16 * 1024 * 1024


class DogfoodEvaluationUnavailable(RuntimeError):
    """Loopback caller configuration, input, or response is unavailable."""


class DogfoodSecretExclusionUnavailable(DogfoodEvaluationUnavailable):
    """The caller cannot keep configured secret material out of its output."""


def _require_exact_text(name: str, value: object, *, maximum: int = 512) -> str:
    if (
        type(value) is not str
        or not value
        or value.isspace()
        or value != value.strip()
        or len(value) > maximum
    ):
        raise DogfoodEvaluationUnavailable(f"{name} is unavailable")
    return value


def _require_opaque_ref(name: str, value: object) -> str:
    result = _require_exact_text(name, value)
    if any(character.isspace() for character in result):
        raise DogfoodEvaluationUnavailable(f"{name} is unavailable")
    return result


def _as_object(value: object, name: str) -> dict[str, object]:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise DogfoodEvaluationUnavailable(f"{name} is unavailable")
    return cast(dict[str, object], value)


@dataclass(frozen=True, slots=True)
class DogfoodHttpConfiguration:
    """Loopback-only destination and redacted bearer secret."""

    base_url: str
    secret: str = field(repr=False)

    def __post_init__(self) -> None:
        base_url = _require_exact_text("dogfood base URL", self.base_url)
        try:
            parsed = urlsplit(base_url)
            port = parsed.port
        except ValueError:
            raise DogfoodEvaluationUnavailable(
                "dogfood caller requires an explicit loopback HTTP URL"
            ) from None
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "::1"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or port is None
        ):
            raise DogfoodEvaluationUnavailable(
                "dogfood caller requires an explicit loopback HTTP URL"
            )
        secret = _require_exact_text(
            "dogfood secret",
            self.secret,
            maximum=16_384,
        )
        if len(secret.encode("utf-8")) < 32:
            raise DogfoodEvaluationUnavailable("dogfood secret is unavailable")

    def reject_secret_material(self, value: object) -> None:
        """Reject any decoded value containing the configured bearer."""

        pending = [value]
        while pending:
            current = pending.pop()
            if type(current) is str:
                if self.secret in current:
                    raise DogfoodSecretExclusionUnavailable(
                        "dogfood secret exclusion is unavailable"
                    )
                continue
            if type(current) is dict:
                document = cast(dict[object, object], current)
                pending.extend(document.keys())
                pending.extend(document.values())
                continue
            if type(current) in {list, tuple}:
                sequence = cast(list[object] | tuple[object, ...], current)
                pending.extend(sequence)

    @classmethod
    def load(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> DogfoodHttpConfiguration:
        source = os.environ if environment is None else environment
        return cls(
            base_url=_require_exact_text(
                "dogfood base URL",
                source.get(DOGFOOD_BASE_URL_ENV),
            ).rstrip("/"),
            secret=_require_exact_text(
                "dogfood secret",
                source.get(DOGFOOD_SECRET_ENV),
                maximum=16_384,
            ),
        )


class DogfoodResolveClient:
    """Minimal plain-HTTP caller of only the frozen resolve operation."""

    __slots__ = ("_configuration",)

    def __init__(self, configuration: DogfoodHttpConfiguration) -> None:
        if type(configuration) is not DogfoodHttpConfiguration:
            raise TypeError("dogfood HTTP configuration is required")
        self._configuration = configuration

    def resolve_acquire(self, *, query: str, request_id: str) -> dict[str, object]:
        """Invoke one Acquire without collapsing a closed refusal outcome."""

        query = _require_exact_text(
            "dogfood query",
            query,
            maximum=MAX_QUERY_CHARACTERS,
        )
        self._configuration.reject_secret_material(query)
        return self.resolve_acquire_document(
            acquire={"kind": "acquire", "need": {"query": query}},
            request_id=request_id,
        )

    def resolve_acquire_document(
        self,
        *,
        acquire: dict[str, object],
        request_id: str,
    ) -> dict[str, object]:
        """Forward one Acquire through the same transport used by MCP."""

        return asyncio.run(
            self.resolve_acquire_document_async(
                acquire=acquire,
                request_id=request_id,
            )
        )

    async def resolve_acquire_document_async(
        self,
        *,
        acquire: dict[str, object],
        request_id: str,
    ) -> dict[str, object]:
        """Forward one Acquire through cancellation-aware loopback HTTP."""

        body, request_id = self._validated_request(
            acquire=acquire,
            request_id=request_id,
        )
        try:
            async with (
                httpx.AsyncClient(
                    follow_redirects=False,
                    timeout=30,
                    trust_env=False,
                ) as client,
                client.stream(
                    "POST",
                    f"{self._configuration.base_url}/v0/resolve",
                    content=body,
                    headers=self._headers(request_id),
                ) as response,
            ):
                response.raise_for_status()
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise DogfoodEvaluationUnavailable(
                            "dogfood resolve response is unavailable"
                        )
            return self._validated_outcome(bytes(raw))
        except DogfoodEvaluationUnavailable:
            raise
        except (
            httpx.HTTPError,
            OSError,
            ValueError,
            UnicodeDecodeError,
            ValidationError,
        ):
            raise DogfoodEvaluationUnavailable(
                "dogfood resolve is unavailable"
            ) from None

    def _validated_request(
        self,
        *,
        acquire: dict[str, object],
        request_id: str,
    ) -> tuple[bytes, str]:
        request_id = _require_opaque_ref("dogfood request_id", request_id)
        try:
            validated_acquire = AcquireWire.model_validate(acquire)
        except ValidationError:
            raise DogfoodEvaluationUnavailable(
                "dogfood Acquire is unavailable"
            ) from None
        acquire_document = validated_acquire.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        )
        self._configuration.reject_secret_material(acquire_document)
        body = json.dumps(
            acquire_document,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return body, request_id

    def _headers(self, request_id: str) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._configuration.secret}",
            "Content-Type": "application/json",
            "X-Context-Request-Id": request_id,
        }

    def _validated_outcome(self, raw: bytes) -> dict[str, object]:
        if len(raw) > MAX_RESPONSE_BYTES:
            raise DogfoodEvaluationUnavailable(
                "dogfood resolve response is unavailable"
            )
        if self._configuration.secret.encode("utf-8") in raw:
            raise DogfoodSecretExclusionUnavailable(
                "dogfood secret exclusion is unavailable"
            )
        outcome = _as_object(json.loads(raw), "dogfood resolve response")
        self._configuration.reject_secret_material(outcome)
        if outcome.get("kind") not in {"resolved", "request_not_available"}:
            raise DogfoodEvaluationUnavailable("dogfood resolve outcome is unavailable")
        return outcome

    def acquire(self, *, query: str, request_id: str) -> dict[str, object]:
        """Invoke one Acquire and require a ContextPackage for evaluation."""

        outcome = self.resolve_acquire(query=query, request_id=request_id)
        package = outcome.get("package")
        if (
            outcome.get("kind") != "resolved"
            or type(package) is not dict
            or type(package.get("blocks")) is not list
            or type(package.get("evidence")) is not list
        ):
            raise DogfoodEvaluationUnavailable(
                "dogfood resolve did not return a ContextPackage"
            )
        return outcome

    def reject_secret_material(self, value: object) -> None:
        """Expose only the configuration's redacted exclusion check."""

        self._configuration.reject_secret_material(value)

    def __repr__(self) -> str:
        return "DogfoodResolveClient(<redacted>)"
