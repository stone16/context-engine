"""Network-free and external adapters for the Supply embedding seam."""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, field
from functools import partial
from hashlib import shake_256
from math import sqrt
from pathlib import Path
from threading import Lock
from typing import IO, Any, BinaryIO, cast
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from adapters._bounded_call import (
    BoundedCallTimedOut,
    BoundedCallUnavailable,
    invoke_bounded,
)
from adapters.local_embedding_model import load_qwen_local_model
from engine.supply.embeddings import (
    CONTEXT_FRAGMENT_EMBEDDING_DIMENSION,
    DETERMINISTIC_TWIN_EMBEDDING_PROFILE,
    QWEN3_EMBEDDING_PROFILE,
    EmbeddingDocumentRefused,
    EmbeddingProfile,
    EmbeddingProviderProfile,
    EmbeddingProviderUnavailable,
    EmbeddingVector,
    validate_embedding_batch,
)

_MAX_EXTERNAL_RESPONSE_BYTES = 64 * 1024 * 1024
_DEFAULT_TIMEOUT_SECONDS = 30.0
_LOCAL_EMBEDDING_TIMEOUT_SECONDS = 30.0
_LOCAL_EMBEDDING_MICRO_BATCH_SIZE = 1
_LOCAL_EMBEDDING_WARMUP_TEXT = "context-engine local embedding warmup"
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
    batch_size: int
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
            or type(self.batch_size) is not int
            or not 1 <= self.batch_size <= 256
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

    @property
    def provider_profile(self) -> EmbeddingProviderProfile:
        """External identity remains unavailable for Runtime activation."""

        raise EmbeddingProviderUnavailable("Embedding provider is unavailable")

    def embed(self, inputs: tuple[str, ...]) -> tuple[EmbeddingVector, ...]:
        if (
            type(inputs) is not tuple
            or not inputs
            or any(type(value) is not str or not value for value in inputs)
        ):
            raise EmbeddingProviderUnavailable("Embedding provider is unavailable")
        try:
            vectors: list[EmbeddingVector] = []
            for offset in range(0, len(inputs), self._configuration.batch_size):
                batch = inputs[offset : offset + self._configuration.batch_size]
                vectors.extend(self._embed_batch(batch))
            return tuple(vectors)
        except Exception:
            raise EmbeddingProviderUnavailable(
                "Embedding provider is unavailable"
            ) from None

    def _embed_batch(self, inputs: tuple[str, ...]) -> tuple[EmbeddingVector, ...]:
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

    def embed_documents(
        self, inputs: tuple[str, ...]
    ) -> tuple[EmbeddingVector, ...]:
        return self.embed(inputs)


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

    @property
    def provider_profile(self) -> EmbeddingProviderProfile:
        if self._profile.dimension != DETERMINISTIC_TWIN_EMBEDDING_PROFILE.dimension:
            raise EmbeddingProviderUnavailable("Embedding provider is unavailable")
        return DETERMINISTIC_TWIN_EMBEDDING_PROFILE

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

    def embed_documents(
        self, inputs: tuple[str, ...]
    ) -> tuple[EmbeddingVector, ...]:
        return self.embed(inputs)


class LocalQwenEmbeddingProvider:
    """Hash-verified, network-free Qwen provider for the activated local carrier."""

    __slots__ = ("_inference_lock", "_model")

    def __init__(self, model_dir: Path) -> None:
        if not isinstance(model_dir, Path):
            raise TypeError("Local embedding provider requires a model directory")
        self._inference_lock = Lock()
        try:
            model = invoke_bounded(
                lambda: load_qwen_local_model(model_dir),
                timeout_seconds=_LOCAL_EMBEDDING_TIMEOUT_SECONDS,
                thread_name="context-engine-local-embedding",
                in_flight_lock=self._inference_lock,
            )
        except BoundedCallUnavailable:
            raise EmbeddingProviderUnavailable(
                "Embedding provider is unavailable"
            ) from None
        self._model: Any = model
        try:
            self._reduce_vectors(
                (_LOCAL_EMBEDDING_WARMUP_TEXT,),
                self._encode_model_inputs([_LOCAL_EMBEDDING_WARMUP_TEXT]),
            )
        except Exception:
            raise EmbeddingProviderUnavailable(
                "Embedding provider is unavailable"
            ) from None

    @property
    def profile(self) -> EmbeddingProfile:
        return QWEN3_EMBEDDING_PROFILE.vector_profile

    @property
    def provider_profile(self) -> EmbeddingProviderProfile:
        return QWEN3_EMBEDDING_PROFILE

    def embed(self, inputs: tuple[str, ...]) -> tuple[EmbeddingVector, ...]:
        return self._embed_with_prefix(
            inputs,
            QWEN3_EMBEDDING_PROFILE.query_prefix,
            document=False,
        )

    def embed_documents(
        self, inputs: tuple[str, ...]
    ) -> tuple[EmbeddingVector, ...]:
        return self._embed_with_prefix(
            inputs,
            QWEN3_EMBEDDING_PROFILE.document_prefix,
            document=True,
        )

    def _embed_with_prefix(
        self,
        inputs: tuple[str, ...],
        prefix: str,
        *,
        document: bool,
    ) -> tuple[EmbeddingVector, ...]:
        if (
            type(inputs) is not tuple
            or not inputs
            or any(type(value) is not str or not value.strip() for value in inputs)
        ):
            raise EmbeddingProviderUnavailable("Embedding provider is unavailable")
        try:
            prefixed = [prefix + value for value in inputs]
            vectors: list[EmbeddingVector] = []
            for offset in range(
                0,
                len(prefixed),
                _LOCAL_EMBEDDING_MICRO_BATCH_SIZE,
            ):
                batch = prefixed[
                    offset : offset + _LOCAL_EMBEDDING_MICRO_BATCH_SIZE
                ]
                try:
                    raw_vectors = invoke_bounded(
                        partial(self._encode_model_inputs, batch),
                        timeout_seconds=_LOCAL_EMBEDDING_TIMEOUT_SECONDS,
                        thread_name="context-engine-local-embedding",
                        in_flight_lock=self._inference_lock,
                    )
                except BoundedCallTimedOut:
                    if document and len(batch) == 1:
                        raise EmbeddingDocumentRefused(
                            "Embedding document is outside provider bounds"
                        ) from None
                    raise
                vectors.extend(self._reduce_vectors(tuple(batch), raw_vectors))
            return validate_embedding_batch(inputs, tuple(vectors), self.profile)
        except EmbeddingDocumentRefused:
            raise
        except (BoundedCallUnavailable, Exception):
            raise EmbeddingProviderUnavailable(
                "Embedding provider is unavailable"
            ) from None

    def _encode_model_inputs(self, inputs: list[str]) -> Any:
        return self._model.encode(
            inputs,
            batch_size=QWEN3_EMBEDDING_PROFILE.batch_size,
            convert_to_numpy=True,
            normalize_embeddings=True,
            precision=QWEN3_EMBEDDING_PROFILE.precision,
            show_progress_bar=False,
        )

    def _reduce_vectors(
        self,
        inputs: tuple[str, ...],
        raw_vectors: Any,
    ) -> tuple[EmbeddingVector, ...]:
        vectors: list[EmbeddingVector] = []
        for raw_vector in raw_vectors:
            if len(raw_vector) != 1024:
                raise ValueError
            truncated = tuple(float(value) for value in raw_vector[:384])
            norm = math.sqrt(sum(value * value for value in truncated))
            if not math.isfinite(norm) or norm == 0.0:
                raise ValueError
            vectors.append(tuple(value / norm for value in truncated))
        return validate_embedding_batch(inputs, tuple(vectors), self.profile)
