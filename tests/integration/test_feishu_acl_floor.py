from __future__ import annotations

import pytest
from sqlalchemy import Engine, text

from adapters.connectors.feishu import (
    FeishuAclVisibility,
    FeishuGroupNode,
    FeishuGroupSnapshot,
    FeishuIdentityMapping,
    FeishuPermissionKind,
    FeishuPermissionSubject,
)
from engine.persistence import DatabaseConfiguration, create_database_engine
from engine.supply import (
    deserialize_supply_change_page,
    serialize_supply_change_page,
)
from tests.support.feishu_integration import (
    accept_feishu_page,
    apply_feishu_page,
    cleanup_feishu_scenario,
    next_observation_time,
    seed_feishu_article,
)

pytestmark = pytest.mark.integration


def test_feishu_acl_floor_never_widens_local_policy_and_does_narrow(
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    accepted = accept_feishu_page(
        migration_configuration=migration_configuration,
        guarded_control_engine=guarded_control_engine,
        guarded_worker_engine=guarded_worker_engine,
        observed_at=next_observation_time(),
        document_ref="document:acl-floor",
        visibility=FeishuAclVisibility.ORGANIZATION,
    )
    engine = create_database_engine(migration_configuration)
    try:
        seed_feishu_article(
            migration_configuration,
            accepted,
            local_policy_kind="private",
        )
        first = apply_feishu_page(guarded_control_engine, accepted)
        with engine.connect() as connection:
            broad_source = connection.execute(
                text(
                    "SELECT local_policy_kind, policy_kind FROM article_access_policy "
                    "WHERE organization_id = :org AND resource_ref = :resource"
                ),
                {
                    "org": accepted.scenario.organization_id,
                    "resource": accepted.document_ref,
                },
            ).one()
        assert first.published is True
        assert tuple(broad_source) == ("private", "private")

        narrower = accept_feishu_page(
            migration_configuration=migration_configuration,
            guarded_control_engine=guarded_control_engine,
            guarded_worker_engine=guarded_worker_engine,
            scenario=None,
            observed_at=next_observation_time(1),
            document_ref="document:narrower-source",
            visibility=None,
            acl_failed=True,
        )
        seed_feishu_article(migration_configuration, narrower)
        second = apply_feishu_page(guarded_control_engine, narrower)
        assert second.published is False
    finally:
        engine.dispose()
        cleanup_feishu_scenario(
            migration_configuration,
            accepted.scenario.organization_id,
        )
        if "narrower" in locals():
            cleanup_feishu_scenario(
                migration_configuration,
                narrower.scenario.organization_id,
            )


@pytest.mark.parametrize("subject_kind", ["identity", "group"])
def test_hostile_runner_cannot_forge_local_delivery_authority(
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    subject_kind: str,
) -> None:
    observed_at = next_observation_time()
    forged_local = f"local:forged-{subject_kind}"
    authoritative_local = f"local:authoritative-{subject_kind}"
    external_ref = f"external:{subject_kind}"
    accepted = accept_feishu_page(
        migration_configuration=migration_configuration,
        guarded_control_engine=guarded_control_engine,
        guarded_worker_engine=guarded_worker_engine,
        observed_at=observed_at,
        document_ref=f"document:hostile-{subject_kind}",
        visibility=FeishuAclVisibility.PRIVATE,
        subjects=(
            FeishuPermissionSubject(
                (
                    FeishuPermissionKind.USER
                    if subject_kind == "identity"
                    else FeishuPermissionKind.GROUP
                ),
                external_ref,
            ),
        ),
        identity_mappings=(
            {
                external_ref: FeishuIdentityMapping(
                    external_ref,
                    forged_local,
                )
            }
            if subject_kind == "identity"
            else None
        ),
        group_snapshot=(
            FeishuGroupSnapshot(
                "groups:hostile",
                (FeishuGroupNode(external_ref, forged_local),),
                observed_at,
            )
            if subject_kind == "group"
            else None
        ),
    )
    engine = create_database_engine(migration_configuration)
    try:
        seed_feishu_article(migration_configuration, accepted)
        with engine.begin() as connection:
            if subject_kind == "group":
                connection.execute(
                    text(
                        "INSERT INTO article_access_group "
                        "(organization_id, group_ref) VALUES (:org, :local)"
                    ),
                    {
                        "org": accepted.scenario.organization_id,
                        "local": authoritative_local,
                    },
                )
            connection.execute(
                text(
                    """
                    UPDATE feishu_subject_mapping
                    SET local_ref = :authoritative,
                        mapping_version = mapping_version + 1
                    WHERE organization_id = :org AND source_id = :source
                      AND subject_kind = :kind AND external_ref = :external
                    """
                ),
                {
                    "authoritative": authoritative_local,
                    "org": accepted.scenario.organization_id,
                    "source": accepted.scenario.source_id,
                    "kind": subject_kind,
                    "external": external_ref,
                },
            )
        applied = apply_feishu_page(guarded_control_engine, accepted)
        with engine.connect() as connection:
            policy = connection.execute(
                text(
                    "SELECT source_observation_status, published, policy_kind "
                    "FROM article_access_policy WHERE organization_id = :org "
                    "AND resource_ref = :resource"
                ),
                {
                    "org": accepted.scenario.organization_id,
                    "resource": accepted.document_ref,
                },
            ).one()
            forged_grants = connection.execute(
                text(
                    "SELECT count(*) FROM resource_access_policy "
                    "WHERE organization_id = :org AND resource_ref = :resource "
                    "AND principal_ref = :forged AND access_state = 'allowed'"
                ),
                {
                    "org": accepted.scenario.organization_id,
                    "resource": accepted.document_ref,
                    "forged": forged_local,
                },
            ).scalar_one()
        assert applied.published is False
        assert tuple(policy) == ("unresolved_group", False, None)
        assert forged_grants == 0
    finally:
        engine.dispose()
        cleanup_feishu_scenario(
            migration_configuration,
            accepted.scenario.organization_id,
        )


def test_hostile_runner_cannot_replay_acl_artifact_across_articles(
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    accepted = accept_feishu_page(
        migration_configuration=migration_configuration,
        guarded_control_engine=guarded_control_engine,
        guarded_worker_engine=guarded_worker_engine,
        observed_at=next_observation_time(),
        document_ref="document:artifact-source",
        visibility=FeishuAclVisibility.PRIVATE,
        identity_mappings={
            "external:reader": FeishuIdentityMapping(
                "external:reader",
                "principal:reader",
            )
        },
        subjects=(
            FeishuPermissionSubject(
                FeishuPermissionKind.USER,
                "external:reader",
            ),
        ),
    )
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            definition = connection.execute(
                text(
                    "SELECT pg_get_functiondef("
                    "'context_control_apply_feishu_acl_observation"
                    "(uuid,uuid,uuid,text,text,boolean)'::regprocedure)"
                )
            ).scalar_one()
            payload = connection.execute(
                text(
                    "SELECT page_payload FROM supply_connector_staged_page "
                    "WHERE organization_id = :org AND page_ref = :page"
                ),
                {
                    "org": accepted.scenario.organization_id,
                    "page": accepted.page_ref,
                },
            ).scalar_one()
        assert "artifact->>'document_ref'" in definition
        assert "IS DISTINCT FROM requested_document_ref" in definition
        source_page = deserialize_supply_change_page(payload)
        target_ref = "document:artifact-target"
        payload_document = source_page.documents[0]
        replayed_payload = serialize_supply_change_page(
            type(source_page)(
                binding=source_page.binding,
                page_ref=source_page.page_ref,
                documents=(
                    type(payload_document)(
                        organization_id=payload_document.organization_id,
                        source_version_id=payload_document.source_version_id,
                        worker_job_id=payload_document.worker_job_id,
                        document_ref=target_ref,
                        content=payload_document.content,
                        content_type=payload_document.content_type,
                        acl_observation=payload_document.acl_observation,
                        metadata=payload_document.metadata,
                    ),
                ),
                deleted_document_refs=source_page.deleted_document_refs,
                checkpoint_proposal=source_page.checkpoint_proposal,
                terminal=source_page.terminal,
            )
        )
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE supply_connector_staged_page "
                    "SET page_payload = :payload, "
                    "payload_digest = digest(:payload, 'sha256') "
                    "WHERE organization_id = :org AND page_ref = :page"
                ),
                {
                    "org": accepted.scenario.organization_id,
                    "page": accepted.page_ref,
                    "payload": replayed_payload,
                },
            )
        target = type(accepted)(
            scenario=accepted.scenario,
            page_ref=accepted.page_ref,
            document_ref=target_ref,
            delete_observation=False,
        )
        seed_feishu_article(migration_configuration, target)
        applied = apply_feishu_page(guarded_control_engine, target)
        with engine.connect() as connection:
            policy = connection.execute(
                text(
                    "SELECT source_observation_status, published, policy_kind "
                    "FROM article_access_policy WHERE organization_id = :org "
                    "AND resource_ref = :resource"
                ),
                {"org": target.scenario.organization_id, "resource": target_ref},
            ).one()
            forged_grants = connection.execute(
                text(
                    "SELECT count(*) FROM resource_access_policy "
                    "WHERE organization_id = :org AND resource_ref = :resource "
                    "AND access_state = 'allowed'"
                ),
                {"org": target.scenario.organization_id, "resource": target_ref},
            ).scalar_one()
        assert applied.published is False
        assert tuple(policy) == ("unresolved_group", False, None)
        assert forged_grants == 0
    finally:
        engine.dispose()
        cleanup_feishu_scenario(
            migration_configuration,
            accepted.scenario.organization_id,
        )
