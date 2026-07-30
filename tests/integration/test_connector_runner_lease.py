from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import Engine

from adapters.connectors.file import FileConnectorAdapter, FileConnectorProcessAdapter
from engine.control import FileRootRef
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLConnectorCheckpointStore,
    PostgreSQLStagedArtifactSink,
    PostgreSQLSupplyExecutionBridge,
    SupplyBridgeExecutionIdentity,
)
from engine.supply import SupplyBridgeExecution, WorkNotAvailable
from tests.integration.test_connector_checkpoint_store import (
    _claims,
    _Scenario,
    _seed_scenario,
)
from tests.integration.test_connector_checkpoint_store import (
    scenarios as _checkpoint_scenarios,
)
from tests.support.file_connector_twin import SyntheticVaultTwin

pytestmark = pytest.mark.integration
scenarios = _checkpoint_scenarios


def _bridge(
    scenario: _Scenario,
    engine: Engine,
    *,
    checked_at: datetime,
) -> PostgreSQLSupplyExecutionBridge:
    claims = _claims(scenario)
    assert claims.policy_epoch is not None
    assert claims.idempotency_key is not None
    assert claims.service_actor_expires_at is not None
    return PostgreSQLSupplyExecutionBridge(
        engine,
        scenario.codec,
        SupplyBridgeExecutionIdentity(
            organization_id=scenario.organization_id,
            service_principal_id=scenario.service_principal_id,
            allowed_source_version_ids=(scenario.source_version_id,),
            allowed_operations=("connector.execute",),
            policy_epoch=claims.policy_epoch,
            idempotency_key=claims.idempotency_key,
            expires_at=claims.service_actor_expires_at,
        ),
        PostgreSQLConnectorCheckpointStore(engine),
        PostgreSQLStagedArtifactSink(engine),
        clock=lambda: checked_at,
    )


def test_file_runner_refuses_absent_expired_or_wrong_job_lease_before_scan(
    scenarios: list[_Scenario],
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _seed_scenario(migration_configuration, guarded_control_engine)
    scenarios.append(scenario)
    claims = _claims(scenario)
    twin = SyntheticVaultTwin({"alpha.md": b"# Alpha\n"})
    adapter = FileConnectorAdapter.from_twin(twin)
    now = datetime.now(UTC).replace(microsecond=0)

    with pytest.raises(TypeError):
        _bridge(scenario, guarded_worker_engine, checked_at=now).execute(
            None,  # type: ignore[arg-type]
            adapter,
        )
    with pytest.raises(WorkNotAvailable, match="^work not available$"):
        _bridge(scenario, guarded_worker_engine, checked_at=now).execute(
            replace(scenario.execution, worker_job_id=uuid4()),
            adapter,
        )
    with pytest.raises(WorkNotAvailable, match="^work not available$"):
        _bridge(
            scenario,
            guarded_worker_engine,
            checked_at=claims.expires_at + timedelta(seconds=1),
        ).execute(scenario.execution, adapter)

    assert twin.snapshot_calls == 0
    assert type(scenario.execution) is SupplyBridgeExecution


def test_valid_exact_lease_executes_file_scan_in_independent_process(
    tmp_path: Path,
    scenarios: list[_Scenario],
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _seed_scenario(migration_configuration, guarded_control_engine)
    scenarios.append(scenario)
    claims = _claims(scenario)
    assert claims.policy_epoch is not None
    vault = tmp_path / "connector-vault"
    vault.mkdir()
    (vault / "alpha.md").write_bytes(b"# Alpha\n")
    adapter = FileConnectorProcessAdapter(
        FileRootRef("synthetic-root"),
        vault,
        policy_epoch=claims.policy_epoch,
        worker_lease=scenario.execution.worker_lease,
        service_principal_id=scenario.service_principal_id,
        idempotency_key=claims.idempotency_key or "",
        service_actor_expires_at=(claims.service_actor_expires_at or claims.expires_at),
    )

    result = _bridge(
        scenario,
        guarded_worker_engine,
        checked_at=datetime.now(UTC).replace(microsecond=0),
    ).execute(scenario.execution, adapter)

    assert len(result.accepted_page_refs) == 2
