from __future__ import annotations

import json

import pytest
from sqlalchemy import Engine

from adapters.connectors.file import FileConnectorAdapter, decode_file_checkpoint
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLConnectorCheckpointStore,
    PostgreSQLStagedArtifactSink,
)
from engine.supply import ConnectorCheckpointBinding
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


def test_first_scan_ingests_and_unchanged_second_scan_is_empty(
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
        {"alpha.md": b"# Alpha\n"},
        policy_epoch=claims.policy_epoch,
    )
    adapter = FileConnectorAdapter.from_twin(twin)
    store = PostgreSQLConnectorCheckpointStore(guarded_worker_engine)
    sink = PostgreSQLStagedArtifactSink(guarded_worker_engine)

    result = _bridge(scenario, guarded_worker_engine, store).execute(
        scenario.execution,
        adapter,
    )

    assert len(result.accepted_page_refs) == 2
    first = sink.load(
        scenario.execution.binding,
        result.accepted_page_refs[0],
        lease_claims=claims,
    )
    second = sink.load(
        scenario.execution.binding,
        result.accepted_page_refs[1],
        lease_claims=claims,
    )
    assert first is not None and second is not None
    assert len(json.loads(first.payload)["documents"]) == 1
    assert json.loads(first.payload)["terminal"] is False
    assert json.loads(second.payload)["documents"] == []
    assert json.loads(second.payload)["deleted_document_refs"] == []
    assert json.loads(second.payload)["terminal"] is True
    checkpoint = store.load(scenario.execution.binding, lease_claims=claims)
    assert checkpoint is not None
    assert decode_file_checkpoint(checkpoint).paths == ("alpha.md",)


def test_changed_file_emits_exactly_that_change() -> None:
    twin = SyntheticVaultTwin(
        {
            "alpha.md": b"# Alpha v1\n",
            "bravo.md": b"# Bravo\n",
        }
    )
    adapter = FileConnectorAdapter.from_twin(twin)
    binding = scenario_binding(twin)
    adapter.load_checkpoint(None)
    initial = adapter.load(binding)
    twin.replace("alpha.md", b"# Alpha v2\n")
    adapter.load_checkpoint(initial.checkpoint_proposal)

    changed = adapter.poll(binding)

    assert len(changed.documents) == 1
    assert changed.documents[0].content == b"# Alpha v2\n"
    assert dict(changed.documents[0].metadata)["path"] == "alpha.md"


def scenario_binding(twin: SyntheticVaultTwin) -> ConnectorCheckpointBinding:
    return ConnectorCheckpointBinding(
        twin.organization_id,
        twin.source_version_id,
        twin.worker_job_id,
    )
