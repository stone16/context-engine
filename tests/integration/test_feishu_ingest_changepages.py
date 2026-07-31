from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine

from adapters.connectors.feishu import (
    FEISHU_DOCS_CAPABILITY_MANIFEST_JSON,
    FeishuAclResponse,
    FeishuAclVisibility,
    FeishuChangePage,
    FeishuConnectorProcessAdapter,
    FeishuDocument,
    FeishuGroupSnapshot,
    serialize_feishu_twin_fixture,
)
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLConnectorCheckpointStore,
)
from tests.integration.test_connector_checkpoint_store import (
    _bridge,
    _claims,
    _seed_scenario,
)
from tests.support.feishu_integration import cleanup_feishu_scenario

pytestmark = pytest.mark.integration


def test_feishu_ingest_changepages_uses_runner_seam_and_durable_checkpoint(
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _seed_scenario(
        migration_configuration,
        guarded_control_engine,
        source_kind="feishu_docs",
        capability_manifest=FEISHU_DOCS_CAPABILITY_MANIFEST_JSON,
    )
    observed_at = datetime.now(UTC).replace(microsecond=0)
    first = FeishuDocument("document:first", "revision:1", b"# First\n")
    second = FeishuDocument("document:second", "revision:1", b"# Second\n")
    acl = {
        document.document_ref: FeishuAclResponse(
            document.document_ref,
            FeishuAclVisibility.ORGANIZATION,
            (),
            observed_at,
        )
        for document in (first, second)
    }
    pages = {
        None: FeishuChangePage((first,), (), "page:2", "checkpoint:1"),
        "page:2": FeishuChangePage((second,), (), None, "checkpoint:2"),
    }
    claims = _claims(scenario)
    assert claims.policy_epoch is not None
    assert claims.idempotency_key is not None
    assert claims.service_actor_expires_at is not None
    adapter = FeishuConnectorProcessAdapter(
        serialize_feishu_twin_fixture(
            pages=pages,
            acl_responses=acl,
            identity_mappings={},
            group_snapshot=FeishuGroupSnapshot("groups:v1", (), observed_at),
        ),
        policy_epoch=claims.policy_epoch,
        worker_lease=scenario.execution.worker_lease,
        service_principal_id=scenario.service_principal_id,
        idempotency_key=claims.idempotency_key,
        service_actor_expires_at=claims.service_actor_expires_at,
    )
    store = PostgreSQLConnectorCheckpointStore(guarded_worker_engine)
    try:
        result = _bridge(scenario, guarded_worker_engine, store).execute(
            scenario.execution,
            adapter,
        )
        assert len(result.accepted_page_refs) == 2
        checkpoint = store.load(
            scenario.execution.binding,
            lease_claims=_claims(scenario),
        )
        assert checkpoint == b'{"page_token":null,"version":1}'
    finally:
        cleanup_feishu_scenario(
            migration_configuration,
            scenario.organization_id,
        )
