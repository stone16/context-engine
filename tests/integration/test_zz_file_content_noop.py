from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from adapters.exact_phrase import PostgreSQLExactPhraseCandidateIndex
from adapters.http.app import create_app
from engine.control import FileImportPath
from engine.persistence import (
    DatabaseConfiguration,
    FileImportUnavailable,
    PostgreSQLMembershipAuthority,
    create_database_engine,
)
from engine.persistence.file_imports import _resource_ref
from engine.runtime.construction import Runtime, required_kernel_dependencies
from engine.runtime.package_digest import QueryDigestKeyring
from engine.supply import WorkNotAvailable
from tests.integration.test_file_import_tracer import (
    MARKDOWN_FIXTURES,
    NOW,
    ROOT,
    _ExactScopeAuthority,
    _FileImportScenario,
    _OrganizationAuthority,
    _prepare_file_import_scenario,
    _prepare_repeat_file_import,
    _publication_effect_counts,
    _run_file_import,
    _RuntimeAuthenticator,
)
from tests.support.file_source_progress import clear_file_source_progress_projection
from tests.support.releases import (
    clear_test_runtime_release,
    ensure_test_runtime_release,
)

pytestmark = pytest.mark.integration


def _resolve_lineage(
    scenario: _FileImportScenario,
    migration_configuration: DatabaseConfiguration,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
    *,
    source_ref: str,
    resource_ref: str,
    request_id: str,
) -> tuple[str, str, str, str, str]:
    ensure_test_runtime_release(scenario.organization_id)
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            user_id = connection.execute(
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
            ).scalar_one()
    finally:
        migration_engine.dispose()
    client = TestClient(
        create_app(
            authenticator=_RuntimeAuthenticator(
                scenario.organization_id,
                user_id,
                scenario.membership_id,
            ),
            organization_authority=_OrganizationAuthority(),
            membership_authority=PostgreSQLMembershipAuthority(guarded_runtime_engine),
            scope_authority=_ExactScopeAuthority(source_ref, resource_ref),
            runtime=Runtime(
                required_kernel_dependencies(),
                candidate_index=PostgreSQLExactPhraseCandidateIndex(),
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
        json={
            "kind": "acquire",
            "need": {"query": "ContextEngine delivers context."},
        },
    )
    assert response.status_code == 200
    package = response.json()["package"]
    evidence = package["evidence"][0]
    return (
        package["blocks"][0]["text"],
        evidence["sourceRef"],
        evidence["resourceRef"],
        evidence["revisionRef"],
        evidence["fragmentRef"],
    )


def test_repeated_canonically_identical_file_import_is_an_auditable_noop(
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
    )
    assert scenario.token is not None
    first = _run_file_import(
        scenario,
        scenario.prepared,
        scenario.token,
        guarded_worker_engine,
    )
    before_lineage = _resolve_lineage(
        scenario,
        migration_configuration,
        guarded_runtime_engine,
        query_digest_keyring,
        source_ref=first.candidate_ref.source_ref,
        resource_ref=first.candidate_ref.resource_ref,
        request_id="file-noop-before",
    )
    canonical_equivalent_bytes = (
        b"\xef\xbb\xbf# Handbook\r\n\r\nContextEngine delivers context.\r\n"
    )
    assert canonical_equivalent_bytes != (
        b"# Handbook\n\nContextEngine delivers context.\n"
    )
    (scenario.root / "handbook.md").write_bytes(canonical_equivalent_bytes)
    repeat_prepared, repeat_token = _prepare_repeat_file_import(
        scenario,
        guarded_control_engine,
        idempotency_key="repeat-identical-content",
    )
    second = _run_file_import(
        scenario,
        repeat_prepared,
        repeat_token,
        guarded_worker_engine,
    )

    assert first.outcome == "published"
    assert first.effect_count == 1
    assert second.outcome == "unchanged"
    assert second.effect_count == 0
    assert second.reason_digest is not None
    assert second.candidate_refs == first.candidate_refs
    assert second.content_identity_digest == first.content_identity_digest
    expected_identity = sha256(
        b"context-engine.file-content-identity.v1\x00"
        + scenario.organization_id.bytes
        + scenario.source_ref.value.bytes
        + first.candidate_ref.resource_ref.encode("utf-8")
        + b"\x00"
        + sha256(b"# Handbook\n\nContextEngine delivers context.\n")
        .hexdigest()
        .encode("ascii")
        + b"\x00context-engine-markdown-v1"
        + b"\x00markdown-config-v1"
    ).hexdigest()
    expected_reason = sha256(
        b"context-engine.file-no-op-reason.v1\x00"
        + b"active-content-identity-match\x00"
        + bytes.fromhex(expected_identity)
        + UUID(first.candidate_ref.revision_ref).bytes
    ).hexdigest()
    assert first.content_identity_digest == expected_identity
    assert second.reason_digest == expected_reason
    after_lineage = _resolve_lineage(
        scenario,
        migration_configuration,
        guarded_runtime_engine,
        query_digest_keyring,
        source_ref=second.candidate_ref.source_ref,
        resource_ref=second.candidate_ref.resource_ref,
        request_id="file-noop-after",
    )
    assert after_lineage == before_lineage

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            counts = _publication_effect_counts(
                connection,
                scenario.organization_id,
            )
            result = connection.execute(
                text(
                    """
                    SELECT outcome, active_revision_id,
                           content_identity_digest, reason_code, reason_digest
                    FROM file_acquisition_result
                    WHERE organization_id = :organization_id
                      AND acquisition_id = :acquisition_id
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "acquisition_id": second.acquisition_id,
                },
            ).one()
            job = connection.execute(
                text(
                    """
                    SELECT state, effect_count, revision_id
                    FROM file_import_job
                    WHERE organization_id = :organization_id
                      AND job_id = :job_id
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "job_id": repeat_prepared.job_id,
                },
            ).one()
    finally:
        migration_engine.dispose()

    assert counts == (2, 2, 1, 1, 1, 1, 3, 1, 1, 1)
    assert result.outcome == "unchanged"
    assert result.active_revision_id == UUID(first.candidate_ref.revision_ref)
    assert result.content_identity_digest == first.content_identity_digest
    assert result.reason_code == "active-content-identity-match"
    assert result.reason_digest == second.reason_digest
    assert job.state == "completed"
    assert job.effect_count == 0
    assert job.revision_id == UUID(first.candidate_ref.revision_ref)

    with migration_engine.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM decision_audit "
                "WHERE organization_id = :organization_id"
            ),
            {"organization_id": scenario.organization_id},
        )
        connection.execute(
            text(
                "DELETE FROM context_run "
                "WHERE organization_id = :organization_id"
            ),
            {"organization_id": scenario.organization_id},
        )
    clear_test_runtime_release(scenario.organization_id)
    clear_file_source_progress_projection(migration_configuration)
    with pytest.raises(
        RuntimeError,
        match="File (?:recovery|replacement|no-op) downgrade requires",
    ):
        command.downgrade(Config(ROOT / "alembic.ini"), "20260723_0012")
    with migration_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            == "20260724_0023"
        )


def test_concurrent_identical_file_imports_publish_at_most_once(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
    )
    assert scenario.token is not None
    second_prepared, second_token = _prepare_repeat_file_import(
        scenario,
        guarded_control_engine,
        idempotency_key="concurrent-identical-content",
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(
                _run_file_import,
                scenario,
                scenario.prepared,
                scenario.token,
                guarded_worker_engine,
            ),
            executor.submit(
                _run_file_import,
                scenario,
                second_prepared,
                second_token,
                guarded_worker_engine,
            ),
        )
        outcomes = tuple(future.result(timeout=10) for future in futures)

    assert sorted(outcome.effect_count for outcome in outcomes) == [0, 1]
    assert {outcome.outcome for outcome in outcomes} == {"published", "unchanged"}
    assert outcomes[0].candidate_refs == outcomes[1].candidate_refs
    assert outcomes[0].content_identity_digest == outcomes[1].content_identity_digest

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            assert _publication_effect_counts(
                connection,
                scenario.organization_id,
            ) == (2, 2, 1, 1, 1, 1, 3, 1, 1, 1)
            assert (
                connection.execute(
                    text(
                        """
                    SELECT count(*) FROM file_acquisition_result
                    WHERE organization_id = :organization_id
                    """
                    ),
                    {"organization_id": scenario.organization_id},
                ).scalar_one()
                == 1
            )
    finally:
        migration_engine.dispose()


@pytest.mark.parametrize(
    ("reason_code", "reason_digest"),
    (
        (None, "a" * 64),
        ("active-content-identity-match", None),
    ),
)
def test_unchanged_outcome_requires_complete_reason_lineage(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    reason_code: str | None,
    reason_digest: str | None,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
    )
    assert scenario.token is not None
    first = _run_file_import(
        scenario,
        scenario.prepared,
        scenario.token,
        guarded_worker_engine,
    )

    migration_engine = create_database_engine(migration_configuration)
    try:
        with (
            pytest.raises(IntegrityError),
            migration_engine.begin() as connection,
        ):
            connection.execute(
                text(
                    """
                    INSERT INTO file_acquisition_result (
                        organization_id, acquisition_id, source_id,
                        resource_ref, active_revision_id, outcome,
                        content_identity_digest, reason_code,
                        reason_digest, observed_at
                    ) VALUES (
                        :organization_id, :acquisition_id, :source_id,
                        :resource_ref, :active_revision_id, 'unchanged',
                        :content_identity_digest, :reason_code,
                        :reason_digest, :observed_at
                    )
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "acquisition_id": first.acquisition_id,
                    "source_id": scenario.source_ref.value,
                    "resource_ref": first.candidate_ref.resource_ref,
                    "active_revision_id": UUID(first.candidate_ref.revision_ref),
                    "content_identity_digest": first.content_identity_digest,
                    "reason_code": reason_code,
                    "reason_digest": reason_digest,
                    "observed_at": NOW,
                },
            )
    finally:
        migration_engine.dispose()


def test_repeated_identical_structural_import_reuses_exact_active_artifact(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=(MARKDOWN_FIXTURES / "combined-v2.md").read_bytes(),
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
        idempotency_key="repeat-identical-structural-content",
    )
    second = _run_file_import(
        scenario,
        repeat_prepared,
        repeat_token,
        guarded_worker_engine,
        config_version="markdown-config-v2",
    )

    assert first.outcome == "published"
    assert second.outcome == "unchanged"
    assert second.effect_count == 0
    assert second.candidate_refs == first.candidate_refs
    assert len(second.candidate_refs) == 6

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            assert _publication_effect_counts(
                connection,
                scenario.organization_id,
            ) == (2, 2, 1, 1, 6, 1, 3, 15, 1, 1)
    finally:
        migration_engine.dispose()


def test_extra_active_index_candidate_is_not_treated_as_unchanged(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=(MARKDOWN_FIXTURES / "combined-v2.md").read_bytes(),
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
        idempotency_key="repeat-corrupted-active-index",
    )

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO exact_phrase_candidate (
                        organization_id, phrase_digest, source_ref,
                        resource_ref, revision_id, fragment_ref
                    ) VALUES (
                        :organization_id, :phrase_digest, :source_ref,
                        :resource_ref, :revision_id, :fragment_ref
                    )
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "phrase_digest": sha256(
                        b"context-engine.exact-phrase.v1\x00unexpected phrase"
                    ).hexdigest(),
                    "source_ref": first.candidate_ref.source_ref,
                    "resource_ref": first.candidate_ref.resource_ref,
                    "revision_id": UUID(first.candidate_ref.revision_ref),
                    "fragment_ref": first.candidate_ref.fragment_ref,
                },
            )

        with pytest.raises(FileImportUnavailable):
            _run_file_import(
                scenario,
                repeat_prepared,
                repeat_token,
                guarded_worker_engine,
                config_version="markdown-config-v2",
            )

        with migration_engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        """
                        SELECT state FROM file_import_job
                        WHERE organization_id = :organization_id
                          AND job_id = :job_id
                        """
                    ),
                    {
                        "organization_id": scenario.organization_id,
                        "job_id": repeat_prepared.job_id,
                    },
                ).scalar_one()
                == "failed"
            )
            assert (
                connection.execute(
                    text(
                        """
                        SELECT count(*) FROM file_acquisition_result
                        WHERE organization_id = :organization_id
                        """
                    ),
                    {"organization_id": scenario.organization_id},
                ).scalar_one()
                == 0
            )
    finally:
        migration_engine.dispose()


@pytest.mark.parametrize(
    ("payload", "config_version"),
    (
        (b"# Handbook\n\nChanged source content.\n", "markdown-config-v1"),
        (
            b"# Handbook\n\nContextEngine delivers context.\n",
            "markdown-config-v2",
        ),
    ),
)
def test_changed_content_or_compiler_contract_takes_replacement_not_noop_path(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    payload: bytes,
    config_version: str,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
    )
    assert scenario.token is not None
    first = _run_file_import(
        scenario,
        scenario.prepared,
        scenario.token,
        guarded_worker_engine,
    )
    (scenario.root / "handbook.md").write_bytes(payload)
    changed_prepared, changed_token = _prepare_repeat_file_import(
        scenario,
        guarded_control_engine,
        idempotency_key=f"changed-{config_version}",
    )

    replacement = _run_file_import(
        scenario,
        changed_prepared,
        changed_token,
        guarded_worker_engine,
        config_version=config_version,
    )

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            counts = _publication_effect_counts(
                connection, scenario.organization_id
            )
            assert counts[:4] == (2, 2, 1, 2)
            assert counts[5:7] == (2, 6)
            assert counts[8:] == (1, 1)
            assert (
                connection.execute(
                    text(
                        """
                    SELECT count(*) FROM file_acquisition_result
                    WHERE organization_id = :organization_id
                    """
                    ),
                    {"organization_id": scenario.organization_id},
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text(
                        """
                    SELECT state FROM file_import_job
                    WHERE organization_id = :organization_id
                      AND job_id = :job_id
                    """
                    ),
                    {
                        "organization_id": scenario.organization_id,
                        "job_id": changed_prepared.job_id,
                    },
                ).scalar_one()
                == "completed"
            )
    finally:
        migration_engine.dispose()

    assert first.outcome == "published"
    assert replacement.outcome == "replaced"
    assert replacement.effect_count == 1
    assert replacement.reason_digest is None
    assert replacement.candidate_ref.revision_ref != first.candidate_ref.revision_ref


def test_identical_content_in_different_organizations_remains_independent(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    first_scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
    )
    second_scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
    )
    assert first_scenario.token is not None
    assert second_scenario.token is not None

    first = _run_file_import(
        first_scenario,
        first_scenario.prepared,
        first_scenario.token,
        guarded_worker_engine,
    )
    second = _run_file_import(
        second_scenario,
        second_scenario.prepared,
        second_scenario.token,
        guarded_worker_engine,
    )

    assert first.outcome == second.outcome == "published"
    assert first.candidate_ref.organization_id != second.candidate_ref.organization_id
    assert first.candidate_ref.resource_ref != second.candidate_ref.resource_ref
    assert first.candidate_ref.revision_ref != second.candidate_ref.revision_ref
    assert first.content_identity_digest != second.content_identity_digest


@pytest.mark.parametrize("membership_state", ("revoked", "expired"))
def test_revoked_or_expired_membership_never_takes_noop_path(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    membership_state: str,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
    )
    assert scenario.token is not None
    first = _run_file_import(
        scenario,
        scenario.prepared,
        scenario.token,
        guarded_worker_engine,
    )
    repeat_prepared, repeat_token = _prepare_repeat_file_import(
        scenario,
        guarded_control_engine,
        idempotency_key=f"membership-{membership_state}-before-repeat",
    )

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            if membership_state == "revoked":
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
                        UPDATE membership SET valid_until = :valid_until
                        WHERE organization_id = :organization_id
                          AND membership_id = :membership_id
                        """
                    ),
                    {
                        "organization_id": scenario.organization_id,
                        "membership_id": scenario.membership_id,
                        "valid_until": NOW,
                    },
                )

        with pytest.raises(WorkNotAvailable):
            _run_file_import(
                scenario,
                repeat_prepared,
                repeat_token,
                guarded_worker_engine,
            )

        with migration_engine.connect() as connection:
            assert _publication_effect_counts(
                connection,
                scenario.organization_id,
            ) == (2, 2, 1, 1, 1, 1, 3, 1, 1, 1)
            assert (
                connection.execute(
                    text(
                        """
                        SELECT count(*) FROM file_acquisition_result
                        WHERE organization_id = :organization_id
                        """
                    ),
                    {"organization_id": scenario.organization_id},
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text(
                        """
                        SELECT active_revision_id FROM context_resource
                        WHERE organization_id = :organization_id
                          AND resource_ref = :resource_ref
                        """
                    ),
                    {
                        "organization_id": scenario.organization_id,
                        "resource_ref": first.candidate_ref.resource_ref,
                    },
                ).scalar_one()
                == UUID(first.candidate_ref.revision_ref)
            )
            assert (
                connection.execute(
                    text(
                        """
                        SELECT state FROM file_import_job
                        WHERE organization_id = :organization_id
                          AND job_id = :job_id
                        """
                    ),
                    {
                        "organization_id": scenario.organization_id,
                        "job_id": repeat_prepared.job_id,
                    },
                ).scalar_one()
                == "failed"
            )
    finally:
        migration_engine.dispose()


def test_incomplete_active_artifact_is_not_treated_as_unchanged(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
    )
    assert scenario.token is not None
    resource_ref = _resource_ref(
        scenario.source_ref,
        FileImportPath("handbook.md"),
    )
    partial_revision_id = UUID("00000000-0000-4000-8000-000000000025")
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            connection.execute(
                text(
                    """
                    INSERT INTO context_resource (
                        organization_id, resource_ref, source_ref,
                        active_revision_id, tombstoned
                    ) VALUES (
                        :organization_id, :resource_ref, :source_ref,
                        :revision_id, false
                    )
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "resource_ref": resource_ref,
                    "source_ref": str(scenario.source_ref.value),
                    "revision_id": partial_revision_id,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO context_revision (
                        organization_id, resource_ref, revision_id
                    ) VALUES (:organization_id, :resource_ref, :revision_id)
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "resource_ref": resource_ref,
                    "revision_id": partial_revision_id,
                },
            )

        with pytest.raises(WorkNotAvailable):
            _run_file_import(
                scenario,
                scenario.prepared,
                scenario.token,
                guarded_worker_engine,
            )

        with migration_engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        """
                    SELECT count(*) FROM file_acquisition_result
                    WHERE organization_id = :organization_id
                    """
                    ),
                    {"organization_id": scenario.organization_id},
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text(
                        """
                    SELECT state FROM file_import_job
                    WHERE organization_id = :organization_id
                      AND job_id = :job_id
                    """
                    ),
                    {
                        "organization_id": scenario.organization_id,
                        "job_id": scenario.prepared.job_id,
                    },
                ).scalar_one()
                == "failed"
            )
    finally:
        migration_engine.dispose()
