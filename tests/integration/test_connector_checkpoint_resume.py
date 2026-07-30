from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import Connection, Engine

from adapters.connectors.file import FileConnectorAdapter
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLConnectorCheckpointStore,
    PostgreSQLStagedArtifactSink,
    PostgreSQLSupplyBridgeLeaseIssuer,
    SupplyBridgeLeasePreemptionRequest,
)
from engine.supply import (
    ConnectorCheckpointBinding,
    StagedArtifact,
    StagedArtifactSink,
    SupplyChangePage,
    WorkerLeaseClaims,
)
from tests.integration.test_connector_checkpoint_store import (
    _bridge,
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


class _FailSecondAcceptance(StagedArtifactSink):
    def __init__(self, inner: StagedArtifactSink) -> None:
        self._inner = inner
        self._calls = 0

    def accept_change_page(
        self,
        connection: Connection,
        page: SupplyChangePage,
        *,
        lease_claims: WorkerLeaseClaims,
    ) -> None:
        self._calls += 1
        if self._calls == 2:
            raise RuntimeError("injected failure before atomic acceptance")
        self._inner.accept_change_page(connection, page, lease_claims=lease_claims)

    def load(
        self,
        binding: ConnectorCheckpointBinding,
        artifact_ref: str,
        *,
        lease_claims: WorkerLeaseClaims,
    ) -> StagedArtifact | None:
        return self._inner.load(binding, artifact_ref, lease_claims=lease_claims)


def test_unaccepted_page_keeps_prior_checkpoint_and_resume_reemits_once(
    scenarios: list[_Scenario],
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _seed_scenario(migration_configuration, guarded_control_engine)
    scenarios.append(scenario)
    claims = _claims(scenario)
    assert claims.policy_epoch is not None
    twin = SyntheticVaultTwin(
        {"alpha.md": b"# Alpha v1\n"},
        snapshots=(
            {"alpha.md": b"# Alpha v1\n"},
            {"alpha.md": b"# Alpha v2\n"},
            {"alpha.md": b"# Alpha v2\n"},
            {"alpha.md": b"# Alpha v2\n"},
        ),
        policy_epoch=claims.policy_epoch,
    )
    adapter = FileConnectorAdapter.from_twin(twin)
    store = PostgreSQLConnectorCheckpointStore(guarded_worker_engine)
    sink = PostgreSQLStagedArtifactSink(guarded_worker_engine)

    with pytest.raises(RuntimeError, match="injected failure"):
        _bridge(
            scenario,
            guarded_worker_engine,
            store,
            _FailSecondAcceptance(sink),
        ).execute(scenario.execution, adapter)

    prior = adapter.emitted_pages[0].checkpoint_proposal
    failed_page_ref = adapter.emitted_pages[1].page_ref
    assert store.load(scenario.execution.binding, lease_claims=claims) == prior

    resumed_token = PostgreSQLSupplyBridgeLeaseIssuer(
        guarded_control_engine,
        scenario.codec,
    ).preempt(
        SupplyBridgeLeasePreemptionRequest(
            organization_id=scenario.organization_id,
            source_id=scenario.source_id,
            source_version_id=scenario.source_version_id,
            worker_job_id=scenario.job_id,
            service_principal_id=scenario.service_principal_id,
            reason_digest="c" * 64,
        )
    )
    resumed_scenario = replace(
        scenario,
        execution=replace(scenario.execution, worker_lease=resumed_token),
    )
    resumed = FileConnectorAdapter.from_twin(twin)
    result = _bridge(resumed_scenario, guarded_worker_engine, store, sink).execute(
        resumed_scenario.execution,
        resumed,
    )

    assert result.accepted_page_refs[0] == failed_page_ref
    assert sum(page.page_ref == failed_page_ref for page in resumed.emitted_pages) == 1
    assert len(result.accepted_page_refs) == 2
