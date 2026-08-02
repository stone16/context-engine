"""Transport-neutral primitives owned by ContextEngine's public contract."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from typing import Final, cast

import rfc8785

MAX_NARROWING_REFS: Final = 64
MAX_NARROWING_REF_LENGTH: Final = 256
MAX_OPAQUE_CAPABILITY_LENGTH: Final = 4_096
MAX_PROJECTED_FIELD_REFS: Final = 64
MAX_PROJECTED_FIELD_REF_LENGTH: Final = 64
PACKAGE_REF_PATTERN: Final = r"^pkg_[0-9a-f]{32}$"
DECISION_REF_PATTERN: Final = r"^dec_[0-9a-f]{32}$"


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
