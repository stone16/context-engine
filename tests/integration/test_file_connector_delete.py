from __future__ import annotations

import json

import pytest
from sqlalchemy import Engine

from adapters.connectors.file import FileConnectorAdapter
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLConnectorCheckpointStore,
    PostgreSQLStagedArtifactSink,
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


def test_deleted_file_emits_durable_delete_observation(
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
        snapshots=(
            {"alpha.md": b"# Alpha\n"},
            {},
            {},
        ),
        policy_epoch=claims.policy_epoch,
    )
    adapter = FileConnectorAdapter.from_twin(twin)
    sink = PostgreSQLStagedArtifactSink(guarded_worker_engine)

    result = _bridge(
        scenario,
        guarded_worker_engine,
        PostgreSQLConnectorCheckpointStore(guarded_worker_engine),
    ).execute(scenario.execution, adapter)

    assert len(result.accepted_page_refs) == 3
    deleted = sink.load(
        scenario.execution.binding,
        result.accepted_page_refs[1],
        lease_claims=claims,
    )
    assert deleted is not None
    payload = json.loads(deleted.payload)
    assert payload["documents"] == []
    assert len(payload["deleted_document_refs"]) == 1
    assert payload["deleted_document_refs"][0]["acl_observation"][
        "evidence_class"
    ] == "weak"
    assert payload["terminal"] is False
