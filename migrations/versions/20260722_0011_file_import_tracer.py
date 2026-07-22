"""Publish one registered Markdown file through an exact WorkerLease.

Revision ID: 20260722_0011
Revises: 20260722_0010
Create Date: 2026-07-22
"""

# ruff: noqa: E501

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260722_0011"
down_revision: str | None = "20260722_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MIGRATOR = "context_engine_migrator"
_CONTROL = "context_engine_control"
_RUNTIME = "context_engine_runtime"
_WORKER = "context_engine_worker"
_DEFINER = "context_engine_worker_lease_definer"
_WORKLOAD = "supply.file-import"
_AUDIENCE = "context-engine-worker"
_OPERATION = "file.import"
_MAX_BIGINT = 2**63 - 1
_MAX_TTL = 3600


def _tenant_table(table: str) -> None:
    for role in ("PUBLIC", _CONTROL, _RUNTIME, _WORKER, _DEFINER):
        op.execute(f"REVOKE ALL ON TABLE {table} FROM {role}")
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_migrator_administration ON {table} "
        f"FOR ALL TO {_MIGRATOR} USING (true) WITH CHECK (true)"
    )


def _immutable(table: str) -> None:
    op.execute(
        f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION public.context_content_reject_mutation()"
    )


def upgrade() -> None:
    """Create the narrow acquisition, job, publication, and exact index path."""

    op.drop_constraint(
        "ck_source_version_issue_21_capabilities",
        "source_version",
        type_="check",
    )
    op.create_check_constraint(
        "ck_source_version_file_capabilities",
        "source_version",
        "capability_manifest IN ("
        "'{\"aclEvidenceMode\": \"mirrored\", \"authorizeAndProject\": "
        "\"unavailable\", \"batchLimits\": \"unavailable\", \"checkpoint\": "
        "\"unavailable\", \"checkpointSemantics\": \"unavailable\", "
        "\"contentKinds\": [\"markdown\"], \"consistencyGuarantees\": "
        "\"unavailable\", \"cursorSemantics\": \"unavailable\", "
        "\"declarationVersion\": \"file-capabilities-v1\", \"deletion\": "
        "\"unavailable\", \"describeCapabilities\": \"unavailable\", "
        "\"discover\": \"unavailable\", \"fileSourceAccess\": \"unavailable\", "
        "\"freshness\": \"unavailable\", \"ingestionJobs\": \"unavailable\", "
        "\"projectionFields\": [], \"readChanges\": \"unavailable\", "
        "\"resourceKinds\": [\"markdown_document\"], \"sourceMode\": "
        "\"materialized\"}'::jsonb, "
        "'{\"aclEvidenceMode\": \"mirrored\", \"authorizeAndProject\": "
        "\"unavailable\", \"batchLimits\": \"unavailable\", \"checkpoint\": "
        "\"unavailable\", \"checkpointSemantics\": \"unavailable\", "
        "\"contentKinds\": [\"markdown\"], \"consistencyGuarantees\": "
        "\"unavailable\", \"cursorSemantics\": \"unavailable\", "
        "\"declarationVersion\": \"file-capabilities-v2\", \"deletion\": "
        "\"unavailable\", \"describeCapabilities\": \"unavailable\", "
        "\"discover\": \"unavailable\", \"fileSourceAccess\": \"available\", "
        "\"freshness\": \"unavailable\", \"ingestionJobs\": \"available\", "
        "\"projectionFields\": [], \"readChanges\": \"unavailable\", "
        "\"resourceKinds\": [\"markdown_document\"], \"sourceMode\": "
        "\"materialized\"}'::jsonb)",
    )

    op.drop_constraint(
        "ck_service_principal_workload_issue17",
        "service_principal",
        type_="check",
    )
    op.drop_constraint(
        "ck_service_principal_operation_noop_complete",
        "service_principal",
        type_="check",
    )
    op.create_check_constraint(
        "ck_service_principal_workload_issue17",
        "service_principal",
        "workload IN ('supply.noop', 'supply.file-import')",
    )
    op.create_check_constraint(
        "ck_service_principal_operation_noop_complete",
        "service_principal",
        "operation IN ('noop.complete', 'file.import')",
    )
    op.create_check_constraint(
        "ck_service_principal_workload_operation_binding",
        "service_principal",
        "(workload = 'supply.noop' AND operation = 'noop.complete') OR "
        "(workload = 'supply.file-import' AND operation = 'file.import')",
    )

    op.alter_column(
        "context_resource",
        "active_revision_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )

    op.create_table(
        "file_acquisition",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("acquisition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("audience_principal_ref", sa.Text(), nullable=False),
        sa.Column("audience_membership_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("audience_membership_version", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("request_digest", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("organization_id", "acquisition_id", name="pk_file_acquisition"),
        sa.UniqueConstraint(
            "organization_id", "source_id", "idempotency_key",
            name="uq_file_acquisition_source_idempotency",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_id", "source_version_id"],
            ["source_version.organization_id", "source_version.source_id", "source_version.version_id"],
            name="fk_file_acquisition_source_version_same_organization",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "audience_membership_id", "audience_membership_version"],
            ["membership.organization_id", "membership.membership_id", "membership.membership_version"],
            name="fk_file_acquisition_membership_version_same_organization",
        ),
        sa.CheckConstraint(
            "relative_path ~ '^[^/\\\\]+\\.[mM][dD]$' AND relative_path NOT IN ('.', '..')",
            name="ck_file_acquisition_one_markdown_filename",
        ),
        sa.CheckConstraint("btrim(audience_principal_ref) <> ''", name="ck_file_acquisition_principal_nonblank"),
        sa.CheckConstraint("audience_membership_version > 0", name="ck_file_acquisition_membership_version_positive"),
        sa.CheckConstraint("idempotency_key ~ '^[^[:space:]]{1,255}$'", name="ck_file_acquisition_idempotency_key"),
        sa.CheckConstraint("request_digest ~ '^[0-9a-f]{64}$'", name="ck_file_acquisition_request_digest"),
    )

    op.create_table(
        "file_import_job",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("acquisition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_principal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workload", sa.Text(), nullable=False),
        sa.Column("worker_audience", sa.Text(), nullable=False),
        sa.Column("actor_kind", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("signing_key_version", sa.BigInteger(), nullable=True),
        sa.Column("lease_nonce_digest", postgresql.BYTEA(), nullable=True),
        sa.Column("lease_issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resource_ref", sa.Text(), nullable=True),
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("fragment_ref", sa.Text(), nullable=True),
        sa.Column("effect_count", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("organization_id", "job_id", name="pk_file_import_job"),
        sa.UniqueConstraint("organization_id", "acquisition_id", name="uq_file_import_job_acquisition"),
        sa.ForeignKeyConstraint(
            ["organization_id", "acquisition_id"],
            ["file_acquisition.organization_id", "file_acquisition.acquisition_id"],
            name="fk_file_import_job_acquisition_same_organization",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "service_principal_id", "workload", "worker_audience", "operation"],
            ["service_principal.organization_id", "service_principal.service_principal_id", "service_principal.workload", "service_principal.worker_audience", "service_principal.operation"],
            name="fk_file_import_job_service_principal_binding",
        ),
        sa.CheckConstraint("workload = 'supply.file-import'", name="ck_file_import_job_workload"),
        sa.CheckConstraint("worker_audience = 'context-engine-worker'", name="ck_file_import_job_worker_audience"),
        sa.CheckConstraint("actor_kind = 'service'", name="ck_file_import_job_actor_kind"),
        sa.CheckConstraint("operation = 'file.import'", name="ck_file_import_job_operation"),
        sa.CheckConstraint("state IN ('available', 'leased', 'running', 'failed', 'completed')", name="ck_file_import_job_state"),
        sa.CheckConstraint(
            "(state = 'available' AND signing_key_version IS NULL AND lease_nonce_digest IS NULL AND lease_issued_at IS NULL AND lease_expires_at IS NULL AND lease_redeemed_at IS NULL AND failed_at IS NULL AND completed_at IS NULL AND resource_ref IS NULL AND revision_id IS NULL AND fragment_ref IS NULL AND effect_count = 0) OR "
            "(state = 'leased' AND signing_key_version > 0 AND octet_length(lease_nonce_digest) = 32 AND lease_issued_at IS NOT NULL AND lease_expires_at > lease_issued_at AND lease_redeemed_at IS NULL AND failed_at IS NULL AND completed_at IS NULL AND resource_ref IS NULL AND revision_id IS NULL AND fragment_ref IS NULL AND effect_count = 0) OR "
            "(state = 'running' AND signing_key_version > 0 AND octet_length(lease_nonce_digest) = 32 AND lease_issued_at IS NOT NULL AND lease_expires_at > lease_issued_at AND lease_redeemed_at >= lease_issued_at AND failed_at IS NULL AND completed_at IS NULL AND resource_ref IS NULL AND revision_id IS NULL AND fragment_ref IS NULL AND effect_count = 0) OR "
            "(state = 'failed' AND signing_key_version > 0 AND octet_length(lease_nonce_digest) = 32 AND lease_issued_at IS NOT NULL AND lease_expires_at > lease_issued_at AND lease_redeemed_at >= lease_issued_at AND failed_at >= lease_redeemed_at AND completed_at IS NULL AND resource_ref IS NULL AND revision_id IS NULL AND fragment_ref IS NULL AND effect_count = 0) OR "
            "(state = 'completed' AND signing_key_version > 0 AND octet_length(lease_nonce_digest) = 32 AND lease_issued_at IS NOT NULL AND lease_expires_at > lease_issued_at AND lease_redeemed_at >= lease_issued_at AND failed_at IS NULL AND completed_at >= lease_redeemed_at AND resource_ref IS NOT NULL AND revision_id IS NOT NULL AND fragment_ref IS NOT NULL AND effect_count = 1)",
            name="ck_file_import_job_state_consistency",
        ),
    )

    op.create_table(
        "file_revision_snapshot",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_ref", sa.Text(), nullable=False),
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("acquisition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("canonical_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("compilation_digest", sa.Text(), nullable=False),
        sa.Column("compiler_version", sa.Text(), nullable=False),
        sa.Column("config_version", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("organization_id", "resource_ref", "revision_id", name="pk_file_revision_snapshot"),
        sa.ForeignKeyConstraint(
            ["organization_id", "resource_ref", "revision_id"],
            ["context_revision.organization_id", "context_revision.resource_ref", "context_revision.revision_id"],
            name="fk_file_revision_snapshot_revision_same_organization",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "acquisition_id"],
            ["file_acquisition.organization_id", "file_acquisition.acquisition_id"],
            name="fk_file_revision_snapshot_acquisition_same_organization",
        ),
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_file_revision_snapshot_content_hash"),
        sa.CheckConstraint("compilation_digest ~ '^[0-9a-f]{64}$'", name="ck_file_revision_snapshot_compilation_digest"),
    )

    op.create_table(
        "revision_publication_event",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_ref", sa.Text(), nullable=False),
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.SmallInteger(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("organization_id", "resource_ref", "revision_id", "ordinal", name="pk_revision_publication_event"),
        sa.UniqueConstraint("organization_id", "resource_ref", "revision_id", "state", name="uq_revision_publication_event_state"),
        sa.ForeignKeyConstraint(
            ["organization_id", "resource_ref", "revision_id"],
            ["context_revision.organization_id", "context_revision.resource_ref", "context_revision.revision_id"],
            name="fk_revision_publication_event_revision_same_organization",
        ),
        sa.CheckConstraint("(ordinal, state) IN ((0, 'prepared'), (1, 'indexed'), (2, 'active'))", name="ck_revision_publication_event_order"),
    )

    op.create_table(
        "exact_phrase_candidate",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("phrase_digest", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column("resource_ref", sa.Text(), nullable=False),
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fragment_ref", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("organization_id", "phrase_digest", "resource_ref", "revision_id", "fragment_ref", name="pk_exact_phrase_candidate"),
        sa.ForeignKeyConstraint(
            ["organization_id", "resource_ref", "revision_id", "fragment_ref"],
            ["context_fragment.organization_id", "context_fragment.resource_ref", "context_fragment.revision_id", "context_fragment.fragment_ref"],
            name="fk_exact_phrase_candidate_fragment_same_organization",
        ),
        sa.CheckConstraint("phrase_digest ~ '^[0-9a-f]{64}$'", name="ck_exact_phrase_candidate_digest"),
    )

    for table in (
        "file_acquisition", "file_import_job", "file_revision_snapshot",
        "revision_publication_event", "exact_phrase_candidate",
    ):
        _tenant_table(table)

    for table in ("file_acquisition", "file_revision_snapshot", "revision_publication_event", "exact_phrase_candidate"):
        _immutable(table)

    tenant = (
        "organization_id = NULLIF("
        "current_setting('app.organization_id', true), ''"
        ")::uuid"
    )

    actor = (
        f"{tenant} AND current_setting('app.actor_kind', true) = 'user' "
        "AND NULLIF(current_setting('app.principal_ref', true), '') IS NOT NULL "
        "AND NULLIF(current_setting('app.request_id', true), '') IS NOT NULL "
        "AND NULLIF(current_setting('app.authentication_binding_ref', true), '') "
        "IS NOT NULL AND NULLIF(current_setting('app.checked_at', true), '') "
        "IS NOT NULL AND EXISTS (SELECT 1 FROM public.membership AS actor_membership "
        "WHERE actor_membership.organization_id = "
        "exact_phrase_candidate.organization_id "
        "AND actor_membership.user_id = NULLIF(current_setting('app.user_id', true), '')::uuid "
        "AND actor_membership.membership_id = NULLIF(current_setting('app.membership_id', true), '')::uuid "
        "AND actor_membership.membership_version = NULLIF(current_setting('app.membership_version', true), '')::bigint "
        "AND actor_membership.status = 'active' "
        "AND actor_membership.valid_from <= NULLIF(current_setting('app.checked_at', true), '')::timestamptz "
        "AND (actor_membership.valid_until IS NULL OR actor_membership.valid_until > "
        "NULLIF(current_setting('app.checked_at', true), '')::timestamptz))"
    )
    op.execute(
        "CREATE POLICY exact_phrase_candidate_runtime ON exact_phrase_candidate "
        f"FOR SELECT TO {_RUNTIME} USING ({actor})"
    )
    op.execute("GRANT SELECT ON TABLE exact_phrase_candidate TO context_engine_runtime")

    job_binding = (
        f"organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid "
        "AND job_id = NULLIF(current_setting('app.worker_job_id', true), '')::uuid "
        f"AND workload = '{_WORKLOAD}' AND worker_audience = '{_AUDIENCE}' "
        f"AND operation = '{_OPERATION}'"
    )
    for command in ("SELECT", "UPDATE"):
        check = f" WITH CHECK ({job_binding})" if command == "UPDATE" else ""
        op.execute(
            f"CREATE POLICY file_import_job_definer_{command.lower()} ON file_import_job "
            f"FOR {command} TO {_DEFINER} USING ({job_binding}){check}"
        )
    op.execute(f"GRANT INSERT ON TABLE source_version TO {_DEFINER}")
    op.execute(
        "GRANT UPDATE (active_version_id) ON TABLE context_source TO "
        f"{_DEFINER}"
    )
    op.execute(
        "CREATE POLICY file_import_job_definer_insert ON file_import_job "
        f"FOR INSERT TO {_DEFINER} WITH CHECK ({job_binding})"
    )

    definer_commands = {
        "context_source": ("SELECT", "UPDATE"),
        "source_version": ("SELECT", "INSERT"),
        "membership": ("SELECT",),
        "service_principal": ("SELECT",),
        "file_acquisition": ("SELECT", "INSERT"),
    }
    for table, commands in definer_commands.items():
        suffix = (
            " AND workload = 'supply.file-import' AND "
            "worker_audience = 'context-engine-worker' AND "
            "operation = 'file.import' AND enabled IS TRUE"
            if table == "service_principal"
            else ""
        )
        for command in commands:
            using = (
                f" USING ({tenant}{suffix})"
                if command in {"SELECT", "UPDATE"}
                else ""
            )
            check = (
                f" WITH CHECK ({tenant}{suffix})"
                if command in {"INSERT", "UPDATE"}
                else ""
            )
            op.execute(
                f"CREATE POLICY {table}_file_import_definer_"
                f"{command.lower()} ON {table} FOR {command} "
                f"TO {_DEFINER}{using}{check}"
            )
    op.execute(
        "GRANT SELECT ON TABLE context_source, source_version, membership, "
        "service_principal, file_acquisition, file_import_job "
        f"TO {_DEFINER}"
    )
    op.execute(
        "GRANT INSERT ON TABLE file_acquisition, file_import_job "
        f"TO {_DEFINER}"
    )
    op.execute(
        "GRANT UPDATE (state, signing_key_version, lease_nonce_digest, lease_issued_at, lease_expires_at, lease_redeemed_at, failed_at, completed_at, resource_ref, revision_id, fragment_ref, effect_count) ON file_import_job TO context_engine_worker_lease_definer"
    )

    definer_org = (
        "organization_id = NULLIF("
        "current_setting('app.organization_id', true), ''"
        ")::uuid"
    )
    publication_commands = {
        "context_resource": ("SELECT", "INSERT", "UPDATE"),
        "context_revision": ("INSERT",),
        "context_fragment": ("INSERT",),
        "resource_access_policy": ("INSERT",),
        "membership_resource_field_right": ("INSERT",),
        "file_revision_snapshot": ("INSERT",),
        "revision_publication_event": ("INSERT",),
        "exact_phrase_candidate": ("INSERT",),
    }
    for table, commands in publication_commands.items():
        for command in commands:
            using = (
                f" USING ({definer_org})"
                if command in {"SELECT", "UPDATE"}
                else ""
            )
            check = (
                f" WITH CHECK ({definer_org})"
                if command in {"INSERT", "UPDATE"}
                else ""
            )
            op.execute(
                f"CREATE POLICY {table}_file_import_definer_"
                f"{command.lower()} ON {table} FOR {command} "
                f"TO {_DEFINER}{using}{check}"
            )
    op.execute(
        f"GRANT SELECT, INSERT ON TABLE context_resource TO {_DEFINER}"
    )
    op.execute(
        "GRANT INSERT ON TABLE context_revision, context_fragment, "
        "resource_access_policy, membership_resource_field_right, "
        "file_revision_snapshot, revision_publication_event, "
        f"exact_phrase_candidate TO {_DEFINER}"
    )
    op.execute(f"GRANT UPDATE (active_revision_id) ON TABLE context_resource TO {_DEFINER}")

    publication_read = (
        f"{tenant} AND current_setting('app.actor_kind', true) = 'user' "
        "AND EXISTS (SELECT 1 FROM public.membership AS actor_membership "
        "WHERE actor_membership.organization_id = revision_publication_event.organization_id "
        "AND actor_membership.user_id = NULLIF(current_setting('app.user_id', true), '')::uuid "
        "AND actor_membership.membership_id = NULLIF(current_setting('app.membership_id', true), '')::uuid "
        "AND actor_membership.membership_version = NULLIF(current_setting('app.membership_version', true), '')::bigint "
        "AND actor_membership.status = 'active' "
        "AND actor_membership.valid_from <= NULLIF(current_setting('app.checked_at', true), '')::timestamptz "
        "AND (actor_membership.valid_until IS NULL OR actor_membership.valid_until > "
        "NULLIF(current_setting('app.checked_at', true), '')::timestamptz)) "
        "AND EXISTS (SELECT 1 FROM public.resource_access_policy AS access_policy "
        "WHERE access_policy.organization_id = revision_publication_event.organization_id "
        "AND access_policy.resource_ref = revision_publication_event.resource_ref "
        "AND access_policy.principal_ref = current_setting('app.principal_ref', true) "
        "AND access_policy.access_state = 'allowed')"
    )
    op.execute(
        "CREATE POLICY revision_publication_event_current_user_actor "
        "ON revision_publication_event FOR SELECT TO context_engine_runtime "
        f"USING ({publication_read})"
    )
    op.execute(
        "GRANT SELECT ON TABLE revision_publication_event TO context_engine_runtime"
    )

    _create_functions()


def _create_functions() -> None:
    op.execute(
        f"""
        CREATE FUNCTION public.context_control_prepare_file_import(
            requested_organization_id uuid, requested_acquisition_id uuid,
            requested_job_id uuid, requested_activated_version_id uuid,
            requested_source_id uuid,
            requested_relative_path text, requested_audience_principal_ref text,
            requested_audience_membership_id uuid,
            requested_audience_membership_version bigint,
            requested_idempotency_key text, requested_request_digest text,
            requested_service_principal_id uuid
        ) RETURNS TABLE (job_id uuid, service_principal_id uuid)
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE selected_version_id uuid; selected_acquisition_id uuid;
                selected_root_ref text; selected_capabilities jsonb;
                trusted_now timestamptz;
        BEGIN
            IF SESSION_USER <> '{_CONTROL}' THEN RETURN; END IF;
            trusted_now := pg_catalog.statement_timestamp();
            PERFORM pg_catalog.set_config('app.organization_id', requested_organization_id::text, true);
            PERFORM pg_catalog.set_config('app.worker_job_id', requested_job_id::text, true);
            SELECT version.version_id, version.root_ref,
                   version.capability_manifest
            INTO selected_version_id, selected_root_ref,
                 selected_capabilities
            FROM public.context_source AS source
            JOIN public.source_version AS version
              ON version.organization_id = source.organization_id
             AND version.source_id = source.source_id
             AND version.version_id = source.active_version_id
            WHERE source.organization_id = requested_organization_id
              AND source.source_id = requested_source_id
              AND version.capability_manifest->>'declarationVersion'
                  IN ('file-capabilities-v1', 'file-capabilities-v2');
            IF selected_version_id IS NULL OR NOT EXISTS (
                SELECT 1 FROM public.membership AS audience_membership
                WHERE audience_membership.organization_id = requested_organization_id
                  AND audience_membership.membership_id = requested_audience_membership_id
                  AND audience_membership.membership_version = requested_audience_membership_version
                  AND audience_membership.status = 'active'
                  AND audience_membership.valid_from <= trusted_now
                  AND (audience_membership.valid_until IS NULL OR audience_membership.valid_until > trusted_now)
            ) OR NOT EXISTS (
                SELECT 1 FROM public.service_principal AS receiver
                WHERE receiver.organization_id = requested_organization_id
                  AND receiver.service_principal_id = requested_service_principal_id
                  AND receiver.workload = '{_WORKLOAD}'
                  AND receiver.worker_audience = '{_AUDIENCE}'
                  AND receiver.operation = '{_OPERATION}'
                  AND receiver.enabled IS TRUE
            ) THEN RETURN; END IF;

            IF selected_capabilities->>'declarationVersion'
               = 'file-capabilities-v1' THEN
                INSERT INTO public.source_version (
                    organization_id, source_id, version_id, source_kind,
                    root_ref, capability_manifest, created_at
                ) VALUES (
                    requested_organization_id, requested_source_id,
                    requested_activated_version_id, 'file', selected_root_ref,
                    jsonb_set(
                        jsonb_set(
                            jsonb_set(
                                selected_capabilities,
                                '{{declarationVersion}}',
                                '"file-capabilities-v2"'::jsonb
                            ),
                            '{{fileSourceAccess}}', '"available"'::jsonb
                        ),
                        '{{ingestionJobs}}', '"available"'::jsonb
                    ),
                    trusted_now
                );
                UPDATE public.context_source
                SET active_version_id = requested_activated_version_id
                WHERE organization_id = requested_organization_id
                  AND source_id = requested_source_id
                  AND active_version_id = selected_version_id;
                IF NOT FOUND THEN RETURN; END IF;
                selected_version_id := requested_activated_version_id;
            ELSIF selected_capabilities->>'fileSourceAccess' <> 'available'
               OR selected_capabilities->>'ingestionJobs' <> 'available' THEN
                RETURN;
            END IF;

            INSERT INTO public.file_acquisition (
                organization_id, acquisition_id, source_id, source_version_id,
                relative_path, audience_principal_ref, audience_membership_id,
                audience_membership_version, idempotency_key, request_digest, created_at
            ) VALUES (
                requested_organization_id, requested_acquisition_id, requested_source_id,
                selected_version_id, requested_relative_path, requested_audience_principal_ref,
                requested_audience_membership_id, requested_audience_membership_version,
                requested_idempotency_key, requested_request_digest, trusted_now
            ) ON CONFLICT (organization_id, source_id, idempotency_key) DO NOTHING;
            SELECT acquisition_id INTO selected_acquisition_id
            FROM public.file_acquisition
            WHERE organization_id = requested_organization_id
              AND source_id = requested_source_id
              AND idempotency_key = requested_idempotency_key
              AND request_digest = requested_request_digest;
            IF selected_acquisition_id IS NULL THEN RETURN; END IF;
            INSERT INTO public.file_import_job (
                organization_id, job_id, acquisition_id, source_id,
                service_principal_id, workload, worker_audience, actor_kind,
                operation, state, created_at
            ) VALUES (
                requested_organization_id, requested_job_id, selected_acquisition_id,
                requested_source_id, requested_service_principal_id, '{_WORKLOAD}',
                '{_AUDIENCE}', 'service', '{_OPERATION}', 'available', trusted_now
            ) ON CONFLICT (organization_id, acquisition_id) DO NOTHING;
            RETURN QUERY SELECT job.job_id, job.service_principal_id
            FROM public.file_import_job AS job
            WHERE job.organization_id = requested_organization_id
              AND job.acquisition_id = selected_acquisition_id
              AND job.service_principal_id = requested_service_principal_id;
        END; $function$
        """
    )

    op.execute(
        f"""
        CREATE FUNCTION public.context_worker_issue_file_import_lease(
            requested_organization_id uuid, requested_job_id uuid,
            requested_service_principal_id uuid, requested_source_ref text,
            requested_signing_key_version bigint, requested_nonce bytea,
            requested_ttl_seconds integer
        ) RETURNS TABLE (issued_at timestamptz, expires_at timestamptz)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$
        DECLARE now_at timestamptz;
        BEGIN
            IF SESSION_USER <> '{_CONTROL}' OR requested_signing_key_version NOT BETWEEN 1 AND {_MAX_BIGINT}
               OR pg_catalog.octet_length(requested_nonce) <> 32
               OR requested_ttl_seconds NOT BETWEEN 1 AND {_MAX_TTL} THEN RETURN; END IF;
            PERFORM pg_catalog.set_config('app.organization_id', requested_organization_id::text, true);
            PERFORM pg_catalog.set_config('app.worker_job_id', requested_job_id::text, true);
            now_at := pg_catalog.date_trunc('second', pg_catalog.transaction_timestamp());
            UPDATE public.file_import_job AS job SET state = 'leased',
                signing_key_version = requested_signing_key_version,
                lease_nonce_digest = public.digest(requested_nonce, 'sha256'),
                lease_issued_at = now_at,
                lease_expires_at = now_at + pg_catalog.make_interval(secs => requested_ttl_seconds)
            WHERE job.organization_id = requested_organization_id AND job.job_id = requested_job_id
              AND job.service_principal_id = requested_service_principal_id
              AND job.source_id::text = requested_source_ref AND job.state = 'available'
              AND EXISTS (SELECT 1 FROM public.service_principal AS principal
                WHERE principal.organization_id = job.organization_id
                  AND principal.service_principal_id = job.service_principal_id
                  AND principal.workload = job.workload
                  AND principal.worker_audience = job.worker_audience
                  AND principal.operation = job.operation AND principal.enabled IS TRUE)
            RETURNING job.lease_issued_at, job.lease_expires_at INTO issued_at, expires_at;
            IF issued_at IS NOT NULL THEN RETURN NEXT; END IF; RETURN;
        END; $function$
        """
    )

    op.execute(
        f"""
        CREATE FUNCTION public.context_worker_redeem_file_import(
            requested_organization_id uuid, requested_job_id uuid,
            requested_service_principal_id uuid, requested_source_ref text,
            requested_signing_key_version bigint, requested_nonce bytea,
            requested_issued_at timestamptz, requested_expires_at timestamptz
        ) RETURNS TABLE (source_ref text, root_ref text, relative_path text,
            audience_principal_ref text, audience_membership_id uuid,
            audience_membership_version bigint, acquisition_id uuid)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$
        DECLARE redeemed_at timestamptz;
        BEGIN
            IF SESSION_USER <> '{_WORKER}' THEN RETURN; END IF;
            PERFORM pg_catalog.set_config('app.organization_id', requested_organization_id::text, true);
            PERFORM pg_catalog.set_config('app.worker_job_id', requested_job_id::text, true);
            redeemed_at := pg_catalog.statement_timestamp();
            UPDATE public.file_import_job AS job SET state = 'running', lease_redeemed_at = redeemed_at
            WHERE job.organization_id = requested_organization_id AND job.job_id = requested_job_id
              AND job.service_principal_id = requested_service_principal_id
              AND job.source_id::text = requested_source_ref
              AND job.state = 'leased' AND job.signing_key_version = requested_signing_key_version
              AND job.lease_nonce_digest = public.digest(requested_nonce, 'sha256')
              AND job.lease_issued_at = requested_issued_at AND job.lease_expires_at = requested_expires_at
              AND redeemed_at >= job.lease_issued_at AND redeemed_at < job.lease_expires_at
              AND EXISTS (SELECT 1 FROM public.service_principal AS principal
                  WHERE principal.organization_id = job.organization_id
                    AND principal.service_principal_id = job.service_principal_id
                    AND principal.workload = job.workload
                    AND principal.worker_audience = job.worker_audience
                    AND principal.operation = job.operation
                    AND principal.enabled IS TRUE);
            IF NOT FOUND THEN RETURN; END IF;
            RETURN QUERY SELECT job.source_id::text, version.root_ref, acquisition.relative_path,
                acquisition.audience_principal_ref, acquisition.audience_membership_id,
                acquisition.audience_membership_version, acquisition.acquisition_id
            FROM public.file_import_job AS job
            JOIN public.file_acquisition AS acquisition
              ON acquisition.organization_id = job.organization_id AND acquisition.acquisition_id = job.acquisition_id
            JOIN public.source_version AS version
              ON version.organization_id = acquisition.organization_id
             AND version.source_id = acquisition.source_id
             AND version.version_id = acquisition.source_version_id
            WHERE job.organization_id = requested_organization_id AND job.job_id = requested_job_id;
        END; $function$
        """
    )

    op.execute(
        f"""
        CREATE FUNCTION public.context_worker_fail_file_import(
            requested_organization_id uuid, requested_job_id uuid,
            requested_service_principal_id uuid, requested_source_ref text,
            requested_signing_key_version bigint, requested_nonce bytea,
            requested_issued_at timestamptz, requested_expires_at timestamptz
        ) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$
        DECLARE changed boolean := false; failed_now timestamptz;
        BEGIN
            IF SESSION_USER <> '{_WORKER}' THEN RETURN false; END IF;
            PERFORM pg_catalog.set_config('app.organization_id', requested_organization_id::text, true);
            PERFORM pg_catalog.set_config('app.worker_job_id', requested_job_id::text, true);
            failed_now := pg_catalog.statement_timestamp();
            UPDATE public.file_import_job AS job
            SET state = 'failed', failed_at = failed_now
            WHERE job.organization_id = requested_organization_id
              AND job.job_id = requested_job_id AND job.state = 'running'
              AND job.service_principal_id = requested_service_principal_id
              AND job.source_id::text = requested_source_ref
              AND job.signing_key_version = requested_signing_key_version
              AND job.lease_nonce_digest = public.digest(requested_nonce, 'sha256')
              AND job.lease_issued_at = requested_issued_at
              AND job.lease_expires_at = requested_expires_at
              AND failed_now >= job.lease_issued_at
              AND failed_now < job.lease_expires_at
              AND EXISTS (SELECT 1 FROM public.service_principal AS principal
                  WHERE principal.organization_id = job.organization_id
                    AND principal.service_principal_id = job.service_principal_id
                    AND principal.workload = job.workload
                    AND principal.worker_audience = job.worker_audience
                    AND principal.operation = job.operation
                    AND principal.enabled IS TRUE);
            changed := FOUND;
            RETURN changed;
        END; $function$
        """
    )

    op.execute(
        f"""
        CREATE FUNCTION public.context_worker_publish_file_import(
            requested_organization_id uuid, requested_job_id uuid,
            requested_service_principal_id uuid, requested_source_ref text,
            requested_resource_ref text, requested_revision_id uuid,
            requested_fragment_ref text, requested_canonical_text text,
            requested_paragraph text, requested_content_hash text,
            requested_compilation_digest text, requested_compiler_version text,
            requested_config_version text, requested_phrase_digest text,
            requested_signing_key_version bigint, requested_nonce bytea,
            requested_issued_at timestamptz, requested_expires_at timestamptz
        ) RETURNS TABLE (effect_count smallint)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$
        DECLARE job_row public.file_import_job%ROWTYPE; acquisition_row public.file_acquisition%ROWTYPE; now_at timestamptz;
        BEGIN
            IF SESSION_USER <> '{_WORKER}' OR requested_content_hash !~ '^[0-9a-f]{{64}}$'
               OR requested_compilation_digest !~ '^[0-9a-f]{{64}}$'
               OR requested_phrase_digest !~ '^[0-9a-f]{{64}}$' THEN RETURN; END IF;
            PERFORM pg_catalog.set_config('app.organization_id', requested_organization_id::text, true);
            PERFORM pg_catalog.set_config('app.worker_job_id', requested_job_id::text, true);
            SELECT * INTO job_row FROM public.file_import_job
            WHERE organization_id = requested_organization_id AND job_id = requested_job_id
              AND service_principal_id = requested_service_principal_id
              AND source_id::text = requested_source_ref
              AND state = 'running'
              AND signing_key_version = requested_signing_key_version
              AND lease_nonce_digest = public.digest(requested_nonce, 'sha256')
              AND lease_issued_at = requested_issued_at
              AND lease_expires_at = requested_expires_at
              AND pg_catalog.statement_timestamp() < lease_expires_at
              AND EXISTS (SELECT 1 FROM public.service_principal AS principal
                  WHERE principal.organization_id = file_import_job.organization_id
                    AND principal.service_principal_id = file_import_job.service_principal_id
                    AND principal.workload = file_import_job.workload
                    AND principal.worker_audience = file_import_job.worker_audience
                    AND principal.operation = file_import_job.operation
                    AND principal.enabled IS TRUE)
            FOR UPDATE;
            IF job_row.job_id IS NULL THEN RETURN; END IF;
            SELECT * INTO acquisition_row FROM public.file_acquisition
            WHERE organization_id = job_row.organization_id AND acquisition_id = job_row.acquisition_id;
            now_at := pg_catalog.statement_timestamp();
            SET CONSTRAINTS ALL DEFERRED;
            INSERT INTO public.context_resource (organization_id, resource_ref, source_ref, active_revision_id, tombstoned)
            VALUES (requested_organization_id, requested_resource_ref, job_row.source_id::text, NULL, false);
            INSERT INTO public.context_revision (organization_id, resource_ref, revision_id)
            VALUES (requested_organization_id, requested_resource_ref, requested_revision_id);
            INSERT INTO public.file_revision_snapshot VALUES (
                requested_organization_id, requested_resource_ref, requested_revision_id,
                job_row.acquisition_id, requested_canonical_text, requested_content_hash,
                requested_compilation_digest, requested_compiler_version, requested_config_version
            );
            INSERT INTO public.context_fragment (
                organization_id, resource_ref, revision_id, fragment_ref, ordinal, content, projection_kind
            ) VALUES (requested_organization_id, requested_resource_ref, requested_revision_id, requested_fragment_ref, 0, requested_paragraph, 'body');
            INSERT INTO public.revision_publication_event VALUES
                (requested_organization_id, requested_resource_ref, requested_revision_id, 0, 'prepared', now_at);
            INSERT INTO public.exact_phrase_candidate VALUES (
                requested_organization_id, requested_phrase_digest, job_row.source_id::text,
                requested_resource_ref, requested_revision_id, requested_fragment_ref
            );
            INSERT INTO public.revision_publication_event VALUES
                (requested_organization_id, requested_resource_ref, requested_revision_id, 1, 'indexed', now_at);
            INSERT INTO public.resource_access_policy VALUES (
                requested_organization_id, requested_resource_ref,
                acquisition_row.audience_principal_ref, 1, 'allowed', NULL
            );
            INSERT INTO public.membership_resource_field_right VALUES (
                requested_organization_id, acquisition_row.audience_membership_id,
                acquisition_row.audience_membership_version, requested_resource_ref, 'body'
            );
            UPDATE public.context_resource SET active_revision_id = requested_revision_id
            WHERE organization_id = requested_organization_id AND resource_ref = requested_resource_ref
              AND active_revision_id IS NULL;
            IF NOT FOUND THEN RETURN; END IF;
            INSERT INTO public.revision_publication_event VALUES
                (requested_organization_id, requested_resource_ref, requested_revision_id, 2, 'active', now_at);
            UPDATE public.file_import_job SET state = 'completed', completed_at = now_at,
                resource_ref = requested_resource_ref, revision_id = requested_revision_id,
                fragment_ref = requested_fragment_ref, effect_count = 1
            WHERE organization_id = requested_organization_id AND job_id = requested_job_id AND state = 'running'
            RETURNING file_import_job.effect_count INTO effect_count;
            IF effect_count IS NOT NULL THEN RETURN NEXT; END IF; RETURN;
        END; $function$
        """
    )

    functions = (
        ("context_control_prepare_file_import", "(uuid, uuid, uuid, uuid, uuid, text, text, uuid, bigint, text, text, uuid)", _CONTROL),
        ("context_worker_issue_file_import_lease", "(uuid, uuid, uuid, text, bigint, bytea, integer)", _CONTROL),
        ("context_worker_redeem_file_import", "(uuid, uuid, uuid, text, bigint, bytea, timestamp with time zone, timestamp with time zone)", _WORKER),
        ("context_worker_fail_file_import", "(uuid, uuid, uuid, text, bigint, bytea, timestamp with time zone, timestamp with time zone)", _WORKER),
        ("context_worker_publish_file_import", "(uuid, uuid, uuid, text, text, uuid, text, text, text, text, text, text, text, text, bigint, bytea, timestamp with time zone, timestamp with time zone)", _WORKER),
    )
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    for name, signature, _grantee in functions:
        op.execute(f"REVOKE ALL ON FUNCTION public.{name}{signature} FROM PUBLIC")
        op.execute(f"ALTER FUNCTION public.{name}{signature} OWNER TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    for name, signature, grantee in functions:
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{name}{signature} TO {grantee}")
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


def downgrade() -> None:
    """Remove the Issue #23 tracer and restore the Issue #21/17 contracts."""

    op.execute(
        "LOCK TABLE context_source, source_version, context_resource, "
        "context_revision, context_fragment, resource_access_policy, "
        "membership_resource_field_right, file_acquisition, file_import_job, "
        "file_revision_snapshot, revision_publication_event, "
        "exact_phrase_candidate IN ACCESS EXCLUSIVE MODE"
    )

    functions = (
        "public.context_worker_publish_file_import(uuid, uuid, uuid, text, text, uuid, text, text, text, text, text, text, text, text, bigint, bytea, timestamp with time zone, timestamp with time zone)",
        "public.context_worker_fail_file_import(uuid, uuid, uuid, text, bigint, bytea, timestamp with time zone, timestamp with time zone)",
        "public.context_worker_redeem_file_import(uuid, uuid, uuid, text, bigint, bytea, timestamp with time zone, timestamp with time zone)",
        "public.context_worker_issue_file_import_lease(uuid, uuid, uuid, text, bigint, bytea, integer)",
        "public.context_control_prepare_file_import(uuid, uuid, uuid, uuid, uuid, text, text, uuid, bigint, text, text, uuid)",
    )
    for function in functions:
        op.execute(f"DROP FUNCTION {function}")

    op.execute(
        "REVOKE SELECT ON TABLE revision_publication_event "
        "FROM context_engine_runtime"
    )
    op.execute(
        "DROP POLICY revision_publication_event_current_user_actor "
        "ON revision_publication_event"
    )

    for table in (
        "exact_phrase_candidate",
        "revision_publication_event",
        "file_revision_snapshot",
    ):
        op.drop_table(table)

    op.execute(
        "DELETE FROM membership_resource_field_right AS field_right "
        "USING file_import_job AS job WHERE job.state = 'completed' "
        "AND field_right.organization_id = job.organization_id "
        "AND field_right.resource_ref = job.resource_ref"
    )
    op.execute(
        "DELETE FROM resource_access_policy AS access_policy "
        "USING file_import_job AS job WHERE job.state = 'completed' "
        "AND access_policy.organization_id = job.organization_id "
        "AND access_policy.resource_ref = job.resource_ref"
    )
    op.execute(
        "UPDATE context_resource AS resource SET active_revision_id = NULL "
        "FROM file_import_job AS job WHERE job.state = 'completed' "
        "AND resource.organization_id = job.organization_id "
        "AND resource.resource_ref = job.resource_ref"
    )
    op.execute("DROP TRIGGER context_fragment_reject_mutation ON context_fragment")
    op.execute(
        "DELETE FROM context_fragment AS fragment USING file_import_job AS job "
        "WHERE job.state = 'completed' "
        "AND fragment.organization_id = job.organization_id "
        "AND fragment.resource_ref = job.resource_ref "
        "AND fragment.revision_id = job.revision_id"
    )
    op.execute(
        "CREATE TRIGGER context_fragment_reject_mutation "
        "BEFORE UPDATE OR DELETE ON context_fragment FOR EACH ROW "
        "EXECUTE FUNCTION public.context_content_reject_mutation()"
    )
    op.execute("DROP TRIGGER context_revision_reject_mutation ON context_revision")
    op.execute(
        "DELETE FROM context_revision AS revision USING file_import_job AS job "
        "WHERE job.state = 'completed' "
        "AND revision.organization_id = job.organization_id "
        "AND revision.resource_ref = job.resource_ref "
        "AND revision.revision_id = job.revision_id"
    )
    op.execute(
        "CREATE TRIGGER context_revision_reject_mutation "
        "BEFORE UPDATE OR DELETE ON context_revision FOR EACH ROW "
        "EXECUTE FUNCTION public.context_content_reject_mutation()"
    )
    op.execute(
        "DELETE FROM context_resource AS resource USING file_import_job AS job "
        "WHERE job.state = 'completed' "
        "AND resource.organization_id = job.organization_id "
        "AND resource.resource_ref = job.resource_ref"
    )

    for table in ("file_import_job", "file_acquisition"):
        op.drop_table(table)

    op.execute(
        "WITH prior AS (SELECT DISTINCT ON (organization_id, source_id) "
        "organization_id, source_id, version_id FROM source_version "
        "WHERE capability_manifest->>'declarationVersion' = "
        "'file-capabilities-v1' ORDER BY organization_id, source_id, "
        "created_at, version_id) UPDATE context_source AS source "
        "SET active_version_id = prior.version_id FROM prior "
        "WHERE prior.organization_id = source.organization_id "
        "AND prior.source_id = source.source_id AND EXISTS (SELECT 1 "
        "FROM source_version AS active WHERE active.organization_id = "
        "source.organization_id AND active.source_id = source.source_id "
        "AND active.version_id = source.active_version_id AND "
        "active.capability_manifest->>'declarationVersion' = "
        "'file-capabilities-v2')"
    )
    op.execute("DROP TRIGGER source_version_immutable ON source_version")
    op.execute(
        "DELETE FROM source_version WHERE "
        "capability_manifest->>'declarationVersion' = 'file-capabilities-v2'"
    )
    op.execute(
        "CREATE TRIGGER source_version_immutable BEFORE UPDATE OR DELETE "
        "ON source_version FOR EACH ROW EXECUTE FUNCTION "
        "public.source_version_reject_mutation()"
    )

    for table, commands in {
        "context_source": ("select", "update"),
        "source_version": ("select", "insert"),
        "membership": ("select",),
        "service_principal": ("select",),
        "context_resource": ("select", "insert", "update"),
        "context_revision": ("insert",),
        "context_fragment": ("insert",),
        "resource_access_policy": ("insert",),
        "membership_resource_field_right": ("insert",),
    }.items():
        for command in commands:
            op.execute(
                f"DROP POLICY {table}_file_import_definer_{command} ON {table}"
            )

    op.execute(f"REVOKE SELECT, UPDATE ON TABLE context_source FROM {_DEFINER}")
    op.execute(f"REVOKE SELECT, INSERT ON TABLE source_version FROM {_DEFINER}")
    op.execute(f"REVOKE SELECT ON TABLE membership FROM {_DEFINER}")
    op.execute(
        f"REVOKE SELECT, INSERT, UPDATE ON TABLE context_resource FROM {_DEFINER}"
    )
    op.execute(
        "REVOKE INSERT ON TABLE context_revision, context_fragment, "
        "resource_access_policy, membership_resource_field_right "
        f"FROM {_DEFINER}"
    )
    op.alter_column(
        "context_resource",
        "active_revision_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.drop_constraint(
        "ck_source_version_file_capabilities", "source_version", type_="check"
    )
    op.create_check_constraint(
        "ck_source_version_issue_21_capabilities",
        "source_version",
        "capability_manifest = "
        "'{\"aclEvidenceMode\": \"mirrored\", \"authorizeAndProject\": "
        "\"unavailable\", \"batchLimits\": \"unavailable\", \"checkpoint\": "
        "\"unavailable\", \"checkpointSemantics\": \"unavailable\", "
        "\"contentKinds\": [\"markdown\"], \"consistencyGuarantees\": "
        "\"unavailable\", \"cursorSemantics\": \"unavailable\", "
        "\"declarationVersion\": \"file-capabilities-v1\", \"deletion\": "
        "\"unavailable\", \"describeCapabilities\": \"unavailable\", "
        "\"discover\": \"unavailable\", \"fileSourceAccess\": "
        "\"unavailable\", \"freshness\": \"unavailable\", \"ingestionJobs\": "
        "\"unavailable\", \"projectionFields\": [], \"readChanges\": "
        "\"unavailable\", \"resourceKinds\": [\"markdown_document\"], "
        "\"sourceMode\": \"materialized\"}'::jsonb",
    )
    for constraint in (
        "ck_service_principal_workload_operation_binding",
        "ck_service_principal_workload_issue17",
        "ck_service_principal_operation_noop_complete",
    ):
        op.drop_constraint(constraint, "service_principal", type_="check")
    op.execute(
        "DELETE FROM service_principal WHERE workload = 'supply.file-import' "
        "AND operation = 'file.import'"
    )
    op.create_check_constraint(
        "ck_service_principal_workload_issue17",
        "service_principal",
        "workload = 'supply.noop'",
    )
    op.create_check_constraint(
        "ck_service_principal_operation_noop_complete",
        "service_principal",
        "operation = 'noop.complete'",
    )
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
