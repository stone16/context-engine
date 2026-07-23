from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import Engine, text

from engine.persistence import DatabaseConfiguration, create_database_engine
from scripts.security_gate.rls import (
    NON_OWNER_EVIDENCE_BY_TABLE,
    audit_live_rls,
    audit_rls_snapshot,
    snapshot_public_schema,
)

ROOT = Path(__file__).parents[2]
MANIFEST_PATH = ROOT / "engine/persistence/schema_security_manifest.yaml"
BEHAVIORAL_EVIDENCE_IDS = set(NON_OWNER_EVIDENCE_BY_TABLE.values())


def _manifest() -> dict[str, object]:
    document = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return cast(dict[str, object], document)


@pytest.mark.security_evidence(id="PG-RLS-ALL-TENANT-TABLES", layer="postgres")
def test_all_manifest_tenant_tables_pass_live_non_owner_rls_audit(
    guarded_runtime_engine: Engine,
) -> None:
    """PG-RLS-ALL-TENANT-TABLES: the live denominator is exactly 40/40."""

    with guarded_runtime_engine.connect() as connection:
        report = audit_live_rls(
            connection,
            _manifest(),
            passed_evidence_ids=BEHAVIORAL_EVIDENCE_IDS,
        )

    assert report["passed"] is True
    assert report["denominator"] == {
        "allTables": 43,
        "tenantOwned": 40,
        "global": 3,
    }
    assert report["coverage"] == {
        "numerator": 40,
        "denominator": 40,
        "percent": 100.0,
    }
    assert report["failures"] == []


def test_no_force_row_level_security_mutation_fails_and_rolls_back(
    guarded_runtime_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Issue #20 mutation: removing FORCE is a hard failure without residue."""

    with guarded_runtime_engine.connect() as runtime_connection:
        baseline = audit_live_rls(
            runtime_connection,
            _manifest(),
            passed_evidence_ids=BEHAVIORAL_EVIDENCE_IDS,
        )
        runtime_snapshot = snapshot_public_schema(runtime_connection)
    assert baseline["passed"] is True

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as migration_connection:
            transaction = migration_connection.begin()
            try:
                migration_connection.execute(
                    text("ALTER TABLE organization_record NO FORCE ROW LEVEL SECURITY")
                )
                mutated_snapshot = snapshot_public_schema(migration_connection)
                mutated_snapshot["currentRole"] = runtime_snapshot["currentRole"]
                mutated = audit_rls_snapshot(
                    manifest=_manifest(),
                    snapshot=mutated_snapshot,
                    passed_evidence_ids=BEHAVIORAL_EVIDENCE_IDS,
                )

                assert mutated["passed"] is False
                assert mutated["coverage"] == {
                    "numerator": 39,
                    "denominator": 40,
                    "percent": 97.5,
                }
                tenant_tables = cast(list[dict[str, Any]], mutated["tenantTables"])
                organization_record = next(
                    table
                    for table in tenant_tables
                    if table["table"] == "organization_record"
                )
                assert organization_record["rlsForced"] is False
                assert organization_record["passed"] is False
            finally:
                transaction.rollback()
    finally:
        migration_engine.dispose()

    with guarded_runtime_engine.connect() as runtime_connection:
        restored = audit_live_rls(
            runtime_connection,
            _manifest(),
            passed_evidence_ids=BEHAVIORAL_EVIDENCE_IDS,
        )
    assert restored["passed"] is True
    assert restored["coverage"] == {
        "numerator": 40,
        "denominator": 40,
        "percent": 100.0,
    }


def test_transactional_permissive_allow_all_policy_mutation_fails_and_rolls_back(
    guarded_runtime_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    """An added permissive policy invalidates exact inventory and isolation."""

    with guarded_runtime_engine.connect() as runtime_connection:
        baseline = audit_live_rls(
            runtime_connection,
            _manifest(),
            passed_evidence_ids=BEHAVIORAL_EVIDENCE_IDS,
        )
        runtime_snapshot = snapshot_public_schema(runtime_connection)
    assert baseline["passed"] is True

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as migration_connection:
            transaction = migration_connection.begin()
            try:
                migration_connection.execute(
                    text(
                        "CREATE POLICY organization_record_allow_all_mutation "
                        "ON organization_record AS PERMISSIVE FOR ALL "
                        "TO context_engine_runtime USING (true) WITH CHECK (true)"
                    )
                )
                mutated_snapshot = snapshot_public_schema(migration_connection)
                mutated_snapshot["currentRole"] = runtime_snapshot["currentRole"]
                mutated = audit_rls_snapshot(
                    manifest=_manifest(),
                    snapshot=mutated_snapshot,
                    passed_evidence_ids=BEHAVIORAL_EVIDENCE_IDS,
                )

                assert mutated["passed"] is False
                organization_record = next(
                    table
                    for table in cast(list[dict[str, Any]], mutated["tenantTables"])
                    if table["table"] == "organization_record"
                )
                assert (
                    organization_record["policies"]["inventoryMatchesDeclared"] is False
                )
                assert organization_record["policies"]["passed"] is False
                assert (
                    "organization_record_allow_all_mutation"
                    in (organization_record["policies"]["live"])
                )
            finally:
                transaction.rollback()
    finally:
        migration_engine.dispose()

    with guarded_runtime_engine.connect() as runtime_connection:
        restored = audit_live_rls(
            runtime_connection,
            _manifest(),
            passed_evidence_ids=BEHAVIORAL_EVIDENCE_IDS,
        )
    assert restored["passed"] is True


def test_transactional_policy_replacement_mutation_fails_and_rolls_back(
    guarded_runtime_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Keeping a policy name cannot hide an allow-all semantic replacement."""

    with guarded_runtime_engine.connect() as runtime_connection:
        runtime_snapshot = snapshot_public_schema(runtime_connection)

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as migration_connection:
            transaction = migration_connection.begin()
            try:
                migration_connection.execute(
                    text(
                        "DROP POLICY organization_record_organization_isolation "
                        "ON organization_record"
                    )
                )
                migration_connection.execute(
                    text(
                        "CREATE POLICY organization_record_organization_isolation "
                        "ON organization_record AS PERMISSIVE FOR ALL "
                        "TO context_engine_runtime USING (true) WITH CHECK (true)"
                    )
                )
                mutated_snapshot = snapshot_public_schema(migration_connection)
                mutated_snapshot["currentRole"] = runtime_snapshot["currentRole"]
                mutated = audit_rls_snapshot(
                    manifest=_manifest(),
                    snapshot=mutated_snapshot,
                    passed_evidence_ids=BEHAVIORAL_EVIDENCE_IDS,
                )

                assert mutated["passed"] is False
                organization_record = next(
                    table
                    for table in cast(list[dict[str, Any]], mutated["tenantTables"])
                    if table["table"] == "organization_record"
                )
                policies = organization_record["policies"]
                assert policies["inventoryMatchesDeclared"] is True
                assert policies["semanticsMatchDeclared"] is False
                assert policies["expectedDigest"] != policies["observedDigest"]
            finally:
                transaction.rollback()
    finally:
        migration_engine.dispose()

    with guarded_runtime_engine.connect() as runtime_connection:
        restored = audit_live_rls(
            runtime_connection,
            _manifest(),
            passed_evidence_ids=BEHAVIORAL_EVIDENCE_IDS,
        )
    assert restored["passed"] is True
