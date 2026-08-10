"""Transport-neutral primitives owned by ContextEngine's public contract."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, cast

import rfc8785

MAX_NARROWING_REFS: Final = 64
MAX_NARROWING_REF_LENGTH: Final = 256
MAX_OPAQUE_CAPABILITY_LENGTH: Final = 4_096
MAX_PROJECTED_FIELD_REFS: Final = 64
MAX_PROJECTED_FIELD_REF_LENGTH: Final = 64
PACKAGE_REF_PATTERN: Final = r"^pkg_[0-9a-f]{32}$"
DECISION_REF_PATTERN: Final = r"^dec_[0-9a-f]{32}$"
_TOKENIZER_PROFILE_DIGEST_DOMAIN: Final = b"context-engine.tokenizer-profile.v1\x00"


@dataclass(frozen=True, slots=True)
class TokenizerProfileDescriptor:
    """Transport-neutral immutable identity for one tokenizer profile."""

    profile_ref: str
    artifact_name: str
    artifact_digest: str
    vocabulary_ref: str
    normalization_ref: str
    accounting_version: str
    counting_contract: str

    def __post_init__(self) -> None:
        for field_name in (
            "profile_ref",
            "artifact_name",
            "vocabulary_ref",
            "normalization_ref",
            "accounting_version",
            "counting_contract",
        ):
            value = getattr(self, field_name)
            if (
                type(value) is not str
                or not value
                or value != value.strip()
                or any(character.isspace() for character in value)
            ):
                raise ValueError(
                    f"TokenizerProfileDescriptor {field_name} must be an opaque ref"
                )
        if "/" in self.artifact_name or "\\" in self.artifact_name:
            raise ValueError("TokenizerProfileDescriptor artifact_name must be a name")
        if (
            type(self.artifact_digest) is not str
            or len(self.artifact_digest) != hashlib.sha256().digest_size * 2
            or any(
                character not in "0123456789abcdef"
                for character in self.artifact_digest
            )
        ):
            raise ValueError(
                "TokenizerProfileDescriptor artifact_digest must be lowercase SHA-256"
            )

    def canonical_document(self) -> dict[str, str]:
        return {
            "accountingVersion": self.accounting_version,
            "artifactDigest": self.artifact_digest,
            "countingContract": self.counting_contract,
            "normalizationRef": self.normalization_ref,
            "profileRef": self.profile_ref,
            "vocabularyRef": self.vocabulary_ref,
        }

    def canonical_json(self) -> str:
        return rfc8785.dumps(cast(Any, self.canonical_document())).decode("utf-8")

    @property
    def profile_digest(self) -> str:
        return hashlib.sha256(
            _TOKENIZER_PROFILE_DIGEST_DOMAIN
            + rfc8785.dumps(cast(Any, self.canonical_document()))
        ).hexdigest()


REGISTERED_V1_TOKENIZER_PROFILES: Final = (
    TokenizerProfileDescriptor(
        profile_ref="unicode-scalar-tokenizer-v1",
        artifact_name="unicode-scalar-v1.json",
        artifact_digest=(
            "8d301e3ce94e5b48febffb2e0871e139cd4d5f808084eccbf9cfc512d3948cca"
        ),
        vocabulary_ref="unicode-scalars-v15.1",
        normalization_ref="none",
        accounting_version="unicode-scalar-accounting-v1",
        counting_contract="one-unicode-scalar-one-token-v1",
    ),
)
REGISTERED_V1_TOKENIZER_IDENTITIES: Final = frozenset(
    (descriptor.profile_ref, descriptor.profile_digest)
    for descriptor in REGISTERED_V1_TOKENIZER_PROFILES
)


def is_registered_v1_tokenizer_identity(
    profile_ref: object,
    profile_digest: object,
) -> bool:
    """Return whether ref and digest name one admitted v1 tokenizer profile."""

    return (profile_ref, profile_digest) in REGISTERED_V1_TOKENIZER_IDENTITIES


type CanonicalJsonValue = (
    None
    | bool
    | int
    | float
    | str
    | list["CanonicalJsonValue"]
    | dict[str, "CanonicalJsonValue"]
)


def _require_unicode_scalars(value: str) -> str:
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise ValueError("package document strings must contain Unicode scalar values")
    return value


def _json_value(value: object, ancestors: set[int]) -> CanonicalJsonValue:
    if type(value) in (type(None), bool, float):
        return cast(None | bool | float, value)
    if type(value) is int:
        integer = value
        if -(2**53) < integer < 2**53:
            return integer
        try:
            binary64 = float(integer)
        except OverflowError as error:
            raise ValueError(
                "package document integers must be exact IEEE 754 binary64 values"
            ) from error
        if int(binary64) != integer:
            raise ValueError(
                "package document integers must be exact IEEE 754 binary64 values"
            )
        return binary64
    if type(value) is str:
        return _require_unicode_scalars(value)

    if isinstance(value, Mapping):
        identity = id(value)
        if identity in ancestors:
            raise ValueError("package document must not contain cyclic containers")
        ancestors.add(identity)
        try:
            document: dict[str, CanonicalJsonValue] = {}
            mapping = cast(Mapping[object, object], value)
            for key, item in mapping.items():
                if type(key) is not str:
                    raise TypeError(
                        "package document mappings require exact string keys"
                    )
                _require_unicode_scalars(key)
                if key == "packageDigest":
                    raise ValueError("package document must not contain packageDigest")
                document[key] = _json_value(item, ancestors)
            return document
        finally:
            ancestors.remove(identity)

    if type(value) in (list, tuple):
        identity = id(value)
        if identity in ancestors:
            raise ValueError("package document must not contain cyclic containers")
        ancestors.add(identity)
        try:
            sequence = cast(list[object] | tuple[object, ...], value)
            return [_json_value(item, ancestors) for item in sequence]
        finally:
            ancestors.remove(identity)

    raise TypeError("package document accepts only exact JSON values")


def canonicalize_context_package(document: Mapping[str, object]) -> bytes:
    """Return RFC 8785 canonical bytes for one Package without its digest field."""

    if not isinstance(document, Mapping):
        raise TypeError("package document must be a mapping")
    return rfc8785.dumps(_json_value(document, set()))


def context_package_digest(document: Mapping[str, object]) -> str:
    """RFC 8785-canonicalize and digest one Package without its digest field."""

    return hashlib.sha256(canonicalize_context_package(document)).hexdigest()


def verify_context_package_digest(
    document: Mapping[str, object], expected_digest: object
) -> bool:
    """Return whether an exact lowercase SHA-256 digest matches the document."""

    if (
        type(expected_digest) is not str
        or len(expected_digest) != hashlib.sha256().digest_size * 2
        or any(character not in "0123456789abcdef" for character in expected_digest)
    ):
        return False
    return hmac.compare_digest(context_package_digest(document), expected_digest)


def verify_context_package_public_document(document: object) -> bool:
    """Verify the digest over the exact decoded public Package representation."""

    if type(document) is not dict:
        return False
    package = cast(dict[object, object], document)
    if any(type(key) is not str for key in package):
        return False
    expected_digest = package.get("packageDigest")
    digest_document = {
        cast(str, key): value
        for key, value in package.items()
        if key != "packageDigest"
    }
    try:
        return verify_context_package_digest(digest_document, expected_digest)
    except (rfc8785.CanonicalizationError, TypeError, ValueError):
        return False


def complete_context_package_nullable_fields(
    document: dict[str, object],
) -> dict[str, object]:
    """Include frozen inactive nullable fields in a public Package document."""

    evidence = document.get("evidence")
    if not isinstance(evidence, list):
        raise TypeError("public ContextPackage Evidence must be an array")
    for item in evidence:
        if not isinstance(item, dict):
            raise TypeError("public ContextPackage Evidence must contain objects")
        item.setdefault("citationOpenRef", None)
    document["continuation"] = None
    return document


def validate_projected_field_refs(value: object) -> tuple[str, ...]:
    """Validate the frozen public projected-field identifier set."""

    if type(value) is not tuple or not value:
        raise ValueError("projected field refs must be a nonempty exact tuple")
    refs = value
    if len(refs) > MAX_PROJECTED_FIELD_REFS:
        raise ValueError(
            f"projected field refs must contain at most {MAX_PROJECTED_FIELD_REFS} "
            "items"
        )
    if any(
        type(ref) is not str
        or not ref
        or len(ref) > MAX_PROJECTED_FIELD_REF_LENGTH
        or ref[0] not in "abcdefghijklmnopqrstuvwxyz"
        or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789_"
            for character in ref
        )
        for ref in refs
    ):
        raise ValueError("projected field refs must use closed lowercase identifiers")
    if len(refs) != len(set(refs)):
        raise ValueError("projected field refs must be unique")
    return refs
