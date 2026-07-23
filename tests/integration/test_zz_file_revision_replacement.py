from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Lock
from time import monotonic
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from adapters.exact_phrase import PostgreSQLExactPhraseCandidateIndex
from adapters.http.app import create_app
from adapters.parsers.markdown import compile_markdown
from engine.control import FileImportPath
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLMembershipAuthority,
    create_database_engine,
)
from engine.persistence.file_imports import PostgreSQLFileImportWorker, _resource_ref
from engine.runtime.construction import Runtime, required_kernel_dependencies
from engine.runtime.content_io import CandidateIndex, exact_phrase_digest
from engine.runtime.contracts import Acquire
from engine.runtime.evidence import CandidateRef
from engine.runtime.materialized import MaterializedProjectionSession
from engine.runtime.package_digest import QueryDigestKeyring
from engine.supply import (
    MarkdownCompilerConfig,
    ParsedDocument,
    WorkerLeaseClaims,
    canonicalize_parsed_document,
)
from tests.integration.test_file_import_tracer import (
    NOW,
    _ExactScopeAuthority,
    _FileImportScenario,
    _OrganizationAuthority,
    _prepare_file_import_scenario,
    _prepare_repeat_file_import,
    _redeem_direct,
    _run_file_import,
    _RuntimeAuthenticator,
)

pytestmark = pytest.mark.integration

OLD_MARKDOWN = b"# Handbook\n\nOLD marker.\n\n## Shared\n\nShared query.\n"
NEW_MARKDOWN = b"# Handbook\n\nNEW marker.\n\n## Shared\n\nShared query.\n"
OLD_V1_MARKDOWN = b"# Handbook\n\nOLD marker.\n"
NEW_V1_MARKDOWN = b"# Handbook\n\nNEW marker.\n"
OLD_CONCURRENT_MARKDOWN = (
    b"# Alpha\n\nOLD alpha.\n\nShared query.\n\n"
    b"## Beta\n\nOLD beta.\n\nShared query.\n"
)
NEW_CONCURRENT_MARKDOWN = (
    b"# Alpha\n\nNEW alpha.\n\nShared query.\n\n"
    b"## Beta\n\nNEW beta.\n\nShared query.\n"
)
UNAFFECTED_MARKDOWN = b"# Reference\n\nUNAFFECTED resource marker.\n"


def _resolve(
    scenario: _FileImportScenario,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
    *,
    user_id: UUID,
    query: str,
    request_id: str,
    candidate_index: CandidateIndex | None = None,
    resource_ref: str | None = None,
) -> dict[str, Any]:
    client = TestClient(
        create_app(
            authenticator=_RuntimeAuthenticator(
                scenario.organization_id,
                user_id,
                scenario.membership_id,
            ),
            organization_authority=_OrganizationAuthority(),
            membership_authority=PostgreSQLMembershipAuthority(
                guarded_runtime_engine
            ),
            scope_authority=_ExactScopeAuthority(
                str(scenario.source_ref.value),
                resource_ref
                or _resource_ref(scenario.source_ref, FileImportPath("handbook.md")),
            ),
            runtime=Runtime(
                required_kernel_dependencies(),
                candidate_index=(
                    candidate_index or PostgreSQLExactPhraseCandidateIndex()
                ),
                clock=lambda: NOW,
                query_digest_keyring=query_digest_keyring,
            ),
            clock=lambda: NOW,
            request_id_factory=lambda: request_id,
        )
    )
    response = client.post(
        "/v1/context:resolve",
        headers={"Authorization": "Bearer runtime-secret"},
        json={"kind": "acquire", "need": {"query": query}},
    )
    assert response.status_code == 200
    package = response.json()["package"]
    assert isinstance(package, dict)
    return cast(dict[str, Any], package)


def _compile_replacement(payload: bytes, *, structural: bool) -> ParsedDocument:
    result = compile_markdown(
        payload,
        MarkdownCompilerConfig(
            "markdown-config-v2" if structural else "markdown-config-v1"
        ),
    )
    assert type(result) is ParsedDocument
    return result


def _stage_replacement_direct(
    guarded_worker_engine: Engine,
    claims: WorkerLeaseClaims,
    document: ParsedDocument,
    *,
    resource_ref: str,
    revision_id: UUID,
    overrides: dict[str, object] | None = None,
) -> object | None:
    parameters: dict[str, object] = {
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
        "signing_key_version": claims.signing_key_version,
        "nonce": claims.nonce,
        "issued_at": claims.issued_at,
        "expires_at": claims.expires_at,
    }
    parameters.update(overrides or {})
    if document.provenance.is_structural_v2:
        statement = """
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
        parameters["compilation_document"] = json.dumps(
            json.loads(canonicalize_parsed_document(document)),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    else:
        fragment = document.fragments[0]
        statement = """
            SELECT *
            FROM public.context_worker_stage_file_replacement(
                :organization_id, :job_id, :service_principal_id,
                :source_ref, :resource_ref, :revision_id,
                :fragment_ref, :canonical_text, :paragraph,
                :content_hash, :compilation_digest,
                :compiler_version, :config_version, :phrase_digest,
                :signing_key_version, :nonce, :issued_at, :expires_at
            )
        """
        parameters.update(
            {
                "fragment_ref": fragment.fragment_ref,
                "paragraph": fragment.contextual_text,
                "phrase_digest": exact_phrase_digest(fragment.search_phrases[0]),
            }
        )
    with guarded_worker_engine.begin() as connection:
        return connection.execute(text(statement), parameters).one_or_none()


def _activate_replacement_direct(
    guarded_worker_engine: Engine,
    claims: WorkerLeaseClaims,
    *,
    resource_ref: str,
    previous_revision_id: UUID,
    replacement_revision_id: UUID,
    overrides: dict[str, object] | None = None,
) -> object | None:
    parameters: dict[str, object] = {
        "organization_id": claims.organization_id,
        "job_id": claims.job_id,
        "service_principal_id": claims.service_principal_id,
        "source_ref": claims.source_ref,
        "resource_ref": resource_ref,
        "previous_revision_id": previous_revision_id,
        "replacement_revision_id": replacement_revision_id,
        "signing_key_version": claims.signing_key_version,
        "nonce": claims.nonce,
        "issued_at": claims.issued_at,
        "expires_at": claims.expires_at,
    }
    parameters.update(overrides or {})
    with guarded_worker_engine.begin() as connection:
        return connection.execute(
            text(
                """
                SELECT * FROM public.context_worker_activate_file_replacement(
                    :organization_id, :job_id, :service_principal_id,
                    :source_ref, :resource_ref, :previous_revision_id,
                    :replacement_revision_id, :signing_key_version,
                    :nonce, :issued_at, :expires_at
                )
                """
            ),
            parameters,
        ).one_or_none()


def _replacement_state(
    migration_engine: Engine,
    scenario: _FileImportScenario,
    *,
    job_id: UUID,
    resource_ref: str,
) -> tuple[object, ...]:
    with migration_engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT job.state, resource.active_revision_id,
                       (SELECT count(*) FROM context_revision AS revision
                        WHERE revision.organization_id = job.organization_id
                          AND revision.resource_ref = :resource_ref),
                       (SELECT count(*)
                        FROM file_revision_replacement_plan AS plan
                        WHERE plan.organization_id = job.organization_id
                          AND plan.resource_ref = :resource_ref),
                       (SELECT count(*)
                        FROM file_revision_supersession AS supersession
                        WHERE supersession.organization_id = job.organization_id
                          AND supersession.resource_ref = :resource_ref)
                FROM file_import_job AS job
                JOIN context_resource AS resource
                  ON resource.organization_id = job.organization_id
                 AND resource.resource_ref = :resource_ref
                WHERE job.organization_id = :organization_id
                  AND job.job_id = :job_id
                """
            ),
            {
                "organization_id": scenario.organization_id,
                "job_id": job_id,
                "resource_ref": resource_ref,
            },
        ).one()
    return tuple(row)


class _BlockingCandidateIndex:
    def __init__(self) -> None:
        self.discovered = Event()
        self.release = Event()
        self._inner = PostgreSQLExactPhraseCandidateIndex()

    def discover(
        self,
        request: Acquire,
        projection_session: MaterializedProjectionSession,
    ) -> tuple[CandidateRef, ...]:
        candidates = self._inner.discover(request, projection_session)
        self.discovered.set()
        if not self.release.wait(timeout=5):
            raise AssertionError("candidate discovery barrier timed out")
        return candidates


def _scenario_user_id(
    scenario: _FileImportScenario,
    migration_configuration: DatabaseConfiguration,
) -> UUID:
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            return cast(
                UUID,
                connection.execute(
                    text(
                        """
                        SELECT user_id FROM membership
                        WHERE organization_id = :organization_id
                          AND membership_id = :membership_id
                        """
                    ),
                    {
                        "organization_id": scenario.organization_id,
                        "membership_id": scenario.membership_id,
                    },
                ).scalar_one(),
            )
    finally:
        engine.dispose()


def _wait_for_ready_replacement(
    migration_engine: Engine,
    scenario: _FileImportScenario,
    job_id: UUID,
) -> UUID:
    deadline = monotonic() + 5
    while monotonic() < deadline:
        with migration_engine.connect() as connection:
            revision_id = connection.execute(
                text(
                    """
                    SELECT replacement_revision_id
                    FROM file_revision_replacement_plan
                    WHERE organization_id = :organization_id
                      AND job_id = :job_id
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "job_id": job_id,
                },
            ).scalar_one_or_none()
        if revision_id is not None:
            return cast(UUID, revision_id)
    raise AssertionError("replacement did not reach the durable ready boundary")


def test_changed_file_publishes_a_new_immutable_active_revision(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=OLD_MARKDOWN,
    )
    assert scenario.token is not None
    first = _run_file_import(
        scenario,
        scenario.prepared,
        scenario.token,
        guarded_worker_engine,
        config_version="markdown-config-v2",
    )

    (scenario.root / "handbook.md").write_bytes(NEW_MARKDOWN)
    changed_prepared, changed_token = _prepare_repeat_file_import(
        scenario,
        guarded_control_engine,
        idempotency_key="replace-old-with-new",
    )
    second = _run_file_import(
        scenario,
        changed_prepared,
        changed_token,
        guarded_worker_engine,
        config_version="markdown-config-v2",
    )

    assert second.outcome == "replaced"
    assert second.effect_count == 1
    assert second.candidate_ref.resource_ref == first.candidate_ref.resource_ref
    assert second.candidate_ref.revision_ref != first.candidate_ref.revision_ref


def test_changed_v1_file_uses_the_same_atomic_replacement_path(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=OLD_V1_MARKDOWN,
    )
    assert scenario.token is not None
    first = _run_file_import(
        scenario,
        scenario.prepared,
        scenario.token,
        guarded_worker_engine,
    )
    (scenario.root / "handbook.md").write_bytes(NEW_V1_MARKDOWN)
    changed_prepared, changed_token = _prepare_repeat_file_import(
        scenario,
        guarded_control_engine,
        idempotency_key="replace-v1-old-with-new",
    )

    second = _run_file_import(
        scenario,
        changed_prepared,
        changed_token,
        guarded_worker_engine,
    )

    assert second.outcome == "replaced"
    assert second.effect_count == 1
    assert second.candidate_ref.resource_ref == first.candidate_ref.resource_ref
    assert second.candidate_ref.revision_ref != first.candidate_ref.revision_ref


@pytest.mark.parametrize(
    ("old_payload", "new_payload", "config_version"),
    [
        (OLD_V1_MARKDOWN, NEW_V1_MARKDOWN, "markdown-config-v1"),
        (OLD_MARKDOWN, NEW_MARKDOWN, "markdown-config-v2"),
    ],
)
def test_changed_import_race_returns_the_late_job_as_a_successful_noop(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
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
    winner_prepared, winner_token = _prepare_repeat_file_import(
        scenario,
        guarded_control_engine,
        idempotency_key=f"replacement-race-winner-{config_version}",
    )
    late_prepared, late_token = _prepare_repeat_file_import(
        scenario,
        guarded_control_engine,
        idempotency_key=f"replacement-race-late-{config_version}",
    )
    late_reached_replace = Event()
    winner_completed = Event()
    original_replace = PostgreSQLFileImportWorker._replace

    def pause_late_replace(
        worker: PostgreSQLFileImportWorker,
        claims: WorkerLeaseClaims,
        resource_ref: str,
        revision_id: UUID,
        document: ParsedDocument,
        payload: dict[str, object],
        *,
        structural: bool,
    ) -> object | None:
        if claims.job_id == late_prepared.job_id:
            late_reached_replace.set()
            if not winner_completed.wait(timeout=5):
                raise AssertionError("replacement race winner did not complete")
        return original_replace(
            worker,
            claims,
            resource_ref,
            revision_id,
            document,
            payload,
            structural=structural,
        )

    monkeypatch.setattr(PostgreSQLFileImportWorker, "_replace", pause_late_replace)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            late_future = executor.submit(
                _run_file_import,
                scenario,
                late_prepared,
                late_token,
                guarded_worker_engine,
                config_version=config_version,
            )
            assert late_reached_replace.wait(timeout=5)
            winner = _run_file_import(
                scenario,
                winner_prepared,
                winner_token,
                guarded_worker_engine,
                config_version=config_version,
            )
            winner_completed.set()
            late = late_future.result(timeout=5)
    finally:
        winner_completed.set()

    assert winner.outcome == "replaced"
    assert winner.effect_count == 1
    assert late.outcome == "unchanged"
    assert late.effect_count == 0
    assert late.candidate_refs == winner.candidate_refs
    assert winner.candidate_ref.revision_ref != first.candidate_ref.revision_ref

    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            durable = connection.execute(
                text(
                    """
                    SELECT job.state, job.effect_count, job.revision_id,
                           job.completed_at IS NOT NULL,
                           result.outcome, result.active_revision_id,
                           result.content_identity_digest, result.reason_code,
                           result.reason_digest,
                           resource.active_revision_id,
                        (SELECT count(*) FROM context_revision
                         WHERE organization_id = :organization_id
                           AND resource_ref = :resource_ref),
                        (SELECT count(*) FROM file_revision_replacement_plan
                         WHERE organization_id = :organization_id
                           AND resource_ref = :resource_ref),
                        (SELECT count(*) FROM file_revision_supersession
                         WHERE organization_id = :organization_id
                           AND resource_ref = :resource_ref)
                    FROM file_import_job AS job
                    JOIN file_acquisition_result AS result
                      ON result.organization_id = job.organization_id
                     AND result.acquisition_id = job.acquisition_id
                     AND result.source_id = job.source_id
                    JOIN context_resource AS resource
                      ON resource.organization_id = job.organization_id
                     AND resource.resource_ref = result.resource_ref
                    WHERE job.organization_id = :organization_id
                      AND job.job_id = :job_id
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "resource_ref": winner.candidate_ref.resource_ref,
                    "job_id": late_prepared.job_id,
                },
            ).one()
        assert tuple(durable[:4]) == (
            "completed",
            0,
            UUID(winner.candidate_ref.revision_ref),
            True,
        )
        assert tuple(durable[4:10]) == (
            "unchanged",
            UUID(winner.candidate_ref.revision_ref),
            late.content_identity_digest,
            "active-content-identity-match",
            late.reason_digest,
            UUID(winner.candidate_ref.revision_ref),
        )
        assert late.reason_digest is not None
        assert len(late.reason_digest) == 64
        assert tuple(durable[10:]) == (2, 1, 1)
    finally:
        engine.dispose()


def test_ready_replacement_keeps_old_http_package_until_atomic_activation(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=OLD_MARKDOWN,
    )
    assert scenario.token is not None
    first = _run_file_import(
        scenario,
        scenario.prepared,
        scenario.token,
        guarded_worker_engine,
        config_version="markdown-config-v2",
    )
    user_id = _scenario_user_id(scenario, migration_configuration)
    resource_ref = first.candidate_ref.resource_ref

    (scenario.root / "handbook.md").write_bytes(NEW_MARKDOWN)
    changed_prepared, changed_token = _prepare_repeat_file_import(
        scenario,
        guarded_control_engine,
        idempotency_key="ready-old-then-atomic-new",
    )
    migration_engine = create_database_engine(migration_configuration)
    activation_started = Event()
    try:
        with migration_engine.connect() as barrier_connection:
            barrier_transaction = barrier_connection.begin()
            barrier_connection.execute(
                text(
                    """
                    SELECT pg_catalog.pg_advisory_xact_lock_shared(
                        pg_catalog.hashtextextended(
                            'context-engine.file-replacement:'
                            || CAST(:organization_id AS text) || ':'
                            || :resource_ref,
                            0
                        )
                    )
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "resource_ref": resource_ref,
                },
            )
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    _run_file_import,
                    scenario,
                    changed_prepared,
                    changed_token,
                    guarded_worker_engine,
                    config_version="markdown-config-v2",
                )
                ready_revision_id = _wait_for_ready_replacement(
                    migration_engine,
                    scenario,
                    changed_prepared.job_id,
                )
                activation_started.set()

                with migration_engine.connect() as readiness_connection:
                    ready = readiness_connection.execute(
                        text(
                            """
                            SELECT job.state, resource.active_revision_id,
                                   array_agg(event.state ORDER BY event.ordinal)
                                       AS states,
                                   (SELECT count(*) FROM context_fragment AS fragment
                                    WHERE fragment.organization_id = job.organization_id
                                      AND fragment.resource_ref = job.resource_ref
                                      AND fragment.revision_id = job.revision_id)
                                       AS fragment_count,
                                   (SELECT count(*)
                                    FROM exact_phrase_candidate AS candidate
                                    WHERE candidate.organization_id =
                                          job.organization_id
                                      AND candidate.resource_ref = job.resource_ref
                                      AND candidate.revision_id = job.revision_id)
                                       AS candidate_count
                            FROM file_import_job AS job
                            JOIN context_resource AS resource
                              ON resource.organization_id = job.organization_id
                             AND resource.resource_ref = job.resource_ref
                            JOIN revision_publication_event AS event
                              ON event.organization_id = job.organization_id
                             AND event.resource_ref = job.resource_ref
                             AND event.revision_id = job.revision_id
                            WHERE job.organization_id = :organization_id
                              AND job.job_id = :job_id
                            GROUP BY job.state, job.organization_id,
                                     job.resource_ref, job.revision_id,
                                     resource.active_revision_id
                            """
                        ),
                        {
                            "organization_id": scenario.organization_id,
                            "job_id": changed_prepared.job_id,
                        },
                    ).one()
                assert ready.state == "ready"
                assert ready.active_revision_id == UUID(
                    first.candidate_ref.revision_ref
                )
                assert ready.states == ["prepared", "indexed"]
                assert ready.fragment_count == 4
                assert ready.candidate_count == 6

                old_package = _resolve(
                    scenario,
                    guarded_runtime_engine,
                    query_digest_keyring,
                    user_id=user_id,
                    query="OLD marker.",
                    request_id="replacement-ready-old",
                )
                not_yet_new = _resolve(
                    scenario,
                    guarded_runtime_engine,
                    query_digest_keyring,
                    user_id=user_id,
                    query="NEW marker.",
                    request_id="replacement-ready-new-hidden",
                )
                assert [block["text"] for block in old_package["blocks"]] == [
                    "# Handbook\n\nOLD marker."
                ]
                assert old_package["evidence"][0]["revisionRef"] == (
                    first.candidate_ref.revision_ref
                )
                assert not_yet_new["blocks"] == []
                assert future.done() is False

                barrier_transaction.commit()
                second = future.result(timeout=5)

        assert activation_started.is_set()
        assert UUID(second.candidate_ref.revision_ref) == ready_revision_id
        new_package = _resolve(
            scenario,
            guarded_runtime_engine,
            query_digest_keyring,
            user_id=user_id,
            query="NEW marker.",
            request_id="replacement-active-new",
        )
        no_longer_old = _resolve(
            scenario,
            guarded_runtime_engine,
            query_digest_keyring,
            user_id=user_id,
            query="OLD marker.",
            request_id="replacement-active-old-hidden",
        )
        assert [block["text"] for block in new_package["blocks"]] == [
            "# Handbook\n\nNEW marker."
        ]
        assert new_package["evidence"][0]["revisionRef"] == (
            second.candidate_ref.revision_ref
        )
        assert no_longer_old["blocks"] == []

        with migration_engine.connect() as connection:
            supersession = connection.execute(
                text(
                    """
                    SELECT superseded_revision_id, replacement_revision_id,
                           retention_state
                    FROM file_revision_supersession
                    WHERE organization_id = :organization_id
                      AND resource_ref = :resource_ref
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "resource_ref": resource_ref,
                },
            ).one()
            old_text = connection.execute(
                text(
                    """
                    SELECT canonical_text FROM file_revision_snapshot
                    WHERE organization_id = :organization_id
                      AND resource_ref = :resource_ref
                      AND revision_id = :revision_id
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "resource_ref": resource_ref,
                    "revision_id": UUID(first.candidate_ref.revision_ref),
                },
            ).scalar_one()
            activated = connection.execute(
                text(
                    """
                    SELECT resource.active_revision_id, job.state,
                           job.effect_count,
                           array_agg(event.state ORDER BY event.ordinal) AS states
                    FROM context_resource AS resource
                    JOIN file_import_job AS job
                      ON job.organization_id = resource.organization_id
                     AND job.resource_ref = resource.resource_ref
                     AND job.revision_id = resource.active_revision_id
                    JOIN revision_publication_event AS event
                      ON event.organization_id = resource.organization_id
                     AND event.resource_ref = resource.resource_ref
                     AND event.revision_id = resource.active_revision_id
                    WHERE resource.organization_id = :organization_id
                      AND resource.resource_ref = :resource_ref
                      AND job.job_id = :job_id
                    GROUP BY resource.active_revision_id,
                             job.state, job.effect_count
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "resource_ref": resource_ref,
                    "job_id": changed_prepared.job_id,
                },
            ).one()
        assert supersession.superseded_revision_id == UUID(
            first.candidate_ref.revision_ref
        )
        assert supersession.replacement_revision_id == UUID(
            second.candidate_ref.revision_ref
        )
        assert supersession.retention_state == "retained_until_explicit_cleanup"
        assert old_text == OLD_MARKDOWN.decode()
        assert activated.active_revision_id == UUID(
            second.candidate_ref.revision_ref
        )
        assert (activated.state, activated.effect_count) == ("completed", 1)
        assert activated.states == ["prepared", "indexed", "active"]
    finally:
        migration_engine.dispose()


def test_repeated_http_readers_observe_only_complete_old_or_new_packages(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=OLD_CONCURRENT_MARKDOWN,
    )
    assert scenario.token is not None
    first = _run_file_import(
        scenario,
        scenario.prepared,
        scenario.token,
        guarded_worker_engine,
        config_version="markdown-config-v2",
    )
    user_id = _scenario_user_id(scenario, migration_configuration)
    resource_ref = first.candidate_ref.resource_ref
    old_revision_ref = first.candidate_ref.revision_ref

    (scenario.root / "handbook.md").write_bytes(NEW_CONCURRENT_MARKDOWN)
    changed_prepared, changed_token = _prepare_repeat_file_import(
        scenario,
        guarded_control_engine,
        idempotency_key="concurrent-readers-old-to-new",
    )
    migration_engine = create_database_engine(migration_configuration)
    observations: list[str] = []
    observation_lock = Lock()
    old_observed = Event()
    new_observed = Event()
    stop_readers = Event()
    new_revision_ref: list[str] = []

    def read_packages(reader: int) -> None:
        for sequence in range(128):
            if stop_readers.is_set():
                return
            package = _resolve(
                scenario,
                guarded_runtime_engine,
                query_digest_keyring,
                user_id=user_id,
                query="Shared query.",
                request_id=f"replacement-reader-{reader}-{sequence}",
            )
            evidence = package["evidence"]
            revision_refs = {
                item["revisionRef"] for item in evidence if isinstance(item, dict)
            }
            if len(evidence) != 2 or len(revision_refs) != 1:
                classification = "empty_or_incomplete" if not evidence else "mixed"
            else:
                observed_revision = revision_refs.pop()
                if observed_revision == old_revision_ref:
                    classification = "old"
                elif new_revision_ref and observed_revision == new_revision_ref[0]:
                    classification = "new"
                else:
                    classification = "mixed"
            with observation_lock:
                observations.append(classification)
                if observations.count("old") >= 4:
                    old_observed.set()
                if observations.count("new") >= 4:
                    new_observed.set()

    try:
        with migration_engine.connect() as barrier_connection:
            barrier_transaction = barrier_connection.begin()
            barrier_connection.execute(
                text(
                    """
                    SELECT pg_catalog.pg_advisory_xact_lock_shared(
                        pg_catalog.hashtextextended(
                            'context-engine.file-replacement:'
                            || CAST(:organization_id AS text) || ':'
                            || :resource_ref,
                            0
                        )
                    )
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "resource_ref": resource_ref,
                },
            )
            with ThreadPoolExecutor(max_workers=5) as executor:
                activation = executor.submit(
                    _run_file_import,
                    scenario,
                    changed_prepared,
                    changed_token,
                    guarded_worker_engine,
                    config_version="markdown-config-v2",
                )
                ready_revision = _wait_for_ready_replacement(
                    migration_engine,
                    scenario,
                    changed_prepared.job_id,
                )
                new_revision_ref.append(str(ready_revision))
                readers = tuple(
                    executor.submit(read_packages, reader) for reader in range(4)
                )
                assert old_observed.wait(timeout=5)
                barrier_transaction.commit()
                second = activation.result(timeout=5)
                assert second.candidate_ref.revision_ref == new_revision_ref[0]
                assert new_observed.wait(timeout=5)
                stop_readers.set()
                for reader in readers:
                    reader.result(timeout=5)

        assert observations.count("old") >= 4
        assert observations.count("new") >= 4
        assert observations.count("mixed") == 0
        assert observations.count("empty_or_incomplete") == 0
    finally:
        stop_readers.set()
        migration_engine.dispose()


def test_activation_waits_for_an_inflight_http_resolution_transaction(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=OLD_MARKDOWN,
    )
    assert scenario.token is not None
    first = _run_file_import(
        scenario,
        scenario.prepared,
        scenario.token,
        guarded_worker_engine,
        config_version="markdown-config-v2",
    )
    user_id = _scenario_user_id(scenario, migration_configuration)
    (scenario.root / "handbook.md").write_bytes(NEW_MARKDOWN)
    changed_prepared, changed_token = _prepare_repeat_file_import(
        scenario,
        guarded_control_engine,
        idempotency_key="activation-waits-for-inflight-resolve",
    )
    blocking_index = _BlockingCandidateIndex()
    migration_engine = create_database_engine(migration_configuration)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            reader = executor.submit(
                _resolve,
                scenario,
                guarded_runtime_engine,
                query_digest_keyring,
                user_id=user_id,
                query="OLD marker.",
                request_id="replacement-inflight-old",
                candidate_index=blocking_index,
            )
            assert blocking_index.discovered.wait(timeout=5)
            activation = executor.submit(
                _run_file_import,
                scenario,
                changed_prepared,
                changed_token,
                guarded_worker_engine,
                config_version="markdown-config-v2",
            )
            _wait_for_ready_replacement(
                migration_engine,
                scenario,
                changed_prepared.job_id,
            )
            try:
                assert activation.done() is False
            finally:
                blocking_index.release.set()
            old_package = reader.result(timeout=5)
            second = activation.result(timeout=5)

        assert [block["text"] for block in old_package["blocks"]] == [
            "# Handbook\n\nOLD marker."
        ]
        assert old_package["evidence"][0]["revisionRef"] == (
            first.candidate_ref.revision_ref
        )
        new_package = _resolve(
            scenario,
            guarded_runtime_engine,
            query_digest_keyring,
            user_id=user_id,
            query="NEW marker.",
            request_id="replacement-after-inflight-new",
        )
        assert new_package["evidence"][0]["revisionRef"] == (
            second.candidate_ref.revision_ref
        )
    finally:
        blocking_index.release.set()
        migration_engine.dispose()


@pytest.mark.security_evidence(id="PG-FILE-REPLACEMENT-026", layer="postgres")
def test_replacement_does_not_change_another_organization_resource(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
) -> None:
    replacing = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=OLD_MARKDOWN,
    )
    unaffected = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=OLD_MARKDOWN,
    )
    assert replacing.token is not None
    assert unaffected.token is not None
    replacing_first = _run_file_import(
        replacing,
        replacing.prepared,
        replacing.token,
        guarded_worker_engine,
        config_version="markdown-config-v2",
    )
    unaffected_first = _run_file_import(
        unaffected,
        unaffected.prepared,
        unaffected.token,
        guarded_worker_engine,
        config_version="markdown-config-v2",
    )
    unaffected_user_id = _scenario_user_id(unaffected, migration_configuration)

    (replacing.root / "handbook.md").write_bytes(NEW_MARKDOWN)
    changed_prepared, changed_token = _prepare_repeat_file_import(
        replacing,
        guarded_control_engine,
        idempotency_key="replace-one-organization-only",
    )
    replacing_second = _run_file_import(
        replacing,
        changed_prepared,
        changed_token,
        guarded_worker_engine,
        config_version="markdown-config-v2",
    )

    unaffected_package = _resolve(
        unaffected,
        guarded_runtime_engine,
        query_digest_keyring,
        user_id=unaffected_user_id,
        query="OLD marker.",
        request_id="replacement-unaffected-organization",
    )
    assert replacing_second.candidate_ref.revision_ref != (
        replacing_first.candidate_ref.revision_ref
    )
    assert unaffected_package["evidence"][0]["revisionRef"] == (
        unaffected_first.candidate_ref.revision_ref
    )
    assert [block["text"] for block in unaffected_package["blocks"]] == [
        "# Handbook\n\nOLD marker."
    ]

    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT count(*) FROM file_revision_supersession
                    WHERE organization_id = :organization_id
                    """
                ),
                {"organization_id": unaffected.organization_id},
            ).scalar_one() == 0
    finally:
        engine.dispose()

    for table_name in (
        "file_revision_replacement_plan",
        "file_revision_supersession",
    ):
        with pytest.raises(DBAPIError), guarded_runtime_engine.connect() as connection:
            connection.execute(
                text(
                    f"SELECT count(*) FROM {table_name}"  # noqa: S608 - fixed list
                )
            ).scalar_one()
        with pytest.raises(DBAPIError), guarded_worker_engine.begin() as connection:
            connection.execute(
                text(f"DELETE FROM {table_name}")  # noqa: S608 - fixed list
            )


def test_replacement_does_not_change_another_resource_in_the_same_organization(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=OLD_MARKDOWN,
    )
    assert scenario.token is not None
    replacing_first = _run_file_import(
        scenario,
        scenario.prepared,
        scenario.token,
        guarded_worker_engine,
        config_version="markdown-config-v2",
    )
    (scenario.root / "reference.md").write_bytes(UNAFFECTED_MARKDOWN)
    unaffected_prepared, unaffected_token = _prepare_repeat_file_import(
        scenario,
        guarded_control_engine,
        idempotency_key="same-organization-unaffected-resource",
        path=FileImportPath("reference.md"),
    )
    unaffected_first = _run_file_import(
        scenario,
        unaffected_prepared,
        unaffected_token,
        guarded_worker_engine,
        config_version="markdown-config-v2",
    )
    unaffected_resource_ref = unaffected_first.candidate_ref.resource_ref

    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            unaffected_before = connection.execute(
                text(
                    """
                    SELECT resource.active_revision_id,
                           (SELECT count(*) FROM context_revision AS revision
                            WHERE revision.organization_id = resource.organization_id
                              AND revision.resource_ref = resource.resource_ref),
                           (SELECT count(*) FROM context_fragment AS fragment
                            WHERE fragment.organization_id = resource.organization_id
                              AND fragment.resource_ref = resource.resource_ref),
                           (SELECT count(*)
                            FROM exact_phrase_candidate AS candidate
                            WHERE candidate.organization_id =
                                  resource.organization_id
                              AND candidate.resource_ref = resource.resource_ref)
                    FROM context_resource AS resource
                    WHERE resource.organization_id = :organization_id
                      AND resource.resource_ref = :resource_ref
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "resource_ref": unaffected_resource_ref,
                },
            ).one()

        (scenario.root / "handbook.md").write_bytes(NEW_MARKDOWN)
        changed_prepared, changed_token = _prepare_repeat_file_import(
            scenario,
            guarded_control_engine,
            idempotency_key="replace-only-one-same-organization-resource",
        )
        replacing_second = _run_file_import(
            scenario,
            changed_prepared,
            changed_token,
            guarded_worker_engine,
            config_version="markdown-config-v2",
        )

        with engine.connect() as connection:
            unaffected_after = connection.execute(
                text(
                    """
                    SELECT resource.active_revision_id,
                           (SELECT count(*) FROM context_revision AS revision
                            WHERE revision.organization_id = resource.organization_id
                              AND revision.resource_ref = resource.resource_ref),
                           (SELECT count(*) FROM context_fragment AS fragment
                            WHERE fragment.organization_id = resource.organization_id
                              AND fragment.resource_ref = resource.resource_ref),
                           (SELECT count(*)
                            FROM exact_phrase_candidate AS candidate
                            WHERE candidate.organization_id =
                                  resource.organization_id
                              AND candidate.resource_ref = resource.resource_ref)
                    FROM context_resource AS resource
                    WHERE resource.organization_id = :organization_id
                      AND resource.resource_ref = :resource_ref
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "resource_ref": unaffected_resource_ref,
                },
            ).one()

        user_id = _scenario_user_id(scenario, migration_configuration)
        package = _resolve(
            scenario,
            guarded_runtime_engine,
            query_digest_keyring,
            user_id=user_id,
            query="UNAFFECTED resource marker.",
            request_id="replacement-unaffected-same-organization-resource",
            resource_ref=unaffected_resource_ref,
        )
        assert replacing_second.candidate_ref.revision_ref != (
            replacing_first.candidate_ref.revision_ref
        )
        assert tuple(unaffected_after) == tuple(unaffected_before)
        assert unaffected_after.active_revision_id == UUID(
            unaffected_first.candidate_ref.revision_ref
        )
        assert package["evidence"][0]["resourceRef"] == unaffected_resource_ref
        assert package["evidence"][0]["revisionRef"] == (
            unaffected_first.candidate_ref.revision_ref
        )
        assert [block["text"] for block in package["blocks"]] == [
            "# Reference\n\nUNAFFECTED resource marker."
        ]
    finally:
        engine.dispose()


@pytest.mark.parametrize("structural", (False, True), ids=("v1", "v2"))
@pytest.mark.parametrize(
    "wrong_binding",
    (
        "organization",
        "job",
        "receiver",
        "source",
        "resource",
        "revision",
        "signing_key_version",
        "nonce",
        "issued_at",
        "expires_binding",
    ),
)
def test_replacement_stage_rejects_wrong_exact_bindings_with_zero_effect(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    structural: bool,
    wrong_binding: str,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=OLD_MARKDOWN if structural else OLD_V1_MARKDOWN,
    )
    assert scenario.token is not None
    first = _run_file_import(
        scenario,
        scenario.prepared,
        scenario.token,
        guarded_worker_engine,
        config_version=("markdown-config-v2" if structural else "markdown-config-v1"),
    )
    replacement_payload = NEW_MARKDOWN if structural else NEW_V1_MARKDOWN
    (scenario.root / "handbook.md").write_bytes(replacement_payload)
    prepared, token = _prepare_repeat_file_import(
        scenario,
        guarded_control_engine,
        idempotency_key=f"stage-wrong-{wrong_binding}-{structural}",
    )
    claims = scenario.codec.verify(
        token,
        expected_organization_id=scenario.organization_id,
        expected_job_id=prepared.job_id,
        expected_service_principal_id=scenario.receiver.service_principal_id,
        expected_workload=scenario.receiver.workload,
        expected_operation=scenario.receiver.operation,
        expected_worker_audience=scenario.receiver.worker_audience,
        expected_source_ref=str(scenario.source_ref.value),
        now=datetime.now(UTC).replace(microsecond=0),
    )
    assert _redeem_direct(guarded_worker_engine, claims) is not None
    resource_ref = first.candidate_ref.resource_ref
    revision_id = UUID(int=prepared.job_id.int ^ 1)
    overrides: dict[str, object] = {}
    requested_resource_ref = resource_ref
    requested_revision_id = revision_id
    if wrong_binding == "organization":
        overrides["organization_id"] = UUID(int=scenario.organization_id.int ^ 1)
    elif wrong_binding == "job":
        overrides["job_id"] = UUID(int=prepared.job_id.int ^ 1)
    elif wrong_binding == "receiver":
        overrides["service_principal_id"] = UUID(
            int=claims.service_principal_id.int ^ 1
        )
    elif wrong_binding == "source":
        overrides["source_ref"] = str(UUID(int=scenario.source_ref.value.int ^ 1))
    elif wrong_binding == "resource":
        requested_resource_ref = f"resource:wrong:{prepared.job_id}"
    elif wrong_binding == "revision":
        requested_revision_id = UUID(first.candidate_ref.revision_ref)
    elif wrong_binding == "signing_key_version":
        overrides["signing_key_version"] = claims.signing_key_version + 1
    elif wrong_binding == "nonce":
        overrides["nonce"] = bytes([claims.nonce[0] ^ 1]) + claims.nonce[1:]
    elif wrong_binding == "issued_at":
        overrides["issued_at"] = claims.issued_at + timedelta(seconds=1)
    else:
        overrides["expires_at"] = claims.issued_at - timedelta(seconds=1)

    engine = create_database_engine(migration_configuration)
    try:
        before = _replacement_state(
            engine,
            scenario,
            job_id=prepared.job_id,
            resource_ref=resource_ref,
        )
        assert _stage_replacement_direct(
            guarded_worker_engine,
            claims,
            _compile_replacement(replacement_payload, structural=structural),
            resource_ref=requested_resource_ref,
            revision_id=requested_revision_id,
            overrides=overrides,
        ) is None
        assert _replacement_state(
            engine,
            scenario,
            job_id=prepared.job_id,
            resource_ref=resource_ref,
        ) == before
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "wrong_binding",
    (
        "organization",
        "job",
        "receiver",
        "source",
        "resource",
        "previous_revision",
        "replacement_revision",
        "nonce",
        "expires_binding",
    ),
)
def test_replacement_activation_rejects_wrong_exact_bindings_with_zero_effect(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    wrong_binding: str,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=OLD_V1_MARKDOWN,
    )
    assert scenario.token is not None
    first = _run_file_import(
        scenario,
        scenario.prepared,
        scenario.token,
        guarded_worker_engine,
    )
    (scenario.root / "handbook.md").write_bytes(NEW_V1_MARKDOWN)
    prepared, token = _prepare_repeat_file_import(
        scenario,
        guarded_control_engine,
        idempotency_key=f"activate-wrong-{wrong_binding}",
    )
    claims = scenario.codec.verify(
        token,
        expected_organization_id=scenario.organization_id,
        expected_job_id=prepared.job_id,
        expected_service_principal_id=scenario.receiver.service_principal_id,
        expected_workload=scenario.receiver.workload,
        expected_operation=scenario.receiver.operation,
        expected_worker_audience=scenario.receiver.worker_audience,
        expected_source_ref=str(scenario.source_ref.value),
        now=datetime.now(UTC).replace(microsecond=0),
    )
    assert _redeem_direct(guarded_worker_engine, claims) is not None
    resource_ref = first.candidate_ref.resource_ref
    replacement_revision_id = UUID(int=prepared.job_id.int ^ 1)
    staged = _stage_replacement_direct(
        guarded_worker_engine,
        claims,
        _compile_replacement(NEW_V1_MARKDOWN, structural=False),
        resource_ref=resource_ref,
        revision_id=replacement_revision_id,
    )
    assert staged is not None
    previous_revision_id = UUID(first.candidate_ref.revision_ref)
    requested_resource_ref = resource_ref
    requested_previous_revision_id = previous_revision_id
    requested_replacement_revision_id = replacement_revision_id
    overrides: dict[str, object] = {}
    if wrong_binding == "organization":
        overrides["organization_id"] = UUID(int=scenario.organization_id.int ^ 1)
    elif wrong_binding == "job":
        overrides["job_id"] = UUID(int=prepared.job_id.int ^ 1)
    elif wrong_binding == "receiver":
        overrides["service_principal_id"] = UUID(
            int=claims.service_principal_id.int ^ 1
        )
    elif wrong_binding == "source":
        overrides["source_ref"] = str(UUID(int=scenario.source_ref.value.int ^ 1))
    elif wrong_binding == "resource":
        requested_resource_ref = f"resource:wrong:{prepared.job_id}"
    elif wrong_binding == "previous_revision":
        requested_previous_revision_id = UUID(int=previous_revision_id.int ^ 1)
    elif wrong_binding == "replacement_revision":
        requested_replacement_revision_id = UUID(
            int=replacement_revision_id.int ^ 1
        )
    elif wrong_binding == "nonce":
        overrides["nonce"] = bytes([claims.nonce[0] ^ 1]) + claims.nonce[1:]
    else:
        overrides["expires_at"] = claims.issued_at - timedelta(seconds=1)

    engine = create_database_engine(migration_configuration)
    try:
        before = _replacement_state(
            engine,
            scenario,
            job_id=prepared.job_id,
            resource_ref=resource_ref,
        )
        assert _activate_replacement_direct(
            guarded_worker_engine,
            claims,
            resource_ref=requested_resource_ref,
            previous_revision_id=requested_previous_revision_id,
            replacement_revision_id=requested_replacement_revision_id,
            overrides=overrides,
        ) is None
        assert _replacement_state(
            engine,
            scenario,
            job_id=prepared.job_id,
            resource_ref=resource_ref,
        ) == before
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "revoked_authority",
    ("principal", "membership", "access"),
)
def test_replacement_activation_rejects_revoked_authority_with_zero_effect(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    revoked_authority: str,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=OLD_V1_MARKDOWN,
    )
    assert scenario.token is not None
    first = _run_file_import(
        scenario,
        scenario.prepared,
        scenario.token,
        guarded_worker_engine,
    )
    (scenario.root / "handbook.md").write_bytes(NEW_V1_MARKDOWN)
    prepared, token = _prepare_repeat_file_import(
        scenario,
        guarded_control_engine,
        idempotency_key=f"activate-revoked-{revoked_authority}",
    )
    claims = scenario.codec.verify(
        token,
        expected_organization_id=scenario.organization_id,
        expected_job_id=prepared.job_id,
        expected_service_principal_id=scenario.receiver.service_principal_id,
        expected_workload=scenario.receiver.workload,
        expected_operation=scenario.receiver.operation,
        expected_worker_audience=scenario.receiver.worker_audience,
        expected_source_ref=str(scenario.source_ref.value),
        now=datetime.now(UTC).replace(microsecond=0),
    )
    assert _redeem_direct(guarded_worker_engine, claims) is not None
    resource_ref = first.candidate_ref.resource_ref
    replacement_revision_id = UUID(int=prepared.job_id.int ^ 1)
    assert _stage_replacement_direct(
        guarded_worker_engine,
        claims,
        _compile_replacement(NEW_V1_MARKDOWN, structural=False),
        resource_ref=resource_ref,
        revision_id=replacement_revision_id,
    ) is not None

    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            if revoked_authority == "principal":
                connection.execute(
                    text(
                        """
                        UPDATE service_principal SET enabled = false
                        WHERE organization_id = :organization_id
                          AND service_principal_id = :principal_id
                        """
                    ),
                    {
                        "organization_id": scenario.organization_id,
                        "principal_id": scenario.receiver.service_principal_id,
                    },
                )
            elif revoked_authority == "membership":
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
            else:
                connection.execute(
                    text(
                        """
                        UPDATE resource_access_policy
                        SET access_state = 'revoked', revoked_at = :revoked_at
                        WHERE organization_id = :organization_id
                          AND resource_ref = :resource_ref
                        """
                    ),
                    {
                        "organization_id": scenario.organization_id,
                        "resource_ref": resource_ref,
                        "revoked_at": NOW,
                    },
                )
        before = _replacement_state(
            engine,
            scenario,
            job_id=prepared.job_id,
            resource_ref=resource_ref,
        )
        assert _activate_replacement_direct(
            guarded_worker_engine,
            claims,
            resource_ref=resource_ref,
            previous_revision_id=UUID(first.candidate_ref.revision_ref),
            replacement_revision_id=replacement_revision_id,
        ) is None
        assert _replacement_state(
            engine,
            scenario,
            job_id=prepared.job_id,
            resource_ref=resource_ref,
        ) == before
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("boundary", "structural"),
    (("stage", False), ("stage", True), ("activate", False)),
    ids=("stage-v1", "stage-v2", "activate"),
)
def test_replacement_rejects_a_lease_that_expires_at_the_durable_boundary(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    boundary: str,
    structural: bool,
) -> None:
    old_payload = OLD_MARKDOWN if structural else OLD_V1_MARKDOWN
    replacement_payload = NEW_MARKDOWN if structural else NEW_V1_MARKDOWN
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
        config_version=("markdown-config-v2" if structural else "markdown-config-v1"),
    )
    (scenario.root / "handbook.md").write_bytes(replacement_payload)
    prepared, token = _prepare_repeat_file_import(
        scenario,
        guarded_control_engine,
        idempotency_key=f"expire-at-{boundary}-{structural}-boundary",
        lease_ttl_seconds=2,
    )
    claims = scenario.codec.verify(
        token,
        expected_organization_id=scenario.organization_id,
        expected_job_id=prepared.job_id,
        expected_service_principal_id=scenario.receiver.service_principal_id,
        expected_workload=scenario.receiver.workload,
        expected_operation=scenario.receiver.operation,
        expected_worker_audience=scenario.receiver.worker_audience,
        expected_source_ref=str(scenario.source_ref.value),
        now=datetime.now(UTC).replace(microsecond=0),
    )
    assert _redeem_direct(guarded_worker_engine, claims) is not None
    resource_ref = first.candidate_ref.resource_ref
    replacement_revision_id = UUID(int=prepared.job_id.int ^ 1)
    document = _compile_replacement(replacement_payload, structural=structural)
    if boundary == "activate":
        assert _stage_replacement_direct(
            guarded_worker_engine,
            claims,
            document,
            resource_ref=resource_ref,
            revision_id=replacement_revision_id,
        ) is not None

    engine = create_database_engine(migration_configuration)
    try:
        before = _replacement_state(
            engine,
            scenario,
            job_id=prepared.job_id,
            resource_ref=resource_ref,
        )
        with engine.connect() as connection:
            connection.execute(text("SELECT pg_sleep(2.1)"))
        result = (
            _stage_replacement_direct(
                guarded_worker_engine,
                claims,
                document,
                resource_ref=resource_ref,
                revision_id=replacement_revision_id,
            )
            if boundary == "stage"
            else _activate_replacement_direct(
                guarded_worker_engine,
                claims,
                resource_ref=resource_ref,
                previous_revision_id=UUID(first.candidate_ref.revision_ref),
                replacement_revision_id=replacement_revision_id,
            )
        )
        assert result is None
        assert _replacement_state(
            engine,
            scenario,
            job_id=prepared.job_id,
            resource_ref=resource_ref,
        ) == before
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "revoked_authority",
    ("principal", "membership", "access"),
)
@pytest.mark.parametrize("structural", (False, True), ids=("v1", "v2"))
def test_replacement_stage_rejects_revoked_authority_with_zero_effect(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    revoked_authority: str,
    structural: bool,
) -> None:
    old_payload = OLD_MARKDOWN if structural else OLD_V1_MARKDOWN
    replacement_payload = NEW_MARKDOWN if structural else NEW_V1_MARKDOWN
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
        config_version=("markdown-config-v2" if structural else "markdown-config-v1"),
    )
    (scenario.root / "handbook.md").write_bytes(replacement_payload)
    prepared, token = _prepare_repeat_file_import(
        scenario,
        guarded_control_engine,
        idempotency_key=f"stage-revoked-{revoked_authority}-{structural}",
    )
    claims = scenario.codec.verify(
        token,
        expected_organization_id=scenario.organization_id,
        expected_job_id=prepared.job_id,
        expected_service_principal_id=scenario.receiver.service_principal_id,
        expected_workload=scenario.receiver.workload,
        expected_operation=scenario.receiver.operation,
        expected_worker_audience=scenario.receiver.worker_audience,
        expected_source_ref=str(scenario.source_ref.value),
        now=datetime.now(UTC).replace(microsecond=0),
    )
    assert _redeem_direct(guarded_worker_engine, claims) is not None
    resource_ref = first.candidate_ref.resource_ref
    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            if revoked_authority == "principal":
                connection.execute(
                    text(
                        """
                        UPDATE service_principal SET enabled = false
                        WHERE organization_id = :organization_id
                          AND service_principal_id = :principal_id
                        """
                    ),
                    {
                        "organization_id": scenario.organization_id,
                        "principal_id": scenario.receiver.service_principal_id,
                    },
                )
            elif revoked_authority == "membership":
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
            else:
                connection.execute(
                    text(
                        """
                        UPDATE resource_access_policy
                        SET access_state = 'revoked', revoked_at = :revoked_at
                        WHERE organization_id = :organization_id
                          AND resource_ref = :resource_ref
                        """
                    ),
                    {
                        "organization_id": scenario.organization_id,
                        "resource_ref": resource_ref,
                        "revoked_at": NOW,
                    },
                )
        before = _replacement_state(
            engine,
            scenario,
            job_id=prepared.job_id,
            resource_ref=resource_ref,
        )
        assert _stage_replacement_direct(
            guarded_worker_engine,
            claims,
            _compile_replacement(replacement_payload, structural=structural),
            resource_ref=resource_ref,
            revision_id=UUID(int=prepared.job_id.int ^ 1),
        ) is None
        assert _replacement_state(
            engine,
            scenario,
            job_id=prepared.job_id,
            resource_ref=resource_ref,
        ) == before
    finally:
        engine.dispose()


def test_unchanged_content_never_creates_replacement_lineage(
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
    first = _run_file_import(
        scenario,
        scenario.prepared,
        scenario.token,
        guarded_worker_engine,
        config_version="markdown-config-v2",
    )
    repeat_prepared, repeat_token = _prepare_repeat_file_import(
        scenario,
        guarded_control_engine,
        idempotency_key="unchanged-never-replacement",
    )
    second = _run_file_import(
        scenario,
        repeat_prepared,
        repeat_token,
        guarded_worker_engine,
        config_version="markdown-config-v2",
    )

    assert second.outcome == "unchanged"
    assert second.effect_count == 0
    assert second.candidate_refs == first.candidate_refs
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            counts = connection.execute(
                text(
                    """
                    SELECT
                        (SELECT count(*) FROM file_revision_replacement_plan
                         WHERE organization_id = :organization_id),
                        (SELECT count(*) FROM file_revision_supersession
                         WHERE organization_id = :organization_id)
                    """
                ),
                {"organization_id": scenario.organization_id},
            ).one()
        assert tuple(counts) == (0, 0)
    finally:
        engine.dispose()
