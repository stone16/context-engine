from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

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


@pytest.mark.security_evidence(id="PROP-TENANT-OWNERSHIP-001", layer="property")
def test_manifest_classifies_the_exact_current_release_schema() -> None:
    """PROP-TENANT-OWNERSHIP-001: no current table is left unclassified."""

    document = manifest()
    tables = table_entries(document)

    assert document["manifestVersion"] == "20.0.0"
    assert set(tables) == {
        "active_release_manifest",
        "alembic_version",
        "context_fragment",
        "context_fragment_field",
        "context_resource",
        "context_revision",
        "context_run",
        "context_run_operator_read_ticket",
        "context_source",
        "decision_audit",
        "delivery_evidence",
        "egress_audit",
        "egress_grant",
        "exact_phrase_candidate",
        "file_acquisition",
        "file_acquisition_result",
        "file_import_job",
        "file_import_job_event",
        "file_publication_recovery",
        "file_resource_cleanup_intent",
        "file_resource_ingestion_guard",
        "file_source_acquisition_checkpoint",
        "file_source_cleanup_intent",
        "file_source_publish_watermark",
        "file_revision_snapshot",
        "file_revision_replacement_plan",
        "file_revision_supersession",
        "membership",
        "membership_resource_field_right",
        "organization",
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
    assert tables["delivery_evidence"]["classification"] == "tenant_owned"
    assert tables["egress_grant"]["classification"] == "tenant_owned"
    assert tables["egress_audit"]["classification"] == "tenant_owned"
    assert tables["service_principal"]["classification"] == "tenant_owned"
    assert tables["worker_noop_job"]["classification"] == "tenant_owned"
    assert tables["context_source"]["classification"] == "tenant_owned"
    assert tables["source_version"]["classification"] == "tenant_owned"
    for file_import_table in (
        "exact_phrase_candidate",
        "file_acquisition",
        "file_acquisition_result",
        "file_import_job",
        "file_import_job_event",
        "file_publication_recovery",
        "file_resource_cleanup_intent",
        "file_resource_ingestion_guard",
        "file_revision_snapshot",
        "file_revision_replacement_plan",
        "file_revision_supersession",
        "file_source_acquisition_checkpoint",
        "file_source_cleanup_intent",
        "file_source_publish_watermark",
        "revision_publication_event",
    ):
        assert tables[file_import_table]["classification"] == "tenant_owned"
    for release_table in (
        "active_release_manifest",
        "release_candidate",
        "release_evaluation",
        "release_manifest",
        "release_operator_grant",
        "release_promotion_audit",
    ):
        assert tables[release_table]["classification"] == "tenant_owned"


def test_egress_audit_primary_key_is_organization_inclusive() -> None:
    audit = table_entries(manifest())["egress_audit"]

    assert audit["organizationInclusiveKeys"] == [
        {
            "name": "pk_egress_audit",
            "kind": "primary_key",
            "columns": ["organization_id", "audit_id"],
        }
    ]


def test_issue_24_structural_markdown_contract_is_versioned_and_function_only() -> None:
    document = manifest()
    entries = table_entries(document)
    operation = next(
        value
        for value in document["controlOperations"]
        if value["name"] == "publish_file_import"
    )

    assert operation["versionedDatabaseFunctions"] == {
        "markdown-config-v1": "context_worker_publish_file_import_v2",
        "markdown-config-v2": "context_worker_publish_structural_file_import_v2",
    }
    snapshot = entries["file_revision_snapshot"]
    contract = snapshot["versionedCompilationContract"]
    assert contract["markdown-config-v1"]["compilationDocument"] == "null"
    assert contract["markdown-config-v2"] == {
        "compilationDocument": "required immutable JSONB",
        "logicalUnits": [
            "heading",
            "paragraph",
            "list",
            "fenced_code",
            "table",
        ],
        "fragmentBoundary": "one Fragment per logical unit",
        "contextBoundary": (
            "parent heading ancestry is copied into the same authorized Fragment; "
            "no parent expansion is performed"
        ),
        "provenance": [
            "stable structural path",
            "exact source position",
            "source text",
            "compiler/config profiles",
        ],
    }
    structural_function = "EXECUTE context_worker_publish_structural_file_import_v2"
    assert (
        structural_function
        in entries["file_revision_snapshot"]["permittedOperations"][
            "context_engine_worker"
        ]
    )
    recovery_steps = {
        "EXECUTE context_worker_acquire_file_publication",
        "EXECUTE context_worker_prepare_file_publication",
        "EXECUTE context_worker_index_file_publication",
        "EXECUTE context_worker_activate_recoverable_file_publication",
    }
    assert recovery_steps <= set(
        entries["file_import_job"]["permittedOperations"]["context_engine_worker"]
    )
    assert entries["context_fragment"]["permittedOperations"][
        "context_engine_worker"
    ] == ["EXECUTE context_worker_prepare_file_publication"]


def test_issue_25_file_noop_contract_is_tenant_scoped_and_function_only() -> None:
    document = manifest()
    entries = table_entries(document)
    operation = next(
        value
        for value in document["controlOperations"]
        if value["name"] == "publish_file_import"
    )

    identity = operation["contentIdentity"]
    assert identity == {
        "domain": "context-engine.file-content-identity.v1",
        "dimensions": [
            "organization_id",
            "source_id",
            "resource_ref",
            "canonical_content_hash",
            "compiler_version",
            "config_version",
        ],
        "concurrencyArbitration": "file_resource_ingestion_guard row lock",
        "unchangedReasonCode": "active-content-identity-match",
        "sourceContentRetainedInOutcome": False,
    }
    assert "file_source_publish_watermark" in operation["atomicWrites"]
    guard = entries["file_resource_ingestion_guard"]
    result = entries["file_acquisition_result"]
    assert guard["organizationInclusiveKeys"][0]["columns"] == [
        "organization_id",
        "source_id",
        "resource_ref",
    ]
    assert result["foreignKeys"] == [
        {
            "name": ("fk_file_acquisition_result_acquisition_source_same_organization"),
            "columns": ["organization_id", "acquisition_id", "source_id"],
            "references": {
                "table": "file_acquisition",
                "columns": ["organization_id", "acquisition_id", "source_id"],
            },
        },
        {
            "name": "fk_file_acquisition_result_guard_same_organization",
            "columns": ["organization_id", "source_id", "resource_ref"],
            "references": {
                "table": "file_resource_ingestion_guard",
                "columns": ["organization_id", "source_id", "resource_ref"],
            },
        },
        {
            "name": "fk_file_acquisition_result_revision_same_organization",
            "columns": [
                "organization_id",
                "resource_ref",
                "active_revision_id",
            ],
            "references": {
                "table": "context_revision",
                "columns": ["organization_id", "resource_ref", "revision_id"],
            },
        },
    ]
    assert result["retention"] == {
        "sourceContent": "none",
        "reason": "fixed code plus organization-scoped digest only",
    }
    for entry in (guard, result):
        assert entry["rowLevelSecurity"]["enabled"] is True
        assert entry["rowLevelSecurity"]["forced"] is True
        assert entry["functionOnlyMutation"]["directTableMutationAllowed"] is False
        assert entry["immutableRows"]["events"] == ["UPDATE", "DELETE"]
        assert entry["permittedOperations"]["context_engine_runtime"] == []


def test_issue_26_file_replacement_contract_is_staged_and_function_only() -> None:
    document = manifest()
    entries = table_entries(document)
    operation = next(
        value
        for value in document["controlOperations"]
        if value["name"] == "replace_file_import"
    )

    assert operation["stageDatabaseFunctions"] == {
        "markdown-config-v1": "context_worker_stage_file_replacement",
        "markdown-config-v2": ("context_worker_stage_structural_file_replacement"),
    }
    assert operation["activateDatabaseFunction"] == (
        "context_worker_activate_file_replacement"
    )
    assert operation["transactions"] == [
        "stage complete replacement",
        "activate active pointer",
    ]
    assert operation["stageRaceOutcome"] == (
        "an equivalent concurrent winner completes the late job as unchanged "
        "only after the supplied v1/v2 compilation exactly matches the active "
        "artifact"
    )
    assert operation["stageAtomicWrites"][:2] == [
        "file_resource_ingestion_guard",
        "file_acquisition_result",
    ]
    assert "file_source_publish_watermark" in operation["stageAtomicWrites"]
    assert "file_source_publish_watermark" in operation["activationAtomicWrites"]
    stage_functions = {
        "context_worker_stage_file_replacement",
        "context_worker_stage_structural_file_replacement",
    }
    for table in ("file_resource_ingestion_guard", "file_acquisition_result"):
        assert stage_functions <= set(
            entries[table]["functionOnlyMutation"]["databaseFunctions"]
        )
        assert {f"EXECUTE {function}" for function in stage_functions} <= set(
            entries[table]["permittedOperations"]["context_engine_worker"]
        )
    assert operation["directTableMutationAllowed"] is False
    assert operation["retention"] == (
        "superseded Revisions remain immutable and retained_until_explicit_cleanup"
    )

    plan = entries["file_revision_replacement_plan"]
    supersession = entries["file_revision_supersession"]
    assert plan["organizationInclusiveKeys"][0]["columns"] == [
        "organization_id",
        "resource_ref",
        "replacement_revision_id",
    ]
    assert supersession["retention"] == {
        "supersededRevision": "retained_until_explicit_cleanup",
        "cleanupAuthority": "not active in Issue #26",
    }
    for entry in (plan, supersession):
        assert entry["rowLevelSecurity"]["enabled"] is True
        assert entry["rowLevelSecurity"]["forced"] is True
        assert entry["immutableRows"]["events"] == ["UPDATE", "DELETE"]
        assert entry["functionOnlyMutation"]["directTableMutationAllowed"] is False
        assert entry["permittedOperations"]["context_engine_runtime"] == []


def test_issue_27_file_recovery_contract_is_generation_fenced_and_auditable() -> None:
    document = manifest()
    entries = table_entries(document)
    operation = next(
        value
        for value in document["controlOperations"]
        if value["name"] == "recover_file_publication"
    )
    lease_issue = next(
        value
        for value in document["controlOperations"]
        if value["name"] == "issue_file_import_lease"
    )

    assert operation["durableBoundaries"] == [
        "acquired",
        "prepared",
        "ready",
        "completed",
    ]
    assert operation["idempotencyBinding"] == [
        "organization_id",
        "job_id",
        "source_id",
        "resource_ref",
        "revision_id",
        "content_identity_digest",
        "publication_payload_digest",
    ]
    assert (
        "context_worker_issue_file_import_lease" not in operation["databaseFunctions"]
    )
    assert lease_issue["atomicWrites"] == [
        "file_import_job",
        "file_import_job_event",
    ]
    assert "higher lease generation" in operation["leaseReclaim"]
    checkpoint = entries["file_publication_recovery"]
    history = entries["file_import_job_event"]
    for entry in (checkpoint, history):
        assert entry["rowLevelSecurity"]["enabled"] is True
        assert entry["rowLevelSecurity"]["forced"] is True
        assert entry["functionOnlyMutation"]["directTableMutationAllowed"] is False
        assert entry["permittedOperations"]["context_engine_runtime"] == []
    assert history["immutableRows"]["events"] == ["UPDATE", "DELETE"]
    assert checkpoint["retention"]["sourceContent"] == "none"

    boundary_functions = {
        "acquired": "context_worker_acquire_file_publication",
        "prepared": "context_worker_prepare_file_publication",
        "ready": "context_worker_index_file_publication",
        "completed": "context_worker_activate_recoverable_file_publication",
    }
    for boundary, tables in operation["atomicWritesByBoundary"].items():
        database_function = boundary_functions[boundary]
        for table_name in tables:
            function_only = entries[table_name]["functionOnlyMutation"]
            declared = function_only.get(
                "databaseFunctions", [function_only.get("databaseFunction")]
            )
            causal = function_only.get("causalDatabaseFunctions", declared)
            assert database_function in causal
            assert (
                f"EXECUTE {database_function}"
                in entries[table_name]["permittedOperations"]["context_engine_worker"]
            )


def test_issue_28_file_tombstone_contract_is_atomic_and_function_only() -> None:
    document = manifest()
    entries = table_entries(document)
    operation = next(
        value
        for value in document["controlOperations"]
        if value["name"] == "tombstone_file_resource"
    )

    assert operation == {
        "name": "tombstone_file_resource",
        "databaseFunction": "context_control_tombstone_file_resource",
        "role": "context_engine_control",
        "definerRole": "context_engine_access_policy_definer",
        "directTableMutationAllowed": False,
        "trustedOrganizationSource": "TrustedControlCall",
        "databaseOwnedTime": True,
        "idempotencyBinding": [
            "organization_id",
            "source_id",
            "resource_ref",
        ],
        "runtimeVisibilityBarrier": (
            "Organization-scoped shared Runtime/exclusive publication "
            "transaction advisory lock"
        ),
        "atomicWrites": [
            "context_resource",
            "organization_policy_epoch",
            "file_resource_cleanup_intent",
            "file_source_acquisition_checkpoint",
            "file_source_publish_watermark",
        ],
    }
    cleanup = entries["file_resource_cleanup_intent"]
    assert cleanup["organizationInclusiveKeys"] == [
        {
            "name": "pk_file_resource_cleanup_intent",
            "kind": "primary_key",
            "columns": ["organization_id", "cleanup_intent_id"],
        },
        {
            "name": "uq_file_resource_cleanup_intent_resource",
            "kind": "unique",
            "columns": ["organization_id", "resource_ref"],
        },
        {
            "name": "uq_file_resource_cleanup_intent_event",
            "kind": "unique",
            "columns": ["organization_id", "event_ref"],
        },
        {
            "name": "uq_file_resource_cleanup_intent_progress_lineage",
            "kind": "unique",
            "columns": [
                "organization_id",
                "cleanup_intent_id",
                "source_id",
                "resource_ref",
                "revision_id",
            ],
        },
    ]
    assert cleanup["rowLevelSecurity"]["enabled"] is True
    assert cleanup["rowLevelSecurity"]["forced"] is True
    assert cleanup["immutableRows"]["events"] == ["UPDATE", "DELETE"]
    assert cleanup["functionOnlyMutation"] == {
        "databaseFunction": "context_control_tombstone_file_resource",
        "role": "context_engine_control",
        "definerRole": "context_engine_access_policy_definer",
        "directTableMutationAllowed": False,
    }
    assert cleanup["permittedOperations"]["context_engine_runtime"] == []
    assert cleanup["permittedOperations"]["context_engine_worker"] == []
    assert cleanup["retention"] == {
        "state": "pending",
        "physicalCleanupCompletion": "not active in Issue #28",
        "sourceContent": "none",
    }
    resource = entries["context_resource"]
    assert (
        "context_control_tombstone_file_resource"
        in resource["functionOnlyMutation"]["databaseFunctions"]
    )
    assert resource["functionOnlyMutation"]["definerRoles"] == [
        "context_engine_worker_lease_definer",
        "context_engine_access_policy_definer",
    ]


def test_issue_30_file_source_offboarding_is_atomic_and_function_only() -> None:
    document = manifest()
    entries = table_entries(document)
    operation = next(
        value
        for value in document["controlOperations"]
        if value["name"] == "offboard_file_source"
    )

    assert operation["databaseFunction"] == ("context_control_offboard_file_source")
    assert operation["definerRole"] == ("context_engine_access_policy_definer")
    assert operation["directTableMutationAllowed"] is False
    assert operation["idempotencyBinding"] == ["organization_id", "source_id"]
    assert operation["atomicWrites"] == [
        "context_source",
        "organization_policy_epoch",
        "file_source_cleanup_intent",
        "file_import_job",
    ]
    cleanup = entries["file_source_cleanup_intent"]
    cleanup_foreign_keys = {
        foreign_key["name"]: foreign_key for foreign_key in cleanup["foreignKeys"]
    }
    assert cleanup_foreign_keys["fk_file_source_cleanup_intent_organization"] == {
        "name": "fk_file_source_cleanup_intent_organization",
        "columns": ["organization_id"],
        "references": {
            "table": "organization",
            "columns": ["organization_id"],
        },
    }
    assert cleanup["rowLevelSecurity"]["enabled"] is True
    assert cleanup["rowLevelSecurity"]["forced"] is True
    assert cleanup["immutableRows"]["events"] == ["UPDATE", "DELETE"]
    assert cleanup["functionOnlyMutation"] == {
        "databaseFunction": "context_control_offboard_file_source",
        "role": "context_engine_control",
        "definerRole": "context_engine_access_policy_definer",
        "directTableMutationAllowed": False,
    }
    assert cleanup["permittedOperations"]["context_engine_runtime"] == []
    assert cleanup["permittedOperations"]["context_engine_worker"] == []
    assert cleanup["retention"] == {
        "state": "pending",
        "physicalCleanupCompletion": "not active in Issue #30",
        "sourceContent": "none",
    }
    assert (
        entries["context_source"]["permittedOperations"]["context_engine_runtime"] == []
    )
    resource_policy = next(
        policy
        for policy in entries["context_resource"]["rowLevelSecurity"]["policies"]
        if policy["name"] == "context_resource_current_user_actor"
    )
    assert "context_runtime_file_source_lifecycle_allows" in (resource_policy["using"])
    for policy in entries["file_import_job"]["rowLevelSecurity"]["policies"]:
        if policy["roles"] == ["context_engine_worker_lease_definer"] and (
            policy["command"] in {"SELECT", "UPDATE"}
        ):
            assert "active_source.lifecycle_state = 'active'" in policy["using"]


def test_issue_21_file_source_manifest_is_closed_and_role_separated() -> None:
    entries = table_entries(manifest())
    source = entries["context_source"]
    version = entries["source_version"]

    assert source["organizationInclusiveKeys"] == [
        {
            "name": "pk_context_source",
            "kind": "primary_key",
            "columns": ["organization_id", "source_id"],
        },
        {
            "name": "uq_context_source_registration_idempotency",
            "kind": "unique",
            "columns": [
                "organization_id",
                "registration_operation",
                "idempotency_key",
            ],
        },
    ]
    source_foreign_keys = {
        foreign_key["name"]: foreign_key for foreign_key in source["foreignKeys"]
    }
    assert source_foreign_keys["fk_context_source_active_version_same_organization"][
        "columns"
    ] == ["organization_id", "source_id", "active_version_id"]
    assert source_foreign_keys["fk_context_source_disabled_version_exact"][
        "columns"
    ] == ["organization_id", "source_id", "disabled_version_id"]
    assert version["organizationInclusiveKeys"] == [
        {
            "name": "pk_source_version",
            "kind": "primary_key",
            "columns": ["organization_id", "source_id", "version_id"],
        }
    ]
    version_foreign_key = version["foreignKeys"][0]
    assert version_foreign_key["name"] == ("fk_source_version_source_same_organization")
    assert "onDelete" not in version_foreign_key
    assert version["immutableRows"] == {
        "trigger": "source_version_immutable",
        "function": "source_version_reject_mutation",
        "events": ["UPDATE", "DELETE"],
        "sqlstate": "55000",
    }
    capability_constraint = next(
        constraint
        for constraint in version["checkConstraints"]
        if constraint["name"] == "ck_source_version_file_capabilities"
    )
    assert "materialized" in capability_constraint["expression"]
    assert "markdown" in capability_constraint["expression"]
    assert "mirrored" in capability_constraint["expression"]
    assert (
        '"resourceKinds": ["markdown_document"]'
        in (capability_constraint["expression"])
    )
    assert '"projectionFields": []' in capability_constraint["expression"]
    for dimension in (
        "batchLimits",
        "checkpointSemantics",
        "consistencyGuarantees",
        "cursorSemantics",
        "freshness",
    ):
        assert f'"{dimension}": "unavailable"' in (capability_constraint["expression"])
    assert (
        '"describeCapabilities": "unavailable"' in (capability_constraint["expression"])
    )
    assert (
        '"declarationVersion": "file-capabilities-v1"'
        in (capability_constraint["expression"])
    )
    assert (
        '"declarationVersion": "file-capabilities-v2"'
        in (capability_constraint["expression"])
    )
    assert '"fileSourceAccess": "available"' in (capability_constraint["expression"])
    assert '"ingestionJobs": "available"' in (capability_constraint["expression"])

    assert source["permittedOperations"] == {
        "context_engine_access_policy_definer": [
            "SELECT",
            "UPDATE lifecycle_state, disabled_version_id, disabled_at",
        ],
        "context_engine_control": [
            "SELECT",
            "INSERT",
            "EXECUTE context_control_offboard_file_source",
        ],
        "context_engine_learning": [],
        "context_engine_runtime": [],
        "context_engine_security_operator": [],
        "context_engine_worker": [],
        "context_engine_worker_lease_definer": ["SELECT", "UPDATE"],
    }
    assert version["permittedOperations"] == {
        "context_engine_control": ["SELECT", "INSERT"],
        "context_engine_learning": [],
        "context_engine_runtime": [],
        "context_engine_security_operator": [],
        "context_engine_worker": [],
        "context_engine_worker_lease_definer": ["SELECT", "INSERT"],
    }
    for entry in (source, version):
        assert entry["rowLevelSecurity"]["enabled"] is True
        assert entry["rowLevelSecurity"]["forced"] is True

    operation = next(
        operation
        for operation in manifest()["controlOperations"]
        if operation["name"] == "register_file_source"
    )
    assert operation == {
        "name": "register_file_source",
        "role": "context_engine_control",
        "directTableMutationAllowed": True,
        "trustedOrganizationSource": "TrustedControlCall",
        "transactionLocalOrganizationSetting": "app.organization_id",
        "organizationScopedIdempotency": True,
        "filesystemAccessAllowed": False,
        "durableJobCreationAllowed": False,
        "atomicWrites": ["context_source", "source_version"],
    }


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
        "context-package-canonical-json-v3",
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
        "ck_service_principal_workload_operation_binding",
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
    worker_operations = [
        operation
        for operation in operations
        if operation["name"] in {"issue_noop_worker_lease", "complete_noop_worker_job"}
    ]
    assert worker_operations == [
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
        "context_engine_worker_lease_definer": ["SELECT"],
        "context_engine_delivery_evidence_definer": ["SELECT"],
        "context_engine_egress_grant_definer": ["SELECT"],
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


@pytest.mark.security_evidence(id="PROP-RLS-FAIL-CLOSED-003", layer="property")
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


@pytest.mark.security_evidence(id="PROP-TENANT-FK-002", layer="property")
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

    expected_definer_operations = {
        "context_resource": ["SELECT", "INSERT", "UPDATE"],
        "context_revision": ["INSERT"],
        "context_fragment": ["SELECT", "INSERT"],
    }
    worker_operations = {
        "context_resource": [
            "EXECUTE context_worker_prepare_file_publication",
            "EXECUTE context_worker_activate_recoverable_file_publication",
        ],
        "context_revision": ["EXECUTE context_worker_prepare_file_publication"],
        "context_fragment": ["EXECUTE context_worker_prepare_file_publication"],
    }
    for entry in (resource, revision, fragment):
        assert entry["organizationColumn"] == "organization_id"
        expected_operations = {
            "context_engine_runtime": ["SELECT"],
            "context_engine_worker": worker_operations[entry["name"]],
            "context_engine_worker_lease_definer": expected_definer_operations[
                entry["name"]
            ],
        }
        if entry["name"] == "context_resource":
            expected_operations["context_engine_access_policy_definer"] = [
                "SELECT",
                "UPDATE tombstoned",
            ]
            expected_operations["context_engine_control"] = [
                "EXECUTE context_control_tombstone_file_resource"
            ]
        assert entry["permittedOperations"] == expected_operations
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
                "field_ref ~ '^[a-z][a-z0-9_]{0,63}$' AND field_ref <> 'body'"
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
        expected_operations = {
            "context_engine_runtime": ["SELECT"],
            "context_engine_worker": [],
        }
        if entry["name"] == "membership_resource_field_right":
            expected_operations["context_engine_worker"] = [
                "EXECUTE context_worker_prepare_file_publication"
            ]
            expected_operations["context_engine_worker_lease_definer"] = [
                "SELECT",
                "INSERT",
            ]
        assert entry["permittedOperations"] == expected_operations
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
        expected_operations = {
            "context_engine_access_policy_definer": ["SELECT", "UPDATE"],
            "context_engine_control": ["EXECUTE change_resource_access"],
            "context_engine_runtime": ["SELECT"],
            "context_engine_worker": [],
        }
        if entry["name"] == "organization_policy_epoch":
            expected_operations["context_engine_delivery_evidence_definer"] = ["SELECT"]
            expected_operations["context_engine_egress_grant_definer"] = ["SELECT"]
            expected_operations["context_engine_control"].append(
                "EXECUTE context_control_tombstone_file_resource"
            )
            expected_operations["context_engine_control"].append(
                "EXECUTE context_control_offboard_file_source"
            )
        if entry["name"] == "resource_access_policy":
            expected_operations["context_engine_worker"] = [
                "EXECUTE context_worker_prepare_file_publication"
            ]
            expected_operations["context_engine_worker_lease_definer"] = [
                "SELECT",
                "INSERT",
            ]
        assert entry["permittedOperations"] == expected_operations
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

    change_access = next(
        operation
        for operation in document["controlOperations"]
        if operation["name"] == "change_resource_access"
    )
    assert change_access == {
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


def test_release_manifest_records_exact_immutable_lineage_keys() -> None:
    """RELEASE-OWNER-019: release identity is immutable and Organization-owned."""

    entries = table_entries(manifest())
    release = entries["release_manifest"]
    candidate = entries["release_candidate"]
    evaluation = entries["release_evaluation"]

    assert release["organizationInclusiveKeys"] == [
        {
            "name": "pk_release_manifest",
            "kind": "primary_key",
            "columns": ["organization_id", "manifest_ref"],
        },
        {
            "name": "uq_release_manifest_exact_digest",
            "kind": "unique",
            "columns": ["organization_id", "manifest_ref", "manifest_digest"],
        },
    ]
    assert release["foreignKeys"] == [
        {
            "name": "fk_release_manifest_organization",
            "columns": ["organization_id"],
            "references": {
                "table": "organization",
                "columns": ["organization_id"],
            },
            "onDelete": "CASCADE",
        }
    ]
    assert candidate["organizationInclusiveKeys"] == [
        {
            "name": "pk_release_candidate",
            "kind": "primary_key",
            "columns": ["organization_id", "candidate_ref"],
        },
        {
            "name": "uq_release_candidate_exact_digest",
            "kind": "unique",
            "columns": ["organization_id", "candidate_ref", "candidate_digest"],
        },
    ]
    assert candidate["foreignKeys"] == [
        {
            "name": "fk_release_candidate_manifest_exact",
            "columns": ["organization_id", "manifest_ref", "manifest_digest"],
            "references": {
                "table": "release_manifest",
                "columns": [
                    "organization_id",
                    "manifest_ref",
                    "manifest_digest",
                ],
            },
        }
    ]
    assert evaluation["organizationInclusiveKeys"] == [
        {
            "name": "pk_release_evaluation",
            "kind": "primary_key",
            "columns": ["organization_id", "evaluation_ref"],
        },
        {
            "name": "uq_release_evaluation_exact_digest",
            "kind": "unique",
            "columns": [
                "organization_id",
                "evaluation_ref",
                "evaluation_digest",
            ],
        },
    ]
    assert evaluation["foreignKeys"] == [
        {
            "name": "fk_release_evaluation_candidate_exact",
            "columns": ["organization_id", "candidate_ref", "candidate_digest"],
            "references": {
                "table": "release_candidate",
                "columns": [
                    "organization_id",
                    "candidate_ref",
                    "candidate_digest",
                ],
            },
        },
        {
            "name": "fk_release_evaluation_manifest_exact",
            "columns": ["organization_id", "manifest_ref", "manifest_digest"],
            "references": {
                "table": "release_manifest",
                "columns": [
                    "organization_id",
                    "manifest_ref",
                    "manifest_digest",
                ],
            },
        },
    ]

    assert {item["name"] for item in release["checkConstraints"]} == {
        "ck_release_manifest_refs_bounded",
        "ck_release_manifest_digests",
        "ck_release_manifest_profile_compatibility",
        "ck_release_manifest_revision_ref_arrays",
        "ck_release_manifest_curation_shape",
    }
    assert {item["name"] for item in candidate["checkConstraints"]} == {
        "ck_release_candidate_refs_bounded",
        "ck_release_candidate_digests",
        "ck_release_candidate_generation_incrementable",
        "ck_release_candidate_expected_base",
        "ck_release_candidate_gate_statuses",
        "ck_release_candidate_commands",
    }
    assert {item["name"] for item in evaluation["checkConstraints"]} == {
        "ck_release_evaluation_refs_bounded",
        "ck_release_evaluation_digests",
        "ck_release_evaluation_generation_incrementable",
        "ck_release_evaluation_expected_base",
        "ck_release_evaluation_gate_statuses",
        "ck_release_evaluation_commands",
        "ck_release_evaluation_signature_profile",
    }

    release_document = json.dumps(release, sort_keys=True)
    assert "curation_mode = 'curation_off'" in release_document
    assert "compatible_revision_refs = active_revision_refs" in release_document
    evaluation_document = json.dumps(evaluation, sort_keys=True)
    assert "release-evaluation-rfc8785-sha256-v1" in evaluation_document
    assert "release-evaluation-hmac-sha256-v1" in evaluation_document
    for entry in (release, candidate, evaluation):
        assert entry["immutableRows"] == {
            "trigger": f"{entry['name']}_reject_mutation",
            "function": "release_lineage_reject_mutation",
            "events": ["UPDATE", "DELETE"],
            "sqlstate": "55000",
        }


def test_release_authority_pointer_and_audit_are_function_only() -> None:
    """LEARN-006/007: only Learning promote may publish or append success audit."""

    entries = table_entries(manifest())
    grant = entries["release_operator_grant"]
    pointer = entries["active_release_manifest"]
    audit = entries["release_promotion_audit"]

    assert grant["organizationInclusiveKeys"] == [
        {
            "name": "pk_release_operator_grant",
            "kind": "primary_key",
            "columns": ["organization_id", "authority_ref"],
        },
        {
            "name": "uq_release_operator_grant_exact_digest",
            "kind": "unique",
            "columns": ["organization_id", "authority_ref", "authority_digest"],
        },
    ]
    assert grant["foreignKeys"] == [
        {
            "name": "fk_release_operator_grant_organization",
            "columns": ["organization_id"],
            "references": {
                "table": "organization",
                "columns": ["organization_id"],
            },
            "onDelete": "CASCADE",
        }
    ]
    assert {item["name"] for item in grant["checkConstraints"]} == {
        "ck_release_operator_grant_refs_bounded",
        "ck_release_operator_grant_digest",
        "ck_release_operator_grant_lifetime",
    }

    assert pointer["organizationInclusiveKeys"] == [
        {
            "name": "pk_active_release_manifest",
            "kind": "primary_key",
            "columns": ["organization_id"],
        },
        {
            "name": "uq_active_release_manifest_promotion",
            "kind": "unique",
            "columns": ["organization_id", "promotion_ref"],
        },
    ]
    assert pointer["foreignKeys"] == [
        {
            "name": "fk_active_release_manifest_exact",
            "columns": ["organization_id", "manifest_ref", "manifest_digest"],
            "references": {
                "table": "release_manifest",
                "columns": [
                    "organization_id",
                    "manifest_ref",
                    "manifest_digest",
                ],
            },
        }
    ]
    assert {item["name"] for item in pointer["checkConstraints"]} == {
        "ck_active_release_manifest_generation",
        "ck_active_release_manifest_bindings",
    }

    assert audit["organizationInclusiveKeys"] == [
        {
            "name": "pk_release_promotion_audit",
            "kind": "primary_key",
            "columns": ["organization_id", "active_generation"],
        },
        {
            "name": "uq_release_promotion_audit_ref",
            "kind": "unique",
            "columns": ["organization_id", "promotion_ref"],
        },
    ]
    assert audit["foreignKeys"] == [
        {
            "name": "fk_release_promotion_audit_candidate_exact",
            "columns": ["organization_id", "candidate_ref", "candidate_digest"],
            "references": {
                "table": "release_candidate",
                "columns": [
                    "organization_id",
                    "candidate_ref",
                    "candidate_digest",
                ],
            },
        },
        {
            "name": "fk_release_promotion_audit_manifest_exact",
            "columns": ["organization_id", "manifest_ref", "manifest_digest"],
            "references": {
                "table": "release_manifest",
                "columns": [
                    "organization_id",
                    "manifest_ref",
                    "manifest_digest",
                ],
            },
        },
        {
            "name": "fk_release_promotion_audit_evaluation_exact",
            "columns": ["organization_id", "evaluation_ref", "evaluation_digest"],
            "references": {
                "table": "release_evaluation",
                "columns": [
                    "organization_id",
                    "evaluation_ref",
                    "evaluation_digest",
                ],
            },
        },
    ]
    assert {item["name"] for item in audit["checkConstraints"]} == {
        "ck_release_promotion_audit_generation",
        "ck_release_promotion_audit_lifetime",
        "ck_release_promotion_audit_refs_bounded",
        "ck_release_promotion_audit_digests",
        "ck_release_promotion_audit_expected_base",
    }
    assert audit["immutableRows"] == {
        "trigger": "release_promotion_audit_reject_mutation",
        "function": "release_lineage_reject_mutation",
        "events": ["UPDATE", "DELETE"],
        "sqlstate": "55000",
    }
    for entry in (pointer, audit):
        assert entry["functionOnlyMutation"] == {
            "databaseFunction": "context_learning_promote_release",
            "role": "context_engine_learning",
            "definerRole": "context_engine_release_definer",
            "directTableMutationAllowed": False,
        }


def test_release_force_rls_and_grants_match_the_promotion_boundary() -> None:
    """DB-008/RELEASE-OWNER-019: release roles are least privilege."""

    entries = table_entries(manifest())
    release_names = (
        "release_manifest",
        "release_candidate",
        "release_evaluation",
        "release_operator_grant",
        "active_release_manifest",
        "release_promotion_audit",
    )
    lineage_names = set(release_names[:3])

    for name in release_names:
        entry = entries[name]
        assert entry["organizationColumn"] == "organization_id"
        assert entry["partitions"] == []
        rls = entry["rowLevelSecurity"]
        assert rls["enabled"] is True
        assert rls["forced"] is True
        assert {
            (policy["roles"][0], policy["command"]) for policy in rls["policies"]
        } >= {
            ("context_engine_migrator", "ALL"),
            ("context_engine_release_definer", "ALL"),
        }
        definer_policy = next(
            policy
            for policy in rls["policies"]
            if policy["roles"] == ["context_engine_release_definer"]
        )
        assert definer_policy["using"] == definer_policy["withCheck"]
        assert "app.organization_id" in definer_policy["using"]
        assert entry["permittedOperations"]["context_engine_control"] == []
        assert entry["permittedOperations"]["context_engine_runtime"] == (
            ["SELECT"]
            if name in {"release_manifest", "active_release_manifest"}
            else []
        )
        assert entry["permittedOperations"]["context_engine_worker"] == []
        assert entry["permittedOperations"]["context_engine_security_operator"] == []
        runtime_policies = [
            policy
            for policy in rls["policies"]
            if policy["roles"] == ["context_engine_runtime"]
        ]
        if name in {"release_manifest", "active_release_manifest"}:
            assert len(runtime_policies) == 1
            runtime_policy = runtime_policies[0]
            assert runtime_policy["name"] == f"{name}_runtime_select"
            assert runtime_policy["command"] == "SELECT"
            assert runtime_policy["roles"] == ["context_engine_runtime"]
            using = runtime_policy["using"]
            assert f"{name}.organization_id" in using
            for required_boundary in (
                "app.organization_id",
                "app.actor_kind",
                "app.user_id",
                "app.membership_id",
                "app.membership_version",
                "app.principal_ref",
                "app.request_id",
                "app.authentication_binding_ref",
                "app.checked_at",
                "public.membership",
                "status = 'active'",
                "valid_from",
                "valid_until",
            ):
                assert required_boundary in using
        else:
            assert runtime_policies == []

        if name in lineage_names:
            learning_policies = {
                policy["command"]: policy
                for policy in rls["policies"]
                if policy["roles"] == ["context_engine_learning"]
            }
            assert set(learning_policies) == {"INSERT", "SELECT"}
            assert "using" not in learning_policies["INSERT"]
            assert "app.organization_id" in learning_policies["INSERT"]["withCheck"]
            assert "withCheck" not in learning_policies["SELECT"]
            assert "app.organization_id" in learning_policies["SELECT"]["using"]
            assert entry["permittedOperations"]["context_engine_learning"] == [
                "SELECT",
                "INSERT",
            ]
        elif name == "release_operator_grant":
            assert entry["permittedOperations"]["context_engine_learning"] == []
            assert all(
                policy["roles"] != ["context_engine_learning"]
                for policy in rls["policies"]
            )
        else:
            assert entry["permittedOperations"]["context_engine_learning"] == [
                "EXECUTE context_learning_promote_release"
            ]
            assert all(
                policy["roles"] != ["context_engine_learning"]
                for policy in rls["policies"]
            )

    assert entries["release_manifest"]["permittedOperations"][
        "context_engine_release_definer"
    ] == ["SELECT"]
    assert entries["release_candidate"]["permittedOperations"][
        "context_engine_release_definer"
    ] == ["SELECT"]
    assert entries["release_evaluation"]["permittedOperations"][
        "context_engine_release_definer"
    ] == ["SELECT"]
    assert entries["release_operator_grant"]["permittedOperations"][
        "context_engine_release_definer"
    ] == ["SELECT"]
    assert entries["active_release_manifest"]["permittedOperations"][
        "context_engine_release_definer"
    ] == ["SELECT", "INSERT", "UPDATE"]
    assert entries["release_promotion_audit"]["permittedOperations"][
        "context_engine_release_definer"
    ] == ["SELECT", "INSERT"]


def test_context_learning_promote_release_is_the_single_atomic_operation() -> None:
    """RELEASE-OWNER-019: promotion owns one generation-bound pointer/audit pair."""

    operations = manifest()["controlOperations"]
    promotion = next(
        operation
        for operation in operations
        if operation["name"] == "context_learning_promote_release"
    )
    assert promotion == {
        "name": "context_learning_promote_release",
        "databaseFunction": "context_learning_promote_release",
        "role": "context_engine_learning",
        "definerRole": "context_engine_release_definer",
        "directTableMutationAllowed": False,
        "databaseOwnedTime": True,
        "securityDefiner": True,
        "searchPath": ["pg_catalog", "pg_temp"],
        "rowSecurity": True,
        "sessionUser": "context_engine_learning",
        "expectedState": {
            "generationBound": True,
            "initialGeneration": 0,
            "initialPointerAbsent": True,
        },
        "revalidates": [
            "release_operator_grant",
            "release_candidate",
            "release_evaluation",
            "release_manifest",
            "active_release_manifest",
        ],
        "candidateEvaluationExactBindings": [
            "security_status",
            "security_evidence_digest",
            "reliability_status",
            "reliability_evidence_digest",
            "quality_status",
            "quality_evidence_digest",
            "budget_status",
            "budget_evidence_digest",
            "capability_coverage_digest",
            "fixture_digest",
            "verification_commands",
        ],
        "requiredManifestCurationMode": "curation_off",
        "atomicWrites": [
            "active_release_manifest",
            "release_promotion_audit",
        ],
    }
    assert (
        sum(
            "active_release_manifest" in operation.get("atomicWrites", [])
            for operation in operations
        )
        == 1
    )
