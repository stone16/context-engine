from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID

from engine.learning import (
    ContentProfileRef,
    ContextLearning,
    CurationProfileRef,
    Gate,
    GateEvidence,
    GateStatus,
    IndexProfileRef,
    PromotionAuthorizationRequest,
    PromotionCommit,
    PromotionReceipt,
    ReleaseCandidate,
    ReleaseCandidateRef,
    ReleaseEvaluation,
    ReleaseEvaluationKeyring,
    ReleaseManifest,
    ReleaseOperatorAuthority,
    ReleasePromotionTransactionPort,
    ReleaseStorePort,
    RuntimeProfileRef,
    TrustedPromotionCall,
    VerifiedReleaseOperatorIdentity,
    release_authority_digest,
)

ORGANIZATION_ID = UUID("33c7b365-c705-45af-b676-067fd510f683")
EVALUATED_AT = datetime(2026, 7, 22, 15, 30, tzinfo=UTC)
SIGNING_KEY = b"evaluation-signing-domain-key-at-least-32-bytes"


def _manifest(
    *,
    suffix: str = "m0-empty-v1",
    profile_digit: int = 1,
) -> ReleaseManifest:
    content = ContentProfileRef(
        profile_ref=f"content-{suffix}",
        profile_digest=str(profile_digit) * 64,
        content_schema_ref="context-content-schema-v1",
    )
    index = IndexProfileRef(
        profile_ref=f"index-{suffix}",
        profile_digest=str(profile_digit + 1) * 64,
        content_profile_digest=content.profile_digest,
        content_schema_ref=content.content_schema_ref,
        index_schema_ref="context-index-schema-v1",
    )
    runtime = RuntimeProfileRef(
        profile_ref=f"runtime-{suffix}",
        profile_digest=str(profile_digit + 2) * 64,
        content_profile_digest=content.profile_digest,
        index_profile_digest=index.profile_digest,
        content_schema_ref=content.content_schema_ref,
        index_schema_ref=index.index_schema_ref,
        tokenizer_ref="empty-tokenizer-v1",
        package_schema_ref="context-package-v1",
    )
    return ReleaseManifest.m0_empty(
        organization_id=ORGANIZATION_ID,
        manifest_ref=f"manifest-{suffix}",
        content_profile=content,
        index_profile=index,
        runtime_profile=runtime,
        curation_profile=CurationProfileRef.off(
            profile_ref=f"curation-off-{suffix}",
            profile_digest=str(profile_digit + 3) * 64,
        ),
    )


def _candidate(
    *,
    manifest: ReleaseManifest | None = None,
    suffix: str = "m0-empty-v1",
    generation: int = 0,
    base_digest: str | None = None,
) -> ReleaseCandidate:
    gates = tuple(
        GateEvidence(
            gate=gate,
            status=GateStatus.PASS,
            evidence_digest=str(index + 5) * 64,
        )
        for index, gate in enumerate(Gate)
    )
    return ReleaseCandidate(
        organization_id=ORGANIZATION_ID,
        candidate_ref=f"candidate-{suffix}",
        manifest=manifest or _manifest(),
        expected_active_generation=generation,
        expected_base_manifest_digest=base_digest,
        gate_evidence=gates,
        capability_coverage_digest="9" * 64,
        fixture_digest="a" * 64,
        verification_commands=("uv run pytest -q tests/unit",),
    )


class _EvaluationStore(ReleaseStorePort):
    def __init__(self, candidate: ReleaseCandidate) -> None:
        self.candidate = candidate
        self.evaluations: list[ReleaseEvaluation] = []
        self.pointer_writes = 0
        self.success_audits = 0

    def load_candidate(self, candidate_ref: ReleaseCandidateRef) -> ReleaseCandidate:
        assert candidate_ref.organization_id == self.candidate.organization_id
        assert candidate_ref.candidate_ref == self.candidate.candidate_ref
        assert candidate_ref.candidate_digest == self.candidate.candidate_digest
        return self.candidate

    def persist_evaluation(self, evaluation: ReleaseEvaluation) -> None:
        self.evaluations.append(evaluation)

    def transaction(
        self,
        organization_id: UUID,
    ) -> AbstractContextManager[ReleasePromotionTransactionPort]:
        raise AssertionError("evaluate opened a promotion transaction")


class _NeverAuthenticator:
    def authenticate(self, opaque_credential: str) -> object:
        raise AssertionError("evaluate authenticated a release operator")


class _ExactAuthenticator:
    def __init__(self) -> None:
        self.operator_ref = "release-operator-49"
        self.authentication_binding_ref = "authentication-binding-49"
        self.authority_ref = "release-authority-49"
        self.authority_digest = release_authority_digest(
            organization_id=ORGANIZATION_ID,
            operator_ref=self.operator_ref,
            authentication_binding_ref=self.authentication_binding_ref,
            authority_ref=self.authority_ref,
        )

    def authenticate(
        self,
        opaque_credential: str,
    ) -> VerifiedReleaseOperatorIdentity:
        assert opaque_credential == "release-credential-49"
        return VerifiedReleaseOperatorIdentity(
            organization_id=ORGANIZATION_ID,
            operator_ref=self.operator_ref,
            authentication_binding_ref=self.authentication_binding_ref,
            authority_ref=self.authority_ref,
            authority_digest=self.authority_digest,
            valid_from=EVALUATED_AT - timedelta(minutes=1),
            expires_at=EVALUATED_AT + timedelta(hours=1),
        )


class _PromotionTransaction(ReleasePromotionTransactionPort):
    def __init__(self, store: _PromotionStore) -> None:
        self._store = store
        self._staged: PromotionCommit | None = None

    def revalidate_promotion(
        self,
        call: TrustedPromotionCall,
        *,
        evaluation_keyring: ReleaseEvaluationKeyring,
    ) -> None:
        assert type(evaluation_keyring) is ReleaseEvaluationKeyring
        assert call.authority_digest == self._store.authority_digest
        assert self._store.evaluations[call.evaluation_ref] == call.evaluation

    def promote_atomically(self, call: TrustedPromotionCall) -> PromotionCommit:
        assert call.expected_active_generation == self._store.generation
        assert call.expected_base_manifest_digest == self._store.active_digest
        self._staged = PromotionCommit(
            organization_id=call.organization_id,
            promotion_ref=call.promotion_ref,
            active_generation=self._store.generation + 1,
            manifest_ref=call.manifest_ref,
            manifest_digest=call.manifest_digest,
            promoted_at=EVALUATED_AT,
        )
        return self._staged

    def commit(self) -> None:
        assert self._staged is not None
        self._store.generation = self._staged.active_generation
        self._store.active_digest = self._staged.manifest_digest
        self._store.success_audits.append(self._staged)


class _PromotionStore(ReleaseStorePort):
    def __init__(
        self,
        candidates: tuple[ReleaseCandidate, ...],
        *,
        authority_digest: str,
    ) -> None:
        self.candidates = {
            candidate.candidate_ref: candidate for candidate in candidates
        }
        self.evaluations: dict[str, ReleaseEvaluation] = {}
        self.authority_digest = authority_digest
        self.generation = 0
        self.active_digest: str | None = None
        self.success_audits: list[PromotionCommit] = []

    def load_candidate(self, candidate_ref: ReleaseCandidateRef) -> ReleaseCandidate:
        candidate = self.candidates[candidate_ref.candidate_ref]
        assert candidate.reference() == candidate_ref
        return candidate

    def persist_evaluation(self, evaluation: ReleaseEvaluation) -> None:
        self.evaluations[evaluation.evaluation_ref] = evaluation

    @contextmanager
    def transaction(
        self,
        organization_id: UUID,
    ) -> Iterator[ReleasePromotionTransactionPort]:
        assert organization_id == ORGANIZATION_ID
        yield _PromotionTransaction(self)


def _promote(
    learning: ContextLearning,
    authority: ReleaseOperatorAuthority,
    *,
    promotion_ref: str,
    candidate: ReleaseCandidate,
    evaluation: ReleaseEvaluation,
) -> PromotionReceipt:
    request = PromotionAuthorizationRequest(
        organization_id=ORGANIZATION_ID,
        promotion_ref=promotion_ref,
        candidate=candidate,
        evaluation=evaluation,
        request_id=f"request-{promotion_ref}",
        audit_reason=f"authorize {promotion_ref}",
        opaque_credential="release-credential-49",
    )
    with authority.authorize(request) as call:
        return learning.promote(call)


def test_evaluate_persists_only_immutable_evaluation_and_never_publishes() -> None:
    candidate = _candidate()
    store = _EvaluationStore(candidate)
    learning = ContextLearning(
        store=store,
        evaluation_keyring=ReleaseEvaluationKeyring(
            active_version=7,
            keys={7: SIGNING_KEY},
        ),
        promotion_authority=ReleaseOperatorAuthority(
            _NeverAuthenticator(),  # type: ignore[arg-type]
            call_ttl=timedelta(minutes=1),
            clock=lambda: EVALUATED_AT,
        ),
        clock=lambda: EVALUATED_AT,
    )

    evaluation = learning.evaluate(candidate.reference())

    assert store.evaluations == [evaluation]
    assert store.pointer_writes == 0
    assert store.success_audits == 0
    assert evaluation.candidate_digest == candidate.candidate_digest
    assert evaluation.manifest_digest == candidate.manifest.manifest_digest
    assert evaluation.expected_active_generation == 0
    assert evaluation.expected_base_manifest_digest is None
    assert evaluation.evaluated_at == EVALUATED_AT
    assert len(evaluation.signature) == 32


def test_initial_activation_ordinary_promotion_and_rollback_share_promote() -> None:
    initial_manifest = _manifest()
    ordinary_manifest = _manifest(suffix="ordinary-v2", profile_digit=2)
    initial = _candidate(manifest=initial_manifest)
    ordinary = _candidate(
        manifest=ordinary_manifest,
        suffix="ordinary-v2",
        generation=1,
        base_digest=initial_manifest.manifest_digest,
    )
    rollback = _candidate(
        manifest=initial_manifest,
        suffix="rollback-to-initial",
        generation=2,
        base_digest=ordinary_manifest.manifest_digest,
    )
    authenticator = _ExactAuthenticator()
    authority = ReleaseOperatorAuthority(
        authenticator,
        call_ttl=timedelta(minutes=5),
        clock=lambda: EVALUATED_AT,
    )
    store = _PromotionStore(
        (initial, ordinary, rollback),
        authority_digest=authenticator.authority_digest,
    )
    learning = ContextLearning(
        store=store,
        evaluation_keyring=ReleaseEvaluationKeyring(
            active_version=7,
            keys={7: SIGNING_KEY},
        ),
        promotion_authority=authority,
        clock=lambda: EVALUATED_AT,
    )
    evaluations = {
        candidate.candidate_ref: learning.evaluate(candidate.reference())
        for candidate in (initial, ordinary, rollback)
    }

    receipts = tuple(
        _promote(
            learning,
            authority,
            promotion_ref=promotion_ref,
            candidate=candidate,
            evaluation=evaluations[candidate.candidate_ref],
        )
        for promotion_ref, candidate in (
            ("promotion-initial", initial),
            ("promotion-ordinary", ordinary),
            ("promotion-rollback", rollback),
        )
    )

    assert tuple(receipt.active_generation for receipt in receipts) == (1, 2, 3)
    assert tuple(receipt.manifest_digest for receipt in receipts) == (
        initial_manifest.manifest_digest,
        ordinary_manifest.manifest_digest,
        initial_manifest.manifest_digest,
    )
    assert tuple(audit.promotion_ref for audit in store.success_audits) == tuple(
        receipt.promotion_ref for receipt in receipts
    )
