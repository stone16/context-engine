from __future__ import annotations

from uuid import uuid4

import pytest

from applications.control import _parser
from applications.operator_authentication import (
    CONTROL_OPERATOR_SECRET_ENV,
    DOGFOOD_SECRET_ENV,
    RELEASE_OPERATOR_SECRET_ENV,
    WORKER_SECRET_ENV,
)
from applications.release_promotion import (
    RELEASE_EVALUATION_SIGNING_KEY_ENV,
    RELEASE_EVALUATION_SIGNING_KEY_VERSION_ENV,
    PublicContractVersion,
    ReleasePromotionConfigurationUnavailable,
    _keyring,
    _manifest,
)
from engine.runtime.release_lineage import (
    PACKAGE_SCHEMA_REF_V0,
    PACKAGE_SCHEMA_REF_V1,
    RUNTIME_PROFILE_DIGEST_V0,
    RUNTIME_PROFILE_DIGEST_V1,
    RUNTIME_PROFILE_REF_V0,
    RUNTIME_PROFILE_REF_V1,
    RUNTIME_TOKENIZER_REF_V0,
    RUNTIME_TOKENIZER_REF_V1,
)
from engine.tokenizer_accounting import (
    HISTORICAL_UTF8_BYTE_TOKENIZER_PROFILE_DIGEST,
    HISTORICAL_UTF8_BYTE_TOKENIZER_PROFILE_DOCUMENT,
    UNICODE_SCALAR_TOKENIZER_PROFILE,
)


@pytest.mark.security_evidence(id="ACCOUNTING-PROMOTION-V1-217", layer="property")
def test_release_promotion_builds_the_activatable_v1_runtime_profile() -> None:
    manifest = _manifest(
        uuid4(),
        ("revision:one",),
        PublicContractVersion.V1,
    )

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


def test_release_promotion_versions_have_stable_distinct_manifest_identity() -> None:
    organization_id = uuid4()
    revisions = ("revision:one",)

    v0 = _manifest(organization_id, revisions, PublicContractVersion.V0)
    repeated_v0 = _manifest(
        organization_id,
        revisions,
        PublicContractVersion.V0,
    )
    v1 = _manifest(organization_id, revisions, PublicContractVersion.V1)
    repeated_v1 = _manifest(
        organization_id,
        revisions,
        PublicContractVersion.V1,
    )

    assert v0.manifest_ref == (
        "manifest-dogfood-"
        "14aec463da860209f0fb59c3adfb136a4d26d1cce664f95ee411033f77f788d5"
    )
    assert repeated_v0.manifest_ref == v0.manifest_ref
    assert v1.manifest_ref == (
        "manifest-dogfood-v1-"
        "14aec463da860209f0fb59c3adfb136a4d26d1cce664f95ee411033f77f788d5"
    )
    assert repeated_v1.manifest_ref == v1.manifest_ref
    assert v1.manifest_ref != v0.manifest_ref
    assert (
        v0.runtime_profile.profile_ref,
        v0.runtime_profile.profile_digest,
        v0.runtime_profile.tokenizer_ref,
        v0.runtime_profile.tokenizer_profile_document,
        v0.runtime_profile.tokenizer_profile_digest,
        v0.runtime_profile.package_schema_ref,
    ) == (
        RUNTIME_PROFILE_REF_V0,
        RUNTIME_PROFILE_DIGEST_V0,
        RUNTIME_TOKENIZER_REF_V0,
        HISTORICAL_UTF8_BYTE_TOKENIZER_PROFILE_DOCUMENT,
        HISTORICAL_UTF8_BYTE_TOKENIZER_PROFILE_DIGEST,
        PACKAGE_SCHEMA_REF_V0,
    )


def test_release_promotion_refuses_an_open_version_string() -> None:
    with pytest.raises(ReleasePromotionConfigurationUnavailable):
        _manifest(uuid4(), ("revision:one",), "v0")  # type: ignore[arg-type]


def test_promote_release_cli_has_closed_versions_and_defaults_to_v0() -> None:
    parser = _parser()
    common_arguments = [
        "promote-release",
        "--organization-id",
        str(uuid4()),
        "--evidence-file",
        "release-evidence.json",
    ]

    defaulted = parser.parse_args(common_arguments)
    explicit_v1 = parser.parse_args(
        [*common_arguments, "--public-contract-version", "v1"]
    )

    assert defaulted.public_contract_version == PublicContractVersion.V0.value
    assert explicit_v1.public_contract_version == PublicContractVersion.V1.value
    with pytest.raises(SystemExit) as refused:
        parser.parse_args(
            [*common_arguments, "--public-contract-version", "v2"]
        )
    assert refused.value.code == 2


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
