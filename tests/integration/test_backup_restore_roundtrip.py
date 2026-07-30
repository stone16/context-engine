from __future__ import annotations

import hashlib
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine, text

from engine.persistence import DatabaseConfiguration, create_database_engine
from scripts.daily_driver.backup import create_database_backup, restore_database_backup

pytestmark = pytest.mark.integration

ORGANIZATION_ID = UUID("14900000-0000-4000-8000-000000000001")
FIRST_RECORD_ID = UUID("14900000-0000-4000-8000-000000000002")
SECOND_RECORD_ID = UUID("14900000-0000-4000-8000-000000000003")
SCRATCH_DATABASE = "context_engine_restore_149"
ROOT = Path(__file__).resolve().parents[2]


def _digest(rows: list[tuple[str, str]]) -> str:
    payload = "\n".join(f"{record_id}:{value}" for record_id, value in rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _representative_rows(engine: Engine) -> list[tuple[str, str]]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT record_id::text, payload "
                "FROM organization_record "
                "WHERE organization_id = :organization_id "
                "ORDER BY record_id"
            ),
            {"organization_id": ORGANIZATION_ID},
        ).tuples()
        return [(record_id, payload) for record_id, payload in rows]


def test_pg_dump_restores_representative_rows_into_a_fresh_database(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
) -> None:
    source_engine = create_database_engine(migration_configuration)
    with source_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organization (organization_id) VALUES (:organization_id) "
                "ON CONFLICT DO NOTHING"
            ),
            {"organization_id": ORGANIZATION_ID},
        )
        connection.execute(
            text(
                "DELETE FROM organization_record "
                "WHERE organization_id = :organization_id"
            ),
            {"organization_id": ORGANIZATION_ID},
        )
        connection.execute(
            text(
                "INSERT INTO organization_record "
                "(organization_id, record_id, parent_record_id, payload) VALUES "
                "(:organization_id, :first_record_id, NULL, :first_payload), "
                "(:organization_id, :second_record_id, :first_record_id, "
                ":second_payload)"
            ),
            {
                "organization_id": ORGANIZATION_ID,
                "first_record_id": FIRST_RECORD_ID,
                "second_record_id": SECOND_RECORD_ID,
                "first_payload": "daily-driver-backup-parent",
                "second_payload": "daily-driver-backup-child",
            },
        )
    expected = _representative_rows(source_engine)

    backup_root = tmp_path / "database-backups"
    backup_root.mkdir()
    outcome = create_database_backup(
        checkout=ROOT,
        backup_root=backup_root,
        recorded_at=datetime(2026, 7, 30, 20, 0, tzinfo=UTC),
    )
    restore_database_backup(
        checkout=ROOT,
        dump_path=outcome.path,
        scratch_database=SCRATCH_DATABASE,
    )

    scratch_url = migration_configuration.url.set(database=SCRATCH_DATABASE)
    scratch_engine = create_database_engine(
        DatabaseConfiguration(
            purpose=migration_configuration.purpose,
            url=scratch_url,
            expected_role=migration_configuration.expected_role,
        )
    )
    try:
        restored = _representative_rows(scratch_engine)
        assert len(restored) == 2
        assert _digest(restored) == _digest(expected)
    finally:
        scratch_engine.dispose()
        source_engine.dispose()
        subprocess.run(
            (
                "docker",
                "compose",
                "--env-file",
                str(ROOT / ".context-engine" / "database.env"),
                "--project-name",
                _compose_project(),
                "exec",
                "-T",
                "postgres",
                "dropdb",
                "--if-exists",
                "--force",
                "--username",
                _database_value("POSTGRES_USER"),
                SCRATCH_DATABASE,
            ),
            cwd=ROOT,
            check=True,
        )


def _database_value(name: str) -> str:
    for line in (ROOT / ".context-engine" / "database.env").read_text(
        encoding="utf-8"
    ).splitlines():
        key, separator, value = line.partition("=")
        if key == name and separator:
            return value
    raise AssertionError(f"missing harness value: {name}")


def _compose_project() -> str:
    return _database_value("CONTEXT_ENGINE_COMPOSE_PROJECT")
