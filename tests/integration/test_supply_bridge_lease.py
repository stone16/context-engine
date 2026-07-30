from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait as wait_for_futures
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from threading import Barrier, Lock
from uuid import uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLConnectorCheckpointStore,
    PostgreSQLStagedArtifactSink,
    PostgreSQLSupplyBridgeLeaseIssuer,
    PostgreSQLSupplyExecutionBridge,
    SupplyBridgeExecutionIdentity,
    SupplyBridgeLeaseIssueRequest,
    SupplyBridgeLeasePreemptionRequest,
    SupplyBridgeUnavailable,
    create_database_engine,
)
from engine.supply.execution import (
    ConnectorCheckpointBinding,
    SupplyBridgeExecution,
    SupplyChangePage,
    serialize_supply_change_page,
)
from engine.supply.jobs import (
    WORKER_LEASE_OPERATION,
    WorkerLeaseClaims,
    WorkNotAvailable,
    generate_worker_lease_nonce,
)
from tests.integration.test_connector_checkpoint_store import (
    _page,
    _Scenario,
    _seed_scenario,
    _TwoPageAdapter,
)
from tests.integration.test_connector_checkpoint_store import (
    scenarios as _checkpoint_scenarios,
)

pytestmark = pytest.mark.integration
scenarios = _checkpoint_scenarios
MUTATED_SOURCE_VERSION_ID = uuid4()
PREEMPTION_REASON_DIGEST = "a" * 64


def _bridge(
    scenario: _Scenario,
    engine: Engine,
    *,
    identity: SupplyBridgeExecutionIdentity | None = None,
) -> PostgreSQLSupplyExecutionBridge:
    claims = _claims(scenario)
    policy_epoch = claims.policy_epoch
    actor_expiry = claims.service_actor_expires_at
    idempotency_key = claims.idempotency_key
    assert policy_epoch is not None
    assert actor_expiry is not None
    assert claims.idempotency_key is not None
    assert idempotency_key is not None
    return PostgreSQLSupplyExecutionBridge(
        engine,
        scenario.codec,
        identity
        or SupplyBridgeExecutionIdentity(
            organization_id=scenario.organization_id,
            service_principal_id=scenario.service_principal_id,
            allowed_source_version_ids=(scenario.source_version_id,),
            allowed_operations=("connector.execute",),
            policy_epoch=policy_epoch,
            idempotency_key=idempotency_key,
            expires_at=actor_expiry,
        ),
        PostgreSQLConnectorCheckpointStore(engine),
        PostgreSQLStagedArtifactSink(engine),
        clock=lambda: datetime.now(UTC).replace(microsecond=0),
    )


def _claims(scenario: _Scenario) -> WorkerLeaseClaims:
    claims = scenario.codec.verify(
        scenario.execution.worker_lease,
        expected_organization_id=scenario.organization_id,
        expected_job_id=scenario.job_id,
        expected_service_principal_id=scenario.service_principal_id,
        expected_workload="supply.connector",
        expected_operation="connector.execute",
        expected_worker_audience="context-engine-connector-runner",
        expected_source_version_ref=str(scenario.source_version_id),
        now=datetime.now(UTC).replace(microsecond=0),
    )
    assert claims.policy_epoch is not None
    assert claims.service_actor_expires_at is not None
    return claims


def test_absent_expired_or_wrong_job_worker_lease_refuses_work_and_has_no_user_path(
    scenarios: list[_Scenario],
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _seed_scenario(migration_configuration, guarded_control_engine)
    scenarios.append(scenario)
    claims = _claims(scenario)
    assert claims.lease_generation is not None
    policy_epoch = claims.policy_epoch
    actor_expiry = claims.service_actor_expires_at
    assert policy_epoch is not None
    assert actor_expiry is not None
    assert claims.idempotency_key is not None
    bridge = _bridge(scenario, guarded_worker_engine)
    page = _page(scenario, 1, terminal=True)

    with pytest.raises(TypeError):
        bridge.execute(None, _TwoPageAdapter((page,)))  # type: ignore[arg-type]
    with pytest.raises(WorkNotAvailable, match="^work not available$"):
        bridge.execute(
            SupplyBridgeExecution(
                organization_id=scenario.organization_id,
                source_version_id=scenario.source_version_id,
                worker_job_id=uuid4(),
                worker_lease=scenario.execution.worker_lease,
            ),
            _TwoPageAdapter((page,)),
        )

    expired_bridge = PostgreSQLSupplyExecutionBridge(
        guarded_worker_engine,
        scenario.codec,
        SupplyBridgeExecutionIdentity(
            organization_id=scenario.organization_id,
            service_principal_id=scenario.service_principal_id,
            allowed_source_version_ids=(scenario.source_version_id,),
            allowed_operations=("connector.execute",),
            policy_epoch=policy_epoch,
            idempotency_key=claims.idempotency_key,
            expires_at=actor_expiry,
        ),
        PostgreSQLConnectorCheckpointStore(guarded_worker_engine),
        PostgreSQLStagedArtifactSink(guarded_worker_engine),
        clock=lambda: datetime.now(UTC).replace(microsecond=0) + timedelta(hours=2),
    )
    with pytest.raises(WorkNotAvailable, match="^work not available$"):
        expired_bridge.execute(scenario.execution, _TwoPageAdapter((page,)))

    execution_fields = {item.name for item in fields(SupplyBridgeExecution)}
    identity_fields = {item.name for item in fields(SupplyBridgeExecutionIdentity)}
    assert execution_fields == {
        "organization_id",
        "source_version_id",
        "worker_job_id",
        "worker_lease",
    }
    assert identity_fields == {
        "organization_id",
        "service_principal_id",
        "actor_kind",
        "workload",
        "worker_audience",
        "operation",
        "allowed_source_version_ids",
        "allowed_operations",
        "policy_epoch",
        "idempotency_key",
        "expires_at",
    }
    identity = SupplyBridgeExecutionIdentity(
        organization_id=scenario.organization_id,
        service_principal_id=scenario.service_principal_id,
        allowed_source_version_ids=(scenario.source_version_id,),
        allowed_operations=("connector.execute",),
        policy_epoch=policy_epoch,
        idempotency_key=claims.idempotency_key,
        expires_at=actor_expiry,
    )
    assert identity.actor_kind == "service"
    assert identity.workload == "supply.connector"
    assert identity.worker_audience == ("context-engine-connector-runner")
    assert identity.operation == "connector.execute"
    assert claims.idempotency_key
    assert policy_epoch > 0
    assert claims.allowed_source_version_refs == (str(scenario.source_version_id),)
    assert claims.allowed_operations == ("connector.execute",)
    assert actor_expiry >= claims.expires_at
    assert not execution_fields.intersection(
        {"user_id", "membership_id", "principal_id", "triggering_user_id"}
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda claims: replace(claims, service_principal_id=uuid4()),
        lambda claims: replace(claims, workload="supply.connector.other"),
        lambda claims: replace(claims, worker_audience="other-runner"),
        lambda claims: replace(
            claims,
            operation=WORKER_LEASE_OPERATION,
            source_version_ref=None,
            lease_generation=None,
            policy_epoch=None,
            idempotency_key=None,
            allowed_source_version_refs=None,
            allowed_operations=None,
            service_actor_expires_at=None,
        ),
        lambda claims: replace(claims, nonce=generate_worker_lease_nonce()),
        lambda claims: replace(
            claims,
            lease_generation=(claims.lease_generation or 0) + 1,
        ),
        lambda claims: replace(
            claims,
            issued_at=claims.issued_at - timedelta(seconds=1),
        ),
        lambda claims: replace(
            claims,
            source_version_ref=str(MUTATED_SOURCE_VERSION_ID),
            allowed_source_version_refs=(str(MUTATED_SOURCE_VERSION_ID),),
        ),
        lambda claims: replace(
            claims,
            policy_epoch=(claims.policy_epoch or 0) + 1,
        ),
        lambda claims: replace(
            claims,
            idempotency_key="f" * 64,
        ),
        lambda claims: replace(
            claims,
            allowed_source_version_refs=tuple(
                sorted(
                    (
                        str(MUTATED_SOURCE_VERSION_ID),
                        claims.source_version_ref or "",
                    )
                )
            ),
        ),
        lambda claims: replace(
            claims,
            allowed_operations=("connector.execute", "noop.complete"),
        ),
        lambda claims: replace(
            claims,
            service_actor_expires_at=(
                claims.service_actor_expires_at or claims.expires_at
            )
            + timedelta(seconds=1),
        ),
    ],
    ids=[
        "principal",
        "workload",
        "audience",
        "operation",
        "nonce",
        "generation",
        "timestamps",
        "source",
        "policy-epoch",
        "idempotency-key",
        "allowed-sources",
        "allowed-operations",
        "actor-expiry",
    ],
)
def test_mismatched_worker_lease_claims_refuse_work(
    scenarios: list[_Scenario],
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    mutation: Callable[[WorkerLeaseClaims], WorkerLeaseClaims],
) -> None:
    scenario = _seed_scenario(migration_configuration, guarded_control_engine)
    scenarios.append(scenario)
    altered_claims = mutation(_claims(scenario))
    altered_execution = replace(
        scenario.execution,
        worker_lease=scenario.codec.mint(altered_claims),
    )

    with pytest.raises(WorkNotAvailable, match="^work not available$"):
        _bridge(scenario, guarded_worker_engine).execute(
            altered_execution,
            _TwoPageAdapter((_page(scenario, 1, terminal=True),)),
        )


def test_non_worker_caller_role_and_completed_lease_replay_refuse_work(
    scenarios: list[_Scenario],
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    guarded_runtime_engine: Engine,
) -> None:
    scenario = _seed_scenario(migration_configuration, guarded_control_engine)
    scenarios.append(scenario)
    adapter = _TwoPageAdapter((_page(scenario, 1, terminal=True),))

    with pytest.raises(SupplyBridgeUnavailable, match="role"):
        _bridge(scenario, guarded_runtime_engine).execute(
            scenario.execution,
            adapter,
        )

    _bridge(scenario, guarded_worker_engine).execute(scenario.execution, adapter)
    with pytest.raises(WorkNotAvailable, match="^work not available$"):
        _bridge(scenario, guarded_worker_engine).execute(
            scenario.execution,
            _TwoPageAdapter(
                (
                    _page(scenario, 1, terminal=True),
                    _page(scenario, 2, terminal=True),
                )
            ),
        )


def test_service_actor_registration_rejects_invalid_tuple_combinations(
    migration_configuration: DatabaseConfiguration,
) -> None:
    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            organization_id = uuid4()
            connection.execute(
                text("INSERT INTO organization (organization_id) VALUES (:org)"),
                {"org": organization_id},
            )
            with (
                pytest.raises(
                    IntegrityError,
                    match="workload_operation_binding",
                ),
                connection.begin_nested(),
            ):
                connection.execute(
                    text(
                        """
                            INSERT INTO service_principal (
                                organization_id, service_principal_id, workload,
                                worker_audience, operation, enabled
                            ) VALUES (
                                :org, :principal, 'supply.connector',
                                'context-engine-worker',
                                'connector.execute', true
                            )
                            """
                    ),
                    {"org": organization_id, "principal": uuid4()},
                )
            connection.execute(
                text("DELETE FROM organization WHERE organization_id = :org"),
                {"org": organization_id},
            )
    finally:
        engine.dispose()


def test_policy_epoch_advance_refuses_previously_minted_lease(
    scenarios: list[_Scenario],
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _seed_scenario(migration_configuration, guarded_control_engine)
    scenarios.append(scenario)
    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE organization_policy_epoch
                    SET policy_epoch = policy_epoch + 1
                    WHERE organization_id = :org
                    """
                ),
                {"org": scenario.organization_id},
            )
    finally:
        engine.dispose()

    with pytest.raises(WorkNotAvailable, match="^work not available$"):
        _bridge(scenario, guarded_worker_engine).execute(
            scenario.execution,
            _TwoPageAdapter((_page(scenario, 1, terminal=True),)),
        )


@pytest.mark.parametrize("outside", ["source", "operation", "idempotency"])
def test_service_actor_allowed_set_refuses_outside_source_or_operation(
    scenarios: list[_Scenario],
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    outside: str,
) -> None:
    scenario = _seed_scenario(migration_configuration, guarded_control_engine)
    scenarios.append(scenario)
    claims = _claims(scenario)
    policy_epoch = claims.policy_epoch
    actor_expiry = claims.service_actor_expires_at
    assert policy_epoch is not None
    assert claims.idempotency_key is not None
    assert actor_expiry is not None
    identity = SupplyBridgeExecutionIdentity(
        organization_id=scenario.organization_id,
        service_principal_id=scenario.service_principal_id,
        allowed_source_version_ids=(
            (uuid4() if outside == "source" else scenario.source_version_id),
        ),
        allowed_operations=(
            "noop.complete" if outside == "operation" else "connector.execute",
        ),
        policy_epoch=policy_epoch,
        idempotency_key=(
            "f" * 64 if outside == "idempotency" else claims.idempotency_key
        ),
        expires_at=actor_expiry,
    )

    with pytest.raises(WorkNotAvailable, match="^work not available$"):
        _bridge(
            scenario,
            guarded_worker_engine,
            identity=identity,
        ).execute(
            scenario.execution,
            _TwoPageAdapter((_page(scenario, 1, terminal=True),)),
        )


def test_expired_service_actor_refuses_valid_lease(
    scenarios: list[_Scenario],
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _seed_scenario(migration_configuration, guarded_control_engine)
    scenarios.append(scenario)
    claims = _claims(scenario)
    policy_epoch = claims.policy_epoch
    assert policy_epoch is not None
    assert claims.idempotency_key is not None
    identity = SupplyBridgeExecutionIdentity(
        organization_id=scenario.organization_id,
        service_principal_id=scenario.service_principal_id,
        allowed_source_version_ids=(scenario.source_version_id,),
        allowed_operations=("connector.execute",),
        policy_epoch=policy_epoch,
        idempotency_key=claims.idempotency_key,
        expires_at=claims.issued_at,
    )

    with pytest.raises(WorkNotAvailable, match="^work not available$"):
        _bridge(
            scenario,
            guarded_worker_engine,
            identity=identity,
        ).execute(
            scenario.execution,
            _TwoPageAdapter((_page(scenario, 1, terminal=True),)),
        )


def test_replayed_redeemed_lease_has_zero_second_effect(
    scenarios: list[_Scenario],
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _seed_scenario(migration_configuration, guarded_control_engine)
    scenarios.append(scenario)
    bridge = _bridge(scenario, guarded_worker_engine)
    page = _page(scenario, 1, terminal=True)
    bridge.execute(scenario.execution, _TwoPageAdapter((page,)))

    replay_adapter = _TwoPageAdapter((page, _page(scenario, 2, terminal=True)))
    with pytest.raises(WorkNotAvailable, match="^work not available$"):
        bridge.execute(scenario.execution, replay_adapter)

    assert replay_adapter.loaded_checkpoints == []
    assert replay_adapter.emitted_pages == []

    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        """
                    SELECT count(*) FROM supply_connector_accepted_page
                    WHERE organization_id = :org AND worker_job_id = :job
                    """
                    ),
                    {"org": scenario.organization_id, "job": scenario.job_id},
                ).scalar_one()
                == 1
            )
    finally:
        engine.dispose()


def test_nonterminal_redeemed_lease_cannot_reenter_connector_operation(
    scenarios: list[_Scenario],
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _seed_scenario(migration_configuration, guarded_control_engine)
    scenarios.append(scenario)
    bridge = _bridge(scenario, guarded_worker_engine)
    pages = (_page(scenario, 1, terminal=False), _page(scenario, 2, terminal=True))

    class _InterruptAfterFirstPage(_TwoPageAdapter):
        def poll(self, binding: ConnectorCheckpointBinding) -> SupplyChangePage:
            del binding
            raise RuntimeError("runner interrupted after first accepted page")

    with pytest.raises(RuntimeError, match="runner interrupted"):
        bridge.execute(scenario.execution, _InterruptAfterFirstPage(pages))

    replay_adapter = _TwoPageAdapter(pages)
    with pytest.raises(WorkNotAvailable, match="^work not available$"):
        bridge.execute(scenario.execution, replay_adapter)

    assert replay_adapter.loaded_checkpoints == []
    assert replay_adapter.emitted_pages == []


def test_two_concurrent_connector_lease_redemptions_enter_adapter_once(
    scenarios: list[_Scenario],
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _seed_scenario(migration_configuration, guarded_control_engine)
    scenarios.append(scenario)
    barrier = Barrier(2)
    effect_lock = Lock()
    effect_count = 0
    page = _page(scenario, 1, terminal=True)

    class _CountingAdapter(_TwoPageAdapter):
        def load(self, binding: ConnectorCheckpointBinding) -> SupplyChangePage:
            nonlocal effect_count
            with effect_lock:
                effect_count += 1
            return super().load(binding)

    def execute_once() -> str:
        adapter = _CountingAdapter((page,))
        barrier.wait(timeout=5)
        try:
            _bridge(scenario, guarded_worker_engine).execute(
                scenario.execution,
                adapter,
            )
        except WorkNotAvailable:
            return "rejected"
        return "completed"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(execute_once) for _ in range(2)]
        done, pending = wait_for_futures(futures, timeout=10)

    assert not pending
    assert sorted(future.result() for future in done) == ["completed", "rejected"]
    assert effect_count == 1


def test_ordinary_issue_refuses_unexpired_running_lease(
    scenarios: list[_Scenario],
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _seed_scenario(migration_configuration, guarded_control_engine)
    scenarios.append(scenario)
    claims = _claims(scenario)
    assert claims.lease_generation is not None
    store = PostgreSQLConnectorCheckpointStore(guarded_worker_engine)
    assert (
        store.redeem_for_execution(
            scenario.execution.binding,
            lease_claims=claims,
        )
        is None
    )

    with pytest.raises(WorkNotAvailable, match="^work not available$"):
        PostgreSQLSupplyBridgeLeaseIssuer(
            guarded_control_engine,
            scenario.codec,
        ).issue(
            SupplyBridgeLeaseIssueRequest(
                organization_id=scenario.organization_id,
                source_id=scenario.source_id,
                source_version_id=scenario.source_version_id,
                worker_job_id=scenario.job_id,
                service_principal_id=scenario.service_principal_id,
            )
        )


def test_ordinary_issue_reclaims_expired_running_lease(
    scenarios: list[_Scenario],
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _seed_scenario(migration_configuration, guarded_control_engine)
    scenarios.append(scenario)
    claims = _claims(scenario)
    assert claims.lease_generation is not None
    store = PostgreSQLConnectorCheckpointStore(guarded_worker_engine)
    assert (
        store.redeem_for_execution(
            scenario.execution.binding,
            lease_claims=claims,
        )
        is None
    )
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE supply_connector_job
                    SET lease_issued_at = lease_issued_at - interval '2 hours',
                        lease_expires_at = lease_expires_at - interval '2 hours',
                        service_actor_expires_at =
                            service_actor_expires_at - interval '2 hours',
                        redeemed_at = redeemed_at - interval '2 hours'
                    WHERE organization_id = :organization_id
                      AND worker_job_id = :worker_job_id
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "worker_job_id": scenario.job_id,
                },
            )
    finally:
        migration_engine.dispose()

    reclaimed = PostgreSQLSupplyBridgeLeaseIssuer(
        guarded_control_engine,
        scenario.codec,
    ).issue(
        SupplyBridgeLeaseIssueRequest(
            organization_id=scenario.organization_id,
            source_id=scenario.source_id,
            source_version_id=scenario.source_version_id,
            worker_job_id=scenario.job_id,
            service_principal_id=scenario.service_principal_id,
        )
    )
    reclaimed_claims = scenario.codec.verify(
        reclaimed,
        expected_organization_id=scenario.organization_id,
        expected_job_id=scenario.job_id,
        expected_service_principal_id=scenario.service_principal_id,
        expected_workload="supply.connector",
        expected_operation="connector.execute",
        expected_worker_audience="context-engine-connector-runner",
        expected_source_version_ref=str(scenario.source_version_id),
        now=datetime.now(UTC).replace(microsecond=0),
    )
    assert reclaimed_claims.lease_generation == claims.lease_generation + 1


def test_explicit_preemption_is_audited_and_stale_worker_accept_fails_closed(
    scenarios: list[_Scenario],
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _seed_scenario(migration_configuration, guarded_control_engine)
    scenarios.append(scenario)
    old_claims = _claims(scenario)
    assert old_claims.lease_generation is not None
    store = PostgreSQLConnectorCheckpointStore(guarded_worker_engine)
    assert (
        store.redeem_for_execution(
            scenario.execution.binding,
            lease_claims=old_claims,
        )
        is None
    )

    preempted_token = PostgreSQLSupplyBridgeLeaseIssuer(
        guarded_control_engine,
        scenario.codec,
    ).preempt(
        SupplyBridgeLeasePreemptionRequest(
            organization_id=scenario.organization_id,
            source_id=scenario.source_id,
            source_version_id=scenario.source_version_id,
            worker_job_id=scenario.job_id,
            service_principal_id=scenario.service_principal_id,
            reason_digest=PREEMPTION_REASON_DIGEST,
        )
    )
    preempted_claims = scenario.codec.verify(
        preempted_token,
        expected_organization_id=scenario.organization_id,
        expected_job_id=scenario.job_id,
        expected_service_principal_id=scenario.service_principal_id,
        expected_workload="supply.connector",
        expected_operation="connector.execute",
        expected_worker_audience="context-engine-connector-runner",
        expected_source_version_ref=str(scenario.source_version_id),
        now=datetime.now(UTC).replace(microsecond=0),
    )
    assert preempted_claims.lease_generation == old_claims.lease_generation + 1

    stale_page = _page(scenario, 1, terminal=False)
    with (
        pytest.raises(WorkNotAvailable, match="^work not available$"),
        guarded_worker_engine.begin() as connection,
    ):
        PostgreSQLStagedArtifactSink(guarded_worker_engine).accept_change_page(
            connection,
            stale_page,
            serialize_supply_change_page(stale_page),
            lease_claims=old_claims,
        )

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            event = connection.execute(
                text(
                    """
                    SELECT event_type, prior_lease_generation,
                           replacement_lease_generation, reason_digest
                    FROM supply_connector_lease_event
                    WHERE organization_id = :organization_id
                      AND worker_job_id = :worker_job_id
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "worker_job_id": scenario.job_id,
                },
            ).one()
            accepted_count = connection.execute(
                text(
                    """
                    SELECT count(*) FROM supply_connector_accepted_page
                    WHERE organization_id = :organization_id
                      AND worker_job_id = :worker_job_id
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "worker_job_id": scenario.job_id,
                },
            ).scalar_one()
    finally:
        migration_engine.dispose()
    assert tuple(event) == (
        "operator_preempted",
        old_claims.lease_generation,
        preempted_claims.lease_generation,
        PREEMPTION_REASON_DIGEST,
    )
    assert accepted_count == 0


def test_security_definer_issue_function_refuses_wrong_caller_role(
    scenarios: list[_Scenario],
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _seed_scenario(migration_configuration, guarded_control_engine)
    scenarios.append(scenario)

    with (
        pytest.raises(ProgrammingError, match="permission denied for function"),
        guarded_worker_engine.begin() as connection,
    ):
        connection.execute(
            text(
                """
                    SELECT * FROM context_supply_issue_connector_lease(
                        :org, :source, :version, :job, :principal,
                        1, :nonce, 60
                    )
                    """
            ),
            {
                "org": scenario.organization_id,
                "source": scenario.source_id,
                "version": scenario.source_version_id,
                "job": scenario.job_id,
                "principal": scenario.service_principal_id,
                "nonce": generate_worker_lease_nonce(),
            },
        ).all()

    with (
        pytest.raises(ProgrammingError, match="permission denied for function"),
        guarded_worker_engine.begin() as connection,
    ):
        connection.execute(
            text(
                """
                SELECT * FROM context_supply_preempt_connector_lease(
                    :org, :source, :version, :job, :principal,
                    1, :nonce, 60, :reason_digest
                )
                """
            ),
            {
                "org": scenario.organization_id,
                "source": scenario.source_id,
                "version": scenario.source_version_id,
                "job": scenario.job_id,
                "principal": scenario.service_principal_id,
                "nonce": generate_worker_lease_nonce(),
                "reason_digest": PREEMPTION_REASON_DIGEST,
            },
        ).all()


def test_security_definer_worker_function_refuses_mutated_actor_context_claim(
    scenarios: list[_Scenario],
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _seed_scenario(migration_configuration, guarded_control_engine)
    scenarios.append(scenario)
    claims = _claims(scenario)

    with guarded_worker_engine.begin() as connection:
        row = connection.execute(
            text(
                """
                SELECT * FROM context_supply_load_connector_checkpoint(
                    :organization_id, :source_version_id, :worker_job_id,
                    :service_principal_id, :lease_generation,
                    :signing_key_version, :nonce, :issued_at, :expires_at,
                    :policy_epoch, :idempotency_key,
                    :allowed_source_version_ids, :allowed_operations,
                    :service_actor_expires_at
                )
                """
            ),
            {
                "organization_id": scenario.organization_id,
                "source_version_id": scenario.source_version_id,
                "worker_job_id": scenario.job_id,
                "service_principal_id": scenario.service_principal_id,
                "lease_generation": claims.lease_generation,
                "signing_key_version": claims.signing_key_version,
                "nonce": claims.nonce,
                "issued_at": claims.issued_at,
                "expires_at": claims.expires_at,
                "policy_epoch": claims.policy_epoch,
                "idempotency_key": "f" * 64,
                "allowed_source_version_ids": [scenario.source_version_id],
                "allowed_operations": ["connector.execute"],
                "service_actor_expires_at": claims.service_actor_expires_at,
            },
        ).one_or_none()

    assert row is None


@pytest.mark.parametrize(
    ("function_name", "function_arguments"),
    [
        (
            "context_supply_redeem_connector_lease",
            "",
        ),
        (
            "context_supply_load_staged_connector_page",
            ":page_ref, ",
        ),
        (
            "context_supply_accept_connector_page",
            ":page_ref, :page_payload, ",
        ),
    ],
)
def test_each_worker_security_definer_refuses_mutated_actor_context(
    scenarios: list[_Scenario],
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    function_name: str,
    function_arguments: str,
) -> None:
    scenario = _seed_scenario(migration_configuration, guarded_control_engine)
    scenarios.append(scenario)
    claims = _claims(scenario)
    page = _page(scenario, 1, terminal=False)
    statement = text(
        "SELECT * FROM "
        + function_name
        + "(:organization_id, :source_version_id, :worker_job_id, "
        ":service_principal_id, "
        + function_arguments
        + ":lease_generation, :signing_key_version, :nonce, "
        ":issued_at, :expires_at, :policy_epoch, :idempotency_key, "
        ":allowed_source_version_ids, :allowed_operations, "
        ":service_actor_expires_at)"
    )
    parameters = {
        "organization_id": scenario.organization_id,
        "source_version_id": scenario.source_version_id,
        "worker_job_id": scenario.job_id,
        "service_principal_id": scenario.service_principal_id,
        "page_ref": page.page_ref,
        "page_payload": serialize_supply_change_page(page),
        "lease_generation": claims.lease_generation,
        "signing_key_version": claims.signing_key_version,
        "nonce": claims.nonce,
        "issued_at": claims.issued_at,
        "expires_at": claims.expires_at,
        "policy_epoch": claims.policy_epoch,
        "idempotency_key": "f" * 64,
        "allowed_source_version_ids": [scenario.source_version_id],
        "allowed_operations": ["connector.execute"],
        "service_actor_expires_at": claims.service_actor_expires_at,
    }

    with guarded_worker_engine.begin() as connection:
        assert connection.execute(statement, parameters).one_or_none() is None


def test_worker_actor_context_is_transaction_local_and_pool_checkout_is_clean(
    scenarios: list[_Scenario],
    migration_configuration: DatabaseConfiguration,
    worker_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
) -> None:
    scenario = _seed_scenario(migration_configuration, guarded_control_engine)
    scenarios.append(scenario)
    claims = _claims(scenario)
    page = _page(scenario, 1, terminal=False)
    engine = create_database_engine(worker_configuration, pool_size=1, max_overflow=0)
    setting_names = (
        "app.organization_id",
        "app.worker_job_id",
        "app.actor_kind",
        "app.service_principal_id",
        "app.workload",
        "app.worker_audience",
        "app.operation",
        "app.allowed_source_version_ids",
        "app.allowed_operations",
        "app.policy_epoch",
        "app.service_actor_expires_at",
        "app.worker_lease_idempotency_key",
    )
    try:
        assert (
            PostgreSQLConnectorCheckpointStore(engine).redeem_for_execution(
                page.binding,
                lease_claims=claims,
            )
            is None
        )
        with engine.connect() as connection:
            transaction = connection.begin()
            PostgreSQLStagedArtifactSink(engine).accept_change_page(
                connection,
                page,
                serialize_supply_change_page(page),
                lease_claims=claims,
            )
            backend_pid = connection.execute(
                text("SELECT pg_catalog.pg_backend_pid()")
            ).scalar_one()
            values = tuple(
                connection.execute(
                    text("SELECT current_setting(:name, true)"),
                    {"name": name},
                ).scalar_one()
                for name in setting_names
            )
            expected_service_actor_expiry = connection.execute(
                text("SELECT CAST(:value AS timestamptz)::text"),
                {"value": claims.service_actor_expires_at},
            ).scalar_one()
            assert values == (
                str(scenario.organization_id),
                str(scenario.job_id),
                "service",
                str(scenario.service_principal_id),
                "supply.connector",
                "context-engine-connector-runner",
                "connector.execute",
                str(scenario.source_version_id),
                "connector.execute",
                str(claims.policy_epoch),
                expected_service_actor_expiry,
                claims.idempotency_key,
            )
            transaction.rollback()
            assert (
                connection.execute(
                    text("SELECT pg_catalog.pg_backend_pid()")
                ).scalar_one()
                == backend_pid
            )
            assert tuple(
                connection.execute(
                    text("SELECT current_setting(:name, true)"),
                    {"name": name},
                ).scalar_one()
                for name in setting_names
            ) == ("",) * len(setting_names)
    finally:
        engine.dispose()
