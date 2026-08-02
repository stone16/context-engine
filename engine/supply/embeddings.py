"""Embedding contracts for Fragment publication and content-free discovery."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from math import isfinite
from re import compile as compile_pattern
from struct import Struct
from struct import error as StructError
from typing import Any, Protocol, cast

import rfc8785

CONTEXT_FRAGMENT_EMBEDDING_DIMENSION = 384
type EmbeddingVector = tuple[float, ...]
_FLOAT32 = Struct("!f")
_IMMUTABLE_REVISION = compile_pattern(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_SHA256_DIGEST = compile_pattern(r"^[0-9a-f]{64}$")
_PROFILE_DIGEST_DOMAIN = b"context-engine.embedding-provider-profile.v1\x00"
_MAX_PROFILE_TEXT_LENGTH = 4096


@dataclass(frozen=True, slots=True)
class EmbeddingProfile:
    """Closed vector shape stored by the current PostgreSQL schema."""

    dimension: int

    def __post_init__(self) -> None:
        if type(self.dimension) is not int or not 1 <= self.dimension <= 4096:
            raise ValueError("Embedding dimension is not available")


@dataclass(frozen=True, slots=True)
class EmbeddingProviderProfile:
    """Complete immutable identity shared by publication and query embedding."""

    model_id: str
    revision: str
    artifact_digest: str
    dimension: int
    pooling: str
    query_prefix: str
    document_prefix: str
    transformation_pipeline: str
    precision: str
    batch_size: int
    profile_digest: str = field(init=False)

    def __post_init__(self) -> None:
        nonblank_fields = (
            self.model_id,
            self.pooling,
            self.transformation_pipeline,
            self.precision,
        )
        if (
            any(
                type(value) is not str
                or len(value) > _MAX_PROFILE_TEXT_LENGTH
                or value != value.strip()
                for value in nonblank_fields
            )
            or any(not value for value in nonblank_fields)
            or type(self.query_prefix) is not str
            or len(self.query_prefix) > _MAX_PROFILE_TEXT_LENGTH
            or type(self.document_prefix) is not str
            or len(self.document_prefix) > _MAX_PROFILE_TEXT_LENGTH
            or type(self.revision) is not str
            or _IMMUTABLE_REVISION.fullmatch(self.revision) is None
            or type(self.artifact_digest) is not str
            or _SHA256_DIGEST.fullmatch(self.artifact_digest) is None
            or type(self.dimension) is not int
            or not 1 <= self.dimension <= 4096
            or type(self.batch_size) is not int
            or not 1 <= self.batch_size <= 1024
        ):
            raise ValueError("Embedding provider profile identity is unresolved")
        object.__setattr__(
            self,
            "profile_digest",
            sha256(
                _PROFILE_DIGEST_DOMAIN
                + rfc8785.dumps(cast(Any, self.canonical_document()))
            ).hexdigest(),
        )

    def canonical_document(self) -> dict[str, object]:
        """Return the exact profile identity covered by ``profile_digest``."""

        return {
            "artifactDigest": self.artifact_digest,
            "batchSize": self.batch_size,
            "dimension": self.dimension,
            "documentPrefix": self.document_prefix,
            "modelId": self.model_id,
            "pooling": self.pooling,
            "precision": self.precision,
            "queryPrefix": self.query_prefix,
            "revision": self.revision,
            "transformationPipeline": self.transformation_pipeline,
        }

    def canonical_json(self) -> str:
        """Return the RFC 8785 profile document persisted in Release lineage."""

        return rfc8785.dumps(cast(Any, self.canonical_document())).decode("utf-8")

    @property
    def vector_profile(self) -> EmbeddingProfile:
        return EmbeddingProfile(self.dimension)


DETERMINISTIC_TWIN_EMBEDDING_PROFILE = EmbeddingProviderProfile(
    model_id="context-engine/deterministic-embedding-twin",
    revision="0" * 40,
    artifact_digest=sha256(
        b"context-engine.embedding-twin.v1\x00network-free-python"
    ).hexdigest(),
    dimension=CONTEXT_FRAGMENT_EMBEDDING_DIMENSION,
    pooling="shake256-component-l2",
    query_prefix="",
    document_prefix="",
    transformation_pipeline="shake256 float components -> l2",
    precision="float64-to-float32",
    batch_size=256,
)

QWEN3_EMBEDDING_PROFILE = EmbeddingProviderProfile(
    model_id="Qwen/Qwen3-Embedding-0.6B",
    revision="97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
    artifact_digest=(
        "8cb25677d5be69ce6ac88ebbdfb5dad30980fee39c35c6324a583e325917eddc"
    ),
    dimension=CONTEXT_FRAGMENT_EMBEDDING_DIMENSION,
    pooling="last_token",
    query_prefix=(
        "Instruct: Given a web search query, retrieve relevant passages that answer "
        "the query\nQuery:"
    ),
    document_prefix="",
    transformation_pipeline="l2 -> truncate 1024->384 -> l2",
    precision="float32",
    batch_size=8,
)

_REGISTERED_EMBEDDING_PROVIDER_PROFILES = {
    (profile.canonical_json(), profile.profile_digest): profile
    for profile in (
        DETERMINISTIC_TWIN_EMBEDDING_PROFILE,
        QWEN3_EMBEDDING_PROFILE,
    )
}


def registered_embedding_provider_profile(
    canonical_document: str,
    profile_digest: str,
) -> EmbeddingProviderProfile:
    """Resolve one exact closed provider identity from persisted lineage."""

    try:
        return _REGISTERED_EMBEDDING_PROVIDER_PROFILES[
            (canonical_document, profile_digest)
        ]
    except (KeyError, TypeError):
        raise ValueError("Embedding provider profile identity is unresolved") from None


class EmbeddingProviderUnavailable(RuntimeError):
    """Content-free transient provider failure."""


class EmbeddingProvider(Protocol):
    """Batch embedding seam shared by Supply publication and query discovery."""

    @property
    def profile(self) -> EmbeddingProfile: ...

    @property
    def provider_profile(self) -> EmbeddingProviderProfile: ...

    def embed(self, inputs: tuple[str, ...]) -> tuple[EmbeddingVector, ...]: ...

    def embed_documents(
        self, inputs: tuple[str, ...]
    ) -> tuple[EmbeddingVector, ...]: ...


def validate_embedding_batch(
    inputs: tuple[str, ...],
    vectors: Sequence[Sequence[object]],
    profile: EmbeddingProfile,
) -> tuple[EmbeddingVector, ...]:
    """Validate one provider response before any vector crosses persistence."""

    try:
        if (
            type(inputs) is not tuple
            or not inputs
            or any(type(value) is not str or not value for value in inputs)
            or len(vectors) != len(inputs)
        ):
            raise EmbeddingProviderUnavailable("Embedding provider is unavailable")
        validated: list[EmbeddingVector] = []
        for raw_vector in vectors:
            if len(raw_vector) != profile.dimension:
                raise EmbeddingProviderUnavailable("Embedding provider is unavailable")
            vector: list[float] = []
            for raw_value in raw_vector:
                if type(raw_value) not in {int, float}:
                    raise EmbeddingProviderUnavailable(
                        "Embedding provider is unavailable"
                    )
                value = float(cast(int | float, raw_value))
                if not isfinite(value) or abs(value) > 1.0e30:
                    raise EmbeddingProviderUnavailable(
                        "Embedding provider is unavailable"
                    )
                stored_value = _FLOAT32.unpack(_FLOAT32.pack(value))[0]
                if not isfinite(stored_value) or abs(stored_value) > 1.0e30:
                    raise EmbeddingProviderUnavailable(
                        "Embedding provider is unavailable"
                    )
                vector.append(stored_value)
            if not any(value != 0.0 for value in vector):
                raise EmbeddingProviderUnavailable("Embedding provider is unavailable")
            validated.append(tuple(vector))
        return tuple(validated)
    except (TypeError, ValueError, OverflowError, StructError):
        raise EmbeddingProviderUnavailable(
            "Embedding provider is unavailable"
        ) from None
