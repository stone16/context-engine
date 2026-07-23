from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any
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


def _stable_empty_package(package: dict[str, Any]) -> dict[str, Any]:
    stable = dict(package)
    for field in (
        "asOf",
        "decisionRef",
        "expiresAt",
        "organizationRef",
        "packageDigest",
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
                source_ref=str(scenario.source_ref.value),
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
