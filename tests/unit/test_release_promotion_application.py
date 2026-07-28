from __future__ import annotations

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
)


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
