from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import Engine, text

from adapters.embeddings import DeterministicEmbeddingTwin
from adapters.file_source import FileReadLimits, FileRootRegistry
from engine.persistence import (
    DatabaseConfiguration,
    FileImportInterrupted,
    FileImportLeaseRedemption,
    FilePublicationBoundary,
    PostgreSQLFileImportWorker,
    PostgreSQLWorkerLeaseIssuer,
    PublishedFileImport,
    create_database_engine,
)
from engine.supply import (
    CONTEXT_FRAGMENT_EMBEDDING_DIMENSION,
    DETERMINISTIC_TWIN_EMBEDDING_PROFILE,
    EmbeddingProfile,
    EmbeddingProviderProfile,
    EmbeddingProviderUnavailable,
    EmbeddingVector,
    MarkdownCompilerConfig,
    WorkNotAvailable,
)
from tests.support.embeddings import QwenEmbeddingTwin
from tests.support.file_imports import (
    FileImportScenario,
    delete_file_import_scenario,
    prepare_file_import_scenario,
    prepare_repeat_file_import,
)

pytestmark = pytest.mark.integration


class _RecordingEmbeddingProvider:
    def __init__(self, *, available: bool = True) -> None:
        self.profile = EmbeddingProfile(CONTEXT_FRAGMENT_EMBEDDING_DIMENSION)
        self.available = available
        self.calls: list[tuple[str, ...]] = []
        self._twin = DeterministicEmbeddingTwin()

    @property
    def provider_profile(self) -> EmbeddingProviderProfile:
        return DETERMINISTIC_TWIN_EMBEDDING_PROFILE

    def embed(self, inputs: tuple[str, ...]) -> tuple[EmbeddingVector, ...]:
        self.calls.append(inputs)
        if not self.available:
            raise EmbeddingProviderUnavailable("provider detail must not escape")
        return self._twin.embed(inputs)

    def embed_documents(
        self, inputs: tuple[str, ...]
    ) -> tuple[EmbeddingVector, ...]:
        return self.embed(inputs)


class _InvalidEmbeddingProvider(_RecordingEmbeddingProvider):
    def embed(self, inputs: tuple[str, ...]) -> tuple[EmbeddingVector, ...]:
        self.calls.append(inputs)
        return ((0.25,),) * len(inputs)


class _MutableProfileEmbeddingProvider(_RecordingEmbeddingProvider):
    def embed(self, inputs: tuple[str, ...]) -> tuple[EmbeddingVector, ...]:
        self.calls.append(inputs)
        self.profile = EmbeddingProfile(1)
        return ((0.25,),) * len(inputs)


def _worker(
    scenario: FileImportScenario,
    guarded_worker_engine: Engine,
    provider: _RecordingEmbeddingProvider,
) -> PostgreSQLFileImportWorker:
    return PostgreSQLFileImportWorker(
        guarded_worker_engine,
        scenario.codec,
        scenario.receiver,
        FileRootRegistry(
            {scenario.root_ref: scenario.root},
            limits=FileReadLimits(max_file_bytes=4096),
        ),
        MarkdownCompilerConfig("markdown-config-v2"),
        embedding_provider=provider,
        clock=lambda: datetime.now(UTC).replace(microsecond=0),
    )


def _run(
    worker: PostgreSQLFileImportWorker,
    scenario: FileImportScenario,
    token: Any,
) -> PublishedFileImport:
    return worker.run(
        FileImportLeaseRedemption(
            token,
            scenario.organization_id,
            scenario.prepared.job_id,
            scenario.source_ref,
        )
    )


def test_profile_change_reembeds_unchanged_content_as_replacement_revision(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
    )
    assert scenario.token is not None
    first = _run(
        _worker(scenario, guarded_worker_engine, _RecordingEmbeddingProvider()),
        scenario,
        scenario.token,
    )
    repeat, repeat_token = prepare_repeat_file_import(
        scenario,
        guarded_control_engine,
        idempotency_key="profile-change-reembed",
    )

    replacement = PostgreSQLFileImportWorker(
        guarded_worker_engine,
        scenario.codec,
        scenario.receiver,
        FileRootRegistry(
            {scenario.root_ref: scenario.root},
            limits=FileReadLimits(max_file_bytes=4096),
        ),
        MarkdownCompilerConfig("markdown-config-v2"),
        embedding_provider=QwenEmbeddingTwin(),
        clock=lambda: datetime.now(UTC).replace(microsecond=0),
    ).run(
        FileImportLeaseRedemption(
            repeat_token,
            repeat.organization_id,
            repeat.job_id,
            repeat.source_ref,
        )
    )

    assert replacement.outcome == "replaced"
    assert replacement.candidate_ref.revision_ref != first.candidate_ref.revision_ref


def _stored_vectors(
    configuration: DatabaseConfiguration,
    scenario: FileImportScenario,
) -> tuple[tuple[str, int], ...]:
    engine = create_database_engine(configuration)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT embedding::text, vector_dims(embedding)
                    FROM context_fragment
                    WHERE organization_id = :organization_id
                    ORDER BY ordinal
                    """
                ),
                {"organization_id": scenario.organization_id},
            ).all()
        return tuple((str(row[0]), int(row[1])) for row in rows)
    finally:
        engine.dispose()


def test_real_publication_persists_deterministic_vectors_and_noop_skips_provider(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=b"# Handbook\n\nFirst fragment.\n\nSecond fragment.\n",
    )
    request.addfinalizer(
        lambda: delete_file_import_scenario(
            migration_configuration, scenario.organization_id
        )
    )
    assert scenario.token is not None
    provider = _RecordingEmbeddingProvider()
    published = cast(
        Any,
        _run(
            _worker(scenario, guarded_worker_engine, provider),
            scenario,
            scenario.token,
        ),
    )

    vectors = _stored_vectors(migration_configuration, scenario)
    assert len(vectors) == len(published.candidate_refs)
    assert all(
        dimension == CONTEXT_FRAGMENT_EMBEDDING_DIMENSION
        for _vector, dimension in vectors
    )
    assert len(provider.calls) == 1
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            storage = connection.execute(
                text(
                    """
                    SELECT format_type(attribute.atttypid, attribute.atttypmod),
                           table_class.relrowsecurity,
                           table_class.relforcerowsecurity,
                           index_definition.indexdef
                    FROM pg_attribute AS attribute
                    JOIN pg_class AS table_class
                      ON table_class.oid = attribute.attrelid
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = table_class.relnamespace
                    JOIN pg_indexes AS index_definition
                      ON index_definition.schemaname = namespace.nspname
                     AND index_definition.tablename = table_class.relname
                     AND index_definition.indexname =
                           'ix_context_fragment_embedding_hnsw'
                    WHERE namespace.nspname = 'public'
                      AND table_class.relname = 'context_fragment'
                      AND attribute.attname = 'embedding'
                    """
                )
            ).one()
        assert tuple(storage[:3]) == ("vector(384)", True, True)
        assert "USING hnsw" in storage.indexdef
        assert "vector_cosine_ops" in storage.indexdef
        assert "WHERE (embedding IS NOT NULL)" in storage.indexdef
    finally:
        engine.dispose()

    prepared, replay_token = prepare_repeat_file_import(
        scenario,
        guarded_control_engine,
        idempotency_key="embedding-noop-replay",
    )
    replay = PostgreSQLFileImportWorker(
        guarded_worker_engine,
        scenario.codec,
        scenario.receiver,
        FileRootRegistry(
            {scenario.root_ref: scenario.root},
            limits=FileReadLimits(max_file_bytes=4096),
        ),
        MarkdownCompilerConfig("markdown-config-v2"),
        embedding_provider=provider,
        clock=lambda: datetime.now(UTC).replace(microsecond=0),
    ).run(
        FileImportLeaseRedemption(
            replay_token,
            prepared.organization_id,
            prepared.job_id,
            prepared.source_ref,
        )
    )

    assert replay.outcome == "unchanged"
    assert len(provider.calls) == 1
    assert _stored_vectors(migration_configuration, scenario) == vectors


def test_provider_failure_interrupts_acquired_checkpoint_and_recovers(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        lease_ttl_seconds=2,
    )
    request.addfinalizer(
        lambda: delete_file_import_scenario(
            migration_configuration, scenario.organization_id
        )
    )
    assert scenario.token is not None
    provider = _RecordingEmbeddingProvider(available=False)

    with pytest.raises(FileImportInterrupted) as interrupted:
        _run(
            _worker(scenario, guarded_worker_engine, provider),
            scenario,
            scenario.token,
        )

    assert interrupted.value.boundary is FilePublicationBoundary.ACQUIRED
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            before = connection.execute(
                text(
                    """
                    SELECT job.state, job.revision_id,
                           resource.active_revision_id,
                           (SELECT count(*) FROM context_fragment AS fragment
                            WHERE fragment.organization_id = job.organization_id)
                    FROM file_import_job AS job
                    LEFT JOIN context_resource AS resource
                      ON resource.organization_id = job.organization_id
                     AND resource.resource_ref = job.resource_ref
                    WHERE job.organization_id = :organization_id
                      AND job.job_id = :job_id
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "job_id": scenario.prepared.job_id,
                },
            ).one()
        assert tuple(before[:1]) == ("running",)
        assert before[1] is not None
        assert before[2] is None
        assert before[3] == 0
        with engine.connect() as connection:
            connection.execute(text("SELECT pg_sleep(2.1)"))
    finally:
        engine.dispose()

    provider.available = True
    recovery_token = PostgreSQLWorkerLeaseIssuer(
        guarded_control_engine,
        scenario.codec,
    ).issue_file_import_lease(scenario.prepared)
    recovered = cast(
        Any,
        _run(
            _worker(scenario, guarded_worker_engine, provider),
            scenario,
            recovery_token,
        ),
    )

    assert recovered.outcome == "published"
    assert len(provider.calls) == 2
    assert _stored_vectors(migration_configuration, scenario)


def test_worker_refuses_embedding_dimension_mismatch_at_composition(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
    )
    request.addfinalizer(
        lambda: delete_file_import_scenario(
            migration_configuration, scenario.organization_id
        )
    )
    provider = _RecordingEmbeddingProvider()
    provider.profile = EmbeddingProfile(CONTEXT_FRAGMENT_EMBEDDING_DIMENSION - 1)

    with pytest.raises(ValueError, match="dimension does not match"):
        _worker(scenario, guarded_worker_engine, provider)


def test_invalid_provider_response_interrupts_before_fragment_persistence(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
    )
    request.addfinalizer(
        lambda: delete_file_import_scenario(
            migration_configuration, scenario.organization_id
        )
    )
    assert scenario.token is not None
    provider = _InvalidEmbeddingProvider()

    with pytest.raises(FileImportInterrupted) as interrupted:
        _run(
            _worker(scenario, guarded_worker_engine, provider),
            scenario,
            scenario.token,
        )

    assert interrupted.value.boundary is FilePublicationBoundary.ACQUIRED
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT count(*) FROM context_fragment "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": scenario.organization_id},
            ).scalar_one() == 0
    finally:
        engine.dispose()


def test_provider_cannot_change_the_composed_dimension_during_publication(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
    )
    request.addfinalizer(
        lambda: delete_file_import_scenario(
            migration_configuration, scenario.organization_id
        )
    )
    assert scenario.token is not None

    with pytest.raises(FileImportInterrupted) as interrupted:
        _run(
            _worker(
                scenario,
                guarded_worker_engine,
                _MutableProfileEmbeddingProvider(),
            ),
            scenario,
            scenario.token,
        )

    assert interrupted.value.boundary is FilePublicationBoundary.ACQUIRED
    assert _stored_vectors(migration_configuration, scenario) == ()


def test_postgresql_refuses_a_vector_that_underflows_to_float32_zero(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
    )
    request.addfinalizer(
        lambda: delete_file_import_scenario(
            migration_configuration, scenario.organization_id
        )
    )
    assert scenario.token is not None

    def underflow_document(
        _worker: PostgreSQLFileImportWorker,
        _token: object,
        _claims: object,
        document: object,
    ) -> str:
        fragments = cast(Any, document).fragments
        return json.dumps(
            [
                {
                    "embedding": [1.0e-50]
                    * CONTEXT_FRAGMENT_EMBEDDING_DIMENSION,
                    "fragmentRef": fragment.fragment_ref,
                }
                for fragment in fragments
            ]
        )

    monkeypatch.setattr(
        PostgreSQLFileImportWorker,
        "_embedding_document",
        underflow_document,
    )

    with pytest.raises(WorkNotAvailable):
        _run(
            _worker(
                scenario,
                guarded_worker_engine,
                _RecordingEmbeddingProvider(),
            ),
            scenario,
            scenario.token,
        )

    assert _stored_vectors(migration_configuration, scenario) == ()
