from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from engine.control import (
    ContextControl,
    ControlOperation,
    ControlOperatorAuthority,
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
                {ControlOperation.REGISTER_SOURCE, ControlOperation.READ_SOURCE}
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
        root_ref="engineering-handbook",
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
                root_ref="different-root",
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
        RegisterFileSource("A", "root-a", "key-a"),
        request_id="register-a",
    )
    source_b = _register(
        control_b,
        authority_b,
        organization_b,
        RegisterFileSource("B", "root-b", "key-b"),
        request_id="register-b",
    )

    engine = create_database_engine(migration_configuration)
    try:
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
        with pytest.raises(DBAPIError), engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE context_source SET active_version_id = :version_id "
                    "WHERE organization_id = :organization_id "
                    "AND source_id = :source_id"
                ),
                {
                    "organization_id": organization_a,
                    "source_id": source_a.source_ref.value,
                    "version_id": source_b.active_version.version_ref,
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
        "concurrent-handbook",
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
