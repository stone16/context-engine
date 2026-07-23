"""Canonical Package payload digests shared by issuance and trusted consumers."""

from __future__ import annotations

import hashlib
from typing import Any, cast

import rfc8785

from engine.runtime.contracts import ContextPackage, context_package_public_document

MODEL_INPUT_DIGEST_DOMAIN = b"context-engine.authorized-model-input.v1\x00"
CHANNEL_PAYLOAD_DIGEST_DOMAIN = b"context-engine.authorized-channel-payload.v1\x00"


def canonical_package_payload(package: ContextPackage) -> bytes:
    if type(package) is not ContextPackage:
        raise TypeError("egress payload requires ContextPackage")
    return rfc8785.dumps(cast(Any, context_package_public_document(package)))


def model_input_digest(package: ContextPackage) -> str:
    return hashlib.sha256(
        MODEL_INPUT_DIGEST_DOMAIN + canonical_package_payload(package)
    ).hexdigest()


def channel_payload_digest(package: ContextPackage) -> str:
    return hashlib.sha256(
        CHANNEL_PAYLOAD_DIGEST_DOMAIN + canonical_package_payload(package)
    ).hexdigest()


def model_payload_bytes_digest(payload: bytes) -> str:
    if type(payload) is not bytes:
        raise TypeError("model egress payload must be bytes")
    return hashlib.sha256(MODEL_INPUT_DIGEST_DOMAIN + payload).hexdigest()


def channel_payload_bytes_digest(payload: bytes) -> str:
    if type(payload) is not bytes:
        raise TypeError("channel egress payload must be bytes")
    return hashlib.sha256(CHANNEL_PAYLOAD_DIGEST_DOMAIN + payload).hexdigest()
