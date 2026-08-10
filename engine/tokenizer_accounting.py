"""Release-bound, network-free tokenizer profiles shared across engine loops."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, cast

import rfc8785

_PROFILE_DIGEST_DOMAIN: Final = b"context-engine.tokenizer-profile.v1\x00"
_ARTIFACT = Path(__file__).with_name("tokenizers") / "unicode-scalar-v1.json"
_ARTIFACT_DIGEST: Final = (
    "8d301e3ce94e5b48febffb2e0871e139cd4d5f808084eccbf9cfc512d3948cca"
)
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


def _require_nonblank(field_name: str, value: object) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or any(character.isspace() for character in value)
    ):
        raise ValueError(f"TokenizerProfile {field_name} must be an opaque ref")
    return value


def _require_digest(field_name: str, value: object) -> str:
    if (
        type(value) is not str
        or len(value) != hashlib.sha256().digest_size * 2
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"TokenizerProfile {field_name} must be lowercase SHA-256")
    return value


@dataclass(frozen=True, slots=True)
class TokenizerProfile:
    """Immutable counting identity persisted in one Runtime Release profile."""

    profile_ref: str
    artifact_digest: str
    vocabulary_ref: str
    normalization_ref: str
    accounting_version: str
    counting_contract: str
    artifact_path: Path = field(repr=False, compare=False)
    profile_digest: str = field(init=False)

    def __post_init__(self) -> None:
        for field_name in (
            "profile_ref",
            "vocabulary_ref",
            "normalization_ref",
            "accounting_version",
            "counting_contract",
        ):
            _require_nonblank(field_name, getattr(self, field_name))
        _require_digest("artifact_digest", self.artifact_digest)
        if not isinstance(self.artifact_path, Path):
            raise TypeError("TokenizerProfile artifact_path must be Path")
        object.__setattr__(
            self,
            "profile_digest",
            hashlib.sha256(
                _PROFILE_DIGEST_DOMAIN
                + rfc8785.dumps(cast(Any, self.canonical_document()))
            ).hexdigest(),
        )

    def canonical_document(self) -> dict[str, object]:
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
            "normalization": "none",
            "vocabulary": "unicode-scalars-v15.1",
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
    profile_ref="unicode-scalar-tokenizer-v1",
    artifact_digest=_ARTIFACT_DIGEST,
    vocabulary_ref="unicode-scalars-v15.1",
    normalization_ref="none",
    accounting_version="unicode-scalar-accounting-v1",
    counting_contract="one-unicode-scalar-one-token-v1",
    artifact_path=_ARTIFACT,
)

_REGISTERED_PROFILES: Final = {
    (
        UNICODE_SCALAR_TOKENIZER_PROFILE.canonical_json(),
        UNICODE_SCALAR_TOKENIZER_PROFILE.profile_digest,
    ): UNICODE_SCALAR_TOKENIZER_PROFILE,
}


def registered_tokenizer_profile(
    canonical_document: str,
    profile_digest: str,
) -> TokenizerProfile:
    try:
        return _REGISTERED_PROFILES[(canonical_document, profile_digest)]
    except (KeyError, TypeError):
        raise TokenizerUnavailable("Tokenizer profile is unavailable") from None


def registered_tokenizer_profile_by_identity(
    profile_ref: str,
    profile_digest: str,
) -> TokenizerProfile:
    """Resolve one active tokenizer identity without fixing a manifest generation."""

    matches = tuple(
        profile
        for profile in _REGISTERED_PROFILES.values()
        if profile.profile_ref == profile_ref
        and profile.profile_digest == profile_digest
    )
    if len(matches) != 1:
        raise TokenizerUnavailable("Tokenizer profile is unavailable")
    return matches[0]


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
