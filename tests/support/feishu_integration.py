"""Real-PostgreSQL fixture helpers for accepted synthetic Feishu pages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import Engine, text

from adapters.connectors.feishu import (
    FEISHU_DOCS_CAPABILITY_MANIFEST_JSON,
    FeishuAclFailure,
    FeishuAclResponse,
    FeishuAclVisibility,
    FeishuChangePage,
    FeishuDocsConnectorAdapter,
    FeishuDocument,
    FeishuDocumentDelete,
    FeishuGroupSnapshot,
    FeishuIdentityMapping,
    FeishuPermissionSubject,
)
from engine.control import AppliedFeishuAclObservation, ApplyFeishuAclObservation
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLConnectorCheckpointStore,
    PostgreSQLFeishuAclControl,
    create_database_engine,
)
from tests.integration.test_connector_checkpoint_store import (
    _bridge,
    _Scenario,
    _seed_scenario,
)
from tests.support.feishu_connector_twin import SyntheticFeishuTwin


@dataclass(frozen=True, slots=True)
class AcceptedFeishuPage:
    scenario: _Scenario
    page_ref: str
    document_ref: str
    delete_observation: bool

    @property
    def command(self) -> ApplyFeishuAclObservation:
        return ApplyFeishuAclObservation(
            organization_id=self.scenario.organization_id,
            source_version_id=self.scenario.source_version_id,
            worker_job_id=self.scenario.job_id,
            page_ref=self.page_ref,
            document_ref=self.document_ref,
            delete_observation=self.delete_observation,
        )


def accept_feishu_page(
    *,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    observed_at: datetime,
    document_ref: str,
    visibility: FeishuAclVisibility | None,
    subjects: tuple[FeishuPermissionSubject, ...] = (),
    group_snapshot: FeishuGroupSnapshot | None = None,
    identity_mappings: dict[str, FeishuIdentityMapping] | None = None,
    deleted: bool = False,
    acl_failed: bool = False,
    scenario: _Scenario | None = None,
    checkpoint_token: str = "checkpoint:1",
) -> AcceptedFeishuPage:
    if scenario is None:
        scenario = _seed_scenario(
            migration_configuration,
            guarded_control_engine,
            source_kind="feishu_docs",
            capability_manifest=FEISHU_DOCS_CAPABILITY_MANIFEST_JSON,
        )
    acl = (
        FeishuAclFailure(document_ref, observed_at)
        if acl_failed
        else FeishuAclResponse(
            document_ref=document_ref,
            visibility=visibility or FeishuAclVisibility.PRIVATE,
            subjects=subjects,
            observed_at=observed_at,
        )
    )
    source_page = FeishuChangePage(
        documents=(
            ()
            if deleted
            else (FeishuDocument(document_ref, "revision:1", b"# Synthetic\n"),)
        ),
        deleted_document_refs=(
            (FeishuDocumentDelete(document_ref, observed_at),) if deleted else ()
        ),
        next_page_token=None,
        checkpoint_token=checkpoint_token,
    )
    snapshot = group_snapshot or FeishuGroupSnapshot(
        "groups:v1",
        (),
        observed_at,
    )
    twin = SyntheticFeishuTwin(
        pages={None: source_page},
        acl_responses={document_ref: acl},
        identity_mappings=identity_mappings or {},
        group_snapshot=snapshot,
    )
    seed_feishu_subject_mappings(
        migration_configuration,
        scenario,
        identity_mappings=identity_mappings or {},
        group_snapshot=snapshot,
    )
    adapter = FeishuDocsConnectorAdapter.from_twin(twin)
    result = _bridge(
        scenario,
        guarded_worker_engine,
        PostgreSQLConnectorCheckpointStore(guarded_worker_engine),
    ).execute(scenario.execution, adapter)
    return AcceptedFeishuPage(
        scenario=scenario,
        page_ref=result.accepted_page_refs[0],
        document_ref=document_ref,
        delete_observation=deleted,
    )


def accept_feishu_observation_sequence(
    *,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    document_ref: str,
    observations: tuple[FeishuAclResponse | FeishuAclFailure, ...],
    identity_mappings: dict[str, FeishuIdentityMapping] | None = None,
    identity_sequences: dict[
        str,
        tuple[FeishuIdentityMapping | Exception, ...],
    ]
    | None = None,
    group_snapshot: FeishuGroupSnapshot | None = None,
    group_snapshot_sequence: tuple[FeishuGroupSnapshot | Exception, ...]
    | None = None,
) -> tuple[AcceptedFeishuPage, ...]:
    if len(observations) < 2:
        raise ValueError("Feishu observation sequence requires at least two pages")
    scenario = _seed_scenario(
        migration_configuration,
        guarded_control_engine,
        source_kind="feishu_docs",
        capability_manifest=FEISHU_DOCS_CAPABILITY_MANIFEST_JSON,
    )
    pages: dict[str | None, FeishuChangePage] = {}
    page_token: str | None = None
    for index in range(len(observations)):
        next_token = None if index == len(observations) - 1 else f"page:{index + 2}"
        pages[page_token] = FeishuChangePage(
            documents=(
                FeishuDocument(
                    document_ref,
                    f"revision:{index + 1}",
                    b"# Synthetic\n",
                ),
            ),
            deleted_document_refs=(),
            next_page_token=next_token,
            checkpoint_token=f"checkpoint:{index + 1}",
        )
        page_token = next_token
    initial_group_snapshot = group_snapshot or FeishuGroupSnapshot(
        "groups:v1",
        (),
        observations[0].observed_at,
    )
    twin = SyntheticFeishuTwin(
        pages=pages,
        acl_responses={},
        acl_sequences={document_ref: observations},
        identity_mappings=identity_mappings or {},
        group_snapshot=initial_group_snapshot,
        identity_sequences=identity_sequences,
        group_snapshot_sequence=group_snapshot_sequence,
    )
    seed_feishu_subject_mappings(
        migration_configuration,
        scenario,
        identity_mappings=identity_mappings or {},
        group_snapshot=initial_group_snapshot,
    )
    result = _bridge(
        scenario,
        guarded_worker_engine,
        PostgreSQLConnectorCheckpointStore(guarded_worker_engine),
    ).execute(
        scenario.execution,
        FeishuDocsConnectorAdapter.from_twin(twin),
    )
    return tuple(
        AcceptedFeishuPage(scenario, page_ref, document_ref, False)
        for page_ref in result.accepted_page_refs
    )


def seed_feishu_article(
    configuration: DatabaseConfiguration,
    accepted: AcceptedFeishuPage,
    *,
    local_policy_kind: str = "organization",
    principal_refs: tuple[str, ...] = (),
) -> None:
    engine = create_database_engine(configuration)
    try:
        with engine.begin() as connection:
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
                    "org": accepted.scenario.organization_id,
                    "resource": accepted.document_ref,
                    "source": str(accepted.scenario.source_id),
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO article_access_policy (
                        organization_id, resource_ref, policy_version,
                        local_policy_kind, local_group_refs, policy_kind,
                        group_refs, published, resolution_rung,
                        source_evidence_mode, source_observation_status,
                        source_observation_version, source_version_ref,
                        source_acl_as_of, source_declared_lag_seconds,
                        fixed_at_policy_epoch
                    ) VALUES (
                        :org, :resource, 1, :local_kind, ARRAY[]::text[],
                        NULL, ARRAY[]::text[], false, 'source_default',
                        'mirrored', 'missing', NULL, :version,
                        statement_timestamp(), 0, 1
                    )
                    """
                ),
                {
                    "org": accepted.scenario.organization_id,
                    "resource": accepted.document_ref,
                    "local_kind": local_policy_kind,
                    "version": accepted.scenario.source_version_id,
                },
            )
            for principal_ref in principal_refs:
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
                        "org": accepted.scenario.organization_id,
                        "resource": accepted.document_ref,
                        "principal": principal_ref,
                    },
                )
    finally:
        engine.dispose()


def seed_feishu_subject_mappings(
    configuration: DatabaseConfiguration,
    scenario: _Scenario,
    *,
    identity_mappings: dict[str, FeishuIdentityMapping],
    group_snapshot: FeishuGroupSnapshot,
) -> None:
    """Seed engine-owned twin mapping authority outside the runner boundary."""

    engine = create_database_engine(configuration)
    try:
        with engine.begin() as connection:
            for node in group_snapshot.nodes:
                if node.local_group_ref is None:
                    continue
                connection.execute(
                    text(
                        "INSERT INTO article_access_group "
                        "(organization_id, group_ref) VALUES (:org, :local) "
                        "ON CONFLICT DO NOTHING"
                    ),
                    {
                        "org": scenario.organization_id,
                        "local": node.local_group_ref,
                    },
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO feishu_subject_mapping (
                            organization_id, source_id, subject_kind,
                            external_ref, local_ref, mapping_version
                        ) VALUES (:org, :source, 'group', :external, :local, 1)
                        """
                    ),
                    {
                        "org": scenario.organization_id,
                        "source": scenario.source_id,
                        "external": node.external_ref,
                        "local": node.local_group_ref,
                    },
                )
            for mapping in identity_mappings.values():
                if mapping.local_principal_ref is None:
                    continue
                connection.execute(
                    text(
                        """
                        INSERT INTO feishu_subject_mapping (
                            organization_id, source_id, subject_kind,
                            external_ref, local_ref, mapping_version
                        ) VALUES (:org, :source, 'identity', :external, :local, 1)
                        """
                    ),
                    {
                        "org": scenario.organization_id,
                        "source": scenario.source_id,
                        "external": mapping.external_ref,
                        "local": mapping.local_principal_ref,
                    },
                )
    finally:
        engine.dispose()


def apply_feishu_page(
    guarded_control_engine: Engine,
    accepted: AcceptedFeishuPage,
) -> AppliedFeishuAclObservation:
    return PostgreSQLFeishuAclControl(guarded_control_engine).apply(accepted.command)


def cleanup_feishu_scenario(
    configuration: DatabaseConfiguration,
    organization_id: UUID,
) -> None:
    engine = create_database_engine(configuration)
    try:
        with engine.begin() as connection:
            connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            for table_name in ("context_fragment", "context_revision"):
                connection.execute(
                    text(
                        f"ALTER TABLE {table_name} DISABLE TRIGGER "  # noqa: S608
                        f"{table_name}_reject_mutation"
                    )
                )
            connection.execute(
                text(
                    "ALTER TABLE exact_phrase_candidate DISABLE TRIGGER "
                    "exact_phrase_candidate_immutable"
                )
            )
            connection.execute(
                text(
                    "UPDATE context_resource SET active_revision_id = NULL "
                    "WHERE organization_id = :org"
                ),
                {"org": organization_id},
            )
            for table_name in (
                "exact_phrase_candidate",
                "membership_resource_field_right",
                "context_fragment_field",
                "context_fragment",
                "revision_publication_event",
                "context_revision",
            ):
                connection.execute(
                    text(
                        f"DELETE FROM {table_name} "  # noqa: S608
                        "WHERE organization_id = :org"
                    ),
                    {"org": organization_id},
                )
            connection.execute(
                text("DELETE FROM context_resource WHERE organization_id = :org"),
                {"org": organization_id},
            )
            connection.execute(
                text(
                    "DELETE FROM article_source_acl_observation "
                    "WHERE organization_id = :org"
                ),
                {"org": organization_id},
            )
            for table_name in (
                "feishu_subject_mapping",
                "supply_connector_checkpoint",
                "supply_connector_accepted_page",
                "supply_connector_staged_page",
                "supply_connector_lease_event",
                "supply_connector_job",
            ):
                connection.execute(
                    text(
                        f"DELETE FROM {table_name} "  # noqa: S608
                        "WHERE organization_id = :org"
                    ),
                    {"org": organization_id},
                )
            connection.execute(
                text(
                    "ALTER TABLE source_version DISABLE TRIGGER "
                    "source_version_immutable"
                )
            )
            connection.execute(
                text("DELETE FROM context_source WHERE organization_id = :org"),
                {"org": organization_id},
            )
            connection.execute(
                text("DELETE FROM source_version WHERE organization_id = :org"),
                {"org": organization_id},
            )
            connection.execute(
                text("DELETE FROM membership WHERE organization_id = :org"),
                {"org": organization_id},
            )
            connection.execute(
                text("DELETE FROM organization WHERE organization_id = :org"),
                {"org": organization_id},
            )
        with engine.begin() as connection:
            for table_name in ("context_fragment", "context_revision"):
                connection.execute(
                    text(
                        f"ALTER TABLE {table_name} ENABLE TRIGGER "  # noqa: S608
                        f"{table_name}_reject_mutation"
                    )
                )
            connection.execute(
                text(
                    "ALTER TABLE exact_phrase_candidate ENABLE TRIGGER "
                    "exact_phrase_candidate_immutable"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE source_version ENABLE TRIGGER source_version_immutable"
                )
            )
    finally:
        engine.dispose()


def next_observation_time(offset: int = 0) -> datetime:
    return datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=2 - offset)


__all__ = [
    "AcceptedFeishuPage",
    "accept_feishu_page",
    "accept_feishu_observation_sequence",
    "apply_feishu_page",
    "cleanup_feishu_scenario",
    "next_observation_time",
    "seed_feishu_article",
    "seed_feishu_subject_mappings",
]
