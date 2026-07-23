from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from time import monotonic, sleep
from typing import Any, cast
from uuid import UUID

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from adapters.http.scope_authority import (
    MissingTrustedScopeAuthority,
    ScopeAuthorityIdentity,
)
from engine.control import (
    ContextControl,
    ControlOperation,
    ControlOperatorAuthority,
    FileImportAudience,
    FileImportPath,
    FileSourceCleanupState,
    FileSourceOffboarding,
    OffboardFileSource,
    PrepareFileImport,
    SourceControlUnavailable,
    SourceNotAvailable,
)
from engine.persistence import (
    DatabaseConfiguration,
    FileImportInterrupted,
    FilePublicationBoundary,
    MembershipIdentity,
    PostgreSQLControlStore,
    PostgreSQLMembershipAuthority,
    PostgreSQLWorkerLeaseIssuer,
    WorkerLeaseIssueNotAvailable,
    create_database_engine,
)
from engine.runtime import ContextAccessTicketIssuer, TicketSigningKeyring
from engine.runtime.contracts import Acquire
from engine.runtime.delivery import _construct_direct_delivery_context
from engine.runtime.evidence import CandidateRef
from engine.runtime.invocation import _construct_authenticated_http_invocation
from engine.runtime.materialized import MaterializedProjectionSession
from engine.runtime.organization import (
    _construct_existing_http_organization_verification,
)
from engine.runtime.package_digest import QueryDigestKeyring
from engine.runtime.ticket_identity import (
    TicketExecutionIdentity,
    _construct_ticket_execution_identity,
)
from engine.runtime.ticket_rejection import TicketNotAvailable
from engine.supply import PreparedFileImport, WorkNotAvailable
from tests.integration.test_file_import_tracer import (
    NOW,
    _ControlAuthenticator,
    _FileImportScenario,
    _prepare_file_import_scenario,
    _prepare_repeat_file_import,
    _run_file_import,
    _scenario_claims,
)
from tests.integration.test_zz_file_publication_recovery import (
    _run as _run_recoverable_file_import,
)
from tests.integration.test_zz_file_publication_recovery import (
    _worker as _recovery_worker,
)
from tests.integration.test_zz_file_revision_replacement import (
    OLD_MARKDOWN,
    _resolve,
    _scenario_user_id,
)

pytestmark = pytest.mark.integration


class _ReplayCandidateIndex:
    def __init__(self, candidate: CandidateRef) -> None:
        self.candidate = candidate

    def discover(
        self,
        request: Acquire,
        projection_session: MaterializedProjectionSession,
    ) -> tuple[CandidateRef, ...]:
        del request, projection_session
        return (self.candidate,)


def _control(
    scenario: _FileImportScenario,
    guarded_control_engine: Engine,
) -> tuple[ContextControl, ControlOperatorAuthority]:
    authority = ControlOperatorAuthority(
        _ControlAuthenticator(scenario.organization_id),
        call_ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    )
    return (
        ContextControl(
            store=PostgreSQLControlStore(
                guarded_control_engine,
                clock=lambda: NOW,
                file_import_receiver=scenario.receiver,
            ),
            authority=authority,
            clock=lambda: NOW,
        ),
        authority,
    )


def _prepare_only(
    scenario: _FileImportScenario,
    guarded_control_engine: Engine,
    *,
    idempotency_key: str,
) -> PreparedFileImport:
    control, authority = _control(scenario, guarded_control_engine)
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.IMPORT_FILE,
        request_id=f"prepare-{idempotency_key}",
    ) as call:
        return control.prepare_file_import(
            call,
            PrepareFileImport(
                source_ref=scenario.source_ref,
                path=FileImportPath("handbook.md"),
                audience=FileImportAudience(
                    principal_ref="principal:file-reader",
                    membership_id=scenario.membership_id,
                    membership_version=1,
                ),
                idempotency_key=idempotency_key,
            ),
        )


def _offboard(
    scenario: _FileImportScenario,
    guarded_control_engine: Engine,
) -> FileSourceOffboarding:
    control, authority = _control(scenario, guarded_control_engine)
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.OFFBOARD_FILE_SOURCE,
        request_id="offboard-file-source",
    ) as call:
        return control.offboard_file_source(
            call,
            OffboardFileSource(source_ref=scenario.source_ref),
        )


def _activate_recoverable_direct(
    scenario: _FileImportScenario,
    migration_configuration: DatabaseConfiguration,
    guarded_worker_engine: Engine,
) -> object | None:
    claims = _scenario_claims(scenario)
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            publication = connection.execute(
                text(
                    """
                    SELECT resource_ref, revision_id
                    FROM file_import_job
                    WHERE organization_id = :organization_id
                      AND job_id = :job_id
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "job_id": scenario.prepared.job_id,
                },
            ).one()
    finally:
        migration_engine.dispose()
    with guarded_worker_engine.begin() as connection:
        return connection.execute(
            text(
                """
                SELECT *
                FROM public.context_worker_activate_recoverable_file_publication(
                    :organization_id, :job_id, :service_principal_id,
                    :source_ref, :resource_ref, :revision_id,
                    :lease_generation, :signing_key_version, :nonce,
                    :issued_at, :expires_at
                )
                """
            ),
            {
                "organization_id": claims.organization_id,
                "job_id": claims.job_id,
                "service_principal_id": claims.service_principal_id,
                "source_ref": claims.source_ref,
                "resource_ref": publication.resource_ref,
                "revision_id": publication.revision_id,
                "lease_generation": claims.lease_generation,
                "signing_key_version": claims.signing_key_version,
                "nonce": claims.nonce,
                "issued_at": claims.issued_at,
                "expires_at": claims.expires_at,
            },
        ).one_or_none()


def _prepare_ready_interrupted_import(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    *,
    lease_ttl_seconds: int = 300,
) -> _FileImportScenario:
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=OLD_MARKDOWN,
        lease_ttl_seconds=lease_ttl_seconds,
    )
    assert scenario.token is not None
    with pytest.raises(FileImportInterrupted):
        _run_recoverable_file_import(
            _recovery_worker(
                scenario,
                guarded_worker_engine,
                config_version="markdown-config-v2",
                interrupt_after=FilePublicationBoundary.INDEXED,
            ),
            scenario,
            scenario.token,
        )
    return scenario


def _publication_lock_waiter_count(
    connection: Any,
    organization_id: UUID,
) -> int:
    """Count waiters only for this Organization's bigint publication lock."""

    return int(
        connection.execute(
            text(
                """
                WITH expected AS (
                    SELECT pg_catalog.hashtextextended(
                        'context-engine.file-publication:'
                        || CAST(:organization_id AS text),
                        0
                    ) AS lock_key
                )
                SELECT count(*)
                FROM pg_catalog.pg_locks AS held
                CROSS JOIN expected
                WHERE held.locktype = 'advisory'
                  AND held.database = (
                      SELECT oid FROM pg_catalog.pg_database
                      WHERE datname = pg_catalog.current_database()
                  )
                  AND held.classid = (
                      (expected.lock_key >> 32) & 4294967295
                  )::oid
                  AND held.objid = (
                      expected.lock_key & 4294967295
                  )::oid
                  AND held.objsubid = 1
                  AND held.granted IS FALSE
                """
            ),
            {"organization_id": organization_id},
        ).scalar_one()
    )


def _stable_empty_package(package: dict[str, Any]) -> dict[str, Any]:
    stable = dict(package)
    for field in (
        "asOf",
        "audienceDigest",
        "decisionRef",
        "expiresAt",
        "packageDigest",
        "packageId",
        "packageSchemaRef",
        "policyEpoch",
        "policySnapshotRef",
        "releaseManifestRef",
        "retentionPolicyRef",
        "runRef",
        "tokenizerRef",
        "continuation",
    ):
        stable.pop(field)
    return stable


def _assert_cleanup_intent_denies_direct_nonowner_access(
    guarded_control_engine: Engine,
    guarded_runtime_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    statements = (
        "SELECT count(*) FROM file_source_cleanup_intent",
        "INSERT INTO file_source_cleanup_intent DEFAULT VALUES",
        "UPDATE file_source_cleanup_intent "
        "SET organization_id = organization_id WHERE false",
        "DELETE FROM file_source_cleanup_intent WHERE false",
    )
    for engine in (
        guarded_control_engine,
        guarded_runtime_engine,
        guarded_worker_engine,
    ):
        for statement in statements:
            with engine.connect() as connection:
                transaction = connection.begin()
                try:
                    with pytest.raises(SQLAlchemyError):
                        connection.execute(text(statement))
                finally:
                    transaction.rollback()


@contextmanager
def _fresh_ticket_identity(
    scenario: _FileImportScenario,
    guarded_runtime_engine: Engine,
    *,
    user_id: UUID,
) -> Iterator[TicketExecutionIdentity]:
    membership_identity = MembershipIdentity(
        organization_id=scenario.organization_id,
        user_id=user_id,
        membership_id=scenario.membership_id,
        membership_version=1,
        principal_ref="principal:file-reader",
        request_id="request:file-source-offboard-ticket",
        authentication_binding_ref="binding:file-source-offboard-ticket",
        checked_at=NOW,
    )
    with PostgreSQLMembershipAuthority(
        guarded_runtime_engine
    ).current_user_actor(membership_identity) as membership:
        scope_identity = ScopeAuthorityIdentity(
            organization_id=scenario.organization_id,
            user_id=user_id,
            membership_id=scenario.membership_id,
            membership_version=1,
            policy_epoch=membership.policy_epoch,
            principal_ref="principal:file-reader",
            agent_version_ref="agent:file-source-offboard-ticket",
            purpose="context.answer",
            request_id=membership_identity.request_id,
            authentication_binding_ref=(
                membership_identity.authentication_binding_ref
            ),
            checked_at=NOW,
        )
        organization = _construct_existing_http_organization_verification(
            organization_id=scenario.organization_id,
            request_id=membership_identity.request_id,
            authentication_binding_ref=(
                membership_identity.authentication_binding_ref
            ),
            verified_at=NOW,
        )
        with MissingTrustedScopeAuthority().current_scope(
            scope_identity
        ) as scope_snapshot:
            invocation = _construct_authenticated_http_invocation(
                request_id=membership_identity.request_id,
                authenticated_organization_ref=str(scenario.organization_id),
                organization_verification=organization,
                user_ref=str(user_id),
                principal_ref="principal:file-reader",
                membership_ref=str(scenario.membership_id),
                membership_version=1,
                current_membership_verification=membership,
                agent_version_ref="agent:file-source-offboard-ticket",
                authenticated_application_ref="application:file-offboard",
                authentication_binding_ref=(
                    membership_identity.authentication_binding_ref
                ),
                trusted_purpose="context.answer",
                received_at=NOW,
                trusted_scope_snapshot=scope_snapshot,
            )
            delivery = _construct_direct_delivery_context(
                purpose="context.answer",
                authenticated_application_ref="application:file-offboard",
                delivery_binding_ref=(
                    membership_identity.authentication_binding_ref
                ),
                established_at=NOW,
            )
            yield _construct_ticket_execution_identity(
                invocation=invocation,
                delivery_context=delivery,
            )


@pytest.mark.security_evidence(id="PG-FILE-SOURCE-OFFBOARD-030", layer="postgres")
def test_offboard_is_immediate_with_stale_candidates_and_zero_worker_effects(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=OLD_MARKDOWN,
    )
    assert scenario.token is not None
    _run_file_import(
        scenario,
        scenario.prepared,
        scenario.token,
        guarded_worker_engine,
        config_version="markdown-config-v2",
    )
    user_id = _scenario_user_id(scenario, migration_configuration)
    delivered = _resolve(
        scenario,
        guarded_runtime_engine,
        query_digest_keyring,
        user_id=user_id,
        query="OLD marker.",
        request_id="file-source-offboard-before",
    )
    evidence = delivered["evidence"][0]
    candidate = CandidateRef(
        organization_id=scenario.organization_id,
        source_ref=evidence["sourceRef"],
        resource_ref=evidence["resourceRef"],
        revision_ref=evidence["revisionRef"],
        fragment_ref=evidence["fragmentRef"],
    )
    leased_job, stale_token = _prepare_repeat_file_import(
        scenario,
        guarded_control_engine,
        idempotency_key="offboard-leased-job",
    )
    available_job = _prepare_only(
        scenario,
        guarded_control_engine,
        idempotency_key="offboard-available-job",
    )

    committed = _offboard(scenario, guarded_control_engine)

    assert committed.organization_id == scenario.organization_id
    assert committed.source_ref == scenario.source_ref
    assert committed.policy_epoch == 2
    assert committed.cancelled_job_count == 2
    assert committed.retained_resource_count == 1
    assert committed.cleanup_state is FileSourceCleanupState.PENDING
    denied = _resolve(
        scenario,
        guarded_runtime_engine,
        query_digest_keyring,
        user_id=user_id,
        query="OLD marker.",
        request_id="file-source-offboard-after",
        candidate_index=_ReplayCandidateIndex(candidate),
    )
    expected_empty = {
        "blocks": [],
        "budgetUsage": {
            "costMicrounits": 0,
            "elapsedMs": 0,
            "providerCalls": 0,
            "tokens": 0,
        },
        "coverage": {"reason": "no_authorized_evidence", "status": "empty"},
        "evidence": [],
        "gaps": [],
        "purpose": "context.answer",
        "ttlSeconds": 300,
    }
    assert _stable_empty_package(denied) == expected_empty
    assert candidate.resource_ref not in str(denied)
    assert candidate.revision_ref not in str(denied)

    with _fresh_ticket_identity(
        scenario,
        guarded_runtime_engine,
        user_id=user_id,
    ) as fresh_identity:
        assert fresh_identity.policy_epoch == 2
        with pytest.raises(TicketNotAvailable):
            ContextAccessTicketIssuer(
                keyring=TicketSigningKeyring(
                    active_version=1,
                    keys={1: b"o" * 32},
                ),
                organization_id=scenario.organization_id,
                provider_ref="provider:file",
                source_ref=scenario.source_ref.value,
                clock=lambda: NOW,
            ).issue(fresh_identity)

    with pytest.raises(SourceNotAvailable):
        _prepare_only(
            scenario,
            guarded_control_engine,
            idempotency_key="offboard-new-job",
        )
    with pytest.raises(WorkerLeaseIssueNotAvailable):
        PostgreSQLWorkerLeaseIssuer(
            guarded_control_engine,
            scenario.codec,
        ).issue_file_import_lease(available_job)
    with pytest.raises(WorkNotAvailable):
        _run_file_import(
            scenario,
            leased_job,
            stale_token,
            guarded_worker_engine,
        )

    _assert_cleanup_intent_denies_direct_nonowner_access(
        guarded_control_engine,
        guarded_runtime_engine,
        guarded_worker_engine,
    )

    replay = _offboard(scenario, guarded_control_engine)
    assert replay == committed
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT source.lifecycle_state, source.disabled_version_id,
                           epoch.policy_epoch, intent.cleanup_state,
                           intent.cancelled_job_count,
                           intent.retained_resource_count,
                           (SELECT count(*) FROM context_revision AS revision
                            WHERE revision.organization_id = source.organization_id),
                           (SELECT count(*) FROM exact_phrase_candidate AS candidate
                            WHERE candidate.organization_id = source.organization_id),
                           (SELECT count(*) FROM file_import_job AS job
                            WHERE job.organization_id = source.organization_id
                              AND job.source_id = source.source_id
                              AND job.state = 'cancelled')
                    FROM context_source AS source
                    JOIN organization_policy_epoch AS epoch
                      ON epoch.organization_id = source.organization_id
                    JOIN file_source_cleanup_intent AS intent
                      ON intent.organization_id = source.organization_id
                     AND intent.source_id = source.source_id
                    WHERE source.organization_id = :organization_id
                      AND source.source_id = :source_id
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "source_id": scenario.source_ref.value,
                },
            ).one()
        assert tuple(row) == (
            "disabled",
            committed.source_version_ref,
            2,
            "pending",
            2,
            1,
            1,
            6,
            2,
        )
    finally:
        migration_engine.dispose()


def test_offboard_epoch_disable_cancellation_and_intent_roll_back_together(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        issue_lease=False,
    )
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE FUNCTION public.test_reject_source_offboard_epoch()
                    RETURNS trigger
                    LANGUAGE plpgsql
                    SECURITY INVOKER
                    SET search_path = pg_catalog
                    AS $function$
                    BEGIN
                        RAISE EXCEPTION 'injected source offboard failure';
                    END;
                    $function$
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TRIGGER reject_source_offboard_epoch
                    BEFORE UPDATE ON organization_policy_epoch
                    FOR EACH ROW
                    EXECUTE FUNCTION public.test_reject_source_offboard_epoch()
                    """
                )
            )
        try:
            with pytest.raises(SourceControlUnavailable):
                _offboard(scenario, guarded_control_engine)
        finally:
            with migration_engine.begin() as connection:
                connection.execute(
                    text(
                        "DROP TRIGGER reject_source_offboard_epoch "
                        "ON organization_policy_epoch"
                    )
                )
                connection.execute(
                    text(
                        "DROP FUNCTION public.test_reject_source_offboard_epoch()"
                    )
                )

        with migration_engine.connect() as connection:
            state = connection.execute(
                text(
                    """
                    SELECT source.lifecycle_state, source.disabled_version_id,
                           epoch.policy_epoch, job.state,
                           job.cancellation_intent_id,
                           (SELECT count(*) FROM file_source_cleanup_intent
                            WHERE organization_id = source.organization_id)
                    FROM context_source AS source
                    JOIN organization_policy_epoch AS epoch
                      ON epoch.organization_id = source.organization_id
                    JOIN file_import_job AS job
                      ON job.organization_id = source.organization_id
                     AND job.source_id = source.source_id
                    WHERE source.organization_id = :organization_id
                      AND source.source_id = :source_id
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "source_id": scenario.source_ref.value,
                },
            ).one()
        assert tuple(state) == ("active", None, 1, "available", None, 0)
    finally:
        migration_engine.dispose()


def test_offboard_counts_resources_committed_before_job_cancellation(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _prepare_ready_interrupted_import(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        guarded_worker_engine,
    )
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as barrier_connection:
            barrier_transaction = barrier_connection.begin()
            barrier_connection.execute(
                text(
                    """
                    SELECT pg_catalog.pg_advisory_xact_lock_shared(
                        pg_catalog.hashtextextended(
                            'context-engine.file-publication:'
                            || CAST(:organization_id AS text),
                            0
                        )
                    )
                    """
                ),
                {"organization_id": scenario.organization_id},
            )

            with ThreadPoolExecutor(max_workers=2) as executor:
                offboarding = executor.submit(
                    _offboard, scenario, guarded_control_engine
                )
                deadline = monotonic() + 10
                while monotonic() < deadline:
                    with migration_engine.connect() as observer:
                        waiters = _publication_lock_waiter_count(
                            observer,
                            scenario.organization_id,
                        )
                    if waiters >= 1:
                        break
                    sleep(0.01)
                else:
                    barrier_transaction.rollback()
                    offboarding.result(timeout=5)
                    pytest.fail("offboarding did not reach the publication fence")

                activation = executor.submit(
                    _activate_recoverable_direct,
                    scenario,
                    migration_configuration,
                    guarded_worker_engine,
                )
                deadline = monotonic() + 10
                while monotonic() < deadline:
                    with migration_engine.connect() as observer:
                        waiters = _publication_lock_waiter_count(
                            observer,
                            scenario.organization_id,
                        )
                    if waiters >= 2:
                        break
                    sleep(0.01)
                else:
                    barrier_transaction.rollback()
                    activation.result(timeout=5)
                    offboarding.result(timeout=5)
                    pytest.fail("activation did not reach the publication fence")

                released_at = barrier_connection.execute(
                    text("SELECT pg_catalog.clock_timestamp()")
                ).scalar_one()
                barrier_transaction.commit()
                committed = offboarding.result(timeout=10)
                assert activation.result(timeout=10) is None

        with migration_engine.connect() as connection:
            state = connection.execute(
                text(
                    """
                    SELECT intent.retained_resource_count, job.state,
                           job.effect_count, source.disabled_at,
                           intent.security_completed_at, job.cancelled_at,
                           (SELECT count(*) FROM context_resource AS resource
                            WHERE resource.organization_id = intent.organization_id
                              AND resource.source_ref = :source_ref)
                    FROM file_source_cleanup_intent AS intent
                    JOIN context_source AS source
                      ON source.organization_id = intent.organization_id
                     AND source.source_id = intent.source_id
                    JOIN file_import_job AS job
                      ON job.organization_id = intent.organization_id
                     AND job.source_id = intent.source_id
                    WHERE intent.organization_id = :organization_id
                      AND intent.source_id = :source_id
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "source_id": scenario.source_ref.value,
                    "source_ref": str(scenario.source_ref.value),
                },
            ).one()
        assert committed.retained_resource_count == 1
        assert tuple(state[:3]) == (1, "cancelled", 0)
        assert state.disabled_at == state.security_completed_at == state.cancelled_at
        assert state.disabled_at >= released_at
        assert state[6] == 1
    finally:
        migration_engine.dispose()


def test_offboard_waits_for_activation_that_already_crossed_publication_fence(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _prepare_ready_interrupted_import(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        guarded_worker_engine,
    )
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as job_barrier:
            barrier_transaction = job_barrier.begin()
            transaction_id = job_barrier.execute(
                text("SELECT pg_current_xact_id()::text")
            ).scalar_one()
            job_barrier.execute(
                text(
                    """
                    SELECT 1 FROM file_import_job
                    WHERE organization_id = :organization_id
                      AND job_id = :job_id
                    FOR UPDATE
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "job_id": scenario.prepared.job_id,
                },
            ).one()

            with ThreadPoolExecutor(max_workers=2) as executor:
                activation = executor.submit(
                    _activate_recoverable_direct,
                    scenario,
                    migration_configuration,
                    guarded_worker_engine,
                )
                deadline = monotonic() + 10
                while monotonic() < deadline:
                    with migration_engine.connect() as observer:
                        blocked = observer.execute(
                            text(
                                """
                                SELECT EXISTS (
                                    SELECT 1 FROM pg_locks
                                    WHERE locktype = 'transactionid'
                                      AND transactionid::text = :transaction_id
                                      AND granted IS FALSE
                                )
                                """
                            ),
                            {"transaction_id": transaction_id},
                        ).scalar_one()
                    if blocked:
                        break
                    sleep(0.01)
                else:
                    barrier_transaction.rollback()
                    activation.result(timeout=5)
                    pytest.fail("activation did not reach the locked job")

                offboarding = executor.submit(
                    _offboard, scenario, guarded_control_engine
                )
                deadline = monotonic() + 10
                while monotonic() < deadline:
                    with migration_engine.connect() as observer:
                        waiters = _publication_lock_waiter_count(
                            observer,
                            scenario.organization_id,
                        )
                    if waiters >= 1:
                        break
                    sleep(0.01)
                else:
                    barrier_transaction.rollback()
                    activation.result(timeout=5)
                    offboarding.result(timeout=5)
                    pytest.fail("offboarding did not wait behind activation")

                barrier_transaction.commit()
                activated = activation.result(timeout=10)
                committed = offboarding.result(timeout=10)

        assert activated is not None
        assert cast(Any, activated).effect_count == 1
        assert committed.cancelled_job_count == 0
        assert committed.retained_resource_count == 1
        with migration_engine.connect() as connection:
            state = connection.execute(
                text(
                    """
                    SELECT job.state, job.effect_count,
                           intent.retained_resource_count
                    FROM file_import_job AS job
                    JOIN file_source_cleanup_intent AS intent
                      ON intent.organization_id = job.organization_id
                     AND intent.source_id = job.source_id
                    WHERE job.organization_id = :organization_id
                      AND job.job_id = :job_id
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "job_id": scenario.prepared.job_id,
                },
            ).one()
        assert tuple(state) == ("completed", 1, 1)
    finally:
        migration_engine.dispose()


@pytest.mark.parametrize("wrong_binding", ["organization", "job", "resource"])
def test_invalid_activation_binding_cannot_acquire_advisory_fences(
    wrong_binding: str,
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _prepare_ready_interrupted_import(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        guarded_worker_engine,
    )
    claims = _scenario_claims(scenario)
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            publication = connection.execute(
                text(
                    """
                    SELECT resource_ref, revision_id
                    FROM file_import_job
                    WHERE organization_id = :organization_id
                      AND job_id = :job_id
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "job_id": scenario.prepared.job_id,
                },
            ).one()
    finally:
        migration_engine.dispose()

    arguments: dict[str, Any] = {
        "organization_id": claims.organization_id,
        "job_id": claims.job_id,
        "service_principal_id": claims.service_principal_id,
        "source_ref": claims.source_ref,
        "resource_ref": publication.resource_ref,
        "revision_id": publication.revision_id,
        "lease_generation": claims.lease_generation,
        "signing_key_version": claims.signing_key_version,
        "nonce": claims.nonce,
        "issued_at": claims.issued_at,
        "expires_at": claims.expires_at,
    }
    arguments[{
        "organization": "organization_id",
        "job": "job_id",
        "resource": "resource_ref",
    }[wrong_binding]] = (
        "resource:wrong-binding"
        if wrong_binding == "resource"
        else UUID(int=0)
    )
    with guarded_worker_engine.begin() as connection:
        assert connection.execute(
            text(
                """
                SELECT *
                FROM public.context_worker_activate_recoverable_file_publication(
                    :organization_id, :job_id, :service_principal_id,
                    :source_ref, :resource_ref, :revision_id,
                    :lease_generation, :signing_key_version, :nonce,
                    :issued_at, :expires_at
                )
                """
            ),
            arguments,
        ).one_or_none() is None
        held_fences = connection.execute(
            text(
                """
                SELECT count(*) FROM pg_catalog.pg_locks
                WHERE pid = pg_catalog.pg_backend_pid()
                  AND locktype = 'advisory'
                """
            )
        ).scalar_one()
        assert held_fences == 0


def test_activation_revalidates_lease_after_waiting_for_publication_fence(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _prepare_ready_interrupted_import(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        guarded_worker_engine,
        lease_ttl_seconds=5,
    )
    claims = _scenario_claims(scenario)
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as barrier_connection:
            barrier_transaction = barrier_connection.begin()
            barrier_connection.execute(
                text(
                    """
                    SELECT pg_catalog.pg_advisory_xact_lock_shared(
                        pg_catalog.hashtextextended(
                            'context-engine.file-publication:'
                            || CAST(:organization_id AS text),
                            0
                        )
                    )
                    """
                ),
                {"organization_id": scenario.organization_id},
            )
            with ThreadPoolExecutor(max_workers=1) as executor:
                activation = executor.submit(
                    _activate_recoverable_direct,
                    scenario,
                    migration_configuration,
                    guarded_worker_engine,
                )
                deadline = monotonic() + 10
                while monotonic() < deadline:
                    with migration_engine.connect() as observer:
                        waiters = _publication_lock_waiter_count(
                            observer,
                            scenario.organization_id,
                        )
                    if waiters == 1:
                        break
                    sleep(0.01)
                else:
                    barrier_transaction.rollback()
                    activation.result(timeout=5)
                    pytest.fail("activation did not reach the publication fence")

                barrier_connection.execute(
                    text(
                        """
                        SELECT pg_catalog.pg_sleep(
                            GREATEST(
                                EXTRACT(EPOCH FROM (
                                    CAST(:expires_at AS timestamptz)
                                    - pg_catalog.clock_timestamp()
                                )) + 0.1,
                                0
                            )::double precision
                        )
                        """
                    ),
                    {"expires_at": claims.expires_at},
                )
                barrier_transaction.commit()
                assert activation.result(timeout=10) is None

        with migration_engine.connect() as connection:
            state = connection.execute(
                text(
                    """
                    SELECT job.state, job.effect_count,
                           resource.active_revision_id
                    FROM file_import_job AS job
                    JOIN context_resource AS resource
                      ON resource.organization_id = job.organization_id
                     AND resource.resource_ref = job.resource_ref
                    WHERE job.organization_id = :organization_id
                      AND job.job_id = :job_id
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "job_id": scenario.prepared.job_id,
                },
            ).one()
        assert tuple(state) == ("ready", 0, None)
    finally:
        migration_engine.dispose()


def test_file_source_offboard_is_organization_isolated_and_non_enumerating(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
) -> None:
    disabled = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
    )
    unaffected = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
    )
    assert disabled.token is not None
    assert unaffected.token is not None
    _run_file_import(
        disabled,
        disabled.prepared,
        disabled.token,
        guarded_worker_engine,
    )
    unaffected_publication = _run_file_import(
        unaffected,
        unaffected.prepared,
        unaffected.token,
        guarded_worker_engine,
    )
    _offboard(disabled, guarded_control_engine)

    unaffected_user_id = _scenario_user_id(unaffected, migration_configuration)
    package = _resolve(
        unaffected,
        guarded_runtime_engine,
        query_digest_keyring,
        user_id=unaffected_user_id,
        query="ContextEngine delivers context.",
        request_id="file-source-offboard-org-b",
    )
    assert package["evidence"][0]["resourceRef"] == (
        unaffected_publication.candidate_ref.resource_ref
    )

    control, authority = _control(disabled, guarded_control_engine)
    for attempted_ref in (
        unaffected.source_ref,
        type(disabled.source_ref)(UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")),
    ):
        with (
            authority.authorize(
                opaque_credential="control-secret",
                operation=ControlOperation.OFFBOARD_FILE_SOURCE,
                request_id=f"offboard-unavailable-{attempted_ref.value}",
            ) as call,
            pytest.raises(SourceNotAvailable),
        ):
            control.offboard_file_source(
                call,
                OffboardFileSource(source_ref=attempted_ref),
            )

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            state = {
                row.organization_id: (
                    row.lifecycle_state,
                    row.policy_epoch,
                    row.cleanup_count,
                )
                for row in connection.execute(
                    text(
                        """
                        SELECT source.organization_id, source.lifecycle_state,
                               epoch.policy_epoch,
                               (SELECT count(*)
                                FROM file_source_cleanup_intent AS intent
                                WHERE intent.organization_id =
                                      source.organization_id) AS cleanup_count
                        FROM context_source AS source
                        JOIN organization_policy_epoch AS epoch
                          ON epoch.organization_id = source.organization_id
                        WHERE source.organization_id IN (:disabled, :unaffected)
                        """
                    ),
                    {
                        "disabled": disabled.organization_id,
                        "unaffected": unaffected.organization_id,
                    },
                )
            }
        assert state == {
            disabled.organization_id: ("disabled", 2, 1),
            unaffected.organization_id: ("active", 1, 0),
        }
    finally:
        migration_engine.dispose()
