from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from scripts.security_gate.rls import (
    NON_OWNER_EVIDENCE_BY_TABLE,
    audit_rls_snapshot,
)

ROOT = Path(__file__).parents[2]
MANIFEST_PATH = ROOT / "engine/persistence/schema_security_manifest.yaml"

GLOBAL_TABLES = {"alembic_version", "organization", "user_account"}
TENANT_TABLES = {
    "active_release_manifest",
    "context_fragment",
    "context_fragment_field",
    "context_resource",
    "context_revision",
    "context_run",
    "context_run_operator_read_ticket",
    "context_source",
    "decision_audit",
    "exact_phrase_candidate",
    "file_acquisition",
    "file_import_job",
    "file_revision_snapshot",
    "membership",
    "membership_resource_field_right",
    "organization_policy_epoch",
    "organization_record",
    "release_candidate",
    "release_evaluation",
    "release_manifest",
    "release_operator_grant",
    "release_promotion_audit",
    "revision_publication_event",
    "resource_access_policy",
    "service_principal",
    "source_version",
    "worker_noop_job",
}


def _manifest_tables() -> dict[str, dict[str, Any]]:
    document = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    raw_tables = document["tables"]
    assert isinstance(raw_tables, list)
    return {
        cast(str, table["name"]): cast(dict[str, Any], table)
        for table in raw_tables
    }


def _manifest() -> dict[str, Any]:
    document = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return cast(dict[str, Any], document)


def _matching_catalog_snapshot() -> dict[str, object]:
    tables: dict[str, object] = {}
    for name, entry in _manifest_tables().items():
        foreign_keys = [
            {
                "name": foreign_key["name"],
                "columns": foreign_key["columns"],
                "referencedTable": foreign_key["references"]["table"],
                "referencedColumns": foreign_key["references"]["columns"],
            }
            for foreign_key in entry.get("foreignKeys", [])
        ]
        rls = entry.get("rowLevelSecurity", {})
        tables[name] = {
            "columns": sorted(
                {
                    column
                    for foreign_key in foreign_keys
                    for column in foreign_key["columns"]
                }
            ),
            "owner": "context_engine_migrator",
            "rlsEnabled": rls.get("enabled", False),
            "rlsForced": rls.get("forced", False),
            "policies": [
                {
                    "name": policy["name"],
                    "permissive": True,
                    "command": policy["command"],
                    "roles": policy["roles"],
                    "using": policy.get("using"),
                    "withCheck": policy.get("withCheck"),
                }
                for policy in rls.get("policies", [])
            ],
            "foreignKeys": foreign_keys,
        }
    return {
        "schema": "public",
        "currentRole": {
            "name": "context_engine_security_test",
            "superuser": False,
            "bypassesRls": False,
            "inherits": False,
        },
        "tables": tables,
    }


def test_manifest_declares_exact_live_table_denominator_and_rls_evidence() -> None:
    """Issue #20: every live table is classified and every tenant proof maps."""

    tables = _manifest_tables()
    global_tables = {
        name for name, entry in tables.items() if entry["classification"] == "global"
    }
    tenant_tables = {
        name
        for name, entry in tables.items()
        if entry["classification"] == "tenant_owned"
    }

    assert global_tables == GLOBAL_TABLES
    assert tenant_tables == TENANT_TABLES
    assert len(tables) == 30

    for name in sorted(GLOBAL_TABLES):
        rationale = tables[name]["classificationRationale"]
        assert isinstance(rationale, str)
        assert rationale.strip()

    for name in sorted(TENANT_TABLES):
        assert tables[name]["nonOwnerEvidence"] == {
            "evidenceId": NON_OWNER_EVIDENCE_BY_TABLE[name],
            "selector": {"table": name},
        }


def test_rls_auditor_requires_every_live_control_and_non_owner_evidence() -> None:
    report = audit_rls_snapshot(
        manifest=_manifest(),
        snapshot=_matching_catalog_snapshot(),
        passed_evidence_ids=set(NON_OWNER_EVIDENCE_BY_TABLE.values()),
    )

    assert report["passed"] is True
    assert report["coverage"] == {
        "numerator": 27,
        "denominator": 27,
        "percent": 100.0,
    }
    inventory = cast(dict[str, object], report["inventory"])
    global_allowlist = cast(list[dict[str, object]], report["globalAllowlist"])
    tenant_reports = cast(list[dict[str, object]], report["tenantTables"])
    assert inventory["exact"] is True
    assert len(global_allowlist) == 3
    assert all(table["passed"] for table in tenant_reports)


def test_rls_auditor_does_not_count_force_rls_or_evidence_gaps() -> None:
    snapshot = _matching_catalog_snapshot()
    tables = cast(dict[str, dict[str, Any]], snapshot["tables"])
    tables["organization_record"]["rlsForced"] = False

    report = audit_rls_snapshot(
        manifest=_manifest(),
        snapshot=snapshot,
        passed_evidence_ids=set(),
    )

    assert report["passed"] is False
    assert report["coverage"] == {
        "numerator": 0,
        "denominator": 27,
        "percent": 0.0,
    }
    tenant_reports = cast(list[dict[str, Any]], report["tenantTables"])
    organization_record = next(
        table
        for table in tenant_reports
        if table["table"] == "organization_record"
    )
    assert organization_record["rlsForced"] is False
    assert organization_record["nonOwnerIsolation"]["passed"] is False
    failures = cast(list[str], report["failures"])
    assert "organization_record: FORCE ROW LEVEL SECURITY is disabled" in failures


def test_rls_auditor_rejects_inventory_ownership_and_policy_mutations() -> None:
    mutations: list[tuple[str, str]] = [
        ("missing_inventory", "live public tables missing from manifest"),
        ("missing_ownership", "no verified Organization-inclusive ownership path"),
        ("disabled_rls", "row level security is disabled"),
        ("empty_policy", "live RLS policy inventory differs from manifest"),
    ]

    for mutation, expected_failure in mutations:
        manifest = _manifest()
        snapshot = _matching_catalog_snapshot()
        if mutation == "missing_inventory":
            manifest_tables = cast(list[dict[str, Any]], manifest["tables"])
            manifest["tables"] = [
                table
                for table in manifest_tables
                if table["name"] != "organization_record"
            ]
        else:
            live_tables = cast(dict[str, dict[str, Any]], snapshot["tables"])
            organization_record = live_tables["organization_record"]
            if mutation == "missing_ownership":
                organization_record["foreignKeys"] = []
            elif mutation == "disabled_rls":
                organization_record["rlsEnabled"] = False
            elif mutation == "empty_policy":
                organization_record["policies"] = []

        report = audit_rls_snapshot(
            manifest=manifest,
            snapshot=snapshot,
            passed_evidence_ids=set(NON_OWNER_EVIDENCE_BY_TABLE.values()),
        )

        failures = cast(list[str], report["failures"])
        assert report["passed"] is False
        assert any(expected_failure in failure for failure in failures)


def test_rls_auditor_pins_global_allowlist_independently_of_manifest() -> None:
    manifest = _manifest()
    manifest_tables = cast(list[dict[str, Any]], manifest["tables"])
    organization_record = next(
        table for table in manifest_tables if table["name"] == "organization_record"
    )
    organization_record["classification"] = "global"
    organization_record["classificationRationale"] = "mutated to global"

    report = audit_rls_snapshot(
        manifest=manifest,
        snapshot=_matching_catalog_snapshot(),
        passed_evidence_ids=set(NON_OWNER_EVIDENCE_BY_TABLE.values()),
    )

    assert report["passed"] is False
    assert "organization_record" in cast(
        dict[str, list[str]], report["globalAllowlistInventory"]
    )["unexpected"]
    assert any(
        "global table allowlist differs from the pinned M0 allowlist" in failure
        for failure in cast(list[str], report["failures"])
    )


def test_rls_auditor_requires_exact_policy_inventory_and_semantics() -> None:
    mutations: list[tuple[str, str]] = [
        ("extra", "live RLS policy inventory differs from manifest"),
        ("permissive", "live RLS policy semantics differ from manifest"),
        ("command", "live RLS policy semantics differ from manifest"),
        ("roles", "live RLS policy semantics differ from manifest"),
        ("using", "live RLS policy semantics differ from manifest"),
        ("with_check", "live RLS policy semantics differ from manifest"),
    ]
    for mutation, expected_failure in mutations:
        snapshot = _matching_catalog_snapshot()
        live_tables = cast(dict[str, dict[str, Any]], snapshot["tables"])
        policies = cast(
            list[dict[str, Any]], live_tables["organization_record"]["policies"]
        )
        runtime_policy = next(
            policy
            for policy in policies
            if policy["name"] == "organization_record_organization_isolation"
        )
        if mutation == "extra":
            policies.append(
                {
                    "name": "organization_record_allow_all",
                    "permissive": True,
                    "command": "ALL",
                    "roles": ["context_engine_runtime"],
                    "using": "true",
                    "withCheck": "true",
                }
            )
        elif mutation == "permissive":
            runtime_policy["permissive"] = False
        elif mutation == "command":
            runtime_policy["command"] = "SELECT"
        elif mutation == "roles":
            runtime_policy["roles"] = ["public"]
        elif mutation == "using":
            runtime_policy["using"] = "true"
        elif mutation == "with_check":
            runtime_policy["withCheck"] = "true"

        report = audit_rls_snapshot(
            manifest=_manifest(),
            snapshot=snapshot,
            passed_evidence_ids=set(NON_OWNER_EVIDENCE_BY_TABLE.values()),
        )

        assert report["passed"] is False
        assert any(
            expected_failure in failure
            for failure in cast(list[str], report["failures"])
        )


def test_rls_auditor_accepts_postgresql_expression_rendering_only() -> None:
    snapshot = _matching_catalog_snapshot()
    live_tables = cast(dict[str, dict[str, Any]], snapshot["tables"])
    policies = cast(
        list[dict[str, Any]], live_tables["context_run"]["policies"]
    )
    reader_policy = next(
        policy
        for policy in policies
        if policy["name"] == "context_run_context_run_reader_definer_read"
    )
    using_expression = cast(str, reader_policy["using"])
    reader_policy["using"] = (
        "(("
        + using_expression.replace(
            " IN ('issue', 'read')",
            " = ANY (ARRAY['issue'::text, 'read'::text])",
        )
        .replace(
            "'app.context_run_operator_ticket_mode'",
            "'app.context_run_operator_ticket_mode'::text",
        )
        .replace(
            "'app.context_run_operator_ticket_decision_ref'",
            "'app.context_run_operator_ticket_decision_ref'::text",
        )
        .replace(
            "'app.context_run_operator_ticket_organization_id'",
            "'app.context_run_operator_ticket_organization_id'::text",
        )
        .replace("''", "''::text")
        + "))"
    )

    report = audit_rls_snapshot(
        manifest=_manifest(),
        snapshot=snapshot,
        passed_evidence_ids=set(NON_OWNER_EVIDENCE_BY_TABLE.values()),
    )

    assert report["passed"] is True


def test_rls_auditor_requires_each_independent_behavioral_evidence_id() -> None:
    passed_ids = set(NON_OWNER_EVIDENCE_BY_TABLE.values())
    passed_ids.remove("PG-WORKER-LEASE-007")

    report = audit_rls_snapshot(
        manifest=_manifest(),
        snapshot=_matching_catalog_snapshot(),
        passed_evidence_ids=passed_ids,
    )

    assert report["passed"] is False
    failed_tables = {
        cast(str, table["table"])
        for table in cast(list[dict[str, Any]], report["tenantTables"])
        if table["passed"] is False
    }
    assert failed_tables == {"service_principal", "worker_noop_job"}
    assert all(
        table["nonOwnerIsolation"]["evidenceId"] != "PG-RLS-ALL-TENANT-TABLES"
        for table in cast(list[dict[str, Any]], report["tenantTables"])
    )
