"""Trusted observation of the active Organization Runtime release lineage."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Final
from uuid import UUID

from engine.release_profiles import (
    CONTENT_PROFILE_DIGEST_V0,
    CONTENT_PROFILE_REF_V0,
    CONTENT_SCHEMA_REF_V0,
    CURATION_PROFILE_DIGEST_V0,
    CURATION_PROFILE_REF_V0,
    DETERMINISTIC_TWIN_EMBEDDING_PROFILE,
    DOGFOOD_VECTOR_INDEX_PROFILE_DIGEST_V1,
    DOGFOOD_VECTOR_INDEX_PROFILE_REF_V1,
    HISTORICAL_UTF8_BYTE_TOKENIZER_PROFILE_DIGEST,
    HISTORICAL_UTF8_BYTE_TOKENIZER_PROFILE_DOCUMENT,
    INDEX_PROFILE_DIGEST_V0,
    INDEX_PROFILE_REF_V0,
    INDEX_SCHEMA_REF_V0,
    PACKAGE_SCHEMA_REF_V0,
    PACKAGE_SCHEMA_REF_V1,
    QWEN3_EMBEDDING_PROFILE,
    QWEN_VECTOR_INDEX_PROFILE_DIGEST_V1,
    QWEN_VECTOR_INDEX_PROFILE_REF_V1,
    RUNTIME_PROFILE_DIGEST_V0,
    RUNTIME_PROFILE_DIGEST_V1,
    RUNTIME_PROFILE_REF_V0,
    RUNTIME_PROFILE_REF_V1,
    RUNTIME_TOKENIZER_REF_V0,
    RUNTIME_TOKENIZER_REF_V1,
    UNICODE_SCALAR_TOKENIZER_PROFILE,
)


class ActiveReleaseUnavailable(RuntimeError):
    """No complete active Runtime release could be observed fail-closed."""


_PUBLIC_RELEASE_REF_DOMAIN: Final = b"context-engine.public-release-ref.v1\x00"


def _require_ref(field_name: str, value: object) -> str:
    if (
        type(value) is not str
        or not value
        or value.isspace()
        or value != value.strip()
        or any(character.isspace() for character in value)
    ):
        raise ValueError(f"active release {field_name} must be an opaque ref")
    return value


def _require_digest(field_name: str, value: object) -> str:
    if (
        type(value) is not str
        or len(value) != sha256().digest_size * 2
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"active release {field_name} must be lowercase SHA-256")
    return value


def public_release_manifest_ref(
    manifest_digest: str,
    active_generation: int,
) -> str:
    """Derive a public opaque activation ref without exposing durable labels."""

    digest = _require_digest("manifest_digest", manifest_digest)
    if (
        type(active_generation) is not int
        or not 1 <= active_generation <= (1 << 63) - 1
    ):
        raise ValueError("active release generation must be a positive signed bigint")
    public_digest = sha256(
        _PUBLIC_RELEASE_REF_DOMAIN
        + bytes.fromhex(digest)
        + active_generation.to_bytes(8, "big", signed=False)
    ).hexdigest()
    return f"rel_{public_digest}"


@dataclass(frozen=True, slots=True)
class ActiveRuntimeRelease:
    """Immutable active manifest facts observed by the current UserActor authority."""

    organization_id: UUID = field(repr=False)
    manifest_digest: str = field(repr=False)
    active_generation: int
    content_profile_ref: str
    content_schema_ref: str
    index_profile_ref: str
    index_schema_ref: str
    runtime_profile_ref: str
    runtime_profile_digest: str = field(repr=False)
    content_profile_digest: str = field(repr=False)
    index_profile_digest: str = field(repr=False)
    embedding_profile_document: str = field(repr=False)
    embedding_profile_digest: str = field(repr=False)
    tokenizer_ref: str
    package_schema_ref: str
    curation_profile_ref: str
    curation_profile_digest: str = field(repr=False)
    curation_mode: str
    curation_snapshot_ref: str | None
    curation_evaluation_digest: str | None = field(repr=False)
    compatible_revision_refs: tuple[str, ...]
    active_revision_refs: tuple[str, ...]
    tokenizer_profile_document: str = HISTORICAL_UTF8_BYTE_TOKENIZER_PROFILE_DOCUMENT
    tokenizer_profile_digest: str = HISTORICAL_UTF8_BYTE_TOKENIZER_PROFILE_DIGEST
    manifest_ref: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.organization_id) is not UUID:
            raise TypeError("active release Organization must be UUID")
        if (
            type(self.active_generation) is not int
            or not 1 <= self.active_generation <= (1 << 63) - 1
        ):
            raise ValueError(
                "active release generation must be a positive signed bigint"
            )
        for field_name in (
            "content_profile_ref",
            "content_schema_ref",
            "index_profile_ref",
            "index_schema_ref",
            "runtime_profile_ref",
            "tokenizer_ref",
            "package_schema_ref",
            "curation_profile_ref",
        ):
            _require_ref(field_name, getattr(self, field_name))
        for field_name in (
            "manifest_digest",
            "runtime_profile_digest",
            "content_profile_digest",
            "index_profile_digest",
            "embedding_profile_digest",
            "curation_profile_digest",
            "tokenizer_profile_digest",
        ):
            _require_digest(field_name, getattr(self, field_name))
        if type(self.active_revision_refs) is not tuple:
            raise TypeError("active release Revisions must be a tuple")
        if type(self.compatible_revision_refs) is not tuple:
            raise TypeError("active release compatible Revisions must be a tuple")
        for field_name, revision_refs in (
            ("active_revision_ref", self.active_revision_refs),
            ("compatible_revision_ref", self.compatible_revision_refs),
        ):
            for revision_ref in revision_refs:
                _require_ref(field_name, revision_ref)
            if len(set(revision_refs)) != len(revision_refs) or revision_refs != tuple(
                sorted(revision_refs)
            ):
                raise ValueError(
                    "active release Revisions must be unique and canonical"
                )
        supported_index_profile = (
            self.index_profile_ref,
            self.index_profile_digest,
            self.embedding_profile_digest,
            self.embedding_profile_document,
        ) in {
            (
                INDEX_PROFILE_REF_V0,
                INDEX_PROFILE_DIGEST_V0,
                DETERMINISTIC_TWIN_EMBEDDING_PROFILE.profile_digest,
                DETERMINISTIC_TWIN_EMBEDDING_PROFILE.canonical_json(),
            ),
            (
                DOGFOOD_VECTOR_INDEX_PROFILE_REF_V1,
                DOGFOOD_VECTOR_INDEX_PROFILE_DIGEST_V1,
                DETERMINISTIC_TWIN_EMBEDDING_PROFILE.profile_digest,
                DETERMINISTIC_TWIN_EMBEDDING_PROFILE.canonical_json(),
            ),
            (
                QWEN_VECTOR_INDEX_PROFILE_REF_V1,
                QWEN_VECTOR_INDEX_PROFILE_DIGEST_V1,
                QWEN3_EMBEDDING_PROFILE.profile_digest,
                QWEN3_EMBEDDING_PROFILE.canonical_json(),
            ),
        }
        supported_runtime_profile = (
            self.runtime_profile_ref,
            self.runtime_profile_digest,
            self.tokenizer_ref,
            self.tokenizer_profile_document,
            self.tokenizer_profile_digest,
            self.package_schema_ref,
        ) in {
            (
                RUNTIME_PROFILE_REF_V0,
                RUNTIME_PROFILE_DIGEST_V0,
                RUNTIME_TOKENIZER_REF_V0,
                HISTORICAL_UTF8_BYTE_TOKENIZER_PROFILE_DOCUMENT,
                HISTORICAL_UTF8_BYTE_TOKENIZER_PROFILE_DIGEST,
                PACKAGE_SCHEMA_REF_V0,
            ),
            (
                RUNTIME_PROFILE_REF_V1,
                RUNTIME_PROFILE_DIGEST_V1,
                RUNTIME_TOKENIZER_REF_V1,
                UNICODE_SCALAR_TOKENIZER_PROFILE.canonical_json(),
                UNICODE_SCALAR_TOKENIZER_PROFILE.profile_digest,
                PACKAGE_SCHEMA_REF_V1,
            ),
        }
        if (
            self.content_profile_ref != CONTENT_PROFILE_REF_V0
            or self.content_schema_ref != CONTENT_SCHEMA_REF_V0
            or not supported_index_profile
            or self.index_schema_ref != INDEX_SCHEMA_REF_V0
            or self.content_profile_digest != CONTENT_PROFILE_DIGEST_V0
            or not supported_runtime_profile
            or self.curation_profile_ref != CURATION_PROFILE_REF_V0
            or self.curation_profile_digest != CURATION_PROFILE_DIGEST_V0
            or self.curation_mode != "curation_off"
            or self.curation_snapshot_ref is not None
            or self.curation_evaluation_digest is not None
            or self.compatible_revision_refs != ()
        ):
            raise ValueError("active release selects an unsupported Runtime profile")
        object.__setattr__(
            self,
            "manifest_ref",
            public_release_manifest_ref(
                self.manifest_digest,
                self.active_generation,
            ),
        )


__all__ = [
    "CONTENT_PROFILE_DIGEST_V0",
    "CONTENT_PROFILE_REF_V0",
    "CONTENT_SCHEMA_REF_V0",
    "CURATION_PROFILE_DIGEST_V0",
    "CURATION_PROFILE_REF_V0",
    "DOGFOOD_VECTOR_INDEX_PROFILE_DIGEST_V1",
    "DOGFOOD_VECTOR_INDEX_PROFILE_REF_V1",
    "INDEX_PROFILE_DIGEST_V0",
    "INDEX_PROFILE_REF_V0",
    "INDEX_SCHEMA_REF_V0",
    "PACKAGE_SCHEMA_REF_V0",
    "PACKAGE_SCHEMA_REF_V1",
    "QWEN_VECTOR_INDEX_PROFILE_DIGEST_V1",
    "QWEN_VECTOR_INDEX_PROFILE_REF_V1",
    "RUNTIME_PROFILE_DIGEST_V0",
    "RUNTIME_PROFILE_DIGEST_V1",
    "RUNTIME_PROFILE_REF_V0",
    "RUNTIME_PROFILE_REF_V1",
    "RUNTIME_TOKENIZER_REF_V0",
    "RUNTIME_TOKENIZER_REF_V1",
    "ActiveReleaseUnavailable",
    "ActiveRuntimeRelease",
    "public_release_manifest_ref",
]
