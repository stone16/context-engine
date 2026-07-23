from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from adapters.file_source import FileReadLimits, FileRootRegistry
from adapters.parsers.markdown import compile_markdown
from engine.control import FileRootRef
from engine.persistence import (
    DatabaseConfiguration,
    FileImportInterrupted,
    FileImportLeaseRedemption,
    FilePublicationBoundary,
    PostgreSQLFileImportWorker,
    PostgreSQLWorkerLeaseIssuer,
    WorkerLeaseIssueNotAvailable,
    create_database_engine,
)
from engine.supply import (
    MarkdownCompilerConfig,
    ParsedDocument,
    WorkNotAvailable,
    canonicalize_parsed_document,
)
from tests.integration.test_file_import_tracer import (
    _FileImportScenario,
    _prepare_file_import_scenario,
    _prepare_repeat_file_import,
    _redeem_direct,
    _run_file_import,
    _scenario_claims,
)
from tests.integration.test_zz_file_revision_replacement import (
    NEW_MARKDOWN,
    NEW_V1_MARKDOWN,
    OLD_MARKDOWN,
    OLD_V1_MARKDOWN,
    _resolve,
    _scenario_user_id,
)

pytestmark = pytest.mark.integration


def _worker(
    scenario: _FileImportScenario,
    guarded_worker_engine: Engine,
    *,
    config_version: str,
    interrupt_after: FilePublicationBoundary | None = None,
) -> PostgreSQLFileImportWorker:
    return PostgreSQLFileImportWorker(
        guarded_worker_engine,
        scenario.codec,
        scenario.receiver,
        FileRootRegistry(
            {scenario.root_ref: scenario.root},
            limits=FileReadLimits(max_file_bytes=4096),
        ),
        MarkdownCompilerConfig(config_version),
        clock=lambda: datetime.now(UTC).replace(microsecond=0),
        interrupt_after=interrupt_after,
    )


def _run(
    worker: PostgreSQLFileImportWorker,
    scenario: _FileImportScenario,
    token: object,
) -> object:
    return worker.run(
        FileImportLeaseRedemption(
            cast(Any, token),
            scenario.organization_id,
            scenario.prepared.job_id,
            scenario.source_ref,
        )
    )


def _wait_for_expiry(configuration: DatabaseConfiguration) -> None:
    engine = create_database_engine(configuration)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT pg_sleep(2.1)"))
    finally:
        engine.dispose()


def _job_snapshot(
    configuration: DatabaseConfiguration,
    scenario: _FileImportScenario,
) -> tuple[object, ...]:
    engine = create_database_engine(configuration)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT job.state, job.lease_generation,
                           job.resource_ref, job.revision_id,
                           resource.active_revision_id,
                           (SELECT count(*) FROM context_revision AS revision
                            WHERE revision.organization_id = job.organization_id),
                           (SELECT count(*) FROM context_fragment AS fragment
                            WHERE fragment.organization_id = job.organization_id),
                           (SELECT count(*) FROM exact_phrase_candidate AS candidate
                            WHERE candidate.organization_id = job.organization_id),
                           (SELECT count(*) FROM file_import_job AS counted_job
                            WHERE counted_job.organization_id = job.organization_id),
                           (SELECT count(*)
                            FROM context_resource AS counted_resource
                            WHERE counted_resource.organization_id =
                                  job.organization_id)
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
        return tuple(row)
    finally:
        engine.dispose()


def _history(
    configuration: DatabaseConfiguration,
    scenario: _FileImportScenario,
) -> tuple[tuple[object, ...], ...]:
    engine = create_database_engine(configuration)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT event_type, boundary, lease_generation,
                           state_at_event, revision_id,
                           reason_digest IS NOT NULL
                    FROM file_import_job_event
                    WHERE organization_id = :organization_id
                      AND job_id = :job_id
                    ORDER BY ordinal
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "job_id": scenario.prepared.job_id,
                },
            ).all()
        return tuple(tuple(row) for row in rows)
    finally:
        engine.dispose()


@pytest.mark.parametrize(
        (
            "payload",
            "config_version",
            "expected_fragments",
            "expected_candidates",
        ),
        [
            (OLD_V1_MARKDOWN, "markdown-config-v1", 1, 1),
            (OLD_MARKDOWN, "markdown-config-v2", 4, 6),
    ],
)
@pytest.mark.parametrize(
    ("boundary", "interrupted_state", "pre_recovery_counts"),
    [
        (FilePublicationBoundary.ACQUIRED, "running", (0, 0, 0)),
        (FilePublicationBoundary.PREPARED, "prepared", None),
        (FilePublicationBoundary.INDEXED, "ready", None),
    ],
)
def test_each_durable_boundary_recovers_once_with_exact_lineage(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    guarded_runtime_engine: Engine,
    query_digest_keyring: object,
    payload: bytes,
    config_version: str,
    expected_fragments: int,
    expected_candidates: int,
    boundary: FilePublicationBoundary,
    interrupted_state: str,
    pre_recovery_counts: tuple[int, int, int] | None,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=payload,
        lease_ttl_seconds=2,
    )
    assert scenario.token is not None
    old_claims = _scenario_claims(scenario)

    with pytest.raises(FileImportInterrupted) as interrupted:
        _run(
            _worker(
                scenario,
                guarded_worker_engine,
                config_version=config_version,
                interrupt_after=boundary,
            ),
            scenario,
            scenario.token,
        )
    assert interrupted.value.boundary is boundary

    before = _job_snapshot(migration_configuration, scenario)
    assert before[0] == interrupted_state
    assert before[1] == 1
    if pre_recovery_counts is not None:
        assert tuple(before[5:8]) == pre_recovery_counts
        assert tuple(before[8:]) == (1, 0)
    elif boundary is FilePublicationBoundary.PREPARED:
        assert tuple(before[5:]) == (1, expected_fragments, 0, 1, 1)
    else:
        assert tuple(before[5:]) == (
            1,
            expected_fragments,
            expected_candidates,
            1,
            1,
        )
    assert _history(migration_configuration, scenario)[-1][:2] == (
        "interrupted",
        boundary.value,
    )
    interrupted_revision_id = before[3]
    user_id = _scenario_user_id(scenario, migration_configuration)
    hidden = _resolve(
        scenario,
        guarded_runtime_engine,
        cast(Any, query_digest_keyring),
        user_id=user_id,
        query="OLD marker.",
        request_id=f"initial-{config_version}-{boundary.value}-hidden",
    )
    assert hidden["blocks"] == []
    assert hidden["evidence"] == []

    _wait_for_expiry(migration_configuration)
    recovery_token = PostgreSQLWorkerLeaseIssuer(
        guarded_control_engine,
        scenario.codec,
    ).issue_file_import_lease(scenario.prepared)
    assert _redeem_direct(guarded_worker_engine, old_claims) is None
    recovered = cast(
        Any,
        _run(
            _worker(
                scenario,
                guarded_worker_engine,
                config_version=config_version,
            ),
            scenario,
            recovery_token,
        ),
    )

    after = _job_snapshot(migration_configuration, scenario)
    assert after[0] == "completed"
    assert after[1] == 2
    assert after[2] == recovered.candidate_ref.resource_ref
    assert after[3] == UUID(recovered.candidate_ref.revision_ref)
    assert after[3] == interrupted_revision_id
    assert after[4] == UUID(recovered.candidate_ref.revision_ref)
    assert tuple(after[5:]) == (
        1,
        expected_fragments,
        expected_candidates,
        1,
        1,
    )
    assert len(recovered.candidate_refs) == expected_fragments
    assert len({candidate.revision_ref for candidate in recovered.candidate_refs}) == 1
    history = _history(migration_configuration, scenario)
    assert [row[0] for row in history].count("interrupted") == 1
    assert [row[0] for row in history].count("reclaimed") == 1
    assert [row[0] for row in history].count("active") == 1
    assert history[-1][0:4] == (
        "active",
        "active",
        2,
        "completed",
    )
    package = _resolve(
        scenario,
        guarded_runtime_engine,
        cast(Any, query_digest_keyring),
        user_id=user_id,
        query="OLD marker.",
        request_id=f"initial-{config_version}-{boundary.value}-active",
    )
    assert [block["text"] for block in package["blocks"]] == (
        ["OLD marker."]
        if config_version == "markdown-config-v1"
        else ["# Handbook\n\nOLD marker."]
    )
    assert len(package["evidence"]) == 1
    assert package["evidence"][0]["revisionRef"] == (
        recovered.candidate_ref.revision_ref
    )


def _acquire_recovery_direct(
    guarded_worker_engine: Engine,
    scenario: _FileImportScenario,
    *,
    artifact_document: object,
) -> object | None:
    claims = _scenario_claims(scenario)
    document = compile_markdown(
        OLD_MARKDOWN,
        MarkdownCompilerConfig("markdown-config-v2"),
    )
    assert type(document) is ParsedDocument
    compilation_document = canonicalize_parsed_document(document).decode("utf-8")
    with guarded_worker_engine.begin() as connection:
        return connection.execute(
            text(
                """
                SELECT * FROM public.context_worker_acquire_file_publication(
                    :organization_id, :job_id, :service_principal_id,
                    :source_ref, :resource_ref, :revision_id,
                    :canonical_text, :content_hash, :compilation_digest,
                    :compiler_version, :config_version,
                    CAST(:compilation_document AS jsonb),
                    CAST(:artifact_document AS jsonb),
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
                "resource_ref": "resource:malformed-recovery-artifact",
                "revision_id": UUID("00000000-0000-0000-0000-000000000027"),
                "canonical_text": document.canonical_text,
                "content_hash": document.content_hash,
                "compilation_digest": document.compilation_digest,
                "compiler_version": document.provenance.compiler_version,
                "config_version": document.provenance.config_version,
                "compilation_document": compilation_document,
                "artifact_document": json.dumps(artifact_document),
                "lease_generation": claims.lease_generation,
                "signing_key_version": claims.signing_key_version,
                "nonce": claims.nonce,
                "issued_at": claims.issued_at,
                "expires_at": claims.expires_at,
            },
        ).one_or_none()


def test_structural_recovery_rejects_artifact_not_derived_from_compilation(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=OLD_MARKDOWN,
    )
    assert scenario.token is not None
    assert _redeem_direct(guarded_worker_engine, _scenario_claims(scenario)) is not None
    assert (
        _acquire_recovery_direct(
            guarded_worker_engine,
            scenario,
            artifact_document=[
                {
                    "fragmentRef": "fragment:paragraph:1",
                    "contextualText": "attacker supplied body",
                    "searchPhrases": ["attacker supplied phrase"],
                }
            ],
        )
        is None
    )
    snapshot = _job_snapshot(migration_configuration, scenario)
    assert snapshot[0] == "running"
    assert snapshot[2:5] == (None, None, None)
    assert snapshot[5:] == (0, 0, 0, 1, 0)


def test_concurrent_reclaim_and_redemption_have_one_owner_and_one_effect(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=OLD_MARKDOWN,
        lease_ttl_seconds=2,
    )
    assert scenario.token is not None
    with pytest.raises(FileImportInterrupted):
        _run(
            _worker(
                scenario,
                guarded_worker_engine,
                config_version="markdown-config-v2",
                interrupt_after=FilePublicationBoundary.PREPARED,
            ),
            scenario,
            scenario.token,
        )
    _wait_for_expiry(migration_configuration)

    def reclaim() -> object:
        return PostgreSQLWorkerLeaseIssuer(
            guarded_control_engine,
            scenario.codec,
        ).issue_file_import_lease(scenario.prepared)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (executor.submit(reclaim), executor.submit(reclaim))
        results: list[object] = []
        failures: list[type[BaseException]] = []
        for future in futures:
            try:
                results.append(future.result(timeout=5))
            except BaseException as error:
                failures.append(type(error))
    assert len(results) == 1
    assert failures == [WorkerLeaseIssueNotAvailable]
    recovery_token = results[0]

    def resume() -> object:
        return _run(
            _worker(
                scenario,
                guarded_worker_engine,
                config_version="markdown-config-v2",
            ),
            scenario,
            recovery_token,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (executor.submit(resume), executor.submit(resume))
        successes: list[object] = []
        rejections: list[type[BaseException]] = []
        for future in futures:
            try:
                successes.append(future.result(timeout=5))
            except BaseException as error:
                rejections.append(type(error))
    assert len(successes) == 1
    assert rejections == [WorkNotAvailable]
    assert _job_snapshot(migration_configuration, scenario)[5:] == (1, 4, 6, 1, 1)
    assert [row[0] for row in _history(migration_configuration, scenario)].count(
        "active"
    ) == 1


@pytest.mark.security_evidence(id="PG-FILE-RECOVERY-027", layer="postgres")
def test_recovery_tables_deny_direct_access_to_every_nonowner_role(
    guarded_control_engine: Engine,
    guarded_runtime_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    statements = (
        "SELECT count(*) FROM {table}",
        "INSERT INTO {table} DEFAULT VALUES",
        "UPDATE {table} SET organization_id = organization_id WHERE false",
        "DELETE FROM {table} WHERE false",
    )
    for engine in (
        guarded_control_engine,
        guarded_runtime_engine,
        guarded_worker_engine,
    ):
        for table in ("file_publication_recovery", "file_import_job_event"):
            for statement in statements:
                with engine.connect() as connection:
                    transaction = connection.begin()
                    try:
                        with pytest.raises(SQLAlchemyError):
                            connection.execute(text(statement.format(table=table)))
                    finally:
                        transaction.rollback()


def test_other_organization_lease_cannot_recover_interrupted_job(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    protected = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=OLD_MARKDOWN,
        lease_ttl_seconds=2,
    )
    other = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=NEW_MARKDOWN,
    )
    assert protected.token is not None
    assert other.token is not None
    with pytest.raises(FileImportInterrupted):
        _run(
            _worker(
                protected,
                guarded_worker_engine,
                config_version="markdown-config-v2",
                interrupt_after=FilePublicationBoundary.PREPARED,
            ),
            protected,
            protected.token,
        )
    protected_before = _job_snapshot(migration_configuration, protected)

    other_claims = _scenario_claims(other)
    assert _redeem_direct(
        guarded_worker_engine,
        other_claims,
        organization_id=protected.organization_id,
        job_id=protected.prepared.job_id,
        source_ref=str(protected.source_ref.value),
    ) is None
    assert _job_snapshot(migration_configuration, protected) == protected_before


@pytest.mark.parametrize(
    ("old_payload", "new_payload", "config_version"),
    [
        (OLD_V1_MARKDOWN, NEW_V1_MARKDOWN, "markdown-config-v1"),
        (OLD_MARKDOWN, NEW_MARKDOWN, "markdown-config-v2"),
    ],
)
def test_ready_replacement_recovery_keeps_old_http_package_until_activation(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    guarded_runtime_engine: Engine,
    query_digest_keyring: object,
    old_payload: bytes,
    new_payload: bytes,
    config_version: str,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=old_payload,
    )
    assert scenario.token is not None
    first = _run_file_import(
        scenario,
        scenario.prepared,
        scenario.token,
        guarded_worker_engine,
        config_version=config_version,
    )
    (scenario.root / "handbook.md").write_bytes(new_payload)
    prepared, token = _prepare_repeat_file_import(
        scenario,
        guarded_control_engine,
        idempotency_key=f"recover-ready-{config_version}",
        lease_ttl_seconds=2,
    )
    recovery_scenario = _FileImportScenario(
        organization_id=scenario.organization_id,
        membership_id=scenario.membership_id,
        receiver=scenario.receiver,
        source_ref=scenario.source_ref,
        prepared=prepared,
        codec=scenario.codec,
        token=token,
        root_ref=FileRootRef(scenario.root_ref.value),
        root=scenario.root,
    )
    with pytest.raises(FileImportInterrupted):
        _run(
            _worker(
                recovery_scenario,
                guarded_worker_engine,
                config_version=config_version,
                interrupt_after=FilePublicationBoundary.INDEXED,
            ),
            recovery_scenario,
            token,
        )

    user_id = _scenario_user_id(scenario, migration_configuration)
    old_query = "OLD marker."
    old_package = _resolve(
        scenario,
        guarded_runtime_engine,
        cast(Any, query_digest_keyring),
        user_id=user_id,
        query=old_query,
        request_id=f"ready-recovery-old-{config_version}",
    )
    assert old_package["evidence"][0]["revisionRef"] == (
        first.candidate_ref.revision_ref
    )
    assert len(old_package["blocks"]) == 1
    assert len(old_package["evidence"]) == 1
    hidden_new = _resolve(
        scenario,
        guarded_runtime_engine,
        cast(Any, query_digest_keyring),
        user_id=user_id,
        query="NEW marker.",
        request_id=f"ready-recovery-hidden-{config_version}",
    )
    assert hidden_new["blocks"] == []
    assert hidden_new["evidence"] == []

    _wait_for_expiry(migration_configuration)
    recovery_token = PostgreSQLWorkerLeaseIssuer(
        guarded_control_engine,
        scenario.codec,
    ).issue_file_import_lease(prepared)
    recovered = cast(
        Any,
        _run(
            _worker(
                recovery_scenario,
                guarded_worker_engine,
                config_version=config_version,
            ),
            recovery_scenario,
            recovery_token,
        ),
    )
    new_package = _resolve(
        scenario,
        guarded_runtime_engine,
        cast(Any, query_digest_keyring),
        user_id=user_id,
        query="NEW marker.",
        request_id=f"ready-recovery-new-{config_version}",
    )
    assert new_package["evidence"][0]["revisionRef"] == (
        recovered.candidate_ref.revision_ref
    )
    assert len(new_package["blocks"]) == 1
    assert len(new_package["evidence"]) == 1
    assert recovered.candidate_ref.revision_ref != first.candidate_ref.revision_ref
    assert [row[0] for row in _history(migration_configuration, recovery_scenario)][
        -3:
    ] == ["interrupted", "reclaimed", "active"]


def test_ready_initial_recovery_revalidates_current_audience_authority(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=OLD_MARKDOWN,
        lease_ttl_seconds=2,
    )
    assert scenario.token is not None
    with pytest.raises(FileImportInterrupted):
        _run(
            _worker(
                scenario,
                guarded_worker_engine,
                config_version="markdown-config-v2",
                interrupt_after=FilePublicationBoundary.INDEXED,
            ),
            scenario,
            scenario.token,
        )

    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE membership SET status = 'revoked'
                    WHERE organization_id = :organization_id
                      AND membership_id = :membership_id
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "membership_id": scenario.membership_id,
                },
            )
    finally:
        engine.dispose()

    _wait_for_expiry(migration_configuration)
    recovery_token = PostgreSQLWorkerLeaseIssuer(
        guarded_control_engine,
        scenario.codec,
    ).issue_file_import_lease(scenario.prepared)
    with pytest.raises(WorkNotAvailable):
        _run(
            _worker(
                scenario,
                guarded_worker_engine,
                config_version="markdown-config-v2",
            ),
            scenario,
            recovery_token,
        )
    after = _job_snapshot(migration_configuration, scenario)
    assert after[0] == "ready"
    assert after[4] is None
