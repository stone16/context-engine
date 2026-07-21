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


def test_manifest_classifies_the_exact_issue_13_content_schema() -> None:
    """PROP-TENANT-OWNERSHIP-001: no current table is left unclassified."""

    document = manifest()
    tables = table_entries(document)

    assert document["manifestVersion"] == "3.0.0"
    assert set(tables) == {
        "alembic_version",
        "context_fragment",
        "context_resource",
        "context_revision",
        "membership",
        "organization",
        "organization_record",
        "user_account",
    }
    assert tables["alembic_version"]["classification"] == "global"
    assert tables["organization"]["classification"] == "global"
    assert tables["user_account"]["classification"] == "global"
    assert tables["membership"]["classification"] == "tenant_owned"
    assert tables["organization_record"]["classification"] == "tenant_owned"
    assert tables["context_resource"]["classification"] == "tenant_owned"
    assert tables["context_revision"]["classification"] == "tenant_owned"
    assert tables["context_fragment"]["classification"] == "tenant_owned"


def test_membership_manifest_requires_exact_user_actor_and_read_only_runtime() -> None:
    """DB-009/DB-010: the identity row is not an Organization-only grant."""

    entry = table_entries(manifest())["membership"]
    assert entry["organizationColumn"] == "organization_id"
    assert entry["organizationInclusiveKeys"] == [
        {
            "name": "pk_membership",
            "kind": "primary_key",
            "columns": ["organization_id", "membership_id"],
        },
        {
            "name": "uq_membership_organization_user",
            "kind": "unique",
            "columns": ["organization_id", "user_id"],
        },
    ]
    assert entry["permittedOperations"] == {
        "context_engine_runtime": ["SELECT"],
        "context_engine_worker": [],
    }

    rls = entry["rowLevelSecurity"]
    assert rls["enabled"] is True
    assert rls["forced"] is True
    runtime_policy = next(
        policy
        for policy in rls["policies"]
        if policy["roles"] == ["context_engine_runtime"]
    )
    assert runtime_policy["command"] == "SELECT"
    expression = runtime_policy["using"]
    for setting_name in (
        "app.organization_id",
        "app.actor_kind",
        "app.user_id",
        "app.membership_id",
        "app.membership_version",
        "app.principal_ref",
        "app.request_id",
        "app.authentication_binding_ref",
        "app.checked_at",
    ):
        assert setting_name in expression
    for membership_property in (
        "status",
        "membership_version",
        "valid_from",
        "valid_until",
    ):
        assert membership_property in expression


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
    assert len(rls["policies"]) == 2
    policy = next(
        candidate
        for candidate in rls["policies"]
        if candidate["roles"] == ["context_engine_runtime"]
    )
    assert policy["roles"] == ["context_engine_runtime"]
    assert policy["using"] == policy["withCheck"]
    assert "app.organization_id" in policy["using"]
    assert "app.membership_id" in policy["using"]
    assert "app.membership_version" in policy["using"]
    assert "app.checked_at" in policy["using"]
    assert "EXISTS" in policy["using"]
    assert "NULLIF" in policy["using"]

    assert rls["writeContextGuard"] == {
        "trigger": "organization_record_write_context_guard",
        "function": "organization_record_require_write_context",
        "timing": "BEFORE",
        "orientation": "STATEMENT",
        "events": ["INSERT", "UPDATE", "DELETE"],
        "missingContextSqlstate": "42501",
        "requiredActorKind": "user",
        "requiresCurrentMembership": True,
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
        "DB-009",
        "DB-010",
        "MIG-001",
        "MIG-002",
    }


def test_content_manifest_preserves_lineage_visibility_and_immutability() -> None:
    entries = table_entries(manifest())
    resource = entries["context_resource"]
    revision = entries["context_revision"]
    fragment = entries["context_fragment"]

    assert resource["organizationInclusiveKeys"] == [
        {
            "name": "pk_context_resource",
            "kind": "primary_key",
            "columns": ["organization_id", "resource_ref"],
        }
    ]
    assert revision["organizationInclusiveKeys"] == [
        {
            "name": "pk_context_revision",
            "kind": "primary_key",
            "columns": ["organization_id", "resource_ref", "revision_id"],
        }
    ]
    assert fragment["organizationInclusiveKeys"] == [
        {
            "name": "pk_context_fragment",
            "kind": "primary_key",
            "columns": [
                "organization_id",
                "resource_ref",
                "revision_id",
                "fragment_ref",
            ],
        },
        {
            "name": "uq_context_fragment_revision_ordinal",
            "kind": "unique",
            "columns": [
                "organization_id",
                "resource_ref",
                "revision_id",
                "ordinal",
            ],
        },
    ]

    resource_foreign_keys = {
        foreign_key["name"]: foreign_key for foreign_key in resource["foreignKeys"]
    }
    active_pointer = resource_foreign_keys[
        "fk_context_resource_active_revision_same_organization"
    ]
    assert active_pointer["columns"] == [
        "organization_id",
        "resource_ref",
        "active_revision_id",
    ]
    assert active_pointer["references"] == {
        "table": "context_revision",
        "columns": ["organization_id", "resource_ref", "revision_id"],
    }
    assert active_pointer["deferrable"] is True
    assert active_pointer["initially"] == "DEFERRED"

    revision_parent = next(
        foreign_key
        for foreign_key in revision["foreignKeys"]
        if foreign_key["name"] == "fk_context_revision_resource_same_organization"
    )
    assert revision_parent["columns"] == ["organization_id", "resource_ref"]
    assert revision_parent["references"] == {
        "table": "context_resource",
        "columns": ["organization_id", "resource_ref"],
    }
    fragment_parent = next(
        foreign_key
        for foreign_key in fragment["foreignKeys"]
        if foreign_key["name"] == "fk_context_fragment_revision_same_organization"
    )
    assert fragment_parent["columns"] == [
        "organization_id",
        "resource_ref",
        "revision_id",
    ]
    assert fragment_parent["references"] == {
        "table": "context_revision",
        "columns": ["organization_id", "resource_ref", "revision_id"],
    }
    assert fragment["checkConstraints"] == [
        {
            "name": "ck_context_fragment_ordinal_nonnegative",
            "expression": "ordinal >= 0",
        },
        {
            "name": "ck_context_fragment_content_nonblank",
            "expression": (
                "translate(content, "
                "U&'\\0009\\000A\\000B\\000C\\000D\\001C\\001D\\001E\\001F"
                "\\0020\\0085\\00A0\\1680\\2000\\2001\\2002\\2003\\2004"
                "\\2005\\2006\\2007\\2008\\2009\\200A\\2028\\2029\\202F"
                "\\205F\\3000', '') <> ''"
            ),
        },
    ]

    for entry in (resource, revision, fragment):
        assert entry["organizationColumn"] == "organization_id"
        assert entry["permittedOperations"] == {
            "context_engine_runtime": ["SELECT"],
            "context_engine_worker": [],
        }
        rls = entry["rowLevelSecurity"]
        assert rls["enabled"] is True
        assert rls["forced"] is True
        runtime_policy = next(
            policy
            for policy in rls["policies"]
            if policy["roles"] == ["context_engine_runtime"]
        )
        assert runtime_policy["command"] == "SELECT"
        assert "app.organization_id" in runtime_policy["using"]
        assert "app.membership_id" in runtime_policy["using"]
        assert "tombstoned" in runtime_policy["using"]
        migrator_policy = next(
            policy
            for policy in rls["policies"]
            if policy["roles"] == ["context_engine_migrator"]
        )
        assert migrator_policy == {
            "name": f"{entry['name']}_migrator_administration",
            "command": "ALL",
            "roles": ["context_engine_migrator"],
            "using": "true",
            "withCheck": "true",
        }

    for entry in (revision, fragment):
        expression = next(
            policy["using"]
            for policy in entry["rowLevelSecurity"]["policies"]
            if policy["roles"] == ["context_engine_runtime"]
        )
        assert "active_revision_id" in expression
        assert "tombstoned" in expression
        assert entry["immutableRows"] == {
            "trigger": f"{entry['name']}_reject_mutation",
            "function": "context_content_reject_mutation",
            "events": ["UPDATE", "DELETE"],
            "sqlstate": "55000",
        }
