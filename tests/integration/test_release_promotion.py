from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Barrier
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from engine.learning import (
    ContentProfileRef,
    ContextLearning,
    CurationProfileRef,
    Gate,
    GateEvidence,
    GateStatus,
    IndexProfileRef,
    PromotionAuthorizationRequest,
    PromotionReceipt,
    ReleaseCandidate,
    ReleaseEvaluation,
    ReleaseEvaluationKeyring,
    ReleaseManifest,
    ReleaseOperatorAuthenticationRejected,
    ReleaseOperatorAuthority,
    ReleasePromotionRejected,
    ReleasePromotionUnavailable,
    RuntimeProfileRef,
    TrustedPromotionCall,
    VerifiedReleaseOperatorIdentity,
    release_authority_digest,
)
from engine.persistence import DatabaseConfiguration, create_database_engine
from engine.persistence.releases import PostgreSQLReleaseStore

pytestmark = pytest.mark.integration

_SIGNING_KEY = b"issue-49-release-evaluation-signing-key-v1"
_SIGNING_KEY_VERSION = 49
_PROMOTE_REGPROCEDURE = (
    "public.context_learning_promote_release(uuid,text,text,text,text,text,text,"
    "text,text,text,text,text,bigint,bytea,bigint,text,timestamptz,timestamptz,"
    "text,text,text)"
)
_IMMUTABLE_RELEASE_TABLES = (
    "release_promotion_audit",
    "release_evaluation",
    "release_candidate",
    "release_manifest",
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class _MutableClock:
    now: datetime

    def __call__(self) -> datetime:
        return self.now


@dataclass(frozen=True, slots=True)
class _ReleaseState:
    pointer: dict[str, Any] | None
    audits: tuple[dict[str, Any], ...]
    manifests: tuple[dict[str, Any], ...]


class _ReleaseDatabaseFixture:
    """Migrator-only fixture setup/cleanup and read-only durable-state oracle."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._organization_ids: list[UUID] = []

    def create_organization(self) -> UUID:
        organization_id = uuid4()
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO organization (organization_id) "
                    "VALUES (:organization_id)"
                ),
                {"organization_id": organization_id},
            )
        self._organization_ids.append(organization_id)
        return organization_id

    def insert_grant(
        self,
        identity: VerifiedReleaseOperatorIdentity,
        *,
        organization_id: UUID | None = None,
        operator_ref: str | None = None,
        authority_digest: str | None = None,
        valid_from: datetime | None = None,
        expires_at: datetime | None = None,
        revoked_at: datetime | None = None,
    ) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO release_operator_grant (
                        organization_id, authority_ref, authority_digest,
                        operator_ref, authentication_binding_ref,
                        valid_from, expires_at, revoked_at
                    ) VALUES (
                        :organization_id, :authority_ref, :authority_digest,
                        :operator_ref, :authentication_binding_ref,
                        :valid_from, :expires_at, :revoked_at
                    )
                    """
                ),
                {
                    "organization_id": organization_id or identity.organization_id,
                    "authority_ref": identity.authority_ref,
                    "authority_digest": authority_digest or identity.authority_digest,
                    "operator_ref": operator_ref or identity.operator_ref,
                    "authentication_binding_ref": (
                        identity.authentication_binding_ref
                    ),
                    "valid_from": valid_from or identity.valid_from,
                    "expires_at": expires_at or identity.expires_at,
                    "revoked_at": revoked_at,
                },
            )

    def revoke_grant(
        self,
        identity: VerifiedReleaseOperatorIdentity,
        *,
        revoked_at: datetime,
    ) -> None:
        self._update_grant(
            identity,
            "revoked_at = :changed_value",
            revoked_at,
        )

    def expire_grant(
        self,
        identity: VerifiedReleaseOperatorIdentity,
        *,
        expires_at: datetime,
    ) -> None:
        self._update_grant(
            identity,
            "expires_at = :changed_value",
            expires_at,
        )

    def replace_grant_digest(
        self,
        identity: VerifiedReleaseOperatorIdentity,
        *,
        authority_digest: str,
    ) -> None:
        self._update_grant(
            identity,
            "authority_digest = :changed_value",
            authority_digest,
        )

    def replace_grant_operator(
        self,
        identity: VerifiedReleaseOperatorIdentity,
        *,
        operator_ref: str,
    ) -> None:
        self._update_grant(
            identity,
            "operator_ref = :changed_value",
            operator_ref,
        )

    def move_grant_to_organization(
        self,
        identity: VerifiedReleaseOperatorIdentity,
        *,
        other_organization_id: UUID,
    ) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    """
                    DELETE FROM release_operator_grant
                    WHERE organization_id = :organization_id
                      AND authority_ref = :authority_ref
                    """
                ),
                {
                    "organization_id": identity.organization_id,
                    "authority_ref": identity.authority_ref,
                },
            )
        self.insert_grant(identity, organization_id=other_organization_id)

    def _update_grant(
        self,
        identity: VerifiedReleaseOperatorIdentity,
        assignment: str,
        changed_value: object,
    ) -> None:
        # ``assignment`` is selected only by the hard-coded helpers above.
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE release_operator_grant SET "
                    + assignment
                    + " WHERE organization_id = :organization_id "
                    "AND authority_ref = :authority_ref"
                ),
                {
                    "changed_value": changed_value,
                    "organization_id": identity.organization_id,
                    "authority_ref": identity.authority_ref,
                },
            )

    def tamper_candidate_gate(self, candidate: ReleaseCandidate) -> None:
        self._tamper_immutable_row(
            table_name="release_candidate",
            trigger_name="release_candidate_reject_mutation",
            statement=(
                "UPDATE release_candidate SET security_status = 'fail' "
                "WHERE organization_id = :organization_id "
                "AND candidate_ref = :row_ref"
            ),
            organization_id=candidate.organization_id,
            row_ref=candidate.candidate_ref,
        )

    def tamper_evaluation_compatibility(
        self,
        evaluation: ReleaseEvaluation,
    ) -> None:
        self._tamper_immutable_row(
            table_name="release_evaluation",
            trigger_name="release_evaluation_reject_mutation",
            statement=(
                "UPDATE release_evaluation SET compatibility_passed = false "
                "WHERE organization_id = :organization_id "
                "AND evaluation_ref = :row_ref"
            ),
            organization_id=evaluation.organization_id,
            row_ref=evaluation.evaluation_ref,
        )

    def tamper_evaluation_compatibility_digest(
        self,
        evaluation: ReleaseEvaluation,
    ) -> None:
        self._tamper_immutable_row(
            table_name="release_evaluation",
            trigger_name="release_evaluation_reject_mutation",
            statement=(
                "UPDATE release_evaluation "
                "SET compatibility_evidence_digest = :digest "
                "WHERE organization_id = :organization_id "
                "AND evaluation_ref = :row_ref"
            ),
            organization_id=evaluation.organization_id,
            row_ref=evaluation.evaluation_ref,
            extra={"digest": _digest("tampered compatibility evidence")},
        )

    def tamper_evaluation_signature(self, evaluation: ReleaseEvaluation) -> None:
        self._tamper_immutable_row(
            table_name="release_evaluation",
            trigger_name="release_evaluation_reject_mutation",
            statement=(
                "UPDATE release_evaluation SET signature = :signature "
                "WHERE organization_id = :organization_id "
                "AND evaluation_ref = :row_ref"
            ),
            organization_id=evaluation.organization_id,
            row_ref=evaluation.evaluation_ref,
            extra={"signature": b"x" * 32},
        )

    def tamper_evaluation_digest(self, evaluation: ReleaseEvaluation) -> None:
        self._tamper_immutable_row(
            table_name="release_evaluation",
            trigger_name="release_evaluation_reject_mutation",
            statement=(
                "UPDATE release_evaluation SET evaluation_digest = :digest "
                "WHERE organization_id = :organization_id "
                "AND evaluation_ref = :row_ref"
            ),
            organization_id=evaluation.organization_id,
            row_ref=evaluation.evaluation_ref,
            extra={"digest": _digest("tampered durable evaluation")},
        )

    def _tamper_immutable_row(
        self,
        *,
        table_name: str,
        trigger_name: str,
        statement: str,
        organization_id: UUID,
        row_ref: str,
        extra: dict[str, object] | None = None,
    ) -> None:
        # Deliberately corrupt a durable row as fixture setup, then restore the
        # production immutability trigger before exercising the Learning login.
        with self._engine.begin() as connection:
            connection.execute(
                text(f"ALTER TABLE {table_name} DISABLE TRIGGER {trigger_name}")
            )
            connection.execute(
                text(statement),
                {
                    "organization_id": organization_id,
                    "row_ref": row_ref,
                    **(extra or {}),
                },
            )
            connection.execute(
                text(f"ALTER TABLE {table_name} ENABLE TRIGGER {trigger_name}")
            )

    def state(self, organization_id: UUID) -> _ReleaseState:
        with self._engine.connect() as connection:
            pointer_row = (
                connection.execute(
                    text(
                        """
                        SELECT active_generation, manifest_ref, manifest_digest,
                               promotion_ref, activated_at
                        FROM active_release_manifest
                        WHERE organization_id = :organization_id
                        """
                    ),
                    {"organization_id": organization_id},
                )
                .mappings()
                .one_or_none()
            )
            audit_rows = connection.execute(
                text(
                    """
                    SELECT active_generation, promotion_ref, candidate_ref,
                           manifest_ref, manifest_digest, evaluation_ref,
                           expected_active_generation,
                           expected_base_manifest_digest, promoted_at
                    FROM release_promotion_audit
                    WHERE organization_id = :organization_id
                    ORDER BY active_generation
                    """
                ),
                {"organization_id": organization_id},
            ).mappings()
            manifest_rows = connection.execute(
                text(
                    """
                    SELECT manifest_ref, manifest_digest, lineage_digest,
                           curation_mode, active_revision_refs
                    FROM release_manifest
                    WHERE organization_id = :organization_id
                    ORDER BY manifest_ref
                    """
                ),
                {"organization_id": organization_id},
            ).mappings()
            return _ReleaseState(
                pointer=dict(pointer_row) if pointer_row is not None else None,
                audits=tuple(dict(row) for row in audit_rows),
                manifests=tuple(dict(row) for row in manifest_rows),
            )

    def cleanup(self) -> None:
        if not self._organization_ids:
            return
        with self._engine.begin() as connection:
            for table_name in _IMMUTABLE_RELEASE_TABLES:
                connection.execute(
                    text(
                        f"ALTER TABLE {table_name} DISABLE TRIGGER "
                        f"{table_name}_reject_mutation"
                    )
                )
            for organization_id in reversed(self._organization_ids):
                parameters = {"organization_id": organization_id}
                for table_name in (
                    "release_promotion_audit",
                    "active_release_manifest",
                    "release_evaluation",
                    "release_candidate",
                    "release_manifest",
                    "release_operator_grant",
                ):
                    connection.execute(
                        text(
                            f"DELETE FROM {table_name} "
                            "WHERE organization_id = :organization_id"
                        ),
                        parameters,
                    )
                connection.execute(
                    text(
                        "DELETE FROM organization "
                        "WHERE organization_id = :organization_id"
                    ),
                    parameters,
                )
            for table_name in reversed(_IMMUTABLE_RELEASE_TABLES):
                connection.execute(
                    text(
                        f"ALTER TABLE {table_name} ENABLE TRIGGER "
                        f"{table_name}_reject_mutation"
                    )
                )


@pytest.fixture
def release_database(
    migration_configuration: DatabaseConfiguration,
) -> Iterator[_ReleaseDatabaseFixture]:
    engine = create_database_engine(migration_configuration)
    fixture = _ReleaseDatabaseFixture(engine)
    try:
        yield fixture
    finally:
        fixture.cleanup()
        engine.dispose()


class _ExactAuthenticator:
    def __init__(
        self,
        *,
        credential: str,
        identity: VerifiedReleaseOperatorIdentity,
    ) -> None:
        self._credential = credential
        self._identity = identity

    def authenticate(self, opaque_credential: str) -> VerifiedReleaseOperatorIdentity:
        if opaque_credential != self._credential:
            raise ReleaseOperatorAuthenticationRejected
        return self._identity


@dataclass(frozen=True, slots=True)
class _LearningScenario:
    organization_id: UUID
    clock: _MutableClock
    credential: str
    identity: VerifiedReleaseOperatorIdentity
    keyring: ReleaseEvaluationKeyring
    authority: ReleaseOperatorAuthority
    store: PostgreSQLReleaseStore
    learning: ContextLearning


def _scenario(
    engine: Engine,
    organization_id: UUID,
    *,
    suffix: str,
) -> _LearningScenario:
    clock = _MutableClock(datetime.now(UTC))
    operator_ref = f"operator-{suffix}"
    authentication_binding_ref = f"authentication-{suffix}"
    authority_ref = f"authority-{suffix}"
    identity = VerifiedReleaseOperatorIdentity(
        organization_id=organization_id,
        operator_ref=operator_ref,
        authentication_binding_ref=authentication_binding_ref,
        authority_ref=authority_ref,
        authority_digest=release_authority_digest(
            organization_id=organization_id,
            operator_ref=operator_ref,
            authentication_binding_ref=authentication_binding_ref,
            authority_ref=authority_ref,
        ),
        valid_from=clock.now - timedelta(hours=2),
        expires_at=clock.now + timedelta(hours=2),
    )
    credential = f"credential-{suffix}"
    keyring = ReleaseEvaluationKeyring(
        active_version=_SIGNING_KEY_VERSION,
        keys={_SIGNING_KEY_VERSION: _SIGNING_KEY},
    )
    authority = ReleaseOperatorAuthority(
        _ExactAuthenticator(credential=credential, identity=identity),
        call_ttl=timedelta(minutes=5),
        clock=clock,
    )
    store = PostgreSQLReleaseStore(engine)
    learning = ContextLearning(
        store=store,
        evaluation_keyring=keyring,
        promotion_authority=authority,
        clock=clock,
    )
    return _LearningScenario(
        organization_id=organization_id,
        clock=clock,
        credential=credential,
        identity=identity,
        keyring=keyring,
        authority=authority,
        store=store,
        learning=learning,
    )


def _manifest(
    organization_id: UUID,
    *,
    suffix: str,
) -> ReleaseManifest:
    content = ContentProfileRef(
        profile_ref=f"content-{suffix}",
        profile_digest=_digest(f"content-profile-{suffix}"),
        content_schema_ref="context-content-schema-v1",
    )
    index = IndexProfileRef(
        profile_ref=f"index-{suffix}",
        profile_digest=_digest(f"index-profile-{suffix}"),
        content_profile_digest=content.profile_digest,
        content_schema_ref=content.content_schema_ref,
        index_schema_ref="context-index-schema-v1",
    )
    runtime = RuntimeProfileRef(
        profile_ref=f"runtime-{suffix}",
        profile_digest=_digest(f"runtime-profile-{suffix}"),
        content_profile_digest=content.profile_digest,
        index_profile_digest=index.profile_digest,
        content_schema_ref=content.content_schema_ref,
        index_schema_ref=index.index_schema_ref,
        tokenizer_ref="empty-tokenizer-v1",
        package_schema_ref="context-package-v1",
    )
    return ReleaseManifest.m0_empty(
        organization_id=organization_id,
        manifest_ref=f"manifest-{suffix}",
        content_profile=content,
        index_profile=index,
        runtime_profile=runtime,
        curation_profile=CurationProfileRef.off(
            profile_ref=f"curation-off-{suffix}",
            profile_digest=_digest(f"curation-profile-{suffix}"),
        ),
    )


def _candidate(
    organization_id: UUID,
    *,
    suffix: str,
    generation: int = 0,
    base_manifest_digest: str | None = None,
    manifest: ReleaseManifest | None = None,
    failed_gate: Gate | None = None,
) -> ReleaseCandidate:
    selected_manifest = manifest or _manifest(organization_id, suffix=suffix)
    return ReleaseCandidate(
        organization_id=organization_id,
        candidate_ref=f"candidate-{suffix}",
        manifest=selected_manifest,
        expected_active_generation=generation,
        expected_base_manifest_digest=base_manifest_digest,
        gate_evidence=tuple(
            GateEvidence(
                gate=gate,
                status=(
                    GateStatus.FAIL if gate is failed_gate else GateStatus.PASS
                ),
                evidence_digest=_digest(f"{gate.value}-gate-{suffix}"),
            )
            for gate in Gate
        ),
        capability_coverage_digest=_digest(f"capability-coverage-{suffix}"),
        fixture_digest=_digest(f"fixture-{suffix}"),
        verification_commands=("make check",),
    )


def _persist_and_evaluate(
    scenario: _LearningScenario,
    candidate: ReleaseCandidate,
) -> ReleaseEvaluation:
    scenario.store.persist_candidate(candidate)
    return scenario.learning.evaluate(candidate.reference())


def _request(
    scenario: _LearningScenario,
    *,
    promotion_ref: str,
    candidate: ReleaseCandidate,
    evaluation: ReleaseEvaluation,
) -> PromotionAuthorizationRequest:
    return PromotionAuthorizationRequest(
        organization_id=scenario.organization_id,
        promotion_ref=promotion_ref,
        candidate=candidate,
        evaluation=evaluation,
        request_id=f"request-{promotion_ref}",
        audit_reason=f"authorize {promotion_ref}",
        opaque_credential=scenario.credential,
    )


def _promote(
    scenario: _LearningScenario,
    *,
    promotion_ref: str,
    candidate: ReleaseCandidate,
    evaluation: ReleaseEvaluation,
) -> PromotionReceipt:
    authorization = _request(
        scenario,
        promotion_ref=promotion_ref,
        candidate=candidate,
        evaluation=evaluation,
    )
    with scenario.authority.authorize(authorization) as call:
        return scenario.learning.promote(call)


def _assert_empty_publication_state(
    release_database: _ReleaseDatabaseFixture,
    organization_id: UUID,
) -> None:
    state = release_database.state(organization_id)
    assert state.pointer is None
    assert state.audits == ()


def test_fresh_database_promotes_initial_empty_curation_off_manifest_through_context_learning(  # noqa: E501
    release_database: _ReleaseDatabaseFixture,
    guarded_learning_engine: Engine,
) -> None:
    organization_id = release_database.create_organization()
    scenario = _scenario(
        guarded_learning_engine,
        organization_id,
        suffix=uuid4().hex,
    )
    release_database.insert_grant(scenario.identity)
    candidate = _candidate(
        organization_id,
        suffix=f"initial-{uuid4().hex}",
    )

    _assert_empty_publication_state(release_database, organization_id)
    evaluation = _persist_and_evaluate(scenario, candidate)
    _assert_empty_publication_state(release_database, organization_id)

    receipt = _promote(
        scenario,
        promotion_ref=f"promotion-initial-{uuid4().hex}",
        candidate=candidate,
        evaluation=evaluation,
    )

    state = release_database.state(organization_id)
    assert receipt.active_generation == 1
    assert receipt.manifest_ref == candidate.manifest.manifest_ref
    assert state.pointer is not None
    assert state.pointer["active_generation"] == 1
    assert state.pointer["manifest_digest"] == candidate.manifest.manifest_digest
    assert len(state.audits) == 1
    assert state.audits[0]["promotion_ref"] == receipt.promotion_ref
    assert state.audits[0]["expected_active_generation"] == 0
    assert state.audits[0]["expected_base_manifest_digest"] is None
    assert state.manifests == (
        {
            "manifest_ref": candidate.manifest.manifest_ref,
            "manifest_digest": candidate.manifest.manifest_digest,
            "lineage_digest": candidate.manifest.lineage_digest,
            "curation_mode": "curation_off",
            "active_revision_refs": [],
        },
    )


def test_concurrent_first_or_later_promotions_commit_exactly_one_pointer_and_audit(
    release_database: _ReleaseDatabaseFixture,
    guarded_learning_engine: Engine,
) -> None:
    organization_id = release_database.create_organization()
    scenario = _scenario(
        guarded_learning_engine,
        organization_id,
        suffix=uuid4().hex,
    )
    release_database.insert_grant(scenario.identity)
    candidates = tuple(
        _candidate(
            organization_id,
            suffix=f"concurrent-{index}-{uuid4().hex}",
        )
        for index in range(2)
    )
    evaluations = tuple(
        _persist_and_evaluate(scenario, candidate) for candidate in candidates
    )
    promotion_refs = tuple(
        f"promotion-concurrent-{index}-{uuid4().hex}" for index in range(2)
    )
    requests = tuple(
        _request(
            scenario,
            promotion_ref=promotion_ref,
            candidate=candidate,
            evaluation=evaluation,
        )
        for promotion_ref, candidate, evaluation in zip(
            promotion_refs,
            candidates,
            evaluations,
            strict=True,
        )
    )
    barrier = Barrier(2)

    def attempt(call: TrustedPromotionCall) -> PromotionReceipt | Exception:
        barrier.wait(timeout=10)
        try:
            return scenario.learning.promote(call)
        except (ReleasePromotionRejected, ReleasePromotionUnavailable) as error:
            return error

    with ExitStack() as stack:
        calls = tuple(
            stack.enter_context(scenario.authority.authorize(request))
            for request in requests
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(executor.map(attempt, calls))

    receipts = tuple(
        outcome for outcome in outcomes if isinstance(outcome, PromotionReceipt)
    )
    failures = tuple(
        outcome for outcome in outcomes if isinstance(outcome, Exception)
    )
    assert len(receipts) == 1
    assert len(failures) == 1
    assert isinstance(
        failures[0],
        ReleasePromotionRejected | ReleasePromotionUnavailable,
    )

    state = release_database.state(organization_id)
    assert state.pointer is not None
    assert state.pointer["active_generation"] == 1
    assert state.pointer["promotion_ref"] == receipts[0].promotion_ref
    assert len(state.audits) == 1
    assert state.audits[0]["promotion_ref"] == receipts[0].promotion_ref
    assert state.audits[0]["manifest_digest"] == receipts[0].manifest_digest


class _InjectedCommitFailure(RuntimeError):
    pass


def test_failure_after_promotion_cas_before_commit_rolls_back_pointer_and_audit(
    release_database: _ReleaseDatabaseFixture,
    guarded_learning_engine: Engine,
) -> None:
    organization_id = release_database.create_organization()
    scenario = _scenario(
        guarded_learning_engine,
        organization_id,
        suffix=uuid4().hex,
    )
    release_database.insert_grant(scenario.identity)
    candidate = _candidate(
        organization_id,
        suffix=f"rollback-before-commit-{uuid4().hex}",
    )
    evaluation = _persist_and_evaluate(scenario, candidate)
    authorization = _request(
        scenario,
        promotion_ref=f"promotion-before-commit-{uuid4().hex}",
        candidate=candidate,
        evaluation=evaluation,
    )

    with (
        pytest.raises(_InjectedCommitFailure),
        scenario.authority.authorize(authorization) as call,
        scenario.store.transaction(organization_id) as transaction,
    ):
        transaction.revalidate_promotion(
            call,
            evaluation_keyring=scenario.keyring,
        )
        commit = transaction.promote_atomically(call)
        assert commit.active_generation == 1
        raise _InjectedCommitFailure("fail after CAS and audit staging")

    _assert_empty_publication_state(release_database, organization_id)


def _assert_insufficient_privilege(
    engine: Engine,
    statement: str,
    parameters: dict[str, object],
) -> None:
    with pytest.raises(DBAPIError) as caught, engine.begin() as connection:
        connection.execute(text(statement), parameters)
    assert getattr(caught.value.orig, "sqlstate", None) == "42501"


def test_application_roles_cannot_directly_mutate_release_pointer_or_audit(
    release_database: _ReleaseDatabaseFixture,
    guarded_runtime_engine: Engine,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    guarded_operator_engine: Engine,
    guarded_learning_engine: Engine,
) -> None:
    organization_id = release_database.create_organization()
    scenario = _scenario(
        guarded_learning_engine,
        organization_id,
        suffix=uuid4().hex,
    )
    candidate = _candidate(
        organization_id,
        suffix=f"direct-dml-{uuid4().hex}",
    )
    scenario.store.persist_candidate(candidate)
    application_engines = (
        guarded_runtime_engine,
        guarded_control_engine,
        guarded_worker_engine,
        guarded_operator_engine,
        guarded_learning_engine,
    )

    for engine in application_engines:
        with engine.connect() as connection:
            privileges = {
                (table_name, privilege): bool(
                    connection.execute(
                        text(
                            "SELECT has_table_privilege("
                            "current_user, :table_name, :privilege)"
                        ),
                        {
                            "table_name": table_name,
                            "privilege": privilege,
                        },
                    ).scalar_one()
                )
                for table_name in (
                    "public.active_release_manifest",
                    "public.release_promotion_audit",
                )
                for privilege in ("INSERT", "UPDATE", "DELETE")
            }
            may_execute_promote = bool(
                connection.execute(
                    text(
                        "SELECT has_function_privilege("
                        "current_user, :procedure, 'EXECUTE')"
                    ),
                    {"procedure": _PROMOTE_REGPROCEDURE},
                ).scalar_one()
            )
            current_user = str(
                connection.execute(text("SELECT current_user")).scalar_one()
            )
        assert not any(privileges.values())
        assert may_execute_promote is (current_user == "context_engine_learning")

        _assert_insufficient_privilege(
            engine,
            """
            INSERT INTO active_release_manifest (
                organization_id, active_generation, manifest_ref,
                manifest_digest, promotion_ref, activated_at
            ) VALUES (
                :organization_id, 1, :manifest_ref,
                :manifest_digest, :promotion_ref, statement_timestamp()
            )
            """,
            {
                "organization_id": organization_id,
                "manifest_ref": candidate.manifest.manifest_ref,
                "manifest_digest": candidate.manifest.manifest_digest,
                "promotion_ref": f"direct-pointer-{uuid4().hex}",
            },
        )
        _assert_insufficient_privilege(
            engine,
            "INSERT INTO release_promotion_audit (organization_id) "
            "VALUES (:organization_id)",
            {"organization_id": organization_id},
        )

    with guarded_learning_engine.connect() as connection:
        grant_privileges = tuple(
            bool(
                connection.execute(
                    text(
                        "SELECT has_table_privilege("
                        "current_user, 'public.release_operator_grant', :privilege)"
                    ),
                    {"privilege": privilege},
                ).scalar_one()
            )
            for privilege in ("INSERT", "UPDATE", "DELETE")
        )
    assert grant_privileges == (False, False, False)
    _assert_insufficient_privilege(
        guarded_learning_engine,
        "INSERT INTO release_operator_grant (organization_id) "
        "VALUES (:organization_id)",
        {"organization_id": organization_id},
    )
    assert not hasattr(scenario.learning, "grant_release_operator")
    assert not hasattr(scenario.store, "grant_release_operator")
    _assert_empty_publication_state(release_database, organization_id)


def test_promote_rechecks_current_operator_authority_digests_and_compatibility(
    release_database: _ReleaseDatabaseFixture,
    guarded_learning_engine: Engine,
) -> None:
    def assert_durable_rejection(
        *,
        label: str,
        mutate: Callable[
            [_LearningScenario, ReleaseCandidate, ReleaseEvaluation],
            None,
        ],
    ) -> None:
        organization_id = release_database.create_organization()
        scenario = _scenario(
            guarded_learning_engine,
            organization_id,
            suffix=f"{label}-{uuid4().hex}",
        )
        release_database.insert_grant(scenario.identity)
        candidate = _candidate(
            organization_id,
            suffix=f"{label}-{uuid4().hex}",
        )
        evaluation = _persist_and_evaluate(scenario, candidate)
        authorization = _request(
            scenario,
            promotion_ref=f"promotion-{label}-{uuid4().hex}",
            candidate=candidate,
            evaluation=evaluation,
        )
        with scenario.authority.authorize(authorization) as call:
            mutate(scenario, candidate, evaluation)
            with pytest.raises(ReleasePromotionRejected):
                scenario.learning.promote(call)
        _assert_empty_publication_state(release_database, organization_id)

    assert_durable_rejection(
        label="revoked-authority",
        mutate=lambda scenario, _candidate_value, _evaluation: (
            release_database.revoke_grant(
                scenario.identity,
                revoked_at=datetime.now(UTC),
            )
        ),
    )
    assert_durable_rejection(
        label="expired-authority",
        mutate=lambda scenario, _candidate_value, _evaluation: (
            release_database.expire_grant(
                scenario.identity,
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        ),
    )
    assert_durable_rejection(
        label="wrong-authority-digest",
        mutate=lambda scenario, _candidate_value, _evaluation: (
            release_database.replace_grant_digest(
                scenario.identity,
                authority_digest=_digest("wrong current authority digest"),
            )
        ),
    )
    assert_durable_rejection(
        label="wrong-operator",
        mutate=lambda scenario, _candidate_value, _evaluation: (
            release_database.replace_grant_operator(
                scenario.identity,
                operator_ref=f"other-operator-{uuid4().hex}",
            )
        ),
    )

    cross_scope_organization = release_database.create_organization()
    assert_durable_rejection(
        label="cross-organization-authority",
        mutate=lambda scenario, _candidate_value, _evaluation: (
            release_database.move_grant_to_organization(
                scenario.identity,
                other_organization_id=cross_scope_organization,
            )
        ),
    )
    assert_durable_rejection(
        label="durable-signature-tamper",
        mutate=lambda _scenario_value, _candidate_value, evaluation: (
            release_database.tamper_evaluation_signature(evaluation)
        ),
    )
    assert_durable_rejection(
        label="durable-digest-tamper",
        mutate=lambda _scenario_value, _candidate_value, evaluation: (
            release_database.tamper_evaluation_digest(evaluation)
        ),
    )
    assert_durable_rejection(
        label="durable-gate-veto",
        mutate=lambda _scenario_value, candidate, _evaluation: (
            release_database.tamper_candidate_gate(candidate)
        ),
    )
    assert_durable_rejection(
        label="durable-compatibility-veto",
        mutate=lambda _scenario_value, _candidate_value, evaluation: (
            release_database.tamper_evaluation_compatibility(evaluation)
        ),
    )
    assert_durable_rejection(
        label="durable-compatibility-digest-tamper",
        mutate=lambda _scenario_value, _candidate_value, evaluation: (
            release_database.tamper_evaluation_compatibility_digest(evaluation)
        ),
    )

    organization_id = release_database.create_organization()
    expired_call_scenario = _scenario(
        guarded_learning_engine,
        organization_id,
        suffix=f"expired-call-{uuid4().hex}",
    )
    release_database.insert_grant(expired_call_scenario.identity)
    candidate = _candidate(
        organization_id,
        suffix=f"expired-call-{uuid4().hex}",
    )
    evaluation = _persist_and_evaluate(expired_call_scenario, candidate)
    authorization = _request(
        expired_call_scenario,
        promotion_ref=f"promotion-expired-call-{uuid4().hex}",
        candidate=candidate,
        evaluation=evaluation,
    )
    with expired_call_scenario.authority.authorize(authorization) as call:
        expired_call_scenario.clock.now += timedelta(minutes=6)
        with pytest.raises(ReleasePromotionRejected):
            expired_call_scenario.learning.promote(call)
    _assert_empty_publication_state(release_database, organization_id)

    for failed_gate in Gate:
        organization_id = release_database.create_organization()
        scenario = _scenario(
            guarded_learning_engine,
            organization_id,
            suffix=f"{failed_gate.value}-veto-{uuid4().hex}",
        )
        release_database.insert_grant(scenario.identity)
        failed_candidate = _candidate(
            organization_id,
            suffix=f"{failed_gate.value}-veto-{uuid4().hex}",
            failed_gate=failed_gate,
        )
        failed_evaluation = _persist_and_evaluate(scenario, failed_candidate)
        authorization = _request(
            scenario,
            promotion_ref=f"promotion-{failed_gate.value}-veto-{uuid4().hex}",
            candidate=failed_candidate,
            evaluation=failed_evaluation,
        )
        with (
            scenario.authority.authorize(authorization) as call,
            pytest.raises(ReleasePromotionRejected),
        ):
            scenario.learning.promote(call)
        _assert_empty_publication_state(release_database, organization_id)


def test_rollback_appends_a_new_event_without_mutating_history(
    release_database: _ReleaseDatabaseFixture,
    guarded_learning_engine: Engine,
) -> None:
    organization_id = release_database.create_organization()
    scenario = _scenario(
        guarded_learning_engine,
        organization_id,
        suffix=uuid4().hex,
    )
    release_database.insert_grant(scenario.identity)

    manifest_a = _manifest(
        organization_id,
        suffix=f"release-a-{uuid4().hex}",
    )
    candidate_a = _candidate(
        organization_id,
        suffix=f"activate-a-{uuid4().hex}",
        manifest=manifest_a,
    )
    evaluation_a = _persist_and_evaluate(scenario, candidate_a)
    first = _promote(
        scenario,
        promotion_ref=f"promotion-a-{uuid4().hex}",
        candidate=candidate_a,
        evaluation=evaluation_a,
    )

    manifest_b = _manifest(
        organization_id,
        suffix=f"release-b-{uuid4().hex}",
    )
    candidate_b = _candidate(
        organization_id,
        suffix=f"activate-b-{uuid4().hex}",
        generation=1,
        base_manifest_digest=manifest_a.manifest_digest,
        manifest=manifest_b,
    )
    evaluation_b = _persist_and_evaluate(scenario, candidate_b)
    second = _promote(
        scenario,
        promotion_ref=f"promotion-b-{uuid4().hex}",
        candidate=candidate_b,
        evaluation=evaluation_b,
    )
    history_before_rollback = release_database.state(organization_id)

    rollback_candidate = _candidate(
        organization_id,
        suffix=f"rollback-to-a-{uuid4().hex}",
        generation=2,
        base_manifest_digest=manifest_b.manifest_digest,
        manifest=manifest_a,
    )
    rollback_evaluation = _persist_and_evaluate(scenario, rollback_candidate)
    rollback = _promote(
        scenario,
        promotion_ref=f"promotion-rollback-a-{uuid4().hex}",
        candidate=rollback_candidate,
        evaluation=rollback_evaluation,
    )

    state = release_database.state(organization_id)
    observed_generations = (
        first.active_generation,
        second.active_generation,
        rollback.active_generation,
    )
    assert observed_generations == (
        1,
        2,
        3,
    )
    assert state.pointer is not None
    assert state.pointer["active_generation"] == 3
    assert state.pointer["manifest_digest"] == manifest_a.manifest_digest
    assert state.audits[:2] == history_before_rollback.audits
    assert tuple(row["active_generation"] for row in state.audits) == (1, 2, 3)
    assert tuple(row["manifest_digest"] for row in state.audits) == (
        manifest_a.manifest_digest,
        manifest_b.manifest_digest,
        manifest_a.manifest_digest,
    )
    assert tuple(row["expected_active_generation"] for row in state.audits) == (
        0,
        1,
        2,
    )
    assert {row["manifest_digest"] for row in state.manifests} == {
        manifest_a.manifest_digest,
        manifest_b.manifest_digest,
    }
    assert len(state.manifests) == 2
