"""Trusted observation of the active Organization Runtime release lineage."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Final
from uuid import UUID


class ActiveReleaseUnavailable(RuntimeError):
    """No complete active Runtime release could be observed fail-closed."""


RUNTIME_PROFILE_REF_V0: Final = "runtime-materialized-openapi-v0"
RUNTIME_TOKENIZER_REF_V0: Final = "utf8-byte-budget-v1"
PACKAGE_SCHEMA_REF_V0: Final = "context-package-openapi-v0"
CONTENT_PROFILE_REF_V0: Final = "content-materialized-v0"
CONTENT_SCHEMA_REF_V0: Final = "context-content-schema-v1"
INDEX_PROFILE_REF_V0: Final = "index-exact-phrase-v0"
INDEX_SCHEMA_REF_V0: Final = "context-index-schema-v1"
CONTENT_PROFILE_DIGEST_V0: Final = sha256(
    b"context-engine.content-profile.materialized-v0"
).hexdigest()
INDEX_PROFILE_DIGEST_V0: Final = sha256(
    b"context-engine.index-profile.exact-phrase-v0"
).hexdigest()
RUNTIME_PROFILE_DIGEST_V0: Final = sha256(
    b"context-engine.runtime-profile.materialized-openapi-v0"
).hexdigest()
CURATION_PROFILE_REF_V0: Final = "curation-off-v0"
CURATION_PROFILE_DIGEST_V0: Final = sha256(
    b"context-engine.curation-profile.off-v0"
).hexdigest()
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
    tokenizer_ref: str
    package_schema_ref: str
    curation_profile_ref: str
    curation_profile_digest: str = field(repr=False)
    curation_mode: str
    curation_snapshot_ref: str | None
    curation_evaluation_digest: str | None = field(repr=False)
    compatible_revision_refs: tuple[str, ...]
    active_revision_refs: tuple[str, ...]
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
            "curation_profile_digest",
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
            if len(set(revision_refs)) != len(
                revision_refs
            ) or revision_refs != tuple(sorted(revision_refs)):
                raise ValueError(
                    "active release Revisions must be unique and canonical"
                )
        if (
            self.content_profile_ref != CONTENT_PROFILE_REF_V0
            or self.content_schema_ref != CONTENT_SCHEMA_REF_V0
            or self.index_profile_ref != INDEX_PROFILE_REF_V0
            or self.index_schema_ref != INDEX_SCHEMA_REF_V0
            or self.runtime_profile_ref != RUNTIME_PROFILE_REF_V0
            or self.content_profile_digest != CONTENT_PROFILE_DIGEST_V0
            or self.index_profile_digest != INDEX_PROFILE_DIGEST_V0
            or self.runtime_profile_digest != RUNTIME_PROFILE_DIGEST_V0
            or self.tokenizer_ref != RUNTIME_TOKENIZER_REF_V0
            or self.package_schema_ref != PACKAGE_SCHEMA_REF_V0
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
    "ActiveReleaseUnavailable",
    "ActiveRuntimeRelease",
    "CONTENT_PROFILE_REF_V0",
    "CONTENT_SCHEMA_REF_V0",
    "CONTENT_PROFILE_DIGEST_V0",
    "CURATION_PROFILE_DIGEST_V0",
    "CURATION_PROFILE_REF_V0",
    "INDEX_PROFILE_REF_V0",
    "INDEX_SCHEMA_REF_V0",
    "INDEX_PROFILE_DIGEST_V0",
    "PACKAGE_SCHEMA_REF_V0",
    "RUNTIME_PROFILE_REF_V0",
    "RUNTIME_PROFILE_DIGEST_V0",
    "RUNTIME_TOKENIZER_REF_V0",
    "public_release_manifest_ref",
]
