from __future__ import annotations

import base64
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import ProgrammingError

from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLConnectorCheckpointStore,
    PostgreSQLStagedArtifactSink,
    PostgreSQLSupplyBridgeLeaseIssuer,
    PostgreSQLSupplyExecutionBridge,
    SupplyBridgeExecutionIdentity,
    SupplyBridgeLeaseIssueRequest,
    create_database_engine,
)
from engine.supply import WorkerLeaseCodec, WorkerLeaseKeyring
from engine.supply.execution import (
    ConnectorAdapter,
    ConnectorCheckpointBinding,
    ConnectorCheckpointStore,
    SourceAclEvidenceClass,
    SourceAclObservation,
    StagedArtifact,
    StagedArtifactSink,
    SupplyBridgeExecution,
    SupplyChangePage,
    SupplyDocumentEnvelope,
    serialize_supply_change_page,
)
from engine.supply.jobs import WorkerLeaseClaims

pytestmark = pytest.mark.integration
SIGNING_KEY = bytes(range(32))


@dataclass(frozen=True, slots=True)
class _Scenario:
    organization_id: UUID
    source_id: UUID
    source_version_id: UUID
    job_id: UUID
    service_principal_id: UUID
    codec: WorkerLeaseCodec
    execution: SupplyBridgeExecution


class _TwoPageAdapter(ConnectorAdapter):
    def __init__(self, pages: tuple[SupplyChangePage, ...]) -> None:
        self._pages = {page.checkpoint_proposal: page for page in pages}
        self._ordered = pages
        self.loaded_checkpoints: list[bytes | None] = []
        self.emitted_pages: list[SupplyChangePage] = []
        self.emitted_page_refs: list[str] = []
        self._checkpoint: bytes | None = None

    def load_checkpoint(self, opaque_checkpoint: bytes | None) -> None:
        self.loaded_checkpoints.append(opaque_checkpoint)
        self._checkpoint = opaque_checkpoint

    def load(self, binding: ConnectorCheckpointBinding) -> SupplyChangePage:
        del binding
        page = self._ordered[0]
        self.emitted_pages.append(page)
        self.emitted_page_refs.append(page.page_ref)
        return page

    def poll(self, binding: ConnectorCheckpointBinding) -> SupplyChangePage:
        del binding
        if self._checkpoint is None:
            page = self._ordered[1]
        else:
            prior_index = next(
                index
                for index, candidate in enumerate(self._ordered)
                if candidate.checkpoint_proposal == self._checkpoint
            )
            page = self._ordered[prior_index + 1]
        self.emitted_pages.append(page)
        self.emitted_page_refs.append(page.page_ref)
        return page


class _FailBeforeAtomicAcceptance(StagedArtifactSink):
    def __init__(self, inner: StagedArtifactSink, page_ref: str) -> None:
        self._inner = inner
        self._page_ref = page_ref

    def accept_change_page(
        self,
        connection: Connection,
        page: SupplyChangePage,
        *,
        lease_claims: WorkerLeaseClaims,
    ) -> None:
        if page.page_ref == self._page_ref:
            raise RuntimeError("injected failure before atomic acceptance")
        self._inner.accept_change_page(
            connection,
            page,
            lease_claims=lease_claims,
        )

    def load(
        self,
        binding: ConnectorCheckpointBinding,
        artifact_ref: str,
        *,
        lease_claims: WorkerLeaseClaims,
    ) -> StagedArtifact | None:
        return self._inner.load(
            binding,
            artifact_ref,
            lease_claims=lease_claims,
        )


class _FailAfterAtomicAcceptance(StagedArtifactSink):
    def __init__(self, inner: StagedArtifactSink, page_ref: str) -> None:
        self._inner = inner
        self._page_ref = page_ref

    def accept_change_page(
        self,
        connection: Connection,
        page: SupplyChangePage,
        *,
        lease_claims: WorkerLeaseClaims,
    ) -> None:
        self._inner.accept_change_page(
            connection,
            page,
            lease_claims=lease_claims,
        )
        if page.page_ref == self._page_ref:
            raise RuntimeError("injected failure after atomic acceptance")

    def load(
        self,
        binding: ConnectorCheckpointBinding,
        artifact_ref: str,
        *,
        lease_claims: WorkerLeaseClaims,
    ) -> StagedArtifact | None:
        return self._inner.load(
            binding,
            artifact_ref,
            lease_claims=lease_claims,
        )


def _seed_scenario(
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    *,
    source_id: UUID | None = None,
    source_version_id: UUID | None = None,
    job_id: UUID | None = None,
) -> _Scenario:
    organization_id = uuid4()
    source_id = source_id or uuid4()
    source_version_id = source_version_id or uuid4()
    job_id = job_id or uuid4()
    service_principal_id = uuid4()
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text("INSERT INTO organization (organization_id) VALUES (:org)"),
                {"org": organization_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO context_source (
                        organization_id, source_id, display_name, source_kind,
                        registration_operation, idempotency_key,
                        registration_digest, active_version_id, created_at
                    ) VALUES (
                        :org, :source, 'Synthetic connector source', 'file',
                        'register_source', :idempotency_key, :digest,
                        :version, clock_timestamp()
                    )
                    """
                ),
                {
                    "org": organization_id,
                    "source": source_id,
                    "version": source_version_id,
                    "idempotency_key": f"connector-{source_id.hex}",
                    "digest": source_id.hex * 2,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO source_version (
                        organization_id, source_id, version_id, source_kind,
                        root_ref, capability_manifest, created_at
                    ) VALUES (
                        :org, :source, :version, 'file', :root,
                        CAST(:manifest AS jsonb), clock_timestamp()
                    )
                    """
                ),
                {
                    "org": organization_id,
                    "source": source_id,
                    "version": source_version_id,
                    "root": f"connector-{source_id.hex}",
                    "manifest": (
                        '{"aclEvidenceMode":"mirrored",'
                        '"authorizeAndProject":"unavailable",'
                        '"batchLimits":"unavailable","checkpoint":"unavailable",'
                        '"checkpointSemantics":"unavailable",'
                        '"contentKinds":["markdown"],'
                        '"consistencyGuarantees":"unavailable",'
                        '"cursorSemantics":"unavailable",'
                        '"declarationVersion":"file-capabilities-v1",'
                        '"deletion":"unavailable",'
                        '"describeCapabilities":"unavailable",'
                        '"discover":"unavailable",'
                        '"fileSourceAccess":"unavailable",'
                        '"freshness":"unavailable",'
                        '"ingestionJobs":"unavailable",'
                        '"projectionFields":[],"readChanges":"unavailable",'
                        '"resourceKinds":["markdown_document"],'
                        '"sourceMode":"materialized"}'
                    ),
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO service_principal (
                        organization_id, service_principal_id, workload,
                        worker_audience, operation, enabled
                    ) VALUES (
                        :org, :principal, 'supply.connector',
                        'context-engine-connector-runner',
                        'connector.execute', true
                    )
                    """
                ),
                {"org": organization_id, "principal": service_principal_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO supply_connector_job (
                        organization_id, source_id, source_version_id,
                        worker_job_id,
                        service_principal_id, workload, worker_audience,
                        actor_kind, operation, state, lease_generation,
                        created_at
                    ) VALUES (
                        :org, :source, :version, :job, :principal,
                        'supply.connector', 'context-engine-connector-runner',
                        'service', 'connector.execute', 'available', 0,
                        clock_timestamp()
                    )
                    """
                ),
                {
                    "org": organization_id,
                    "source": source_id,
                    "version": source_version_id,
                    "job": job_id,
                    "principal": service_principal_id,
                },
            )
    finally:
        migration_engine.dispose()

    codec = WorkerLeaseCodec(
        WorkerLeaseKeyring(active_version=1, keys={1: SIGNING_KEY})
    )
    token = PostgreSQLSupplyBridgeLeaseIssuer(guarded_control_engine, codec).issue(
        SupplyBridgeLeaseIssueRequest(
            organization_id=organization_id,
            source_id=source_id,
            source_version_id=source_version_id,
            worker_job_id=job_id,
            service_principal_id=service_principal_id,
        )
    )
    return _Scenario(
        organization_id=organization_id,
        source_id=source_id,
        source_version_id=source_version_id,
        job_id=job_id,
        service_principal_id=service_principal_id,
        codec=codec,
        execution=SupplyBridgeExecution(
            organization_id=organization_id,
            source_version_id=source_version_id,
            worker_job_id=job_id,
            worker_lease=token,
        ),
    )


def _page(
    scenario: _Scenario,
    ordinal: int,
    *,
    terminal: bool,
) -> SupplyChangePage:
    binding = ConnectorCheckpointBinding(
        organization_id=scenario.organization_id,
        source_version_id=scenario.source_version_id,
        worker_job_id=scenario.job_id,
    )
    acl = SourceAclObservation(
        organization_id=scenario.organization_id,
        evidence_class=SourceAclEvidenceClass.MIRRORED,
        evidence_payload=f"synthetic-acl-{ordinal}".encode(),
    )
    return SupplyChangePage(
        binding=binding,
        page_ref=f"page:{ordinal}",
        documents=(
            SupplyDocumentEnvelope(
                organization_id=scenario.organization_id,
                source_version_id=scenario.source_version_id,
                worker_job_id=scenario.job_id,
                document_ref=f"document:{ordinal}",
                content=f"# Synthetic page {ordinal}".encode(),
                content_type="text/markdown",
                acl_observation=acl,
            ),
        ),
        deleted_document_refs=(),
        checkpoint_proposal=f"opaque-checkpoint-{ordinal}".encode(),
        terminal=terminal,
    )


def _claims(scenario: _Scenario) -> WorkerLeaseClaims:
    claims = scenario.codec.verify(
        scenario.execution.worker_lease,
        expected_organization_id=scenario.organization_id,
        expected_job_id=scenario.job_id,
        expected_service_principal_id=scenario.service_principal_id,
        expected_workload="supply.connector",
        expected_operation="connector.execute",
        expected_worker_audience="context-engine-connector-runner",
        expected_source_version_ref=str(scenario.source_version_id),
        now=datetime.now(UTC).replace(microsecond=0),
    )
    assert claims.policy_epoch is not None
    assert claims.service_actor_expires_at is not None
    return claims


def _bridge(
    scenario: _Scenario,
    guarded_worker_engine: Engine,
    store: ConnectorCheckpointStore,
    staged_sink: StagedArtifactSink | None = None,
) -> PostgreSQLSupplyExecutionBridge:
    claims = _claims(scenario)
    policy_epoch = claims.policy_epoch
    actor_expiry = claims.service_actor_expires_at
    idempotency_key = claims.idempotency_key
    assert policy_epoch is not None
    assert actor_expiry is not None
    assert idempotency_key is not None
    return PostgreSQLSupplyExecutionBridge(
        guarded_worker_engine,
        scenario.codec,
        SupplyBridgeExecutionIdentity(
            organization_id=scenario.organization_id,
            service_principal_id=scenario.service_principal_id,
            allowed_source_version_ids=(scenario.source_version_id,),
            allowed_operations=("connector.execute",),
            policy_epoch=policy_epoch,
            idempotency_key=idempotency_key,
            expires_at=actor_expiry,
        ),
        store,
        staged_sink or PostgreSQLStagedArtifactSink(guarded_worker_engine),
    )


@pytest.fixture
def scenarios(
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
) -> Iterator[list[_Scenario]]:
    created: list[_Scenario] = []
    yield created
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE source_version DISABLE TRIGGER "
                    "source_version_immutable"
                )
            )
            for scenario in created:
                for table in (
                    "supply_connector_checkpoint",
                    "supply_connector_accepted_page",
                    "supply_connector_staged_page",
                    "supply_connector_job",
                ):
                    connection.execute(
                        text(
                            f"DELETE FROM {table} "  # noqa: S608
                            "WHERE organization_id = :org"
                        ),
                        {"org": scenario.organization_id},
                    )
                connection.execute(
                    text("DELETE FROM context_source WHERE organization_id = :org"),
                    {"org": scenario.organization_id},
                )
                connection.execute(
                    text("DELETE FROM source_version WHERE organization_id = :org"),
                    {"org": scenario.organization_id},
                )
                connection.execute(
                    text("DELETE FROM organization WHERE organization_id = :org"),
                    {"org": scenario.organization_id},
                )
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE source_version ENABLE TRIGGER source_version_immutable"
                )
            )
    finally:
        migration_engine.dispose()


def test_checkpoint_advances_only_with_durably_accepted_change_pages(
    scenarios: list[_Scenario],
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _seed_scenario(migration_configuration, guarded_control_engine)
    scenarios.append(scenario)
    pages = (_page(scenario, 1, terminal=False), _page(scenario, 2, terminal=True))
    adapter = _TwoPageAdapter(pages)
    store = PostgreSQLConnectorCheckpointStore(guarded_worker_engine)
    staged_sink = PostgreSQLStagedArtifactSink(guarded_worker_engine)

    result = _bridge(scenario, guarded_worker_engine, store).execute(
        scenario.execution,
        adapter,
    )

    assert result.accepted_page_refs == ("page:1", "page:2")
    assert adapter.loaded_checkpoints == [None, b"opaque-checkpoint-1"]
    assert store.load(
        scenario.execution.binding,
        lease_claims=_claims(scenario),
    ) == (b"opaque-checkpoint-2")
    for page in pages:
        artifact = staged_sink.load(
            page.binding,
            page.page_ref,
            lease_claims=_claims(scenario),
        )
        assert artifact is not None
        assert artifact.payload == serialize_supply_change_page(page)
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT page_ref FROM supply_connector_accepted_page
                    WHERE organization_id = :org AND worker_job_id = :job
                    ORDER BY accepted_ordinal
                    """
                ),
                {"org": scenario.organization_id, "job": scenario.job_id},
            ).scalars().all() == ["page:1", "page:2"]
    finally:
        migration_engine.dispose()


def test_staged_payload_round_trip_preserves_every_emitted_page_fact(
    scenarios: list[_Scenario],
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _seed_scenario(migration_configuration, guarded_control_engine)
    scenarios.append(scenario)
    binding = scenario.execution.binding
    acl = SourceAclObservation(
        organization_id=scenario.organization_id,
        evidence_class=SourceAclEvidenceClass.LIVE,
        evidence_payload=b"\x00synthetic-live-acl\xff",
    )
    page = SupplyChangePage(
        binding=binding,
        page_ref="page:full-payload",
        documents=(
            SupplyDocumentEnvelope(
                organization_id=scenario.organization_id,
                source_version_id=scenario.source_version_id,
                worker_job_id=scenario.job_id,
                document_ref="document:full-payload",
                content=b"\x00# Synthetic payload\n\xff",
                content_type="application/octet-stream",
                acl_observation=acl,
                metadata=(("language", "zh-CN"), ("source_revision", "r:7")),
            ),
        ),
        deleted_document_refs=("document:deleted",),
        checkpoint_proposal=b"\x00opaque-checkpoint\xff",
        terminal=True,
    )
    _bridge(
        scenario,
        guarded_worker_engine,
        PostgreSQLConnectorCheckpointStore(guarded_worker_engine),
    ).execute(scenario.execution, _TwoPageAdapter((page,)))

    artifact = PostgreSQLStagedArtifactSink(guarded_worker_engine).load(
        binding,
        page.page_ref,
        lease_claims=_claims(scenario),
    )
    assert artifact is not None
    assert artifact.payload == serialize_supply_change_page(page)
    assert json.loads(artifact.payload) == {
        "binding": {
            "organization_id": str(scenario.organization_id),
            "source_version_id": str(scenario.source_version_id),
            "worker_job_id": str(scenario.job_id),
        },
        "checkpoint_proposal": base64.b64encode(page.checkpoint_proposal).decode(
            "ascii"
        ),
        "deleted_document_refs": ["document:deleted"],
        "documents": [
            {
                "acl_observation": {
                    "evidence_class": "live",
                    "evidence_payload": base64.b64encode(
                        b"\x00synthetic-live-acl\xff"
                    ).decode("ascii"),
                    "organization_id": str(scenario.organization_id),
                    "source_lacks_stronger_acl": None,
                },
                "content": base64.b64encode(b"\x00# Synthetic payload\n\xff").decode(
                    "ascii"
                ),
                "content_type": "application/octet-stream",
                "document_ref": "document:full-payload",
                "metadata": [
                    ["language", "zh-CN"],
                    ["source_revision", "r:7"],
                ],
                "organization_id": str(scenario.organization_id),
                "source_version_id": str(scenario.source_version_id),
                "worker_job_id": str(scenario.job_id),
            }
        ],
        "page_ref": "page:full-payload",
        "terminal": True,
    }


@pytest.mark.parametrize(
    ("mutated_path", "mutated_value"),
    [
        (("binding", "organization_id"), lambda: str(uuid4())),
        (("binding", "source_version_id"), lambda: str(uuid4())),
        (("binding", "worker_job_id"), lambda: str(uuid4())),
        (("page_ref",), lambda: "page:mismatched-payload-ref"),
    ],
)
def test_atomic_acceptance_refuses_payload_outside_the_exact_binding(
    scenarios: list[_Scenario],
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    mutated_path: tuple[str, ...],
    mutated_value: Callable[[], str],
) -> None:
    scenario = _seed_scenario(migration_configuration, guarded_control_engine)
    scenarios.append(scenario)
    page = _page(scenario, 1, terminal=False)
    claims = _claims(scenario)
    payload = json.loads(serialize_supply_change_page(page))
    target = payload
    for path_element in mutated_path[:-1]:
        target = target[path_element]
    target[mutated_path[-1]] = mutated_value()
    mutated_payload = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")

    with guarded_worker_engine.begin() as connection:
        row = connection.execute(
            text(
                """
                SELECT accepted_ordinal
                FROM public.context_supply_accept_connector_page(
                    :organization_id, :source_version_id, :worker_job_id,
                    :service_principal_id, :page_ref, :page_payload,
                    :lease_generation, :signing_key_version, :nonce,
                    :issued_at, :expires_at, :policy_epoch,
                    :idempotency_key, :allowed_source_version_ids,
                    :allowed_operations, :service_actor_expires_at
                )
                """
            ),
            {
                "organization_id": scenario.organization_id,
                "source_version_id": scenario.source_version_id,
                "worker_job_id": scenario.job_id,
                "service_principal_id": scenario.service_principal_id,
                "page_ref": page.page_ref,
                "page_payload": mutated_payload,
                "lease_generation": claims.lease_generation,
                "signing_key_version": claims.signing_key_version,
                "nonce": claims.nonce,
                "issued_at": claims.issued_at,
                "expires_at": claims.expires_at,
                "policy_epoch": claims.policy_epoch,
                "idempotency_key": claims.idempotency_key,
                "allowed_source_version_ids": [scenario.source_version_id],
                "allowed_operations": ["connector.execute"],
                "service_actor_expires_at": claims.service_actor_expires_at,
            },
        ).one_or_none()

    assert row is None
    store = PostgreSQLConnectorCheckpointStore(guarded_worker_engine)
    assert store.load(page.binding, lease_claims=claims) is None
    assert (
        PostgreSQLStagedArtifactSink(guarded_worker_engine).load(
            page.binding,
            page.page_ref,
            lease_claims=claims,
        )
        is None
    )


def test_checkpoint_comes_only_from_page_and_no_stage_mutator_exists(
    scenarios: list[_Scenario],
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _seed_scenario(migration_configuration, guarded_control_engine)
    scenarios.append(scenario)
    page = replace(
        _page(scenario, 1, terminal=True),
        checkpoint_proposal=b"opaque-from-the-accepted-page-only",
    )
    store = PostgreSQLConnectorCheckpointStore(guarded_worker_engine)

    _bridge(scenario, guarded_worker_engine, store).execute(
        scenario.execution,
        _TwoPageAdapter((page,)),
    )

    assert store.load(page.binding, lease_claims=_claims(scenario)) == (
        b"opaque-from-the-accepted-page-only"
    )
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM pg_catalog.pg_proc AS procedure
                        JOIN pg_catalog.pg_namespace AS namespace
                          ON namespace.oid = procedure.pronamespace
                        WHERE namespace.nspname = 'public'
                          AND procedure.proname =
                              'context_supply_stage_connector_page'
                        """
                    )
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text(
                        """
                    SELECT state
                    FROM supply_connector_job
                    WHERE organization_id = :organization_id
                      AND worker_job_id = :worker_job_id
                    """
                    ),
                    {
                        "organization_id": scenario.organization_id,
                        "worker_job_id": scenario.job_id,
                    },
                ).scalar_one()
                == "completed"
            )
    finally:
        migration_engine.dispose()


@pytest.mark.parametrize(
    "failing_sink_type",
    [_FailBeforeAtomicAcceptance, _FailAfterAtomicAcceptance],
)
def test_rollback_leaves_prior_checkpoint_and_resume_reemits_exact_page(
    scenarios: list[_Scenario],
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    failing_sink_type: type[_FailBeforeAtomicAcceptance]
    | type[_FailAfterAtomicAcceptance],
) -> None:
    scenario = _seed_scenario(migration_configuration, guarded_control_engine)
    scenarios.append(scenario)
    pages = (_page(scenario, 1, terminal=False), _page(scenario, 2, terminal=True))
    durable_store = PostgreSQLConnectorCheckpointStore(guarded_worker_engine)
    durable_sink = PostgreSQLStagedArtifactSink(guarded_worker_engine)
    failing_adapter = _TwoPageAdapter(pages)

    with pytest.raises(RuntimeError, match="injected failure"):
        _bridge(
            scenario,
            guarded_worker_engine,
            durable_store,
            failing_sink_type(durable_sink, "page:2"),
        ).execute(scenario.execution, failing_adapter)

    assert durable_store.load(
        scenario.execution.binding,
        lease_claims=_claims(scenario),
    ) == (b"opaque-checkpoint-1")
    assert (
        durable_sink.load(
            pages[0].binding,
            pages[0].page_ref,
            lease_claims=_claims(scenario),
        )
        is not None
    )
    assert (
        durable_sink.load(
            pages[1].binding,
            pages[1].page_ref,
            lease_claims=_claims(scenario),
        )
        is None
    )
    resumed_adapter = _TwoPageAdapter(pages)
    resumed = _bridge(scenario, guarded_worker_engine, durable_store).execute(
        scenario.execution,
        resumed_adapter,
    )
    assert resumed.accepted_page_refs == ("page:2",)
    assert resumed_adapter.loaded_checkpoints == [b"opaque-checkpoint-1"]
    assert resumed_adapter.emitted_page_refs == ["page:2"]
    assert resumed_adapter.emitted_pages == [pages[1]]
    resumed_artifact = durable_sink.load(
        pages[1].binding,
        pages[1].page_ref,
        lease_claims=_claims(scenario),
    )
    assert resumed_artifact is not None
    assert resumed_artifact.payload == serialize_supply_change_page(pages[1])


def test_cross_organization_and_source_version_checkpoint_reads_return_none(
    scenarios: list[_Scenario],
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    colliding_source_id = uuid4()
    colliding_source_version_id = uuid4()
    colliding_job_id = uuid4()
    first = _seed_scenario(
        migration_configuration,
        guarded_control_engine,
        source_id=colliding_source_id,
        source_version_id=colliding_source_version_id,
        job_id=colliding_job_id,
    )
    second = _seed_scenario(
        migration_configuration,
        guarded_control_engine,
        source_id=colliding_source_id,
        source_version_id=colliding_source_version_id,
        job_id=colliding_job_id,
    )
    scenarios.extend((first, second))
    store = PostgreSQLConnectorCheckpointStore(guarded_worker_engine)
    _bridge(first, guarded_worker_engine, store).execute(
        first.execution,
        _TwoPageAdapter((_page(first, 1, terminal=True),)),
    )

    assert store.load(second.execution.binding, lease_claims=_claims(second)) is None
    assert (
        PostgreSQLStagedArtifactSink(guarded_worker_engine).load(
            second.execution.binding,
            "page:1",
            lease_claims=_claims(second),
        )
        is None
    )
    assert (
        store.load(
            ConnectorCheckpointBinding(
                organization_id=first.organization_id,
                source_version_id=uuid4(),
                worker_job_id=first.job_id,
            ),
            lease_claims=_claims(first),
        )
        is None
    )


@pytest.mark.security_evidence(id="PG-SUPPLY-BRIDGE-125", layer="postgres")
def test_supply_bridge_tables_are_force_rls_function_only_and_exactly_bound(
    scenarios: list[_Scenario],
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    guarded_runtime_engine: Engine,
) -> None:
    """PG-SUPPLY-BRIDGE-125: direct access and wrong binding have zero effect."""

    scenario = _seed_scenario(migration_configuration, guarded_control_engine)
    scenarios.append(scenario)
    store = PostgreSQLConnectorCheckpointStore(guarded_worker_engine)
    _bridge(scenario, guarded_worker_engine, store).execute(
        scenario.execution,
        _TwoPageAdapter((_page(scenario, 1, terminal=True),)),
    )

    for engine in (guarded_worker_engine, guarded_runtime_engine):
        for table in (
            "supply_connector_job",
            "supply_connector_accepted_page",
            "supply_connector_checkpoint",
            "supply_connector_staged_page",
        ):
            with (
                pytest.raises(ProgrammingError, match="permission denied for table"),
                engine.connect() as connection,
            ):
                connection.execute(text(f"SELECT * FROM {table}")).all()

    assert (
        store.load(
            ConnectorCheckpointBinding(
                organization_id=scenario.organization_id,
                source_version_id=uuid4(),
                worker_job_id=scenario.job_id,
            ),
            lease_claims=_claims(scenario),
        )
        is None
    )

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            relations = connection.execute(
                text(
                    """
                    SELECT relation.relname, relation.relrowsecurity,
                           relation.relforcerowsecurity,
                           pg_get_userbyid(relation.relowner)
                    FROM pg_class AS relation
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = relation.relnamespace
                    WHERE namespace.nspname = 'public'
                      AND relation.relname IN (
                          'supply_connector_job',
                          'supply_connector_accepted_page',
                          'supply_connector_checkpoint'
                          , 'supply_connector_staged_page'
                      )
                    ORDER BY relation.relname
                    """
                )
            ).all()
            privileges = {
                (role, table_name, privilege): connection.execute(
                    text("SELECT has_table_privilege(:role, :table_name, :privilege)"),
                    {
                        "role": role,
                        "table_name": f"public.{table_name}",
                        "privilege": privilege,
                    },
                ).scalar_one()
                for role in (
                    "context_engine_control",
                    "context_engine_worker",
                    "context_engine_runtime",
                )
                for table_name in (
                    "supply_connector_job",
                    "supply_connector_accepted_page",
                    "supply_connector_checkpoint",
                    "supply_connector_staged_page",
                )
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE")
            }
    finally:
        migration_engine.dispose()

    assert [tuple(row) for row in relations] == [
        (
            "supply_connector_accepted_page",
            True,
            True,
            "context_engine_migrator",
        ),
        ("supply_connector_checkpoint", True, True, "context_engine_migrator"),
        ("supply_connector_job", True, True, "context_engine_migrator"),
        ("supply_connector_staged_page", True, True, "context_engine_migrator"),
    ]
    assert not {key for key, granted in privileges.items() if granted}
