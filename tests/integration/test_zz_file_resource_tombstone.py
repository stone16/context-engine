from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from engine.control import (
    ContextControl,
    ControlOperation,
    ControlOperatorAuthority,
    FileImportAudience,
    FileImportPath,
    FileResourceTombstone,
    FileRootRef,
    PrepareFileImport,
    RegisterFileSource,
    SourceControlUnavailable,
    SourceNotAvailable,
    TombstoneFileResource,
)
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLControlStore,
    PostgreSQLWorkerLeaseIssuer,
    create_database_engine,
)
from engine.runtime.contracts import Acquire
from engine.runtime.evidence import CandidateRef
from engine.runtime.materialized import MaterializedProjectionSession
from engine.runtime.package_digest import QueryDigestKeyring
from tests.integration.test_file_import_tracer import (
    NOW,
    _ControlAuthenticator,
    _FileImportScenario,
    _prepare_file_import_scenario,
    _run_file_import,
)
from tests.integration.test_zz_file_revision_replacement import (
    OLD_MARKDOWN,
    _resolve,
    _scenario_user_id,
)

pytestmark = pytest.mark.integration


class _ReplayCandidateIndex:
    def __init__(self, candidate: CandidateRef) -> None:
        self.candidate = candidate

    def discover(
        self,
        request: Acquire,
        projection_session: MaterializedProjectionSession,
    ) -> tuple[CandidateRef, ...]:
        del request, projection_session
        return (self.candidate,)


def _control(
    scenario: _FileImportScenario,
    guarded_control_engine: Engine,
) -> tuple[ContextControl, ControlOperatorAuthority]:
    authority = ControlOperatorAuthority(
        _ControlAuthenticator(scenario.organization_id),
        call_ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    )
    return (
        ContextControl(
            store=PostgreSQLControlStore(
                guarded_control_engine,
                clock=lambda: NOW,
            ),
            authority=authority,
            clock=lambda: NOW,
        ),
        authority,
    )


def _tombstone(
    scenario: _FileImportScenario,
    guarded_control_engine: Engine,
    *,
    resource_ref: str,
    event_ref: str,
    event_sequence: int,
) -> FileResourceTombstone:
    control, authority = _control(scenario, guarded_control_engine)
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.TOMBSTONE_FILE_RESOURCE,
        request_id=f"tombstone-{event_ref}",
    ) as call:
        return control.tombstone_file_resource(
            call,
            TombstoneFileResource(
                source_ref=scenario.source_ref,
                resource_ref=resource_ref,
                event_ref=event_ref,
                event_sequence=event_sequence,
            ),
        )


def _prepare_second_source_scenario(
    scenario: _FileImportScenario,
    tmp_path: Path,
    guarded_control_engine: Engine,
) -> _FileImportScenario:
    root_ref = FileRootRef(f"second-root-{scenario.organization_id.hex}")
    root = tmp_path / root_ref.value
    root.mkdir()
    (root / "appendix.md").write_bytes(b"# Appendix\n\nOther content.\n")
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
        ),
        authority=authority,
        clock=lambda: NOW,
    )
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.REGISTER_SOURCE,
        request_id="register-second-tombstone-source",
    ) as call:
        source = control.register_source(
            call,
            RegisterFileSource(
                "Appendix",
                root_ref,
                "second-tombstone-source",
            ),
        )
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.IMPORT_FILE,
        request_id="prepare-second-tombstone-source",
    ) as call:
        prepared = control.prepare_file_import(
            call,
            PrepareFileImport(
                source_ref=source.source_ref,
                path=FileImportPath("appendix.md"),
                audience=FileImportAudience(
                    principal_ref="principal:file-reader",
                    membership_id=scenario.membership_id,
                    membership_version=1,
                ),
                idempotency_key="second-tombstone-source-import",
            ),
        )
    token = PostgreSQLWorkerLeaseIssuer(
        guarded_control_engine,
        scenario.codec,
    ).issue_file_import_lease(prepared)
    return replace(
        scenario,
        source_ref=source.source_ref,
        prepared=prepared,
        token=token,
        root_ref=root_ref,
        root=root,
    )


def _stable_empty_package(package: dict[str, Any]) -> dict[str, Any]:
    stable = dict(package)
    for field in (
        "asOf",
        "decisionRef",
        "expiresAt",
        "organizationRef",
        "packageDigest",
    ):
        stable.pop(field)
    return stable


def _runtime_storage_counts(
    guarded_runtime_engine: Engine,
    scenario: _FileImportScenario,
    *,
    user_id: UUID,
    resource_ref: str,
    revision_id: UUID,
) -> tuple[int, int, int, int]:
    settings = {
        "app.actor_kind": "user",
        "app.authentication_binding_ref": "binding:file-tombstone",
        "app.checked_at": NOW.isoformat().replace("+00:00", "Z"),
        "app.membership_id": str(scenario.membership_id),
        "app.membership_version": "1",
        "app.organization_id": str(scenario.organization_id),
        "app.principal_ref": "principal:file-reader",
        "app.request_id": "request:file-tombstone-storage",
        "app.user_id": str(user_id),
    }
    with guarded_runtime_engine.begin() as connection:
        for name, value in settings.items():
            connection.execute(
                text("SELECT set_config(:name, :value, true)"),
                {"name": name, "value": value},
            )
        row = connection.execute(
            text(
                """
                SELECT
                    (SELECT count(*) FROM context_resource
                     WHERE organization_id = :organization_id
                       AND resource_ref = :resource_ref),
                    (SELECT count(*) FROM context_revision
                     WHERE organization_id = :organization_id
                       AND resource_ref = :resource_ref
                       AND revision_id = :revision_id),
                    (SELECT count(*) FROM context_fragment
                     WHERE organization_id = :organization_id
                       AND resource_ref = :resource_ref
                       AND revision_id = :revision_id),
                    (SELECT count(*) FROM exact_phrase_candidate
                     WHERE organization_id = :organization_id
                       AND resource_ref = :resource_ref
                       AND revision_id = :revision_id)
                """
            ),
            {
                "organization_id": scenario.organization_id,
                "resource_ref": resource_ref,
                "revision_id": revision_id,
            },
        ).one()
    return cast(tuple[int, int, int, int], tuple(row))


def _assert_cleanup_intent_denies_direct_nonowner_access(
    guarded_control_engine: Engine,
    guarded_runtime_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    statements = (
        "SELECT count(*) FROM file_resource_cleanup_intent",
        "INSERT INTO file_resource_cleanup_intent DEFAULT VALUES",
        "UPDATE file_resource_cleanup_intent "
        "SET organization_id = organization_id WHERE false",
        "DELETE FROM file_resource_cleanup_intent WHERE false",
    )
    for engine in (
        guarded_control_engine,
        guarded_runtime_engine,
        guarded_worker_engine,
    ):
        for statement in statements:
            with engine.connect() as connection:
                transaction = connection.begin()
                try:
                    with pytest.raises(SQLAlchemyError):
                        connection.execute(text(statement))
                finally:
                    transaction.rollback()


@pytest.mark.security_evidence(id="PG-FILE-TOMBSTONE-028", layer="postgres")
def test_tombstone_is_synchronously_empty_with_stale_physical_rows_and_index(
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
    published = _run_file_import(
        scenario,
        scenario.prepared,
        scenario.token,
        guarded_worker_engine,
        config_version="markdown-config-v2",
    )
    resource_ref = published.candidate_ref.resource_ref
    revision_id = UUID(published.candidate_ref.revision_ref)
    user_id = _scenario_user_id(scenario, migration_configuration)

    before = _resolve(
        scenario,
        guarded_runtime_engine,
        query_digest_keyring,
        user_id=user_id,
        query="OLD marker.",
        request_id="file-tombstone-before",
    )
    assert [block["text"] for block in before["blocks"]] == [
        "# Handbook\n\nOLD marker."
    ]
    delivered_evidence = before["evidence"][0]
    candidate = CandidateRef(
        organization_id=scenario.organization_id,
        source_ref=delivered_evidence["sourceRef"],
        resource_ref=delivered_evidence["resourceRef"],
        revision_ref=delivered_evidence["revisionRef"],
        fragment_ref=delivered_evidence["fragmentRef"],
    )
    replay = _ReplayCandidateIndex(candidate)

    committed = _tombstone(
        scenario,
        guarded_control_engine,
        resource_ref=resource_ref,
        event_ref="file-delete-10",
        event_sequence=10,
    )
    assert committed.organization_id == scenario.organization_id
    assert committed.source_ref == scenario.source_ref
    assert committed.resource_ref == resource_ref
    assert committed.revision_ref == revision_id
    assert committed.policy_epoch == 2

    deleted = _resolve(
        scenario,
        guarded_runtime_engine,
        query_digest_keyring,
        user_id=user_id,
        query="OLD marker.",
        request_id="file-tombstone-deleted",
        candidate_index=replay,
    )
    missing_resource_ref = "resource:file:" + "f" * 64
    missing_candidate = CandidateRef(
        organization_id=scenario.organization_id,
        source_ref=str(scenario.source_ref.value),
        resource_ref=missing_resource_ref,
        revision_ref="ffffffff-ffff-4fff-8fff-ffffffffffff",
        fragment_ref="fragment:missing",
    )
    missing = _resolve(
        scenario,
        guarded_runtime_engine,
        query_digest_keyring,
        user_id=user_id,
        query="OLD marker.",
        request_id="file-tombstone-unknown",
        candidate_index=_ReplayCandidateIndex(missing_candidate),
        resource_ref=missing_resource_ref,
    )

    expected_empty = {
        "blocks": [],
        "budgetUsage": {
            "costMicrounits": 0,
            "elapsedMs": 0,
            "providerCalls": 0,
            "tokens": 0,
        },
        "coverage": {
            "reason": "no_authorized_evidence",
            "status": "empty",
        },
        "evidence": [],
        "gaps": [],
        "purpose": "context.answer",
        "ttlSeconds": 300,
    }
    assert _stable_empty_package(deleted) == expected_empty
    assert _stable_empty_package(missing) == expected_empty
    assert resource_ref not in str(deleted)
    assert candidate.revision_ref not in str(deleted)
    assert candidate.fragment_ref not in str(deleted)

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            durable = connection.execute(
                text(
                    """
                    SELECT resource.tombstoned, resource.active_revision_id,
                           epoch.policy_epoch,
                           intent.cleanup_intent_id, intent.revision_id,
                           intent.event_ref, intent.event_sequence,
                           intent.policy_epoch, intent.state,
                           (SELECT count(*) FROM context_revision AS revision
                            WHERE revision.organization_id = resource.organization_id
                              AND revision.resource_ref = resource.resource_ref),
                           (SELECT count(*) FROM context_fragment AS fragment
                            WHERE fragment.organization_id = resource.organization_id
                              AND fragment.resource_ref = resource.resource_ref),
                           (SELECT count(*) FROM exact_phrase_candidate AS candidate
                            WHERE candidate.organization_id = resource.organization_id
                              AND candidate.resource_ref = resource.resource_ref)
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
                    "resource_ref": resource_ref,
                },
            ).one()
        assert tuple(durable) == (
            True,
            revision_id,
            2,
            committed.cleanup_intent_ref,
            revision_id,
            "file-delete-10",
            10,
            2,
            "pending",
            1,
            4,
            6,
        )
    finally:
        migration_engine.dispose()

    # Runtime can still discover the deliberately stale candidate, but the
    # authoritative Resource, Revision, and Fragment relations expose no row.
    assert _runtime_storage_counts(
        guarded_runtime_engine,
        scenario,
        user_id=user_id,
        resource_ref=resource_ref,
        revision_id=revision_id,
    ) == (0, 0, 0, 6)
    # This assertion belongs in the registered gate selector so the table's
    # non-owner evidence cannot false-green on FORCE RLS metadata alone.
    _assert_cleanup_intent_denies_direct_nonowner_access(
        guarded_control_engine,
        guarded_runtime_engine,
        guarded_worker_engine,
    )


def test_duplicate_and_older_delete_events_are_idempotent_and_non_regressive(
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
    published = _run_file_import(
        scenario,
        scenario.prepared,
        scenario.token,
        guarded_worker_engine,
    )
    resource_ref = published.candidate_ref.resource_ref

    first = _tombstone(
        scenario,
        guarded_control_engine,
        resource_ref=resource_ref,
        event_ref="file-delete-10",
        event_sequence=10,
    )
    duplicate = _tombstone(
        scenario,
        guarded_control_engine,
        resource_ref=resource_ref,
        event_ref="file-delete-10",
        event_sequence=10,
    )
    older = _tombstone(
        scenario,
        guarded_control_engine,
        resource_ref=resource_ref,
        event_ref="file-delete-09",
        event_sequence=9,
    )
    assert duplicate == first
    assert older == first

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            state = connection.execute(
                text(
                    """
                    SELECT resource.tombstoned, epoch.policy_epoch,
                           (SELECT count(*)
                            FROM file_resource_cleanup_intent AS intent
                            WHERE intent.organization_id = :organization_id
                              AND intent.resource_ref = :resource_ref)
                    FROM context_resource AS resource
                    JOIN organization_policy_epoch AS epoch
                      ON epoch.organization_id = resource.organization_id
                    WHERE resource.organization_id = :organization_id
                      AND resource.resource_ref = :resource_ref
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "resource_ref": resource_ref,
                },
            ).one()
        assert tuple(state) == (True, 2, 1)
    finally:
        migration_engine.dispose()


def test_tombstone_and_epoch_advance_roll_back_as_one_transaction(
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
    published = _run_file_import(
        scenario,
        scenario.prepared,
        scenario.token,
        guarded_worker_engine,
    )
    resource_ref = published.candidate_ref.resource_ref
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE FUNCTION public.test_reject_tombstone_epoch()
                    RETURNS trigger
                    LANGUAGE plpgsql
                    SECURITY INVOKER
                    SET search_path = pg_catalog
                    AS $function$
                    BEGIN
                        RAISE EXCEPTION 'injected tombstone epoch failure';
                    END;
                    $function$
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TRIGGER reject_tombstone_epoch
                    BEFORE UPDATE ON organization_policy_epoch
                    FOR EACH ROW
                    EXECUTE FUNCTION public.test_reject_tombstone_epoch()
                    """
                )
            )
        try:
            with pytest.raises(SourceControlUnavailable):
                _tombstone(
                    scenario,
                    guarded_control_engine,
                    resource_ref=resource_ref,
                    event_ref="file-delete-rollback",
                    event_sequence=1,
                )
        finally:
            with migration_engine.begin() as connection:
                connection.execute(
                    text(
                        "DROP TRIGGER reject_tombstone_epoch "
                        "ON organization_policy_epoch"
                    )
                )
                connection.execute(
                    text("DROP FUNCTION public.test_reject_tombstone_epoch()")
                )

        with migration_engine.connect() as connection:
            state = connection.execute(
                text(
                    """
                    SELECT resource.tombstoned, epoch.policy_epoch,
                           (SELECT count(*)
                            FROM file_resource_cleanup_intent AS intent
                            WHERE intent.organization_id = :organization_id)
                    FROM context_resource AS resource
                    JOIN organization_policy_epoch AS epoch
                      ON epoch.organization_id = resource.organization_id
                    WHERE resource.organization_id = :organization_id
                      AND resource.resource_ref = :resource_ref
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "resource_ref": resource_ref,
                },
            ).one()
        assert tuple(state) == (False, 1, 0)
    finally:
        migration_engine.dispose()


def test_file_tombstone_is_organization_isolated(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
) -> None:
    deleted_scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
    )
    unaffected_scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
    )
    assert deleted_scenario.token is not None
    assert unaffected_scenario.token is not None
    deleted_publication = _run_file_import(
        deleted_scenario,
        deleted_scenario.prepared,
        deleted_scenario.token,
        guarded_worker_engine,
    )
    unaffected_publication = _run_file_import(
        unaffected_scenario,
        unaffected_scenario.prepared,
        unaffected_scenario.token,
        guarded_worker_engine,
    )

    _tombstone(
        deleted_scenario,
        guarded_control_engine,
        resource_ref=deleted_publication.candidate_ref.resource_ref,
        event_ref="file-delete-org-a",
        event_sequence=1,
    )
    unaffected_user_id = _scenario_user_id(
        unaffected_scenario,
        migration_configuration,
    )
    unaffected = _resolve(
        unaffected_scenario,
        guarded_runtime_engine,
        query_digest_keyring,
        user_id=unaffected_user_id,
        query="ContextEngine delivers context.",
        request_id="file-tombstone-org-b",
    )
    assert unaffected["evidence"][0]["resourceRef"] == (
        unaffected_publication.candidate_ref.resource_ref
    )

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            epochs = {
                row.organization_id: row.policy_epoch
                for row in connection.execute(
                    text(
                        "SELECT organization_id, policy_epoch "
                        "FROM organization_policy_epoch "
                        "WHERE organization_id IN (:deleted, :unaffected)"
                    ),
                    {
                        "deleted": deleted_scenario.organization_id,
                        "unaffected": unaffected_scenario.organization_id,
                    },
                )
            }
        assert epochs == {
            deleted_scenario.organization_id: 2,
            unaffected_scenario.organization_id: 1,
        }
    finally:
        migration_engine.dispose()


def test_cleanup_intent_denies_direct_access_to_every_nonowner_role(
    guarded_control_engine: Engine,
    guarded_runtime_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    _assert_cleanup_intent_denies_direct_nonowner_access(
        guarded_control_engine,
        guarded_runtime_engine,
        guarded_worker_engine,
    )


def test_unknown_or_wrong_source_tombstone_has_zero_effect(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    protected = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
    )
    other = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
    )
    assert protected.token is not None
    published = _run_file_import(
        protected,
        protected.prepared,
        protected.token,
        guarded_worker_engine,
    )
    resource_ref = published.candidate_ref.resource_ref

    for scenario, attempted_ref, event_ref in (
        (protected, "resource:file:" + "f" * 64, "missing-resource"),
        (other, resource_ref, "wrong-source-and-organization"),
    ):
        with pytest.raises(SourceNotAvailable):
            _tombstone(
                scenario,
                guarded_control_engine,
                resource_ref=attempted_ref,
                event_ref=event_ref,
                event_sequence=1,
            )

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            state = connection.execute(
                text(
                    """
                    SELECT resource.tombstoned, epoch.policy_epoch,
                           (SELECT count(*)
                            FROM file_resource_cleanup_intent AS intent
                            WHERE intent.organization_id = :organization_id)
                    FROM context_resource AS resource
                    JOIN organization_policy_epoch AS epoch
                      ON epoch.organization_id = resource.organization_id
                    WHERE resource.organization_id = :organization_id
                      AND resource.resource_ref = :resource_ref
                    """
                ),
                {
                    "organization_id": protected.organization_id,
                    "resource_ref": resource_ref,
                },
            ).one()
        assert tuple(state) == (False, 1, 0)
    finally:
        migration_engine.dispose()


def test_organization_scoped_event_ref_cannot_rebind_between_tombstoned_sources(
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
    first = _run_file_import(
        scenario,
        scenario.prepared,
        scenario.token,
        guarded_worker_engine,
    )
    second_scenario = _prepare_second_source_scenario(
        scenario,
        tmp_path,
        guarded_control_engine,
    )
    assert second_scenario.token is not None
    second = _run_file_import(
        second_scenario,
        second_scenario.prepared,
        second_scenario.token,
        guarded_worker_engine,
    )
    assert scenario.source_ref != second_scenario.source_ref
    assert first.candidate_ref.resource_ref != second.candidate_ref.resource_ref

    first_tombstone = _tombstone(
        scenario,
        guarded_control_engine,
        resource_ref=first.candidate_ref.resource_ref,
        event_ref="organization-delete-event-1",
        event_sequence=1,
    )
    second_tombstone = _tombstone(
        second_scenario,
        guarded_control_engine,
        resource_ref=second.candidate_ref.resource_ref,
        event_ref="organization-delete-event-2",
        event_sequence=2,
    )
    with pytest.raises(SourceNotAvailable):
        _tombstone(
            scenario,
            guarded_control_engine,
            resource_ref=first.candidate_ref.resource_ref,
            event_ref=second_tombstone.event_ref,
            event_sequence=3,
        )
    with pytest.raises(SourceNotAvailable):
        _tombstone(
            second_scenario,
            guarded_control_engine,
            resource_ref=second.candidate_ref.resource_ref,
            event_ref=first_tombstone.event_ref,
            event_sequence=3,
        )

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            state = connection.execute(
                text(
                    """
                    SELECT resource_ref, tombstoned
                    FROM context_resource
                    WHERE organization_id = :organization_id
                      AND resource_ref IN (:first_resource_ref, :second_resource_ref)
                    ORDER BY resource_ref
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "first_resource_ref": first.candidate_ref.resource_ref,
                    "second_resource_ref": second.candidate_ref.resource_ref,
                },
            ).all()
            intent_count = connection.execute(
                text(
                    "SELECT count(*) FROM file_resource_cleanup_intent "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": scenario.organization_id},
            ).scalar_one()
        assert [tuple(row) for row in state] == sorted(
            [
                (first.candidate_ref.resource_ref, True),
                (second.candidate_ref.resource_ref, True),
            ]
        )
        assert intent_count == 2
    finally:
        migration_engine.dispose()
