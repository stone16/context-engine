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


def test_manifest_classifies_the_exact_issue_48_field_projection_schema() -> None:
    """PROP-TENANT-OWNERSHIP-001: no current table is left unclassified."""

    document = manifest()
    tables = table_entries(document)

    assert document["manifestVersion"] == "8.0.0"
    assert set(tables) == {
        "alembic_version",
        "context_fragment",
        "context_fragment_field",
        "context_resource",
        "context_revision",
        "context_run",
        "context_run_operator_read_ticket",
        "decision_audit",
        "membership",
        "membership_resource_field_right",
        "organization",
        "organization_policy_epoch",
        "organization_record",
        "resource_access_policy",
        "service_principal",
        "user_account",
        "worker_noop_job",
    }
    assert tables["alembic_version"]["classification"] == "global"
    assert tables["organization"]["classification"] == "global"
    assert tables["user_account"]["classification"] == "global"
    assert tables["membership"]["classification"] == "tenant_owned"
    assert tables["organization_record"]["classification"] == "tenant_owned"
    assert tables["organization_policy_epoch"]["classification"] == "tenant_owned"
    assert tables["resource_access_policy"]["classification"] == "tenant_owned"
    assert tables["context_resource"]["classification"] == "tenant_owned"
    assert tables["context_revision"]["classification"] == "tenant_owned"
    assert tables["context_fragment"]["classification"] == "tenant_owned"
    assert tables["context_fragment_field"]["classification"] == "tenant_owned"
    assert tables["membership_resource_field_right"]["classification"] == (
        "tenant_owned"
    )
    assert tables["context_run"]["classification"] == "tenant_owned"
    assert tables["context_run_operator_read_ticket"]["classification"] == (
        "tenant_owned"
    )
    assert tables["decision_audit"]["classification"] == "tenant_owned"
    assert tables["service_principal"]["classification"] == "tenant_owned"
    assert tables["worker_noop_job"]["classification"] == "tenant_owned"


def test_issue_19_lineage_manifest_is_closed_and_role_separated() -> None:
    """TRACE-REDACTION-012: durable lineage exposes no denial detail."""

    entries = table_entries(manifest())
    run = entries["context_run"]
    audit = entries["decision_audit"]
    ticket = entries["context_run_operator_read_ticket"]

    assert run["organizationInclusiveKeys"] == [
        {
            "name": "pk_context_run",
            "kind": "primary_key",
            "columns": ["organization_id", "run_ref"],
        },
        {
            "name": "uq_context_run_decision_ref",
            "kind": "unique",
            "columns": ["organization_id", "decision_ref"],
        },
        {
            "name": "uq_context_run_lineage",
            "kind": "unique",
            "columns": ["organization_id", "run_ref", "decision_ref"],
        },
    ]
    run_foreign_keys = {
        foreign_key["name"]: foreign_key for foreign_key in run["foreignKeys"]
    }
    assert run_foreign_keys["fk_context_run_organization"] == {
        "name": "fk_context_run_organization",
        "columns": ["organization_id"],
        "references": {
            "table": "organization",
            "columns": ["organization_id"],
        },
    }
    assert run_foreign_keys["fk_context_run_membership_same_organization"] == {
        "name": "fk_context_run_membership_same_organization",
        "columns": ["organization_id", "membership_id"],
        "references": {
            "table": "membership",
            "columns": ["organization_id", "membership_id"],
        },
    }

    run_constraint_names = {
        constraint["name"] for constraint in run["checkConstraints"]
    }
    assert run_constraint_names == {
        "ck_context_run_reference_fields_nonblank",
        "ck_context_run_membership_version_positive",
        "ck_context_run_policy_epoch_positive",
        "ck_context_run_effective_scope_digest_sha256",
        "ck_context_run_query_digest_profile",
        "ck_context_run_query_digest_key_version_positive",
        "ck_context_run_query_digest_sha256",
        "ck_context_run_outcome",
        "ck_context_run_package_digest_profile",
        "ck_context_run_package_digest_sha256",
        "ck_context_run_package_retention_digest_only",
        "ck_context_run_authorized_evidence_refs_array",
        "ck_context_run_budget_ceilings_positive",
        "ck_context_run_budget_usage_nonnegative",
        "ck_context_run_budget_usage_within_ceiling",
        "ck_context_run_outcome_evidence_consistency",
        "ck_context_run_timestamp_order",
    }
    expressions = " ".join(
        constraint["expression"] for constraint in run["checkConstraints"]
    )
    for literal in (
        "context-query-json-hmac-sha256-v1",
        "context-package-canonical-json-v1",
        "context-package-canonical-json-v2",
        "digest_only",
        "delivered_authorized",
        "delivered_empty",
        "jsonb_typeof(authorized_evidence_refs) = 'array'",
    ):
        assert literal in expressions
    assert "query_text" not in expressions
    assert "package_payload" not in expressions

    assert audit["organizationInclusiveKeys"] == [
        {
            "name": "pk_decision_audit",
            "kind": "primary_key",
            "columns": ["organization_id", "decision_ref"],
        }
    ]
    assert audit["foreignKeys"] == [
        {
            "name": "fk_decision_audit_context_run_same_organization",
            "columns": ["organization_id", "run_ref", "decision_ref"],
            "references": {
                "table": "context_run",
                "columns": ["organization_id", "run_ref", "decision_ref"],
            },
        }
    ]
    assert {constraint["name"] for constraint in audit["checkConstraints"]} == {
        "ck_decision_audit_policy_snapshot_ref_nonblank",
        "ck_decision_audit_policy_epoch_positive",
        "ck_decision_audit_category_no_authorized_evidence",
    }
    audit_document = json.dumps(audit, sort_keys=True)
    for prohibited in (
        "query_digest",
        "query_text",
        "content",
        "payload",
        "candidate",
        "resource_ref",
        "fragment_ref",
        "denied_count",
    ):
        assert prohibited not in audit_document

    assert run["permittedOperations"] == {
        "context_engine_runtime": ["INSERT"],
        "context_engine_security_operator": [
            "EXECUTE read_context_run_by_operator_ticket"
        ],
        "context_engine_context_run_reader_definer": ["SELECT"],
        "context_engine_worker": [],
        "context_engine_control": [
            "EXECUTE issue_context_run_operator_read_ticket",
            "EXECUTE revoke_context_run_operator_read_ticket",
        ],
    }
    assert audit["permittedOperations"] == run["permittedOperations"]
    for entry in (run, audit):
        rls = entry["rowLevelSecurity"]
        assert rls["enabled"] is True
        assert rls["forced"] is True
        policies = rls["policies"]
        runtime_policy = next(
            policy
            for policy in policies
            if policy["roles"] == ["context_engine_runtime"]
        )
        assert runtime_policy["command"] == "INSERT"
        assert "using" not in runtime_policy
        assert "app.organization_id" in runtime_policy["withCheck"]
        for setting_name in (
            "app.actor_kind",
            "app.user_id",
            "app.membership_id",
            "app.membership_version",
            "app.principal_ref",
            "app.request_id",
            "app.authentication_binding_ref",
            "app.checked_at",
        ):
            assert setting_name in runtime_policy["withCheck"]
        definer_policy = next(
            policy
            for policy in policies
            if policy["roles"] == ["context_engine_context_run_reader_definer"]
        )
        allowed_modes = (
            "= 'read'" if entry["name"] == "decision_audit" else "IN ('issue', 'read')"
        )
        assert definer_policy == {
            "name": f"{entry['name']}_context_run_reader_definer_read",
            "command": "SELECT",
            "roles": ["context_engine_context_run_reader_definer"],
            "using": (
                f"{entry['name']}.organization_id = NULLIF("
                "current_setting("
                "'app.context_run_operator_ticket_organization_id', true), "
                f"'')::uuid AND {entry['name']}.decision_ref = "
                "current_setting("
                "'app.context_run_operator_ticket_decision_ref', true) AND "
                "current_setting('app.context_run_operator_ticket_mode', "
                f"true) {allowed_modes}"
            ),
        }
        migrator_policy = next(
            policy
            for policy in policies
            if policy["roles"] == ["context_engine_migrator"]
        )
        assert migrator_policy == {
            "name": f"{entry['name']}_migrator_administration",
            "command": "ALL",
            "roles": ["context_engine_migrator"],
            "using": "true",
            "withCheck": "true",
        }
        assert not [
            policy
            for policy in policies
            if policy["roles"]
            in (["context_engine_worker"], ["context_engine_control"])
        ]
        assert "TRACE-REDACTION-012" in entry["securityInvariantIds"]
        assert {"DB-001", "DB-002", "DB-004", "OBS-004", "OBS-005"} <= set(
            entry["negativeTestIds"]
        )

    assert ticket["organizationInclusiveKeys"] == [
        {
            "name": "pk_context_run_operator_read_ticket",
            "kind": "primary_key",
            "columns": ["organization_id", "ticket_digest"],
        }
    ]
    assert ticket["capabilityUniqueKeys"] == [
        {
            "name": "uq_context_run_operator_read_ticket_digest",
            "kind": "unique",
            "columns": ["ticket_digest"],
            "rationale": (
                "A raw ticket can identify at most one Organization-bound "
                "capability; replay cannot revoke or consume a second tenant's row"
            ),
        }
    ]
    assert ticket["foreignKeys"] == [
        {
            "name": "fk_context_run_operator_ticket_exact_decision",
            "columns": ["organization_id", "decision_ref"],
            "references": {
                "table": "context_run",
                "columns": ["organization_id", "decision_ref"],
            },
            "onDelete": "CASCADE",
        }
    ]
    assert {item["name"] for item in ticket["checkConstraints"]} == {
        "ck_context_run_operator_ticket_digest_sha256",
        "ck_context_run_operator_ticket_bindings_nonblank",
        "ck_context_run_operator_ticket_exact_ttl",
    }
    assert ticket["operatorReadBoundary"] == {
        "ticketFormat": "64 lowercase hexadecimal characters",
        "storedAs": "sha256_digest_only",
        "databaseOwnedTtlSeconds": 60,
        "issueFunction": "issue_context_run_operator_read_ticket",
        "revokeFunction": "revoke_context_run_operator_read_ticket",
        "readFunction": "read_context_run_by_operator_ticket",
        "functionOwner": "context_engine_context_run_reader_definer",
        "securityDefiner": True,
        "searchPath": ["pg_catalog", "pg_temp"],
        "rowSecurity": True,
        "issueSessionUser": "context_engine_control",
        "readSessionUser": "context_engine_security_operator",
        "consumeMode": "atomic_delete_before_exact_projection",
    }
    assert ticket["permittedOperations"] == {
        "context_engine_runtime": [],
        "context_engine_security_operator": [
            "EXECUTE read_context_run_by_operator_ticket"
        ],
        "context_engine_context_run_reader_definer": [
            "SELECT",
            "INSERT",
            "DELETE",
        ],
        "context_engine_worker": [],
        "context_engine_control": [
            "EXECUTE issue_context_run_operator_read_ticket",
            "EXECUTE revoke_context_run_operator_read_ticket",
        ],
    }


def test_worker_lease_manifest_requires_exact_receiver_and_job() -> None:
    """DB-011/JOB-001/JOB-005: worker authority is exact and fail closed."""

    entries = table_entries(manifest())
    principal = entries["service_principal"]
    job = entries["worker_noop_job"]

    assert principal["organizationInclusiveKeys"] == [
        {
            "name": "pk_service_principal",
            "kind": "primary_key",
            "columns": ["organization_id", "service_principal_id"],
        },
        {
            "name": "uq_service_principal_worker_binding",
            "kind": "unique",
            "columns": [
                "organization_id",
                "service_principal_id",
                "workload",
                "worker_audience",
                "operation",
            ],
        },
    ]
    assert job["organizationInclusiveKeys"] == [
        {
            "name": "pk_worker_noop_job",
            "kind": "primary_key",
            "columns": ["organization_id", "job_id"],
        }
    ]
    job_foreign_keys = {
        foreign_key["name"]: foreign_key for foreign_key in job["foreignKeys"]
    }
    assert job_foreign_keys["fk_worker_noop_job_service_principal_binding"] == {
        "name": "fk_worker_noop_job_service_principal_binding",
        "columns": [
            "organization_id",
            "service_principal_id",
            "workload",
            "worker_audience",
            "operation",
        ],
        "references": {
            "table": "service_principal",
            "columns": [
                "organization_id",
                "service_principal_id",
                "workload",
                "worker_audience",
                "operation",
            ],
        },
    }

    assert {constraint["name"] for constraint in principal["checkConstraints"]} == {
        "ck_service_principal_workload_bounds",
        "ck_service_principal_workload_issue17",
        "ck_service_principal_worker_audience_bounds",
        "ck_service_principal_worker_audience_issue17",
        "ck_service_principal_operation_noop_complete",
    }
    assert {constraint["name"] for constraint in job["checkConstraints"]} == {
        "ck_worker_noop_job_workload_bounds",
        "ck_worker_noop_job_workload_issue17",
        "ck_worker_noop_job_worker_audience_bounds",
        "ck_worker_noop_job_worker_audience_issue17",
        "ck_worker_noop_job_actor_kind_service",
        "ck_worker_noop_job_operation_noop_complete",
        "ck_worker_noop_job_state",
        "ck_worker_noop_job_signing_key_version_positive",
        "ck_worker_noop_job_nonce_sha256_length",
        "ck_worker_noop_job_state_consistency",
    }
    state_constraint = next(
        constraint["expression"]
        for constraint in job["checkConstraints"]
        if constraint["name"] == "ck_worker_noop_job_state_consistency"
    )
    for state in ("available", "leased", "completed"):
        assert f"state = '{state}'" in state_constraint
    for field in (
        "signing_key_version",
        "lease_nonce_digest",
        "lease_issued_at",
        "lease_expires_at",
        "lease_redeemed_at",
        "completed_at",
        "effect_count",
    ):
        assert field in state_constraint

    assert principal["permittedOperations"] == {
        "context_engine_runtime": [],
        "context_engine_worker": [],
        "context_engine_worker_lease_definer": ["SELECT"],
    }
    assert job["permittedOperations"] == {
        "context_engine_control": ["EXECUTE issue_noop_worker_lease"],
        "context_engine_runtime": [],
        "context_engine_worker": ["EXECUTE complete_noop_worker_job"],
        "context_engine_worker_lease_definer": ["SELECT", "UPDATE"],
    }

    for entry in (principal, job):
        rls = entry["rowLevelSecurity"]
        assert rls["enabled"] is True
        assert rls["forced"] is True
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
        assert not [
            policy
            for policy in rls["policies"]
            if policy["roles"] == ["context_engine_worker"]
        ]
        assert {"DB-011", "JOB-001", "JOB-005", "WORKER-LEASE-007"} <= set(
            entry["negativeTestIds"]
        )
        assert "WORKER-LEASE-007" in entry["securityInvariantIds"]

    principal_definer_policy = next(
        policy
        for policy in principal["rowLevelSecurity"]["policies"]
        if policy["roles"] == ["context_engine_worker_lease_definer"]
    )
    assert principal_definer_policy["command"] == "SELECT"
    assert "enabled IS TRUE" in principal_definer_policy["using"]
    for receiver_value in ("supply.noop", "context-engine-worker", "noop.complete"):
        assert receiver_value in principal_definer_policy["using"]
    definer_policies = [
        policy
        for policy in job["rowLevelSecurity"]["policies"]
        if policy["roles"] == ["context_engine_worker_lease_definer"]
    ]
    assert {policy["command"] for policy in definer_policies} == {
        "SELECT",
        "UPDATE",
    }
    update_policy = next(
        policy for policy in definer_policies if policy["command"] == "UPDATE"
    )
    assert update_policy["using"] == update_policy["withCheck"]
    assert "app.worker_job_id" in update_policy["using"]
    assert "active_service_principal.enabled IS TRUE" in update_policy["using"]
    for receiver_value in ("supply.noop", "context-engine-worker", "noop.complete"):
        assert receiver_value in update_policy["using"]

    operations = manifest()["controlOperations"]
    assert operations[1:] == [
        {
            "name": "issue_noop_worker_lease",
            "databaseFunction": "context_worker_issue_noop_lease",
            "role": "context_engine_control",
            "definerRole": "context_engine_worker_lease_definer",
            "directTableMutationAllowed": False,
            "databaseOwnedTime": True,
            "maxTtlSeconds": 3600,
            "expiredLeaseReissuance": True,
            "atomicWrites": ["worker_noop_job"],
        },
        {
            "name": "complete_noop_worker_job",
            "databaseFunction": "context_worker_complete_noop_job",
            "role": "context_engine_worker",
            "definerRole": "context_engine_worker_lease_definer",
            "directTableMutationAllowed": False,
            "databaseOwnedTime": True,
            "rawNonceComparedAsSha256": True,
            "fixedReceiver": {
                "databaseRole": "context_engine_worker",
                "workload": "supply.noop",
                "workerAudience": "context-engine-worker",
                "operation": "noop.complete",
            },
            "callerSuppliedReceiverDimensions": [],
            "atomicWrites": ["worker_noop_job"],
        },
    ]


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
        {
            "name": "uq_membership_organization_id_version",
            "kind": "unique",
            "columns": [
                "organization_id",
                "membership_id",
                "membership_version",
            ],
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
    field = entries["context_fragment_field"]
    right = entries["membership_resource_field_right"]

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
            "name": "ck_context_fragment_projection_kind",
            "expression": "projection_kind IN ('body', 'fields')",
        },
        {
            "name": "ck_context_fragment_projection_payload",
            "expression": (
                "(projection_kind = 'body' AND content IS NOT NULL AND "
                "translate(content, U&'\\0009\\000A\\000B\\000C\\000D\\001C"
                "\\001D\\001E\\001F\\0020\\0085\\00A0\\1680\\2000\\2001"
                "\\2002\\2003\\2004\\2005\\2006\\2007\\2008\\2009\\200A"
                "\\2028\\2029\\202F\\205F\\3000', '') <> '') OR "
                "(projection_kind = 'fields' AND content IS NULL)"
            ),
        },
    ]
    assert fragment["projectionModes"] == {
        "body": {
            "content": "required_nonblank",
            "runtimeRightFieldRef": "body",
        },
        "fields": {
            "content": "must_be_null",
            "valuesTable": "context_fragment_field",
        },
    }

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

    fragment_policy = next(
        policy["using"]
        for policy in fragment["rowLevelSecurity"]["policies"]
        if policy["roles"] == ["context_engine_runtime"]
    )
    assert "projection_kind = 'fields'" in fragment_policy
    assert "membership_resource_field_right" in fragment_policy
    assert "field_right.field_ref = 'body'" in fragment_policy
    assert "resource_access_policy" in fragment_policy
    assert "current_access.access_state = 'allowed'" in fragment_policy

    assert field["organizationInclusiveKeys"] == [
        {
            "name": "pk_context_fragment_field",
            "kind": "primary_key",
            "columns": [
                "organization_id",
                "resource_ref",
                "revision_id",
                "fragment_ref",
                "field_ref",
            ],
        },
        {
            "name": "uq_context_fragment_field_parent_ordinal",
            "kind": "unique",
            "columns": [
                "organization_id",
                "resource_ref",
                "revision_id",
                "fragment_ref",
                "ordinal",
            ],
        },
    ]
    assert field["foreignKeys"] == [
        {
            "name": "fk_context_fragment_field_parent_same_organization",
            "columns": [
                "organization_id",
                "resource_ref",
                "revision_id",
                "fragment_ref",
            ],
            "references": {
                "table": "context_fragment",
                "columns": [
                    "organization_id",
                    "resource_ref",
                    "revision_id",
                    "fragment_ref",
                ],
            },
        }
    ]
    assert field["checkConstraints"] == [
        {
            "name": "ck_context_fragment_field_ordinal_bounded",
            "expression": "ordinal BETWEEN 0 AND 63",
        },
        {
            "name": "ck_context_fragment_field_ref",
            "expression": (
                "field_ref ~ '^[a-z][a-z0-9_]{0,63}$' "
                "AND field_ref <> 'body'"
            ),
        },
        {
            "name": "ck_context_fragment_field_value_nonblank",
            "expression": (
                "translate(field_value, "
                "U&'\\0009\\000A\\000B\\000C\\000D\\001C\\001D\\001E\\001F"
                "\\0020\\0085\\00A0\\1680\\2000\\2001\\2002\\2003\\2004"
                "\\2005\\2006\\2007\\2008\\2009\\200A\\2028\\2029\\202F"
                "\\205F\\3000', '') <> ''"
            ),
        },
    ]
    assert field["parentProjectionGuard"] == {
        "trigger": "context_fragment_field_fields_parent_guard",
        "function": "context_fragment_field_require_fields_parent",
        "requiredProjectionKind": "fields",
        "sqlstate": "23514",
    }
    assert field["immutableRows"] == {
        "trigger": "context_fragment_field_reject_mutation",
        "function": "context_content_reject_mutation",
        "events": ["UPDATE", "DELETE"],
        "sqlstate": "55000",
    }

    assert right["organizationInclusiveKeys"] == [
        {
            "name": "pk_membership_resource_field_right",
            "kind": "primary_key",
            "columns": [
                "organization_id",
                "membership_id",
                "membership_version",
                "resource_ref",
                "field_ref",
            ],
        }
    ]
    right_foreign_keys = {
        foreign_key["name"]: foreign_key for foreign_key in right["foreignKeys"]
    }
    assert right_foreign_keys["fk_membership_field_right_membership_version"][
        "columns"
    ] == [
        "organization_id",
        "membership_id",
        "membership_version",
    ]
    assert right_foreign_keys["fk_membership_field_right_resource_same_organization"][
        "columns"
    ] == ["organization_id", "resource_ref"]
    assert right["mutationLinearization"] == {
        "trigger": "membership_resource_field_right_mutation_lock",
        "function": "membership_resource_field_right_lock_mutation",
        "scope": "organization_transaction_advisory_lock",
        "runtimeLockMode": "shared",
        "mutationLockMode": "exclusive",
    }

    for entry in (field, right):
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
        for required_setting in (
            "app.organization_id",
            "app.user_id",
            "app.membership_id",
            "app.membership_version",
            "app.checked_at",
        ):
            assert required_setting in runtime_policy["using"]
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

    field_policy = next(
        policy["using"]
        for policy in field["rowLevelSecurity"]["policies"]
        if policy["roles"] == ["context_engine_runtime"]
    )
    assert "membership_resource_field_right" in field_policy
    assert "field_right.field_ref = context_fragment_field.field_ref" in field_policy
    assert "resource_access_policy" in field_policy
    assert "current_access.principal_ref = current_setting" in field_policy
    assert "current_access.access_state = 'allowed'" in field_policy
    right_policy = next(
        policy["using"]
        for policy in right["rowLevelSecurity"]["policies"]
        if policy["roles"] == ["context_engine_runtime"]
    )
    assert "context_resource" in right_policy
    assert "tombstoned IS FALSE" in right_policy
    assert "resource_access_policy" in right_policy
    assert "current_access.access_state = 'allowed'" in right_policy


def test_policy_epoch_manifest_seals_runtime_reads_and_control_mutation() -> None:
    """PG-REVOCATION-006: one DB operation owns mutation plus epoch advance."""

    document = manifest()
    entries = table_entries(document)
    epoch = entries["organization_policy_epoch"]
    access = entries["resource_access_policy"]

    assert epoch["organizationInclusiveKeys"] == [
        {
            "name": "pk_organization_policy_epoch",
            "kind": "primary_key",
            "columns": ["organization_id"],
        }
    ]
    assert epoch["checkConstraints"] == [
        {
            "name": "ck_organization_policy_epoch_positive_signed_bigint",
            "expression": "policy_epoch BETWEEN 1 AND 9223372036854775807",
        }
    ]
    assert access["organizationInclusiveKeys"] == [
        {
            "name": "pk_resource_access_policy",
            "kind": "primary_key",
            "columns": ["organization_id", "resource_ref", "principal_ref"],
        }
    ]
    access_foreign_keys = {
        foreign_key["name"]: foreign_key for foreign_key in access["foreignKeys"]
    }
    assert access_foreign_keys[
        "fk_resource_access_policy_resource_same_organization"
    ] == {
        "name": "fk_resource_access_policy_resource_same_organization",
        "columns": ["organization_id", "resource_ref"],
        "references": {
            "table": "context_resource",
            "columns": ["organization_id", "resource_ref"],
        },
        "onDelete": "CASCADE",
    }
    assert access_foreign_keys["fk_resource_access_policy_organization"] == {
        "name": "fk_resource_access_policy_organization",
        "columns": ["organization_id"],
        "references": {
            "table": "organization",
            "columns": ["organization_id"],
        },
        "onDelete": "CASCADE",
    }
    assert {constraint["name"] for constraint in access["checkConstraints"]} == {
        "ck_resource_access_policy_version_positive_signed_bigint",
        "ck_resource_access_policy_resource_ref_nonblank",
        "ck_resource_access_policy_principal_ref_nonblank",
        "ck_resource_access_policy_state",
        "ck_resource_access_policy_state_timestamp",
    }

    for entry in (epoch, access):
        assert entry["organizationColumn"] == "organization_id"
        assert entry["permittedOperations"] == {
            "context_engine_access_policy_definer": ["SELECT", "UPDATE"],
            "context_engine_control": ["EXECUTE change_resource_access"],
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
        assert "app.principal_ref" in runtime_policy["using"]
        assert all(
            policy["roles"] != ["context_engine_control"] for policy in rls["policies"]
        )
        definer_policies = [
            policy
            for policy in rls["policies"]
            if policy["roles"] == ["context_engine_access_policy_definer"]
        ]
        assert {policy["command"] for policy in definer_policies} == {
            "SELECT",
            "UPDATE",
        }
        select_policy = next(
            policy for policy in definer_policies if policy["command"] == "SELECT"
        )
        update_policy = next(
            policy for policy in definer_policies if policy["command"] == "UPDATE"
        )
        assert "withCheck" not in select_policy
        assert update_policy["using"] == update_policy["withCheck"]
        assert all(
            "app.organization_id" in policy["using"] for policy in definer_policies
        )

    assert document["controlOperations"][0] == {
        "name": "change_resource_access",
        "databaseFunction": "context_control_revoke_resource_access",
        "role": "context_engine_control",
        "definerRole": "context_engine_access_policy_definer",
        "directTableMutationAllowed": False,
        "atomicWrites": [
            "resource_access_policy",
            "organization_policy_epoch",
        ],
    }
