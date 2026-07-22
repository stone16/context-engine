from __future__ import annotations

import json
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from engine.learning import (
    ContentProfileRef,
    ContextLearning,
    CurationProfileRef,
    Gate,
    GateEvidence,
    GateStatus,
    IndexProfileRef,
    ReleaseCandidate,
    ReleaseCandidateRef,
    ReleaseEvaluation,
    ReleaseEvaluationKeyring,
    ReleaseEvaluationUnavailable,
    ReleaseManifest,
    ReleaseOperatorAuthority,
    ReleasePromotionTransactionPort,
    RuntimeProfileRef,
)

ROOT = Path(__file__).parents[2]
ORGANIZATION_A = UUID("33c7b365-c705-45af-b676-067fd510f683")
ORGANIZATION_B = UUID("d50e9030-c75c-422a-af06-cd3d7463ad73")
EVALUATED_AT = datetime(2026, 7, 22, 16, 0, tzinfo=UTC)
SIGNING_KEY = b"m0-learning-isolation-key-at-least-32-bytes"
M0_LEARNING_TABLES = {
    "active_release_manifest",
    "release_candidate",
    "release_evaluation",
    "release_manifest",
    "release_operator_grant",
    "release_promotion_audit",
}
FORBIDDEN_CROSS_ORGANIZATION_CARRIERS = {
    "feedback_artifact",
    "global_artifact",
    "learning_export",
}


def _candidate(organization_id: UUID, *, suffix: str) -> ReleaseCandidate:
    content = ContentProfileRef(
        profile_ref=f"content-{suffix}",
        profile_digest="1" * 64,
        content_schema_ref="context-content-schema-v1",
    )
    index = IndexProfileRef(
        profile_ref=f"index-{suffix}",
        profile_digest="2" * 64,
        content_profile_digest=content.profile_digest,
        content_schema_ref=content.content_schema_ref,
        index_schema_ref="context-index-schema-v1",
    )
    runtime = RuntimeProfileRef(
        profile_ref=f"runtime-{suffix}",
        profile_digest="3" * 64,
        content_profile_digest=content.profile_digest,
        index_profile_digest=index.profile_digest,
        content_schema_ref=content.content_schema_ref,
        index_schema_ref=index.index_schema_ref,
        tokenizer_ref="empty-tokenizer-v1",
        package_schema_ref="context-package-v1",
    )
    manifest = ReleaseManifest.m0_empty(
        organization_id=organization_id,
        manifest_ref=f"manifest-{suffix}",
        content_profile=content,
        index_profile=index,
        runtime_profile=runtime,
        curation_profile=CurationProfileRef.off(
            profile_ref=f"curation-off-{suffix}",
            profile_digest="4" * 64,
        ),
    )
    return ReleaseCandidate(
        organization_id=organization_id,
        candidate_ref=f"candidate-{suffix}",
        manifest=manifest,
        expected_active_generation=0,
        expected_base_manifest_digest=None,
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
        verification_commands=("make check",),
    )


class _CrossOrganizationStore:
    def __init__(self, returned_candidate: ReleaseCandidate) -> None:
        self.returned_candidate = returned_candidate
        self.evaluations: list[ReleaseEvaluation] = []

    def load_candidate(self, candidate_ref: ReleaseCandidateRef) -> ReleaseCandidate:
        del candidate_ref
        return self.returned_candidate

    def persist_evaluation(self, evaluation: ReleaseEvaluation) -> None:
        self.evaluations.append(evaluation)

    def transaction(
        self,
        organization_id: UUID,
    ) -> AbstractContextManager[ReleasePromotionTransactionPort]:
        del organization_id
        raise AssertionError("cross-Organization evaluation opened publication")


class _NeverAuthenticator:
    def authenticate(self, opaque_credential: str) -> object:
        del opaque_credential
        raise AssertionError("evaluation attempted release-operator authentication")


@pytest.mark.security_evidence(id="PROP-CROSS-ORG-LEARN-015", layer="property")
def test_m0_learning_artifact_contract_has_no_cross_organization_carrier() -> None:
    """M0 has only Organization-owned release lineage and no global raw carrier."""

    manifest = json.loads(
        (ROOT / "engine/persistence/schema_security_manifest.yaml").read_text(
            encoding="utf-8"
        )
    )
    migration = (
        ROOT
        / "migrations/versions/20260722_0009_learning_release_promotion.py"
    ).read_text(encoding="utf-8")

    tables = {entry["name"]: entry for entry in manifest["tables"]}
    assert set(tables) >= M0_LEARNING_TABLES
    for table in M0_LEARNING_TABLES:
        assert tables[table]["classification"] == "tenant_owned"
        assert tables[table]["organizationColumn"] == "organization_id"
    public_tables = set(tables)
    assert FORBIDDEN_CROSS_ORGANIZATION_CARRIERS.isdisjoint(public_tables)
    assert all(
        name not in migration.casefold()
        for name in FORBIDDEN_CROSS_ORGANIZATION_CARRIERS
    )


@pytest.mark.security_evidence(id="RUNTIME-CROSS-ORG-LEARN-015", layer="runtime")
def test_context_learning_rejects_cross_organization_candidate_lineage() -> None:
    """A mismatched store result cannot become evaluation or publication state."""

    requested = _candidate(ORGANIZATION_A, suffix="org-a").reference()
    store = _CrossOrganizationStore(_candidate(ORGANIZATION_B, suffix="org-b"))
    learning = ContextLearning(
        store=store,
        evaluation_keyring=ReleaseEvaluationKeyring(
            active_version=1,
            keys={1: SIGNING_KEY},
        ),
        promotion_authority=ReleaseOperatorAuthority(
            _NeverAuthenticator(),  # type: ignore[arg-type]
            call_ttl=timedelta(minutes=1),
            clock=lambda: EVALUATED_AT,
        ),
        clock=lambda: EVALUATED_AT,
    )

    with pytest.raises(
        ReleaseEvaluationUnavailable,
        match="release candidate could not be loaded safely",
    ):
        learning.evaluate(requested)

    assert store.evaluations == []
