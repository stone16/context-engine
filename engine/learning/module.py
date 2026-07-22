"""Public in-process ContextLearning Module boundary."""

from __future__ import annotations

from datetime import datetime

from engine.learning.evaluation import (
    GateStatus,
    ReleaseCandidate,
    ReleaseCandidateRef,
    ReleaseClock,
    ReleaseEvaluation,
    ReleaseEvaluationKeyring,
    ReleaseEvaluationUnavailable,
    evaluate_candidate,
    verify_release_candidate,
    verify_release_evaluation,
)
from engine.learning.promotion import (
    PromotionCommit,
    PromotionReceipt,
    ReleaseOperatorAuthority,
    ReleasePromotionRejected,
    ReleasePromotionUnavailable,
    ReleaseStorePort,
    TrustedPromotionCall,
    _construct_promotion_receipt,
    _consume_trusted_promotion_call,
    _require_active_trusted_promotion_call,
)


class ContextLearning:
    """Evaluate release candidates and exclusively own active release promotion."""

    __slots__ = ("_clock", "_evaluation_keyring", "_promotion_authority", "_store")

    def __init__(
        self,
        *,
        store: ReleaseStorePort,
        evaluation_keyring: ReleaseEvaluationKeyring,
        promotion_authority: ReleaseOperatorAuthority,
        clock: ReleaseClock,
    ) -> None:
        for method_name in (
            "load_candidate",
            "persist_evaluation",
            "transaction",
        ):
            if not callable(getattr(store, method_name, None)):
                raise TypeError("ContextLearning release store is incomplete")
        if type(evaluation_keyring) is not ReleaseEvaluationKeyring:
            raise TypeError("ContextLearning requires ReleaseEvaluationKeyring")
        if type(promotion_authority) is not ReleaseOperatorAuthority:
            raise TypeError("ContextLearning requires ReleaseOperatorAuthority")
        if not callable(clock):
            raise TypeError("ContextLearning clock must be callable")
        self._store = store
        self._evaluation_keyring = evaluation_keyring
        self._promotion_authority = promotion_authority
        self._clock = clock

    def evaluate(self, candidate_ref: ReleaseCandidateRef) -> ReleaseEvaluation:
        """Persist one immutable signed evaluation without publication access."""

        if type(candidate_ref) is not ReleaseCandidateRef:
            raise TypeError("evaluate requires ReleaseCandidateRef")
        try:
            candidate = self._store.load_candidate(candidate_ref)
            if (
                type(candidate) is not ReleaseCandidate
                or candidate.reference() != candidate_ref
                or not verify_release_candidate(candidate)
            ):
                raise ReleaseEvaluationUnavailable(
                    "release candidate could not be loaded safely"
                )
            evaluated_at = self._clock()
            evaluation = evaluate_candidate(
                candidate,
                keyring=self._evaluation_keyring,
                evaluated_at=evaluated_at,
            )
            self._store.persist_evaluation(evaluation)
            return evaluation
        except ReleaseEvaluationUnavailable:
            raise
        except Exception:
            raise ReleaseEvaluationUnavailable(
                "release evaluation is unavailable"
            ) from None

    def promote(self, call: TrustedPromotionCall) -> PromotionReceipt:
        """Revalidate and atomically promote through the one retained transaction."""

        try:
            _require_active_trusted_promotion_call(
                call,
                authority=self._promotion_authority,
            )
            _consume_trusted_promotion_call(call)
            self._require_current_call_time(call, self._clock())
            self._require_promotable(call)
            with self._store.transaction(call.organization_id) as transaction:
                self._require_transaction(transaction)
                transaction.revalidate_promotion(
                    call,
                    evaluation_keyring=self._evaluation_keyring,
                )
                commit = transaction.promote_atomically(call)
                self._require_exact_commit(call, commit)
                transaction.commit()
            return _construct_promotion_receipt(commit)
        except ReleasePromotionRejected:
            raise
        except ReleasePromotionUnavailable:
            raise
        except Exception:
            raise ReleasePromotionUnavailable(
                "release promotion is unavailable"
            ) from None

    @staticmethod
    def _require_current_call_time(
        call: TrustedPromotionCall,
        checked_at: datetime,
    ) -> None:
        offset = checked_at.utcoffset() if checked_at.tzinfo is not None else None
        if (
            type(checked_at) is not datetime
            or checked_at.tzinfo is None
            or offset is None
            or offset.total_seconds() != 0
            or checked_at < call.issued_at
            or checked_at >= call.expires_at
        ):
            raise ReleasePromotionRejected

    def _require_promotable(self, call: TrustedPromotionCall) -> None:
        candidate = call.candidate
        evaluation = call.evaluation
        if (
            not verify_release_candidate(candidate)
            or not verify_release_evaluation(
                evaluation,
                candidate=candidate,
                keyring=self._evaluation_keyring,
            )
            or call.organization_id != candidate.organization_id
            or call.candidate_ref != candidate.candidate_ref
            or call.candidate_digest != candidate.candidate_digest
            or call.manifest_ref != candidate.manifest.manifest_ref
            or call.manifest_digest != candidate.manifest.manifest_digest
            or call.evaluation_ref != evaluation.evaluation_ref
            or call.evaluation_digest != evaluation.evaluation_digest
            or call.expected_active_generation
            != candidate.expected_active_generation
            or call.expected_active_generation
            != evaluation.expected_active_generation
            or call.expected_base_manifest_digest
            != candidate.expected_base_manifest_digest
            or call.expected_base_manifest_digest
            != evaluation.expected_base_manifest_digest
            or any(
                evidence.status is not GateStatus.PASS
                for evidence in evaluation.gate_evidence
            )
            or not evaluation.compatibility_passed
        ):
            raise ReleasePromotionRejected

    @staticmethod
    def _require_transaction(transaction: object) -> None:
        for method_name in (
            "revalidate_promotion",
            "promote_atomically",
            "commit",
        ):
            if not callable(getattr(transaction, method_name, None)):
                raise ReleasePromotionUnavailable(
                    "release promotion transaction is incomplete"
                )

    @staticmethod
    def _require_exact_commit(
        call: TrustedPromotionCall,
        commit: PromotionCommit,
    ) -> None:
        if (
            type(commit) is not PromotionCommit
            or commit.organization_id != call.organization_id
            or commit.promotion_ref != call.promotion_ref
            or commit.active_generation != call.expected_active_generation + 1
            or commit.manifest_ref != call.manifest_ref
            or commit.manifest_digest != call.manifest_digest
        ):
            raise ReleasePromotionUnavailable(
                "release promotion returned an invalid commit"
            )
