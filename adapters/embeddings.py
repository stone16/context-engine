"""Network-free and external adapters for the Supply embedding seam."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, field
from hashlib import shake_256
from math import sqrt
from typing import IO, BinaryIO, cast
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from engine.supply.embeddings import (
    CONTEXT_FRAGMENT_EMBEDDING_DIMENSION,
    EmbeddingProfile,
    EmbeddingProviderUnavailable,
    EmbeddingVector,
    validate_embedding_batch,
)

_MAX_EXTERNAL_RESPONSE_BYTES = 64 * 1024 * 1024
_DEFAULT_TIMEOUT_SECONDS = 30.0
EmbeddingTransport = Callable[[Request, float, int], bytes]


class _RejectRedirectHandler(HTTPRedirectHandler):
    """Keep the configured endpoint as the only bearer-credential recipient."""

    def redirect_request(
        self,
        request: Request,
        fp: IO[bytes],
        code: int,
        message: str,
        headers: object,
        new_url: str,
    ) -> Request:
        del message, new_url
        raise HTTPError(
            request.full_url,
            code,
            "Embedding redirect is unavailable",
            headers,  # type: ignore[arg-type]
            fp,
        )


@dataclass(frozen=True, slots=True)
class ExternalEmbeddingConfiguration:
    """Environment-derived external provider configuration."""

    endpoint: str = field(repr=False)
    model: str
    api_key: str = field(repr=False)
    dimension: int
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        parsed = urlsplit(self.endpoint)
        if (
            type(self.endpoint) is not str
            or not self.endpoint
            or self.endpoint != self.endpoint.strip()
            or parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or bool(parsed.query)
            or bool(parsed.fragment)
            or type(self.model) is not str
            or not self.model
            or self.model != self.model.strip()
            or type(self.api_key) is not str
            or not self.api_key
            or self.api_key != self.api_key.strip()
            or type(self.timeout_seconds) not in {int, float}
            or not 0 < float(self.timeout_seconds) <= 120
        ):
            raise ValueError("Embedding configuration is not available")
        EmbeddingProfile(self.dimension)


def _default_transport(request: Request, timeout: float, maximum_bytes: int) -> bytes:
    with closing(
        cast(
            BinaryIO,
            build_opener(_RejectRedirectHandler()).open(  # noqa: S310
                request,
                timeout=timeout,
            ),
        )
    ) as response:
        payload = response.read(maximum_bytes + 1)
    if len(payload) > maximum_bytes:
        raise OSError("embedding response exceeded the configured bound")
    return payload


class ExternalEmbeddingProvider:
    """Call one environment-configured JSON embedding endpoint."""

    __slots__ = ("_configuration", "_transport")

    def __init__(
        self,
        configuration: ExternalEmbeddingConfiguration,
        *,
        transport: EmbeddingTransport = _default_transport,
    ) -> None:
        if type(configuration) is not ExternalEmbeddingConfiguration:
            raise TypeError("External embedding configuration is required")
        if not callable(transport):
            raise TypeError("External embedding transport is required")
        self._configuration = configuration
        self._transport = transport

    @property
    def profile(self) -> EmbeddingProfile:
        return EmbeddingProfile(self._configuration.dimension)

    def embed(self, inputs: tuple[str, ...]) -> tuple[EmbeddingVector, ...]:
        if (
            type(inputs) is not tuple
            or not inputs
            or any(type(value) is not str or not value for value in inputs)
        ):
            raise EmbeddingProviderUnavailable("Embedding provider is unavailable")
        try:
            body = json.dumps(
                {
                    "dimensions": self.profile.dimension,
                    "encoding_format": "float",
                    "input": list(inputs),
                    "model": self._configuration.model,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            request = Request(
                self._configuration.endpoint,
                data=body,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self._configuration.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            raw_response = self._transport(
                request,
                float(self._configuration.timeout_seconds),
                _MAX_EXTERNAL_RESPONSE_BYTES,
            )
            response = json.loads(raw_response)
            raw_data = response["data"]
            if type(raw_data) is not list or len(raw_data) != len(inputs):
                raise ValueError
            ordered: list[list[object] | None] = [None] * len(inputs)
            for item in raw_data:
                if type(item) is not dict:
                    raise ValueError
                index = item.get("index")
                vector = item.get("embedding")
                if (
                    type(index) is not int
                    or not 0 <= index < len(inputs)
                    or ordered[index] is not None
                    or type(vector) is not list
                ):
                    raise ValueError
                ordered[index] = cast(list[object], vector)
            if any(vector is None for vector in ordered):
                raise ValueError
            return validate_embedding_batch(
                inputs,
                cast(list[list[object]], ordered),
                self.profile,
            )
        except Exception:
            raise EmbeddingProviderUnavailable(
                "Embedding provider is unavailable"
            ) from None


class DeterministicEmbeddingTwin:
    """Stable content-derived vectors for tests without network egress."""

    __slots__ = ("_profile",)

    def __init__(
        self,
        dimension: int = CONTEXT_FRAGMENT_EMBEDDING_DIMENSION,
    ) -> None:
        self._profile = EmbeddingProfile(dimension)

    @property
    def profile(self) -> EmbeddingProfile:
        return self._profile

    def embed(self, inputs: tuple[str, ...]) -> tuple[EmbeddingVector, ...]:
        if (
            type(inputs) is not tuple
            or not inputs
            or any(type(value) is not str or not value for value in inputs)
        ):
            raise EmbeddingProviderUnavailable("Embedding provider is unavailable")
        vectors: list[EmbeddingVector] = []
        for value in inputs:
            raw = shake_256(
                b"context-engine.embedding-twin.v1\x00" + value.encode("utf-8")
            ).digest(self.profile.dimension * 2)
            unscaled = tuple(
                (int.from_bytes(raw[offset : offset + 2], "big") - 32767.5) / 32767.5
                for offset in range(0, len(raw), 2)
            )
            norm = sqrt(sum(component * component for component in unscaled))
            vectors.append(tuple(component / norm for component in unscaled))
        return validate_embedding_batch(inputs, vectors, self.profile)
