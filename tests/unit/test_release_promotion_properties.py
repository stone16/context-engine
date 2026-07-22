from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Literal, cast
from uuid import UUID

import pytest
from hypothesis import given, seed, settings
from hypothesis import strategies as st

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
    ReleaseCandidate,
    ReleaseCandidateRef,
    ReleaseEvaluation,
    ReleaseEvaluationKeyring,
    ReleaseManifest,
    ReleaseOperatorAuthenticationRejected,
    ReleaseOperatorAuthority,
    ReleasePromotionRejected,
    ReleasePromotionTransactionPort,
    ReleasePromotionUnavailable,
    ReleaseStorePort,
    RuntimeProfileRef,
    TrustedPromotionCall,
    VerifiedReleaseOperatorIdentity,
    release_authority_digest,
)

ORGANIZATION_ID = UUID("33c7b365-c705-45af-b676-067fd510f683")
EVALUATED_AT = datetime(2026, 7, 22, 15, 30, tzinfo=UTC)
SIGNING_KEY = b"evaluation-signing-domain-key-at-least-32-bytes"

type RejectionKind = Literal[
    "missing_authority",
    "wrong_authority",
    "security_gate",
    "reliability_gate",
    "quality_gate",
    "budget_gate",
    "incompatible_lineage",
    "digest_mismatch",
    "signature_tamper",
    "stale_state",
    "expired",
    "replay",
    "commit_failure",
]

REJECTION_KINDS: tuple[RejectionKind, ...] = (
    "missing_authority",
    "wrong_authority",
    "security_gate",
    "reliability_gate",
    "quality_gate",
    "budget_gate",
    "incompatible_lineage",
    "digest_mismatch",
    "signature_tamper",
    "stale_state",
    "expired",
    "replay",
    "commit_failure",
)


class _MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class _Authenticator:
    def __init__(self, *, issuer: str) -> None:
        self.operator_ref = f"release-operator-{issuer}"
        self.authentication_binding_ref = f"authentication-binding-{issuer}"
        self.authority_ref = f"release-authority-{issuer}"
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


def _manifest(*, suffix: str, curation_on: bool = False) -> ReleaseManifest:
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
    if curation_on:
        revision_refs = ("revision-curated-v1",)
        return ReleaseManifest(
            organization_id=ORGANIZATION_ID,
            manifest_ref=f"manifest-{suffix}",
            content_profile=content,
            index_profile=index,
            runtime_profile=runtime,
            curation_profile=CurationProfileRef.on(
                profile_ref=f"curation-on-{suffix}",
                profile_digest="4" * 64,
                curation_snapshot_ref="curation-snapshot-v1",
                compatible_revision_refs=revision_refs,
                evaluation_digest="5" * 64,
            ),
            active_revision_refs=revision_refs,
        )
    return ReleaseManifest.m0_empty(
        organization_id=ORGANIZATION_ID,
        manifest_ref=f"manifest-{suffix}",
        content_profile=content,
        index_profile=index,
        runtime_profile=runtime,
        curation_profile=CurationProfileRef.off(
            profile_ref=f"curation-off-{suffix}",
            profile_digest="4" * 64,
        ),
    )


def _candidate(
    *,
    suffix: str,
    manifest: ReleaseManifest | None = None,
    expected_generation: int = 0,
    expected_base: str | None = None,
    failed_gate: Gate | None = None,
) -> ReleaseCandidate:
    gate_evidence = tuple(
        GateEvidence(
            gate=gate,
            status=(GateStatus.FAIL if gate is failed_gate else GateStatus.PASS),
            evidence_digest=f"{index + 6:x}" * 64,
        )
        for index, gate in enumerate(Gate)
    )
    return ReleaseCandidate(
        organization_id=ORGANIZATION_ID,
        candidate_ref=f"candidate-{suffix}",
        manifest=manifest or _manifest(suffix=suffix),
        expected_active_generation=expected_generation,
        expected_base_manifest_digest=expected_base,
        gate_evidence=gate_evidence,
        capability_coverage_digest="a" * 64,
        fixture_digest="b" * 64,
        verification_commands=("uv run pytest -q tests/unit",),
    )


class _Transaction(ReleasePromotionTransactionPort):
    def __init__(self, store: _Store) -> None:
        self._store = store
        self._staged: PromotionCommit | None = None

    def revalidate_promotion(
        self,
        call: TrustedPromotionCall,
        *,
        evaluation_keyring: ReleaseEvaluationKeyring,
    ) -> None:
        assert type(evaluation_keyring) is ReleaseEvaluationKeyring
        if (
            call.authority_digest != self._store.authority_digest
            or self._store.evaluations.get(call.evaluation_ref)
            is not call.evaluation
        ):
            raise ReleasePromotionRejected

    def promote_atomically(self, call: TrustedPromotionCall) -> PromotionCommit:
        if (
            call.expected_active_generation != self._store.active_generation
            or call.expected_base_manifest_digest
            != self._store.active_manifest_digest
        ):
            raise ReleasePromotionRejected
        self._staged = PromotionCommit(
            organization_id=call.organization_id,
            promotion_ref=call.promotion_ref,
            active_generation=self._store.active_generation + 1,
            manifest_ref=call.manifest_ref,
            manifest_digest=call.manifest_digest,
            promoted_at=EVALUATED_AT,
        )
        return self._staged

    def commit(self) -> None:
        if self._store.fail_commit:
            raise RuntimeError("injected commit failure")
        if self._staged is None:
            raise RuntimeError("no staged promotion")
        self._store.active_generation = self._staged.active_generation
        self._store.active_manifest_digest = self._staged.manifest_digest
        self._store.success_audits.append(self._staged)


class _Store(ReleaseStorePort):
    def __init__(
        self,
        candidates: tuple[ReleaseCandidate, ...],
        *,
        authority_digest: str,
        active_generation: int = 0,
        active_manifest_digest: str | None = None,
        fail_commit: bool = False,
    ) -> None:
        self.candidates = {
            candidate.candidate_ref: candidate for candidate in candidates
        }
        self.evaluations: dict[str, ReleaseEvaluation] = {}
        self.authority_digest = authority_digest
        self.active_generation = active_generation
        self.active_manifest_digest = active_manifest_digest
        self.success_audits: list[PromotionCommit] = []
        self.fail_commit = fail_commit

    def state(self) -> tuple[int, str | None, tuple[PromotionCommit, ...]]:
        return (
            self.active_generation,
            self.active_manifest_digest,
            tuple(self.success_audits),
        )

    def load_candidate(self, candidate_ref: ReleaseCandidateRef) -> ReleaseCandidate:
        candidate = self.candidates[candidate_ref.candidate_ref]
        if candidate.reference() != candidate_ref:
            raise LookupError("candidate digest mismatch")
        return candidate

    def persist_evaluation(self, evaluation: ReleaseEvaluation) -> None:
        self.evaluations[evaluation.evaluation_ref] = evaluation

    @contextmanager
    def transaction(
        self,
        organization_id: UUID,
    ) -> Iterator[ReleasePromotionTransactionPort]:
        if organization_id != ORGANIZATION_ID:
            raise ReleasePromotionRejected
        yield _Transaction(self)


def _learning(
    candidates: tuple[ReleaseCandidate, ...],
    *,
    clock: _MutableClock,
    active_generation: int = 0,
    active_manifest_digest: str | None = None,
    fail_commit: bool = False,
) -> tuple[
    ContextLearning,
    ReleaseOperatorAuthority,
    _Store,
    _Authenticator,
]:
    authenticator = _Authenticator(issuer="expected")
    authority = ReleaseOperatorAuthority(
        authenticator,
        call_ttl=timedelta(minutes=5),
        clock=clock,
    )
    store = _Store(
        candidates,
        authority_digest=authenticator.authority_digest,
        active_generation=active_generation,
        active_manifest_digest=active_manifest_digest,
        fail_commit=fail_commit,
    )
    learning = ContextLearning(
        store=store,
        evaluation_keyring=ReleaseEvaluationKeyring(
            active_version=7,
            keys={7: SIGNING_KEY},
        ),
        promotion_authority=authority,
        clock=clock,
    )
    return learning, authority, store, authenticator


def _request(
    *,
    candidate: ReleaseCandidate,
    evaluation: ReleaseEvaluation,
    suffix: str,
    nonce: int,
) -> PromotionAuthorizationRequest:
    return PromotionAuthorizationRequest(
        organization_id=ORGANIZATION_ID,
        promotion_ref=f"promotion-{suffix}-{nonce}",
        candidate=candidate,
        evaluation=evaluation,
        request_id=f"request-{suffix}-{nonce}",
        audit_reason=f"approve {suffix} {nonce}",
        opaque_credential="release-credential-49",
    )


@pytest.mark.security_evidence(id="PROP-RELEASE-OWNER-019", layer="property")
@pytest.mark.parametrize("rejection_kind", REJECTION_KINDS)
@seed(49)
@settings(max_examples=3, deadline=None)
@given(nonce=st.integers(min_value=0, max_value=1_000_000))
def test_generated_rejected_promotions_leave_pointer_generation_and_success_audit_unchanged(  # noqa: E501
    rejection_kind: RejectionKind,
    nonce: int,
) -> None:
    failed_gate = {
        "security_gate": Gate.SECURITY,
        "reliability_gate": Gate.RELIABILITY,
        "quality_gate": Gate.QUALITY,
        "budget_gate": Gate.BUDGET,
    }.get(rejection_kind)
    candidate = _candidate(
        suffix=rejection_kind,
        failed_gate=failed_gate,
        manifest=(
            _manifest(suffix=rejection_kind, curation_on=True)
            if rejection_kind == "incompatible_lineage"
            else None
        ),
    )
    clock = _MutableClock(EVALUATED_AT)
    learning, authority, store, _ = _learning(
        (candidate,),
        clock=clock,
        active_generation=1 if rejection_kind == "stale_state" else 0,
        active_manifest_digest=(
            "f" * 64 if rejection_kind == "stale_state" else None
        ),
        fail_commit=rejection_kind == "commit_failure",
    )
    evaluation = learning.evaluate(candidate.reference())
    if rejection_kind == "digest_mismatch":
        object.__setattr__(candidate, "candidate_digest", "e" * 64)
    elif rejection_kind == "signature_tamper":
        object.__setattr__(
            evaluation,
            "signature",
            bytes([evaluation.signature[0] ^ 1]) + evaluation.signature[1:],
        )
    request = _request(
        candidate=candidate,
        evaluation=evaluation,
        suffix=rejection_kind,
        nonce=nonce,
    )

    if rejection_kind == "replay":
        with authority.authorize(request) as call:
            first_receipt = learning.promote(call)
            state_after_first_commit = store.state()
            with pytest.raises(ReleasePromotionRejected):
                learning.promote(call)
        assert first_receipt.active_generation == 1
        assert store.state() == state_after_first_commit
        return

    state_before_rejection = store.state()
    with pytest.raises(
        (
            ReleaseOperatorAuthenticationRejected,
            ReleasePromotionRejected,
            ReleasePromotionUnavailable,
        )
    ):
        if rejection_kind == "missing_authority":
            learning.promote(cast(TrustedPromotionCall, object()))
        else:
            call_authority = authority
            if rejection_kind == "wrong_authority":
                call_authority = ReleaseOperatorAuthority(
                    _Authenticator(issuer="wrong"),
                    call_ttl=timedelta(minutes=5),
                    clock=clock,
                )
            with call_authority.authorize(request) as call:
                if rejection_kind == "expired":
                    clock.value = EVALUATED_AT + timedelta(minutes=5)
                learning.promote(call)

    assert store.state() == state_before_rejection


def _promote_once(
    learning: ContextLearning,
    authority: ReleaseOperatorAuthority,
    *,
    candidate: ReleaseCandidate,
    evaluation: ReleaseEvaluation,
    suffix: str,
) -> None:
    request = _request(
        candidate=candidate,
        evaluation=evaluation,
        suffix=suffix,
        nonce=0,
    )
    with authority.authorize(request) as call:
        learning.promote(call)


def test_expected_activation_generation_absorbs_stale_replay_and_aba() -> None:
    manifest_a = _manifest(suffix="a")
    manifest_b = _manifest(suffix="b")
    initial_a = _candidate(suffix="initial-a", manifest=manifest_a)
    ordinary_b = _candidate(
        suffix="ordinary-b",
        manifest=manifest_b,
        expected_generation=1,
        expected_base=manifest_a.manifest_digest,
    )
    rollback_a = _candidate(
        suffix="rollback-a",
        manifest=manifest_a,
        expected_generation=2,
        expected_base=manifest_b.manifest_digest,
    )
    stale_after_aba = _candidate(
        suffix="stale-after-aba",
        manifest=manifest_b,
        expected_generation=1,
        expected_base=manifest_a.manifest_digest,
    )
    candidates = (initial_a, ordinary_b, rollback_a, stale_after_aba)
    clock = _MutableClock(EVALUATED_AT)
    learning, authority, store, _ = _learning(candidates, clock=clock)
    evaluations = {
        candidate.candidate_ref: learning.evaluate(candidate.reference())
        for candidate in candidates
    }

    initial_request = _request(
        candidate=initial_a,
        evaluation=evaluations[initial_a.candidate_ref],
        suffix="initial-a",
        nonce=0,
    )
    with authority.authorize(initial_request) as initial_call:
        learning.promote(initial_call)
        state_after_initial = store.state()
        with pytest.raises(ReleasePromotionRejected):
            learning.promote(initial_call)
        assert store.state() == state_after_initial

    _promote_once(
        learning,
        authority,
        candidate=ordinary_b,
        evaluation=evaluations[ordinary_b.candidate_ref],
        suffix="ordinary-b",
    )
    _promote_once(
        learning,
        authority,
        candidate=rollback_a,
        evaluation=evaluations[rollback_a.candidate_ref],
        suffix="rollback-a",
    )
    assert store.active_generation == 3
    assert store.active_manifest_digest == manifest_a.manifest_digest

    state_after_aba = store.state()
    stale_request = _request(
        candidate=stale_after_aba,
        evaluation=evaluations[stale_after_aba.candidate_ref],
        suffix="stale-after-aba",
        nonce=0,
    )
    with (
        authority.authorize(stale_request) as stale_call,
        pytest.raises(ReleasePromotionRejected),
    ):
        learning.promote(stale_call)

    assert store.state() == state_after_aba
