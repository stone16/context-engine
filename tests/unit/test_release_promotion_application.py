from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from applications.operator_authentication import (
    CONTROL_OPERATOR_SECRET_ENV,
    DOGFOOD_SECRET_ENV,
    RELEASE_OPERATOR_SECRET_ENV,
    WORKER_SECRET_ENV,
)
from applications.release_promotion import (
    RELEASE_EVALUATION_SIGNING_KEY_ENV,
    RELEASE_EVALUATION_SIGNING_KEY_VERSION_ENV,
    ReleasePromotionConfigurationUnavailable,
    _keyring,
    _manifest,
)
from engine.runtime.release_lineage import (
    PACKAGE_SCHEMA_REF_V1,
    RUNTIME_PROFILE_DIGEST_V1,
    RUNTIME_PROFILE_REF_V1,
    RUNTIME_TOKENIZER_REF_V1,
)
from engine.tokenizer_accounting import UNICODE_SCALAR_TOKENIZER_PROFILE

ROOT = Path(__file__).parents[2]


@pytest.mark.security_evidence(id="ACCOUNTING-PROMOTION-V1-217", layer="property")
def test_release_promotion_builds_the_activatable_v1_runtime_profile() -> None:
    manifest = _manifest(uuid4(), ("revision:one",))

    assert manifest.runtime_profile.profile_ref == RUNTIME_PROFILE_REF_V1
    assert manifest.runtime_profile.profile_digest == RUNTIME_PROFILE_DIGEST_V1
    assert manifest.runtime_profile.tokenizer_ref == RUNTIME_TOKENIZER_REF_V1
    assert manifest.runtime_profile.package_schema_ref == PACKAGE_SCHEMA_REF_V1
    assert (
        manifest.runtime_profile.tokenizer_profile_document
        == UNICODE_SCALAR_TOKENIZER_PROFILE.canonical_json()
    )
    assert (
        manifest.runtime_profile.tokenizer_profile_digest
        == UNICODE_SCALAR_TOKENIZER_PROFILE.profile_digest
    )


def test_dogfood_api_stays_v0_while_bot_delivery_consumes_v1() -> None:
    composition = (ROOT / "adapters/http/dogfood.py").read_text(encoding="utf-8")
    caller = (ROOT / "adapters/http/dogfood_client.py").read_text(encoding="utf-8")
    v1_sdk = (ROOT / "sdk/typescript-v1/src/generated/sdk.gen.ts").read_text(
        encoding="utf-8"
    )
    bot = (ROOT / "bot_delivery/typescript/src/main.ts").read_text(encoding="utf-8")

    assert 'public_contract_version="v1"' not in composition
    assert 'f"{self._configuration.base_url}/v0/resolve"' in caller
    assert "url: '/v1/resolve'" in v1_sdk
    assert 'from "@context-engine/resolve-sdk-v1"' in bot


def test_evaluation_key_refuses_identical_hex_encoded_operator_secret() -> None:
    evaluation_key = "ab" * 32
    environment = {
        RELEASE_EVALUATION_SIGNING_KEY_VERSION_ENV: "1",
        RELEASE_EVALUATION_SIGNING_KEY_ENV: evaluation_key,
        CONTROL_OPERATOR_SECRET_ENV: "control-secret-value",
        RELEASE_OPERATOR_SECRET_ENV: evaluation_key,
        DOGFOOD_SECRET_ENV: "dogfood-secret-value",
        WORKER_SECRET_ENV: "cd" * 32,
    }

    with pytest.raises(ReleasePromotionConfigurationUnavailable):
        _keyring(environment)


def test_evaluation_key_refuses_hex_encoding_of_raw_operator_secret() -> None:
    raw_operator_secret = "a" * 32
    environment = {
        RELEASE_EVALUATION_SIGNING_KEY_VERSION_ENV: "1",
        RELEASE_EVALUATION_SIGNING_KEY_ENV: raw_operator_secret.encode().hex(),
        CONTROL_OPERATOR_SECRET_ENV: "control-secret-value",
        RELEASE_OPERATOR_SECRET_ENV: raw_operator_secret,
        DOGFOOD_SECRET_ENV: "dogfood-secret-value",
        WORKER_SECRET_ENV: "cd" * 32,
    }

    with pytest.raises(ReleasePromotionConfigurationUnavailable):
        _keyring(environment)


def test_evaluation_key_refuses_worker_secret_collision() -> None:
    worker_secret = "cd" * 32
    environment = {
        RELEASE_EVALUATION_SIGNING_KEY_VERSION_ENV: "1",
        RELEASE_EVALUATION_SIGNING_KEY_ENV: worker_secret,
        CONTROL_OPERATOR_SECRET_ENV: "control-secret-value",
        RELEASE_OPERATOR_SECRET_ENV: "release-secret-value",
        DOGFOOD_SECRET_ENV: "dogfood-secret-value",
        WORKER_SECRET_ENV: worker_secret,
    }

    with pytest.raises(ReleasePromotionConfigurationUnavailable):
        _keyring(environment)


def test_distinct_unicode_secrets_yield_the_active_keyring() -> None:
    environment = {
        RELEASE_EVALUATION_SIGNING_KEY_VERSION_ENV: "1",
        RELEASE_EVALUATION_SIGNING_KEY_ENV: "ab" * 32,
        CONTROL_OPERATOR_SECRET_ENV: "control-secret-密码-value-at-least-32-bytes",
        RELEASE_OPERATOR_SECRET_ENV: "release-secret-密码-value-at-least-32-bytes",
        DOGFOOD_SECRET_ENV: "dogfood-secret-密码-value-at-least-32-bytes",
        WORKER_SECRET_ENV: "cd" * 32,
    }

    keyring = _keyring(environment)

    assert keyring.active_version == 1
