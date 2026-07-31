from __future__ import annotations

import pytest
from sqlalchemy import Engine, text

from adapters.connectors.feishu import FeishuAclVisibility
from engine.persistence import DatabaseConfiguration, create_database_engine
from tests.support.feishu_integration import (
    accept_feishu_page,
    cleanup_feishu_scenario,
    next_observation_time,
)

pytestmark = pytest.mark.integration


def test_feishu_observation_and_policy_epoch_roll_back_together(
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    accepted = accept_feishu_page(
        migration_configuration=migration_configuration,
        guarded_control_engine=guarded_control_engine,
        guarded_worker_engine=guarded_worker_engine,
        observed_at=next_observation_time(),
        document_ref="document:atomic",
        visibility=FeishuAclVisibility.ORGANIZATION,
    )
    engine = create_database_engine(migration_configuration)
    try:
        with (
            pytest.raises(RuntimeError, match="roll back after authority"),
            guarded_control_engine.begin() as connection,
        ):
            connection.execute(
                text("SELECT set_config('app.organization_id', :org, true)"),
                {"org": str(accepted.scenario.organization_id)},
            )
            row = connection.execute(
                text(
                    """
                        SELECT * FROM context_control_apply_feishu_acl_observation(
                            :org, :version, :job, :page, :document, false
                        )
                        """
                ),
                {
                    "org": accepted.scenario.organization_id,
                    "version": accepted.scenario.source_version_id,
                    "job": accepted.scenario.job_id,
                    "page": accepted.page_ref,
                    "document": accepted.document_ref,
                },
            ).one()
            assert row.policy_epoch == 2
            raise RuntimeError("roll back after authority")
        with engine.connect() as connection:
            observation_count = connection.execute(
                text(
                    "SELECT count(*) FROM article_source_acl_observation "
                    "WHERE organization_id = :org AND resource_ref = :resource"
                ),
                {
                    "org": accepted.scenario.organization_id,
                    "resource": accepted.document_ref,
                },
            ).scalar_one()
            epoch = connection.execute(
                text(
                    "SELECT policy_epoch FROM organization_policy_epoch "
                    "WHERE organization_id = :org"
                ),
                {"org": accepted.scenario.organization_id},
            ).scalar_one()
        assert observation_count == 0
        assert epoch == 1
    finally:
        engine.dispose()
        cleanup_feishu_scenario(
            migration_configuration,
            accepted.scenario.organization_id,
        )
