"""Release-bound, network-free tokenizer profiles shared across engine loops."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from context_engine_contracts import (
    REGISTERED_V1_TOKENIZER_PROFILES,
    TokenizerProfileDescriptor,
)

_PROFILE_DIGEST_DOMAIN: Final = b"context-engine.tokenizer-profile.v1\x00"
_TOKENIZER_ARTIFACT_ROOT = Path(__file__).with_name("tokenizers")
_HISTORICAL_DOCUMENT: Final = (
    '{"accountingVersion":"utf8-byte-budget-v1",'
    '"artifactDigest":"' + hashlib.sha256(b"utf8-byte-budget-v1").hexdigest() + '",'
    '"countingContract":"historical-delivered-block-utf8-bytes-v1",'
    '"normalizationRef":"none","profileRef":"utf8-byte-budget-v1",'
    '"vocabularyRef":"utf8-octets"}'
)
HISTORICAL_UTF8_BYTE_TOKENIZER_PROFILE_DOCUMENT: Final = _HISTORICAL_DOCUMENT
HISTORICAL_UTF8_BYTE_TOKENIZER_PROFILE_DIGEST: Final = hashlib.sha256(
    _PROFILE_DIGEST_DOMAIN + _HISTORICAL_DOCUMENT.encode("utf-8")
).hexdigest()


class TokenizerUnavailable(RuntimeError):
    """One tokenizer identity or its pinned artifact is unavailable."""


@dataclass(frozen=True, slots=True)
class TokenizerProfile:
    """Immutable counting identity persisted in one Runtime Release profile."""

    descriptor: TokenizerProfileDescriptor
    artifact_path: Path = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self.descriptor) is not TokenizerProfileDescriptor:
            raise TypeError("TokenizerProfile requires TokenizerProfileDescriptor")
        if not isinstance(self.artifact_path, Path):
            raise TypeError("TokenizerProfile artifact_path must be Path")

    @property
    def profile_ref(self) -> str:
        return self.descriptor.profile_ref

    @property
    def artifact_digest(self) -> str:
        return self.descriptor.artifact_digest

    @property
    def vocabulary_ref(self) -> str:
        return self.descriptor.vocabulary_ref

    @property
    def normalization_ref(self) -> str:
        return self.descriptor.normalization_ref

    @property
    def accounting_version(self) -> str:
        return self.descriptor.accounting_version

    @property
    def counting_contract(self) -> str:
        return self.descriptor.counting_contract

    @property
    def profile_digest(self) -> str:
        return self.descriptor.profile_digest

    def canonical_document(self) -> dict[str, str]:
        return self.descriptor.canonical_document()

    def canonical_json(self) -> str:
        return self.descriptor.canonical_json()

    def load(self) -> AccountingTokenizer:
        try:
            artifact = self.artifact_path.read_bytes()
        except OSError:
            raise TokenizerUnavailable("Tokenizer artifact is unavailable") from None
        if hashlib.sha256(artifact).hexdigest() != self.artifact_digest:
            raise TokenizerUnavailable("Tokenizer artifact hash does not match")
        try:
            document = json.loads(artifact)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise TokenizerUnavailable("Tokenizer artifact is unavailable") from None
        if document != {
            "accountingVersion": self.accounting_version,
            "normalization": self.normalization_ref,
            "vocabulary": self.vocabulary_ref,
        }:
            raise TokenizerUnavailable("Tokenizer artifact identity does not match")
        return AccountingTokenizer(self)


@dataclass(frozen=True, slots=True)
class AccountingTokenizer:
    profile: TokenizerProfile

    def count(self, value: str | bytes) -> int:
        if type(value) is bytes:
            try:
                decoded = value.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                raise TokenizerUnavailable("Tokenizer input is not UTF-8") from None
        elif type(value) is str:
            decoded = value
        else:
            raise TypeError("Tokenizer input must be exact str or bytes")
        if any(0xD800 <= ord(character) <= 0xDFFF for character in decoded):
            raise TokenizerUnavailable("Tokenizer input is not Unicode scalar text")
        return len(decoded)


UNICODE_SCALAR_TOKENIZER_PROFILE = TokenizerProfile(
    descriptor=REGISTERED_V1_TOKENIZER_PROFILES[0],
    artifact_path=(
        _TOKENIZER_ARTIFACT_ROOT
        / REGISTERED_V1_TOKENIZER_PROFILES[0].artifact_name
    ),
)


def _runtime_profile(descriptor: TokenizerProfileDescriptor) -> TokenizerProfile:
    if descriptor is UNICODE_SCALAR_TOKENIZER_PROFILE.descriptor:
        return UNICODE_SCALAR_TOKENIZER_PROFILE
    return TokenizerProfile(
        descriptor=descriptor,
        artifact_path=_TOKENIZER_ARTIFACT_ROOT / descriptor.artifact_name,
    )


def registered_tokenizer_profile(
    canonical_document: str,
    profile_digest: str,
) -> TokenizerProfile:
    matches = tuple(
        descriptor
        for descriptor in REGISTERED_V1_TOKENIZER_PROFILES
        if descriptor.canonical_json() == canonical_document
        and descriptor.profile_digest == profile_digest
    )
    if len(matches) != 1:
        raise TokenizerUnavailable("Tokenizer profile is unavailable")
    return _runtime_profile(matches[0])


def registered_tokenizer_profile_by_identity(
    profile_ref: str,
    profile_digest: str,
) -> TokenizerProfile:
    """Resolve one active tokenizer identity without fixing a manifest generation."""

    matches = tuple(
        descriptor
        for descriptor in REGISTERED_V1_TOKENIZER_PROFILES
        if descriptor.profile_ref == profile_ref
        and descriptor.profile_digest == profile_digest
    )
    if len(matches) != 1:
        raise TokenizerUnavailable("Tokenizer profile is unavailable")
    return _runtime_profile(matches[0])


def load_registered_tokenizer(
    canonical_document: str,
    profile_digest: str,
) -> AccountingTokenizer:
    return registered_tokenizer_profile(canonical_document, profile_digest).load()


__all__ = [
    "AccountingTokenizer",
    "HISTORICAL_UTF8_BYTE_TOKENIZER_PROFILE_DIGEST",
    "HISTORICAL_UTF8_BYTE_TOKENIZER_PROFILE_DOCUMENT",
    "TokenizerProfile",
    "TokenizerUnavailable",
    "UNICODE_SCALAR_TOKENIZER_PROFILE",
    "load_registered_tokenizer",
    "registered_tokenizer_profile",
    "registered_tokenizer_profile_by_identity",
]
