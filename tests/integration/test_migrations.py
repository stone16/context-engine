from __future__ import annotations

import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from adapters.connectors.feishu import FEISHU_DOCS_CAPABILITY_MANIFEST_JSON
from adapters.embeddings import DeterministicEmbeddingTwin
from adapters.file_source import FileChangeProvider, FileReadLimits, FileRootRegistry
from adapters.parsers.markdown import compile_markdown
from engine.control import (
    FILE_CAPABILITY_MANIFEST,
    ActivateFileChangeFeed,
    ActivateFileDeleteObservations,
    ChangeLimit,
    ContextControl,
    ControlOperation,
    ControlOperatorAuthority,
    FileChangeBaseline,
    FileChangeBaselineEntry,
    FileChangeBaselineRef,
    FileChangeControlProofs,
    FileChangeProviderProofs,
    FileChangeSource,
    FileImportAudience,
    FileImportPath,
    FileImportReceiver,
    FileRootRef,
    InitialScan,
    ProviderOk,
    RegisterFileSource,
    ScheduledFileChangePage,
    ScheduleFileChangePage,
    SourceNotAvailable,
)
from engine.persistence import (
    DatabaseConfiguration,
    HarnessDatabaseConfigurations,
    PostgreSQLControlStore,
    PostgreSQLWorkerLeaseIssuer,
    assert_control_role,
    assert_runtime_role,
    assert_worker_role,
    create_database_engine,
)
from engine.persistence.membership_context import (
    MembershipIdentity,
    PostgreSQLMembershipAuthority,
)
from engine.runtime.citation import (
    CitationOpenIssue,
    CitationOpenProfile,
    issue_citation_open_ref,
)
from engine.supply import (
    QWEN3_EMBEDDING_PROFILE,
    MarkdownCompilerConfig,
    ParsedDocument,
    canonicalize_parsed_document,
)
from tests.integration.test_context_run_schema import (
    LineageIdentity,
    insert_context_run,
)
from tests.integration.test_zz_file_resource_tombstone import _tombstone
from tests.integration.test_zz_file_revision_replacement import NEW_MARKDOWN
from tests.integration.test_zz_file_source_offboarding import _offboard
from tests.support.file_imports import (
    NOW,
)
from tests.support.file_imports import (
    ControlAuthenticator as _ControlAuthenticator,
)
from tests.support.file_imports import (
    delete_file_import_scenario as _delete_file_import_scenario,
)
from tests.support.file_imports import (
    prepare_file_import_scenario as _prepare_file_import_scenario,
)
from tests.support.file_imports import (
    prepare_repeat_file_import as _prepare_repeat_file_import,
)
from tests.support.file_imports import (
    redeem_file_import_direct as _redeem_file_import_direct,
)
from tests.support.file_imports import (
    run_file_import as _run_file_import,
)
from tests.support.file_imports import (
    scenario_claims as _scenario_claims,
)
from tests.support.file_source_progress import clear_file_source_progress_projection
from tests.support.migrations import (
    HEAD_REVISION,
    downgrade_revision,
    isolated_revision_database,
)

pytestmark = pytest.mark.integration
ROOT = Path(__file__).parents[2]
HEAD_TABLES = [
    "action_delivery_attempt",
    "action_perform_audit",
    "action_prepare_audit",
    "action_provider_attempt",
    "action_receipt",
    "action_reconciliation",
    "action_ticket",
    "active_release_manifest",
    "alembic_version",
    "article_access_group",
    "article_access_group_membership",
    "article_access_policy",
    "article_explicit_policy_setting",
    "article_source_acl_observation",
    "bulk_article_policy_change_audit",
    "citation_open_locator",
    "context_feedback",
    "context_fragment",
    "context_fragment_field",
    "context_resource",
    "context_revision",
    "context_run",
    "context_run_operator_read_ticket",
    "context_source",
    "decision_audit",
    "delivery_evidence",
    "egress_audit",
    "egress_grant",
    "exact_phrase_candidate",
    "feishu_subject_mapping",
    "file_acquisition",
    "file_acquisition_result",
    "file_delete_observation_execution",
    "file_import_job",
    "file_import_job_event",
    "file_publication_recovery",
    "file_resource_cleanup_intent",
    "file_resource_ingestion_guard",
    "file_revision_replacement_plan",
    "file_revision_snapshot",
    "file_revision_supersession",
    "file_source_acquisition_checkpoint",
    "file_source_change",
    "file_source_change_page",
    "file_source_cleanup_intent",
    "file_source_delete_observation_page",
    "file_source_publish_watermark",
    "membership",
    "membership_resource_field_right",
    "model_egress_audit",
    "organization",
    "organization_article_policy_default",
    "organization_policy_epoch",
    "organization_record",
    "private_delivery_audit",
    "release_candidate",
    "release_evaluation",
    "release_manifest",
    "release_operator_grant",
    "release_promotion_audit",
    "resource_access_policy",
    "revision_link_edge",
    "revision_publication_event",
    "service_principal",
    "source_article_policy_default",
    "source_version",
    "supply_connector_accepted_page",
    "supply_connector_checkpoint",
    "supply_connector_job",
    "supply_connector_lease_event",
    "supply_connector_staged_page",
    "user_account",
    "worker_noop_job",
]
ARTICLE_POLICY_TABLES = {
    "article_access_group",
    "article_access_group_membership",
    "article_access_policy",
    "article_explicit_policy_setting",
    "article_source_acl_observation",
    "bulk_article_policy_change_audit",
    "feishu_subject_mapping",
    "organization_article_policy_default",
    "source_article_policy_default",
}

MIGRATION_TEST_START_REVISIONS = {
    "test_empty_baseline_remains_a_reversible_historical_revision": "20260720_0001",
    "test_article_policy_revision_backfills_legacy_access_and_reapplies_cleanly": (
        "20260730_0042"
    ),
    "test_article_policy_revision_backfills_exact_file_acl_version_and_stales": (
        "20260730_0042"
    ),
    "test_article_policy_downgrade_restores_source_version_least_privilege": (
        "20260730_0042"
    ),
    "test_article_policy_downgrade_waits_for_source_authority_writer": (
        "20260730_0042"
    ),
    "test_organization_isolation_revision_downgrades_and_reapplies_cleanly": (
        "20260720_0002"
    ),
    "test_supply_execution_bridge_revision_downgrades_and_reapplies_cleanly": (
        "20260729_0041"
    ),
    "test_feishu_acl_revisions_downgrade_and_reapply_when_empty": (
        "20260731_0048"
    ),
    "test_file_scan_bound_acceptance_fails_closed_after_downgrade": (
        "20260730_0047"
    ),
    "test_file_scan_bound_revision_downgrades_and_reapplies_when_default_only": (
        "20260730_0047"
    ),
    "test_file_scan_bound_downgrade_observes_in_flight_refusal_report": (
        "20260730_0047"
    ),
    "test_membership_revision_downgrades_to_issue_8_and_reapplies_cleanly": (
        "20260721_0003"
    ),
    "test_content_schema_revision_downgrades_to_membership_and_reapplies_cleanly": (
        "20260721_0004"
    ),
    "test_policy_epoch_revision_downgrades_to_content_and_reapplies_cleanly": (
        "20260721_0005"
    ),
    "test_worker_lease_revision_downgrades_to_policy_epoch_and_reapplies_cleanly": (
        "20260722_0006"
    ),
    "test_decision_lineage_revision_downgrades_to_worker_lease_and_reapplies_cleanly": (
        "20260722_0007"
    ),
    (
        "test_field_projection_revision_downgrades_to_decision_lineage_"
        "and_reapplies_cleanly"
    ): (
        "20260722_0008"
    ),
    "test_file_source_revision_downgrades_to_learning_release_and_reapplies_cleanly": (
        "20260722_0010"
    ),
    "test_structural_markdown_revision_downgrades_and_reapplies_cleanly": (
        "20260723_0012"
    ),
    "test_file_noop_revision_downgrades_and_reapplies_cleanly": "20260724_0013",
    "test_file_replacement_revision_downgrades_and_reapplies_cleanly": (
        "20260723_0014"
    ),
    "test_file_recovery_revision_downgrades_and_reapplies_cleanly": "20260723_0015",
    "test_file_tombstone_revision_downgrades_and_reapplies_cleanly": "20260723_0016",
    "test_file_progress_revision_downgrades_and_reapplies_cleanly": "20260723_0017",
    "test_file_source_offboarding_revision_downgrades_and_reapplies_cleanly": (
        "20260723_0018"
    ),
    "test_file_change_feed_revision_downgrades_and_reapplies_cleanly": (
        "20260725_0028"
    ),
    "test_file_delete_observation_revision_owns_atomic_read_volatility": (
        "20260725_0030"
    ),
    "test_file_delete_execution_revision_downgrades_only_while_empty": (
        "20260725_0031"
    ),
    "test_mixed_file_upsert_scheduling_revision_downgrades_and_reapplies_empty": (
        "20260725_0032"
    ),
    "test_file_dispatch_revision_downgrades_and_reapplies_empty": "20260725_0033",
    "test_file_reclaim_revision_downgrades_and_reapplies_empty": "20260726_0034",
    "test_file_change_scheduling_revision_downgrades_and_reapplies_cleanly": (
        "20260725_0029"
    ),
    "test_recursive_file_path_revision_downgrades_and_reapplies_when_empty": (
        "20260726_0035"
    ),
    "test_fragment_embedding_revision_downgrades_and_reapplies_when_empty": (
        "20260726_0036"
    ),
    "test_direct_file_change_activation_revision_downgrades_and_reapplies": (
        "20260727_0037"
    ),
    "test_pending_file_schedule_projection_revision_downgrades_and_reapplies": (
        "20260727_0038"
    ),
    "test_file_source_status_revision_downgrades_and_reapplies_when_empty": (
        "20260727_0039"
    ),
    "test_revision_link_graph_revision_downgrades_and_reapplies_when_empty": (
        "20260731_0051"
    ),
    "test_embedding_profile_revision_downgrades_and_reapplies_cleanly": (
        "20260731_0052"
    ),
    "test_embedding_profile_upgrade_refuses_unproven_existing_vectors": (
        "20260731_0052"
    ),
    "test_embedding_profile_upgrade_serializes_with_release_promotion_lock_order": (
        "20260731_0052"
    ),
    "test_delivery_evidence_revision_downgrades_only_while_empty": "20260723_0019",
    "test_citation_open_revision_downgrades_only_while_empty": "20260724_0024",
    "test_model_egress_revision_downgrades_only_while_audit_is_empty": (
        "20260724_0025"
    ),
    "test_structural_snapshot_constraint_rejects_missing_json_bindings": (
        "20260723_0012"
    ),
    "test_in_flight_old_scheduler_fails_closed_when_downgrade_wins_fence": (
        "20260725_0032"
    ),
    "test_fragment_embedding_revision_preserves_retained_fragments": (
        "20260726_0036"
    ),
    "test_empty_content_downgrade_preserves_v2_context_run_history": (
        "20260722_0008"
    ),
}

MIGRATION_TEST_HEAD_PRECONDITIONS = {
    "test_article_policy_downgrade_rejects_every_deferred_admin_state",
    "test_article_policy_downgrade_refuses_state_that_would_reauthorize_content",
    "test_bulk_article_policy_revision_downgrades_and_reapplies_when_audit_empty",
    "test_file_reclaim_revision_refuses_retained_higher_generation",
    "test_mixed_file_upsert_downgrade_waits_for_in_flight_scheduler",
    "test_file_delete_observation_revision_refuses_accepted_baseline_downgrade",
    "test_file_delete_observation_revision_refuses_retained_action_ticket",
    "test_file_change_scheduling_revision_refuses_downgrade_with_new_manual_path",
    "test_file_source_status_downgrade_observes_in_flight_compilation_refusal",
    "test_recursive_file_path_revision_refuses_retained_nested_lineage",
    "test_file_change_scheduling_downgrade_serializes_with_manual_import",
    "test_citation_open_revision_refuses_downgrade_with_retained_lineage",
    "test_file_source_offboarding_refuses_downgrade_with_committed_intent",
    "test_file_progress_refuses_downgrade_and_preserves_refs",
    "test_file_tombstone_revision_refuses_downgrade_with_committed_intent",
    "test_recovery_upgrade_adopts_an_existing_ready_replacement",
    "test_openapi_v0_revision_refuses_downgrade_with_v3_context_run_history",
    "test_field_projection_downgrade_refuses_populated_content_atomically",
    "test_field_projection_downgrade_serializes_with_concurrent_fragment_insert",
    "test_feishu_subject_mapping_downgrade_serializes_with_source_writer",
    "test_feishu_subject_mapping_revision_refuses_retained_authority",
    "test_revision_link_graph_downgrade_refuses_retained_v3_state",
    "test_embedding_profile_downgrade_refuses_retained_qwen_lineage",
}


@pytest.fixture
def migration_test_database(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[HarnessDatabaseConfigurations]:
    test_name = request.node.name.partition("[")[0]
    if test_name in MIGRATION_TEST_START_REVISIONS:
        revision = MIGRATION_TEST_START_REVISIONS[test_name]
    elif test_name in MIGRATION_TEST_HEAD_PRECONDITIONS:
        revision = HEAD_REVISION
    else:
        raise AssertionError(
            f"migration test has no revision precondition: {test_name}"
        )
    with isolated_revision_database(revision) as configurations:
        for configuration in (
            configurations.migration,
            configurations.control,
            configurations.identity,
            configurations.egress,
            configurations.action,
            configurations.runtime,
            configurations.worker,
            configurations.scheduler,
            configurations.learning,
            configurations.release_operator,
            configurations.operator,
            configurations.security_test,
        ):
            monkeypatch.setenv(
                configuration.purpose.environment_variable,
                configuration.url.render_as_string(hide_password=False),
            )
        yield configurations


@pytest.fixture
def migration_configuration(
    migration_test_database: HarnessDatabaseConfigurations,
) -> DatabaseConfiguration:
    return migration_test_database.migration


@pytest.fixture
def guarded_control_engine(
    migration_test_database: HarnessDatabaseConfigurations,
) -> Iterator[Engine]:
    engine = create_database_engine(migration_test_database.control)
    try:
        with engine.connect() as connection:
            assert_control_role(connection)
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def guarded_worker_engine(
    migration_test_database: HarnessDatabaseConfigurations,
) -> Iterator[Engine]:
    engine = create_database_engine(migration_test_database.worker)
    try:
        with engine.connect() as connection:
            assert_worker_role(connection)
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def guarded_runtime_engine(
    migration_test_database: HarnessDatabaseConfigurations,
) -> Iterator[Engine]:
    engine = create_database_engine(migration_test_database.security_test)
    try:
        with engine.connect() as connection:
            assert_runtime_role(connection)
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def clear_openapi_v0_test_release_after_each_test(
    migration_test_database: HarnessDatabaseConfigurations,
) -> Iterator[None]:
    """The disposable revision database is the complete per-test cleanup."""

    del migration_test_database
    yield


@pytest.fixture(autouse=True)
def migrated_database() -> Iterator[None]:
    """The revision-specific database fixture owns migration setup."""

    yield


def _delete_issue_27_upgrade_fixture(
    configuration: DatabaseConfiguration,
    organization_id: UUID,
) -> None:
    """Remove only the disposable migration-compatibility scenario."""

    engine = create_database_engine(configuration)
    immutable_tables = (
        (
            "file_delete_observation_execution",
            "file_delete_observation_execution_immutable",
        ),
        ("file_source_delete_observation_page", None),
        ("file_source_change", "file_source_change_immutable"),
        ("file_source_change_page", "file_source_change_page_immutable"),
        ("file_source_cleanup_intent", "file_source_cleanup_intent_immutable"),
        (
            "file_source_publish_watermark",
            "file_source_publish_watermark_immutable",
        ),
        (
            "file_source_acquisition_checkpoint",
            "file_source_acquisition_checkpoint_immutable",
        ),
        ("file_resource_cleanup_intent", "file_resource_cleanup_intent_immutable"),
        ("file_import_job_event", "file_import_job_event_immutable"),
        ("file_revision_supersession", "file_revision_supersession_immutable"),
        ("file_revision_replacement_plan", "file_revision_replacement_plan_immutable"),
        ("exact_phrase_candidate", "exact_phrase_candidate_immutable"),
        ("revision_publication_event", "revision_publication_event_immutable"),
        ("context_fragment", "context_fragment_reject_mutation"),
        ("file_revision_snapshot", "file_revision_snapshot_immutable"),
        ("context_revision", "context_revision_reject_mutation"),
        ("file_acquisition_result", "file_acquisition_result_immutable"),
        ("file_resource_ingestion_guard", "file_resource_ingestion_guard_immutable"),
        ("file_acquisition", "file_acquisition_immutable"),
        ("source_version", "source_version_immutable"),
    )
    try:
        with engine.begin() as connection:
            for table, trigger in immutable_tables:
                if trigger is not None:
                    connection.execute(
                        text(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}")
                    )
        try:
            with engine.begin() as connection:
                user_ids = tuple(
                    connection.execute(
                        text(
                            "SELECT user_id FROM membership "
                            "WHERE organization_id = :org"
                        ),
                        {"org": organization_id},
                    ).scalars()
                )
                for table in (
                    "action_ticket",
                    "action_delivery_attempt",
                    "context_feedback",
                    "file_delete_observation_execution",
                    "file_source_publish_watermark",
                    "file_source_acquisition_checkpoint",
                    "file_resource_cleanup_intent",
                    "file_import_job_event",
                    "file_publication_recovery",
                    "file_revision_supersession",
                    "file_revision_replacement_plan",
                    "file_acquisition_result",
                    "exact_phrase_candidate",
                    "revision_publication_event",
                    "membership_resource_field_right",
                    "resource_access_policy",
                    "article_access_policy",
                    "article_source_acl_observation",
                    "article_explicit_policy_setting",
                    "article_access_group_membership",
                    "article_access_group",
                    "context_fragment",
                    "file_revision_snapshot",
                    "context_revision",
                    "context_resource",
                    "file_resource_ingestion_guard",
                    "file_import_job",
                    "file_source_cleanup_intent",
                    "file_acquisition",
                    "file_source_delete_observation_page",
                    "file_source_change",
                    "file_source_change_page",
                    "source_article_policy_default",
                    "context_source",
                    "source_version",
                    "service_principal",
                    "membership",
                    "organization_article_policy_default",
                ):
                    connection.execute(
                        text(f"DELETE FROM {table} WHERE organization_id = :org"),
                        {"org": organization_id},
                    )
                for user_id in user_ids:
                    connection.execute(
                        text(
                            "DELETE FROM user_account "
                            "WHERE user_id = :user_id AND NOT EXISTS ("
                            "SELECT 1 FROM membership "
                            "WHERE membership.user_id = user_account.user_id)"
                        ),
                        {"user_id": user_id},
                    )
                connection.execute(
                    text("DELETE FROM organization WHERE organization_id = :org"),
                    {"org": organization_id},
                )
        finally:
            with engine.begin() as connection:
                for table, trigger in reversed(immutable_tables):
                    if trigger is not None:
                        connection.execute(
                            text(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}")
                        )
    finally:
        engine.dispose()


def _revision_rows(configuration: DatabaseConfiguration) -> list[str]:
    engine = create_database_engine(configuration)
    try:
        with engine.connect() as connection:
            return list(
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalars()
            )
    finally:
        engine.dispose()


def _application_tables(configuration: DatabaseConfiguration) -> list[str]:
    engine = create_database_engine(configuration)
    try:
        with engine.connect() as connection:
            return list(
                connection.execute(
                    text(
                        """
                        SELECT tablename
                        FROM pg_tables
                        WHERE schemaname = 'public'
                        ORDER BY tablename
                        """
                    )
                ).scalars()
            )
    finally:
        engine.dispose()


def test_empty_baseline_remains_a_reversible_historical_revision(
    migration_configuration: DatabaseConfiguration,
) -> None:
    alembic_configuration = Config(ROOT / "alembic.ini")

    try:
        command.downgrade(alembic_configuration, "base")
        assert _revision_rows(migration_configuration) == []
        command.upgrade(alembic_configuration, "20260720_0001")
        assert _revision_rows(migration_configuration) == ["20260720_0001"]
        assert _application_tables(migration_configuration) == ["alembic_version"]
    finally:
        command.upgrade(alembic_configuration, "head")
    assert _revision_rows(migration_configuration) == [HEAD_REVISION]


def test_article_policy_revision_backfills_legacy_access_and_reapplies_cleanly(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Issue #141 preserves legacy PRIVATE access across 0040 compatibility."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    organization_id = uuid4()
    visible_resource_ref = f"resource:article-backfill-visible:{uuid4()}"
    isolated_resource_ref = f"resource:article-backfill-isolated:{uuid4()}"
    principal_ref = f"principal:article-backfill:{uuid4()}"
    source_ref = f"source:article-backfill:{uuid4()}"

    try:
        command.downgrade(alembic_configuration, "20260729_0041")
        assert _revision_rows(migration_configuration) == ["20260729_0041"]
        assert ARTICLE_POLICY_TABLES.isdisjoint(
            _application_tables(migration_configuration)
        )
        engine = create_database_engine(migration_configuration)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO organization (organization_id) "
                        "VALUES (:organization_id)"
                    ),
                    {"organization_id": organization_id},
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO context_resource (
                            organization_id, resource_ref, source_ref,
                            active_revision_id, tombstoned
                        ) VALUES
                        (:organization_id, :visible_resource_ref, :source_ref,
                         NULL, false),
                        (:organization_id, :isolated_resource_ref, :source_ref,
                         NULL, false)
                        """
                    ),
                    {
                        "organization_id": organization_id,
                        "visible_resource_ref": visible_resource_ref,
                        "isolated_resource_ref": isolated_resource_ref,
                        "source_ref": source_ref,
                    },
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO resource_access_policy (
                            organization_id, resource_ref, principal_ref,
                            access_version, access_state, revoked_at
                        ) VALUES (
                            :organization_id, :resource_ref, :principal_ref,
                            1, 'allowed', NULL
                        )
                        """
                    ),
                    {
                        "organization_id": organization_id,
                        "resource_ref": visible_resource_ref,
                        "principal_ref": principal_ref,
                    },
                )
        finally:
            engine.dispose()

        for pass_number in (1, 2):
            command.upgrade(alembic_configuration, "head")
            assert _revision_rows(migration_configuration) == [HEAD_REVISION]
            assert ARTICLE_POLICY_TABLES.issubset(
                _application_tables(migration_configuration)
            )
            engine = create_database_engine(migration_configuration)
            try:
                with engine.connect() as connection:
                    policies = {
                        row.resource_ref: tuple(row)[1:]
                        for row in connection.execute(
                            text(
                                """
                                SELECT resource_ref, local_policy_kind,
                                       policy_kind, published, resolution_rung,
                                       source_evidence_mode,
                                       source_observation_status,
                                       source_observation_version,
                                       fixed_at_policy_epoch
                                FROM article_access_policy
                                WHERE organization_id = :organization_id
                                ORDER BY resource_ref
                                """
                            ),
                            {"organization_id": organization_id},
                        )
                    }
                    observations = connection.execute(
                        text(
                            """
                            SELECT resource_ref, evidence_mode,
                                   observation_status, policy_kind,
                                   observation_version
                            FROM article_source_acl_observation
                            WHERE organization_id = :organization_id
                            """
                        ),
                        {"organization_id": organization_id},
                    ).all()
            finally:
                engine.dispose()
            assert policies == {
                visible_resource_ref: (
                    "private",
                    "private",
                    True,
                    "explicit_article",
                    "mirrored",
                    "resolved",
                    1,
                    1,
                ),
                isolated_resource_ref: (
                    "private",
                    None,
                    False,
                    "tenant_default",
                    "mirrored",
                    "missing",
                    None,
                    1,
                ),
            }
            assert [tuple(row) for row in observations] == [
                (
                    visible_resource_ref,
                    "mirrored",
                    "resolved",
                    "private",
                    1,
                )
            ]
            if pass_number == 1:
                command.downgrade(alembic_configuration, "20260729_0041")
                assert ARTICLE_POLICY_TABLES.isdisjoint(
                    _application_tables(migration_configuration)
                )
    finally:
        command.upgrade(alembic_configuration, "head")
        engine = create_database_engine(migration_configuration)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "DELETE FROM resource_access_policy "
                        "WHERE organization_id = :organization_id"
                    ),
                    {"organization_id": organization_id},
                )
                connection.execute(
                    text(
                        "DELETE FROM context_resource "
                        "WHERE organization_id = :organization_id"
                    ),
                    {"organization_id": organization_id},
                )
                connection.execute(
                    text(
                        "DELETE FROM organization "
                        "WHERE organization_id = :organization_id"
                    ),
                    {"organization_id": organization_id},
                )
        finally:
            engine.dispose()


def test_article_policy_revision_backfills_exact_file_acl_version_and_stales(
    migration_configuration: DatabaseConfiguration,
    guarded_runtime_engine: Engine,
) -> None:
    """Legacy File access binds durable ACL time and the active SourceVersion."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    organization_id = uuid4()
    source_id = uuid4()
    source_version_id = uuid4()
    replacement_version_id = uuid4()
    resource_ref = f"resource:file-article-backfill:{uuid4()}"
    principal_ref = f"principal:file-article-backfill:{uuid4()}"
    capability_document = json.dumps(
        FILE_CAPABILITY_MANIFEST.document(), separators=(",", ":"), sort_keys=True
    )

    try:
        command.downgrade(alembic_configuration, "20260729_0041")
        engine = create_database_engine(migration_configuration)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text("INSERT INTO organization (organization_id) VALUES (:org)"),
                    {"org": organization_id},
                )
                connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
                connection.execute(
                    text(
                        """
                        INSERT INTO context_source (
                            organization_id, source_id, display_name, source_kind,
                            registration_operation, idempotency_key,
                            registration_digest, active_version_id, created_at
                        ) VALUES (
                            :org, :source_id, 'Legacy File', 'file',
                            'register_source', :idempotency_key,
                            :registration_digest, :source_version_id,
                            statement_timestamp()
                        )
                        """
                    ),
                    {
                        "org": organization_id,
                        "source_id": source_id,
                        "idempotency_key": f"legacy-file-{uuid4().hex}",
                        "registration_digest": "a" * 64,
                        "source_version_id": source_version_id,
                    },
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO source_version (
                            organization_id, source_id, version_id, source_kind,
                            root_ref, capability_manifest, created_at
                        ) VALUES (
                            :org, :source_id, :source_version_id, 'file',
                            'legacy-file-root', CAST(:capabilities AS jsonb),
                            statement_timestamp()
                        )
                        """
                    ),
                    {
                        "org": organization_id,
                        "source_id": source_id,
                        "source_version_id": source_version_id,
                        "capabilities": capability_document,
                    },
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO context_resource (
                            organization_id, resource_ref, source_ref,
                            active_revision_id, tombstoned
                        ) VALUES (:org, :resource, :source_ref, NULL, false)
                        """
                    ),
                    {
                        "org": organization_id,
                        "resource": resource_ref,
                        "source_ref": str(source_id),
                    },
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO resource_access_policy (
                            organization_id, resource_ref, principal_ref,
                            access_version, access_state, revoked_at
                        ) VALUES (:org, :resource, :principal, 1, 'allowed', NULL)
                        """
                    ),
                    {
                        "org": organization_id,
                        "resource": resource_ref,
                        "principal": principal_ref,
                    },
                )
        finally:
            engine.dispose()

        command.upgrade(alembic_configuration, "head")
        engine = create_database_engine(migration_configuration)
        try:
            with engine.connect() as connection:
                observation = connection.execute(
                    text(
                        """
                        SELECT source_id, source_version_ref, observed_at, acl_as_of,
                               declared_lag_seconds
                        FROM article_source_acl_observation
                        WHERE organization_id = :org AND resource_ref = :resource
                        """
                    ),
                    {"org": organization_id, "resource": resource_ref},
                ).one()
                policy = connection.execute(
                    text(
                        """
                        SELECT source_version_ref, source_acl_as_of,
                               source_declared_lag_seconds, published
                        FROM article_access_policy
                        WHERE organization_id = :org AND resource_ref = :resource
                        """
                    ),
                    {"org": organization_id, "resource": resource_ref},
                ).one()
            assert tuple(observation[:2]) == (source_id, source_version_id)
            assert observation.observed_at == observation.acl_as_of
            assert observation.declared_lag_seconds == 0
            assert tuple(policy) == (
                source_version_id,
                observation.acl_as_of,
                0,
                True,
            )

            with guarded_runtime_engine.begin() as connection:
                connection.execute(
                    text("SELECT set_config('app.organization_id', :org, true)"),
                    {"org": str(organization_id)},
                )
                assert connection.execute(
                    text(
                        "SELECT public.context_runtime_article_source_version_allows("
                        ":org, :resource, :version)"
                    ),
                    {
                        "org": organization_id,
                        "resource": resource_ref,
                        "version": source_version_id,
                    },
                ).scalar_one() is True

            with engine.begin() as connection:
                connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
                connection.execute(
                    text(
                        """
                        INSERT INTO source_version (
                            organization_id, source_id, version_id, source_kind,
                            root_ref, capability_manifest, created_at
                        ) VALUES (
                            :org, :source_id, :version_id, 'file',
                            'replacement-file-root', CAST(:capabilities AS jsonb),
                            statement_timestamp()
                        )
                        """
                    ),
                    {
                        "org": organization_id,
                        "source_id": source_id,
                        "version_id": replacement_version_id,
                        "capabilities": capability_document,
                    },
                )
                connection.execute(
                    text(
                        "UPDATE context_source SET active_version_id = :version_id "
                        "WHERE organization_id = :org AND source_id = :source_id"
                    ),
                    {
                        "org": organization_id,
                        "source_id": source_id,
                        "version_id": replacement_version_id,
                    },
                )

            with guarded_runtime_engine.begin() as connection:
                connection.execute(
                    text("SELECT set_config('app.organization_id', :org, true)"),
                    {"org": str(organization_id)},
                )
                assert connection.execute(
                    text(
                        "SELECT public.context_runtime_article_source_version_allows("
                        ":org, :resource, :version)"
                    ),
                    {
                        "org": organization_id,
                        "resource": resource_ref,
                        "version": source_version_id,
                    },
                ).scalar_one() is False

            with pytest.raises(RuntimeError, match="not safely representable"):
                downgrade_revision(migration_configuration, "20260730_0042")
        finally:
            engine.dispose()
    finally:
        command.upgrade(alembic_configuration, "head")
        _delete_issue_27_upgrade_fixture(migration_configuration, organization_id)


def test_article_policy_downgrade_rejects_every_deferred_admin_state(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """0040 cannot represent changed defaults, explicit settings, or groups."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    organization_id = uuid4()
    source_ref = f"source:downgrade-admin:{uuid4()}"
    resource_ref = f"resource:downgrade-admin:{uuid4()}"
    group_ref = "group:downgrade-admin"
    engine = create_database_engine(migration_configuration)
    mutations = (
        (
            "UPDATE organization_article_policy_default "
            "SET default_version = 2 WHERE organization_id = :org",
            "UPDATE organization_article_policy_default "
            "SET default_version = 1 WHERE organization_id = :org",
        ),
        (
            "INSERT INTO source_article_policy_default (organization_id, "
            "source_ref, policy_kind, group_refs, default_version) VALUES "
            "(:org, :source, 'private', ARRAY[]::text[], 2)",
            "DELETE FROM source_article_policy_default WHERE organization_id = :org "
            "AND source_ref = :source",
        ),
        (
            "INSERT INTO article_explicit_policy_setting (organization_id, "
            "source_ref, resource_ref, policy_kind, group_refs) VALUES "
            "(:org, :source, :resource, 'private', ARRAY[]::text[])",
            "DELETE FROM article_explicit_policy_setting WHERE organization_id = "
            ":org AND resource_ref = :resource",
        ),
        (
            "INSERT INTO article_access_group (organization_id, group_ref) "
            "VALUES (:org, :group_ref)",
            "DELETE FROM article_access_group WHERE organization_id = :org "
            "AND group_ref = :group_ref",
        ),
    )
    parameters = {
        "org": organization_id,
        "source": source_ref,
        "resource": resource_ref,
        "group_ref": group_ref,
    }
    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO organization (organization_id) VALUES (:org)"),
                parameters,
            )
            connection.execute(
                text(
                    "INSERT INTO context_resource (organization_id, resource_ref, "
                    "source_ref, active_revision_id, tombstoned) VALUES "
                    "(:org, :resource, :source, NULL, false)"
                ),
                parameters,
            )
        for mutation, cleanup in mutations:
            with engine.begin() as connection:
                connection.execute(text(mutation), parameters)
            with pytest.raises(RuntimeError, match="not safely representable"):
                downgrade_revision(migration_configuration, "20260730_0042")
            assert _revision_rows(migration_configuration) == [HEAD_REVISION]
            with engine.begin() as connection:
                connection.execute(text(cleanup), parameters)
    finally:
        command.upgrade(alembic_configuration, "head")
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM context_resource WHERE organization_id = :org"),
                parameters,
            )
            connection.execute(
                text("DELETE FROM organization WHERE organization_id = :org"),
                parameters,
            )
        engine.dispose()


def test_article_policy_downgrade_refuses_state_that_would_reauthorize_content(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Rollback is atomic when ADR-0077 denial cannot survive legacy policy."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    organization_id = uuid4()
    source_ref = f"source:unsafe-downgrade:{uuid4()}"
    resource_ref = f"resource:unsafe-downgrade:{uuid4()}"
    principal_ref = f"principal:unsafe-downgrade:{uuid4()}"
    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO organization (organization_id) VALUES (:org)"),
                {"org": organization_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO context_resource (
                        organization_id, resource_ref, source_ref,
                        active_revision_id, tombstoned
                    ) VALUES (:org, :resource, :source, NULL, false)
                    """
                ),
                {
                    "org": organization_id,
                    "resource": resource_ref,
                    "source": source_ref,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO resource_access_policy (
                        organization_id, resource_ref, principal_ref,
                        access_version, access_state, revoked_at
                    ) VALUES (:org, :resource, :principal, 1, 'allowed', NULL)
                    """
                ),
                {
                    "org": organization_id,
                    "resource": resource_ref,
                    "principal": principal_ref,
                },
            )
            connection.execute(
                text(
                    """
                    UPDATE article_access_policy
                    SET policy_version = 2, policy_kind = NULL,
                        group_refs = ARRAY[]::text[], published = false,
                        source_observation_status = 'failed',
                        source_observation_version = 2,
                        fixed_at_policy_epoch = 2
                    WHERE organization_id = :org AND resource_ref = :resource
                    """
                ),
                {"org": organization_id, "resource": resource_ref},
            )

        with pytest.raises(RuntimeError, match="not safely representable"):
            downgrade_revision(migration_configuration, "20260730_0042")

        assert _revision_rows(migration_configuration) == [HEAD_REVISION]
        with engine.connect() as connection:
            policy = connection.execute(
                text(
                    "SELECT published, source_observation_status "
                    "FROM article_access_policy "
                    "WHERE organization_id = :org AND resource_ref = :resource"
                ),
                {"org": organization_id, "resource": resource_ref},
            ).one()
        assert tuple(policy) == (False, "failed")
    finally:
        command.upgrade(alembic_configuration, "head")
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM resource_access_policy WHERE organization_id = :org"),
                {"org": organization_id},
            )
            connection.execute(
                text("DELETE FROM context_resource WHERE organization_id = :org"),
                {"org": organization_id},
            )
            connection.execute(
                text("DELETE FROM organization WHERE organization_id = :org"),
                {"org": organization_id},
            )
        engine.dispose()


def test_article_policy_downgrade_restores_source_version_least_privilege(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """0041 must not leave its SourceVersion definer capability in 0040."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    try:
        command.downgrade(alembic_configuration, "20260729_0041")
        engine = create_database_engine(migration_configuration)
        try:
            with engine.connect() as connection:
                assert connection.execute(
                    text(
                        "SELECT has_table_privilege("
                        "'context_engine_access_policy_definer', "
                        "'public.source_version', 'SELECT')"
                    )
                ).scalar_one() is False
        finally:
            engine.dispose()
    finally:
        command.upgrade(alembic_configuration, "head")


def test_article_policy_downgrade_waits_for_source_authority_writer(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
) -> None:
    """0041 representability is serialized behind current SourceVersion state."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        issue_lease=False,
    )
    engine = create_database_engine(migration_configuration)
    try:
        with guarded_control_engine.connect() as writer:
            writer_transaction = writer.begin()
            try:
                activated_version_id = uuid4()
                assert writer.execute(
                    text(
                        "SELECT activated_version_id FROM public."
                        "context_control_activate_file_change_feed("
                        ":org, :source_id, :activated_version_id)"
                    ),
                    {
                        "org": scenario.organization_id,
                        "source_id": scenario.source_ref.value,
                        "activated_version_id": activated_version_id,
                    },
                ).scalar_one() == activated_version_id
                with ThreadPoolExecutor(max_workers=1) as executor:
                    downgrade = executor.submit(
                        command.downgrade,
                        alembic_configuration,
                        "20260729_0041",
                    )
                    waiting = False
                    deadline = monotonic() + 5
                    while monotonic() < deadline:
                        with engine.connect() as observer:
                            waiting = observer.execute(
                                text(
                                    "SELECT EXISTS (SELECT 1 FROM pg_locks "
                                    "WHERE relation = 'context_source'::regclass "
                                    "AND mode = 'AccessExclusiveLock' "
                                    "AND granted IS FALSE)"
                                )
                            ).scalar_one()
                        if waiting or downgrade.done():
                            break
                        sleep(0.01)
                    observed_waiting = waiting
                    observed_pending = not downgrade.done()
                    writer_transaction.commit()
                    downgrade.result(timeout=10)
                    assert observed_waiting is True
                    assert observed_pending is True
            finally:
                if writer_transaction.is_active:
                    writer_transaction.rollback()
        assert _revision_rows(migration_configuration) == ["20260729_0041"]
    finally:
        command.upgrade(alembic_configuration, "head")
        engine.dispose()
        _delete_file_import_scenario(
            migration_configuration,
            scenario.organization_id,
        )


def test_organization_isolation_revision_downgrades_and_reapplies_cleanly(
    migration_configuration: DatabaseConfiguration,
) -> None:
    alembic_configuration = Config(ROOT / "alembic.ini")

    try:
        command.downgrade(alembic_configuration, "20260720_0001")
        assert _revision_rows(migration_configuration) == ["20260720_0001"]
        assert _application_tables(migration_configuration) == ["alembic_version"]
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    assert _application_tables(migration_configuration) == HEAD_TABLES


def test_supply_execution_bridge_revision_downgrades_and_reapplies_cleanly(
    migration_configuration: DatabaseConfiguration,
) -> None:
    alembic_configuration = Config(ROOT / "alembic.ini")

    try:
        command.downgrade(alembic_configuration, "20260727_0040")
        assert _revision_rows(migration_configuration) == ["20260727_0040"]
        application_tables = _application_tables(migration_configuration)
        assert "supply_connector_job" not in application_tables
        assert "supply_connector_staged_page" not in application_tables
        assert "supply_connector_accepted_page" not in application_tables
        assert "supply_connector_checkpoint" not in application_tables
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == [HEAD_REVISION]


def test_bulk_article_policy_revision_downgrades_and_reapplies_when_audit_empty(
    migration_configuration: DatabaseConfiguration,
) -> None:
    configuration = Config(ROOT / "alembic.ini")

    command.downgrade(configuration, "20260730_0045")
    try:
        assert _revision_rows(migration_configuration) == ["20260730_0045"]
        assert "bulk_article_policy_change_audit" not in _application_tables(
            migration_configuration
        )
    finally:
        command.upgrade(configuration, "head")

    assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    assert _application_tables(migration_configuration) == HEAD_TABLES


def test_feishu_acl_revisions_downgrade_and_reapply_when_empty(
    migration_configuration: DatabaseConfiguration,
) -> None:
    alembic_configuration = Config(ROOT / "alembic.ini")

    command.upgrade(alembic_configuration, "head")
    assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    try:
        command.downgrade(alembic_configuration, "20260731_0048")
        assert _revision_rows(migration_configuration) == ["20260731_0048"]
        engine = create_database_engine(migration_configuration)
        try:
            with engine.connect() as connection:
                source_constraints = set(
                    connection.execute(
                        text(
                            "SELECT conname FROM pg_constraint "
                            "WHERE conrelid IN "
                            "('context_source'::regclass, 'source_version'::regclass)"
                        )
                    ).scalars()
                )
                trigger_definition = connection.execute(
                    text(
                        "SELECT pg_get_functiondef("
                        "'article_access_policy_fix_from_file_access_grant()'::regprocedure)"
                    )
                ).scalar_one()
            assert "ck_context_source_kind_file" in source_constraints
            assert "ck_source_version_kind_file" in source_constraints
            assert "ck_source_version_file_capabilities" in source_constraints
            assert "source.source_kind = 'file'" not in trigger_definition
        finally:
            engine.dispose()
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            source_constraints = set(
                connection.execute(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conrelid IN "
                        "('context_source'::regclass, 'source_version'::regclass)"
                    )
                ).scalars()
            )
            trigger_definition = connection.execute(
                text(
                    "SELECT pg_get_functiondef("
                    "'article_access_policy_fix_from_file_access_grant()'::regprocedure)"
                )
            ).scalar_one()
        assert "ck_context_source_kind" in source_constraints
        assert "ck_source_version_kind" in source_constraints
        assert "ck_source_version_capabilities" in source_constraints
        assert "source.source_kind = 'file'" in trigger_definition
    finally:
        engine.dispose()


@pytest.mark.parametrize("retain_mapping", [False, True])
def test_feishu_subject_mapping_revision_refuses_retained_authority(
    migration_configuration: DatabaseConfiguration,
    retain_mapping: bool,
) -> None:
    organization_id = uuid4()
    source_id = uuid4()
    version_id = uuid4()
    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO organization (organization_id) VALUES (:org)"),
                {"org": organization_id},
            )
            connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            connection.execute(
                text(
                    """
                    INSERT INTO context_source (
                        organization_id, source_id, source_kind, display_name,
                        active_version_id, registration_operation,
                        idempotency_key, registration_digest, lifecycle_state,
                        created_at
                    ) VALUES (
                        :org, :source, 'feishu_docs', 'Synthetic Feishu',
                        :version, 'register_source', :idempotency,
                        :digest, 'active', statement_timestamp()
                    )
                    """
                ),
                {
                    "org": organization_id,
                    "source": source_id,
                    "version": version_id,
                    "idempotency": f"feishu-mapping-{source_id}",
                    "digest": "a" * 64,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO source_version (
                        organization_id, source_id, version_id, source_kind,
                        root_ref, capability_manifest, created_at
                    ) VALUES (
                        :org, :source, :version, 'feishu_docs', 'feishu-root',
                        CAST(:capabilities AS jsonb), statement_timestamp()
                    )
                    """
                ),
                {
                    "org": organization_id,
                    "source": source_id,
                    "version": version_id,
                    "capabilities": FEISHU_DOCS_CAPABILITY_MANIFEST_JSON,
                },
            )
            if retain_mapping:
                connection.execute(
                    text(
                        """
                        INSERT INTO feishu_subject_mapping (
                            organization_id, source_id, subject_kind,
                            external_ref, local_ref, mapping_version
                        ) VALUES (:org, :source, 'identity',
                                  'external:test', 'principal:test', 1)
                        """
                    ),
                    {"org": organization_id, "source": source_id},
                )
        with pytest.raises(
            SQLAlchemyError,
            match="retained Feishu authorization state",
        ):
            downgrade_revision(migration_configuration, "20260731_0051")
        assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    finally:
        engine.dispose()


def test_feishu_subject_mapping_downgrade_serializes_with_source_writer(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """0051 cannot miss Feishu authority committed during its decision."""

    organization_id = uuid4()
    source_id = uuid4()
    version_id = uuid4()
    parameters = {
        "org": organization_id,
        "source": source_id,
        "version": version_id,
        "idempotency": f"feishu-downgrade-race-{source_id}",
        "digest": "b" * 64,
        "capabilities": FEISHU_DOCS_CAPABILITY_MANIFEST_JSON,
    }
    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO organization (organization_id) VALUES (:org)"),
                parameters,
            )
        with engine.connect() as writer:
            writer_transaction = writer.begin()
            try:
                writer.execute(text("SET CONSTRAINTS ALL DEFERRED"))
                writer.execute(
                    text(
                        """
                        INSERT INTO context_source (
                            organization_id, source_id, source_kind, display_name,
                            active_version_id, registration_operation,
                            idempotency_key, registration_digest, lifecycle_state,
                            created_at
                        ) VALUES (
                            :org, :source, 'feishu_docs', 'Synthetic Feishu',
                            :version, 'register_source', :idempotency,
                            :digest, 'active', statement_timestamp()
                        )
                        """
                    ),
                    parameters,
                )
                writer.execute(
                    text(
                        """
                        INSERT INTO source_version (
                            organization_id, source_id, version_id, source_kind,
                            root_ref, capability_manifest, created_at
                        ) VALUES (
                            :org, :source, :version, 'feishu_docs', 'feishu-root',
                            CAST(:capabilities AS jsonb), statement_timestamp()
                        )
                        """
                    ),
                    parameters,
                )
                with ThreadPoolExecutor(max_workers=1) as executor:
                    pending_downgrade = executor.submit(
                        downgrade_revision,
                        migration_configuration,
                        "20260731_0051",
                    )
                    downgrade_waiting = False
                    with engine.connect() as observer:
                        deadline = monotonic() + 10
                        while monotonic() < deadline:
                            downgrade_waiting = observer.execute(
                                text(
                                    """
                                    SELECT EXISTS (
                                        SELECT 1 FROM pg_locks
                                        WHERE database = (
                                            SELECT oid FROM pg_database
                                            WHERE datname = current_database()
                                        )
                                          AND relation = 'context_source'::regclass
                                          AND mode = 'AccessExclusiveLock'
                                          AND granted IS FALSE
                                    )
                                    """
                                )
                            ).scalar_one()
                            if downgrade_waiting or pending_downgrade.done():
                                break
                            sleep(0.01)
                    assert downgrade_waiting is True
                    assert pending_downgrade.done() is False
                    writer_transaction.commit()
                    with pytest.raises(
                        SQLAlchemyError,
                        match="retained Feishu authorization state",
                    ):
                        pending_downgrade.result(timeout=10)
            finally:
                if writer_transaction.is_active:
                    writer_transaction.rollback()
        assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    finally:
        engine.dispose()


def test_membership_revision_downgrades_to_issue_8_and_reapplies_cleanly(
    migration_configuration: DatabaseConfiguration,
) -> None:
    alembic_configuration = Config(ROOT / "alembic.ini")

    try:
        command.downgrade(alembic_configuration, "20260720_0002")
        assert _revision_rows(migration_configuration) == ["20260720_0002"]
        assert _application_tables(migration_configuration) == [
            "alembic_version",
            "organization",
            "organization_record",
        ]
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == [HEAD_REVISION]


def test_content_schema_revision_downgrades_to_membership_and_reapplies_cleanly(
    migration_configuration: DatabaseConfiguration,
) -> None:
    alembic_configuration = Config(ROOT / "alembic.ini")

    try:
        command.downgrade(alembic_configuration, "20260721_0003")
        assert _revision_rows(migration_configuration) == ["20260721_0003"]
        assert _application_tables(migration_configuration) == [
            "alembic_version",
            "membership",
            "organization",
            "organization_record",
            "user_account",
        ]
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    assert _application_tables(migration_configuration) == HEAD_TABLES


def test_policy_epoch_revision_downgrades_to_content_and_reapplies_cleanly(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """PG-REVOCATION-006: the epoch/access boundary is one reversible revision."""

    alembic_configuration = Config(ROOT / "alembic.ini")

    try:
        command.downgrade(alembic_configuration, "20260721_0004")
        assert _revision_rows(migration_configuration) == ["20260721_0004"]
        assert _application_tables(migration_configuration) == [
            "alembic_version",
            "context_fragment",
            "context_resource",
            "context_revision",
            "membership",
            "organization",
            "organization_record",
            "user_account",
        ]
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    assert _application_tables(migration_configuration) == HEAD_TABLES


def test_worker_lease_revision_downgrades_to_policy_epoch_and_reapplies_cleanly(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Issue #17 worker authority is one reversible schema revision."""

    alembic_configuration = Config(ROOT / "alembic.ini")

    try:
        command.downgrade(alembic_configuration, "20260721_0005")
        assert _revision_rows(migration_configuration) == ["20260721_0005"]
        assert _application_tables(migration_configuration) == [
            "alembic_version",
            "context_fragment",
            "context_resource",
            "context_revision",
            "membership",
            "organization",
            "organization_policy_epoch",
            "organization_record",
            "resource_access_policy",
            "user_account",
        ]
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    assert _application_tables(migration_configuration) == HEAD_TABLES


def test_decision_lineage_revision_downgrades_to_worker_lease_and_reapplies_cleanly(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Issue #19 durable decision lineage is one reversible schema revision."""

    alembic_configuration = Config(ROOT / "alembic.ini")

    try:
        command.downgrade(alembic_configuration, "20260722_0006")
        assert _revision_rows(migration_configuration) == ["20260722_0006"]
        assert "context_run" not in _application_tables(migration_configuration)
        assert "context_run_operator_read_ticket" not in _application_tables(
            migration_configuration
        )
        assert "decision_audit" not in _application_tables(migration_configuration)
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    assert _application_tables(migration_configuration) == HEAD_TABLES


def test_field_projection_revision_downgrades_to_decision_lineage_and_reapplies_cleanly(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Issue #48 supports only the proven empty-content schema rollback."""

    alembic_configuration = Config(ROOT / "alembic.ini")

    try:
        command.downgrade(alembic_configuration, "20260722_0007")
        assert _revision_rows(migration_configuration) == ["20260722_0007"]
        tables = _application_tables(migration_configuration)
        assert "context_fragment_field" not in tables
        assert "membership_resource_field_right" not in tables
        engine = create_database_engine(migration_configuration)
        try:
            with engine.connect() as connection:
                fragment_columns = set(
                    connection.execute(
                        text(
                            """
                            SELECT column_name
                            FROM information_schema.columns
                            WHERE table_schema = 'public'
                              AND table_name = 'context_fragment'
                            """
                        )
                    ).scalars()
                )
            assert "projection_kind" not in fragment_columns
        finally:
            engine.dispose()
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    assert "context_fragment_field" in _application_tables(migration_configuration)
    assert "membership_resource_field_right" in _application_tables(
        migration_configuration
    )


def test_file_source_revision_downgrades_to_learning_release_and_reapplies_cleanly(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Issue #21 source registration is one reversible schema revision."""

    alembic_configuration = Config(ROOT / "alembic.ini")

    try:
        command.downgrade(alembic_configuration, "20260722_0009")
        assert _revision_rows(migration_configuration) == ["20260722_0009"]
        tables = _application_tables(migration_configuration)
        assert "context_source" not in tables
        assert "source_version" not in tables
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    tables = _application_tables(migration_configuration)
    assert "context_source" in tables
    assert "source_version" in tables


def test_structural_markdown_revision_downgrades_and_reapplies_cleanly(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Issue #24 owns one explicit, reversible compiler-v2 schema boundary."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    engine = create_database_engine(migration_configuration)
    try:
        command.downgrade(alembic_configuration, "20260722_0011")
        assert _revision_rows(migration_configuration) == ["20260722_0011"]
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        """
                    SELECT count(*)
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'file_revision_snapshot'
                      AND column_name = 'compilation_document'
                    """
                    )
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text(
                        """
                    SELECT count(*)
                    FROM pg_proc AS procedure
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = procedure.pronamespace
                    WHERE namespace.nspname = 'public'
                      AND procedure.proname =
                          'context_worker_publish_structural_file_import'
                    """
                    )
                ).scalar_one()
                == 0
            )
    finally:
        command.upgrade(alembic_configuration, "head")
        engine.dispose()

    assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        """
                    SELECT count(*)
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'file_revision_snapshot'
                      AND column_name = 'compilation_document'
                    """
                    )
                ).scalar_one()
                == 1
            )
            assert (
                connection.execute(
                    text(
                        """
                    SELECT count(*)
                    FROM pg_proc AS procedure
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = procedure.pronamespace
                    WHERE namespace.nspname = 'public'
                      AND procedure.proname =
                          'context_worker_publish_structural_file_import'
                    """
                    )
                ).scalar_one()
                == 1
            )
    finally:
        engine.dispose()


def test_file_noop_revision_downgrades_and_reapplies_cleanly(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Issue #25 owns one reversible acquisition-outcome schema boundary."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    try:
        command.downgrade(alembic_configuration, "20260723_0012")
        assert _revision_rows(migration_configuration) == ["20260723_0012"]
        tables = _application_tables(migration_configuration)
        assert "file_acquisition_result" not in tables
        assert "file_resource_ingestion_guard" not in tables
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    tables = _application_tables(migration_configuration)
    assert "file_acquisition_result" in tables
    assert "file_resource_ingestion_guard" in tables


def test_file_replacement_revision_downgrades_and_reapplies_cleanly(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Issue #26 owns one reversible empty replacement-lineage boundary."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    try:
        command.downgrade(alembic_configuration, "20260724_0013")
        assert _revision_rows(migration_configuration) == ["20260724_0013"]
        tables = _application_tables(migration_configuration)
        assert "file_revision_replacement_plan" not in tables
        assert "file_revision_supersession" not in tables
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    tables = _application_tables(migration_configuration)
    assert "file_revision_replacement_plan" in tables
    assert "file_revision_supersession" in tables


def test_file_recovery_revision_downgrades_and_reapplies_cleanly(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Issue #27 owns one reversible empty recovery schema boundary."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    try:
        command.downgrade(alembic_configuration, "20260723_0014")
        assert _revision_rows(migration_configuration) == ["20260723_0014"]
        tables = _application_tables(migration_configuration)
        assert "file_publication_recovery" not in tables
        assert "file_import_job_event" not in tables
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    tables = _application_tables(migration_configuration)
    assert "file_publication_recovery" in tables
    assert "file_import_job_event" in tables


def test_file_tombstone_revision_downgrades_and_reapplies_cleanly(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Issue #28 owns one reversible empty cleanup-intent boundary."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    try:
        command.downgrade(alembic_configuration, "20260723_0015")
        assert _revision_rows(migration_configuration) == ["20260723_0015"]
        assert "file_resource_cleanup_intent" not in _application_tables(
            migration_configuration
        )
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    assert "file_resource_cleanup_intent" in _application_tables(
        migration_configuration
    )


def test_file_progress_revision_downgrades_and_reapplies_cleanly(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Issue #29 progress is a deterministic projection of retained lineage."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    try:
        command.downgrade(alembic_configuration, "20260723_0016")
        tables = _application_tables(migration_configuration)
        assert _revision_rows(migration_configuration) == ["20260723_0016"]
        assert "file_source_acquisition_checkpoint" not in tables
        assert "file_source_publish_watermark" not in tables
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    assert "file_source_acquisition_checkpoint" in _application_tables(
        migration_configuration
    )
    assert "file_source_publish_watermark" in _application_tables(
        migration_configuration
    )


def test_file_source_offboarding_revision_downgrades_and_reapplies_cleanly(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Issue #30 owns one reversible empty source-offboarding boundary."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    try:
        command.downgrade(alembic_configuration, "20260723_0017")
        tables = _application_tables(migration_configuration)
        assert _revision_rows(migration_configuration) == ["20260723_0017"]
        assert "file_source_cleanup_intent" not in tables
        engine = create_database_engine(migration_configuration)
        try:
            with engine.connect() as connection:
                columns = set(
                    connection.execute(
                        text(
                            """
                            SELECT column_name
                            FROM information_schema.columns
                            WHERE table_schema = 'public'
                              AND table_name = 'context_source'
                            """
                        )
                    ).scalars()
                )
                private_functions = connection.execute(
                    text(
                        """
                        SELECT count(*) FROM pg_catalog.pg_proc AS procedure
                        JOIN pg_catalog.pg_namespace AS namespace
                          ON namespace.oid = procedure.pronamespace
                        WHERE namespace.nspname = 'public'
                          AND (
                            (procedure.proname =
                                'context_worker_activate_file_replacement_impl'
                             AND procedure.pronargs = 12)
                            OR
                            (procedure.proname =
                                'context_worker_activate_recoverable_file_publication_impl'
                             AND procedure.pronargs = 11)
                          )
                        """
                    )
                ).scalar_one()
                public_execute = connection.execute(
                    text(
                        """
                        SELECT bool_and(pg_catalog.has_function_privilege(
                            'context_engine_worker', procedure.oid, 'EXECUTE'
                        ))
                        FROM pg_catalog.pg_proc AS procedure
                        JOIN pg_catalog.pg_namespace AS namespace
                          ON namespace.oid = procedure.pronamespace
                        WHERE namespace.nspname = 'public'
                          AND (
                            (procedure.proname =
                                'context_worker_activate_file_replacement'
                             AND procedure.pronargs = 12)
                            OR
                            (procedure.proname =
                                'context_worker_activate_recoverable_file_publication'
                             AND procedure.pronargs = 11)
                          )
                        """
                    )
                ).scalar_one()
            assert "lifecycle_state" not in columns
            assert private_functions == 0
            assert public_execute is True
        finally:
            engine.dispose()
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    assert "file_source_cleanup_intent" in _application_tables(migration_configuration)
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            privileges = connection.execute(
                text(
                    """
                    SELECT procedure.proname,
                           pg_catalog.has_function_privilege(
                               'context_engine_worker', procedure.oid, 'EXECUTE'
                           ) AS worker_execute,
                           pg_catalog.has_function_privilege(
                               'context_engine_control', procedure.oid, 'EXECUTE'
                           ) AS control_execute,
                           pg_catalog.has_function_privilege(
                               'context_engine_runtime', procedure.oid, 'EXECUTE'
                           ) AS runtime_execute,
                           pg_catalog.has_function_privilege(
                               'public', procedure.oid, 'EXECUTE'
                           ) AS public_execute
                    FROM pg_catalog.pg_proc AS procedure
                    JOIN pg_catalog.pg_namespace AS namespace
                      ON namespace.oid = procedure.pronamespace
                    WHERE namespace.nspname = 'public'
                      AND (
                        (procedure.proname =
                            'context_worker_activate_file_replacement_impl'
                         AND procedure.pronargs = 12)
                        OR
                        (procedure.proname =
                            'context_worker_activate_recoverable_file_publication_impl'
                         AND procedure.pronargs = 11)
                      )
                    ORDER BY procedure.proname
                    """
                )
            ).all()
        assert len(privileges) == 2
        assert all(tuple(row)[1:] == (False, False, False, False) for row in privileges)
    finally:
        engine.dispose()


def test_file_change_feed_revision_downgrades_and_reapplies_cleanly(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Issue #81 removes only an empty provider page projection."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            v3_organizations = tuple(
                connection.execute(
                    text(
                        """
                        SELECT DISTINCT version.organization_id
                        FROM source_version AS version
                        WHERE version.capability_manifest->>'declarationVersion' =
                              'file-capabilities-v3'
                        """
                    )
                ).scalars()
            )
    finally:
        engine.dispose()

    for organization_id in v3_organizations:
        _delete_issue_27_upgrade_fixture(
            migration_configuration,
            organization_id,
        )
    try:
        command.downgrade(alembic_configuration, "20260724_0027")
        tables = _application_tables(migration_configuration)
        assert _revision_rows(migration_configuration) == ["20260724_0027"]
        assert "file_source_change_page" not in tables
        assert "file_source_change" not in tables
        engine = create_database_engine(migration_configuration)
        try:
            with engine.connect() as connection:
                checkpoint_columns = set(
                    connection.execute(
                        text(
                            """
                            SELECT column_name
                            FROM information_schema.columns
                            WHERE table_schema = 'public'
                              AND table_name = 'file_source_acquisition_checkpoint'
                            """
                        )
                    ).scalars()
                )
                function_count = connection.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM pg_catalog.pg_proc AS procedure
                        JOIN pg_catalog.pg_namespace AS namespace
                          ON namespace.oid = procedure.pronamespace
                        WHERE namespace.nspname = 'public'
                          AND procedure.proname IN (
                            'context_control_activate_file_change_feed',
                            'context_control_accept_file_change_page'
                          )
                        """
                    )
                ).scalar_one()
                public_progress_execute = connection.execute(
                    text(
                        """
                        SELECT pg_catalog.has_function_privilege(
                            'public',
                            'public.context_control_read_file_source_progress('
                            'uuid, uuid)',
                            'EXECUTE'
                        )
                        """
                    )
                ).scalar_one()
            assert "source_version_id" not in checkpoint_columns
            assert "change_page_ref" not in checkpoint_columns
            assert function_count == 0
            assert public_progress_execute is False
        finally:
            engine.dispose()
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    tables = _application_tables(migration_configuration)
    assert "file_source_change_page" in tables
    assert "file_source_change" in tables
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            checkpoint_page_key = tuple(
                connection.execute(
                    text(
                        """
                        SELECT attribute.attname
                        FROM pg_catalog.pg_constraint AS constraint_row
                        JOIN pg_catalog.pg_class AS table_class
                          ON table_class.oid = constraint_row.conrelid
                        JOIN LATERAL unnest(constraint_row.conkey)
                          WITH ORDINALITY AS key(attnum, position) ON true
                        JOIN pg_catalog.pg_attribute AS attribute
                          ON attribute.attrelid = table_class.oid
                         AND attribute.attnum = key.attnum
                        WHERE table_class.relname =
                              'file_source_acquisition_checkpoint'
                          AND constraint_row.conname =
                              'uq_file_source_acquisition_checkpoint_change_page'
                        ORDER BY key.position
                        """
                    )
                ).scalars()
            )
            public_progress_execute = connection.execute(
                text(
                    """
                    SELECT pg_catalog.has_function_privilege(
                        'public',
                        'public.context_control_read_file_source_progress(uuid, uuid)',
                        'EXECUTE'
                    )
                    """
                )
            ).scalar_one()
        assert checkpoint_page_key == ("organization_id", "change_page_ref")
        assert public_progress_execute is False
    finally:
        engine.dispose()


def test_file_delete_observation_revision_owns_atomic_read_volatility(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Issue #85 installs and reverses the one-snapshot read contract."""

    alembic_configuration = Config(ROOT / "alembic.ini")

    def read_volatility() -> dict[str, str]:
        engine = create_database_engine(migration_configuration)
        try:
            with engine.connect() as connection:
                rows = connection.execute(
                    text(
                        """
                        SELECT procedure.proname, procedure.provolatile::text
                        FROM pg_catalog.pg_proc AS procedure
                        JOIN pg_catalog.pg_namespace AS namespace
                          ON namespace.oid = procedure.pronamespace
                        WHERE namespace.nspname = 'public'
                          AND procedure.proname IN (
                            'context_control_read_file_source_progress',
                            'context_control_read_complete_file_change_baseline'
                          )
                        """
                    )
                ).all()
                return {str(row[0]): str(row[1]) for row in rows}
        finally:
            engine.dispose()

    try:
        command.downgrade(alembic_configuration, "20260725_0029")
        assert read_volatility() == {
            "context_control_read_file_source_progress": "v",
        }
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    assert read_volatility() == {
        "context_control_read_complete_file_change_baseline": "s",
        "context_control_read_file_source_progress": "s",
    }

    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            trigger_definition = connection.execute(
                text(
                    "SELECT pg_catalog.pg_get_functiondef("
                    "'public.context_file_change_require_capability_binding()'"
                    "::regprocedure)"
                )
            ).scalar_one()
        assert "set_config" not in trigger_definition
        assert "File page tenant context is not trusted" in trigger_definition
    finally:
        engine.dispose()


def test_file_delete_execution_revision_downgrades_only_while_empty(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Issue #87 removes only unused execution machinery."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM file_delete_observation_execution")
                ).scalar_one()
                == 0
            )
    finally:
        engine.dispose()
    try:
        command.downgrade(alembic_configuration, "20260725_0030")
        assert _revision_rows(migration_configuration) == ["20260725_0030"]
        assert "file_delete_observation_execution" not in _application_tables(
            migration_configuration
        )
        engine = create_database_engine(migration_configuration)
        try:
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        text(
                            "SELECT has_function_privilege("
                            "'context_engine_worker_lease_definer', "
                            "'context_control_tombstone_file_resource("
                            "uuid,uuid,text,text,bigint,uuid)', 'EXECUTE')"
                        )
                    ).scalar_one()
                    is False
                )
        finally:
            engine.dispose()
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    assert "file_delete_observation_execution" in _application_tables(
        migration_configuration
    )
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT has_function_privilege("
                        "'context_engine_worker_lease_definer', "
                        "'context_control_tombstone_file_resource("
                        "uuid,uuid,text,text,bigint,uuid)', 'EXECUTE')"
                    )
                ).scalar_one()
                is True
            )
    finally:
        engine.dispose()


def test_mixed_file_upsert_scheduling_revision_downgrades_and_reapplies_empty(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Issue #89 restores the prior mixed-page refusal when no lineage exists."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    try:
        command.downgrade(alembic_configuration, "20260725_0031")
        assert _revision_rows(migration_configuration) == ["20260725_0031"]
        engine = create_database_engine(migration_configuration)
        try:
            with engine.connect() as connection:
                prior = connection.execute(
                    text(
                        "SELECT pg_catalog.pg_get_functiondef("
                        "'public.context_control_schedule_file_change_page("
                        "uuid,uuid,uuid,text,text,uuid,bigint,uuid)'::regprocedure)"
                    )
                ).scalar_one()
                assert "selected_upsert_count" not in prior
                assert "change.change_kind <> 'upsert'" in prior
                assert (
                    connection.execute(
                        text(
                            "SELECT has_table_privilege("
                            "'context_engine_worker_lease_definer', "
                            "'public.alembic_version', 'SELECT')"
                        )
                    ).scalar_one()
                    is False
                )
        finally:
            engine.dispose()
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            current = connection.execute(
                text(
                    "SELECT pg_catalog.pg_get_functiondef("
                    "'public.context_control_schedule_file_change_page("
                    "uuid,uuid,uuid,text,text,uuid,bigint,uuid)'::regprocedure)"
                )
            ).scalar_one()
            assert "selected_upsert_count" in current
            assert "change.change_kind NOT IN ('upsert', 'delete')" in current
            assert current.count("change.change_kind = 'upsert'") == 4
            assert "FROM public.alembic_version" in current
            assert "insufficient_privilege" in current
            assert HEAD_REVISION not in current
            assert connection.execute(
                text(
                    "SELECT ARRAY["
                    "has_table_privilege('context_engine_worker_lease_definer', "
                    "'public.alembic_version', 'SELECT'), "
                    "has_table_privilege('context_engine_control', "
                    "'public.alembic_version', 'SELECT'), "
                    "has_table_privilege('context_engine_runtime', "
                    "'public.alembic_version', 'SELECT'), "
                    "has_table_privilege('context_engine_worker', "
                    "'public.alembic_version', 'SELECT')]"
                )
            ).scalar_one() == [True, False, False, False]
    finally:
        engine.dispose()


def test_file_dispatch_revision_downgrades_and_reapplies_empty(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Issue #91 removes only its unclaimed scheduler capability cleanly."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    try:
        command.downgrade(alembic_configuration, "20260725_0032")
        assert _revision_rows(migration_configuration) == ["20260725_0032"]
        engine = create_database_engine(migration_configuration)
        try:
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        text(
                            "SELECT to_regprocedure("
                            "'public.context_scheduler_claim_file_import("
                            "bigint,bytea,text[])'"
                            ") IS NULL"
                        )
                    ).scalar_one()
                    is True
                )
                privileges = connection.execute(
                    text(
                        "SELECT table_name, privilege_type, NULL::text AS column_name "
                        "FROM information_schema.table_privileges "
                        "WHERE table_schema = 'public' AND grantee = "
                        "'context_engine_file_dispatch_definer' "
                        "UNION ALL "
                        "SELECT table_name, privilege_type, column_name "
                        "FROM information_schema.column_privileges "
                        "WHERE table_schema = 'public' AND grantee = "
                        "'context_engine_file_dispatch_definer'"
                    )
                ).all()
                assert privileges == []
                assert (
                    connection.execute(
                        text(
                            "SELECT NOT EXISTS ("
                            "SELECT 1 FROM information_schema.columns "
                            "WHERE table_schema = 'public' "
                            "AND table_name = 'file_import_job' "
                            "AND column_name = 'dispatch_claimed')"
                        )
                    ).scalar_one()
                    is True
                )
        finally:
            engine.dispose()
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == [HEAD_REVISION]


def test_file_reclaim_revision_downgrades_and_reapplies_empty(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Issue #93 removes only its unminted higher-generation capability."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    try:
        command.downgrade(alembic_configuration, "20260725_0033")
        assert _revision_rows(migration_configuration) == ["20260725_0033"]
        engine = create_database_engine(migration_configuration)
        try:
            with engine.connect() as connection:
                definition = connection.execute(
                    text(
                        "SELECT pg_get_functiondef("
                        "'public.context_scheduler_claim_file_import("
                        "bigint,bytea,text[])'::regprocedure)"
                    )
                ).scalar_one()
                assert "context_scheduler_claim_first_file_import" not in definition
                assert "event_type" not in definition
                assert connection.execute(
                    text(
                        "SELECT to_regprocedure("
                        "'public.context_scheduler_claim_first_file_import("
                        "bigint,bytea,text[])') IS NULL"
                    )
                ).scalar_one()
                assert connection.execute(
                    text(
                        "SELECT to_regclass("
                        "'public.ix_file_import_job_dispatch_expired') IS NULL"
                    )
                ).scalar_one()
        finally:
            engine.dispose()
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == [HEAD_REVISION]


def test_file_reclaim_revision_refuses_retained_higher_generation(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
) -> None:
    """Downgrade preserves every retained scheduler-created reclaim fact."""

    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        issue_lease=False,
    )
    organization_id = scenario.organization_id
    job_id = scenario.prepared.job_id
    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE file_import_job SET state = 'leased', "
                    "lease_generation = 2, signing_key_version = 1, "
                    "lease_nonce_digest = digest('reclaim-test', 'sha256'), "
                    "lease_issued_at = clock_timestamp(), "
                    "lease_expires_at = clock_timestamp() + interval '5 minutes', "
                    "dispatch_claimed = true WHERE "
                    "organization_id = :organization_id AND job_id = :job_id"
                ),
                {
                    "organization_id": organization_id,
                    "job_id": job_id,
                },
            )
        with pytest.raises(
            RuntimeError,
            match="automatic File reclaim downgrade requires no retained",
        ):
            downgrade_revision(migration_configuration, "20260726_0034")
        assert _revision_rows(migration_configuration) == [HEAD_REVISION]
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT lease_generation, dispatch_claimed FROM "
                    "file_import_job WHERE organization_id = :organization_id "
                    "AND job_id = :job_id"
                ),
                {"organization_id": organization_id, "job_id": job_id},
            ).one() == (2, True)
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE file_import_job SET state = 'available', "
                    "lease_generation = 0, signing_key_version = NULL, "
                    "lease_nonce_digest = NULL, lease_issued_at = NULL, "
                    "lease_expires_at = NULL, dispatch_claimed = false WHERE "
                    "organization_id = :organization_id AND job_id = :job_id"
                ),
                {"organization_id": organization_id, "job_id": job_id},
            )
        command.upgrade(Config(ROOT / "alembic.ini"), "head")
        engine.dispose()


def test_mixed_file_upsert_downgrade_waits_for_in_flight_scheduler(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
) -> None:
    """Rollback fences an old function body before checking mixed lineage."""

    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        issue_lease=False,
    )
    provider_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    checkpoint_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32, 64)))
    authority = ControlOperatorAuthority(
        _ControlAuthenticator(scenario.organization_id),
        call_ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    )
    control = ContextControl(
        store=PostgreSQLControlStore(
            guarded_control_engine,
            clock=lambda: NOW,
            file_import_receiver=scenario.receiver,
            file_change_checkpoint_signing_key=checkpoint_key,
        ),
        authority=authority,
        clock=lambda: NOW,
        file_change_proofs=FileChangeControlProofs(
            provider_verification_key=provider_key.public_key()
        ),
    )
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.ACTIVATE_FILE_CHANGE_FEED,
        request_id="mixed-downgrade-activate-v3",
    ) as call:
        control.activate_file_change_feed(
            call,
            ActivateFileChangeFeed(scenario.source_ref),
        )
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.ACTIVATE_FILE_DELETE_OBSERVATIONS,
        request_id="mixed-downgrade-activate-v4",
    ) as call:
        v4 = control.activate_file_delete_observations(
            call,
            ActivateFileDeleteObservations(scenario.source_ref),
        )
    (scenario.root / "removed.md").write_bytes(b"# Removed\n\nBaseline.\n")
    provider = FileChangeProvider(
        FileRootRegistry(
            {scenario.root_ref: scenario.root},
            limits=FileReadLimits(max_file_bytes=1_024 * 1_024),
        ),
        proofs=FileChangeProviderProofs(
            provider_signing_key=provider_key,
            checkpoint_verification_key=checkpoint_key.public_key(),
        ),
    )
    source = FileChangeSource(scenario.organization_id, v4.active_version)
    baseline = provider.read_changes(source, InitialScan(), ChangeLimit(2))
    assert type(baseline) is ProviderOk
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        request_id="mixed-downgrade-accept-baseline",
    ) as call:
        control.accept_file_change_page(call, baseline.value)
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.READ_SOURCE_PROGRESS,
        request_id="mixed-downgrade-read-baseline",
    ) as call:
        progress = control.read_file_source_progress(call, scenario.source_ref)
    assert progress.complete_change_baseline is not None
    (scenario.root / "handbook.md").write_bytes(NEW_MARKDOWN)
    (scenario.root / "removed.md").unlink()
    mixed = provider.read_changes(
        FileChangeSource(
            scenario.organization_id,
            v4.active_version,
            scan_head=progress.change_scan_head,
            complete_baseline=progress.complete_change_baseline,
        ),
        InitialScan(),
        ChangeLimit(2),
    )
    assert type(mixed) is ProviderOk
    assert {change.kind.value for change in mixed.value.changes} == {
        "upsert",
        "delete",
    }
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        request_id="mixed-downgrade-accept-current",
    ) as call:
        accepted = control.accept_file_change_page(call, mixed.value)
    schedule = ScheduleFileChangePage(
        accepted.source_ref,
        accepted.source_version_ref,
        accepted.page_ref,
        FileImportAudience(
            "principal:file-reader",
            scenario.membership_id,
            1,
        ),
    )

    def schedule_page() -> ScheduledFileChangePage:
        with authority.authorize(
            opaque_credential="control-secret",
            operation=ControlOperation.SCHEDULE_FILE_CHANGE_PAGE,
            request_id="mixed-downgrade-in-flight-schedule",
        ) as call:
            return control.schedule_file_change_page(call, schedule)

    engine = create_database_engine(migration_configuration)
    alembic_configuration = Config(ROOT / "alembic.ini")
    source_lock_key = "context-engine.file-source-progress:"
    source_lock_key += f"{scenario.organization_id}:{scenario.source_ref.value}"
    migration_fence_key = "context-engine.file-change-scheduling-migration-fence"
    try:
        with engine.connect() as blocker:
            blocker_transaction = blocker.begin()
            try:
                blocker.execute(
                    text(
                        "SELECT pg_advisory_xact_lock("
                        "hashtextextended(:source_lock_key, 0))"
                    ),
                    {"source_lock_key": source_lock_key},
                )
                with ThreadPoolExecutor(max_workers=2) as executor:
                    pending_schedule = executor.submit(schedule_page)
                    try:
                        with engine.connect() as observer:
                            deadline = monotonic() + 10
                            while monotonic() < deadline:
                                scheduler_waiting = observer.execute(
                                    text(
                                        """
                                        SELECT EXISTS (
                                            SELECT 1
                                            FROM pg_locks AS waiting
                                            JOIN pg_locks AS held
                                              ON held.locktype = waiting.locktype
                                             AND held.database = waiting.database
                                             AND held.classid = waiting.classid
                                             AND held.objid = waiting.objid
                                             AND held.objsubid = waiting.objsubid
                                            WHERE waiting.locktype = 'advisory'
                                              AND waiting.mode = 'ExclusiveLock'
                                              AND waiting.granted IS FALSE
                                              AND waiting.database = (
                                                SELECT database.oid
                                                FROM pg_database AS database
                                                WHERE database.datname =
                                                  current_database()
                                              )
                                              AND waiting.classid = (
                                                (hashtextextended(:lock_key, 0)
                                                  >> 32) & 4294967295
                                              )::oid
                                              AND waiting.objid = (
                                                hashtextextended(:lock_key, 0)
                                                  & 4294967295
                                              )::oid
                                              AND waiting.objsubid = 1
                                              AND held.mode = 'ExclusiveLock'
                                              AND held.granted IS TRUE
                                        )
                                        """
                                    ),
                                    {"lock_key": source_lock_key},
                                ).scalar_one()
                                if scheduler_waiting:
                                    break
                                sleep(0.01)
                        assert scheduler_waiting
                        pending_downgrade = executor.submit(
                            downgrade_revision,
                            migration_configuration,
                            "20260725_0032",
                        )
                        with engine.connect() as observer:
                            deadline = monotonic() + 10
                            while monotonic() < deadline:
                                downgrade_waiting = observer.execute(
                                    text(
                                        """
                                        SELECT EXISTS (
                                            SELECT 1
                                            FROM pg_locks AS waiting
                                            JOIN pg_locks AS held
                                              ON held.locktype = waiting.locktype
                                             AND held.database = waiting.database
                                             AND held.classid = waiting.classid
                                             AND held.objid = waiting.objid
                                             AND held.objsubid = waiting.objsubid
                                            WHERE waiting.locktype = 'advisory'
                                              AND waiting.mode = 'ExclusiveLock'
                                              AND waiting.granted IS FALSE
                                              AND waiting.database = (
                                                SELECT database.oid
                                                FROM pg_database AS database
                                                WHERE database.datname =
                                                  current_database()
                                              )
                                              AND waiting.classid = (
                                                (hashtextextended(:lock_key, 0)
                                                  >> 32) & 4294967295
                                              )::oid
                                              AND waiting.objid = (
                                                hashtextextended(:lock_key, 0)
                                                  & 4294967295
                                              )::oid
                                              AND waiting.objsubid = 1
                                              AND held.mode = 'ShareLock'
                                              AND held.granted IS TRUE
                                        )
                                        """
                                    ),
                                    {"lock_key": migration_fence_key},
                                ).scalar_one()
                                if downgrade_waiting:
                                    break
                                sleep(0.01)
                        assert downgrade_waiting
                    finally:
                        blocker_transaction.commit()
                    scheduled = pending_schedule.result(timeout=10)
                    assert [change.ordinal for change in scheduled.changes] == [1]
                    with pytest.raises(
                        RuntimeError,
                        match=(
                            "mixed File upsert scheduling downgrade requires no "
                            "retained"
                        ),
                    ):
                        pending_downgrade.result(timeout=10)
            finally:
                if blocker_transaction.is_active:
                    blocker_transaction.rollback()
        assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    finally:
        command.upgrade(alembic_configuration, "head")
        engine.dispose()
        _delete_issue_27_upgrade_fixture(
            migration_configuration,
            scenario.organization_id,
        )


def test_in_flight_old_scheduler_fails_closed_when_downgrade_wins_fence(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
) -> None:
    """An old function body cannot write after rollback replaces its generation."""

    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        issue_lease=False,
    )
    provider_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    checkpoint_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32, 64)))
    authority = ControlOperatorAuthority(
        _ControlAuthenticator(scenario.organization_id),
        call_ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    )
    control = ContextControl(
        store=PostgreSQLControlStore(
            guarded_control_engine,
            clock=lambda: NOW,
            file_import_receiver=scenario.receiver,
            file_change_checkpoint_signing_key=checkpoint_key,
        ),
        authority=authority,
        clock=lambda: NOW,
        file_change_proofs=FileChangeControlProofs(
            provider_verification_key=provider_key.public_key()
        ),
    )
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.ACTIVATE_FILE_CHANGE_FEED,
        request_id="mixed-downgrade-losing-activate-v3",
    ) as call:
        control.activate_file_change_feed(
            call,
            ActivateFileChangeFeed(scenario.source_ref),
        )
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.ACTIVATE_FILE_DELETE_OBSERVATIONS,
        request_id="mixed-downgrade-losing-activate-v4",
    ) as call:
        v4 = control.activate_file_delete_observations(
            call,
            ActivateFileDeleteObservations(scenario.source_ref),
        )
    (scenario.root / "removed.md").write_bytes(b"# Removed\n\nBaseline.\n")
    provider = FileChangeProvider(
        FileRootRegistry(
            {scenario.root_ref: scenario.root},
            limits=FileReadLimits(max_file_bytes=1_024 * 1_024),
        ),
        proofs=FileChangeProviderProofs(
            provider_signing_key=provider_key,
            checkpoint_verification_key=checkpoint_key.public_key(),
        ),
    )
    source = FileChangeSource(scenario.organization_id, v4.active_version)
    baseline = provider.read_changes(source, InitialScan(), ChangeLimit(2))
    assert type(baseline) is ProviderOk
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        request_id="mixed-downgrade-losing-accept-baseline",
    ) as call:
        accepted_baseline = control.accept_file_change_page(call, baseline.value)
    complete_baseline = FileChangeBaseline(
        reference=FileChangeBaselineRef(
            source_version_ref=accepted_baseline.source_version_ref,
            scan_ref=accepted_baseline.scan_ref,
            scan_epoch=accepted_baseline.scan_epoch,
            page_ref=accepted_baseline.page_ref,
            checkpoint_ref=accepted_baseline.checkpoint_ref,
            sequence=accepted_baseline.sequence,
        ),
        entries=tuple(
            FileChangeBaselineEntry(
                kind=change.kind,
                path=change.path,
                content_sha256=change.content_sha256,
                content_length=change.content_length,
            )
            for change in baseline.value.changes
        ),
    )
    (scenario.root / "handbook.md").write_bytes(NEW_MARKDOWN)
    (scenario.root / "removed.md").unlink()
    mixed = provider.read_changes(
        FileChangeSource(
            scenario.organization_id,
            v4.active_version,
            scan_head=accepted_baseline.scan_head,
            complete_baseline=complete_baseline,
        ),
        InitialScan(),
        ChangeLimit(2),
    )
    assert type(mixed) is ProviderOk
    assert {change.kind.value for change in mixed.value.changes} == {
        "upsert",
        "delete",
    }
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        request_id="mixed-downgrade-losing-accept-current",
    ) as call:
        accepted = control.accept_file_change_page(call, mixed.value)
    schedule = ScheduleFileChangePage(
        accepted.source_ref,
        accepted.source_version_ref,
        accepted.page_ref,
        FileImportAudience(
            "principal:file-reader",
            scenario.membership_id,
            1,
        ),
    )

    def schedule_page() -> ScheduledFileChangePage:
        with authority.authorize(
            opaque_credential="control-secret",
            operation=ControlOperation.SCHEDULE_FILE_CHANGE_PAGE,
            request_id="mixed-downgrade-losing-old-scheduler",
        ) as call:
            return control.schedule_file_change_page(call, schedule)

    engine = create_database_engine(migration_configuration)
    alembic_configuration = Config(ROOT / "alembic.ini")
    migration_fence_key = "context-engine.file-change-scheduling-migration-fence"
    try:
        with engine.connect() as blocker:
            blocker_transaction = blocker.begin()
            try:
                blocker.execute(text("LOCK TABLE context_source IN ACCESS SHARE MODE"))
                with ThreadPoolExecutor(max_workers=2) as executor:
                    pending_downgrade = executor.submit(
                        command.downgrade,
                        alembic_configuration,
                        "20260725_0031",
                    )
                    try:
                        with engine.connect() as observer:
                            deadline = monotonic() + 10
                            while monotonic() < deadline:
                                downgrade_holds_fence = observer.execute(
                                    text(
                                        """
                                        SELECT EXISTS (
                                            SELECT 1
                                            FROM pg_locks AS advisory
                                            JOIN pg_locks AS relation_lock
                                              ON relation_lock.pid = advisory.pid
                                             AND relation_lock.database =
                                                 advisory.database
                                            WHERE advisory.locktype = 'advisory'
                                              AND advisory.mode = 'ExclusiveLock'
                                              AND advisory.granted IS TRUE
                                              AND advisory.database = (
                                                SELECT database.oid
                                                FROM pg_database AS database
                                                WHERE database.datname =
                                                  current_database()
                                              )
                                              AND advisory.classid = (
                                                (hashtextextended(:lock_key, 0)
                                                  >> 32) & 4294967295
                                              )::oid
                                              AND advisory.objid = (
                                                hashtextextended(:lock_key, 0)
                                                  & 4294967295
                                              )::oid
                                              AND advisory.objsubid = 1
                                              AND relation_lock.relation =
                                                'public.context_source'::regclass
                                              AND relation_lock.mode =
                                                'AccessExclusiveLock'
                                              AND relation_lock.granted IS FALSE
                                        )
                                        """
                                    ),
                                    {"lock_key": migration_fence_key},
                                ).scalar_one()
                                if downgrade_holds_fence:
                                    break
                                sleep(0.01)
                        assert downgrade_holds_fence
                        pending_schedule = executor.submit(schedule_page)
                        with engine.connect() as observer:
                            deadline = monotonic() + 10
                            while monotonic() < deadline:
                                scheduler_waiting = observer.execute(
                                    text(
                                        """
                                        SELECT EXISTS (
                                            SELECT 1 FROM pg_locks AS waiting
                                            JOIN pg_locks AS held
                                              ON held.locktype = waiting.locktype
                                             AND held.database = waiting.database
                                             AND held.classid = waiting.classid
                                             AND held.objid = waiting.objid
                                             AND held.objsubid = waiting.objsubid
                                        WHERE waiting.locktype = 'advisory'
                                          AND waiting.mode = 'ShareLock'
                                          AND waiting.granted IS FALSE
                                          AND waiting.database = (
                                            SELECT database.oid
                                            FROM pg_database AS database
                                            WHERE database.datname =
                                              current_database()
                                          )
                                          AND waiting.classid = (
                                            (hashtextextended(:lock_key, 0)
                                              >> 32) & 4294967295
                                          )::oid
                                          AND waiting.objid = (
                                            hashtextextended(:lock_key, 0)
                                              & 4294967295
                                          )::oid
                                          AND waiting.objsubid = 1
                                          AND held.mode = 'ExclusiveLock'
                                          AND held.granted IS TRUE
                                    )
                                    """
                                    ),
                                    {"lock_key": migration_fence_key},
                                ).scalar_one()
                                if scheduler_waiting:
                                    break
                                sleep(0.01)
                        assert scheduler_waiting
                    finally:
                        blocker_transaction.commit()
                    pending_downgrade.result(timeout=10)
                    with pytest.raises(SourceNotAvailable):
                        pending_schedule.result(timeout=10)
            finally:
                if blocker_transaction.is_active:
                    blocker_transaction.rollback()
        assert _revision_rows(migration_configuration) == ["20260725_0031"]
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM file_acquisition "
                        "WHERE organization_id = :organization_id "
                        "AND change_page_ref IS NOT NULL"
                    ),
                    {"organization_id": scenario.organization_id},
                ).scalar_one()
                == 0
            )
    finally:
        command.upgrade(alembic_configuration, "head")
        engine.dispose()
        _delete_issue_27_upgrade_fixture(
            migration_configuration,
            scenario.organization_id,
        )


def test_file_delete_observation_revision_refuses_accepted_baseline_downgrade(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
) -> None:
    """Issue #85 never deletes an accepted v4 baseline during rollback."""

    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        issue_lease=False,
    )
    provider_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    checkpoint_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32, 64)))
    authority = ControlOperatorAuthority(
        _ControlAuthenticator(scenario.organization_id),
        call_ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    )
    control = ContextControl(
        store=PostgreSQLControlStore(
            guarded_control_engine,
            clock=lambda: NOW,
            file_import_receiver=scenario.receiver,
            file_change_checkpoint_signing_key=checkpoint_key,
        ),
        authority=authority,
        clock=lambda: NOW,
        file_change_proofs=FileChangeControlProofs(
            provider_verification_key=provider_key.public_key()
        ),
    )
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.ACTIVATE_FILE_CHANGE_FEED,
        request_id="migration-v4-activate-v3",
    ) as call:
        control.activate_file_change_feed(
            call,
            ActivateFileChangeFeed(scenario.source_ref),
        )
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.ACTIVATE_FILE_DELETE_OBSERVATIONS,
        request_id="migration-v4-activate",
    ) as call:
        v4 = control.activate_file_delete_observations(
            call,
            ActivateFileDeleteObservations(scenario.source_ref),
        )
    page = FileChangeProvider(
        FileRootRegistry(
            {scenario.root_ref: scenario.root},
            limits=FileReadLimits(max_file_bytes=1_024 * 1_024),
        ),
        proofs=FileChangeProviderProofs(
            provider_signing_key=provider_key,
            checkpoint_verification_key=checkpoint_key.public_key(),
        ),
    ).read_changes(
        FileChangeSource(scenario.organization_id, v4.active_version),
        InitialScan(),
        ChangeLimit(1),
    )
    assert type(page) is ProviderOk
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        request_id="migration-v4-accept-baseline",
    ) as call:
        accepted = control.accept_file_change_page(call, page.value)

    with pytest.raises(
        RuntimeError,
        match="requires no accepted v4 page",
    ):
        downgrade_revision(migration_configuration, "20260725_0030")
    assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            retained = connection.execute(
                text(
                    """
                    SELECT page.page_ref, binding.baseline_page_ref,
                           checkpoint.checkpoint_ref
                    FROM file_source_delete_observation_page AS binding
                    JOIN file_source_change_page AS page
                      ON page.organization_id = binding.organization_id
                     AND page.source_id = binding.source_id
                     AND page.source_version_id = binding.source_version_id
                     AND page.page_ref = binding.page_ref
                    JOIN file_source_acquisition_checkpoint AS checkpoint
                      ON checkpoint.organization_id = page.organization_id
                     AND checkpoint.source_id = page.source_id
                     AND checkpoint.source_version_id = page.source_version_id
                     AND checkpoint.change_page_ref = page.page_ref
                    WHERE page.organization_id = :organization_id
                      AND page.source_id = :source_id
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "source_id": scenario.source_ref.value,
                },
            ).one()
        assert tuple(retained) == (
            accepted.page_ref,
            None,
            accepted.checkpoint_ref,
        )
    finally:
        engine.dispose()
        _delete_issue_27_upgrade_fixture(
            migration_configuration,
            scenario.organization_id,
        )


def test_file_delete_observation_revision_refuses_retained_action_ticket(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
) -> None:
    """Issue #85 rollback refuses v4 ActionTicket lineage before mutation."""

    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        issue_lease=False,
    )
    authority = ControlOperatorAuthority(
        _ControlAuthenticator(scenario.organization_id),
        call_ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    )
    control = ContextControl(
        store=PostgreSQLControlStore(
            guarded_control_engine,
            clock=lambda: NOW,
        ),
        authority=authority,
        clock=lambda: NOW,
    )
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.ACTIVATE_FILE_CHANGE_FEED,
        request_id="migration-v4-ticket-activate-v3",
    ) as call:
        control.activate_file_change_feed(
            call,
            ActivateFileChangeFeed(scenario.source_ref),
        )
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.ACTIVATE_FILE_DELETE_OBSERVATIONS,
        request_id="migration-v4-ticket-activate",
    ) as call:
        v4 = control.activate_file_delete_observations(
            call,
            ActivateFileDeleteObservations(scenario.source_ref),
        )

    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            identity = connection.execute(
                text(
                    """
                    SELECT membership.user_id, epoch.policy_epoch
                    FROM membership
                    JOIN organization_policy_epoch AS epoch
                      ON epoch.organization_id = membership.organization_id
                    WHERE membership.organization_id = :organization_id
                      AND membership.membership_id = :membership_id
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "membership_id": scenario.membership_id,
                },
            ).one()
            connection.execute(
                text(
                    """
                    INSERT INTO action_delivery_attempt (
                        organization_id, delivery_attempt_ref,
                        authenticated_service_digest, delivery_evidence_digest,
                        authentication_binding_digest, user_id, membership_id,
                        membership_version, destination_digest, consumer_digest,
                        purpose_digest, audience_digest, identity_digest,
                        policy_epoch, profile_ref, retention_policy_ref,
                        created_at, retain_until
                    ) VALUES (
                        :organization_id, :attempt_ref,
                        :digest, :digest, :digest, :user_id, :membership_id,
                        1, :digest, :digest, :digest, :digest, :digest,
                        :policy_epoch, 'private-action-prepare-v1',
                        'action-digest-audit-retention-v1', :created_at,
                        :retain_until
                    )
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "attempt_ref": "dla_" + "8" * 32,
                    "digest": b"\x08" * 32,
                    "user_id": identity.user_id,
                    "membership_id": scenario.membership_id,
                    "policy_epoch": identity.policy_epoch,
                    "created_at": NOW,
                    "retain_until": NOW + timedelta(days=1),
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO action_ticket (
                        organization_id, ticket_ref, delivery_attempt_ref,
                        operation, ticket_audience, payload_digest,
                        idempotency_digest, approval_digest, approval_tier,
                        source_id, source_version_id, policy_epoch,
                        signing_key_version, profile_ref, state, issued_at,
                        expires_at, retention_policy_ref, retain_until
                    ) VALUES (
                        :organization_id, :ticket_ref, :attempt_ref,
                        'create_placeholder',
                        'private-effect:create-placeholder', :payload_digest,
                        :idempotency_digest, :approval_digest,
                        'preapproved_private_delivery_v1', :source_id,
                        :source_version_id, :policy_epoch, 1,
                        'private-action-prepare-v1', 'prepared', :issued_at,
                        :expires_at, 'action-digest-audit-retention-v1',
                        :retain_until
                    )
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "ticket_ref": "act_" + "8" * 32,
                    "attempt_ref": "dla_" + "8" * 32,
                    "payload_digest": b"\x01" * 32,
                    "idempotency_digest": b"\x02" * 32,
                    "approval_digest": b"\x03" * 32,
                    "source_id": scenario.source_ref.value,
                    "source_version_id": v4.active_version.version_ref,
                    "policy_epoch": identity.policy_epoch,
                    "issued_at": NOW,
                    "expires_at": NOW + timedelta(minutes=1),
                    "retain_until": NOW + timedelta(days=1),
                },
            )

        with pytest.raises(
            RuntimeError,
            match="requires no v4 ActionTicket lineage",
        ):
            downgrade_revision(migration_configuration, "20260725_0030")
        assert _revision_rows(migration_configuration) == [HEAD_REVISION]
        with engine.connect() as connection:
            retained = connection.execute(
                text(
                    """
                    SELECT source.active_version_id, ticket.source_version_id
                    FROM context_source AS source
                    JOIN action_ticket AS ticket
                      ON ticket.organization_id = source.organization_id
                     AND ticket.source_id = source.source_id
                    WHERE source.organization_id = :organization_id
                      AND source.source_id = :source_id
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "source_id": scenario.source_ref.value,
                },
            ).one()
        assert tuple(retained) == (
            v4.active_version.version_ref,
            v4.active_version.version_ref,
        )
    finally:
        engine.dispose()
        _delete_issue_27_upgrade_fixture(
            migration_configuration,
            scenario.organization_id,
        )


def test_file_change_scheduling_revision_downgrades_and_reapplies_cleanly(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    """Issue #83 rollback preserves the existing manual File import path."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
    )
    try:
        try:
            command.downgrade(alembic_configuration, "20260725_0028")
            assert _revision_rows(migration_configuration) == ["20260725_0028"]
            engine = create_database_engine(migration_configuration)
            try:
                with engine.connect() as connection:
                    acquisition_columns = set(
                        connection.execute(
                            text(
                                """
                                SELECT column_name
                                FROM information_schema.columns
                                WHERE table_schema = 'public'
                                  AND table_name = 'file_acquisition'
                                """
                            )
                        ).scalars()
                    )
                    scheduling_functions = connection.execute(
                        text(
                            """
                            SELECT count(*)
                            FROM pg_catalog.pg_proc AS procedure
                            JOIN pg_catalog.pg_namespace AS namespace
                              ON namespace.oid = procedure.pronamespace
                            WHERE namespace.nspname = 'public'
                              AND procedure.proname =
                                  'context_control_schedule_file_change_page'
                            """
                        )
                    ).scalar_one()
                assert {
                    "change_page_ref",
                    "change_ordinal",
                    "expected_content_sha256",
                    "expected_content_length",
                }.isdisjoint(acquisition_columns)
                assert scheduling_functions == 0
            finally:
                engine.dispose()
            assert scenario.token is not None
            published = _run_file_import(
                scenario,
                scenario.prepared,
                scenario.token,
                guarded_worker_engine,
            )
            assert published.outcome == "published"
        finally:
            command.upgrade(alembic_configuration, "head")

        assert _revision_rows(migration_configuration) == [HEAD_REVISION]
        engine = create_database_engine(migration_configuration)
        try:
            with engine.connect() as connection:
                acquisition_columns = set(
                    connection.execute(
                        text(
                            """
                            SELECT column_name
                            FROM information_schema.columns
                            WHERE table_schema = 'public'
                              AND table_name = 'file_acquisition'
                            """
                        )
                    ).scalars()
                )
                public_execute = connection.execute(
                    text(
                        """
                        SELECT pg_catalog.has_function_privilege(
                            'public',
                            'public.context_control_schedule_file_change_page('
                            'uuid, uuid, uuid, text, text, uuid, bigint, uuid)',
                            'EXECUTE'
                        )
                        """
                    )
                ).scalar_one()
                epoch_fence = connection.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM pg_catalog.pg_trigger AS trigger
                        JOIN pg_catalog.pg_proc AS procedure
                          ON procedure.oid = trigger.tgfoid
                        WHERE trigger.tgrelid =
                              'file_source_publish_watermark'::regclass
                          AND trigger.tgname =
                              'file_source_publish_watermark_current_scheduled_epoch'
                          AND procedure.proname =
                              'context_file_source_fence_scheduled_publication_epoch'
                          AND trigger.tgenabled = 'O'
                        """
                    )
                ).scalar_one()
            assert {
                "change_page_ref",
                "change_ordinal",
                "expected_content_sha256",
                "expected_content_length",
            } <= acquisition_columns
            assert public_execute is False
            assert epoch_fence == 1
        finally:
            engine.dispose()
    finally:
        _delete_issue_27_upgrade_fixture(
            migration_configuration,
            scenario.organization_id,
        )


def test_file_change_scheduling_revision_refuses_downgrade_with_new_manual_path(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
) -> None:
    """Issue #83 rollback cannot strand a newly valid manual File import."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
    )
    (scenario.root / ".md").write_bytes(b"# Dotfile\n")
    _prepare_repeat_file_import(
        scenario,
        guarded_control_engine,
        idempotency_key="newly-valid-dotfile",
        path=FileImportPath(".md"),
    )
    try:
        with pytest.raises(
            RuntimeError,
            match="newer manual File import paths",
        ):
            downgrade_revision(migration_configuration, "20260725_0029")
        assert _revision_rows(migration_configuration) == [HEAD_REVISION]
        engine = create_database_engine(migration_configuration)
        try:
            with engine.connect() as connection:
                retained_path = connection.execute(
                    text(
                        "SELECT relative_path FROM file_acquisition "
                        "WHERE organization_id = :org AND relative_path = '.md'"
                    ),
                    {"org": scenario.organization_id},
                ).scalar_one()
            assert retained_path == ".md"
        finally:
            engine.dispose()
    finally:
        command.upgrade(alembic_configuration, "head")
        _delete_issue_27_upgrade_fixture(
            migration_configuration,
            scenario.organization_id,
        )


def test_recursive_file_path_revision_downgrades_and_reapplies_when_empty(
    migration_configuration: DatabaseConfiguration,
) -> None:
    alembic_configuration = Config(ROOT / "alembic.ini")

    try:
        command.downgrade(alembic_configuration, "20260726_0034")
        assert _revision_rows(migration_configuration) == ["20260726_0034"]
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == [HEAD_REVISION]


def test_fragment_embedding_revision_downgrades_and_reapplies_when_empty(
    migration_configuration: DatabaseConfiguration,
) -> None:
    alembic_configuration = Config(ROOT / "alembic.ini")

    try:
        command.downgrade(alembic_configuration, "20260726_0035")
        assert _revision_rows(migration_configuration) == ["20260726_0035"]
        engine = create_database_engine(migration_configuration)
        try:
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        text(
                            "SELECT to_regclass("
                            "'public.ix_context_fragment_embedding_hnsw')"
                        )
                    ).scalar_one()
                    is None
                )
        finally:
            engine.dispose()
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == [HEAD_REVISION]


def test_direct_file_change_activation_revision_downgrades_and_reapplies(
    migration_configuration: DatabaseConfiguration,
) -> None:
    alembic_configuration = Config(ROOT / "alembic.ini")
    regprocedure = "public.context_control_activate_file_change_feed(uuid, uuid, uuid)"

    def definition() -> str:
        engine = create_database_engine(migration_configuration)
        try:
            with engine.connect() as connection:
                return str(
                    connection.execute(
                        text(
                            f"SELECT pg_get_functiondef('{regprocedure}'::regprocedure)"
                        )
                    ).scalar_one()
                )
        finally:
            engine.dispose()

    assert "selected_capabilities NOT IN" in definition()
    try:
        command.downgrade(alembic_configuration, "20260726_0036")
        assert _revision_rows(migration_configuration) == ["20260726_0036"]
        downgraded = definition()
        assert "selected_capabilities <>" in downgraded
        assert "selected_capabilities NOT IN" not in downgraded
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    assert "selected_capabilities NOT IN" in definition()


def test_pending_file_schedule_projection_revision_downgrades_and_reapplies(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Issue #112 adds only one reversible read-only reconciliation function."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    try:
        command.downgrade(alembic_configuration, "20260727_0037")
        assert _revision_rows(migration_configuration) == ["20260727_0037"]
        engine = create_database_engine(migration_configuration)
        try:
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        text(
                            """
                        SELECT to_regprocedure(
                          'public.context_control_read_pending_file_change_schedules(uuid,uuid)'
                        ) IS NULL
                        """
                        )
                    ).scalar_one()
                    is True
                )
        finally:
            engine.dispose()
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT ARRAY[
                      has_function_privilege(
                        'context_engine_control',
                        'public.context_control_read_pending_file_change_schedules(uuid,uuid)',
                        'EXECUTE'
                      ),
                      has_function_privilege(
                        'context_engine_runtime',
                        'public.context_control_read_pending_file_change_schedules(uuid,uuid)',
                        'EXECUTE'
                      ),
                      has_function_privilege(
                        'context_engine_worker',
                        'public.context_control_read_pending_file_change_schedules(uuid,uuid)',
                        'EXECUTE'
                      )
                    ]
                    """
                )
            ).scalar_one() == [True, False, False]
    finally:
        engine.dispose()


def test_file_source_status_revision_downgrades_and_reapplies_when_empty(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Issue #113 adds closed status functions and one nullable category."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    try:
        command.downgrade(alembic_configuration, "20260727_0038")
        assert _revision_rows(migration_configuration) == ["20260727_0038"]
        engine = create_database_engine(migration_configuration)
        try:
            with engine.connect() as connection:
                assert connection.execute(
                    text(
                        """
                        SELECT ARRAY[
                          to_regprocedure(
                            'public.context_control_read_file_source_status(uuid,uuid)'
                          ) IS NULL,
                          to_regprocedure(
                            'public.context_worker_fail_file_import_with_category(uuid,uuid,uuid,text,text,bigint,bigint,bytea,timestamptz,timestamptz)'
                          ) IS NULL,
                          NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_schema = 'public'
                              AND table_name = 'file_import_job'
                              AND column_name = 'compilation_refusal_category'
                          )
                        ]
                        """
                    )
                ).scalar_one() == [True, True, True]
        finally:
            engine.dispose()
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT ARRAY[
                      has_function_privilege(
                        'context_engine_control',
                        'public.context_control_read_file_source_status(uuid,uuid)',
                        'EXECUTE'
                      ),
                      has_function_privilege(
                        'context_engine_runtime',
                        'public.context_control_read_file_source_status(uuid,uuid)',
                        'EXECUTE'
                      ),
                      has_function_privilege(
                        'context_engine_worker',
                        'public.context_worker_fail_file_import_with_category(uuid,uuid,uuid,text,text,bigint,bigint,bytea,timestamptz,timestamptz)',
                        'EXECUTE'
                      ),
                      has_function_privilege(
                        'context_engine_control',
                        'public.context_worker_fail_file_import_with_category(uuid,uuid,uuid,text,text,bigint,bigint,bytea,timestamptz,timestamptz)',
                        'EXECUTE'
                      ),
                      has_schema_privilege(
                        'context_engine_worker_lease_definer',
                        'public',
                        'CREATE'
                      )
                    ]
                    """
                )
            ).scalar_one() == [True, False, True, False, False]
    finally:
        engine.dispose()


def test_revision_link_graph_revision_downgrades_and_reapplies_when_empty(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Issue #151 installs and removes the empty graph contract exactly."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    assert _revision_rows(migration_configuration) == ["20260731_0051"]
    assert "revision_link_edge" not in _application_tables(migration_configuration)
    try:
        for _ in range(2):
            command.upgrade(alembic_configuration, "head")
            assert _revision_rows(migration_configuration) == [HEAD_REVISION]
            assert "revision_link_edge" in _application_tables(
                migration_configuration
            )
            engine = create_database_engine(migration_configuration)
            try:
                with engine.connect() as connection:
                    assert connection.execute(
                        text(
                            """
                            SELECT ARRAY[
                              to_regprocedure(
                                'public.context_runtime_resolve_one_hop_graph(uuid[],text[],text[],uuid[],text[],integer,integer)'
                              ) IS NOT NULL,
                              to_regprocedure(
                                'public.context_internal_revision_link_edges_match(uuid,text,uuid,jsonb)'
                              ) IS NOT NULL
                            ]
                            """
                        )
                    ).scalar_one() == [True, True]
            finally:
                engine.dispose()
            command.downgrade(alembic_configuration, "20260731_0051")
            assert _revision_rows(migration_configuration) == ["20260731_0051"]
            assert "revision_link_edge" not in _application_tables(
                migration_configuration
            )
    finally:
        command.upgrade(alembic_configuration, "head")
    assert _revision_rows(migration_configuration) == [HEAD_REVISION]


def test_revision_link_graph_downgrade_refuses_retained_v3_state(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    """A retained rich Revision prevents loss of its graph semantics."""

    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=b"# Synthetic retained graph\n\nLink [[adjacent]].\n",
    )
    assert scenario.token is not None
    try:
        _run_file_import(
            scenario,
            scenario.prepared,
            scenario.token,
            guarded_worker_engine,
            config_version="markdown-config-v3",
        )

        with pytest.raises(
            RuntimeError,
            match="rich Markdown graph downgrade requires no retained v3 state",
        ):
            downgrade_revision(migration_configuration, "20260731_0052")

        assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    finally:
        _delete_file_import_scenario(
            migration_configuration,
            scenario.organization_id,
        )


def test_embedding_profile_revision_downgrades_and_reapplies_cleanly(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Issue #147 installs and removes exact embedding lineage symmetrically."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    assert _revision_rows(migration_configuration) == ["20260731_0052"]
    engine = create_database_engine(migration_configuration)
    try:
        for _ in range(2):
            command.upgrade(alembic_configuration, "head")
            assert _revision_rows(migration_configuration) == [HEAD_REVISION]
            with engine.connect() as connection:
                columns = {
                    (row.table_name, row.column_name)
                    for row in connection.execute(
                        text(
                            """
                            SELECT table_name, column_name
                            FROM information_schema.columns
                            WHERE table_schema = 'public'
                              AND table_name IN (
                                  'context_fragment',
                                  'file_publication_recovery',
                                  'release_manifest'
                              )
                              AND column_name IN (
                                  'embedding_profile_document',
                                  'embedding_profile_digest'
                              )
                            """
                        )
                    )
                }
                policies = set(
                    connection.execute(
                        text(
                            """
                            SELECT policyname
                            FROM pg_policies
                            WHERE schemaname = 'public'
                              AND tablename = 'context_fragment'
                              AND policyname =
                                  'context_fragment_release_definer_select'
                            """
                        )
                    ).scalars()
                )
                functions = connection.execute(
                    text(
                        """
                        SELECT ARRAY[
                          to_regprocedure(
                            'public.context_worker_classify_file_import_internal(uuid,uuid,uuid,text,text,text,text,text,text,text,bigint,bytea,timestamptz,timestamptz)'
                          ) IS NOT NULL,
                          to_regprocedure(
                            'public.context_worker_acquire_file_publication(uuid,uuid,uuid,text,text,uuid,text,text,text,text,text,text,jsonb,jsonb,bigint,bigint,bytea,timestamptz,timestamptz)'
                          ) IS NOT NULL,
                          to_regprocedure(
                            'public.context_worker_prepare_file_publication(uuid,uuid,uuid,text,text,uuid,text,jsonb,jsonb,jsonb,text,bigint,bigint,bytea,timestamptz,timestamptz)'
                          ) IS NOT NULL
                        ]
                        """
                    )
                ).scalar_one()
            assert columns == {
                ("context_fragment", "embedding_profile_digest"),
                ("file_publication_recovery", "embedding_profile_digest"),
                ("release_manifest", "embedding_profile_document"),
                ("release_manifest", "embedding_profile_digest"),
            }
            assert policies == {"context_fragment_release_definer_select"}
            assert functions == [True, True, True]

            command.downgrade(alembic_configuration, "20260731_0052")
            assert _revision_rows(migration_configuration) == ["20260731_0052"]
            with engine.connect() as connection:
                assert connection.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name IN (
                              'context_fragment',
                              'file_publication_recovery',
                              'release_manifest'
                          )
                          AND column_name IN (
                              'embedding_profile_document',
                              'embedding_profile_digest'
                          )
                        """
                    )
                ).scalar_one() == 0
    finally:
        command.upgrade(alembic_configuration, "head")
        engine.dispose()
    assert _revision_rows(migration_configuration) == [HEAD_REVISION]


def test_embedding_profile_upgrade_refuses_unproven_existing_vectors(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Issue #147 never manufactures twin provenance for historical vectors."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    organization_id = uuid4()
    revision_id = uuid4()
    resource_ref = f"resource:unproven-embedding:{uuid4()}"
    parameters = {
        "organization_id": organization_id,
        "revision_id": revision_id,
        "resource_ref": resource_ref,
        "embedding": "["
        + ",".join(
            repr(value)
            for value in DeterministicEmbeddingTwin().embed(("unproven",))[0]
        )
        + "]",
    }
    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO organization (organization_id) "
                    "VALUES (:organization_id)"
                ),
                parameters,
            )
            connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            connection.execute(
                text(
                    """
                    INSERT INTO context_resource (
                        organization_id, resource_ref, source_ref,
                        active_revision_id, tombstoned
                    ) VALUES (
                        :organization_id, :resource_ref, 'source:unproven',
                        :revision_id, false
                    )
                    """
                ),
                parameters,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO context_revision (
                        organization_id, resource_ref, revision_id
                    ) VALUES (
                        :organization_id, :resource_ref, :revision_id
                    )
                    """
                ),
                parameters,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO context_fragment (
                        organization_id, resource_ref, revision_id,
                        fragment_ref, ordinal, projection_kind, content,
                        embedding
                    ) VALUES (
                        :organization_id, :resource_ref, :revision_id,
                        'fragment:unproven', 0, 'body', 'unproven',
                        CAST(:embedding AS vector)
                    )
                    """
                ),
                parameters,
            )

        with pytest.raises(
            RuntimeError,
            match="embedding profile upgrade requires a provenance-free corpus",
        ):
            command.upgrade(alembic_configuration, "head")
        assert _revision_rows(migration_configuration) == ["20260731_0052"]
    finally:
        engine.dispose()


def test_embedding_profile_upgrade_serializes_with_release_promotion_lock_order(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """0053 cannot deadlock with promotion's Release-then-Fragment reads."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    assert _revision_rows(migration_configuration) == ["20260731_0052"]
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as promotion_connection:
            promotion_transaction = promotion_connection.begin()
            try:
                promotion_connection.execute(
                    text("SELECT 1 FROM release_manifest LIMIT 0")
                )
                with ThreadPoolExecutor(max_workers=1) as executor:
                    pending_upgrade = executor.submit(
                        command.upgrade,
                        alembic_configuration,
                        "head",
                    )
                    upgrade_waiting = False
                    with engine.connect() as observer:
                        deadline = monotonic() + 10
                        while monotonic() < deadline:
                            upgrade_waiting = observer.execute(
                                text(
                                    """
                                    SELECT EXISTS (
                                        SELECT 1 FROM pg_locks
                                        WHERE database = (
                                            SELECT oid FROM pg_database
                                            WHERE datname = current_database()
                                        )
                                          AND relation =
                                              'release_manifest'::regclass
                                          AND mode = 'AccessExclusiveLock'
                                          AND granted IS FALSE
                                    )
                                    """
                                )
                            ).scalar_one()
                            if upgrade_waiting or pending_upgrade.done():
                                break
                            sleep(0.01)
                    assert upgrade_waiting is True
                    assert pending_upgrade.done() is False

                    promotion_connection.execute(
                        text("SELECT 1 FROM context_fragment LIMIT 0")
                    )
                    promotion_transaction.commit()
                    pending_upgrade.result(timeout=10)
            finally:
                if promotion_transaction.is_active:
                    promotion_transaction.rollback()
        assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    finally:
        engine.dispose()


def test_embedding_profile_downgrade_refuses_retained_qwen_lineage(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """A Qwen-bound Release cannot lose its exact provider identity."""

    organization_id = uuid4()
    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO organization (organization_id) VALUES (:org)"
                ),
                {"org": organization_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO release_manifest (
                        organization_id, manifest_ref, manifest_digest,
                        lineage_digest, content_profile_ref,
                        content_profile_digest, content_schema_ref,
                        index_profile_ref, index_profile_digest,
                        index_content_profile_digest,
                        index_content_schema_ref, index_schema_ref,
                        embedding_profile_document, embedding_profile_digest,
                        runtime_profile_ref, runtime_profile_digest,
                        runtime_content_profile_digest,
                        runtime_index_profile_digest,
                        runtime_content_schema_ref, runtime_index_schema_ref,
                        runtime_tokenizer_ref, runtime_package_schema_ref,
                        curation_profile_ref, curation_profile_digest,
                        curation_mode, compatible_revision_refs,
                        active_revision_refs
                    ) VALUES (
                        :org, 'manifest:qwen:downgrade', :digest, :digest,
                        'content-profile', :digest, 'content-schema',
                        'index-profile', :digest, :digest, 'content-schema',
                        'index-schema', CAST(:profile_document AS jsonb),
                        :profile_digest, 'runtime-profile', :digest, :digest,
                        :digest, 'content-schema', 'index-schema', 'tokenizer',
                        'package-schema', 'curation-profile', :digest,
                        'curation_off', '[]'::jsonb, '[]'::jsonb
                    )
                    """
                ),
                {
                    "org": organization_id,
                    "digest": "a" * 64,
                    "profile_document": QWEN3_EMBEDDING_PROFILE.canonical_json(),
                    "profile_digest": QWEN3_EMBEDDING_PROFILE.profile_digest,
                },
            )

        with pytest.raises(
            RuntimeError,
            match="embedding profile downgrade requires twin-only lineage",
        ):
            downgrade_revision(migration_configuration, "20260802_0053")
        assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE release_manifest DISABLE TRIGGER "
                    "release_manifest_reject_mutation"
                )
            )
            connection.execute(
                text(
                    "DELETE FROM release_manifest WHERE organization_id = :org"
                ),
                {"org": organization_id},
            )
            connection.execute(
                text(
                    "ALTER TABLE release_manifest ENABLE TRIGGER "
                    "release_manifest_reject_mutation"
                )
            )
            connection.execute(
                text("DELETE FROM organization WHERE organization_id = :org"),
                {"org": organization_id},
            )
        engine.dispose()


def test_file_scan_bound_revision_downgrades_and_reapplies_when_default_only(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Issue #135 removes provenance only when no non-default fact is retained."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    try:
        command.downgrade(alembic_configuration, "20260730_0044")
        assert _revision_rows(migration_configuration) == ["20260730_0044"]
        engine = create_database_engine(migration_configuration)
        try:
            with engine.connect() as connection:
                assert connection.execute(
                    text(
                        """
                        SELECT ARRAY[
                          to_regprocedure(
                            'public.context_control_accept_bounded_file_delete_observation_page(uuid,uuid,uuid,text,uuid,smallint,text,text,text,bigint,uuid,jsonb,boolean,integer,jsonb)'
                          ) IS NULL,
                          to_regprocedure(
                            'public.context_control_report_file_scan_bound_refusal(uuid,uuid,integer)'
                          ) IS NULL,
                          NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_schema = 'public'
                              AND table_name = 'file_source_change_page'
                              AND column_name = 'scan_bound'
                          )
                        ]
                        """
                    )
                ).scalar_one() == [True, True, True]
        finally:
            engine.dispose()
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == [HEAD_REVISION]


def test_file_scan_bound_acceptance_fails_closed_after_downgrade(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
) -> None:
    """A store bound to provenance authority never falls back after rollback."""

    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        issue_lease=False,
    )
    provider_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    checkpoint_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32, 64)))
    authority = ControlOperatorAuthority(
        _ControlAuthenticator(scenario.organization_id),
        call_ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    )
    control = ContextControl(
        store=PostgreSQLControlStore(
            guarded_control_engine,
            clock=lambda: NOW,
            file_import_receiver=scenario.receiver,
            file_change_checkpoint_signing_key=checkpoint_key,
        ),
        authority=authority,
        clock=lambda: NOW,
        file_change_proofs=FileChangeControlProofs(
            provider_verification_key=provider_key.public_key()
        ),
    )
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.ACTIVATE_FILE_CHANGE_FEED,
        request_id="bound-rollback-activate-v3",
    ) as call:
        control.activate_file_change_feed(
            call,
            ActivateFileChangeFeed(scenario.source_ref),
        )
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.ACTIVATE_FILE_DELETE_OBSERVATIONS,
        request_id="bound-rollback-activate-v4",
    ) as call:
        v4 = control.activate_file_delete_observations(
            call,
            ActivateFileDeleteObservations(scenario.source_ref),
        )
    provider = FileChangeProvider(
        FileRootRegistry(
            {scenario.root_ref: scenario.root},
            limits=FileReadLimits(max_file_bytes=1_024 * 1_024),
        ),
        proofs=FileChangeProviderProofs(
            provider_signing_key=provider_key,
            checkpoint_verification_key=checkpoint_key.public_key(),
        ),
    )
    page = provider.read_changes(
        FileChangeSource(scenario.organization_id, v4.active_version),
        InitialScan(),
        ChangeLimit(2),
    )
    assert type(page) is ProviderOk
    def accept_page() -> object:
        with authority.authorize(
            opaque_credential="control-secret",
            operation=ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
            request_id="bound-rollback-accept-loses-fence",
        ) as call:
            return control.accept_file_change_page(call, page.value)

    migration_engine = create_database_engine(migration_configuration)
    alembic_configuration = Config(ROOT / "alembic.ini")
    fence = "context-engine.file-status-migration-fence"
    try:
        with migration_engine.connect() as blocker:
            transaction = blocker.begin()
            try:
                blocker.execute(text("LOCK TABLE context_source IN ACCESS SHARE MODE"))
                with ThreadPoolExecutor(max_workers=2) as executor:
                    pending_downgrade = executor.submit(
                        command.downgrade,
                        alembic_configuration,
                        "20260730_0044",
                    )
                    with migration_engine.connect() as observer:
                        deadline = monotonic() + 10
                        while monotonic() < deadline:
                            downgrade_holds_fence = observer.execute(
                                text(
                                    """
                                    SELECT EXISTS (
                                      SELECT 1 FROM pg_locks
                                      WHERE locktype = 'advisory'
                                        AND mode = 'ExclusiveLock'
                                        AND granted IS TRUE
                                        AND classid = ((
                                          hashtextextended(:fence, 0) >> 32
                                        ) & 4294967295)::oid
                                        AND objid = (hashtextextended(:fence, 0)
                                          & 4294967295)::oid
                                        AND objsubid = 1
                                    )
                                    """
                                ),
                                {"fence": fence},
                            ).scalar_one()
                            if downgrade_holds_fence:
                                break
                            sleep(0.01)
                    assert downgrade_holds_fence
                    pending_accept = executor.submit(accept_page)
                    with migration_engine.connect() as observer:
                        deadline = monotonic() + 10
                        while monotonic() < deadline:
                            accept_waits_for_fence = observer.execute(
                                text(
                                    """
                                    SELECT EXISTS (
                                      SELECT 1 FROM pg_locks
                                      WHERE locktype = 'advisory'
                                        AND mode = 'ShareLock'
                                        AND granted IS FALSE
                                        AND classid = ((
                                          hashtextextended(:fence, 0) >> 32
                                        ) & 4294967295)::oid
                                        AND objid = (hashtextextended(:fence, 0)
                                          & 4294967295)::oid
                                        AND objsubid = 1
                                    )
                                    """
                                ),
                                {"fence": fence},
                            ).scalar_one()
                            if accept_waits_for_fence:
                                break
                            sleep(0.01)
                    assert accept_waits_for_fence
                    transaction.commit()
                    pending_downgrade.result(timeout=10)
                    with pytest.raises(SourceNotAvailable):
                        pending_accept.result(timeout=10)
            finally:
                if transaction.is_active:
                    transaction.rollback()
    finally:
        command.upgrade(alembic_configuration, "head")
        _delete_file_import_scenario(
            migration_configuration,
            scenario.organization_id,
        )
        migration_engine.dispose()


def test_file_scan_bound_downgrade_observes_in_flight_refusal_report(
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
) -> None:
    """The exclusive status fence cannot discard a concurrent bound refusal."""

    organization_id, user_id, membership_id, receiver_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    migration_engine = create_database_engine(migration_configuration)
    with migration_engine.begin() as connection:
        connection.execute(
            text("INSERT INTO organization (organization_id) VALUES (:org)"),
            {"org": organization_id},
        )
        connection.execute(
            text("INSERT INTO user_account (user_id) VALUES (:user)"),
            {"user": user_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO membership (
                    organization_id, membership_id, user_id, status,
                    membership_version, valid_from
                ) VALUES (:org, :membership, :user, 'active', 1, :now)
                """
            ),
            {
                "org": organization_id,
                "membership": membership_id,
                "user": user_id,
                "now": NOW - timedelta(days=1),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO service_principal (
                    organization_id, service_principal_id, workload,
                    worker_audience, operation, enabled
                ) VALUES (
                    :org, :receiver, 'supply.file-import',
                    'context-engine-worker', 'file.import', true
                )
                """
            ),
            {"org": organization_id, "receiver": receiver_id},
        )
    authority = ControlOperatorAuthority(
        _ControlAuthenticator(organization_id),
        call_ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    )
    control = ContextControl(
        store=PostgreSQLControlStore(
            guarded_control_engine,
            clock=lambda: NOW,
            file_import_receiver=FileImportReceiver(receiver_id),
        ),
        authority=authority,
        clock=lambda: NOW,
    )
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.REGISTER_SOURCE,
        request_id="register-bound-race-source",
    ) as call:
        source = control.register_source(
            call,
            RegisterFileSource(
                "Bound race source",
                FileRootRef("bound-race-root"),
                "bound-race-source",
            ),
        )
    fence = "context-engine.file-status-migration-fence"

    def report_refusal() -> str:
        with guarded_control_engine.begin() as connection:
            row = connection.execute(
                text(
                    "SELECT * FROM public."
                    "context_control_report_file_scan_bound_refusal("
                    ":organization_id, :source_id, 10000)"
                ),
                {
                    "organization_id": organization_id,
                    "source_id": source.source_ref.value,
                },
            ).one()
            category = row.refusal_category
            assert type(category) is str
            return category

    try:
        with migration_engine.connect() as blocker:
            transaction = blocker.begin()
            try:
                blocker.execute(
                    text(
                        "SELECT 1 FROM context_source "
                        "WHERE organization_id = :organization_id "
                        "AND source_id = :source_id FOR UPDATE"
                    ),
                    {
                        "organization_id": organization_id,
                        "source_id": source.source_ref.value,
                    },
                )
                with ThreadPoolExecutor(max_workers=2) as executor:
                    pending_report = executor.submit(report_refusal)
                    worker_holds_fence = False
                    with migration_engine.connect() as observer:
                        deadline = monotonic() + 10
                        while monotonic() < deadline:
                            worker_holds_fence = observer.execute(
                                text(
                                    """
                                    SELECT EXISTS (
                                      SELECT 1 FROM pg_locks
                                      WHERE locktype = 'advisory'
                                        AND mode = 'ShareLock'
                                        AND granted IS TRUE
                                        AND classid = ((
                                          hashtextextended(:fence, 0) >> 32
                                        ) & 4294967295)::oid
                                        AND objid = (hashtextextended(:fence, 0)
                                          & 4294967295)::oid
                                        AND objsubid = 1
                                    )
                                    """
                                ),
                                {"fence": fence},
                            ).scalar_one()
                            if worker_holds_fence:
                                break
                            sleep(0.01)
                    assert worker_holds_fence
                    pending_downgrade = executor.submit(
                        command.downgrade,
                        Config(ROOT / "alembic.ini"),
                        "20260730_0044",
                    )
                    transaction.commit()
                    assert pending_report.result(timeout=10) == "scan_bound_exceeded"
                    with pytest.raises(
                        RuntimeError,
                        match="default-only retained provenance",
                    ):
                        pending_downgrade.result(timeout=10)
            finally:
                if transaction.is_active:
                    transaction.rollback()
        assert _revision_rows(migration_configuration) == ["20260730_0047"]
    finally:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE context_source SET file_scan_refusal_category = NULL, "
                    "file_scan_refusal_bound = NULL "
                    "WHERE organization_id = :organization_id "
                    "AND source_id = :source_id"
                ),
                {
                    "organization_id": organization_id,
                    "source_id": source.source_ref.value,
                },
            )
        _delete_file_import_scenario(
            migration_configuration,
            organization_id,
        )
        migration_engine.dispose()


def test_file_source_status_downgrade_observes_in_flight_compilation_refusal(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    """The exclusive downgrade fence cannot discard a concurrent category."""

    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
    )
    claims = _scenario_claims(scenario)
    assert _redeem_file_import_direct(guarded_worker_engine, claims) is not None
    migration_engine = create_database_engine(migration_configuration)
    fence = "context-engine.file-status-migration-fence"

    def retain_refusal() -> bool:
        with guarded_worker_engine.begin() as connection:
            return bool(
                connection.execute(
                    text(
                        """
                        SELECT public.context_worker_fail_file_import_with_category(
                            :organization_id, :job_id, :service_principal_id,
                            :source_ref, 'unsupported_construct',
                            :lease_generation, :signing_key_version, :nonce,
                            :issued_at, :expires_at
                        )
                        """
                    ),
                    {
                        "organization_id": claims.organization_id,
                        "job_id": claims.job_id,
                        "service_principal_id": claims.service_principal_id,
                        "source_ref": claims.source_ref,
                        "lease_generation": claims.lease_generation,
                        "signing_key_version": claims.signing_key_version,
                        "nonce": claims.nonce,
                        "issued_at": claims.issued_at,
                        "expires_at": claims.expires_at,
                    },
                ).scalar_one()
            )

    try:
        with migration_engine.connect() as blocker:
            blocker_transaction = blocker.begin()
            try:
                blocker.execute(
                    text(
                        "SELECT 1 FROM file_import_job "
                        "WHERE organization_id = :organization_id "
                        "AND job_id = :job_id FOR UPDATE"
                    ),
                    {
                        "organization_id": scenario.organization_id,
                        "job_id": scenario.prepared.job_id,
                    },
                )
                with ThreadPoolExecutor(max_workers=2) as executor:
                    pending_refusal = executor.submit(retain_refusal)
                    with migration_engine.connect() as observer:
                        deadline = monotonic() + 10
                        while monotonic() < deadline:
                            worker_holds_fence = observer.execute(
                                text(
                                    """
                                    SELECT EXISTS (
                                        SELECT 1 FROM pg_locks
                                        WHERE locktype = 'advisory'
                                          AND mode = 'ShareLock'
                                          AND granted IS TRUE
                                          AND database = (
                                            SELECT oid FROM pg_database
                                            WHERE datname = current_database()
                                          )
                                          AND classid = (
                                            (hashtextextended(:fence, 0) >> 32)
                                            & 4294967295
                                          )::oid
                                          AND objid = (
                                            hashtextextended(:fence, 0)
                                            & 4294967295
                                          )::oid
                                          AND objsubid = 1
                                    )
                                    """
                                ),
                                {"fence": fence},
                            ).scalar_one()
                            if worker_holds_fence:
                                break
                            sleep(0.01)
                    assert worker_holds_fence
                    pending_downgrade = executor.submit(
                        downgrade_revision,
                        migration_configuration,
                        "20260727_0039",
                    )
                    with migration_engine.connect() as observer:
                        deadline = monotonic() + 10
                        while monotonic() < deadline:
                            downgrade_waits_for_worker = observer.execute(
                                text(
                                    """
                                    SELECT EXISTS (
                                        SELECT 1 FROM pg_locks
                                        WHERE locktype = 'advisory'
                                          AND mode = 'ExclusiveLock'
                                          AND granted IS FALSE
                                          AND database = (
                                            SELECT oid FROM pg_database
                                            WHERE datname = current_database()
                                          )
                                          AND classid = (
                                            (hashtextextended(:fence, 0) >> 32)
                                            & 4294967295
                                          )::oid
                                          AND objid = (
                                            hashtextextended(:fence, 0)
                                            & 4294967295
                                          )::oid
                                          AND objsubid = 1
                                    )
                                    """
                                ),
                                {"fence": fence},
                            ).scalar_one()
                            if downgrade_waits_for_worker:
                                break
                            sleep(0.01)
                    assert downgrade_waits_for_worker
                    blocker_transaction.commit()
                    assert pending_refusal.result(timeout=10) is True
                    with pytest.raises(
                        RuntimeError,
                        match="requires no retained compilation refusals",
                    ):
                        pending_downgrade.result(timeout=10)
            finally:
                if blocker_transaction.is_active:
                    blocker_transaction.rollback()
        assert _revision_rows(migration_configuration) == [HEAD_REVISION]
        with migration_engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT compilation_refusal_category FROM file_import_job "
                        "WHERE organization_id = :organization_id AND job_id = :job_id"
                    ),
                    {
                        "organization_id": scenario.organization_id,
                        "job_id": scenario.prepared.job_id,
                    },
                ).scalar_one()
                == "unsupported_construct"
            )
    finally:
        command.upgrade(Config(ROOT / "alembic.ini"), "head")
        migration_engine.dispose()
        _delete_issue_27_upgrade_fixture(
            migration_configuration,
            scenario.organization_id,
        )


def test_fragment_embedding_revision_preserves_retained_fragments(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    alembic_configuration = Config(ROOT / "alembic.ini")
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
    )
    assert scenario.token is not None
    _run_file_import(
        scenario,
        scenario.prepared,
        scenario.token,
        guarded_worker_engine,
    )
    try:
        command.downgrade(alembic_configuration, "20260726_0035")
        assert _revision_rows(migration_configuration) == ["20260726_0035"]
        engine = create_database_engine(migration_configuration)
        try:
            with engine.connect() as connection:
                retained = connection.execute(
                    text(
                        "SELECT count(*) FROM context_fragment "
                        "WHERE organization_id = :organization_id"
                    ),
                    {"organization_id": scenario.organization_id},
                ).scalar_one()
                embedding_column = connection.execute(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_schema = 'public' "
                        "AND table_name = 'context_fragment' "
                        "AND column_name = 'embedding'"
                    )
                ).scalar_one()
            assert retained > 0
            assert embedding_column == 0
        finally:
            engine.dispose()
    finally:
        command.upgrade(alembic_configuration, "head")
        _delete_issue_27_upgrade_fixture(
            migration_configuration,
            scenario.organization_id,
        )


def test_recursive_file_path_revision_refuses_retained_nested_lineage(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
) -> None:
    alembic_configuration = Config(ROOT / "alembic.ini")
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
    )
    nested = scenario.root / "notes"
    nested.mkdir()
    (nested / "nested.md").write_bytes(b"# Nested\n")
    _prepare_repeat_file_import(
        scenario,
        guarded_control_engine,
        idempotency_key="nested-migration-lineage",
        path=FileImportPath("notes/nested.md"),
    )
    try:
        with pytest.raises(
            RuntimeError,
            match="requires no retained nested lineage",
        ):
            downgrade_revision(migration_configuration, "20260726_0035")
        assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    finally:
        command.upgrade(alembic_configuration, "head")
        _delete_issue_27_upgrade_fixture(
            migration_configuration,
            scenario.organization_id,
        )


def test_file_change_scheduling_downgrade_serializes_with_manual_import(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
) -> None:
    """The manual-path compatibility check cannot race an in-flight import."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        issue_lease=False,
    )
    acquisition_id = uuid4()
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as writer:
            writer_transaction = writer.begin()
            try:
                writer.execute(
                    text(
                        """
                        INSERT INTO file_acquisition (
                            organization_id, acquisition_id, source_id,
                            source_version_id, relative_path,
                            audience_principal_ref, audience_membership_id,
                            audience_membership_version, idempotency_key,
                            request_digest, created_at
                        )
                        SELECT organization_id, :acquisition_id, source_id,
                               source_version_id, '.md',
                               audience_principal_ref, audience_membership_id,
                               audience_membership_version,
                               'concurrent-new-manual-path',
                               :request_digest, :created_at
                        FROM file_acquisition
                        WHERE organization_id = :organization_id
                        LIMIT 1
                        """
                    ),
                    {
                        "organization_id": scenario.organization_id,
                        "acquisition_id": acquisition_id,
                        "request_digest": "a" * 64,
                        "created_at": datetime.now(UTC),
                    },
                )
                with ThreadPoolExecutor(max_workers=1) as executor:
                    pending_downgrade = executor.submit(
                        downgrade_revision,
                        migration_configuration,
                        "20260725_0029",
                    )
                    downgrade_waiting = False
                    try:
                        with engine.connect() as observer:
                            deadline = monotonic() + 10
                            while monotonic() < deadline:
                                downgrade_waiting = observer.execute(
                                    text(
                                        """
                                        SELECT EXISTS (
                                            SELECT 1
                                            FROM pg_locks
                                            WHERE database = (
                                                SELECT oid
                                                FROM pg_database
                                                WHERE datname = current_database()
                                            )
                                                  AND relation IN (
                                                      'public.context_source'::regclass,
                                                      'public.source_version'::regclass,
                                                      'public.file_acquisition'::regclass
                                                  )
                                              AND mode = 'AccessExclusiveLock'
                                              AND granted IS FALSE
                                        )
                                        """
                                    )
                                ).scalar_one()
                                if downgrade_waiting:
                                    break
                                sleep(0.01)
                    finally:
                        if writer_transaction.is_active:
                            writer_transaction.commit()
                    assert downgrade_waiting
                    with pytest.raises(
                        RuntimeError,
                        match="newer manual File import paths",
                    ):
                        pending_downgrade.result(timeout=10)
            finally:
                if writer_transaction.is_active:
                    writer_transaction.rollback()

        assert _revision_rows(migration_configuration) == [HEAD_REVISION]
        with engine.connect() as connection:
            retained_path = connection.execute(
                text(
                    "SELECT relative_path FROM file_acquisition "
                    "WHERE organization_id = :org AND acquisition_id = :acquisition"
                ),
                {"org": scenario.organization_id, "acquisition": acquisition_id},
            ).scalar_one()
        assert retained_path == ".md"
    finally:
        engine.dispose()
        if _revision_rows(migration_configuration) != [HEAD_REVISION]:
            command.upgrade(alembic_configuration, "head")
        _delete_issue_27_upgrade_fixture(
            migration_configuration,
            scenario.organization_id,
        )


def test_delivery_evidence_revision_downgrades_only_while_empty(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Issue #63 carrier can be removed only before any attestation exists."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM delivery_evidence"))
    finally:
        engine.dispose()
    try:
        command.downgrade(alembic_configuration, "20260723_0018")
        assert _revision_rows(migration_configuration) == ["20260723_0018"]
        assert "delivery_evidence" not in _application_tables(migration_configuration)
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    assert "delivery_evidence" in _application_tables(migration_configuration)


def test_citation_open_revision_downgrades_only_while_empty(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Issue #69 carrier can be removed only before locator lineage exists."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM citation_open_locator"))
    finally:
        engine.dispose()
    try:
        command.downgrade(alembic_configuration, "20260724_0023")
        assert _revision_rows(migration_configuration) == ["20260724_0023"]
        assert "citation_open_locator" not in _application_tables(
            migration_configuration
        )
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    assert "citation_open_locator" in _application_tables(migration_configuration)


def test_citation_open_revision_refuses_downgrade_with_retained_lineage(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    guarded_runtime_engine: Engine,
) -> None:
    """Issue #69 rollback retains digest lineage until profile cleanup."""

    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
    )
    assert scenario.token is not None
    published = _run_file_import(
        scenario,
        scenario.prepared,
        scenario.token,
        guarded_worker_engine,
    )
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            user_id = connection.execute(
                text(
                    "SELECT user_id FROM membership WHERE organization_id = :org "
                    "AND membership_id = :membership"
                ),
                {"org": scenario.organization_id, "membership": scenario.membership_id},
            ).scalar_one()
        now = datetime.now(UTC)
        authority = PostgreSQLMembershipAuthority(guarded_runtime_engine)
        with authority.current_user_actor(
            MembershipIdentity(
                organization_id=scenario.organization_id,
                user_id=user_id,
                membership_id=scenario.membership_id,
                membership_version=1,
                principal_ref="principal:file-tracer",
                request_id="migration-citation-lineage",
                authentication_binding_ref="binding:file-tracer",
                checked_at=now,
            )
        ) as verification:
            assert verification.citation_open_session is not None
            issue_citation_open_ref(
                verification.citation_open_session,
                CitationOpenIssue(
                    organization_id=scenario.organization_id,
                    package_ref="pkg_" + "a" * 32,
                    evidence_ref="ev_" + "b" * 64,
                    resource_ref=published.candidate_ref.resource_ref,
                    revision_id=UUID(published.candidate_ref.revision_ref),
                    fragment_ref=published.candidate_ref.fragment_ref,
                    issued_at=now,
                    expires_at=now + timedelta(minutes=5),
                ),
                profile=CitationOpenProfile(
                    profile_ref="private-citation-open-v1",
                    retention_policy_ref="citation-locator-retention-v1",
                    maximum_ttl=timedelta(minutes=10),
                    retention_period=timedelta(days=30),
                ),
            )

        with pytest.raises(SQLAlchemyError):
            downgrade_revision(migration_configuration, "20260724_0024")
        assert _revision_rows(migration_configuration) == [HEAD_REVISION]
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM citation_open_locator "
                        "WHERE organization_id = :org"
                    ),
                    {"org": scenario.organization_id},
                ).scalar_one()
                == 1
            )
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM citation_open_locator WHERE organization_id = :org"),
                {"org": scenario.organization_id},
            )
        engine.dispose()
        _delete_issue_27_upgrade_fixture(
            migration_configuration,
            scenario.organization_id,
        )


def test_model_egress_revision_downgrades_only_while_audit_is_empty(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Issue #70 schema is reversible only before retained audit exists."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM model_egress_audit"))
    finally:
        engine.dispose()
    try:
        command.downgrade(alembic_configuration, "20260724_0024")
        assert _revision_rows(migration_configuration) == ["20260724_0024"]
        assert "model_egress_audit" not in _application_tables(migration_configuration)
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    assert "model_egress_audit" in _application_tables(migration_configuration)


def test_file_source_offboarding_refuses_downgrade_with_committed_intent(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
) -> None:
    """A disabled source is never silently re-enabled by schema downgrade."""

    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        issue_lease=False,
    )
    committed = _offboard(scenario, guarded_control_engine)
    with pytest.raises(SQLAlchemyError):
        downgrade_revision(migration_configuration, "20260723_0018")

    assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            state = connection.execute(
                text(
                    """
                    SELECT source.lifecycle_state, source.disabled_version_id,
                           epoch.policy_epoch, intent.cleanup_intent_id,
                           intent.cleanup_state
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
        assert tuple(state) == (
            "disabled",
            committed.source_version_ref,
            2,
            committed.cleanup_intent_ref,
            "pending",
        )
    finally:
        migration_engine.dispose()
        _delete_issue_27_upgrade_fixture(
            migration_configuration,
            scenario.organization_id,
        )


def test_file_progress_refuses_downgrade_and_preserves_refs(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    """Issue #29 never discards the database ordering behind opaque refs."""

    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
    )
    assert scenario.token is not None
    _run_file_import(
        scenario,
        scenario.prepared,
        scenario.token,
        guarded_worker_engine,
    )
    repeated, repeated_token = _prepare_repeat_file_import(
        scenario,
        guarded_control_engine,
        idempotency_key="progress-ref-rebuild-repeat",
    )
    repeated_result = _run_file_import(
        scenario,
        repeated,
        repeated_token,
        guarded_worker_engine,
    )
    _tombstone(
        scenario,
        guarded_control_engine,
        resource_ref=repeated_result.candidate_ref.resource_ref,
        event_ref="progress-ref-rebuild-delete",
        event_sequence=1,
    )
    migration_engine = create_database_engine(migration_configuration)

    def progress_refs() -> tuple[
        tuple[tuple[int, str, str], ...],
        tuple[tuple[int, str, str], ...],
    ]:
        with migration_engine.connect() as connection:
            checkpoint_rows = connection.execute(
                text(
                    """
                    SELECT sequence, checkpoint_ref, change_kind
                    FROM file_source_acquisition_checkpoint
                    WHERE organization_id = :organization_id
                      AND source_id = :source_id
                    ORDER BY sequence
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "source_id": scenario.source_ref.value,
                },
            ).all()
            watermark_rows = connection.execute(
                text(
                    """
                    SELECT sequence, watermark_ref, outcome
                    FROM file_source_publish_watermark
                    WHERE organization_id = :organization_id
                      AND source_id = :source_id
                    ORDER BY sequence
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "source_id": scenario.source_ref.value,
                },
            ).all()
        return (
            tuple(
                (int(row.sequence), str(row.checkpoint_ref), str(row.change_kind))
                for row in checkpoint_rows
            ),
            tuple(
                (int(row.sequence), str(row.watermark_ref), str(row.outcome))
                for row in watermark_rows
            ),
        )

    before = progress_refs()
    assert [row[0] for row in before[0]] == [1, 2, 3]
    assert [row[2] for row in before[0]] == [
        "file_import",
        "file_import",
        "file_tombstone",
    ]
    assert [row[0] for row in before[1]] == [1, 2, 3]
    alembic_configuration = Config(ROOT / "alembic.ini")
    try:
        with pytest.raises(
            RuntimeError,
            match="requires empty progress streams",
        ):
            downgrade_revision(migration_configuration, "20260723_0017")
        assert _revision_rows(migration_configuration) == [HEAD_REVISION]
        assert progress_refs() == before
    finally:
        if _revision_rows(migration_configuration) != [HEAD_REVISION]:
            command.upgrade(alembic_configuration, "head")
        migration_engine.dispose()
        _delete_issue_27_upgrade_fixture(
            migration_configuration, scenario.organization_id
        )


def test_file_tombstone_revision_refuses_downgrade_with_committed_intent(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    """A durable cleanup obligation makes the Issue #28 boundary irreversible."""

    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
    )
    assert scenario.token is not None
    published = _run_file_import(
        scenario,
        scenario.prepared,
        scenario.token,
        guarded_worker_engine,
    )
    committed = _tombstone(
        scenario,
        guarded_control_engine,
        resource_ref=published.candidate_ref.resource_ref,
        event_ref="file-delete-downgrade-guard",
        event_sequence=1,
    )

    with pytest.raises(SQLAlchemyError):
        downgrade_revision(migration_configuration, "20260723_0016")

    assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            state = connection.execute(
                text(
                    """
                    SELECT resource.tombstoned, epoch.policy_epoch,
                           intent.cleanup_intent_id, intent.state
                    FROM context_resource AS resource
                    JOIN organization_policy_epoch AS epoch
                      ON epoch.organization_id = resource.organization_id
                    JOIN file_resource_cleanup_intent AS intent
                      ON intent.organization_id = resource.organization_id
                     AND intent.resource_ref = resource.resource_ref
                    WHERE resource.organization_id = :organization_id
                      AND resource.resource_ref = :resource_ref
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "resource_ref": published.candidate_ref.resource_ref,
                },
            ).one()
        assert tuple(state) == (
            True,
            committed.policy_epoch,
            committed.cleanup_intent_ref,
            "pending",
        )
    finally:
        migration_engine.dispose()
        _delete_issue_27_upgrade_fixture(
            migration_configuration, scenario.organization_id
        )


def test_recovery_upgrade_adopts_an_existing_ready_replacement(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    """A pre-embedding ready Revision rewinds and resumes through embedding."""

    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=b"# Handbook\n\nOLD marker.\n\n## Shared\n\nShared query.\n",
    )
    assert scenario.token is not None
    initial = _run_file_import(
        scenario,
        scenario.prepared,
        scenario.token,
        guarded_worker_engine,
        config_version="markdown-config-v2",
    )
    (scenario.root / "handbook.md").write_bytes(NEW_MARKDOWN)
    replacement, replacement_token = _prepare_repeat_file_import(
        scenario,
        guarded_control_engine,
        idempotency_key="ready-before-recovery-upgrade",
        lease_ttl_seconds=2,
    )
    claims = _scenario_claims(
        replace(scenario, prepared=replacement, token=replacement_token)
    )
    document = compile_markdown(
        NEW_MARKDOWN, MarkdownCompilerConfig("markdown-config-v2")
    )
    assert type(document) is ParsedDocument
    revision_id = uuid4()
    resource_ref = initial.candidate_ref.resource_ref
    alembic_configuration = Config(ROOT / "alembic.ini")
    try:
        clear_file_source_progress_projection(
            migration_configuration, scenario.organization_id
        )
        command.downgrade(alembic_configuration, "20260723_0014")
        with guarded_worker_engine.begin() as connection:
            redeemed = connection.execute(
                text(
                    """
                    SELECT * FROM public.context_worker_redeem_file_import(
                        :organization_id, :job_id, :service_principal_id,
                        :source_ref, :signing_key_version, :nonce,
                        :issued_at, :expires_at
                    )
                    """
                ),
                {
                    "organization_id": claims.organization_id,
                    "job_id": claims.job_id,
                    "service_principal_id": claims.service_principal_id,
                    "source_ref": claims.source_ref,
                    "signing_key_version": claims.signing_key_version,
                    "nonce": claims.nonce,
                    "issued_at": claims.issued_at,
                    "expires_at": claims.expires_at,
                },
            ).one_or_none()
            assert redeemed is not None
            staged = connection.execute(
                text(
                    """
                    SELECT *
                    FROM public.context_worker_stage_structural_file_replacement(
                        :organization_id, :job_id, :service_principal_id,
                        :source_ref, :resource_ref, :revision_id,
                        :canonical_text, :content_hash, :compilation_digest,
                        :compiler_version, :config_version,
                        CAST(:compilation_document AS jsonb),
                        :signing_key_version, :nonce, :issued_at, :expires_at
                    )
                    """
                ),
                {
                    "organization_id": claims.organization_id,
                    "job_id": claims.job_id,
                    "service_principal_id": claims.service_principal_id,
                    "source_ref": claims.source_ref,
                    "resource_ref": resource_ref,
                    "revision_id": revision_id,
                    "canonical_text": document.canonical_text,
                    "content_hash": document.content_hash,
                    "compilation_digest": document.compilation_digest,
                    "compiler_version": document.provenance.compiler_version,
                    "config_version": document.provenance.config_version,
                    "compilation_document": json.dumps(
                        json.loads(canonicalize_parsed_document(document)),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    "signing_key_version": claims.signing_key_version,
                    "nonce": claims.nonce,
                    "issued_at": claims.issued_at,
                    "expires_at": claims.expires_at,
                },
            ).one_or_none()
            assert staged is not None
        command.upgrade(alembic_configuration, "head")
        migration_engine = create_database_engine(migration_configuration)
        try:
            with migration_engine.connect() as connection:
                assert (
                    connection.execute(
                        text(
                            "SELECT checkpoint FROM file_publication_recovery "
                            "WHERE organization_id = :organization_id "
                            "AND job_id = :job_id"
                        ),
                        {
                            "organization_id": claims.organization_id,
                            "job_id": claims.job_id,
                        },
                    ).scalar_one()
                    == "acquired"
                )
        finally:
            migration_engine.dispose()
        while datetime.now(UTC) <= claims.expires_at:
            sleep(0.05)
        recovered_token = PostgreSQLWorkerLeaseIssuer(
            guarded_control_engine, scenario.codec
        ).issue_file_import_lease(replacement)
        recovered = _run_file_import(
            scenario,
            replacement,
            recovered_token,
            guarded_worker_engine,
            config_version="markdown-config-v2",
        )
        assert recovered.outcome == "replaced"
        assert recovered.candidate_ref.revision_ref == str(revision_id)
    finally:
        command.upgrade(alembic_configuration, "head")
        _delete_issue_27_upgrade_fixture(
            migration_configuration, scenario.organization_id
        )


def test_structural_snapshot_constraint_rejects_missing_json_bindings(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """A v2 snapshot cannot exploit PostgreSQL CHECK's UNKNOWN result."""

    engine = create_database_engine(migration_configuration)
    try:
        with pytest.raises(IntegrityError) as raised, engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TEMP TABLE structural_snapshot_constraint_probe
                    (LIKE file_revision_snapshot INCLUDING CONSTRAINTS)
                    ON COMMIT DROP
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO structural_snapshot_constraint_probe (
                        organization_id, resource_ref, revision_id,
                        acquisition_id, canonical_text, content_hash,
                        compilation_digest, compiler_version, config_version,
                        compilation_document
                    ) VALUES (
                        :organization_id, :resource_ref, :revision_id,
                        :acquisition_id, '# Missing bindings\n', :digest,
                        :digest, 'context-engine-markdown-v2',
                        'markdown-config-v2', '{}'::jsonb
                    )
                    """
                ),
                {
                    "organization_id": uuid4(),
                    "resource_ref": f"resource:missing-bindings:{uuid4()}",
                    "revision_id": uuid4(),
                    "acquisition_id": uuid4(),
                    "digest": "0" * 64,
                },
            )
    finally:
        engine.dispose()

    assert "ck_file_revision_snapshot_structural_document" in str(raised.value.orig)


def test_empty_content_downgrade_preserves_v2_context_run_history(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Schema rollback does not invalidate already-retained v2 lineage."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    identity = LineageIdentity(
        organization_id=uuid4(),
        user_id=uuid4(),
        membership_id=uuid4(),
        run_ref="run_" + "9" * 32,
        decision_ref="dec_" + "a" * 32,
    )
    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO organization (organization_id) "
                    "VALUES (:organization_id)"
                ),
                {"organization_id": identity.organization_id},
            )
            connection.execute(
                text("INSERT INTO user_account (user_id) VALUES (:user_id)"),
                {"user_id": identity.user_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO membership (
                        organization_id, membership_id, user_id, status,
                        membership_version, valid_from, valid_until
                    ) VALUES (
                        :organization_id, :membership_id, :user_id, 'active',
                        1, statement_timestamp() - interval '1 day', NULL
                    )
                    """
                ),
                {
                    "organization_id": identity.organization_id,
                    "membership_id": identity.membership_id,
                    "user_id": identity.user_id,
                },
            )
            insert_context_run(connection, identity)
            connection.execute(
                text(
                    """
                    UPDATE context_run
                    SET package_digest_profile =
                        'context-package-canonical-json-v2'
                    WHERE organization_id = :organization_id
                      AND run_ref = :run_ref
                    """
                ),
                {
                    "organization_id": identity.organization_id,
                    "run_ref": identity.run_ref,
                },
            )

        command.downgrade(alembic_configuration, "20260722_0007")
        assert _revision_rows(migration_configuration) == ["20260722_0007"]
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        """
                    SELECT package_digest_profile
                    FROM context_run
                    WHERE organization_id = :organization_id
                      AND run_ref = :run_ref
                    """
                    ),
                    {
                        "organization_id": identity.organization_id,
                        "run_ref": identity.run_ref,
                    },
                ).scalar_one()
                == "context-package-canonical-json-v2"
            )
            profile_constraint = connection.execute(
                text(
                    """
                    SELECT pg_get_constraintdef(oid, true)
                    FROM pg_constraint
                    WHERE conrelid = 'public.context_run'::regclass
                      AND conname = 'ck_context_run_package_digest_profile'
                    """
                )
            ).scalar_one()
        assert "context-package-canonical-json-v1" in profile_constraint
        assert "context-package-canonical-json-v2" in profile_constraint
    finally:
        command.upgrade(alembic_configuration, "head")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM context_run WHERE organization_id = :organization_id"
                ),
                {"organization_id": identity.organization_id},
            )
            connection.execute(
                text("DELETE FROM membership WHERE organization_id = :organization_id"),
                {"organization_id": identity.organization_id},
            )
            connection.execute(
                text("DELETE FROM user_account WHERE user_id = :user_id"),
                {"user_id": identity.user_id},
            )
            connection.execute(
                text(
                    "DELETE FROM organization WHERE organization_id = :organization_id"
                ),
                {"organization_id": identity.organization_id},
            )
        engine.dispose()


def test_openapi_v0_revision_refuses_downgrade_with_v3_context_run_history(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """The v0 digest profile cannot be made invalid by a schema rollback."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    identity = LineageIdentity(
        organization_id=uuid4(),
        user_id=uuid4(),
        membership_id=uuid4(),
        run_ref="run_" + "b" * 32,
        decision_ref="dec_" + "c" * 32,
    )
    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO organization (organization_id) VALUES (:org)"),
                {"org": identity.organization_id},
            )
            connection.execute(
                text("INSERT INTO user_account (user_id) VALUES (:user_id)"),
                {"user_id": identity.user_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO membership (
                        organization_id, membership_id, user_id, status,
                        membership_version, valid_from
                    ) VALUES (
                        :org, :membership_id, :user_id, 'active', 1,
                        statement_timestamp() - interval '1 day'
                    )
                    """
                ),
                {
                    "org": identity.organization_id,
                    "membership_id": identity.membership_id,
                    "user_id": identity.user_id,
                },
            )
            insert_context_run(connection, identity)
            connection.execute(
                text(
                    "UPDATE context_run SET package_digest_profile = "
                    "'context-package-canonical-json-v3' "
                    "WHERE organization_id = :org"
                ),
                {"org": identity.organization_id},
            )

        with pytest.raises(SQLAlchemyError, match="v3 ContextRun lineage exists"):
            downgrade_revision(migration_configuration, "20260723_0021")
        assert _revision_rows(migration_configuration) == [HEAD_REVISION]
    finally:
        if _revision_rows(migration_configuration) != [HEAD_REVISION]:
            command.upgrade(alembic_configuration, "head")
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM context_run WHERE organization_id = :org"),
                {"org": identity.organization_id},
            )
            connection.execute(
                text("DELETE FROM membership WHERE organization_id = :org"),
                {"org": identity.organization_id},
            )
            connection.execute(
                text("DELETE FROM user_account WHERE user_id = :user_id"),
                {"user_id": identity.user_id},
            )
            connection.execute(
                text("DELETE FROM organization WHERE organization_id = :org"),
                {"org": identity.organization_id},
            )
        engine.dispose()


def test_field_projection_downgrade_refuses_populated_content_atomically(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Stored Fragments retain Issue #48's explicit-right default denial."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    organization_id = uuid4()
    user_id = uuid4()
    membership_id = uuid4()
    revision_id = uuid4()
    resource_ref = f"resource:downgrade:{uuid4()}"
    fragment_ref = f"fragment:downgrade:{uuid4()}"
    engine = create_database_engine(migration_configuration)
    parameters = {
        "organization_id": organization_id,
        "user_id": user_id,
        "membership_id": membership_id,
        "revision_id": revision_id,
        "resource_ref": resource_ref,
        "fragment_ref": fragment_ref,
    }
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO organization (organization_id) "
                    "VALUES (:organization_id)"
                ),
                parameters,
            )
            connection.execute(
                text("INSERT INTO user_account (user_id) VALUES (:user_id)"),
                parameters,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO membership (
                        organization_id, membership_id, user_id, status,
                        membership_version, valid_from, valid_until
                    ) VALUES (
                        :organization_id, :membership_id, :user_id, 'active',
                        1, statement_timestamp(), NULL
                    )
                    """
                ),
                parameters,
            )
            connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            connection.execute(
                text(
                    """
                    INSERT INTO context_resource (
                        organization_id, resource_ref, source_ref,
                        active_revision_id, tombstoned
                    ) VALUES (
                        :organization_id, :resource_ref, 'source:downgrade',
                        :revision_id, false
                    )
                    """
                ),
                parameters,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO context_revision (
                        organization_id, resource_ref, revision_id
                    ) VALUES (
                        :organization_id, :resource_ref, :revision_id
                    )
                    """
                ),
                parameters,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO context_fragment (
                        organization_id, resource_ref, revision_id,
                        fragment_ref, ordinal, projection_kind, content
                    ) VALUES (
                        :organization_id, :resource_ref, :revision_id,
                        :fragment_ref, 0, 'body', 'private-body'
                    )
                    """
                ),
                parameters,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO membership_resource_field_right (
                        organization_id, membership_id, membership_version,
                        resource_ref, field_ref
                    ) VALUES (
                        :organization_id, :membership_id, 1,
                        :resource_ref, 'body'
                    )
                    """
                ),
                parameters,
            )

        with pytest.raises(
            RuntimeError,
            match="downgrade requires an empty content schema",
        ):
            downgrade_revision(migration_configuration, "20260722_0008")

        assert _revision_rows(migration_configuration) == [HEAD_REVISION]
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT content FROM context_fragment "
                        "WHERE organization_id = :organization_id"
                    ),
                    parameters,
                ).scalar_one()
                == "private-body"
            )
            assert (
                connection.execute(
                    text(
                        "SELECT field_ref FROM membership_resource_field_right "
                        "WHERE organization_id = :organization_id"
                    ),
                    parameters,
                ).scalar_one()
                == "body"
            )
            policy = connection.execute(
                text(
                    """
                    SELECT qual
                    FROM pg_policies
                    WHERE schemaname = 'public'
                      AND tablename = 'context_fragment'
                      AND policyname = 'context_fragment_current_user_actor'
                    """
                )
            ).scalar_one()
            assert "article_access_policy" in str(policy)
        assert "membership_resource_field_right" in str(policy)
    finally:
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE context_fragment DISABLE TRIGGER "
                        "context_fragment_reject_mutation"
                    )
                )
                connection.execute(
                    text(
                        "ALTER TABLE context_revision DISABLE TRIGGER "
                        "context_revision_reject_mutation"
                    )
                )
            with engine.begin() as connection:
                for statement in (
                    "DELETE FROM membership_resource_field_right "
                    "WHERE organization_id = :organization_id",
                    "DELETE FROM context_fragment "
                    "WHERE organization_id = :organization_id",
                    "DELETE FROM context_revision "
                    "WHERE organization_id = :organization_id",
                    "DELETE FROM context_resource "
                    "WHERE organization_id = :organization_id",
                    "DELETE FROM membership WHERE organization_id = :organization_id",
                    "DELETE FROM user_account WHERE user_id = :user_id",
                    "DELETE FROM organization WHERE organization_id = :organization_id",
                ):
                    connection.execute(text(statement), parameters)
        except SQLAlchemyError:
            if _revision_rows(migration_configuration) != [HEAD_REVISION]:
                command.upgrade(alembic_configuration, "head")
            raise
        finally:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE context_revision ENABLE TRIGGER "
                        "context_revision_reject_mutation"
                    )
                )
                connection.execute(
                    text(
                        "ALTER TABLE context_fragment ENABLE TRIGGER "
                        "context_fragment_reject_mutation"
                    )
                )
            engine.dispose()


def test_field_projection_downgrade_serializes_with_concurrent_fragment_insert(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """The empty-schema decision cannot race an in-flight publisher commit."""

    alembic_configuration = Config(ROOT / "alembic.ini")
    organization_id = uuid4()
    revision_id = uuid4()
    resource_ref = f"resource:downgrade-race:{uuid4()}"
    fragment_ref = f"fragment:downgrade-race:{uuid4()}"
    parameters = {
        "organization_id": organization_id,
        "revision_id": revision_id,
        "resource_ref": resource_ref,
        "fragment_ref": fragment_ref,
    }
    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO organization (organization_id) "
                    "VALUES (:organization_id)"
                ),
                parameters,
            )
            connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            connection.execute(
                text(
                    """
                    INSERT INTO context_resource (
                        organization_id, resource_ref, source_ref,
                        active_revision_id, tombstoned
                    ) VALUES (
                        :organization_id, :resource_ref,
                        'source:downgrade-race', :revision_id, false
                    )
                    """
                ),
                parameters,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO context_revision (
                        organization_id, resource_ref, revision_id
                    ) VALUES (
                        :organization_id, :resource_ref, :revision_id
                    )
                    """
                ),
                parameters,
            )

        with engine.connect() as publisher:
            publisher_transaction = publisher.begin()
            try:
                publisher.execute(
                    text(
                        """
                        INSERT INTO context_fragment (
                            organization_id, resource_ref, revision_id,
                            fragment_ref, ordinal, projection_kind, content
                        ) VALUES (
                            :organization_id, :resource_ref, :revision_id,
                            :fragment_ref, 0, 'body', 'concurrent-private-body'
                        )
                        """
                    ),
                    parameters,
                )
                with ThreadPoolExecutor(max_workers=1) as executor:
                    pending_downgrade = executor.submit(
                        downgrade_revision,
                        migration_configuration,
                        "20260722_0008",
                    )
                    downgrade_waiting = False
                    try:
                        with engine.connect() as observer:
                            deadline = monotonic() + 10
                            while monotonic() < deadline:
                                downgrade_waiting = observer.execute(
                                    text(
                                        """
                                        SELECT EXISTS (
                                            SELECT 1
                                            FROM pg_locks
                                            WHERE database = (
                                                SELECT oid
                                                FROM pg_database
                                                WHERE datname = current_database()
                                            )
                                              AND relation = (
                                                  'public.context_fragment'::regclass
                                              )
                                              AND mode = 'AccessExclusiveLock'
                                              AND granted IS FALSE
                                        )
                                        """
                                    )
                                ).scalar_one()
                                if downgrade_waiting:
                                    break
                                sleep(0.01)
                    finally:
                        if publisher_transaction.is_active:
                            publisher_transaction.commit()
                    assert downgrade_waiting
                    with pytest.raises(
                        RuntimeError,
                        match="downgrade requires an empty content schema",
                    ):
                        pending_downgrade.result(timeout=10)
            finally:
                if publisher_transaction.is_active:
                    publisher_transaction.rollback()

        assert _revision_rows(migration_configuration) == [HEAD_REVISION]
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT content FROM context_fragment "
                        "WHERE organization_id = :organization_id"
                    ),
                    parameters,
                ).scalar_one()
                == "concurrent-private-body"
            )
    finally:
        if _revision_rows(migration_configuration) != [HEAD_REVISION]:
            command.upgrade(alembic_configuration, "head")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE context_fragment DISABLE TRIGGER "
                    "context_fragment_reject_mutation"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE context_revision DISABLE TRIGGER "
                    "context_revision_reject_mutation"
                )
            )
        try:
            with engine.begin() as connection:
                for statement in (
                    "DELETE FROM context_fragment "
                    "WHERE organization_id = :organization_id",
                    "DELETE FROM context_revision "
                    "WHERE organization_id = :organization_id",
                    "DELETE FROM context_resource "
                    "WHERE organization_id = :organization_id",
                    "DELETE FROM organization WHERE organization_id = :organization_id",
                ):
                    connection.execute(text(statement), parameters)
        finally:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE context_revision ENABLE TRIGGER "
                        "context_revision_reject_mutation"
                    )
                )
                connection.execute(
                    text(
                        "ALTER TABLE context_fragment ENABLE TRIGGER "
                        "context_fragment_reject_mutation"
                    )
                )
            engine.dispose()
