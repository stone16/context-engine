from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import Engine, text

from engine.article_access_policy import (
    ArticleAccessPolicyKind,
    ArticleAccessPolicySetting,
)
from engine.control import (
    ContextControl,
    ControlOperation,
    ControlOperatorAuthority,
    SetSourceArticlePolicyDefault,
)
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLControlStore,
    create_database_engine,
)
from tests.support.article_access_policy import (
    article_policy,
    delete_article_policy_scenario,
    ingest_article,
    insert_organization,
    observe_source_acl,
    set_source_default,
    set_tenant_default,
    unique_article_refs,
)
from tests.support.file_imports import (
    NOW,
    ControlAuthenticator,
    delete_file_import_scenario,
    prepare_file_import_scenario,
    run_file_import,
)

pytestmark = pytest.mark.integration


def test_operation_bound_control_default_change_affects_only_later_file_articles(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    scenario = prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
    )
    authority = ControlOperatorAuthority(
        ControlAuthenticator(scenario.organization_id),
        call_ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    )
    store = PostgreSQLControlStore(guarded_control_engine, clock=lambda: NOW)
    control = ContextControl(
        store=store,
        article_policy_store=store,
        authority=authority,
        clock=lambda: NOW,
    )
    assert scenario.token is not None
    first = run_file_import(
        scenario,
        scenario.prepared,
        scenario.token,
        guarded_worker_engine,
    )
    try:
        with authority.authorize(
            opaque_credential="control-secret",
            operation=ControlOperation.SET_SOURCE_ARTICLE_POLICY_DEFAULT,
            request_id="change-source-default",
        ) as call:
            version = control.set_source_article_policy_default(
                call,
                SetSourceArticlePolicyDefault(
                    source_ref=str(scenario.source_ref.value),
                    expected_version=1,
                    setting=ArticleAccessPolicySetting(
                        ArticleAccessPolicyKind.ORGANIZATION
                    ),
                ),
            )
        assert version == 2
        later_resource = f"resource:later-control-default:{uuid4()}"
        migration_engine = create_database_engine(migration_configuration)
        try:
            observe_source_acl(
                migration_engine,
                organization_id=scenario.organization_id,
                source_ref=str(scenario.source_ref.value),
                resource_ref=later_resource,
            )
            ingest_article(
                migration_engine,
                organization_id=scenario.organization_id,
                source_ref=str(scenario.source_ref.value),
                resource_ref=later_resource,
            )
            assert article_policy(
                migration_engine,
                scenario.organization_id,
                first.candidate_refs[0].resource_ref,
            )[:2] == ("private", 1)
            assert article_policy(
                migration_engine,
                scenario.organization_id,
                later_resource,
            )[:2] == ("organization", 1)
        finally:
            migration_engine.dispose()
    finally:
        delete_file_import_scenario(
            migration_configuration,
            scenario.organization_id,
        )


def test_real_file_first_publication_fixes_private_mirrored_article_policy(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    scenario = prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
    )
    assert scenario.token is not None
    result = run_file_import(
        scenario,
        scenario.prepared,
        scenario.token,
        guarded_worker_engine,
    )
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            policy = connection.execute(
                text(
                    """
                    SELECT policy_kind, local_policy_kind, policy_version,
                           resolution_rung, source_evidence_mode,
                           source_observation_status
                    FROM article_access_policy
                    WHERE organization_id = :organization_id
                      AND resource_ref = :resource_ref
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "resource_ref": result.candidate_refs[0].resource_ref,
                },
            ).one()
        assert tuple(policy) == (
            "private",
            "private",
            1,
            "source_default",
            "mirrored",
            "resolved",
        )
    finally:
        engine.dispose()
        delete_file_import_scenario(
            migration_configuration,
            scenario.organization_id,
        )


def test_source_default_change_affects_only_articles_first_ingested_later(
    migration_configuration: DatabaseConfiguration,
) -> None:
    engine = create_database_engine(migration_configuration)
    organization_id = uuid4()
    source_ref, first_resource_ref = unique_article_refs("source-fixation")
    second_resource_ref = f"resource:source-fixation:{uuid4()}"
    try:
        insert_organization(engine, organization_id)
        set_source_default(engine, organization_id, source_ref, "private")
        for resource_ref in (first_resource_ref, second_resource_ref):
            observe_source_acl(
                engine,
                organization_id=organization_id,
                source_ref=source_ref,
                resource_ref=resource_ref,
            )
        ingest_article(
            engine,
            organization_id=organization_id,
            source_ref=source_ref,
            resource_ref=first_resource_ref,
        )

        set_source_default(engine, organization_id, source_ref, "organization")
        ingest_article(
            engine,
            organization_id=organization_id,
            source_ref=source_ref,
            resource_ref=second_resource_ref,
        )

        assert article_policy(engine, organization_id, first_resource_ref) == (
            "private",
            1,
            "source_default",
        )
        assert article_policy(engine, organization_id, second_resource_ref) == (
            "organization",
            1,
            "source_default",
        )
    finally:
        engine.dispose()
        delete_article_policy_scenario(migration_configuration, organization_id)


def test_tenant_default_change_affects_only_articles_first_ingested_later(
    migration_configuration: DatabaseConfiguration,
) -> None:
    engine = create_database_engine(migration_configuration)
    organization_id = uuid4()
    source_ref, first_resource_ref = unique_article_refs("tenant-fixation")
    second_resource_ref = f"resource:tenant-fixation:{uuid4()}"
    try:
        insert_organization(engine, organization_id)
        set_tenant_default(engine, organization_id, "private")
        for resource_ref in (first_resource_ref, second_resource_ref):
            observe_source_acl(
                engine,
                organization_id=organization_id,
                source_ref=source_ref,
                resource_ref=resource_ref,
            )
        ingest_article(
            engine,
            organization_id=organization_id,
            source_ref=source_ref,
            resource_ref=first_resource_ref,
        )

        set_tenant_default(engine, organization_id, "organization")
        ingest_article(
            engine,
            organization_id=organization_id,
            source_ref=source_ref,
            resource_ref=second_resource_ref,
        )

        assert article_policy(engine, organization_id, first_resource_ref) == (
            "private",
            1,
            "tenant_default",
        )
        assert article_policy(engine, organization_id, second_resource_ref) == (
            "organization",
            1,
            "tenant_default",
        )
    finally:
        engine.dispose()
        delete_article_policy_scenario(migration_configuration, organization_id)
