from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from engine.control import (
    FILE_CAPABILITY_MANIFEST,
    FILE_CHANGE_CAPABILITY_MANIFEST,
    ActivateFileChangeFeed,
    ContextControl,
    ControlOperation,
    ControlOperatorAuthority,
    FileImportAudience,
    FileImportPath,
    FileImportReceiver,
    FileRootRef,
    PrepareFileImport,
    RegisterFileSource,
    SourceManifest,
    SourceNotAvailable,
    SourceRef,
    VerifiedControlOperatorIdentity,
)
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLControlStore,
    create_database_engine,
)
from tests.support.migrations import downgrade_revision

pytestmark = pytest.mark.integration
NOW = datetime(2026, 7, 22, 19, 30, tzinfo=UTC)


class _Authenticator:
    def __init__(self, organization_id: UUID) -> None:
        self.organization_id = organization_id

    def authenticate(self, opaque_credential: str) -> VerifiedControlOperatorIdentity:
        if opaque_credential != f"credential:{self.organization_id}":
            raise AssertionError("unexpected test credential")
        return VerifiedControlOperatorIdentity(
            organization_id=self.organization_id,
            operator_ref=f"operator:{self.organization_id}",
            authentication_binding_ref=f"binding:{self.organization_id}",
            authority_ref=f"source-admin:{self.organization_id}",
            allowed_operations=frozenset(
                {
                    ControlOperation.ACTIVATE_FILE_CHANGE_FEED,
                    ControlOperation.IMPORT_FILE,
                    ControlOperation.REGISTER_SOURCE,
                    ControlOperation.READ_SOURCE,
                }
            ),
            valid_from=NOW - timedelta(minutes=1),
            expires_at=NOW + timedelta(hours=1),
        )


def _control(engine: Engine, organization_id: UUID) -> tuple[
    ContextControl, ControlOperatorAuthority
]:
    authority = ControlOperatorAuthority(
        _Authenticator(organization_id),
        call_ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    )
    return (
        ContextControl(
            store=PostgreSQLControlStore(engine, clock=lambda: NOW),
            authority=authority,
            clock=lambda: NOW,
        ),
        authority,
    )


def _delete_disposable_file_change_organization(
    configuration: DatabaseConfiguration,
    organization_id: UUID,
    user_id: UUID,
) -> None:
    """Remove the activation test's dedicated Organization and lineage."""

    engine = create_database_engine(configuration)
    immutable_tables = (
        (
            "file_source_acquisition_checkpoint",
            "file_source_acquisition_checkpoint_immutable",
        ),
        ("file_acquisition", "file_acquisition_immutable"),
        ("source_version", "source_version_immutable"),
    )
    try:
        with engine.begin() as connection:
            for table, trigger in immutable_tables:
                connection.execute(
                    text(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}")
                )
        try:
            with engine.begin() as connection:
                for table in (
                    "file_source_acquisition_checkpoint",
                    "file_import_job",
                    "file_acquisition",
                    "article_access_policy",
                    "article_source_acl_observation",
                    "article_explicit_policy_setting",
                    "article_access_group_membership",
                    "article_access_group",
                    "source_article_policy_default",
                    "context_source",
                    "source_version",
                    "service_principal",
                    "membership",
                    "organization_article_policy_default",
                ):
                    connection.execute(
                        text(
                            f"DELETE FROM {table} "  # noqa: S608 - fixed list
                            "WHERE organization_id = :organization_id"
                        ),
                        {"organization_id": organization_id},
                    )
                connection.execute(
                    text(
                        "DELETE FROM user_account WHERE user_id = :user_id "
                        "AND NOT EXISTS ("
                        "SELECT 1 FROM membership "
                        "WHERE membership.user_id = user_account.user_id)"
                    ),
                    {"user_id": user_id},
                )
                connection.execute(
                    text(
                        "DELETE FROM organization "
                        "WHERE organization_id = :organization_id"
                    ),
                    {"organization_id": organization_id},
                )
        finally:
            with engine.begin() as connection:
                for table, trigger in reversed(immutable_tables):
                    connection.execute(
                        text(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}")
                    )
    finally:
        engine.dispose()


def _retains_v3_acquisition_lineage(
    configuration: DatabaseConfiguration, organization_id: UUID
) -> bool:
    """Evaluate the 0028 guard's acquisition-lineage branch for one Organization.

    The guard names the first whole-database blocker it finds, so the exact
    blocker string is a function of everything the volume retains. What this
    Organization proves is that its own v3 acquisition lineage is one.
    """

    engine = create_database_engine(configuration)
    try:
        with engine.connect() as connection:
            return bool(
                connection.execute(
                    text(
                        """
                        SELECT EXISTS (
                            SELECT 1
                            FROM source_version AS version
                            JOIN file_acquisition AS acquisition
                              ON acquisition.organization_id =
                                 version.organization_id
                             AND acquisition.source_id = version.source_id
                             AND acquisition.source_version_id = version.version_id
                            WHERE version.organization_id = :organization_id
                              AND version.capability_manifest ->>
                                  'declarationVersion' = 'file-capabilities-v3'
                        )
                        """
                    ),
                    {"organization_id": organization_id},
                ).scalar_one()
            )
    finally:
        engine.dispose()


def _register(
    control: ContextControl,
    authority: ControlOperatorAuthority,
    organization_id: UUID,
    command: RegisterFileSource,
    *,
    request_id: str,
) -> SourceManifest:
    with authority.authorize(
        opaque_credential=f"credential:{organization_id}",
        operation=ControlOperation.REGISTER_SOURCE,
        request_id=request_id,
    ) as call:
        return control.register_source(call, command)


def _read(
    control: ContextControl,
    authority: ControlOperatorAuthority,
    organization_id: UUID,
    source_ref: SourceRef,
    *,
    request_id: str,
) -> SourceManifest:
    with authority.authorize(
        opaque_credential=f"credential:{organization_id}",
        operation=ControlOperation.READ_SOURCE,
        request_id=request_id,
    ) as call:
        return control.read_source(call, source_ref)


@pytest.fixture
def organizations(
    migration_configuration: DatabaseConfiguration,
) -> tuple[UUID, UUID]:
    organization_a, organization_b = uuid4(), uuid4()
    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO organization (organization_id) "
                    "VALUES (:organization_a), (:organization_b)"
                ),
                {
                    "organization_a": organization_a,
                    "organization_b": organization_b,
                },
            )
    finally:
        engine.dispose()
    return organization_a, organization_b


@pytest.mark.security_evidence(id="PG-FILE-SOURCE-RLS-021", layer="postgres")
def test_control_registers_reads_and_idempotently_isolates_file_sources(
    guarded_control_engine: Engine,
    migration_configuration: DatabaseConfiguration,
    organizations: tuple[UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_a, organization_b = organizations
    control_a, authority_a = _control(guarded_control_engine, organization_a)
    control_b, authority_b = _control(guarded_control_engine, organization_b)
    command = RegisterFileSource(
        display_name="Engineering handbook",
        root_ref=FileRootRef("engineering-handbook"),
        idempotency_key="shared-registration-key",
    )

    filesystem_calls: list[object] = []

    def reject_filesystem(*args: object, **kwargs: object) -> None:
        filesystem_calls.append((args, kwargs))
        raise AssertionError("registration touched the filesystem")

    monkeypatch.setattr(Path, "open", reject_filesystem)
    monkeypatch.setattr(os, "scandir", reject_filesystem)

    first = _register(
        control_a,
        authority_a,
        organization_a,
        command,
        request_id="register-a-1",
    )
    retry = _register(
        control_a,
        authority_a,
        organization_a,
        command,
        request_id="register-a-2",
    )
    other = _register(
        control_b,
        authority_b,
        organization_b,
        command,
        request_id="register-b-1",
    )

    assert retry == first
    assert other.source_ref != first.source_ref
    assert _read(
        control_a,
        authority_a,
        organization_a,
        first.source_ref,
        request_id="read-a",
    ) == first
    assert filesystem_calls == []

    failures: list[tuple[type[Exception], str]] = []
    for source_ref in (first.source_ref, type(first.source_ref)(uuid4())):
        with pytest.raises(SourceNotAvailable) as error:
            _read(
                control_b,
                authority_b,
                organization_b,
                source_ref,
                request_id=f"read-b-{len(failures)}",
            )
        failures.append((type(error.value), str(error.value)))
    assert failures[0] == failures[1]

    with pytest.raises(SourceNotAvailable):
        _register(
            control_a,
            authority_a,
            organization_a,
            RegisterFileSource(
                display_name="Different request",
                root_ref=FileRootRef("different-root"),
                idempotency_key=command.idempotency_key,
            ),
            request_id="register-a-conflict",
        )

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            counts = {
                table: connection.execute(
                    text(
                        f"SELECT count(*) FROM {table} "  # noqa: S608 - fixed list
                        "WHERE organization_id = :organization_id"
                    ),
                    {"organization_id": organization_a},
                ).scalar_one()
                for table in (
                    "worker_noop_job",
                    "context_resource",
                    "context_revision",
                    "context_fragment",
                )
            }
            source_count = connection.execute(
                text(
                    "SELECT count(*) FROM context_source "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": organization_a},
            ).scalar_one()
            version_count = connection.execute(
                text(
                    "SELECT count(*) FROM source_version "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": organization_a},
            ).scalar_one()
    finally:
        migration_engine.dispose()
    assert counts == {
        "worker_noop_job": 0,
        "context_resource": 0,
        "context_revision": 0,
        "context_fragment": 0,
    }
    assert (source_count, version_count) == (1, 1)


@pytest.mark.security_evidence(id="PG-FILE-CHANGE-ACTIVATE-081", layer="postgres")
def test_control_atomically_activates_one_immutable_v3_file_source_version(
    guarded_control_engine: Engine,
    migration_configuration: DatabaseConfiguration,
    request: pytest.FixtureRequest,
) -> None:
    organization_id, user_id, membership_id = uuid4(), uuid4(), uuid4()
    request.addfinalizer(
        partial(
            _delete_disposable_file_change_organization,
            migration_configuration,
            organization_id,
            user_id,
        )
    )
    receiver = FileImportReceiver(uuid4())
    migration_engine = create_database_engine(migration_configuration)
    try:
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
                    ) VALUES (:org, :receiver, 'supply.file-import',
                        'context-engine-worker', 'file.import', true)
                    """
                ),
                {"org": organization_id, "receiver": receiver.service_principal_id},
            )
    finally:
        migration_engine.dispose()

    authority = ControlOperatorAuthority(
        _Authenticator(organization_id),
        call_ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    )
    control = ContextControl(
        store=PostgreSQLControlStore(
            guarded_control_engine,
            clock=lambda: NOW,
            file_import_receiver=receiver,
        ),
        authority=authority,
        clock=lambda: NOW,
    )
    source = _register(
        control,
        authority,
        organization_id,
        RegisterFileSource("Handbook", FileRootRef("handbook"), "v3-source"),
        request_id="register-v3-source",
    )
    with authority.authorize(
        opaque_credential=f"credential:{organization_id}",
        operation=ControlOperation.IMPORT_FILE,
        request_id="activate-v2-prerequisite",
    ) as call:
        control.prepare_file_import(
            call,
            PrepareFileImport(
                source_ref=source.source_ref,
                path=FileImportPath("handbook.md"),
                audience=FileImportAudience(
                    principal_ref="principal:file-reader",
                    membership_id=membership_id,
                    membership_version=1,
                ),
                idempotency_key="v2-prerequisite",
            ),
        )

    def counts() -> tuple[int, int, int, int]:
        engine = create_database_engine(migration_configuration)
        try:
            with engine.connect() as connection:
                return tuple(
                    connection.execute(
                        text(
                            """
                            SELECT
                              (SELECT count(*) FROM source_version
                               WHERE organization_id = :org AND source_id = :source),
                              (SELECT count(*) FROM file_import_job
                               WHERE organization_id = :org AND source_id = :source),
                              (SELECT count(*) FROM file_source_acquisition_checkpoint
                               WHERE organization_id = :org AND source_id = :source),
                              (SELECT count(*) FROM file_source_publish_watermark
                               WHERE organization_id = :org AND source_id = :source)
                            """
                        ),
                        {"org": organization_id, "source": source.source_ref.value},
                    ).one()
                )
        finally:
            engine.dispose()

    before = counts()
    with authority.authorize(
        opaque_credential=f"credential:{organization_id}",
        operation=ControlOperation.ACTIVATE_FILE_CHANGE_FEED,
        request_id="activate-v3",
    ) as call:
        activated = control.activate_file_change_feed(
            call, ActivateFileChangeFeed(source.source_ref)
        )
    with authority.authorize(
        opaque_credential=f"credential:{organization_id}",
        operation=ControlOperation.ACTIVATE_FILE_CHANGE_FEED,
        request_id="activate-v3-replay",
    ) as call:
        replay = control.activate_file_change_feed(
            call, ActivateFileChangeFeed(source.source_ref)
        )

    assert replay == activated
    assert activated.active_version.capabilities is FILE_CHANGE_CAPABILITY_MANIFEST
    assert activated.active_version.version_ref != source.active_version.version_ref
    assert counts() == (before[0] + 1, before[1], before[2], before[3])

    with authority.authorize(
        opaque_credential=f"credential:{organization_id}",
        operation=ControlOperation.IMPORT_FILE,
        request_id="v3-manual-import-still-active",
    ) as call:
        control.prepare_file_import(
            call,
            PrepareFileImport(
                source_ref=source.source_ref,
                path=FileImportPath("after-activation.md"),
                audience=FileImportAudience(
                    principal_ref="principal:file-reader",
                    membership_id=membership_id,
                    membership_version=1,
                ),
                idempotency_key="v3-manual-import",
            ),
        )
    assert counts() == (
        before[0] + 1,
        before[1] + 1,
        before[2] + 1,
        before[3],
    )

    assert _retains_v3_acquisition_lineage(migration_configuration, organization_id)
    with pytest.raises(
        RuntimeError,
        match="File change-feed downgrade requires no retained",
    ):
        downgrade_revision(migration_configuration, "20260725_0028")


def test_file_source_tables_fail_closed_for_non_owner_role_matrix(
    guarded_control_engine: Engine,
    guarded_runtime_engine: Engine,
    organizations: tuple[UUID, UUID],
) -> None:
    organization_a, organization_b = organizations

    for table_name in ("context_source", "source_version"):
        with guarded_control_engine.connect() as connection:
            assert connection.execute(
                text(f"SELECT count(*) FROM {table_name}")  # noqa: S608
            ).scalar_one() == 0
        with pytest.raises(DBAPIError), guarded_control_engine.begin() as connection:
            connection.execute(text(f"DELETE FROM {table_name}"))  # noqa: S608
        with pytest.raises(DBAPIError), guarded_runtime_engine.connect() as connection:
            connection.execute(
                text(f"SELECT count(*) FROM {table_name}")  # noqa: S608
            ).scalar_one()

    with pytest.raises(DBAPIError), guarded_control_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.organization_id', :organization_b, true)"),
            {"organization_b": str(organization_b)},
        )
        connection.execute(
            text(
                """
                INSERT INTO context_source (
                    organization_id, source_id, display_name, source_kind,
                    registration_operation, idempotency_key,
                    registration_digest, active_version_id, created_at
                ) VALUES (
                    :organization_a, :source_id, 'Forbidden', 'file',
                    'register_source', 'forbidden-key', :digest,
                    :version_id, :created_at
                )
                """
            ),
            {
                "organization_a": organization_a,
                "source_id": uuid4(),
                "digest": "0" * 64,
                "version_id": uuid4(),
                "created_at": NOW,
            },
        )

    with guarded_control_engine.connect() as connection:
        connection.execute(
            text("SELECT set_config('app.organization_id', :organization_a, true)"),
            {"organization_a": str(organization_a)},
        )
        assert connection.execute(
            text(
                "SELECT count(*) FROM context_source "
                "WHERE organization_id = :organization_a"
            ),
            {"organization_a": organization_a},
        ).scalar_one() == 0


@pytest.mark.security_evidence(id="PG-FILE-SOURCE-FK-021", layer="postgres")
def test_source_version_is_immutable_and_active_pointer_stays_in_organization(
    guarded_control_engine: Engine,
    migration_configuration: DatabaseConfiguration,
    organizations: tuple[UUID, UUID],
) -> None:
    organization_a, organization_b = organizations
    control_a, authority_a = _control(guarded_control_engine, organization_a)
    control_b, authority_b = _control(guarded_control_engine, organization_b)
    source_a = _register(
        control_a,
        authority_a,
        organization_a,
        RegisterFileSource("A", FileRootRef("root-a"), "key-a"),
        request_id="register-a",
    )
    source_b = _register(
        control_b,
        authority_b,
        organization_b,
        RegisterFileSource("B", FileRootRef("root-b"), "key-b"),
        request_id="register-b",
    )

    with pytest.raises(DBAPIError), guarded_control_engine.begin() as connection:
        assert connection.execute(text("SELECT current_user")).scalar_one() == (
            "context_engine_control"
        )
        connection.execute(
            text("SELECT set_config('app.organization_id', :value, true)"),
            {"value": str(organization_a)},
        )
        connection.execute(
            text(
                """
                INSERT INTO source_version (
                    organization_id, source_id, version_id, source_kind,
                    root_ref, capability_manifest, created_at
                ) VALUES (
                    :organization_a, :source_b_id, :version_id, 'file',
                    'cross-organization-root', CAST(:capabilities AS jsonb),
                    :created_at
                )
                """
            ),
            {
                "organization_a": organization_a,
                "source_b_id": source_b.source_ref.value,
                "version_id": uuid4(),
                "capabilities": json.dumps(
                    FILE_CAPABILITY_MANIFEST.document(),
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                "created_at": NOW,
            },
        )

    with pytest.raises(DBAPIError), guarded_control_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.organization_id', :value, true)"),
            {"value": str(organization_a)},
        )
        connection.execute(
            text(
                """
                INSERT INTO context_source (
                    organization_id, source_id, display_name, source_kind,
                    registration_operation, idempotency_key,
                    registration_digest, active_version_id, created_at
                ) VALUES (
                    :organization_a, :source_id, 'Broken pointer', 'file',
                    'register_source', :idempotency_key, :digest,
                    :active_version_id, :created_at
                )
                """
            ),
            {
                "organization_a": organization_a,
                "source_id": uuid4(),
                "idempotency_key": f"invalid-pointer-{uuid4().hex}",
                "digest": "0" * 64,
                "active_version_id": source_b.active_version.version_ref,
                "created_at": NOW,
            },
        )

    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            delete_action = connection.execute(
                text(
                    """
                    SELECT constraint_record.confdeltype
                    FROM pg_constraint AS constraint_record
                    WHERE constraint_record.conname =
                        'fk_source_version_source_same_organization'
                    """
                )
            ).scalar_one()
        assert delete_action == "a"

        with pytest.raises(DBAPIError), engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE source_version SET root_ref = 'changed' "
                    "WHERE organization_id = :organization_id "
                    "AND source_id = :source_id"
                ),
                {
                    "organization_id": organization_a,
                    "source_id": source_a.source_ref.value,
                },
            )
    finally:
        engine.dispose()


def test_source_registration_retry_matrix_is_atomic_under_concurrency(
    guarded_control_engine: Engine,
    migration_configuration: DatabaseConfiguration,
    organizations: tuple[UUID, UUID],
) -> None:
    organization_a, _ = organizations
    command = RegisterFileSource(
        "Concurrent handbook",
        FileRootRef("concurrent-handbook"),
        "concurrent-handbook-v1",
    )

    def register(request_index: int) -> SourceManifest:
        control, authority = _control(guarded_control_engine, organization_a)
        return _register(
            control,
            authority,
            organization_a,
            command,
            request_id=f"concurrent-register-{request_index}",
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(register, range(8)))

    assert len({manifest.source_ref for manifest in results}) == 1
    assert len(
        {manifest.active_version.version_ref for manifest in results}
    ) == 1

    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT count(*) FROM context_source "
                    "WHERE organization_id = :organization_id "
                    "AND idempotency_key = :idempotency_key"
                ),
                {
                    "organization_id": organization_a,
                    "idempotency_key": command.idempotency_key,
                },
            ).scalar_one() == 1
            assert connection.execute(
                text(
                    "SELECT count(*) FROM source_version "
                    "WHERE organization_id = :organization_id "
                    "AND source_id = :source_id"
                ),
                {
                    "organization_id": organization_a,
                    "source_id": results[0].source_ref.value,
                },
            ).scalar_one() == 1
    finally:
        engine.dispose()
