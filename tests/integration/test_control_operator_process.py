from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from applications.operator_authentication import (
    CONTROL_OPERATOR_OPERATIONS_ENV,
    CONTROL_OPERATOR_SECRET_ENV,
    DOGFOOD_SECRET_ENV,
    OPERATOR_ORGANIZATION_ENV,
    RELEASE_OPERATOR_SECRET_ENV,
    WORKER_SECRET_ENV,
)
from engine.control import (
    FILE_CHANGE_CAPABILITY_MANIFEST,
    FILE_DELETE_OBSERVATION_CAPABILITY_MANIFEST,
)
from engine.persistence import (
    DatabaseConfiguration,
    create_database_engine,
)

pytestmark = pytest.mark.integration
ROOT = Path(__file__).parents[2]
CONTROL_SECRET = "issue-111-control-operator-secret-0001"
RELEASE_SECRET = "issue-111-release-operator-secret-0001"
DOGFOOD_SECRET = "issue-111-dogfood-runtime-secret-0001"
WORKER_SECRET = "ab" * 32


@pytest.fixture
def operator_organizations(
    migration_configuration: DatabaseConfiguration,
) -> Iterator[tuple[UUID, UUID]]:
    organization_id = uuid4()
    other_organization_id = uuid4()
    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO organization (organization_id) "
                    "VALUES (:organization_id), (:other_organization_id)"
                ),
                {
                    "organization_id": organization_id,
                    "other_organization_id": other_organization_id,
                },
            )
        yield organization_id, other_organization_id
    finally:
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE source_version "
                        "DISABLE TRIGGER source_version_immutable"
                    )
                )
                for table_name in ("context_source", "source_version"):
                    connection.execute(
                        text(
                            f"DELETE FROM {table_name} "  # noqa: S608 - fixed list
                            "WHERE organization_id IN "
                            "(:organization_id, :other_organization_id)"
                        ),
                        {
                            "organization_id": organization_id,
                            "other_organization_id": other_organization_id,
                        },
                    )
                connection.execute(
                    text(
                        "DELETE FROM organization WHERE organization_id IN "
                        "(:organization_id, :other_organization_id)"
                    ),
                    {
                        "organization_id": organization_id,
                        "other_organization_id": other_organization_id,
                    },
                )
                connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
                connection.execute(
                    text(
                        "ALTER TABLE source_version "
                        "ENABLE TRIGGER source_version_immutable"
                    )
                )
        finally:
            engine.dispose()


def _operator_environment(organization_id: UUID) -> dict[str, str]:
    return {
        **os.environ,
        OPERATOR_ORGANIZATION_ENV: str(organization_id),
        CONTROL_OPERATOR_SECRET_ENV: CONTROL_SECRET,
        RELEASE_OPERATOR_SECRET_ENV: RELEASE_SECRET,
        DOGFOOD_SECRET_ENV: DOGFOOD_SECRET,
        WORKER_SECRET_ENV: WORKER_SECRET,
        CONTROL_OPERATOR_OPERATIONS_ENV: (
            "register_source,read_source,activate_file_change_feed,"
            "activate_file_delete_observations"
        ),
    }


def _run(
    arguments: list[str],
    *,
    environment: dict[str, str],
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["context-engine-control", *arguments],
        cwd=ROOT,
        env=environment,
        check=check,
        capture_output=True,
        text=True,
    )


def test_control_process_registers_reads_and_activates_one_file_source(
    migration_configuration: DatabaseConfiguration,
    operator_organizations: tuple[UUID, UUID],
) -> None:
    organization_id, _ = operator_organizations
    environment = _operator_environment(organization_id)
    register_arguments = [
        "register-file-source",
        "--organization-id",
        str(organization_id),
        "--display-name",
        "Maintainer notes",
        "--root-ref",
        "maintainer-notes",
        "--idempotency-key",
        "maintainer-notes-v1",
    ]

    registered = _run(register_arguments, environment=environment)
    retried = _run(register_arguments, environment=environment)
    registered_manifest = json.loads(registered.stdout)
    assert retried.stdout == registered.stdout
    assert registered.stderr == retried.stderr == ""
    assert registered_manifest["displayName"] == "Maintainer notes"
    assert registered_manifest["activeVersion"]["rootRef"] == "maintainer-notes"
    source_ref = UUID(registered_manifest["sourceRef"])

    distinct_registration = _run(
        [*register_arguments[:-1], "maintainer-notes-v2"],
        environment=environment,
    )
    assert UUID(json.loads(distinct_registration.stdout)["sourceRef"]) != source_ref

    read_arguments = [
        "read-source",
        "--organization-id",
        str(organization_id),
        "--source-ref",
        str(source_ref),
    ]
    read_registered = _run(read_arguments, environment=environment)
    assert read_registered.stdout == registered.stdout

    change_feed = _run(
        [
            "activate-change-feed",
            "--organization-id",
            str(organization_id),
            "--source-ref",
            str(source_ref),
        ],
        environment=environment,
    )
    change_manifest = json.loads(change_feed.stdout)
    assert change_manifest["activeVersion"]["capabilities"] == (
        FILE_CHANGE_CAPABILITY_MANIFEST.document()
    )

    delete_observations = _run(
        [
            "activate-delete-observations",
            "--organization-id",
            str(organization_id),
            "--source-ref",
            str(source_ref),
        ],
        environment=environment,
    )
    delete_manifest = json.loads(delete_observations.stdout)
    assert delete_manifest["activeVersion"]["capabilities"] == (
        FILE_DELETE_OBSERVATION_CAPABILITY_MANIFEST.document()
    )
    assert json.loads(_run(read_arguments, environment=environment).stdout) == (
        delete_manifest
    )

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            durable_counts = tuple(
                connection.execute(
                    text(
                        "SELECT "
                        "(SELECT count(*) FROM context_source "
                        " WHERE organization_id = :organization_id), "
                        "(SELECT count(*) FROM source_version "
                        " WHERE organization_id = :organization_id), "
                        "(SELECT count(*) FROM file_acquisition "
                        " WHERE organization_id = :organization_id), "
                        "(SELECT count(*) FROM file_import_job "
                        " WHERE organization_id = :organization_id), "
                        "(SELECT count(*) FROM "
                        " file_source_acquisition_checkpoint "
                        " WHERE organization_id = :organization_id)"
                    ),
                    {"organization_id": organization_id},
                ).one()
            )
        assert durable_counts == (2, 4, 0, 0, 0)
    finally:
        migration_engine.dispose()


def test_control_subcommands_refuse_absent_configuration_and_wrong_organization(
    migration_configuration: DatabaseConfiguration,
    operator_organizations: tuple[UUID, UUID],
) -> None:
    organization_id, other_organization_id = operator_organizations
    configured = _operator_environment(organization_id)
    wrong_organization = _run(
        [
            "register-file-source",
            "--organization-id",
            str(other_organization_id),
            "--display-name",
            "Must remain hidden",
            "--root-ref",
            "wrong-organization-root",
            "--idempotency-key",
            "wrong-organization-key",
        ],
        environment=configured,
        check=False,
    )
    assert wrong_organization.returncode != 0
    assert wrong_organization.stdout == ""
    assert wrong_organization.stderr == "context-engine-control: operation refused\n"
    rendered = wrong_organization.stdout + wrong_organization.stderr
    assert str(organization_id) not in rendered
    assert str(other_organization_id) not in rendered
    assert "wrong-organization" not in rendered
    assert "register" not in rendered

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            counts = connection.execute(
                text(
                    "SELECT "
                    "(SELECT count(*) FROM context_source "
                    " WHERE organization_id IN "
                    " (:organization_id, :other_organization_id)), "
                    "(SELECT count(*) FROM source_version "
                    " WHERE organization_id IN "
                    " (:organization_id, :other_organization_id))"
                ),
                {
                    "organization_id": organization_id,
                    "other_organization_id": other_organization_id,
                },
            ).one()
        assert tuple(counts) == (0, 0)
    finally:
        migration_engine.dispose()

    absent = configured.copy()
    for name in (
        OPERATOR_ORGANIZATION_ENV,
        CONTROL_OPERATOR_SECRET_ENV,
        RELEASE_OPERATOR_SECRET_ENV,
        DOGFOOD_SECRET_ENV,
        WORKER_SECRET_ENV,
        CONTROL_OPERATOR_OPERATIONS_ENV,
    ):
        del absent[name]
    source_ref = str(uuid4())
    commands = (
        [
            "register-file-source",
            "--organization-id",
            str(organization_id),
            "--display-name",
            "Absent",
            "--root-ref",
            "absent-root",
            "--idempotency-key",
            "absent-key",
        ],
        [
            "read-source",
            "--organization-id",
            str(organization_id),
            "--source-ref",
            source_ref,
        ],
        [
            "activate-change-feed",
            "--organization-id",
            str(organization_id),
            "--source-ref",
            source_ref,
        ],
        [
            "activate-delete-observations",
            "--organization-id",
            str(organization_id),
            "--source-ref",
            source_ref,
        ],
    )
    for arguments in commands:
        refused = _run(arguments, environment=absent, check=False)
        assert refused.returncode != 0
        assert refused.stdout == ""
        assert refused.stderr == "context-engine-control: operation refused\n"
