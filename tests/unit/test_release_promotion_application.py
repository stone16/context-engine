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
