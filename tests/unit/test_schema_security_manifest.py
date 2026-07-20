from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).parents[2]
MANIFEST_PATH = ROOT / "engine/persistence/schema_security_manifest.yaml"


def manifest() -> dict[str, Any]:
    document = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return cast(dict[str, Any], document)


def table_entries(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw_tables = document["tables"]
    assert isinstance(raw_tables, list)
    assert all(isinstance(table, dict) for table in raw_tables)
    return {
        cast(str, table["name"]): cast(dict[str, Any], table) for table in raw_tables
    }


def test_manifest_classifies_the_exact_bounded_issue_8_schema() -> None:
    """PROP-TENANT-OWNERSHIP-001: no current table is left unclassified."""

    document = manifest()
    tables = table_entries(document)

    assert document["manifestVersion"] == "1.0.0"
    assert set(tables) == {
        "alembic_version",
        "organization",
        "organization_record",
    }
    assert tables["alembic_version"]["classification"] == "global"
    assert tables["organization"]["classification"] == "global"
    assert tables["organization_record"]["classification"] == "tenant_owned"


def test_tenant_owned_manifest_entry_preserves_every_security_property() -> None:
    """PROP-TENANT-FK-002/PROP-RLS-FAIL-CLOSED-003 structural properties."""

    entry = table_entries(manifest())["organization_record"]
    organization_column = entry["organizationColumn"]
    assert organization_column == "organization_id"

    keys = entry["organizationInclusiveKeys"]
    assert isinstance(keys, list) and keys
    assert all(key["columns"][0] == organization_column for key in keys)

    foreign_keys = {
        foreign_key["name"]: foreign_key for foreign_key in entry["foreignKeys"]
    }
    assert foreign_keys["fk_organization_record_organization"]["columns"] == [
        organization_column
    ]
    same_org_fk = foreign_keys["fk_organization_record_parent_same_organization"]
    assert same_org_fk["columns"][0] == organization_column
    assert same_org_fk["references"]["columns"][0] == organization_column

    rls = entry["rowLevelSecurity"]
    assert rls["enabled"] is True
    assert rls["forced"] is True
    assert len(rls["policies"]) == 1
    policy = rls["policies"][0]
    assert policy["roles"] == ["context_engine_runtime"]
    assert policy["using"] == policy["withCheck"]
    assert "app.organization_id" in policy["using"]
    assert "NULLIF" in policy["using"]

    assert rls["writeContextGuard"] == {
        "trigger": "organization_record_write_context_guard",
        "function": "organization_record_require_write_context",
        "timing": "BEFORE",
        "orientation": "STATEMENT",
        "events": ["INSERT", "UPDATE", "DELETE"],
        "missingContextSqlstate": "42501",
    }

    assert entry["permittedOperations"] == {
        "context_engine_runtime": ["SELECT", "INSERT", "UPDATE", "DELETE"],
        "context_engine_worker": [],
    }
    assert set(entry["securityInvariantIds"]) == {
        "TENANT-OWNERSHIP-001",
        "TENANT-FK-002",
        "RLS-FAIL-CLOSED-003",
    }
    assert set(entry["negativeTestIds"]) == {
        "DB-001",
        "DB-002",
        "DB-003",
        "DB-004",
        "DB-006",
        "DB-007",
        "DB-008",
        "MIG-001",
        "MIG-002",
    }
