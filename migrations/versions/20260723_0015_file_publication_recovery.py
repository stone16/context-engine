"""Recover one File publication through durable idempotent boundaries.

Revision ID: 20260723_0015
Revises: 20260723_0014
Create Date: 2026-07-23
"""

# ruff: noqa: E501

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260723_0015"
down_revision: str | None = "20260723_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONTROL = "context_engine_control"
_WORKER = "context_engine_worker"
_DEFINER = "context_engine_worker_lease_definer"
_MAX_BIGINT = 9223372036854775807
_MAX_TTL = 3600
_ISSUE_SIGNATURE = "(uuid, uuid, uuid, text, bigint, bytea, integer)"
_LEGACY_REDEEM_SIGNATURE = "(uuid, uuid, uuid, text, bigint, bytea, timestamp with time zone, timestamp with time zone)"
_LEGACY_FAIL_SIGNATURE = _LEGACY_REDEEM_SIGNATURE
_REDEEM_SIGNATURE = "(uuid, uuid, uuid, text, bigint, bigint, bytea, timestamp with time zone, timestamp with time zone)"
_FAIL_SIGNATURE = _REDEEM_SIGNATURE
_ACQUIRE_SIGNATURE = "(uuid, uuid, uuid, text, text, uuid, text, text, text, text, text, jsonb, jsonb, bigint, bigint, bytea, timestamp with time zone, timestamp with time zone)"
_STEP_SIGNATURE = "(uuid, uuid, uuid, text, text, uuid, text, jsonb, jsonb, bigint, bigint, bytea, timestamp with time zone, timestamp with time zone)"
_ACTIVATE_SIGNATURE = "(uuid, uuid, uuid, text, text, uuid, bigint, bigint, bytea, timestamp with time zone, timestamp with time zone)"
_INTERRUPT_SIGNATURE = "(uuid, uuid, uuid, text, text, bigint, bigint, bytea, timestamp with time zone, timestamp with time zone)"
_PUBLISH_V1_SIGNATURE = "(uuid, uuid, uuid, text, text, uuid, text, text, text, text, text, text, text, text, bigint, bytea, timestamp with time zone, timestamp with time zone)"
_PUBLISH_V2_SIGNATURE = "(uuid, uuid, uuid, text, text, uuid, text, text, text, text, text, jsonb, bigint, bytea, timestamp with time zone, timestamp with time zone)"
_STAGE_V1_SIGNATURE = _PUBLISH_V1_SIGNATURE
_STAGE_V2_SIGNATURE = _PUBLISH_V2_SIGNATURE
_LEGACY_ACTIVATE_SIGNATURE = "(uuid, uuid, uuid, text, text, uuid, uuid, bigint, bytea, timestamp with time zone, timestamp with time zone)"
_PUBLISH_V1_GENERATION_SIGNATURE = "(uuid, uuid, uuid, text, text, uuid, text, text, text, text, text, text, text, text, bigint, bigint, bytea, timestamp with time zone, timestamp with time zone)"
_PUBLISH_V2_GENERATION_SIGNATURE = "(uuid, uuid, uuid, text, text, uuid, text, text, text, text, text, jsonb, bigint, bigint, bytea, timestamp with time zone, timestamp with time zone)"
_STAGE_V1_GENERATION_SIGNATURE = _PUBLISH_V1_GENERATION_SIGNATURE
_STAGE_V2_GENERATION_SIGNATURE = _PUBLISH_V2_GENERATION_SIGNATURE
_LEGACY_ACTIVATE_GENERATION_SIGNATURE = "(uuid, uuid, uuid, text, text, uuid, uuid, bigint, bigint, bytea, timestamp with time zone, timestamp with time zone)"


def _tenant_table(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_migrator_administration ON {table} "
        "FOR ALL TO context_engine_migrator USING (true) WITH CHECK (true)"
    )
    tenant = (
        "organization_id = NULLIF("
        "current_setting('app.organization_id', true), ''"
        ")::uuid"
    )
    op.execute(
        f"CREATE POLICY {table}_file_import_definer_select ON {table} "
        f"FOR SELECT TO {_DEFINER} USING ({tenant})"
    )
    op.execute(
        f"CREATE POLICY {table}_file_import_definer_insert ON {table} "
        f"FOR INSERT TO {_DEFINER} WITH CHECK ({tenant})"
    )


def _job_constraint() -> str:
    lease = (
        "lease_generation > 0 AND signing_key_version > 0 AND "
        "octet_length(lease_nonce_digest) = 32 AND "
        "lease_issued_at IS NOT NULL AND lease_expires_at > lease_issued_at"
    )
    no_lineage = (
        "resource_ref IS NULL AND revision_id IS NULL AND fragment_ref IS NULL"
    )
    durable_identity = "resource_ref IS NOT NULL AND revision_id IS NOT NULL"
    return (
        "(state = 'available' AND lease_generation = 0 AND "
        "signing_key_version IS NULL AND lease_nonce_digest IS NULL AND "
        "lease_issued_at IS NULL AND lease_expires_at IS NULL AND "
        "lease_redeemed_at IS NULL AND recovery_from_state IS NULL AND "
        "failed_at IS NULL AND completed_at IS NULL AND "
        f"{no_lineage} AND effect_count = 0) OR "
        f"(state = 'leased' AND {lease} AND lease_redeemed_at IS NULL AND "
        "failed_at IS NULL AND completed_at IS NULL AND effect_count = 0 AND "
        "((recovery_from_state IS NULL AND "
        f"{no_lineage}) OR "
        "(recovery_from_state = 'running' AND fragment_ref IS NULL) OR "
        "(recovery_from_state IN ('prepared', 'ready') AND "
        f"{durable_identity} AND fragment_ref IS NOT NULL))) OR "
        f"(state = 'running' AND {lease} AND "
        "lease_redeemed_at >= lease_issued_at AND recovery_from_state IS NULL "
        "AND failed_at IS NULL AND completed_at IS NULL AND fragment_ref IS NULL "
        "AND ((resource_ref IS NULL AND revision_id IS NULL) OR "
        f"{durable_identity}) AND effect_count = 0) OR "
        f"(state IN ('prepared', 'ready') AND {lease} AND "
        "lease_redeemed_at >= lease_issued_at AND recovery_from_state IS NULL "
        "AND failed_at IS NULL AND completed_at IS NULL AND "
        f"{durable_identity} AND fragment_ref IS NOT NULL AND effect_count = 0) OR "
        f"(state = 'failed' AND {lease} AND "
        "lease_redeemed_at >= lease_issued_at AND recovery_from_state IS NULL "
        "AND failed_at >= lease_redeemed_at AND completed_at IS NULL AND "
        f"{no_lineage} AND effect_count = 0) OR "
        f"(state = 'completed' AND {lease} AND "
        "lease_redeemed_at >= lease_issued_at AND recovery_from_state IS NULL "
        "AND failed_at IS NULL AND completed_at >= lease_redeemed_at AND "
        f"{durable_identity} AND fragment_ref IS NOT NULL AND "
        "effect_count IN (0, 1))"
    )


def upgrade() -> None:
    """Add durable File publication checkpoints, reclaim, and audit."""

    op.add_column(
        "file_import_job",
        sa.Column(
            "lease_generation",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "file_import_job",
        sa.Column("recovery_from_state", sa.Text(), nullable=True),
    )
    # Rows created before this revision already consumed their original lease.
    # Generation zero remains reserved for jobs that have never been issued.
    op.execute(
        "UPDATE file_import_job SET lease_generation = 1 "
        "WHERE state <> 'available'"
    )
    op.drop_constraint("ck_file_import_job_state", "file_import_job", type_="check")
    op.create_check_constraint(
        "ck_file_import_job_state",
        "file_import_job",
        "state IN ('available', 'leased', 'running', 'prepared', 'ready', 'failed', 'completed')",
    )
    op.drop_constraint(
        "ck_file_import_job_state_consistency", "file_import_job", type_="check"
    )
    op.create_check_constraint(
        "ck_file_import_job_state_consistency",
        "file_import_job",
        _job_constraint(),
    )
    op.create_check_constraint(
        "ck_file_import_job_recovery_from_state",
        "file_import_job",
        "recovery_from_state IS NULL OR recovery_from_state IN ('running', 'prepared', 'ready')",
    )

    op.create_table(
        "file_publication_recovery",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_ref", sa.Text(), nullable=False),
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "previous_revision_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("publication_kind", sa.Text(), nullable=False),
        sa.Column("checkpoint", sa.Text(), nullable=False),
        sa.Column("content_identity_digest", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("compilation_digest", sa.Text(), nullable=False),
        sa.Column("publication_payload_digest", sa.Text(), nullable=False),
        sa.Column("compiler_version", sa.Text(), nullable=False),
        sa.Column("config_version", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id", "job_id", name="pk_file_publication_recovery"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "resource_ref",
            "revision_id",
            name="uq_file_publication_recovery_revision",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id"],
            ["file_import_job.organization_id", "file_import_job.job_id"],
            name="fk_file_publication_recovery_job_same_organization",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_id", "resource_ref"],
            [
                "file_resource_ingestion_guard.organization_id",
                "file_resource_ingestion_guard.source_id",
                "file_resource_ingestion_guard.resource_ref",
            ],
            name="fk_file_publication_recovery_guard_same_organization",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "resource_ref", "previous_revision_id"],
            [
                "context_revision.organization_id",
                "context_revision.resource_ref",
                "context_revision.revision_id",
            ],
            name="fk_file_publication_recovery_previous_same_organization",
        ),
        sa.CheckConstraint(
            "publication_kind IN ('initial', 'replacement') AND "
            "((publication_kind = 'initial' AND previous_revision_id IS NULL) OR "
            "(publication_kind = 'replacement' AND previous_revision_id IS NOT NULL "
            "AND previous_revision_id <> revision_id))",
            name="ck_file_publication_recovery_kind",
        ),
        sa.CheckConstraint(
            "checkpoint IN ('acquired', 'prepared', 'ready', 'completed')",
            name="ck_file_publication_recovery_checkpoint",
        ),
        sa.CheckConstraint(
            "content_identity_digest ~ '^[0-9a-f]{64}$' AND "
            "content_hash ~ '^[0-9a-f]{64}$' AND "
            "compilation_digest ~ '^[0-9a-f]{64}$' AND "
            "publication_payload_digest ~ '^[0-9a-f]{64}$'",
            name="ck_file_publication_recovery_digests",
        ),
        sa.CheckConstraint(
            "(compiler_version = 'context-engine-markdown-v1' AND config_version = 'markdown-config-v1') OR "
            "(compiler_version = 'context-engine-markdown-v2' AND config_version = 'markdown-config-v2')",
            name="ck_file_publication_recovery_compiler",
        ),
    )
    op.create_table(
        "file_import_job_event",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("boundary", sa.Text(), nullable=False),
        sa.Column("lease_generation", sa.BigInteger(), nullable=False),
        sa.Column("state_at_event", sa.Text(), nullable=False),
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason_digest", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id", "job_id", "ordinal", name="pk_file_import_job_event"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id"],
            ["file_import_job.organization_id", "file_import_job.job_id"],
            name="fk_file_import_job_event_job_same_organization",
        ),
        sa.CheckConstraint(
            "event_type IN ('acquired', 'prepared', 'indexed', 'interrupted', 'reclaimed', 'active', 'unchanged')",
            name="ck_file_import_job_event_type",
        ),
        sa.CheckConstraint(
            "boundary IN ('acquired', 'prepared', 'indexed', 'active')",
            name="ck_file_import_job_event_boundary",
        ),
        sa.CheckConstraint(
            "lease_generation > 0", name="ck_file_import_job_event_generation"
        ),
        sa.CheckConstraint(
            "((event_type IN ('interrupted', 'reclaimed', 'unchanged') "
            "AND reason_digest ~ '^[0-9a-f]{64}$') OR "
            "(event_type NOT IN ('interrupted', 'reclaimed', 'unchanged') "
            "AND reason_digest IS NULL))",
            name="ck_file_import_job_event_reason_digest",
        ),
    )
    for table in ("file_publication_recovery", "file_import_job_event"):
        _tenant_table(table)
    op.execute(
        "CREATE TRIGGER file_import_job_event_immutable BEFORE UPDATE OR DELETE "
        "ON file_import_job_event FOR EACH ROW EXECUTE FUNCTION context_content_reject_mutation()"
    )
    op.execute(
        f"CREATE POLICY file_publication_recovery_file_import_definer_update "
        "ON file_publication_recovery FOR UPDATE "
        f"TO {_DEFINER} USING (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid) "
        "WITH CHECK (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid)"
    )
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE ON file_publication_recovery TO {_DEFINER}"
    )
    op.execute(f"GRANT SELECT, INSERT ON file_import_job_event TO {_DEFINER}")
    op.execute(
        f"GRANT UPDATE (lease_generation, recovery_from_state) ON file_import_job TO {_DEFINER}"
    )
    _backfill_ready_replacement_recovery()
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"DROP FUNCTION public.context_worker_issue_file_import_lease{_ISSUE_SIGNATURE}"
    )
    op.execute(
        f"DROP FUNCTION public.context_worker_redeem_file_import{_LEGACY_REDEEM_SIGNATURE}"
    )
    op.execute(
        f"DROP FUNCTION public.context_worker_fail_file_import{_LEGACY_FAIL_SIGNATURE}"
    )
    for name, signature in (
        ("context_worker_publish_file_import_v2", _PUBLISH_V1_SIGNATURE),
        ("context_worker_publish_structural_file_import_v2", _PUBLISH_V2_SIGNATURE),
        ("context_worker_stage_file_replacement", _STAGE_V1_SIGNATURE),
        ("context_worker_stage_structural_file_replacement", _STAGE_V2_SIGNATURE),
        ("context_worker_activate_file_replacement", _LEGACY_ACTIVATE_SIGNATURE),
    ):
        op.execute(
            f"REVOKE EXECUTE ON FUNCTION public.{name}{signature} FROM {_WORKER}"
        )

    _create_issue_function()
    _create_redeem_function()
    _create_recovery_safe_fail_function()
    _create_generation_gated_legacy_wrappers()
    _create_acquire_function()
    _create_prepare_function()
    _create_index_function()
    _create_activate_function()
    _create_interrupt_function()
    for name, signature in (
        ("context_worker_issue_file_import_lease", _ISSUE_SIGNATURE),
        ("context_worker_redeem_file_import", _REDEEM_SIGNATURE),
        ("context_worker_fail_file_import", _FAIL_SIGNATURE),
        ("context_worker_publish_file_import_v2", _PUBLISH_V1_GENERATION_SIGNATURE),
        ("context_worker_publish_structural_file_import_v2", _PUBLISH_V2_GENERATION_SIGNATURE),
        ("context_worker_stage_file_replacement", _STAGE_V1_GENERATION_SIGNATURE),
        ("context_worker_stage_structural_file_replacement", _STAGE_V2_GENERATION_SIGNATURE),
        ("context_worker_activate_file_replacement", _LEGACY_ACTIVATE_GENERATION_SIGNATURE),
        ("context_worker_acquire_file_publication", _ACQUIRE_SIGNATURE),
        ("context_worker_prepare_file_publication", _STEP_SIGNATURE),
        ("context_worker_index_file_publication", _STEP_SIGNATURE),
        ("context_worker_activate_recoverable_file_publication", _ACTIVATE_SIGNATURE),
        ("context_worker_record_file_import_interruption", _INTERRUPT_SIGNATURE),
    ):
        op.execute(f"REVOKE ALL ON FUNCTION public.{name}{signature} FROM PUBLIC")
        op.execute(f"ALTER FUNCTION public.{name}{signature} OWNER TO {_DEFINER}")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.context_worker_issue_file_import_lease{_ISSUE_SIGNATURE} TO {_CONTROL}"
    )
    for name, signature in (
        ("context_worker_redeem_file_import", _REDEEM_SIGNATURE),
        ("context_worker_fail_file_import", _FAIL_SIGNATURE),
        ("context_worker_publish_file_import_v2", _PUBLISH_V1_GENERATION_SIGNATURE),
        ("context_worker_publish_structural_file_import_v2", _PUBLISH_V2_GENERATION_SIGNATURE),
        ("context_worker_stage_file_replacement", _STAGE_V1_GENERATION_SIGNATURE),
        ("context_worker_stage_structural_file_replacement", _STAGE_V2_GENERATION_SIGNATURE),
        ("context_worker_activate_file_replacement", _LEGACY_ACTIVATE_GENERATION_SIGNATURE),
        ("context_worker_acquire_file_publication", _ACQUIRE_SIGNATURE),
        ("context_worker_prepare_file_publication", _STEP_SIGNATURE),
        ("context_worker_index_file_publication", _STEP_SIGNATURE),
        ("context_worker_activate_recoverable_file_publication", _ACTIVATE_SIGNATURE),
        ("context_worker_record_file_import_interruption", _INTERRUPT_SIGNATURE),
    ):
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{name}{signature} TO {_WORKER}")
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


def _backfill_ready_replacement_recovery() -> None:
    """Adopt Issue #26 ready replacement plans into the recovery protocol."""

    op.execute(
        """
        WITH ready AS (
            SELECT job.organization_id, job.job_id, job.source_id,
                   job.resource_ref, job.revision_id,
                   job.lease_generation, plan.previous_revision_id,
                   plan.content_identity_digest, plan.prepared_at,
                   snapshot.content_hash, snapshot.compilation_digest,
                   snapshot.compiler_version, snapshot.config_version,
                   snapshot.canonical_text, snapshot.compilation_document,
                   CASE
                     WHEN snapshot.compilation_document IS NULL THEN
                       jsonb_build_array(jsonb_build_object(
                         'fragmentRef', 'fragment:paragraph:1',
                         'contextualText', split_part(
                           snapshot.canonical_text, chr(10), 3
                         ),
                         'searchPhrases', jsonb_build_array(split_part(
                           snapshot.canonical_text, chr(10), 3
                         ))
                       ))
                     ELSE (
                       SELECT jsonb_agg(jsonb_build_object(
                         'fragmentRef', item.fragment->'fragmentRef',
                         'contextualText', item.fragment->'contextualText',
                         'searchPhrases', item.fragment->'searchPhrases'
                       ) ORDER BY item.ordinal)
                       FROM jsonb_array_elements(
                         snapshot.compilation_document->'fragments'
                       ) WITH ORDINALITY AS item(fragment, ordinal)
                     )
                   END AS artifact_document
            FROM file_import_job AS job
            JOIN file_revision_replacement_plan AS plan
              ON plan.organization_id = job.organization_id
             AND plan.job_id = job.job_id
             AND plan.resource_ref = job.resource_ref
             AND plan.replacement_revision_id = job.revision_id
            JOIN file_revision_snapshot AS snapshot
              ON snapshot.organization_id = plan.organization_id
             AND snapshot.resource_ref = plan.resource_ref
             AND snapshot.revision_id = plan.replacement_revision_id
            WHERE job.state = 'ready'
        )
        INSERT INTO file_publication_recovery (
            organization_id, job_id, source_id, resource_ref, revision_id,
            previous_revision_id, publication_kind, checkpoint,
            content_identity_digest, content_hash, compilation_digest,
            publication_payload_digest, compiler_version, config_version,
            created_at, updated_at
        )
        SELECT organization_id, job_id, source_id, resource_ref, revision_id,
               previous_revision_id, 'replacement', 'ready',
               content_identity_digest, content_hash, compilation_digest,
               encode(digest(convert_to(jsonb_build_object(
                 'compilationDocument', compilation_document,
                 'artifactDocument', artifact_document
               )::text, 'UTF8'), 'sha256'), 'hex'),
               compiler_version, config_version, prepared_at, prepared_at
        FROM ready
        """
    )
    op.execute(
        """
        INSERT INTO file_import_job_event (
            organization_id, job_id, ordinal, event_type, boundary,
            lease_generation, state_at_event, revision_id,
            reason_digest, occurred_at
        )
        SELECT recovery.organization_id, recovery.job_id, event.ordinal,
               event.event_type, event.boundary, job.lease_generation,
               event.state_at_event, recovery.revision_id, NULL,
               recovery.created_at
        FROM file_publication_recovery AS recovery
        JOIN file_import_job AS job
          ON job.organization_id = recovery.organization_id
         AND job.job_id = recovery.job_id
        CROSS JOIN (VALUES
          (0::bigint, 'acquired'::text, 'acquired'::text, 'running'::text),
          (1::bigint, 'prepared'::text, 'prepared'::text, 'prepared'::text),
          (2::bigint, 'indexed'::text, 'indexed'::text, 'ready'::text)
        ) AS event(ordinal, event_type, boundary, state_at_event)
        WHERE recovery.checkpoint = 'ready'
        """
    )


def _create_generation_gated_legacy_wrappers() -> None:
    """Keep pre-recovery evidence seams usable without a generation bypass."""

    generation_guard = """
            PERFORM pg_catalog.set_config(
                'app.organization_id', requested_organization_id::text, true
            );
            PERFORM pg_catalog.set_config(
                'app.worker_job_id', requested_job_id::text, true
            );
            IF SESSION_USER <> 'context_engine_worker'
               OR requested_lease_generation NOT BETWEEN 1 AND 9223372036854775807
               OR NOT EXISTS (
                    SELECT 1 FROM public.file_import_job AS job
                    WHERE job.organization_id = requested_organization_id
                      AND job.job_id = requested_job_id
                      AND job.lease_generation = requested_lease_generation
               )
            THEN RETURN; END IF;
    """
    op.execute(
        f"""
        CREATE FUNCTION public.context_worker_publish_file_import_v2(
            requested_organization_id uuid, requested_job_id uuid,
            requested_service_principal_id uuid, requested_source_ref text,
            requested_resource_ref text, requested_revision_id uuid,
            requested_fragment_ref text, requested_canonical_text text,
            requested_paragraph text, requested_content_hash text,
            requested_compilation_digest text, requested_compiler_version text,
            requested_config_version text, requested_phrase_digest text,
            requested_lease_generation bigint,
            requested_signing_key_version bigint, requested_nonce bytea,
            requested_issued_at timestamptz, requested_expires_at timestamptz
        ) RETURNS TABLE (
            effect_count smallint, outcome text, active_revision_id uuid,
            fragment_refs text[], content_identity_digest text,
            reason_digest text
        ) LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$ BEGIN
            {generation_guard}
            RETURN QUERY SELECT *
            FROM public.context_worker_publish_file_import_v2(
                requested_organization_id, requested_job_id,
                requested_service_principal_id, requested_source_ref,
                requested_resource_ref, requested_revision_id,
                requested_fragment_ref, requested_canonical_text,
                requested_paragraph, requested_content_hash,
                requested_compilation_digest, requested_compiler_version,
                requested_config_version, requested_phrase_digest,
                requested_signing_key_version, requested_nonce,
                requested_issued_at, requested_expires_at
            );
        END; $function$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION public.context_worker_publish_structural_file_import_v2(
            requested_organization_id uuid, requested_job_id uuid,
            requested_service_principal_id uuid, requested_source_ref text,
            requested_resource_ref text, requested_revision_id uuid,
            requested_canonical_text text, requested_content_hash text,
            requested_compilation_digest text, requested_compiler_version text,
            requested_config_version text, requested_compilation_document jsonb,
            requested_lease_generation bigint,
            requested_signing_key_version bigint, requested_nonce bytea,
            requested_issued_at timestamptz, requested_expires_at timestamptz
        ) RETURNS TABLE (
            effect_count smallint, outcome text, active_revision_id uuid,
            fragment_refs text[], content_identity_digest text,
            reason_digest text
        ) LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$ BEGIN
            {generation_guard}
            RETURN QUERY SELECT *
            FROM public.context_worker_publish_structural_file_import_v2(
                requested_organization_id, requested_job_id,
                requested_service_principal_id, requested_source_ref,
                requested_resource_ref, requested_revision_id,
                requested_canonical_text, requested_content_hash,
                requested_compilation_digest, requested_compiler_version,
                requested_config_version, requested_compilation_document,
                requested_signing_key_version, requested_nonce,
                requested_issued_at, requested_expires_at
            );
        END; $function$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION public.context_worker_stage_file_replacement(
            requested_organization_id uuid, requested_job_id uuid,
            requested_service_principal_id uuid, requested_source_ref text,
            requested_resource_ref text, requested_revision_id uuid,
            requested_fragment_ref text, requested_canonical_text text,
            requested_paragraph text, requested_content_hash text,
            requested_compilation_digest text, requested_compiler_version text,
            requested_config_version text, requested_phrase_digest text,
            requested_lease_generation bigint,
            requested_signing_key_version bigint, requested_nonce bytea,
            requested_issued_at timestamptz, requested_expires_at timestamptz
        ) RETURNS TABLE (
            previous_revision_id uuid, replacement_revision_id uuid,
            fragment_refs text[], content_identity_digest text,
            effect_count smallint, outcome text, active_revision_id uuid,
            reason_digest text
        ) LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$ BEGIN
            {generation_guard}
            RETURN QUERY SELECT *
            FROM public.context_worker_stage_file_replacement(
                requested_organization_id, requested_job_id,
                requested_service_principal_id, requested_source_ref,
                requested_resource_ref, requested_revision_id,
                requested_fragment_ref, requested_canonical_text,
                requested_paragraph, requested_content_hash,
                requested_compilation_digest, requested_compiler_version,
                requested_config_version, requested_phrase_digest,
                requested_signing_key_version, requested_nonce,
                requested_issued_at, requested_expires_at
            );
        END; $function$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION public.context_worker_stage_structural_file_replacement(
            requested_organization_id uuid, requested_job_id uuid,
            requested_service_principal_id uuid, requested_source_ref text,
            requested_resource_ref text, requested_revision_id uuid,
            requested_canonical_text text, requested_content_hash text,
            requested_compilation_digest text, requested_compiler_version text,
            requested_config_version text, requested_compilation_document jsonb,
            requested_lease_generation bigint,
            requested_signing_key_version bigint, requested_nonce bytea,
            requested_issued_at timestamptz, requested_expires_at timestamptz
        ) RETURNS TABLE (
            previous_revision_id uuid, replacement_revision_id uuid,
            fragment_refs text[], content_identity_digest text,
            effect_count smallint, outcome text, active_revision_id uuid,
            reason_digest text
        ) LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$ BEGIN
            {generation_guard}
            RETURN QUERY SELECT *
            FROM public.context_worker_stage_structural_file_replacement(
                requested_organization_id, requested_job_id,
                requested_service_principal_id, requested_source_ref,
                requested_resource_ref, requested_revision_id,
                requested_canonical_text, requested_content_hash,
                requested_compilation_digest, requested_compiler_version,
                requested_config_version, requested_compilation_document,
                requested_signing_key_version, requested_nonce,
                requested_issued_at, requested_expires_at
            );
        END; $function$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION public.context_worker_activate_file_replacement(
            requested_organization_id uuid, requested_job_id uuid,
            requested_service_principal_id uuid, requested_source_ref text,
            requested_resource_ref text, requested_previous_revision_id uuid,
            requested_replacement_revision_id uuid,
            requested_lease_generation bigint,
            requested_signing_key_version bigint, requested_nonce bytea,
            requested_issued_at timestamptz, requested_expires_at timestamptz
        ) RETURNS TABLE (
            effect_count smallint, outcome text, active_revision_id uuid,
            fragment_refs text[], content_identity_digest text,
            reason_digest text
        ) LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$ BEGIN
            {generation_guard}
            RETURN QUERY SELECT *
            FROM public.context_worker_activate_file_replacement(
                requested_organization_id, requested_job_id,
                requested_service_principal_id, requested_source_ref,
                requested_resource_ref, requested_previous_revision_id,
                requested_replacement_revision_id,
                requested_signing_key_version, requested_nonce,
                requested_issued_at, requested_expires_at
            );
        END; $function$
        """
    )


def _create_issue_function() -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.context_worker_issue_file_import_lease(
            requested_organization_id uuid, requested_job_id uuid,
            requested_service_principal_id uuid, requested_source_ref text,
            requested_signing_key_version bigint, requested_nonce bytea,
            requested_ttl_seconds integer
        ) RETURNS TABLE (
            issued_at timestamptz, expires_at timestamptz,
            lease_generation bigint
        )
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$
        DECLARE
            now_at timestamptz;
            job_row public.file_import_job%ROWTYPE;
            resume_state text;
            next_generation bigint;
            next_ordinal bigint;
        BEGIN
            IF SESSION_USER <> '{_CONTROL}'
               OR requested_signing_key_version NOT BETWEEN 1 AND {_MAX_BIGINT}
               OR pg_catalog.octet_length(requested_nonce) <> 32
               OR requested_ttl_seconds NOT BETWEEN 1 AND {_MAX_TTL}
            THEN RETURN; END IF;
            PERFORM pg_catalog.set_config(
                'app.organization_id', requested_organization_id::text, true
            );
            PERFORM pg_catalog.set_config(
                'app.worker_job_id', requested_job_id::text, true
            );
            now_at := pg_catalog.date_trunc(
                'second', pg_catalog.transaction_timestamp()
            );
            SELECT * INTO job_row FROM public.file_import_job AS job
            WHERE job.organization_id = requested_organization_id
              AND job.job_id = requested_job_id
              AND job.service_principal_id = requested_service_principal_id
              AND job.source_id::text = requested_source_ref
              AND (
                  job.state = 'available'
                  OR (
                      job.state IN ('leased', 'running', 'prepared', 'ready')
                      AND job.lease_expires_at <= now_at
                  )
              )
              AND EXISTS (
                  SELECT 1 FROM public.service_principal AS principal
                  WHERE principal.organization_id = job.organization_id
                    AND principal.service_principal_id = job.service_principal_id
                    AND principal.workload = job.workload
                    AND principal.worker_audience = job.worker_audience
                    AND principal.operation = job.operation
                    AND principal.enabled IS TRUE
              )
            FOR UPDATE;
            IF job_row.job_id IS NULL THEN RETURN; END IF;
            IF job_row.state = 'leased' THEN
                resume_state := job_row.recovery_from_state;
            ELSIF job_row.state = 'available' THEN
                resume_state := NULL;
            ELSE
                resume_state := job_row.state;
            END IF;
            next_generation := job_row.lease_generation + 1;
            IF job_row.lease_generation > 0 THEN
                SELECT COALESCE(max(event.ordinal), -1) + 1
                INTO next_ordinal
                FROM public.file_import_job_event AS event
                WHERE event.organization_id = requested_organization_id
                  AND event.job_id = requested_job_id;
                INSERT INTO public.file_import_job_event (
                    organization_id, job_id, ordinal, event_type, boundary,
                    lease_generation, state_at_event, revision_id,
                    reason_digest, occurred_at
                ) VALUES (
                    requested_organization_id, requested_job_id, next_ordinal,
                    'reclaimed',
                    CASE COALESCE(resume_state, 'running')
                        WHEN 'running' THEN 'acquired'
                        WHEN 'prepared' THEN 'prepared'
                        ELSE 'indexed'
                    END,
                    next_generation, job_row.state, job_row.revision_id,
                    encode(public.digest(
                        convert_to('context-engine.file-reclaim.v1', 'UTF8')
                        || decode('00', 'hex')
                        || uuid_send(requested_organization_id)
                        || uuid_send(requested_job_id)
                        || int8send(next_generation),
                        'sha256'
                    ), 'hex'), now_at
                );
            END IF;
            UPDATE public.file_import_job AS job
            SET state = 'leased',
                lease_generation = next_generation,
                recovery_from_state = resume_state,
                signing_key_version = requested_signing_key_version,
                lease_nonce_digest = public.digest(requested_nonce, 'sha256'),
                lease_issued_at = now_at,
                lease_expires_at = now_at + pg_catalog.make_interval(
                    secs => requested_ttl_seconds
                ),
                lease_redeemed_at = NULL
            WHERE job.organization_id = requested_organization_id
              AND job.job_id = requested_job_id
            RETURNING job.lease_issued_at, job.lease_expires_at,
                      job.lease_generation
            INTO issued_at, expires_at, lease_generation;
            IF issued_at IS NOT NULL THEN RETURN NEXT; END IF;
        END; $function$
        """
    )


def _create_redeem_function() -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.context_worker_redeem_file_import(
            requested_organization_id uuid, requested_job_id uuid,
            requested_service_principal_id uuid, requested_source_ref text,
            requested_lease_generation bigint,
            requested_signing_key_version bigint, requested_nonce bytea,
            requested_issued_at timestamptz, requested_expires_at timestamptz
        ) RETURNS TABLE (
            source_ref text, root_ref text, relative_path text,
            audience_principal_ref text, audience_membership_id uuid,
            audience_membership_version bigint, acquisition_id uuid
        )
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$
        DECLARE redeemed_at timestamptz;
        BEGIN
            IF SESSION_USER <> '{_WORKER}' THEN RETURN; END IF;
            PERFORM pg_catalog.set_config(
                'app.organization_id', requested_organization_id::text, true
            );
            PERFORM pg_catalog.set_config(
                'app.worker_job_id', requested_job_id::text, true
            );
            redeemed_at := pg_catalog.statement_timestamp();
            UPDATE public.file_import_job AS job
            SET state = COALESCE(job.recovery_from_state, 'running'),
                recovery_from_state = NULL,
                lease_redeemed_at = redeemed_at
            WHERE job.organization_id = requested_organization_id
              AND job.job_id = requested_job_id
              AND job.service_principal_id = requested_service_principal_id
              AND job.source_id::text = requested_source_ref
              AND job.state = 'leased'
              AND job.lease_generation = requested_lease_generation
              AND job.signing_key_version = requested_signing_key_version
              AND job.lease_nonce_digest = public.digest(requested_nonce, 'sha256')
              AND job.lease_issued_at = requested_issued_at
              AND job.lease_expires_at = requested_expires_at
              AND redeemed_at >= job.lease_issued_at
              AND redeemed_at < job.lease_expires_at
              AND EXISTS (
                  SELECT 1 FROM public.service_principal AS principal
                  WHERE principal.organization_id = job.organization_id
                    AND principal.service_principal_id = job.service_principal_id
                    AND principal.workload = job.workload
                    AND principal.worker_audience = job.worker_audience
                    AND principal.operation = job.operation
                    AND principal.enabled IS TRUE
              );
            IF NOT FOUND THEN RETURN; END IF;
            RETURN QUERY
            SELECT job.source_id::text, version.root_ref,
                   acquisition.relative_path,
                   acquisition.audience_principal_ref,
                   acquisition.audience_membership_id,
                   acquisition.audience_membership_version,
                   acquisition.acquisition_id
            FROM public.file_import_job AS job
            JOIN public.file_acquisition AS acquisition
              ON acquisition.organization_id = job.organization_id
             AND acquisition.acquisition_id = job.acquisition_id
            JOIN public.source_version AS version
              ON version.organization_id = acquisition.organization_id
             AND version.source_id = acquisition.source_id
             AND version.version_id = acquisition.source_version_id
            WHERE job.organization_id = requested_organization_id
              AND job.job_id = requested_job_id;
        END; $function$
        """
    )


def _create_recovery_safe_fail_function() -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.context_worker_fail_file_import(
            requested_organization_id uuid, requested_job_id uuid,
            requested_service_principal_id uuid, requested_source_ref text,
            requested_lease_generation bigint,
            requested_signing_key_version bigint, requested_nonce bytea,
            requested_issued_at timestamptz,
            requested_expires_at timestamptz
        ) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$
        DECLARE changed boolean := false; failed_now timestamptz;
        BEGIN
            IF SESSION_USER <> '{_WORKER}' THEN RETURN false; END IF;
            PERFORM pg_catalog.set_config(
                'app.organization_id', requested_organization_id::text, true
            );
            PERFORM pg_catalog.set_config(
                'app.worker_job_id', requested_job_id::text, true
            );
            failed_now := pg_catalog.statement_timestamp();
            UPDATE public.file_import_job AS job
            SET state = 'failed', failed_at = failed_now
            WHERE job.organization_id = requested_organization_id
              AND job.job_id = requested_job_id
              AND job.state = 'running'
              AND job.lease_generation = requested_lease_generation
              AND job.resource_ref IS NULL
              AND job.revision_id IS NULL
              AND job.fragment_ref IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM public.file_publication_recovery AS recovery
                  WHERE recovery.organization_id = job.organization_id
                    AND recovery.job_id = job.job_id
              )
              AND job.service_principal_id = requested_service_principal_id
              AND job.source_id::text = requested_source_ref
              AND job.signing_key_version = requested_signing_key_version
              AND job.lease_nonce_digest = public.digest(requested_nonce, 'sha256')
              AND job.lease_issued_at = requested_issued_at
              AND job.lease_expires_at = requested_expires_at
              AND failed_now >= job.lease_issued_at
              AND failed_now < job.lease_expires_at
              AND EXISTS (
                  SELECT 1 FROM public.service_principal AS principal
                  WHERE principal.organization_id = job.organization_id
                    AND principal.service_principal_id = job.service_principal_id
                    AND principal.workload = job.workload
                    AND principal.worker_audience = job.worker_audience
                    AND principal.operation = job.operation
                    AND principal.enabled IS TRUE
              );
            changed := FOUND;
            RETURN changed;
        END; $function$
        """
    )


def _artifact_validation() -> str:
    return r"""
        jsonb_typeof(requested_artifact_document) = 'array'
        AND jsonb_array_length(requested_artifact_document) BETWEEN 1 AND 4096
        AND NOT EXISTS (
            SELECT 1 FROM jsonb_array_elements(
                requested_artifact_document
            ) AS item(fragment)
            WHERE jsonb_typeof(item.fragment) IS DISTINCT FROM 'object'
               OR COALESCE(btrim(item.fragment->>'fragmentRef') = '', true)
               OR COALESCE(btrim(item.fragment->>'contextualText') = '', true)
               OR jsonb_typeof(item.fragment->'searchPhrases')
                    IS DISTINCT FROM 'array'
               OR jsonb_array_length(item.fragment->'searchPhrases')
                    NOT BETWEEN 1 AND 4096
               OR EXISTS (
                    SELECT 1 FROM jsonb_array_elements_text(
                        item.fragment->'searchPhrases'
                    ) AS phrase(value)
                    WHERE btrim(phrase.value) = ''
               )
        )
        AND (
            SELECT count(DISTINCT item.fragment->>'fragmentRef')
            FROM jsonb_array_elements(requested_artifact_document)
                AS item(fragment)
        ) = jsonb_array_length(requested_artifact_document)
    """


def _publication_artifact_validation() -> str:
    """Bind the compact write artifact to the accepted compiler contract."""

    return r"""
        (
            requested_compiler_version = 'context-engine-markdown-v1'
            AND requested_config_version = 'markdown-config-v1'
            AND requested_compilation_document IS NULL
            AND requested_artifact_document = jsonb_build_array(
                jsonb_build_object(
                    'fragmentRef', 'fragment:paragraph:1',
                    'contextualText', split_part(
                        requested_canonical_text, chr(10), 3
                    ),
                    'searchPhrases', jsonb_build_array(split_part(
                        requested_canonical_text, chr(10), 3
                    ))
                )
            )
        ) OR (
            requested_compiler_version = 'context-engine-markdown-v2'
            AND requested_config_version = 'markdown-config-v2'
            AND jsonb_typeof(requested_compilation_document) = 'object'
            AND requested_compilation_document->>'canonicalText'
                IS NOT DISTINCT FROM requested_canonical_text
            AND requested_compilation_document->>'contentHash'
                IS NOT DISTINCT FROM requested_content_hash
            AND requested_compilation_document->>'compilationDigest'
                IS NOT DISTINCT FROM requested_compilation_digest
            AND requested_compilation_document#>>'{provenance,compilerVersion}'
                IS NOT DISTINCT FROM requested_compiler_version
            AND requested_compilation_document#>>'{provenance,configVersion}'
                IS NOT DISTINCT FROM requested_config_version
            AND requested_compilation_document#>>'{provenance,canonicalizationProfile}'
                IS NOT DISTINCT FROM 'markdown-structural-units-v2'
            AND requested_compilation_document#>>'{provenance,compilationDigestProfile}'
                IS NOT DISTINCT FROM 'rfc8785-sha256-v2'
            AND jsonb_typeof(requested_compilation_document->'sections') = 'array'
            AND jsonb_typeof(requested_compilation_document->'fragments') = 'array'
            AND jsonb_array_length(requested_compilation_document->'fragments')
                BETWEEN 1 AND 4096
            AND jsonb_array_length(requested_compilation_document->'sections')
                = jsonb_array_length(requested_compilation_document->'fragments')
            AND NOT EXISTS (
                SELECT 1
                FROM jsonb_array_elements(
                    requested_compilation_document->'fragments'
                ) WITH ORDINALITY AS item(fragment, source_ordinal)
                WHERE jsonb_typeof(item.fragment) IS DISTINCT FROM 'object'
                   OR COALESCE(
                        item.fragment->>'fragmentRef'
                            !~ '^fragment:(heading|paragraph|list|fenced_code|table):[1-9][0-9]*$',
                        true
                   )
                   OR COALESCE(
                        item.fragment->>'kind'
                            NOT IN ('heading', 'paragraph', 'list', 'fenced_code', 'table'),
                        true
                   )
                   OR CASE
                        WHEN jsonb_typeof(item.fragment->'path') = 'array'
                        THEN jsonb_array_length(item.fragment->'path') < 2
                             OR EXISTS (
                                SELECT 1 FROM jsonb_array_elements_text(
                                    item.fragment->'path'
                                ) AS path(segment)
                                WHERE btrim(path.segment) = ''
                                   OR path.segment <> btrim(path.segment)
                             )
                        ELSE true
                      END
                   OR jsonb_typeof(item.fragment->'position')
                        IS DISTINCT FROM 'object'
                   OR COALESCE(item.fragment#>>'{position,start,line}' !~ '^[1-9][0-9]*$', true)
                   OR COALESCE(item.fragment#>>'{position,start,column}' !~ '^[1-9][0-9]*$', true)
                   OR COALESCE(item.fragment#>>'{position,start,byteOffset}' !~ '^[0-9]+$', true)
                   OR COALESCE(item.fragment#>>'{position,end,line}' !~ '^[1-9][0-9]*$', true)
                   OR COALESCE(item.fragment#>>'{position,end,column}' !~ '^[1-9][0-9]*$', true)
                   OR COALESCE(item.fragment#>>'{position,end,byteOffset}' !~ '^[1-9][0-9]*$', true)
                   OR CASE
                        WHEN item.fragment#>>'{position,start,byteOffset}' ~ '^[0-9]+$'
                         AND item.fragment#>>'{position,end,byteOffset}' ~ '^[1-9][0-9]*$'
                        THEN (item.fragment#>>'{position,start,byteOffset}')::numeric
                             >= (item.fragment#>>'{position,end,byteOffset}')::numeric
                        ELSE true
                      END
                   OR COALESCE(
                        translate(
                            item.fragment->>'sourceText',
                            U&'\0009\000A\000B\000C\000D\001C\001D\001E\001F\0020\0085\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000',
                            ''
                        ) = '',
                        true
                   )
                   OR COALESCE(
                        translate(
                            item.fragment->>'contextualText',
                            U&'\0009\000A\000B\000C\000D\001C\001D\001E\001F\0020\0085\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000',
                            ''
                        ) = '',
                        true
                   )
                   OR CASE
                        WHEN jsonb_typeof(item.fragment->'searchPhrases') = 'array'
                        THEN jsonb_array_length(item.fragment->'searchPhrases')
                                NOT BETWEEN 1 AND 4096
                             OR EXISTS (
                                SELECT 1 FROM jsonb_array_elements_text(
                                    item.fragment->'searchPhrases'
                                ) AS phrase(value)
                                WHERE translate(
                                    phrase.value,
                                    U&'\0009\000A\000B\000C\000D\001C\001D\001E\001F\0020\0085\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000',
                                    ''
                                ) = ''
                             )
                             OR (
                                SELECT count(DISTINCT phrase.value)
                                FROM jsonb_array_elements_text(
                                    item.fragment->'searchPhrases'
                                ) AS phrase(value)
                             ) <> jsonb_array_length(
                                item.fragment->'searchPhrases'
                             )
                        ELSE true
                      END
            )
            AND NOT EXISTS (
                SELECT 1
                FROM jsonb_array_elements(
                    requested_compilation_document->'fragments'
                ) WITH ORDINALITY AS fragment_item(fragment, ordinal)
                JOIN jsonb_array_elements(
                    requested_compilation_document->'sections'
                ) WITH ORDINALITY AS section_item(section, ordinal)
                USING (ordinal)
                WHERE fragment_item.fragment->'kind'
                        IS DISTINCT FROM section_item.section->'kind'
                   OR fragment_item.fragment->'path'
                        IS DISTINCT FROM section_item.section->'path'
                   OR fragment_item.fragment->'position'
                        IS DISTINCT FROM section_item.section->'position'
            )
            AND (
                SELECT count(DISTINCT item.fragment->>'fragmentRef')
                FROM jsonb_array_elements(
                    requested_compilation_document->'fragments'
                ) AS item(fragment)
            ) = jsonb_array_length(
                requested_compilation_document->'fragments'
            )
            AND requested_artifact_document = (
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'fragmentRef', item.fragment->'fragmentRef',
                        'contextualText', item.fragment->'contextualText',
                        'searchPhrases', item.fragment->'searchPhrases'
                    ) ORDER BY item.ordinal
                )
                FROM jsonb_array_elements(
                    requested_compilation_document->'fragments'
                ) WITH ORDINALITY AS item(fragment, ordinal)
            )
        )
    """


def _publication_payload_digest() -> str:
    return """
        encode(public.digest(convert_to(
            jsonb_build_object(
                'compilationDocument', requested_compilation_document,
                'artifactDocument', requested_artifact_document
            )::text,
            'UTF8'
        ), 'sha256'), 'hex')
    """


def _create_acquire_function() -> None:
    artifact_validation = _artifact_validation()
    publication_validation = _publication_artifact_validation()
    payload_digest = _publication_payload_digest()
    op.execute(
        f"""
        CREATE FUNCTION public.context_worker_acquire_file_publication(
            requested_organization_id uuid, requested_job_id uuid,
            requested_service_principal_id uuid, requested_source_ref text,
            requested_resource_ref text, requested_revision_id uuid,
            requested_canonical_text text, requested_content_hash text,
            requested_compilation_digest text, requested_compiler_version text,
            requested_config_version text,
            requested_compilation_document jsonb,
            requested_artifact_document jsonb,
            requested_lease_generation bigint,
            requested_signing_key_version bigint, requested_nonce bytea,
            requested_issued_at timestamptz, requested_expires_at timestamptz
        ) RETURNS TABLE (
            checkpoint text, publication_kind text, stable_revision_id uuid,
            previous_revision_id uuid, fragment_refs text[],
            content_identity_digest text, effect_count smallint, outcome text,
            active_revision_id uuid, reason_digest text
        )
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$
        DECLARE
            job_row public.file_import_job%ROWTYPE;
            recovery_row public.file_publication_recovery%ROWTYPE;
            decision record;
            old_revision uuid;
            active_fragments text[];
            now_at timestamptz;
            next_ordinal bigint;
        BEGIN
            IF SESSION_USER <> '{_WORKER}'
               OR requested_revision_id IS NULL
               OR btrim(requested_resource_ref) = ''
               OR requested_content_hash !~ '^[0-9a-f]{{64}}$'
               OR requested_compilation_digest !~ '^[0-9a-f]{{64}}$'
               OR encode(public.digest(
                    convert_to(requested_canonical_text, 'UTF8'), 'sha256'
                  ), 'hex') <> requested_content_hash
               OR NOT ({artifact_validation})
               OR NOT ({publication_validation})
            THEN RETURN; END IF;
            PERFORM pg_catalog.set_config(
                'app.organization_id', requested_organization_id::text, true
            );
            PERFORM pg_catalog.set_config(
                'app.worker_job_id', requested_job_id::text, true
            );
            now_at := pg_catalog.statement_timestamp();
            SELECT * INTO job_row FROM public.file_import_job AS job
            WHERE job.organization_id = requested_organization_id
              AND job.job_id = requested_job_id
              AND job.service_principal_id = requested_service_principal_id
              AND job.source_id::text = requested_source_ref
              AND job.state IN ('running', 'prepared', 'ready')
              AND job.lease_generation = requested_lease_generation
              AND job.signing_key_version = requested_signing_key_version
              AND job.lease_nonce_digest = public.digest(requested_nonce, 'sha256')
              AND job.lease_issued_at = requested_issued_at
              AND job.lease_expires_at = requested_expires_at
              AND now_at < job.lease_expires_at
              AND EXISTS (
                  SELECT 1 FROM public.service_principal AS principal
                  WHERE principal.organization_id = job.organization_id
                    AND principal.service_principal_id = job.service_principal_id
                    AND principal.workload = job.workload
                    AND principal.worker_audience = job.worker_audience
                    AND principal.operation = job.operation
                    AND principal.enabled IS TRUE
              )
            FOR UPDATE;
            IF job_row.job_id IS NULL THEN RETURN; END IF;

            SELECT * INTO recovery_row
            FROM public.file_publication_recovery AS recovery
            WHERE recovery.organization_id = requested_organization_id
              AND recovery.job_id = requested_job_id;
            IF recovery_row.job_id IS NOT NULL THEN
                IF recovery_row.source_id::text <> requested_source_ref
                   OR recovery_row.resource_ref <> requested_resource_ref
                   OR recovery_row.content_hash <> requested_content_hash
                   OR recovery_row.compilation_digest <>
                        requested_compilation_digest
                   OR recovery_row.compiler_version <>
                        requested_compiler_version
                   OR recovery_row.config_version <> requested_config_version
                   OR recovery_row.publication_payload_digest <> ({payload_digest})
                   OR job_row.resource_ref <> recovery_row.resource_ref
                   OR job_row.revision_id <> recovery_row.revision_id
                THEN RETURN; END IF;
                IF job_row.state = 'running'
                   AND recovery_row.checkpoint <> 'acquired'
                THEN RETURN;
                ELSIF job_row.state = 'prepared'
                   AND recovery_row.checkpoint <> 'prepared'
                THEN RETURN;
                ELSIF job_row.state = 'ready'
                   AND recovery_row.checkpoint <> 'ready'
                THEN RETURN;
                END IF;
                SELECT array_agg(fragment.fragment_ref ORDER BY fragment.ordinal)
                INTO active_fragments
                FROM public.context_fragment AS fragment
                WHERE fragment.organization_id = requested_organization_id
                  AND fragment.resource_ref = recovery_row.resource_ref
                  AND fragment.revision_id = recovery_row.revision_id;
                RETURN QUERY SELECT recovery_row.checkpoint,
                    recovery_row.publication_kind, recovery_row.revision_id,
                    recovery_row.previous_revision_id, active_fragments,
                    recovery_row.content_identity_digest, NULL::smallint,
                    NULL::text, NULL::uuid, NULL::text;
                RETURN;
            END IF;
            IF job_row.state <> 'running'
               OR job_row.resource_ref IS NOT NULL
               OR job_row.revision_id IS NOT NULL
            THEN RETURN; END IF;

            INSERT INTO public.file_resource_ingestion_guard (
                organization_id, source_id, resource_ref, created_at
            ) VALUES (
                requested_organization_id, job_row.source_id,
                requested_resource_ref, now_at
            ) ON CONFLICT (organization_id, resource_ref) DO NOTHING;
            PERFORM 1 FROM public.file_resource_ingestion_guard AS guard
            WHERE guard.organization_id = requested_organization_id
              AND guard.source_id = job_row.source_id
              AND guard.resource_ref = requested_resource_ref
            FOR UPDATE;
            IF NOT FOUND THEN RETURN; END IF;
            IF EXISTS (
                SELECT 1 FROM public.file_publication_recovery AS recovery
                WHERE recovery.organization_id = requested_organization_id
                  AND recovery.resource_ref = requested_resource_ref
                  AND recovery.job_id <> requested_job_id
                  AND recovery.checkpoint <> 'completed'
            ) THEN
                RETURN QUERY SELECT 'contended'::text, NULL::text,
                    NULL::uuid, NULL::uuid, NULL::text[], NULL::text,
                    NULL::smallint, NULL::text, NULL::uuid, NULL::text;
                RETURN;
            END IF;

            SELECT * INTO decision
            FROM public.context_worker_classify_file_import_internal(
                requested_organization_id, requested_job_id,
                requested_service_principal_id, requested_source_ref,
                requested_resource_ref, requested_canonical_text,
                requested_content_hash, requested_compiler_version,
                requested_config_version, requested_signing_key_version,
                requested_nonce, requested_issued_at, requested_expires_at
            );
            IF decision.classification = 'unchanged' THEN
                IF NOT EXISTS (
                    SELECT 1 FROM public.file_revision_snapshot AS snapshot
                    WHERE snapshot.organization_id = requested_organization_id
                      AND snapshot.resource_ref = requested_resource_ref
                      AND snapshot.revision_id = decision.active_revision_id
                      AND snapshot.compilation_digest = requested_compilation_digest
                      AND snapshot.compilation_document IS NOT DISTINCT FROM
                          requested_compilation_document
                      AND (
                          SELECT count(*) FROM public.context_fragment AS fragment
                          WHERE fragment.organization_id = requested_organization_id
                            AND fragment.resource_ref = requested_resource_ref
                            AND fragment.revision_id = decision.active_revision_id
                      ) = jsonb_array_length(requested_artifact_document)
                      AND NOT EXISTS (
                          SELECT 1 FROM jsonb_array_elements(
                              requested_artifact_document
                          ) WITH ORDINALITY AS expected(fragment, ordinal)
                          WHERE NOT EXISTS (
                              SELECT 1 FROM public.context_fragment AS fragment
                              WHERE fragment.organization_id = requested_organization_id
                                AND fragment.resource_ref = requested_resource_ref
                                AND fragment.revision_id = decision.active_revision_id
                                AND fragment.fragment_ref = expected.fragment->>'fragmentRef'
                                AND fragment.ordinal = (expected.ordinal - 1)::integer
                                AND fragment.content = expected.fragment->>'contextualText'
                                AND fragment.projection_kind = 'body'
                          )
                      )
                      AND NOT EXISTS (
                          SELECT 1
                          FROM jsonb_array_elements(requested_artifact_document)
                              AS expected(fragment)
                          CROSS JOIN LATERAL jsonb_array_elements_text(
                              expected.fragment->'searchPhrases'
                          ) AS phrase(value)
                          WHERE NOT EXISTS (
                              SELECT 1 FROM public.exact_phrase_candidate AS candidate
                              WHERE candidate.organization_id = requested_organization_id
                                AND candidate.source_ref = requested_source_ref
                                AND candidate.resource_ref = requested_resource_ref
                                AND candidate.revision_id = decision.active_revision_id
                                AND candidate.fragment_ref = expected.fragment->>'fragmentRef'
                                AND candidate.phrase_digest = encode(public.digest(
                                    convert_to('context-engine.exact-phrase.v1', 'UTF8')
                                    || decode('00', 'hex')
                                    || convert_to(phrase.value, 'UTF8'), 'sha256'
                                ), 'hex')
                          )
                      )
                      AND (
                          SELECT count(*)
                          FROM public.exact_phrase_candidate AS candidate
                          WHERE candidate.organization_id =
                                requested_organization_id
                            AND candidate.resource_ref = requested_resource_ref
                            AND candidate.revision_id =
                                decision.active_revision_id
                      ) = (
                          SELECT count(*)
                          FROM jsonb_array_elements(requested_artifact_document)
                              AS expected(fragment)
                          CROSS JOIN LATERAL jsonb_array_elements_text(
                              expected.fragment->'searchPhrases'
                          ) AS phrase(value)
                      )
                ) THEN
                    RAISE EXCEPTION 'no-op payload is not the active artifact'
                        USING ERRCODE = '22023';
                END IF;
                SELECT COALESCE(max(event.ordinal), -1) + 1 INTO next_ordinal
                FROM public.file_import_job_event AS event
                WHERE event.organization_id = requested_organization_id
                  AND event.job_id = requested_job_id;
                INSERT INTO public.file_import_job_event VALUES (
                    requested_organization_id, requested_job_id, next_ordinal,
                    'unchanged', 'active', job_row.lease_generation,
                    'completed', decision.active_revision_id,
                    decision.reason_digest, now_at
                );
                RETURN QUERY SELECT 'active'::text, 'initial'::text,
                    decision.active_revision_id, NULL::uuid,
                    decision.fragment_refs, decision.content_identity_digest,
                    0::smallint, 'unchanged'::text,
                    decision.active_revision_id, decision.reason_digest;
                RETURN;
            ELSIF decision.classification IS NULL THEN
                RETURN;
            ELSIF decision.classification NOT IN ('publish', 'changed') THEN
                RETURN;
            END IF;
            IF decision.classification = 'changed' THEN
                SELECT resource.active_revision_id INTO old_revision
                FROM public.context_resource AS resource
                WHERE resource.organization_id = requested_organization_id
                  AND resource.resource_ref = requested_resource_ref
                  AND resource.source_ref = requested_source_ref
                  AND resource.active_revision_id IS NOT NULL
                  AND resource.tombstoned IS FALSE;
                IF old_revision IS NULL THEN RETURN; END IF;
            END IF;
            IF NOT EXISTS (
                SELECT 1
                FROM public.file_acquisition AS acquisition
                JOIN public.membership AS audience_membership
                  ON audience_membership.organization_id = acquisition.organization_id
                 AND audience_membership.membership_id = acquisition.audience_membership_id
                 AND audience_membership.membership_version = acquisition.audience_membership_version
                 AND audience_membership.status = 'active'
                 AND audience_membership.valid_from <= now_at
                 AND (audience_membership.valid_until IS NULL OR audience_membership.valid_until > now_at)
                WHERE acquisition.organization_id = requested_organization_id
                  AND acquisition.acquisition_id = job_row.acquisition_id
                  AND acquisition.source_id = job_row.source_id
                  AND (
                      decision.classification = 'publish'
                      OR (
                          EXISTS (
                              SELECT 1 FROM public.resource_access_policy AS access_policy
                              WHERE access_policy.organization_id = acquisition.organization_id
                                AND access_policy.resource_ref = requested_resource_ref
                                AND access_policy.principal_ref = acquisition.audience_principal_ref
                                AND access_policy.access_state = 'allowed'
                          )
                          AND EXISTS (
                              SELECT 1 FROM public.membership_resource_field_right AS field_right
                              WHERE field_right.organization_id = acquisition.organization_id
                                AND field_right.membership_id = acquisition.audience_membership_id
                                AND field_right.membership_version = acquisition.audience_membership_version
                                AND field_right.resource_ref = requested_resource_ref
                                AND field_right.field_ref = 'body'
                          )
                      )
                  )
            ) THEN RETURN; END IF;
            INSERT INTO public.file_publication_recovery (
                organization_id, job_id, source_id, resource_ref, revision_id,
                previous_revision_id, publication_kind, checkpoint,
                content_identity_digest, content_hash, compilation_digest,
                publication_payload_digest, compiler_version, config_version,
                created_at, updated_at
            ) VALUES (
                requested_organization_id, requested_job_id, job_row.source_id,
                requested_resource_ref, requested_revision_id, old_revision,
                CASE decision.classification
                    WHEN 'publish' THEN 'initial' ELSE 'replacement'
                END,
                'acquired', decision.content_identity_digest,
                requested_content_hash, requested_compilation_digest,
                ({payload_digest}),
                requested_compiler_version, requested_config_version,
                now_at, now_at
            );
            UPDATE public.file_import_job
            SET resource_ref = requested_resource_ref,
                revision_id = requested_revision_id
            WHERE organization_id = requested_organization_id
              AND job_id = requested_job_id AND state = 'running';
            IF NOT FOUND THEN RETURN; END IF;
            SELECT COALESCE(max(event.ordinal), -1) + 1 INTO next_ordinal
            FROM public.file_import_job_event AS event
            WHERE event.organization_id = requested_organization_id
              AND event.job_id = requested_job_id;
            INSERT INTO public.file_import_job_event VALUES (
                requested_organization_id, requested_job_id, next_ordinal,
                'acquired', 'acquired', job_row.lease_generation, 'running',
                requested_revision_id, NULL, now_at
            );
            RETURN QUERY SELECT 'acquired'::text,
                CASE decision.classification
                    WHEN 'publish' THEN 'initial' ELSE 'replacement'
                END,
                requested_revision_id, old_revision, NULL::text[],
                decision.content_identity_digest, NULL::smallint, NULL::text,
                NULL::uuid, NULL::text;
        END; $function$
        """
    )


def _create_prepare_function() -> None:
    artifact_validation = _artifact_validation()
    payload_digest = _publication_payload_digest()
    op.execute(
        f"""
        CREATE FUNCTION public.context_worker_prepare_file_publication(
            requested_organization_id uuid, requested_job_id uuid,
            requested_service_principal_id uuid, requested_source_ref text,
            requested_resource_ref text, requested_revision_id uuid,
            requested_canonical_text text,
            requested_compilation_document jsonb,
            requested_artifact_document jsonb,
            requested_lease_generation bigint,
            requested_signing_key_version bigint, requested_nonce bytea,
            requested_issued_at timestamptz, requested_expires_at timestamptz
        ) RETURNS TABLE (checkpoint text, fragment_refs text[])
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$
        DECLARE
            job_row public.file_import_job%ROWTYPE;
            recovery_row public.file_publication_recovery%ROWTYPE;
            first_fragment text;
            prepared_fragments text[];
            now_at timestamptz;
            next_ordinal bigint;
        BEGIN
            IF SESSION_USER <> '{_WORKER}'
               OR NOT ({artifact_validation})
            THEN RETURN; END IF;
            PERFORM pg_catalog.set_config('app.organization_id', requested_organization_id::text, true);
            PERFORM pg_catalog.set_config('app.worker_job_id', requested_job_id::text, true);
            now_at := pg_catalog.statement_timestamp();
            SELECT * INTO job_row FROM public.file_import_job AS job
            WHERE job.organization_id = requested_organization_id
              AND job.job_id = requested_job_id
              AND job.service_principal_id = requested_service_principal_id
              AND job.source_id::text = requested_source_ref
              AND job.state = 'running'
              AND job.resource_ref = requested_resource_ref
              AND job.revision_id = requested_revision_id
              AND job.lease_generation = requested_lease_generation
              AND job.signing_key_version = requested_signing_key_version
              AND job.lease_nonce_digest = public.digest(requested_nonce, 'sha256')
              AND job.lease_issued_at = requested_issued_at
              AND job.lease_expires_at = requested_expires_at
              AND now_at < job.lease_expires_at
              AND EXISTS (SELECT 1 FROM public.service_principal AS principal
                  WHERE principal.organization_id = job.organization_id
                    AND principal.service_principal_id = job.service_principal_id
                    AND principal.workload = job.workload
                    AND principal.worker_audience = job.worker_audience
                    AND principal.operation = job.operation
                    AND principal.enabled IS TRUE)
            FOR UPDATE;
            IF job_row.job_id IS NULL THEN RETURN; END IF;
            SELECT * INTO recovery_row
            FROM public.file_publication_recovery AS recovery
            WHERE recovery.organization_id = requested_organization_id
              AND recovery.job_id = requested_job_id
              AND recovery.resource_ref = requested_resource_ref
              AND recovery.revision_id = requested_revision_id
              AND recovery.checkpoint = 'acquired'
              AND encode(public.digest(
                    convert_to(requested_canonical_text, 'UTF8'), 'sha256'
                  ), 'hex') = recovery.content_hash
              AND (
                  (recovery.compiler_version = 'context-engine-markdown-v1'
                   AND requested_compilation_document IS NULL)
                  OR
                  (recovery.compiler_version = 'context-engine-markdown-v2'
                   AND requested_compilation_document->>'compilationDigest'
                       = recovery.compilation_digest)
              )
              AND recovery.publication_payload_digest = ({payload_digest})
            FOR UPDATE;
            IF recovery_row.job_id IS NULL THEN RETURN; END IF;
            PERFORM 1 FROM public.file_resource_ingestion_guard AS guard
            WHERE guard.organization_id = requested_organization_id
              AND guard.source_id = job_row.source_id
              AND guard.resource_ref = requested_resource_ref
            FOR UPDATE;
            IF NOT FOUND THEN RETURN; END IF;
            IF NOT EXISTS (
                SELECT 1 FROM public.file_acquisition AS acquisition
                JOIN public.membership AS membership
                  ON membership.organization_id = acquisition.organization_id
                 AND membership.membership_id = acquisition.audience_membership_id
                 AND membership.membership_version = acquisition.audience_membership_version
                 AND membership.status = 'active'
                 AND membership.valid_from <= now_at
                 AND (membership.valid_until IS NULL OR membership.valid_until > now_at)
                WHERE acquisition.organization_id = requested_organization_id
                  AND acquisition.acquisition_id = job_row.acquisition_id
                  AND acquisition.source_id = job_row.source_id
                  AND (
                      recovery_row.publication_kind = 'initial'
                      OR (
                          EXISTS (
                              SELECT 1 FROM public.resource_access_policy AS access_policy
                              WHERE access_policy.organization_id = acquisition.organization_id
                                AND access_policy.resource_ref = requested_resource_ref
                                AND access_policy.principal_ref = acquisition.audience_principal_ref
                                AND access_policy.access_state = 'allowed'
                          )
                          AND EXISTS (
                              SELECT 1 FROM public.membership_resource_field_right AS field_right
                              WHERE field_right.organization_id = acquisition.organization_id
                                AND field_right.membership_id = acquisition.audience_membership_id
                                AND field_right.membership_version = acquisition.audience_membership_version
                                AND field_right.resource_ref = requested_resource_ref
                                AND field_right.field_ref = 'body'
                          )
                      )
                  )
            ) THEN RETURN; END IF;
            SET CONSTRAINTS ALL DEFERRED;
            IF recovery_row.publication_kind = 'initial' THEN
                INSERT INTO public.context_resource (
                    organization_id, resource_ref, source_ref,
                    active_revision_id, tombstoned
                ) VALUES (
                    requested_organization_id, requested_resource_ref,
                    requested_source_ref, NULL, false
                ) ON CONFLICT (organization_id, resource_ref) DO NOTHING;
                IF NOT EXISTS (
                    SELECT 1 FROM public.context_resource AS resource
                    WHERE resource.organization_id = requested_organization_id
                      AND resource.resource_ref = requested_resource_ref
                      AND resource.source_ref = requested_source_ref
                      AND resource.active_revision_id IS NULL
                      AND resource.tombstoned IS FALSE
                ) THEN RETURN; END IF;
            ELSIF NOT EXISTS (
                SELECT 1 FROM public.context_resource AS resource
                WHERE resource.organization_id = requested_organization_id
                  AND resource.resource_ref = requested_resource_ref
                  AND resource.source_ref = requested_source_ref
                  AND resource.active_revision_id = recovery_row.previous_revision_id
                  AND resource.tombstoned IS FALSE
            ) THEN RETURN;
            END IF;
            INSERT INTO public.context_revision VALUES (
                requested_organization_id, requested_resource_ref,
                requested_revision_id
            );
            INSERT INTO public.file_revision_snapshot (
                organization_id, resource_ref, revision_id, acquisition_id,
                canonical_text, content_hash, compilation_digest,
                compiler_version, config_version, compilation_document
            ) VALUES (
                requested_organization_id, requested_resource_ref,
                requested_revision_id, job_row.acquisition_id,
                requested_canonical_text,
                recovery_row.content_hash, recovery_row.compilation_digest,
                recovery_row.compiler_version, recovery_row.config_version,
                requested_compilation_document
            );
            INSERT INTO public.context_fragment (
                organization_id, resource_ref, revision_id, fragment_ref,
                ordinal, content, projection_kind
            )
            SELECT requested_organization_id, requested_resource_ref,
                   requested_revision_id, item.fragment->>'fragmentRef',
                   (item.ordinal - 1)::integer,
                   item.fragment->>'contextualText', 'body'
            FROM jsonb_array_elements(requested_artifact_document)
                WITH ORDINALITY AS item(fragment, ordinal)
            ORDER BY item.ordinal;
            INSERT INTO public.revision_publication_event VALUES (
                requested_organization_id, requested_resource_ref,
                requested_revision_id, 0, 'prepared', now_at
            );
            IF recovery_row.publication_kind = 'initial' THEN
                INSERT INTO public.resource_access_policy
                SELECT requested_organization_id, requested_resource_ref,
                       acquisition.audience_principal_ref, 1, 'allowed', NULL
                FROM public.file_acquisition AS acquisition
                WHERE acquisition.organization_id = requested_organization_id
                  AND acquisition.acquisition_id = job_row.acquisition_id;
                INSERT INTO public.membership_resource_field_right
                SELECT requested_organization_id,
                       acquisition.audience_membership_id,
                       acquisition.audience_membership_version,
                       requested_resource_ref, 'body'
                FROM public.file_acquisition AS acquisition
                WHERE acquisition.organization_id = requested_organization_id
                  AND acquisition.acquisition_id = job_row.acquisition_id;
            END IF;
            SELECT array_agg(fragment_ref ORDER BY ordinal),
                   (array_agg(fragment_ref ORDER BY ordinal))[1]
            INTO prepared_fragments, first_fragment
            FROM public.context_fragment
            WHERE organization_id = requested_organization_id
              AND resource_ref = requested_resource_ref
              AND revision_id = requested_revision_id;
            UPDATE public.file_publication_recovery AS recovery
            SET checkpoint = 'prepared', updated_at = now_at
            WHERE recovery.organization_id = requested_organization_id
              AND recovery.job_id = requested_job_id
              AND recovery.checkpoint = 'acquired';
            IF NOT FOUND THEN RETURN; END IF;
            UPDATE public.file_import_job
            SET state = 'prepared', fragment_ref = first_fragment
            WHERE organization_id = requested_organization_id
              AND job_id = requested_job_id AND state = 'running';
            IF NOT FOUND THEN RETURN; END IF;
            SELECT COALESCE(max(event.ordinal), -1) + 1 INTO next_ordinal
            FROM public.file_import_job_event AS event
            WHERE event.organization_id = requested_organization_id
              AND event.job_id = requested_job_id;
            INSERT INTO public.file_import_job_event VALUES (
                requested_organization_id, requested_job_id, next_ordinal,
                'prepared', 'prepared', job_row.lease_generation, 'prepared',
                requested_revision_id, NULL, now_at
            );
            RETURN QUERY SELECT 'prepared'::text, prepared_fragments;
        END; $function$
        """
    )


def _create_index_function() -> None:
    artifact_validation = _artifact_validation()
    payload_digest = _publication_payload_digest()
    op.execute(
        f"""
        CREATE FUNCTION public.context_worker_index_file_publication(
            requested_organization_id uuid, requested_job_id uuid,
            requested_service_principal_id uuid, requested_source_ref text,
            requested_resource_ref text, requested_revision_id uuid,
            requested_canonical_text text,
            requested_compilation_document jsonb,
            requested_artifact_document jsonb,
            requested_lease_generation bigint,
            requested_signing_key_version bigint, requested_nonce bytea,
            requested_issued_at timestamptz, requested_expires_at timestamptz
        ) RETURNS TABLE (checkpoint text, fragment_refs text[])
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$
        DECLARE
            job_row public.file_import_job%ROWTYPE;
            recovery_row public.file_publication_recovery%ROWTYPE;
            indexed_fragments text[];
            now_at timestamptz;
            next_ordinal bigint;
        BEGIN
            IF SESSION_USER <> '{_WORKER}' OR NOT ({artifact_validation})
            THEN RETURN; END IF;
            PERFORM pg_catalog.set_config('app.organization_id', requested_organization_id::text, true);
            PERFORM pg_catalog.set_config('app.worker_job_id', requested_job_id::text, true);
            now_at := pg_catalog.statement_timestamp();
            SELECT * INTO job_row FROM public.file_import_job AS job
            WHERE job.organization_id = requested_organization_id
              AND job.job_id = requested_job_id
              AND job.service_principal_id = requested_service_principal_id
              AND job.source_id::text = requested_source_ref
              AND job.state = 'prepared'
              AND job.resource_ref = requested_resource_ref
              AND job.revision_id = requested_revision_id
              AND job.lease_generation = requested_lease_generation
              AND job.signing_key_version = requested_signing_key_version
              AND job.lease_nonce_digest = public.digest(requested_nonce, 'sha256')
              AND job.lease_issued_at = requested_issued_at
              AND job.lease_expires_at = requested_expires_at
              AND now_at < job.lease_expires_at
              AND EXISTS (SELECT 1 FROM public.service_principal AS principal
                  WHERE principal.organization_id = job.organization_id
                    AND principal.service_principal_id = job.service_principal_id
                    AND principal.workload = job.workload
                    AND principal.worker_audience = job.worker_audience
                    AND principal.operation = job.operation
                    AND principal.enabled IS TRUE)
            FOR UPDATE;
            IF job_row.job_id IS NULL THEN RETURN; END IF;
            SELECT * INTO recovery_row
            FROM public.file_publication_recovery AS recovery
            WHERE recovery.organization_id = requested_organization_id
              AND recovery.job_id = requested_job_id
              AND recovery.resource_ref = requested_resource_ref
              AND recovery.revision_id = requested_revision_id
              AND recovery.checkpoint = 'prepared'
              AND encode(public.digest(
                    convert_to(requested_canonical_text, 'UTF8'), 'sha256'
                  ), 'hex') = recovery.content_hash
              AND recovery.publication_payload_digest = ({payload_digest})
            FOR UPDATE;
            IF recovery_row.job_id IS NULL
               OR NOT EXISTS (
                    SELECT 1 FROM public.file_revision_snapshot AS snapshot
                    WHERE snapshot.organization_id = requested_organization_id
                      AND snapshot.resource_ref = requested_resource_ref
                      AND snapshot.revision_id = requested_revision_id
                      AND snapshot.content_hash = recovery_row.content_hash
                      AND snapshot.canonical_text = requested_canonical_text
                      AND snapshot.compilation_digest = recovery_row.compilation_digest
                      AND snapshot.compilation_document IS NOT DISTINCT FROM requested_compilation_document
                      AND (
                          SELECT count(*) FROM public.context_fragment AS fragment
                          WHERE fragment.organization_id = requested_organization_id
                            AND fragment.resource_ref = requested_resource_ref
                            AND fragment.revision_id = requested_revision_id
                      ) = jsonb_array_length(requested_artifact_document)
               )
            THEN RETURN; END IF;
            INSERT INTO public.exact_phrase_candidate (
                organization_id, phrase_digest, source_ref, resource_ref,
                revision_id, fragment_ref
            )
            SELECT requested_organization_id,
                   encode(public.digest(
                       convert_to('context-engine.exact-phrase.v1', 'UTF8')
                       || decode('00', 'hex')
                       || convert_to(phrase.value, 'UTF8'), 'sha256'
                   ), 'hex'), requested_source_ref, requested_resource_ref,
                   requested_revision_id, item.fragment->>'fragmentRef'
            FROM jsonb_array_elements(requested_artifact_document)
                WITH ORDINALITY AS item(fragment, fragment_ordinal)
            CROSS JOIN LATERAL jsonb_array_elements_text(
                item.fragment->'searchPhrases'
            ) WITH ORDINALITY AS phrase(value, phrase_ordinal)
            ORDER BY item.fragment_ordinal, phrase.phrase_ordinal;
            INSERT INTO public.revision_publication_event VALUES (
                requested_organization_id, requested_resource_ref,
                requested_revision_id, 1, 'indexed', now_at
            );
            SELECT array_agg(fragment_ref ORDER BY ordinal)
            INTO indexed_fragments
            FROM public.context_fragment
            WHERE organization_id = requested_organization_id
              AND resource_ref = requested_resource_ref
              AND revision_id = requested_revision_id;
            IF recovery_row.publication_kind = 'replacement' THEN
                INSERT INTO public.file_revision_replacement_plan (
                    organization_id, source_id, resource_ref,
                    previous_revision_id, replacement_revision_id,
                    acquisition_id, job_id, content_identity_digest, prepared_at
                ) VALUES (
                    requested_organization_id, job_row.source_id,
                    requested_resource_ref, recovery_row.previous_revision_id,
                    requested_revision_id, job_row.acquisition_id,
                    requested_job_id, recovery_row.content_identity_digest,
                    now_at
                );
            END IF;
            UPDATE public.file_publication_recovery AS recovery
            SET checkpoint = 'ready', updated_at = now_at
            WHERE recovery.organization_id = requested_organization_id
              AND recovery.job_id = requested_job_id
              AND recovery.checkpoint = 'prepared';
            IF NOT FOUND THEN RETURN; END IF;
            UPDATE public.file_import_job
            SET state = 'ready'
            WHERE organization_id = requested_organization_id
              AND job_id = requested_job_id AND state = 'prepared';
            IF NOT FOUND THEN RETURN; END IF;
            SELECT COALESCE(max(event.ordinal), -1) + 1 INTO next_ordinal
            FROM public.file_import_job_event AS event
            WHERE event.organization_id = requested_organization_id
              AND event.job_id = requested_job_id;
            INSERT INTO public.file_import_job_event VALUES (
                requested_organization_id, requested_job_id, next_ordinal,
                'indexed', 'indexed', job_row.lease_generation, 'ready',
                requested_revision_id, NULL, now_at
            );
            RETURN QUERY SELECT 'ready'::text, indexed_fragments;
        END; $function$
        """
    )


def _create_activate_function() -> None:
    op.execute(
        f"""
        CREATE FUNCTION public.context_worker_activate_recoverable_file_publication(
            requested_organization_id uuid, requested_job_id uuid,
            requested_service_principal_id uuid, requested_source_ref text,
            requested_resource_ref text, requested_revision_id uuid,
            requested_lease_generation bigint,
            requested_signing_key_version bigint, requested_nonce bytea,
            requested_issued_at timestamptz, requested_expires_at timestamptz
        ) RETURNS TABLE (
            effect_count smallint, outcome text, active_revision_id uuid,
            fragment_refs text[], content_identity_digest text,
            reason_digest text
        )
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$
        DECLARE
            job_row public.file_import_job%ROWTYPE;
            recovery_row public.file_publication_recovery%ROWTYPE;
            activated record;
            active_fragments text[];
            now_at timestamptz;
            next_ordinal bigint;
        BEGIN
            IF SESSION_USER <> '{_WORKER}' THEN RETURN; END IF;
            PERFORM pg_catalog.set_config('app.organization_id', requested_organization_id::text, true);
            PERFORM pg_catalog.set_config('app.worker_job_id', requested_job_id::text, true);
            now_at := pg_catalog.statement_timestamp();
            SELECT * INTO job_row FROM public.file_import_job AS job
            WHERE job.organization_id = requested_organization_id
              AND job.job_id = requested_job_id
              AND job.service_principal_id = requested_service_principal_id
              AND job.source_id::text = requested_source_ref
              AND job.state = 'ready'
              AND job.resource_ref = requested_resource_ref
              AND job.revision_id = requested_revision_id
              AND job.lease_generation = requested_lease_generation
              AND job.signing_key_version = requested_signing_key_version
              AND job.lease_nonce_digest = public.digest(requested_nonce, 'sha256')
              AND job.lease_issued_at = requested_issued_at
              AND job.lease_expires_at = requested_expires_at
              AND now_at < job.lease_expires_at
              AND EXISTS (
                  SELECT 1 FROM public.service_principal AS principal
                  WHERE principal.organization_id = job.organization_id
                    AND principal.service_principal_id = job.service_principal_id
                    AND principal.workload = job.workload
                    AND principal.worker_audience = job.worker_audience
                    AND principal.operation = job.operation
                    AND principal.enabled IS TRUE
              )
            FOR UPDATE;
            IF job_row.job_id IS NULL THEN RETURN; END IF;
            SELECT * INTO recovery_row
            FROM public.file_publication_recovery AS recovery
            WHERE recovery.organization_id = requested_organization_id
              AND recovery.job_id = requested_job_id
              AND recovery.resource_ref = requested_resource_ref
              AND recovery.revision_id = requested_revision_id
              AND recovery.checkpoint = 'ready'
            FOR UPDATE;
            IF recovery_row.job_id IS NULL THEN RETURN; END IF;
            IF recovery_row.publication_kind = 'replacement' THEN
                SELECT * INTO activated
                FROM public.context_worker_activate_file_replacement(
                    requested_organization_id, requested_job_id,
                    requested_service_principal_id, requested_source_ref,
                    requested_resource_ref,
                    recovery_row.previous_revision_id,
                    requested_revision_id, requested_signing_key_version,
                    requested_nonce, requested_issued_at, requested_expires_at
                );
                IF activated.effect_count IS DISTINCT FROM 1 THEN RETURN; END IF;
                effect_count := activated.effect_count;
                outcome := activated.outcome;
                active_revision_id := activated.active_revision_id;
                fragment_refs := activated.fragment_refs;
                content_identity_digest := activated.content_identity_digest;
                reason_digest := activated.reason_digest;
            ELSE
                PERFORM pg_catalog.pg_advisory_xact_lock(
                    pg_catalog.hashtextextended(
                        'context-engine.file-publication:'
                        || requested_organization_id::text, 0
                    )
                );
                PERFORM 1 FROM public.file_resource_ingestion_guard AS guard
                WHERE guard.organization_id = requested_organization_id
                  AND guard.source_id = job_row.source_id
                  AND guard.resource_ref = requested_resource_ref
                FOR UPDATE;
                IF NOT FOUND OR NOT EXISTS (
                    SELECT 1 FROM public.context_resource AS resource
                    WHERE resource.organization_id = requested_organization_id
                      AND resource.resource_ref = requested_resource_ref
                      AND resource.source_ref = requested_source_ref
                      AND resource.active_revision_id IS NULL
                      AND resource.tombstoned IS FALSE
                      AND EXISTS (
                          SELECT 1
                          FROM public.file_acquisition AS acquisition
                          JOIN public.membership AS audience_membership
                            ON audience_membership.organization_id = acquisition.organization_id
                           AND audience_membership.membership_id = acquisition.audience_membership_id
                           AND audience_membership.membership_version = acquisition.audience_membership_version
                           AND audience_membership.status = 'active'
                           AND audience_membership.valid_from <= now_at
                           AND (audience_membership.valid_until IS NULL OR audience_membership.valid_until > now_at)
                          JOIN public.resource_access_policy AS access_policy
                            ON access_policy.organization_id = acquisition.organization_id
                           AND access_policy.resource_ref = requested_resource_ref
                           AND access_policy.principal_ref = acquisition.audience_principal_ref
                           AND access_policy.access_state = 'allowed'
                          JOIN public.membership_resource_field_right AS field_right
                            ON field_right.organization_id = acquisition.organization_id
                           AND field_right.membership_id = acquisition.audience_membership_id
                           AND field_right.membership_version = acquisition.audience_membership_version
                           AND field_right.resource_ref = requested_resource_ref
                           AND field_right.field_ref = 'body'
                          WHERE acquisition.organization_id = requested_organization_id
                            AND acquisition.acquisition_id = job_row.acquisition_id
                            AND acquisition.source_id = job_row.source_id
                      )
                      AND (
                          SELECT array_agg(event.state ORDER BY event.ordinal)
                          FROM public.revision_publication_event AS event
                          WHERE event.organization_id = requested_organization_id
                            AND event.resource_ref = requested_resource_ref
                            AND event.revision_id = requested_revision_id
                      ) = ARRAY['prepared', 'indexed']::text[]
                      AND NOT EXISTS (
                          SELECT 1 FROM public.context_fragment AS fragment
                          WHERE fragment.organization_id = requested_organization_id
                            AND fragment.resource_ref = requested_resource_ref
                            AND fragment.revision_id = requested_revision_id
                            AND NOT EXISTS (
                                SELECT 1 FROM public.exact_phrase_candidate AS candidate
                                WHERE candidate.organization_id = fragment.organization_id
                                  AND candidate.resource_ref = fragment.resource_ref
                                  AND candidate.revision_id = fragment.revision_id
                                  AND candidate.fragment_ref = fragment.fragment_ref
                            )
                      )
                ) THEN RETURN; END IF;
                SELECT array_agg(fragment_ref ORDER BY ordinal)
                INTO active_fragments FROM public.context_fragment
                WHERE organization_id = requested_organization_id
                  AND resource_ref = requested_resource_ref
                  AND revision_id = requested_revision_id;
                UPDATE public.context_resource AS resource
                SET active_revision_id = requested_revision_id
                WHERE resource.organization_id = requested_organization_id
                  AND resource.resource_ref = requested_resource_ref
                  AND resource.active_revision_id IS NULL;
                IF NOT FOUND THEN RETURN; END IF;
                INSERT INTO public.revision_publication_event VALUES (
                    requested_organization_id, requested_resource_ref,
                    requested_revision_id, 2, 'active', now_at
                );
                UPDATE public.file_import_job
                SET state = 'completed', completed_at = now_at, effect_count = 1
                WHERE organization_id = requested_organization_id
                  AND job_id = requested_job_id AND state = 'ready';
                IF NOT FOUND THEN RETURN; END IF;
                effect_count := 1;
                outcome := 'published';
                active_revision_id := requested_revision_id;
                fragment_refs := active_fragments;
                content_identity_digest := recovery_row.content_identity_digest;
                reason_digest := NULL;
            END IF;
            UPDATE public.file_publication_recovery AS recovery
            SET checkpoint = 'completed', updated_at = now_at
            WHERE recovery.organization_id = requested_organization_id
              AND recovery.job_id = requested_job_id
              AND recovery.checkpoint = 'ready';
            IF NOT FOUND THEN RETURN; END IF;
            SELECT COALESCE(max(event.ordinal), -1) + 1 INTO next_ordinal
            FROM public.file_import_job_event AS event
            WHERE event.organization_id = requested_organization_id
              AND event.job_id = requested_job_id;
            INSERT INTO public.file_import_job_event VALUES (
                requested_organization_id, requested_job_id, next_ordinal,
                'active', 'active', job_row.lease_generation, 'completed',
                requested_revision_id, NULL, now_at
            );
            RETURN NEXT;
        END; $function$
        """
    )


def _create_interrupt_function() -> None:
    op.execute(
        f"""
        CREATE FUNCTION public.context_worker_record_file_import_interruption(
            requested_organization_id uuid, requested_job_id uuid,
            requested_service_principal_id uuid, requested_source_ref text,
            requested_boundary text, requested_lease_generation bigint,
            requested_signing_key_version bigint,
            requested_nonce bytea, requested_issued_at timestamptz,
            requested_expires_at timestamptz
        ) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$
        DECLARE
            job_row public.file_import_job%ROWTYPE;
            expected_state text;
            expected_checkpoint text;
            next_ordinal bigint;
            now_at timestamptz;
        BEGIN
            IF SESSION_USER <> '{_WORKER}'
               OR requested_boundary NOT IN ('acquired', 'prepared', 'indexed')
            THEN RETURN false; END IF;
            expected_state := CASE requested_boundary
                WHEN 'acquired' THEN 'running'
                WHEN 'prepared' THEN 'prepared'
                ELSE 'ready'
            END;
            expected_checkpoint := CASE requested_boundary
                WHEN 'indexed' THEN 'ready' ELSE requested_boundary
            END;
            PERFORM pg_catalog.set_config('app.organization_id', requested_organization_id::text, true);
            PERFORM pg_catalog.set_config('app.worker_job_id', requested_job_id::text, true);
            now_at := pg_catalog.statement_timestamp();
            SELECT * INTO job_row FROM public.file_import_job AS job
            WHERE job.organization_id = requested_organization_id
              AND job.job_id = requested_job_id
              AND job.service_principal_id = requested_service_principal_id
              AND job.source_id::text = requested_source_ref
              AND job.state = expected_state
              AND job.lease_generation = requested_lease_generation
              AND job.signing_key_version = requested_signing_key_version
              AND job.lease_nonce_digest = public.digest(requested_nonce, 'sha256')
              AND job.lease_issued_at = requested_issued_at
              AND job.lease_expires_at = requested_expires_at
              AND now_at < job.lease_expires_at
              AND EXISTS (
                  SELECT 1 FROM public.file_publication_recovery AS recovery
                  WHERE recovery.organization_id = job.organization_id
                    AND recovery.job_id = job.job_id
                    AND recovery.checkpoint = expected_checkpoint
              )
            FOR UPDATE;
            IF job_row.job_id IS NULL THEN RETURN false; END IF;
            SELECT COALESCE(max(event.ordinal), -1) + 1 INTO next_ordinal
            FROM public.file_import_job_event AS event
            WHERE event.organization_id = requested_organization_id
              AND event.job_id = requested_job_id;
            INSERT INTO public.file_import_job_event VALUES (
                requested_organization_id, requested_job_id, next_ordinal,
                'interrupted', requested_boundary, job_row.lease_generation,
                job_row.state, job_row.revision_id,
                encode(public.digest(
                    convert_to('context-engine.file-interruption.v1', 'UTF8')
                    || decode('00', 'hex')
                    || uuid_send(requested_organization_id)
                    || uuid_send(requested_job_id)
                    || int8send(job_row.lease_generation)
                    || convert_to(requested_boundary, 'UTF8'), 'sha256'
                ), 'hex'), now_at
            );
            RETURN true;
        END; $function$
        """
    )


def _restore_non_recoverable_lease_functions() -> None:
    """Restore the Issue #23 lease behavior before removing recovery columns."""

    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.context_worker_issue_file_import_lease(
            requested_organization_id uuid, requested_job_id uuid,
            requested_service_principal_id uuid, requested_source_ref text,
            requested_signing_key_version bigint, requested_nonce bytea,
            requested_ttl_seconds integer
        ) RETURNS TABLE (issued_at timestamptz, expires_at timestamptz)
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$
        DECLARE now_at timestamptz;
        BEGIN
            IF SESSION_USER <> '{_CONTROL}'
               OR requested_signing_key_version NOT BETWEEN 1 AND {_MAX_BIGINT}
               OR pg_catalog.octet_length(requested_nonce) <> 32
               OR requested_ttl_seconds NOT BETWEEN 1 AND {_MAX_TTL}
            THEN RETURN; END IF;
            PERFORM pg_catalog.set_config(
                'app.organization_id', requested_organization_id::text, true
            );
            PERFORM pg_catalog.set_config(
                'app.worker_job_id', requested_job_id::text, true
            );
            now_at := pg_catalog.date_trunc(
                'second', pg_catalog.transaction_timestamp()
            );
            UPDATE public.file_import_job AS job
            SET state = 'leased',
                signing_key_version = requested_signing_key_version,
                lease_nonce_digest = public.digest(requested_nonce, 'sha256'),
                lease_issued_at = now_at,
                lease_expires_at = now_at + pg_catalog.make_interval(
                    secs => requested_ttl_seconds
                )
            WHERE job.organization_id = requested_organization_id
              AND job.job_id = requested_job_id
              AND job.service_principal_id = requested_service_principal_id
              AND job.source_id::text = requested_source_ref
              AND job.state = 'available'
              AND EXISTS (
                  SELECT 1 FROM public.service_principal AS principal
                  WHERE principal.organization_id = job.organization_id
                    AND principal.service_principal_id = job.service_principal_id
                    AND principal.workload = job.workload
                    AND principal.worker_audience = job.worker_audience
                    AND principal.operation = job.operation
                    AND principal.enabled IS TRUE
              )
            RETURNING job.lease_issued_at, job.lease_expires_at
            INTO issued_at, expires_at;
            IF issued_at IS NOT NULL THEN RETURN NEXT; END IF;
        END; $function$
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.context_worker_redeem_file_import(
            requested_organization_id uuid, requested_job_id uuid,
            requested_service_principal_id uuid, requested_source_ref text,
            requested_signing_key_version bigint, requested_nonce bytea,
            requested_issued_at timestamptz,
            requested_expires_at timestamptz
        ) RETURNS TABLE (
            source_ref text, root_ref text, relative_path text,
            audience_principal_ref text, audience_membership_id uuid,
            audience_membership_version bigint, acquisition_id uuid
        )
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$
        DECLARE redeemed_at timestamptz;
        BEGIN
            IF SESSION_USER <> '{_WORKER}' THEN RETURN; END IF;
            PERFORM pg_catalog.set_config(
                'app.organization_id', requested_organization_id::text, true
            );
            PERFORM pg_catalog.set_config(
                'app.worker_job_id', requested_job_id::text, true
            );
            redeemed_at := pg_catalog.statement_timestamp();
            UPDATE public.file_import_job AS job
            SET state = 'running', lease_redeemed_at = redeemed_at
            WHERE job.organization_id = requested_organization_id
              AND job.job_id = requested_job_id
              AND job.service_principal_id = requested_service_principal_id
              AND job.source_id::text = requested_source_ref
              AND job.state = 'leased'
              AND job.signing_key_version = requested_signing_key_version
              AND job.lease_nonce_digest = public.digest(requested_nonce, 'sha256')
              AND job.lease_issued_at = requested_issued_at
              AND job.lease_expires_at = requested_expires_at
              AND redeemed_at >= job.lease_issued_at
              AND redeemed_at < job.lease_expires_at
              AND EXISTS (
                  SELECT 1 FROM public.service_principal AS principal
                  WHERE principal.organization_id = job.organization_id
                    AND principal.service_principal_id = job.service_principal_id
                    AND principal.workload = job.workload
                    AND principal.worker_audience = job.worker_audience
                    AND principal.operation = job.operation
                    AND principal.enabled IS TRUE
              );
            IF NOT FOUND THEN RETURN; END IF;
            RETURN QUERY
            SELECT job.source_id::text, version.root_ref,
                   acquisition.relative_path,
                   acquisition.audience_principal_ref,
                   acquisition.audience_membership_id,
                   acquisition.audience_membership_version,
                   acquisition.acquisition_id
            FROM public.file_import_job AS job
            JOIN public.file_acquisition AS acquisition
              ON acquisition.organization_id = job.organization_id
             AND acquisition.acquisition_id = job.acquisition_id
            JOIN public.source_version AS version
              ON version.organization_id = acquisition.organization_id
             AND version.source_id = acquisition.source_id
             AND version.version_id = acquisition.source_version_id
            WHERE job.organization_id = requested_organization_id
              AND job.job_id = requested_job_id;
        END; $function$
        """
    )


def downgrade() -> None:
    """Remove recovery only when no resumable publication would be lost."""

    op.execute(
        "LOCK TABLE file_publication_recovery, file_import_job_event "
        "IN ACCESS EXCLUSIVE MODE"
    )
    bind = op.get_bind()
    if bind.execute(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM file_publication_recovery "
            "WHERE checkpoint <> 'completed' "
            "UNION ALL "
            "SELECT 1 FROM file_import_job_event AS event "
            "JOIN file_import_job AS job "
            "ON job.organization_id = event.organization_id "
            "AND job.job_id = event.job_id "
            "WHERE job.state <> 'completed'"
            ")"
        )
    ).scalar_one():
        raise RuntimeError(
            "File recovery downgrade requires no resumable recovery rows"
        )
    for name, signature in (
        ("context_worker_record_file_import_interruption", _INTERRUPT_SIGNATURE),
        ("context_worker_activate_recoverable_file_publication", _ACTIVATE_SIGNATURE),
        ("context_worker_index_file_publication", _STEP_SIGNATURE),
        ("context_worker_prepare_file_publication", _STEP_SIGNATURE),
        ("context_worker_acquire_file_publication", _ACQUIRE_SIGNATURE),
        ("context_worker_fail_file_import", _FAIL_SIGNATURE),
        ("context_worker_redeem_file_import", _REDEEM_SIGNATURE),
        ("context_worker_issue_file_import_lease", _ISSUE_SIGNATURE),
        ("context_worker_activate_file_replacement", _LEGACY_ACTIVATE_GENERATION_SIGNATURE),
        ("context_worker_stage_structural_file_replacement", _STAGE_V2_GENERATION_SIGNATURE),
        ("context_worker_stage_file_replacement", _STAGE_V1_GENERATION_SIGNATURE),
        ("context_worker_publish_structural_file_import_v2", _PUBLISH_V2_GENERATION_SIGNATURE),
        ("context_worker_publish_file_import_v2", _PUBLISH_V1_GENERATION_SIGNATURE),
    ):
        op.execute(f"DROP FUNCTION public.{name}{signature}")
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    _restore_non_recoverable_lease_functions()
    _restore_non_recoverable_fail_function()
    for name, signature in (
        ("context_worker_publish_file_import_v2", _PUBLISH_V1_SIGNATURE),
        ("context_worker_publish_structural_file_import_v2", _PUBLISH_V2_SIGNATURE),
        ("context_worker_stage_file_replacement", _STAGE_V1_SIGNATURE),
        ("context_worker_stage_structural_file_replacement", _STAGE_V2_SIGNATURE),
        ("context_worker_activate_file_replacement", _LEGACY_ACTIVATE_SIGNATURE),
    ):
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{name}{signature} TO {_WORKER}")
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")
    op.drop_table("file_import_job_event")
    op.drop_table("file_publication_recovery")
    op.drop_constraint(
        "ck_file_import_job_recovery_from_state", "file_import_job", type_="check"
    )
    op.drop_constraint(
        "ck_file_import_job_state_consistency", "file_import_job", type_="check"
    )
    op.drop_constraint("ck_file_import_job_state", "file_import_job", type_="check")
    op.drop_column("file_import_job", "recovery_from_state")
    op.drop_column("file_import_job", "lease_generation")
    op.create_check_constraint(
        "ck_file_import_job_state",
        "file_import_job",
        "state IN ('available', 'leased', 'running', 'ready', 'failed', 'completed')",
    )
    op.create_check_constraint(
        "ck_file_import_job_state_consistency",
        "file_import_job",
        "(state = 'available' AND signing_key_version IS NULL AND lease_nonce_digest IS NULL AND lease_issued_at IS NULL AND lease_expires_at IS NULL AND lease_redeemed_at IS NULL AND failed_at IS NULL AND completed_at IS NULL AND resource_ref IS NULL AND revision_id IS NULL AND fragment_ref IS NULL AND effect_count = 0) OR "
        "(state = 'leased' AND signing_key_version > 0 AND octet_length(lease_nonce_digest) = 32 AND lease_issued_at IS NOT NULL AND lease_expires_at > lease_issued_at AND lease_redeemed_at IS NULL AND failed_at IS NULL AND completed_at IS NULL AND resource_ref IS NULL AND revision_id IS NULL AND fragment_ref IS NULL AND effect_count = 0) OR "
        "(state = 'running' AND signing_key_version > 0 AND octet_length(lease_nonce_digest) = 32 AND lease_issued_at IS NOT NULL AND lease_expires_at > lease_issued_at AND lease_redeemed_at >= lease_issued_at AND failed_at IS NULL AND completed_at IS NULL AND resource_ref IS NULL AND revision_id IS NULL AND fragment_ref IS NULL AND effect_count = 0) OR "
        "(state = 'ready' AND signing_key_version > 0 AND octet_length(lease_nonce_digest) = 32 AND lease_issued_at IS NOT NULL AND lease_expires_at > lease_issued_at AND lease_redeemed_at >= lease_issued_at AND failed_at IS NULL AND completed_at IS NULL AND resource_ref IS NOT NULL AND revision_id IS NOT NULL AND fragment_ref IS NOT NULL AND effect_count = 0) OR "
        "(state = 'failed' AND signing_key_version > 0 AND octet_length(lease_nonce_digest) = 32 AND lease_issued_at IS NOT NULL AND lease_expires_at > lease_issued_at AND lease_redeemed_at >= lease_issued_at AND failed_at >= lease_redeemed_at AND completed_at IS NULL AND resource_ref IS NULL AND revision_id IS NULL AND fragment_ref IS NULL AND effect_count = 0) OR "
        "(state = 'completed' AND signing_key_version > 0 AND octet_length(lease_nonce_digest) = 32 AND lease_issued_at IS NOT NULL AND lease_expires_at > lease_issued_at AND lease_redeemed_at >= lease_issued_at AND failed_at IS NULL AND completed_at >= lease_redeemed_at AND resource_ref IS NOT NULL AND revision_id IS NOT NULL AND fragment_ref IS NOT NULL AND effect_count IN (0, 1))",
    )


def _restore_non_recoverable_fail_function() -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.context_worker_fail_file_import(
            requested_organization_id uuid, requested_job_id uuid,
            requested_service_principal_id uuid, requested_source_ref text,
            requested_signing_key_version bigint, requested_nonce bytea,
            requested_issued_at timestamptz,
            requested_expires_at timestamptz
        ) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$
        DECLARE changed boolean := false; failed_now timestamptz;
        BEGIN
            IF SESSION_USER <> '{_WORKER}' THEN RETURN false; END IF;
            PERFORM pg_catalog.set_config(
                'app.organization_id', requested_organization_id::text, true
            );
            PERFORM pg_catalog.set_config(
                'app.worker_job_id', requested_job_id::text, true
            );
            failed_now := pg_catalog.statement_timestamp();
            UPDATE public.file_import_job AS job
            SET state = 'failed', failed_at = failed_now
            WHERE job.organization_id = requested_organization_id
              AND job.job_id = requested_job_id
              AND job.state = 'running'
              AND job.service_principal_id = requested_service_principal_id
              AND job.source_id::text = requested_source_ref
              AND job.signing_key_version = requested_signing_key_version
              AND job.lease_nonce_digest = public.digest(requested_nonce, 'sha256')
              AND job.lease_issued_at = requested_issued_at
              AND job.lease_expires_at = requested_expires_at
              AND failed_now >= job.lease_issued_at
              AND failed_now < job.lease_expires_at
              AND EXISTS (
                  SELECT 1 FROM public.service_principal AS principal
                  WHERE principal.organization_id = job.organization_id
                    AND principal.service_principal_id = job.service_principal_id
                    AND principal.workload = job.workload
                    AND principal.worker_audience = job.worker_audience
                    AND principal.operation = job.operation
                    AND principal.enabled IS TRUE
              );
            changed := FOUND;
            RETURN changed;
        END; $function$
        """
    )
