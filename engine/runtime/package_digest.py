"""Deterministic digest profiles for ContextPackage and sensitive queries."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final, NoReturn, cast
from uuid import UUID

import rfc8785

PACKAGE_DIGEST_PROFILE: Final = "context-package-canonical-json-v1"
QUERY_DIGEST_PROFILE: Final = "context-query-json-hmac-sha256-v1"
_QUERY_DIGEST_DOMAIN: Final = b"context-engine.query-digest.v1\x00"
_MINIMUM_QUERY_KEY_BYTES: Final = 32
_MAX_KEY_VERSION: Final = (1 << 63) - 1


def _require_key_version(field_name: str, value: object) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_KEY_VERSION:
        raise ValueError(f"{field_name} must be a positive signed 64-bit integer")
    return value


class QueryDigestKeyring:
    """Explicit versioned query-digest keys with no ambient/default secret."""

    __slots__ = ("_active_version", "_keys")

    def __init__(self, *, active_version: int, keys: Mapping[int, bytes]) -> None:
        version = _require_key_version(
            "active query-digest key version", active_version
        )
        if not isinstance(keys, Mapping) or not keys:
            raise ValueError("query-digest keyring requires versioned keys")
        copied: dict[int, bytes] = {}
        for key_version, key in keys.items():
            canonical_version = _require_key_version(
                "query-digest key version", key_version
            )
            if type(key) is not bytes or len(key) < _MINIMUM_QUERY_KEY_BYTES:
                raise ValueError("query-digest keys require at least 256 bits")
            copied[canonical_version] = bytes(key)
        if version not in copied:
            raise ValueError("active query-digest key version must exist")
        self._active_version = version
        self._keys = MappingProxyType(copied)

    @property
    def active_version(self) -> int:
        return self._active_version

    def _active_key(self) -> bytes:
        return self._keys[self._active_version]

    def __repr__(self) -> str:
        return "QueryDigestKeyring(<redacted>)"

    def __reduce__(self) -> NoReturn:
        raise TypeError("QueryDigestKeyring is not serializable")


@dataclass(frozen=True, slots=True)
class QueryDigest:
    """Versioned Organization-bound digest safe to retain instead of a query."""

    value: str = field(repr=False)
    profile: str
    key_version: int = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.value) is not str
            or len(self.value) != hashlib.sha256().digest_size * 2
            or any(character not in "0123456789abcdef" for character in self.value)
        ):
            raise ValueError("query digest value must be lowercase SHA-256")
        if type(self.profile) is not str or self.profile != QUERY_DIGEST_PROFILE:
            raise ValueError("query digest profile must match the active schema")
        _require_key_version("query digest key version", self.key_version)


def _require_unicode_scalars(value: str) -> str:
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise ValueError("package document strings must contain Unicode scalar values")
    return value


type CanonicalJsonValue = (
    None
    | bool
    | int
    | float
    | str
    | list["CanonicalJsonValue"]
    | dict[str, "CanonicalJsonValue"]
)


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


def query_digest(
    keyring: QueryDigestKeyring,
    organization_id: UUID,
    query: str,
) -> QueryDigest:
    """HMAC the exact query under one Organization and versioned key.

    The authenticated bytes are ``context-engine.query-digest.v1\\0``, the
    Organization UUID's 16 network-order bytes, and the query represented as a
    canonical ``ensure_ascii=True`` JSON string. Query text is never normalized.
    """

    if type(keyring) is not QueryDigestKeyring:
        raise TypeError("keyring must be QueryDigestKeyring")
    if type(organization_id) is not UUID:
        raise TypeError("organization_id must be UUID")
    if type(query) is not str:
        raise TypeError("query must be an exact string")
    encoded_query = json.dumps(
        query,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("ascii")
    value = hmac.digest(
        keyring._active_key(),
        _QUERY_DIGEST_DOMAIN + organization_id.bytes + encoded_query,
        "sha256",
    ).hex()
    return QueryDigest(
        value=value,
        profile=QUERY_DIGEST_PROFILE,
        key_version=keyring.active_version,
    )
