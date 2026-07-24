"""Canonical codecs shared by opaque internal protocol values."""

from __future__ import annotations

import base64
import binascii


def encode_base64url(value: bytes) -> str:
    """Encode canonical unpadded base64url."""

    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def decode_base64url(value: str) -> bytes:
    """Decode only canonical unpadded base64url."""

    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    if not value or any(character not in alphabet for character in value):
        raise ValueError("invalid base64url")
    try:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, ValueError):
        raise ValueError("invalid base64url") from None
    if encode_base64url(decoded) != value:
        raise ValueError("noncanonical base64url")
    return decoded
