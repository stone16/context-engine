"""Private canonical signing primitives for distinct Runtime ticket protocols."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Final, NoReturn
from uuid import UUID

_ALGORITHM: Final = "HS256"
_PROTOCOL_VERSION: Final = 1
_HEADER_FIELDS: Final = frozenset({"alg", "dom", "kid", "typ", "v"})
_MAX_KEY_VERSION: Final = (1 << 63) - 1
_MINIMUM_KEY_BYTES: Final = 32
_NONCE_BYTES: Final = 32
_MAX_TOKEN_LENGTH: Final = 8192


def _require_positive_bigint(field_name: str, value: object) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_KEY_VERSION:
        raise ValueError(f"{field_name} must be a positive signed 64-bit integer")
    return value


def _require_identifier(
    field_name: str,
    value: object,
    *,
    maximum_length: int,
) -> str:
    if (
        type(value) is not str
        or not value
        or value.isspace()
        or len(value) > maximum_length
        or any(character.isspace() for character in value)
    ):
        raise ValueError(f"{field_name} must be a bounded nonblank identifier")
    return value


def _require_uuid(field_name: str, value: object) -> UUID:
    if type(value) is not UUID:
        raise TypeError(f"{field_name} must be UUID")
    return value


def _require_utc(field_name: str, value: object) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
        or value.microsecond != 0
    ):
        raise ValueError(f"{field_name} must be whole-second UTC")
    return value


def _require_opaque_ticket_value(field_name: str, value: object) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > _MAX_TOKEN_LENGTH
        or any(character.isspace() for character in value)
    ):
        raise ValueError(f"{field_name} must be a bounded opaque value")
    return value


class TicketSigningKeyring:
    """Explicit versioned ticket keys with no ambient/default secret."""

    __slots__ = ("_active_version", "_keys")

    def __init__(self, *, active_version: int, keys: Mapping[int, bytes]) -> None:
        version = _require_positive_bigint("active key version", active_version)
        if not isinstance(keys, Mapping) or not keys:
            raise ValueError("ticket keyring requires versioned keys")
        copied: dict[int, bytes] = {}
        for key_version, secret in keys.items():
            canonical_version = _require_positive_bigint(
                "signing key version", key_version
            )
            if type(secret) is not bytes or len(secret) < _MINIMUM_KEY_BYTES:
                raise ValueError("ticket signing keys require at least 256 bits")
            copied[canonical_version] = bytes(secret)
        if version not in copied:
            raise ValueError("active ticket key version must exist")
        self._active_version = version
        self._keys = MappingProxyType(copied)

    @property
    def active_version(self) -> int:
        return self._active_version

    def _key_for(self, version: int) -> bytes | None:
        return self._keys.get(version)

    def __repr__(self) -> str:
        return "TicketSigningKeyring(<redacted>)"

    def __reduce__(self) -> NoReturn:
        raise TypeError("TicketSigningKeyring is not serializable")


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: object) -> bytes:
    if type(value) is not str or not value or "=" in value:
        raise ValueError
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    if any(character not in alphabet for character in value):
        raise ValueError
    decoded = base64.b64decode(
        value + "=" * (-len(value) % 4),
        altchars=b"-_",
        validate=True,
    )
    if _base64url_encode(decoded) != value:
        raise ValueError
    return decoded


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError
        document[key] = value
    return document


def _decode_document(
    encoded: str,
    expected_fields: frozenset[str],
) -> dict[str, object]:
    raw = _base64url_decode(encoded)
    document = json.loads(raw, object_pairs_hook=_reject_duplicate_pairs)
    if type(document) is not dict or frozenset(document) != expected_fields:
        raise ValueError
    if _canonical_json(document) != raw:
        raise ValueError
    return document


def _mint_signed_ticket(
    keyring: TicketSigningKeyring,
    *,
    domain: str,
    token_type: str,
    claims: Mapping[str, object],
) -> str:
    version = keyring.active_version
    key = keyring._key_for(version)
    if key is None:  # pragma: no cover - keyring construction proves this
        raise ValueError("active ticket signing key is unavailable")
    header = {
        "alg": _ALGORITHM,
        "dom": domain,
        "kid": version,
        "typ": token_type,
        "v": _PROTOCOL_VERSION,
    }
    encoded_header = _base64url_encode(_canonical_json(header))
    encoded_claims = _base64url_encode(_canonical_json(claims))
    signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
    signature = hmac.digest(key, signing_input, "sha256")
    return f"{encoded_header}.{encoded_claims}.{_base64url_encode(signature)}"


def _verify_signed_ticket(
    value: str,
    keyring: TicketSigningKeyring,
    *,
    domain: str,
    token_type: str,
    claim_fields: frozenset[str],
) -> dict[str, object]:
    _require_opaque_ticket_value("ticket", value)
    encoded_header, encoded_claims, encoded_signature = value.split(".")
    header = _decode_document(encoded_header, _HEADER_FIELDS)
    if type(header["v"]) is not int or header["v"] != _PROTOCOL_VERSION:
        raise ValueError
    key_version = _require_positive_bigint("signing key version", header["kid"])
    if header != {
        "alg": _ALGORITHM,
        "dom": domain,
        "kid": key_version,
        "typ": token_type,
        "v": _PROTOCOL_VERSION,
    }:
        raise ValueError
    key = keyring._key_for(key_version)
    if key is None:
        raise ValueError
    supplied_signature = _base64url_decode(encoded_signature)
    if len(supplied_signature) != hashlib.sha256().digest_size:
        raise ValueError
    signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
    expected_signature = hmac.digest(key, signing_input, "sha256")
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise ValueError
    document = _decode_document(encoded_claims, claim_fields)
    if (
        type(document["signing_key_version"]) is not int
        or document["signing_key_version"] != key_version
    ):
        raise ValueError
    return document


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_timestamp(value: object) -> datetime:
    if type(value) is not str:
        raise ValueError
    parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    if _timestamp(parsed) != value:
        raise ValueError
    return parsed


def _parse_uuid(value: object) -> UUID:
    if type(value) is not str:
        raise ValueError
    parsed = UUID(value)
    if str(parsed) != value:
        raise ValueError
    return parsed


def _generate_nonce() -> bytes:
    return secrets.token_bytes(_NONCE_BYTES)


def _require_nonce(value: object) -> bytes:
    if type(value) is not bytes or len(value) != _NONCE_BYTES:
        raise ValueError("ticket nonce must contain exactly 256 bits")
    return value


def _parse_nonce(value: object) -> bytes:
    return _require_nonce(_base64url_decode(value))


def _nonce_document(value: bytes) -> str:
    return _base64url_encode(_require_nonce(value))


_EXPECTED_DECODING_ERRORS = (
    ValueError,
    TypeError,
    UnicodeError,
    RecursionError,
    binascii.Error,
    json.JSONDecodeError,
)
