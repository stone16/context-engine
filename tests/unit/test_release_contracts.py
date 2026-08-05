from __future__ import annotations

import pickle
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest

from engine.learning import (
    RELEASE_EVALUATION_DIGEST_PROFILE,
    RELEASE_EVALUATION_SIGNATURE_PROFILE,
    ContentProfileRef,
    CurationProfileRef,
    Gate,
    GateEvidence,
    GateStatus,
    IndexProfileRef,
    PromotionReceipt,
    ReleaseCandidate,
    ReleaseEvaluationKeyring,
    ReleaseManifest,
    RuntimeProfileRef,
    TrustedPromotionCall,
    candidate_document,
    evaluate_candidate,
    evaluation_document,
    verify_release_evaluation,
)

ORGANIZATION_ID = UUID("33c7b365-c705-45af-b676-067fd510f683")


def _profiles() -> tuple[ContentProfileRef, IndexProfileRef, RuntimeProfileRef]:
    content = ContentProfileRef(
        profile_ref="content-m0-empty-v1",
        profile_digest="1" * 64,
        content_schema_ref="context-content-schema-v1",
    )
    index = IndexProfileRef(
        profile_ref="index-m0-empty-v1",
        profile_digest="2" * 64,
        content_profile_digest=content.profile_digest,
        content_schema_ref=content.content_schema_ref,
        index_schema_ref="context-index-schema-v1",
    )
    runtime = RuntimeProfileRef(
        profile_ref="runtime-m0-empty-v1",
        profile_digest="3" * 64,
        content_profile_digest=content.profile_digest,
        index_profile_digest=index.profile_digest,
        content_schema_ref=content.content_schema_ref,
        index_schema_ref=index.index_schema_ref,
        tokenizer_ref="empty-tokenizer-v1",
        package_schema_ref="context-package-v1",
    )
    return content, index, runtime


def test_explicit_m0_empty_manifest_keeps_profiles_and_curation_off() -> None:
    content, index, runtime = _profiles()

    manifest = ReleaseManifest.m0_empty(
        organization_id=ORGANIZATION_ID,
        manifest_ref="manifest-m0-empty-v1",
        content_profile=content,
        index_profile=index,
        runtime_profile=runtime,
        curation_profile=CurationProfileRef.off(
            profile_ref="curation-off-v1",
            profile_digest="4" * 64,
        ),
    )

    assert manifest.content_profile is content
    assert manifest.index_profile is index
    assert manifest.runtime_profile is runtime
    assert manifest.curation_profile.is_curation_off
    assert manifest.active_revision_refs == ()
    assert len(manifest.manifest_digest) == 64
    assert len(manifest.lineage_digest) == 64
    with pytest.raises(FrozenInstanceError):
        manifest.manifest_ref = "mutated"  # type: ignore[misc]


def test_manifest_rejects_cross_profile_compatibility_substitution() -> None:
    content, index, runtime = _profiles()
    incompatible_runtime = RuntimeProfileRef(
        profile_ref=runtime.profile_ref,
        profile_digest=runtime.profile_digest,
        content_profile_digest=content.profile_digest,
        index_profile_digest="9" * 64,
        content_schema_ref=content.content_schema_ref,
        index_schema_ref=index.index_schema_ref,
        tokenizer_ref=runtime.tokenizer_ref,
        package_schema_ref=runtime.package_schema_ref,
    )

    with pytest.raises(ValueError, match="RuntimeProfile.*IndexProfile"):
        ReleaseManifest.m0_empty(
            organization_id=ORGANIZATION_ID,
            manifest_ref="manifest-m0-empty-v1",
            content_profile=content,
            index_profile=index,
            runtime_profile=incompatible_runtime,
            curation_profile=CurationProfileRef.off(
                profile_ref="curation-off-v1",
                profile_digest="4" * 64,
            ),
        )


def test_index_profile_rejects_document_digest_substitution() -> None:
    _content, index, _runtime = _profiles()

    with pytest.raises(ValueError, match="[Ee]mbedding provider profile"):
        replace(index, embedding_profile_document='{"modelId":"substituted"}')

    with pytest.raises(ValueError, match="[Ee]mbedding provider profile"):
        replace(index, embedding_profile_digest="f" * 64)


def _candidate(*, generation: int = 0) -> ReleaseCandidate:
    content, index, runtime = _profiles()
    manifest = ReleaseManifest.m0_empty(
        organization_id=ORGANIZATION_ID,
        manifest_ref="manifest-m0-empty-v1",
        content_profile=content,
        index_profile=index,
        runtime_profile=runtime,
        curation_profile=CurationProfileRef.off(
            profile_ref="curation-off-v1",
            profile_digest="4" * 64,
        ),
    )
    return ReleaseCandidate(
        organization_id=ORGANIZATION_ID,
        candidate_ref="candidate-m0-empty-v1",
        manifest=manifest,
        expected_active_generation=generation,
        expected_base_manifest_digest=(None if generation == 0 else "b" * 64),
        gate_evidence=tuple(
            GateEvidence(
                gate=gate,
                status=GateStatus.PASS,
                evidence_digest=f"{index + 5:x}" * 64,
            )
            for index, gate in enumerate(Gate)
        ),
        capability_coverage_digest="9" * 64,
        fixture_digest="a" * 64,
        verification_commands=("make lint", "make typecheck", "make test"),
    )


def test_candidate_and_evaluation_have_stable_canonical_signed_vectors() -> None:
    candidate = _candidate()
    keyring = ReleaseEvaluationKeyring(
        active_version=7,
        keys={7: b"evaluation-signing-domain-key-at-least-32-bytes"},
    )

    evaluation = evaluate_candidate(
        candidate,
        keyring=keyring,
        evaluated_at=datetime(2026, 7, 22, 15, 30, tzinfo=UTC),
    )

    assert set(candidate_document(candidate)) == {
        "candidate_ref",
        "capability_coverage_digest",
        "expected_active_generation",
        "expected_base_manifest_digest",
        "fixture_digest",
        "gate_evidence",
        "manifest_digest",
        "manifest_ref",
        "organization_id",
        "verification_commands",
    }
    assert set(evaluation_document(evaluation)) == {
        "candidate_digest",
        "candidate_ref",
        "capability_coverage_digest",
        "compatibility_evidence_digest",
        "compatibility_passed",
        "evaluated_at",
        "expected_active_generation",
        "expected_base_manifest_digest",
        "fixture_digest",
        "gate_evidence",
        "manifest_digest",
        "manifest_ref",
        "organization_id",
        "verification_commands",
    }
    assert candidate.candidate_digest == (
        "d86579d77af81fc5f5ee63cf90d5968daf05424fcd9cf29ca28d2030edf11a2c"
    )
    assert evaluation.evaluation_digest == (
        "68694c3e7430fdcf835236d0aad6cd1030cd9dcd9dbcc9bdc7a0cce773cc8056"
    )
    assert evaluation.signature.hex() == (
        "d56f71a0c3852df474f540602930cc76724ac3ab0c2b57926a6d4afa19afd3c9"
    )
    assert evaluation.digest_profile == RELEASE_EVALUATION_DIGEST_PROFILE
    assert evaluation.signature_profile == RELEASE_EVALUATION_SIGNATURE_PROFILE
    assert repr(evaluation) == "ReleaseEvaluation(<redacted>)"
    assert verify_release_evaluation(
        evaluation,
        candidate=candidate,
        keyring=keyring,
    )


def test_signed_bigint_generation_is_canonicalized_as_decimal_text() -> None:
    generation = (1 << 63) - 2
    candidate = _candidate(generation=generation)
    keyring = ReleaseEvaluationKeyring(
        active_version=(1 << 63) - 1,
        keys={(1 << 63) - 1: b"evaluation-signing-domain-key-at-least-32-bytes"},
    )

    evaluation = evaluate_candidate(
        candidate,
        keyring=keyring,
        evaluated_at=datetime(2026, 7, 22, 15, 30, tzinfo=UTC),
    )

    assert candidate_document(candidate)["expected_active_generation"] == str(
        generation
    )
    assert evaluation_document(evaluation)["expected_active_generation"] == str(
        generation
    )
    assert verify_release_evaluation(
        evaluation,
        candidate=candidate,
        keyring=keyring,
    )


def test_evaluation_keyring_has_no_default_fallback_or_cross_domain_reuse() -> None:
    candidate = _candidate()
    signer = ReleaseEvaluationKeyring(
        active_version=7,
        keys={7: b"evaluation-signing-domain-key-at-least-32-bytes"},
    )
    evaluation = evaluate_candidate(
        candidate,
        keyring=signer,
        evaluated_at=datetime(2026, 7, 22, 15, 30, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="explicit versioned keys"):
        ReleaseEvaluationKeyring(active_version=7, keys={})
    with pytest.raises(ValueError, match="active.*must exist"):
        ReleaseEvaluationKeyring(
            active_version=8,
            keys={7: b"evaluation-signing-domain-key-at-least-32-bytes"},
        )
    with pytest.raises(ValueError, match="256 bits"):
        ReleaseEvaluationKeyring(active_version=7, keys={7: b"short"})

    unknown_version_keyring = ReleaseEvaluationKeyring(
        active_version=8,
        keys={8: b"other-explicit-evaluation-key-at-least-32-bytes"},
    )
    assert not verify_release_evaluation(
        evaluation,
        candidate=candidate,
        keyring=unknown_version_keyring,
    )
    assert "evaluation-signing-domain-key" not in repr(signer)
    with pytest.raises(TypeError, match="not serializable"):
        pickle.dumps(signer)


def test_trusted_promotion_call_and_receipt_are_not_caller_constructible() -> None:
    with pytest.raises(TypeError, match="authority-constructed"):
        cast(Any, TrustedPromotionCall)()
    with pytest.raises(TypeError, match="commit-constructed"):
        cast(Any, PromotionReceipt)()
