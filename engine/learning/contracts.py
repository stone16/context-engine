"""Immutable release composition contracts owned by ContextLearning."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, NoReturn, cast
from uuid import UUID

import rfc8785

MAX_SIGNED_BIGINT: Final = (1 << 63) - 1
MAX_REFERENCE_LENGTH: Final = 255
_MANIFEST_LINEAGE_DOMAIN: Final = b"context-engine.release-manifest-lineage.v1\x00"
_MANIFEST_DOMAIN: Final = b"context-engine.release-manifest.v1\x00"


def _require_uuid(field_name: str, value: object) -> UUID:
    if type(value) is not UUID:
        raise TypeError(f"{field_name} must be UUID")
    return value


def _require_ref(field_name: str, value: object) -> str:
    if (
        type(value) is not str
        or not value
        or value.isspace()
        or value != value.strip()
        or len(value) > MAX_REFERENCE_LENGTH
        or any(character.isspace() for character in value)
    ):
        raise ValueError(f"{field_name} must be a bounded nonblank reference")
    return value


def _require_digest(field_name: str, value: object) -> str:
    if (
        type(value) is not str
        or len(value) != hashlib.sha256().digest_size * 2
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field_name} must be lowercase SHA-256")
    return value


def _require_generation(field_name: str, value: object) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SIGNED_BIGINT:
        raise ValueError(
            f"{field_name} must be a nonnegative signed 64-bit integer"
        )
    return value


def _canonical_bigint(value: int) -> str:
    """Encode an already-validated bigint without RFC 8785 number narrowing."""

    return str(value)


def _require_canonical_refs(
    field_name: str,
    value: object,
    *,
    allow_empty: bool,
) -> tuple[str, ...]:
    if type(value) is not tuple or (not allow_empty and not value):
        qualifier = "a tuple" if allow_empty else "a nonempty tuple"
        raise ValueError(f"{field_name} must be {qualifier}")
    references = value
    for reference in references:
        _require_ref(f"{field_name} member", reference)
    if len(set(references)) != len(references):
        raise ValueError(f"{field_name} must contain unique references")
    if references != tuple(sorted(references)):
        raise ValueError(f"{field_name} must use canonical reference order")
    return references


type CanonicalJsonValue = (
    None
    | bool
    | int
    | float
    | str
    | list["CanonicalJsonValue"]
    | dict[str, "CanonicalJsonValue"]
)


def _canonical_digest(domain: bytes, document: dict[str, object]) -> str:
    """Hash one exact RFC 8785 document under a release-specific domain."""

    canonical_document = cast(dict[str, CanonicalJsonValue], document)
    return hashlib.sha256(domain + rfc8785.dumps(canonical_document)).hexdigest()


@dataclass(frozen=True, slots=True)
class ContentProfileRef:
    """Immutable content schema/profile identity selected by a manifest."""

    profile_ref: str
    profile_digest: str
    content_schema_ref: str

    def __post_init__(self) -> None:
        _require_ref("ContentProfile profile_ref", self.profile_ref)
        _require_digest("ContentProfile profile_digest", self.profile_digest)
        _require_ref("ContentProfile content_schema_ref", self.content_schema_ref)


@dataclass(frozen=True, slots=True)
class IndexProfileRef:
    """Immutable index identity bound to the exact ContentProfile."""

    profile_ref: str
    profile_digest: str
    content_profile_digest: str
    content_schema_ref: str
    index_schema_ref: str

    def __post_init__(self) -> None:
        _require_ref("IndexProfile profile_ref", self.profile_ref)
        _require_digest("IndexProfile profile_digest", self.profile_digest)
        _require_digest(
            "IndexProfile content_profile_digest", self.content_profile_digest
        )
        _require_ref("IndexProfile content_schema_ref", self.content_schema_ref)
        _require_ref("IndexProfile index_schema_ref", self.index_schema_ref)


@dataclass(frozen=True, slots=True)
class RuntimeProfileRef:
    """Immutable Runtime identity bound to content, index, tokenizer and Package."""

    profile_ref: str
    profile_digest: str
    content_profile_digest: str
    index_profile_digest: str
    content_schema_ref: str
    index_schema_ref: str
    tokenizer_ref: str
    package_schema_ref: str

    def __post_init__(self) -> None:
        _require_ref("RuntimeProfile profile_ref", self.profile_ref)
        _require_digest("RuntimeProfile profile_digest", self.profile_digest)
        _require_digest(
            "RuntimeProfile content_profile_digest", self.content_profile_digest
        )
        _require_digest(
            "RuntimeProfile index_profile_digest", self.index_profile_digest
        )
        _require_ref("RuntimeProfile content_schema_ref", self.content_schema_ref)
        _require_ref("RuntimeProfile index_schema_ref", self.index_schema_ref)
        _require_ref("RuntimeProfile tokenizer_ref", self.tokenizer_ref)
        _require_ref("RuntimeProfile package_schema_ref", self.package_schema_ref)


class CurationMode(StrEnum):
    """Closed manifest choice; Curation-on execution is not active in M0."""

    OFF = "curation_off"
    ON = "curation_on"


@dataclass(frozen=True, slots=True)
class CurationProfileRef:
    """Explicit curation-off or structurally complete future snapshot selection."""

    profile_ref: str
    profile_digest: str
    mode: CurationMode
    curation_snapshot_ref: str | None = None
    compatible_revision_refs: tuple[str, ...] = ()
    evaluation_digest: str | None = None

    def __post_init__(self) -> None:
        _require_ref("CurationProfile profile_ref", self.profile_ref)
        _require_digest("CurationProfile profile_digest", self.profile_digest)
        if type(self.mode) is not CurationMode:
            raise TypeError("CurationProfile mode must be CurationMode")
        if self.mode is CurationMode.OFF:
            if (
                self.curation_snapshot_ref is not None
                or self.compatible_revision_refs != ()
                or self.evaluation_digest is not None
            ):
                raise ValueError(
                    "curation-off cannot select a snapshot, Revisions, or evaluation"
                )
            return
        if self.curation_snapshot_ref is None:
            raise ValueError("curation-on requires CurationSnapshotRef")
        _require_ref(
            "CurationProfile curation_snapshot_ref", self.curation_snapshot_ref
        )
        _require_canonical_refs(
            "CurationProfile compatible_revision_refs",
            self.compatible_revision_refs,
            allow_empty=False,
        )
        if self.evaluation_digest is None:
            raise ValueError("curation-on requires evaluation digest")
        _require_digest("CurationProfile evaluation_digest", self.evaluation_digest)

    @classmethod
    def off(
        cls,
        *,
        profile_ref: str,
        profile_digest: str,
    ) -> CurationProfileRef:
        return cls(
            profile_ref=profile_ref,
            profile_digest=profile_digest,
            mode=CurationMode.OFF,
        )

    @classmethod
    def on(
        cls,
        *,
        profile_ref: str,
        profile_digest: str,
        curation_snapshot_ref: str,
        compatible_revision_refs: tuple[str, ...],
        evaluation_digest: str,
    ) -> CurationProfileRef:
        return cls(
            profile_ref=profile_ref,
            profile_digest=profile_digest,
            mode=CurationMode.ON,
            curation_snapshot_ref=curation_snapshot_ref,
            compatible_revision_refs=compatible_revision_refs,
            evaluation_digest=evaluation_digest,
        )

    @property
    def is_curation_off(self) -> bool:
        return self.mode is CurationMode.OFF


def _profile_document(profile: object) -> dict[str, object]:
    if type(profile) is ContentProfileRef:
        return {
            "content_schema_ref": profile.content_schema_ref,
            "profile_digest": profile.profile_digest,
            "profile_ref": profile.profile_ref,
        }
    if type(profile) is IndexProfileRef:
        return {
            "content_profile_digest": profile.content_profile_digest,
            "content_schema_ref": profile.content_schema_ref,
            "index_schema_ref": profile.index_schema_ref,
            "profile_digest": profile.profile_digest,
            "profile_ref": profile.profile_ref,
        }
    if type(profile) is RuntimeProfileRef:
        return {
            "content_profile_digest": profile.content_profile_digest,
            "content_schema_ref": profile.content_schema_ref,
            "index_profile_digest": profile.index_profile_digest,
            "index_schema_ref": profile.index_schema_ref,
            "package_schema_ref": profile.package_schema_ref,
            "profile_digest": profile.profile_digest,
            "profile_ref": profile.profile_ref,
            "tokenizer_ref": profile.tokenizer_ref,
        }
    if type(profile) is CurationProfileRef:
        return {
            "compatible_revision_refs": list(profile.compatible_revision_refs),
            "curation_snapshot_ref": profile.curation_snapshot_ref,
            "evaluation_digest": profile.evaluation_digest,
            "mode": profile.mode.value,
            "profile_digest": profile.profile_digest,
            "profile_ref": profile.profile_ref,
        }
    raise TypeError("unknown release profile nominal type")


@dataclass(frozen=True, slots=True)
class ReleaseManifest:
    """One immutable Organization-owned release profile composition."""

    organization_id: UUID = field(repr=False)
    manifest_ref: str
    content_profile: ContentProfileRef
    index_profile: IndexProfileRef
    runtime_profile: RuntimeProfileRef
    curation_profile: CurationProfileRef
    active_revision_refs: tuple[str, ...] = ()
    lineage_digest: str = field(init=False)
    manifest_digest: str = field(init=False)

    def __post_init__(self) -> None:
        _require_uuid("ReleaseManifest organization_id", self.organization_id)
        _require_ref("ReleaseManifest manifest_ref", self.manifest_ref)
        if type(self.content_profile) is not ContentProfileRef:
            raise TypeError("ReleaseManifest requires ContentProfileRef")
        if type(self.index_profile) is not IndexProfileRef:
            raise TypeError("ReleaseManifest requires IndexProfileRef")
        if type(self.runtime_profile) is not RuntimeProfileRef:
            raise TypeError("ReleaseManifest requires RuntimeProfileRef")
        if type(self.curation_profile) is not CurationProfileRef:
            raise TypeError("ReleaseManifest requires CurationProfileRef")
        _require_canonical_refs(
            "ReleaseManifest active_revision_refs",
            self.active_revision_refs,
            allow_empty=True,
        )
        if (
            self.index_profile.content_profile_digest
            != self.content_profile.profile_digest
            or self.index_profile.content_schema_ref
            != self.content_profile.content_schema_ref
        ):
            raise ValueError("IndexProfile is incompatible with ContentProfile")
        if (
            self.runtime_profile.content_profile_digest
            != self.content_profile.profile_digest
            or self.runtime_profile.content_schema_ref
            != self.content_profile.content_schema_ref
        ):
            raise ValueError("RuntimeProfile is incompatible with ContentProfile")
        if (
            self.runtime_profile.index_profile_digest
            != self.index_profile.profile_digest
            or self.runtime_profile.index_schema_ref
            != self.index_profile.index_schema_ref
        ):
            raise ValueError("RuntimeProfile is incompatible with IndexProfile")
        if (
            self.curation_profile.mode is CurationMode.ON
            and self.curation_profile.compatible_revision_refs
            != self.active_revision_refs
        ):
            raise ValueError(
                "CurationProfile compatible Revisions must exactly match the manifest"
            )

        lineage_document = release_manifest_lineage_document(self)
        lineage_digest = _canonical_digest(
            _MANIFEST_LINEAGE_DOMAIN,
            lineage_document,
        )
        object.__setattr__(self, "lineage_digest", lineage_digest)
        object.__setattr__(
            self,
            "manifest_digest",
            _canonical_digest(
                _MANIFEST_DOMAIN,
                {
                    **lineage_document,
                    "lineage_digest": lineage_digest,
                    "manifest_ref": self.manifest_ref,
                },
            ),
        )

    @classmethod
    def m0_empty(
        cls,
        *,
        organization_id: UUID,
        manifest_ref: str,
        content_profile: ContentProfileRef,
        index_profile: IndexProfileRef,
        runtime_profile: RuntimeProfileRef,
        curation_profile: CurationProfileRef,
    ) -> ReleaseManifest:
        if type(curation_profile) is not CurationProfileRef:
            raise TypeError("M0 empty manifest requires CurationProfileRef")
        if not curation_profile.is_curation_off:
            raise ValueError("M0 empty manifest must explicitly select curation-off")
        return cls(
            organization_id=organization_id,
            manifest_ref=manifest_ref,
            content_profile=content_profile,
            index_profile=index_profile,
            runtime_profile=runtime_profile,
            curation_profile=curation_profile,
            active_revision_refs=(),
        )

    def __reduce__(self) -> NoReturn:
        raise TypeError("ReleaseManifest is not serializable")


def release_manifest_lineage_document(manifest: ReleaseManifest) -> dict[str, object]:
    """Return the exact Organization/profile composition used for lineage."""

    if type(manifest) is not ReleaseManifest:
        raise TypeError("manifest must be ReleaseManifest")
    return {
        "active_revision_refs": list(manifest.active_revision_refs),
        "content_profile": _profile_document(manifest.content_profile),
        "curation_profile": _profile_document(manifest.curation_profile),
        "index_profile": _profile_document(manifest.index_profile),
        "organization_id": str(manifest.organization_id),
        "runtime_profile": _profile_document(manifest.runtime_profile),
    }


def verify_release_manifest(manifest: ReleaseManifest) -> bool:
    """Detect mutation or mismatched immutable manifest lineage."""

    if type(manifest) is not ReleaseManifest:
        return False
    try:
        lineage = release_manifest_lineage_document(manifest)
        expected_lineage = _canonical_digest(_MANIFEST_LINEAGE_DOMAIN, lineage)
        expected_manifest = _canonical_digest(
            _MANIFEST_DOMAIN,
            {
                **lineage,
                "lineage_digest": expected_lineage,
                "manifest_ref": manifest.manifest_ref,
            },
        )
    except (TypeError, ValueError):
        return False
    return (
        manifest.lineage_digest == expected_lineage
        and manifest.manifest_digest == expected_manifest
    )
