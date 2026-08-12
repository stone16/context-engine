"""Pure immutable Release profile bindings shared by Runtime and operators."""

from __future__ import annotations

from hashlib import sha256
from typing import Final

from engine.embedding_profiles import (
    DETERMINISTIC_TWIN_EMBEDDING_PROFILE,
    QWEN3_EMBEDDING_PROFILE,
)
from engine.tokenizer_accounting import (
    HISTORICAL_UTF8_BYTE_TOKENIZER_PROFILE_DIGEST,
    HISTORICAL_UTF8_BYTE_TOKENIZER_PROFILE_DOCUMENT,
    UNICODE_SCALAR_TOKENIZER_PROFILE,
)

RUNTIME_PROFILE_REF_V0: Final = "runtime-materialized-openapi-v0"
RUNTIME_PROFILE_REF_V1: Final = "runtime-materialized-openapi-v1"
RUNTIME_TOKENIZER_REF_V0: Final = "utf8-byte-budget-v1"
RUNTIME_TOKENIZER_REF_V1: Final = UNICODE_SCALAR_TOKENIZER_PROFILE.profile_ref
PACKAGE_SCHEMA_REF_V0: Final = "context-package-openapi-v0"
PACKAGE_SCHEMA_REF_V1: Final = "context-package-openapi-v1"
CONTENT_PROFILE_REF_V0: Final = "content-materialized-v0"
CONTENT_SCHEMA_REF_V0: Final = "context-content-schema-v1"
INDEX_PROFILE_REF_V0: Final = "index-exact-phrase-v0"
DOGFOOD_VECTOR_INDEX_PROFILE_REF_V1: Final = "index-file-pgvector-deterministic-twin-v1"
QWEN_VECTOR_INDEX_PROFILE_REF_V1: Final = "index-file-pgvector-qwen3-0.6b-v1"
INDEX_SCHEMA_REF_V0: Final = "context-index-schema-v1"
CONTENT_PROFILE_DIGEST_V0: Final = sha256(
    b"context-engine.content-profile.materialized-v0"
).hexdigest()
INDEX_PROFILE_DIGEST_V0: Final = sha256(
    b"context-engine.index-profile.exact-phrase-v0"
).hexdigest()
DOGFOOD_VECTOR_INDEX_PROFILE_DIGEST_V1: Final = sha256(
    b"context-engine.index-profile.file-pgvector-v1\x00"
    b"embedding-model:deterministic-twin-v1\x00"
    b"embedding-input:contextual-fragment-v1"
).hexdigest()
QWEN_VECTOR_INDEX_PROFILE_DIGEST_V1: Final = sha256(
    b"context-engine.index-profile.file-pgvector.v1\x00"
    + bytes.fromhex(QWEN3_EMBEDDING_PROFILE.profile_digest)
).hexdigest()
RUNTIME_PROFILE_DIGEST_V0: Final = sha256(
    b"context-engine.runtime-profile.materialized-openapi-v0"
).hexdigest()
RUNTIME_PROFILE_DIGEST_V1: Final = sha256(
    b"context-engine.runtime-profile.materialized-openapi-v1\x00"
    + bytes.fromhex(UNICODE_SCALAR_TOKENIZER_PROFILE.profile_digest)
).hexdigest()
CURATION_PROFILE_REF_V0: Final = "curation-off-v0"
CURATION_PROFILE_DIGEST_V0: Final = sha256(
    b"context-engine.curation-profile.off-v0"
).hexdigest()


__all__ = [
    "CONTENT_PROFILE_DIGEST_V0",
    "CONTENT_PROFILE_REF_V0",
    "CONTENT_SCHEMA_REF_V0",
    "CURATION_PROFILE_DIGEST_V0",
    "CURATION_PROFILE_REF_V0",
    "DETERMINISTIC_TWIN_EMBEDDING_PROFILE",
    "DOGFOOD_VECTOR_INDEX_PROFILE_DIGEST_V1",
    "DOGFOOD_VECTOR_INDEX_PROFILE_REF_V1",
    "HISTORICAL_UTF8_BYTE_TOKENIZER_PROFILE_DIGEST",
    "HISTORICAL_UTF8_BYTE_TOKENIZER_PROFILE_DOCUMENT",
    "INDEX_PROFILE_DIGEST_V0",
    "INDEX_PROFILE_REF_V0",
    "INDEX_SCHEMA_REF_V0",
    "PACKAGE_SCHEMA_REF_V0",
    "PACKAGE_SCHEMA_REF_V1",
    "QWEN3_EMBEDDING_PROFILE",
    "QWEN_VECTOR_INDEX_PROFILE_DIGEST_V1",
    "QWEN_VECTOR_INDEX_PROFILE_REF_V1",
    "RUNTIME_PROFILE_DIGEST_V0",
    "RUNTIME_PROFILE_DIGEST_V1",
    "RUNTIME_PROFILE_REF_V0",
    "RUNTIME_PROFILE_REF_V1",
    "RUNTIME_TOKENIZER_REF_V0",
    "RUNTIME_TOKENIZER_REF_V1",
    "UNICODE_SCALAR_TOKENIZER_PROFILE",
    "expected_qwen_release_bindings",
]


def expected_qwen_release_bindings() -> dict[str, object]:
    """Return the exact active local Qwen Release compatibility projection."""

    return {
        "content_profile_ref": CONTENT_PROFILE_REF_V0,
        "content_profile_digest": CONTENT_PROFILE_DIGEST_V0,
        "content_schema_ref": CONTENT_SCHEMA_REF_V0,
        "index_profile_ref": QWEN_VECTOR_INDEX_PROFILE_REF_V1,
        "index_profile_digest": QWEN_VECTOR_INDEX_PROFILE_DIGEST_V1,
        "index_schema_ref": INDEX_SCHEMA_REF_V0,
        "embedding_profile_document": QWEN3_EMBEDDING_PROFILE.canonical_document(),
        "embedding_profile_digest": QWEN3_EMBEDDING_PROFILE.profile_digest,
        "runtime_profile_ref": RUNTIME_PROFILE_REF_V1,
        "runtime_profile_digest": RUNTIME_PROFILE_DIGEST_V1,
        "runtime_tokenizer_ref": RUNTIME_TOKENIZER_REF_V1,
        "runtime_tokenizer_profile_document": (
            UNICODE_SCALAR_TOKENIZER_PROFILE.canonical_document()
        ),
        "runtime_tokenizer_profile_digest": (
            UNICODE_SCALAR_TOKENIZER_PROFILE.profile_digest
        ),
        "runtime_package_schema_ref": PACKAGE_SCHEMA_REF_V1,
        "curation_profile_ref": CURATION_PROFILE_REF_V0,
        "curation_profile_digest": CURATION_PROFILE_DIGEST_V0,
        "curation_mode": "curation_off",
        "curation_snapshot_ref": None,
        "curation_evaluation_digest": None,
        "compatible_revision_refs": [],
    }
