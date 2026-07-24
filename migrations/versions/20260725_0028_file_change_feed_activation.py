"""Activate deterministic File change-feed capabilities.

Revision ID: 20260725_0028
Revises: 20260724_0027
Create Date: 2026-07-25
"""

# ruff: noqa: E501

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260725_0028"
down_revision: str | None = "20260724_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONTROL = "context_engine_control"
_DEFINER = "context_engine_worker_lease_definer"
_FUNCTION = "context_control_activate_file_change_feed"
_SIGNATURE = "(uuid, uuid, uuid)"
_ACCEPT_FUNCTION = "context_control_accept_file_change_page"
_ACCEPT_SIGNATURE = (
    "(uuid, uuid, uuid, text, uuid, smallint, text, text, text, bigint, uuid, "
    "jsonb, boolean)"
)
_PAGE = "file_source_change_page"
_CHANGE = "file_source_change"
_READ_PROGRESS = "context_control_read_file_source_progress"
_READ_PROGRESS_SIGNATURE = "(uuid, uuid)"
_V1 = """{"aclEvidenceMode":"mirrored","authorizeAndProject":"unavailable","batchLimits":"unavailable","checkpoint":"unavailable","checkpointSemantics":"unavailable","consistencyGuarantees":"unavailable","contentKinds":["markdown"],"cursorSemantics":"unavailable","declarationVersion":"file-capabilities-v1","deletion":"unavailable","describeCapabilities":"unavailable","discover":"unavailable","fileSourceAccess":"unavailable","freshness":"unavailable","ingestionJobs":"unavailable","projectionFields":[],"readChanges":"unavailable","resourceKinds":["markdown_document"],"sourceMode":"materialized"}"""
_V2 = """{"aclEvidenceMode":"mirrored","authorizeAndProject":"unavailable","batchLimits":"unavailable","checkpoint":"unavailable","checkpointSemantics":"unavailable","consistencyGuarantees":"unavailable","contentKinds":["markdown"],"cursorSemantics":"unavailable","declarationVersion":"file-capabilities-v2","deletion":"unavailable","describeCapabilities":"unavailable","discover":"unavailable","fileSourceAccess":"available","freshness":"unavailable","ingestionJobs":"available","projectionFields":[],"readChanges":"unavailable","resourceKinds":["markdown_document"],"sourceMode":"materialized"}"""
_V3 = """{"aclEvidenceMode":"mirrored","authorizeAndProject":"unavailable","batchLimits":"available","checkpoint":"available","checkpointSemantics":"available","consistencyGuarantees":"unavailable","contentKinds":["markdown"],"cursorSemantics":"available","declarationVersion":"file-capabilities-v3","deletion":"unavailable","describeCapabilities":"available","discover":"unavailable","fileSourceAccess":"available","freshness":"unavailable","ingestionJobs":"available","projectionFields":[],"readChanges":"available","resourceKinds":["markdown_document"],"sourceMode":"materialized"}"""


def _install_capability_constraint(*documents: str) -> None:
    op.drop_constraint(
        "ck_source_version_file_capabilities",
        "source_version",
        type_="check",
    )
    allowed = ", ".join(f"'{document}'::jsonb" for document in documents)
    op.create_check_constraint(
        "ck_source_version_file_capabilities",
        "source_version",
        f"capability_manifest IN ({allowed})",
    )


def _set_v3_manual_import_allowed(*, allowed: bool) -> None:
    prior = "IN ('file-capabilities-v1', 'file-capabilities-v2');"
    current = (
        "IN ('file-capabilities-v1', 'file-capabilities-v2', "
        "'file-capabilities-v3');"
    )
    searched, replacement = (prior, current) if allowed else (current, prior)
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"""
        DO $block$
        DECLARE
            definition text;
            replacement_definition text;
        BEGIN
            definition := pg_catalog.pg_get_functiondef(
                'public.context_control_prepare_file_import_pre_offboarding(
                    uuid, uuid, uuid, uuid, uuid, text, text, uuid,
                    bigint, text, text, uuid
                )'::regprocedure
            );
            replacement_definition := pg_catalog.replace(
                definition, $search${searched}$search$, $replacement${replacement}$replacement$
            );
            IF replacement_definition = definition THEN
                RAISE EXCEPTION 'File import capability predicate was not recognized';
            END IF;
            EXECUTE replacement_definition;
        END;
        $block$
        """
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


def upgrade() -> None:
    """Permit and atomically activate the exact immutable v3 declaration."""

    _install_capability_constraint(_V1, _V2, _V3)
    _set_v3_manual_import_allowed(allowed=True)
    op.create_table(
        _PAGE,
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_ref", sa.Text(), nullable=False),
        sa.Column("scan_ref", sa.Text(), nullable=False),
        sa.Column("scan_epoch", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_limit", sa.SmallInteger(), nullable=False),
        sa.Column(
            "superseded_scan_epoch", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("predecessor_page_ref", sa.Text(), nullable=True),
        sa.Column("page_ordinal", sa.BigInteger(), nullable=False),
        sa.Column("change_count", sa.SmallInteger(), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id", "source_id", "page_ref",
            name="pk_file_source_change_page",
        ),
        sa.UniqueConstraint(
            "organization_id", "source_id", "source_version_id", "scan_epoch",
            "page_ordinal", name="uq_file_source_change_page_ordinal",
        ),
        sa.UniqueConstraint(
            "organization_id", "source_id", "source_version_id", "scan_ref",
            "page_ref", name="uq_file_source_change_page_scan_ref",
        ),
        sa.UniqueConstraint(
            "organization_id", "source_id", "source_version_id", "page_ref",
            name="uq_file_source_change_page_version_ref",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_id", "source_version_id"],
            ["source_version.organization_id", "source_version.source_id", "source_version.version_id"],
            name="fk_file_source_change_page_source_version_exact",
        ),
        sa.CheckConstraint(
            "page_ref ~ '^[0-9a-f]{64}$' AND scan_ref ~ '^[0-9a-f]{64}$'",
            name="ck_file_source_change_page_refs",
        ),
        sa.CheckConstraint(
            "predecessor_page_ref IS NULL OR predecessor_page_ref ~ '^[0-9a-f]{64}$'",
            name="ck_file_source_change_page_predecessor",
        ),
        sa.CheckConstraint(
            "superseded_scan_epoch IS NULL OR "
            "(predecessor_page_ref IS NULL AND superseded_scan_epoch <> scan_epoch)",
            name="ck_file_source_change_page_supersession",
        ),
        sa.CheckConstraint(
            "page_ordinal BETWEEN 1 AND 9223372036854775807 "
            "AND page_limit BETWEEN 1 AND 100 "
            "AND change_count BETWEEN 0 AND page_limit",
            name="ck_file_source_change_page_bounds",
        ),
    )
    op.create_table(
        _CHANGE,
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scan_ref", sa.Text(), nullable=False),
        sa.Column("page_ref", sa.Text(), nullable=False),
        sa.Column("change_ordinal", sa.SmallInteger(), nullable=False),
        sa.Column("change_kind", sa.Text(), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("content_length", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id", "source_id", "page_ref", "change_ordinal",
            name="pk_file_source_change",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_id", "source_version_id", "scan_ref", "page_ref"],
            [f"{_PAGE}.organization_id", f"{_PAGE}.source_id", f"{_PAGE}.source_version_id", f"{_PAGE}.scan_ref", f"{_PAGE}.page_ref"],
            name="fk_file_source_change_page_exact",
        ),
        sa.CheckConstraint(
            "change_ordinal BETWEEN 1 AND 100 AND change_kind = 'upsert'",
            name="ck_file_source_change_kind_ordinal",
        ),
        sa.CheckConstraint(
            "relative_path ~ '^[^/\\\\]*\\.[mM][dD]$' "
            "AND relative_path NOT IN ('.', '..') "
            "AND relative_path = btrim(relative_path) "
            "AND char_length(relative_path) <= 255 "
            "AND relative_path !~ '[\\u0001-\\u001f]' "
            "AND relative_path !~ "
            "'^[[:space:]\\u0085\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]|"
            "[[:space:]\\u0085\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]$'",
            name="ck_file_source_change_markdown_path",
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$' AND content_length >= 0",
            name="ck_file_source_change_content_identity",
        ),
    )
    for table in (_PAGE, _CHANGE):
        op.execute(f"REVOKE ALL ON TABLE {table} FROM PUBLIC")
        for role in (_CONTROL, "context_engine_runtime", "context_engine_worker"):
            op.execute(f"REVOKE ALL ON TABLE {table} FROM {role}")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_migrator_administration ON {table} "
            "FOR ALL TO context_engine_migrator USING (true) WITH CHECK (true)"
        )
        tenant = "organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid"
        op.execute(
            f"CREATE POLICY {table}_file_change_definer_select ON {table} "
            f"FOR SELECT TO {_DEFINER} USING ({tenant})"
        )
        op.execute(
            f"CREATE POLICY {table}_file_change_definer_insert ON {table} "
            f"FOR INSERT TO {_DEFINER} WITH CHECK ({tenant})"
        )
        op.execute(f"GRANT SELECT, INSERT ON TABLE {table} TO {_DEFINER}")
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION public.context_content_reject_mutation()"
        )
    op.add_column(
        "file_source_acquisition_checkpoint",
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "file_source_acquisition_checkpoint",
        sa.Column("change_page_ref", sa.Text(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_file_source_acquisition_checkpoint_change_page",
        "file_source_acquisition_checkpoint",
        ["organization_id", "change_page_ref"],
    )
    op.create_foreign_key(
        "fk_file_source_acquisition_checkpoint_change_page_exact",
        "file_source_acquisition_checkpoint",
        _PAGE,
        ["organization_id", "source_id", "source_version_id", "change_page_ref"],
        ["organization_id", "source_id", "source_version_id", "page_ref"],
    )
    op.drop_constraint(
        "ck_file_source_acquisition_checkpoint_lineage",
        "file_source_acquisition_checkpoint",
        type_="check",
    )
    op.create_check_constraint(
        "ck_file_source_acquisition_checkpoint_lineage",
        "file_source_acquisition_checkpoint",
        "(change_kind = 'file_import' AND acquisition_id IS NOT NULL AND job_id IS NOT NULL AND cleanup_intent_id IS NULL AND resource_ref IS NULL AND revision_id IS NULL AND event_ref IS NULL AND event_sequence IS NULL AND source_version_id IS NULL AND change_page_ref IS NULL) OR "
        "(change_kind = 'file_tombstone' AND acquisition_id IS NULL AND job_id IS NULL AND cleanup_intent_id IS NOT NULL AND resource_ref ~ '^resource:file:[0-9a-f]{64}$' AND revision_id IS NOT NULL AND event_ref ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' AND event_sequence BETWEEN 1 AND 9223372036854775807 AND source_version_id IS NULL AND change_page_ref IS NULL) OR "
        "(change_kind = 'file_change_page' AND acquisition_id IS NULL AND job_id IS NULL AND cleanup_intent_id IS NULL AND resource_ref IS NULL AND revision_id IS NULL AND event_ref IS NULL AND event_sequence IS NULL AND source_version_id IS NOT NULL AND change_page_ref ~ '^[0-9a-f]{64}$')",
    )
    op.execute(
        f"""
        CREATE FUNCTION public.{_FUNCTION}(
            requested_organization_id uuid,
            requested_source_id uuid,
            requested_activated_version_id uuid
        ) RETURNS TABLE (activated_version_id uuid)
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE
            selected_version_id uuid;
            selected_root_ref text;
            selected_capabilities jsonb;
            trusted_now timestamptz;
        BEGIN
            IF SESSION_USER <> '{_CONTROL}'
               OR requested_organization_id IS NULL
               OR requested_source_id IS NULL
               OR requested_activated_version_id IS NULL
            THEN RETURN; END IF;
            PERFORM pg_catalog.set_config(
                'app.organization_id', requested_organization_id::text, true
            );
            SELECT source.active_version_id, version.root_ref,
                   version.capability_manifest
            INTO selected_version_id, selected_root_ref, selected_capabilities
            FROM public.context_source AS source
            JOIN public.source_version AS version
              ON version.organization_id = source.organization_id
             AND version.source_id = source.source_id
             AND version.version_id = source.active_version_id
            WHERE source.organization_id = requested_organization_id
              AND source.source_id = requested_source_id
              AND source.source_kind = 'file'
              AND source.lifecycle_state = 'active'
            FOR UPDATE OF source;
            IF selected_version_id IS NULL THEN RETURN; END IF;
            IF selected_capabilities = '{_V3}'::jsonb THEN
                activated_version_id := selected_version_id;
                RETURN NEXT;
                RETURN;
            END IF;
            IF selected_capabilities <> '{_V2}'::jsonb THEN RETURN; END IF;
            trusted_now := pg_catalog.statement_timestamp();
            INSERT INTO public.source_version (
                organization_id, source_id, version_id, source_kind,
                root_ref, capability_manifest, created_at
            ) VALUES (
                requested_organization_id, requested_source_id,
                requested_activated_version_id, 'file', selected_root_ref,
                '{_V3}'::jsonb, trusted_now
            );
            UPDATE public.context_source AS source
            SET active_version_id = requested_activated_version_id
            WHERE source.organization_id = requested_organization_id
              AND source.source_id = requested_source_id
              AND source.active_version_id = selected_version_id
              AND source.lifecycle_state = 'active';
            IF NOT FOUND THEN RETURN; END IF;
            activated_version_id := requested_activated_version_id;
            RETURN NEXT;
        END;
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_FUNCTION}{_SIGNATURE} FROM PUBLIC")
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"ALTER FUNCTION public.{_FUNCTION}{_SIGNATURE} OWNER TO {_DEFINER}")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_FUNCTION}{_SIGNATURE} TO {_CONTROL}"
    )
    op.execute("RESET ROLE")
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"DROP FUNCTION public.{_READ_PROGRESS}{_READ_PROGRESS_SIGNATURE}"
    )
    op.execute("RESET ROLE")
    op.execute(
        f"""
        CREATE FUNCTION public.{_READ_PROGRESS}(
            requested_organization_id uuid,
            requested_source_id uuid
        ) RETURNS TABLE (
            acquisition_sequence bigint,
            acquisition_checkpoint_ref text,
            acquisition_change_kind text,
            acquisition_acquisition_id uuid,
            acquisition_job_id uuid,
            acquisition_cleanup_intent_id uuid,
            acquisition_resource_ref text,
            acquisition_revision_id uuid,
            acquisition_event_ref text,
            acquisition_event_sequence bigint,
            acquisition_accepted_at timestamptz,
            publish_sequence bigint,
            publish_watermark_ref text,
            publish_checkpoint_ref text,
            publish_change_kind text,
            publish_outcome text,
            publish_acquisition_id uuid,
            publish_job_id uuid,
            publish_cleanup_intent_id uuid,
            publish_resource_ref text,
            publish_revision_id uuid,
            publish_event_ref text,
            publish_event_sequence bigint,
            publish_published_at timestamptz,
            acquisition_source_version_id uuid,
            acquisition_change_page_ref text,
            change_source_version_id uuid,
            change_scan_ref text,
            change_scan_epoch uuid,
            change_page_limit smallint,
            change_superseded_scan_epoch uuid,
            change_page_ref text,
            change_checkpoint_ref text,
            change_sequence bigint,
            change_complete boolean
        )
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        BEGIN
            IF SESSION_USER <> '{_CONTROL}'
               OR NULLIF(current_setting('app.organization_id', true), '')::uuid
                    IS DISTINCT FROM requested_organization_id
               OR NOT EXISTS (
                   SELECT 1 FROM public.context_source AS source
                   WHERE source.organization_id = requested_organization_id
                     AND source.source_id = requested_source_id
                     AND source.source_kind = 'file'
               )
            THEN RETURN; END IF;
            RETURN QUERY
            WITH latest AS (
                SELECT checkpoint.*
                FROM public.file_source_acquisition_checkpoint AS checkpoint
                WHERE checkpoint.organization_id = requested_organization_id
                  AND checkpoint.source_id = requested_source_id
                ORDER BY checkpoint.sequence DESC LIMIT 1
            ), change_head AS (
                SELECT page.source_version_id, page.scan_ref,
                       page.scan_epoch, initial.page_limit,
                       initial.superseded_scan_epoch,
                       page.page_ref,
                       checkpoint.checkpoint_ref, checkpoint.sequence,
                       page.complete
                FROM public.file_source_acquisition_checkpoint AS checkpoint
                JOIN public.{_PAGE} AS page
                  ON page.organization_id = checkpoint.organization_id
                 AND page.source_id = checkpoint.source_id
                 AND page.source_version_id = checkpoint.source_version_id
                 AND page.page_ref = checkpoint.change_page_ref
                JOIN public.{_PAGE} AS initial
                  ON initial.organization_id = page.organization_id
                 AND initial.source_id = page.source_id
                 AND initial.source_version_id = page.source_version_id
                 AND initial.scan_epoch = page.scan_epoch
                 AND initial.page_ordinal = 1
                WHERE checkpoint.organization_id = requested_organization_id
                  AND checkpoint.source_id = requested_source_id
                  AND checkpoint.change_kind = 'file_change_page'
                ORDER BY checkpoint.sequence DESC LIMIT 1
            ), visible AS (
                SELECT watermark.*
                FROM public.file_source_publish_watermark AS watermark
                WHERE watermark.organization_id = requested_organization_id
                  AND watermark.source_id = requested_source_id
                  AND NOT EXISTS (
                      SELECT 1
                      FROM public.file_source_acquisition_checkpoint AS blocking
                      WHERE blocking.organization_id = watermark.organization_id
                        AND blocking.source_id = watermark.source_id
                        AND blocking.sequence <= watermark.sequence
                        AND blocking.change_kind <> 'file_change_page'
                        AND NOT EXISTS (
                            SELECT 1
                            FROM public.file_source_publish_watermark AS outcome
                            WHERE outcome.organization_id = blocking.organization_id
                              AND outcome.source_id = blocking.source_id
                              AND outcome.sequence = blocking.sequence
                        )
                  )
                ORDER BY watermark.sequence DESC LIMIT 1
            )
            SELECT latest.sequence, latest.checkpoint_ref,
                   latest.change_kind, latest.acquisition_id,
                   latest.job_id, latest.cleanup_intent_id,
                   latest.resource_ref, latest.revision_id,
                   latest.event_ref, latest.event_sequence,
                   latest.accepted_at,
                   visible.sequence, visible.watermark_ref,
                   visible.checkpoint_ref, visible.change_kind,
                   visible.outcome, published.acquisition_id,
                   published.job_id, published.cleanup_intent_id,
                   visible.resource_ref, visible.revision_id,
                   published.event_ref, published.event_sequence,
                   visible.published_at, latest.source_version_id,
                   latest.change_page_ref, change_head.source_version_id,
                   change_head.scan_ref, change_head.scan_epoch,
                   change_head.page_limit,
                   change_head.superseded_scan_epoch, change_head.page_ref,
                   change_head.checkpoint_ref, change_head.sequence,
                   change_head.complete
            FROM (SELECT 1) AS singleton
            LEFT JOIN latest ON true
            LEFT JOIN change_head ON true
            LEFT JOIN visible ON true
            LEFT JOIN public.file_source_acquisition_checkpoint AS published
              ON published.organization_id = visible.organization_id
             AND published.source_id = visible.source_id
             AND published.sequence = visible.sequence;
        END;
        $function$
        """
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION public.{_READ_PROGRESS}"
        f"{_READ_PROGRESS_SIGNATURE} FROM PUBLIC"
    )
    op.execute(
        f"ALTER FUNCTION public.{_READ_PROGRESS}{_READ_PROGRESS_SIGNATURE} "
        f"OWNER TO {_DEFINER}"
    )
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_READ_PROGRESS}"
        f"{_READ_PROGRESS_SIGNATURE} TO {_CONTROL}"
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")
    op.execute(
        f"""
        CREATE FUNCTION public.{_ACCEPT_FUNCTION}(
            requested_organization_id uuid,
            requested_source_id uuid,
            requested_source_version_id uuid,
            requested_scan_ref text,
            requested_scan_epoch uuid,
            requested_page_limit smallint,
            requested_page_ref text,
            requested_predecessor_page_ref text,
            requested_predecessor_checkpoint_ref text,
            requested_predecessor_sequence bigint,
            requested_superseded_scan_epoch uuid,
            requested_changes jsonb,
            requested_complete boolean
        ) RETURNS TABLE (
            source_id uuid, source_version_id uuid, page_ref text,
            checkpoint_ref text, sequence bigint, change_count smallint,
            complete boolean, accepted_at timestamptz,
            superseded_scan_epoch uuid, page_limit smallint
        )
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE
            trusted_now timestamptz;
            requested_change_count integer;
            expected_predecessor text;
            latest_change_page_ref text;
            latest_checkpoint_ref text;
            latest_checkpoint_sequence bigint;
            latest_page_complete boolean;
            latest_scan_epoch uuid;
            next_page_ordinal bigint;
            next_sequence bigint;
        BEGIN
            IF SESSION_USER <> '{_CONTROL}'
               OR requested_organization_id IS NULL
               OR requested_source_id IS NULL
               OR requested_source_version_id IS NULL
               OR requested_scan_ref !~ '^[0-9a-f]{{64}}$'
               OR requested_scan_epoch IS NULL
               OR requested_page_limit NOT BETWEEN 1 AND 100
               OR requested_page_ref !~ '^[0-9a-f]{{64}}$'
               OR requested_complete IS NULL
               OR pg_catalog.jsonb_typeof(requested_changes) <> 'array'
               OR (requested_predecessor_page_ref IS NULL) IS DISTINCT FROM
                  (requested_predecessor_checkpoint_ref IS NULL
                   AND requested_predecessor_sequence IS NULL)
               OR (requested_predecessor_checkpoint_ref IS NOT NULL AND
                   (requested_predecessor_checkpoint_ref !~ '^facp_[0-9a-f]{{64}}$'
                    OR requested_predecessor_sequence NOT BETWEEN 1 AND 9223372036854775807))
               OR (requested_predecessor_page_ref IS NOT NULL
                   AND requested_superseded_scan_epoch IS NOT NULL)
            THEN RETURN; END IF;
            requested_change_count := pg_catalog.jsonb_array_length(requested_changes);
            IF requested_change_count NOT BETWEEN 0 AND requested_page_limit
            THEN RETURN; END IF;
            IF EXISTS (
                SELECT 1
                FROM pg_catalog.jsonb_array_elements(requested_changes)
                     AS item(element)
                WHERE pg_catalog.jsonb_typeof(item.element) <> 'object'
                   OR pg_catalog.jsonb_typeof(item.element->'path') <> 'string'
                   OR item.element->>'path' !~ '^[^/\\\\]*\\.[mM][dD]$'
                   OR item.element->>'path' IN ('.', '..')
                   OR item.element->>'path' <> pg_catalog.btrim(item.element->>'path')
                   OR pg_catalog.char_length(item.element->>'path') > 255
                   OR item.element->>'path' ~ '[\\u0001-\\u001f]'
                   OR item.element->>'path' ~
                      '^[[:space:]\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]|[[:space:]\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]$'
            ) THEN RETURN; END IF;
            PERFORM pg_catalog.set_config(
                'app.organization_id', requested_organization_id::text, true
            );
            PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
                'context-engine.file-source-progress:'
                || requested_organization_id::text || ':'
                || requested_source_id::text, 0
            ));
            IF NOT EXISTS (
                SELECT 1 FROM public.context_source AS source
                JOIN public.source_version AS version
                  ON version.organization_id = source.organization_id
                 AND version.source_id = source.source_id
                 AND version.version_id = source.active_version_id
                WHERE source.organization_id = requested_organization_id
                  AND source.source_id = requested_source_id
                  AND source.active_version_id = requested_source_version_id
                  AND source.lifecycle_state = 'active'
                  AND version.capability_manifest = '{_V3}'::jsonb
                FOR UPDATE OF source
            ) THEN RETURN; END IF;
            IF EXISTS (
                SELECT 1 FROM public.{_PAGE} AS page
                WHERE page.organization_id = requested_organization_id
                  AND page.source_id = requested_source_id
                  AND page.page_ref = requested_page_ref
            ) THEN
                RETURN QUERY
                SELECT page.source_id, page.source_version_id, page.page_ref,
                       checkpoint.checkpoint_ref, checkpoint.sequence,
                       page.change_count, page.complete, page.accepted_at,
                       initial.superseded_scan_epoch, page.page_limit
                FROM public.{_PAGE} AS page
                JOIN public.file_source_acquisition_checkpoint AS checkpoint
                  ON checkpoint.organization_id = page.organization_id
                 AND checkpoint.source_id = page.source_id
                 AND checkpoint.source_version_id = page.source_version_id
                 AND checkpoint.change_page_ref = page.page_ref
                 AND checkpoint.change_kind = 'file_change_page'
                JOIN public.{_PAGE} AS initial
                  ON initial.organization_id = page.organization_id
                 AND initial.source_id = page.source_id
                 AND initial.source_version_id = page.source_version_id
                 AND initial.scan_epoch = page.scan_epoch
                 AND initial.page_ordinal = 1
                WHERE page.organization_id = requested_organization_id
                  AND page.source_id = requested_source_id
                  AND page.source_version_id = requested_source_version_id
                  AND page.scan_ref = requested_scan_ref
                  AND page.scan_epoch = requested_scan_epoch
                  AND page.page_limit = requested_page_limit
                  AND page.page_ref = requested_page_ref
                  AND page.predecessor_page_ref IS NOT DISTINCT FROM requested_predecessor_page_ref
                  AND page.change_count = requested_change_count
                  AND page.complete = requested_complete
                  AND NOT EXISTS (
                      SELECT 1 FROM public.{_CHANGE} AS change
                      FULL JOIN LATERAL (
                          SELECT ordinality::smallint AS ordinal,
                                 element->>'kind' AS kind,
                                 element->>'path' AS path,
                                 element->>'contentSha256' AS digest,
                                 (element->>'contentLength')::bigint AS length
                          FROM pg_catalog.jsonb_array_elements(requested_changes)
                          WITH ORDINALITY AS item(element, ordinality)
                      ) AS supplied
                        ON supplied.ordinal = change.change_ordinal
                       AND supplied.kind = change.change_kind
                       AND supplied.path = change.relative_path
                       AND supplied.digest = change.content_sha256
                       AND supplied.length = change.content_length
                      WHERE change.organization_id = page.organization_id
                        AND change.source_id = page.source_id
                        AND change.page_ref = page.page_ref
                        AND (change.change_ordinal IS NULL OR supplied.ordinal IS NULL)
                  );
                RETURN;
            END IF;
            SELECT checkpoint.change_page_ref, checkpoint.checkpoint_ref,
                   checkpoint.sequence, page.complete, page.scan_epoch
            INTO latest_change_page_ref, latest_checkpoint_ref,
                 latest_checkpoint_sequence, latest_page_complete,
                 latest_scan_epoch
            FROM public.file_source_acquisition_checkpoint AS checkpoint
            JOIN public.{_PAGE} AS page
              ON page.organization_id = checkpoint.organization_id
             AND page.source_id = checkpoint.source_id
             AND page.source_version_id = checkpoint.source_version_id
             AND page.page_ref = checkpoint.change_page_ref
            WHERE checkpoint.organization_id = requested_organization_id
              AND checkpoint.source_id = requested_source_id
              AND checkpoint.change_kind = 'file_change_page'
            ORDER BY checkpoint.sequence DESC LIMIT 1;
            IF requested_predecessor_page_ref IS NULL THEN
                IF (latest_change_page_ref IS NULL
                    AND requested_superseded_scan_epoch IS NOT NULL)
                   OR (latest_change_page_ref IS NOT NULL
                       AND requested_superseded_scan_epoch
                           IS DISTINCT FROM latest_scan_epoch)
                THEN RETURN; END IF;
            ELSIF requested_predecessor_page_ref IS DISTINCT FROM latest_change_page_ref
               OR requested_predecessor_checkpoint_ref IS DISTINCT FROM latest_checkpoint_ref
               OR requested_predecessor_sequence IS DISTINCT FROM latest_checkpoint_sequence
            THEN RETURN; END IF;
            SELECT page.page_ref, page.page_ordinal + 1
            INTO expected_predecessor, next_page_ordinal
            FROM public.{_PAGE} AS page
            WHERE page.organization_id = requested_organization_id
              AND page.source_id = requested_source_id
              AND page.source_version_id = requested_source_version_id
              AND page.scan_ref = requested_scan_ref
              AND page.scan_epoch = requested_scan_epoch
            ORDER BY page.page_ordinal DESC LIMIT 1;
            IF next_page_ordinal IS NULL THEN
                next_page_ordinal := 1;
                IF requested_predecessor_page_ref IS NOT NULL THEN RETURN; END IF;
            ELSIF requested_predecessor_page_ref IS DISTINCT FROM expected_predecessor
               OR EXISTS (
                   SELECT 1 FROM public.{_PAGE} AS page
                   WHERE page.organization_id = requested_organization_id
                     AND page.source_id = requested_source_id
                     AND page.source_version_id = requested_source_version_id
                     AND page.scan_ref = requested_scan_ref
                     AND page.scan_epoch = requested_scan_epoch
                     AND page.complete IS TRUE
               )
            THEN RETURN; END IF;
            trusted_now := pg_catalog.statement_timestamp();
            INSERT INTO public.{_PAGE} (
                organization_id, source_id, source_version_id, page_ref,
                scan_ref, scan_epoch, page_limit, superseded_scan_epoch,
                predecessor_page_ref, page_ordinal,
                change_count,
                complete, accepted_at
            ) VALUES (
                requested_organization_id, requested_source_id,
                requested_source_version_id, requested_page_ref,
                requested_scan_ref, requested_scan_epoch,
                requested_page_limit,
                requested_superseded_scan_epoch,
                requested_predecessor_page_ref,
                next_page_ordinal, requested_change_count,
                requested_complete, trusted_now
            );
            INSERT INTO public.{_CHANGE} (
                organization_id, source_id, source_version_id, scan_ref,
                page_ref, change_ordinal, change_kind, relative_path,
                content_sha256, content_length
            )
            SELECT requested_organization_id, requested_source_id,
                   requested_source_version_id, requested_scan_ref,
                   requested_page_ref, item.ordinality::smallint,
                   item.element->>'kind', item.element->>'path',
                   item.element->>'contentSha256',
                   (item.element->>'contentLength')::bigint
            FROM pg_catalog.jsonb_array_elements(requested_changes)
            WITH ORDINALITY AS item(element, ordinality);
            IF (SELECT count(*) FROM public.{_CHANGE} AS change
                WHERE change.organization_id = requested_organization_id
                  AND change.source_id = requested_source_id
                  AND change.page_ref = requested_page_ref) <> requested_change_count
            THEN RAISE EXCEPTION USING ERRCODE = '55000',
                MESSAGE = 'File change page was not accepted atomically'; END IF;
            SELECT COALESCE(max(checkpoint.sequence), 0) + 1
            INTO next_sequence
            FROM public.file_source_acquisition_checkpoint AS checkpoint
            WHERE checkpoint.organization_id = requested_organization_id
              AND checkpoint.source_id = requested_source_id;
            INSERT INTO public.file_source_acquisition_checkpoint (
                organization_id, source_id, sequence, checkpoint_ref,
                change_kind, source_version_id, change_page_ref, accepted_at
            ) VALUES (
                requested_organization_id, requested_source_id, next_sequence,
                'facp_' || pg_catalog.encode(public.digest(
                    pg_catalog.convert_to('context-engine.file-change-page-checkpoint.v1', 'UTF8')
                    || pg_catalog.decode('00', 'hex')
                    || pg_catalog.uuid_send(requested_organization_id)
                    || pg_catalog.uuid_send(requested_source_id)
                    || pg_catalog.int8send(next_sequence)
                    || pg_catalog.decode(requested_page_ref, 'hex'), 'sha256'
                ), 'hex'),
                'file_change_page', requested_source_version_id,
                requested_page_ref, trusted_now
            );
            RETURN QUERY
            SELECT requested_source_id, requested_source_version_id,
                   requested_page_ref, checkpoint.checkpoint_ref,
                   checkpoint.sequence, requested_change_count::smallint,
                   requested_complete, trusted_now,
                   initial.superseded_scan_epoch, requested_page_limit
            FROM public.file_source_acquisition_checkpoint AS checkpoint
            JOIN public.{_PAGE} AS initial
              ON initial.organization_id = checkpoint.organization_id
             AND initial.source_id = checkpoint.source_id
             AND initial.source_version_id = checkpoint.source_version_id
             AND initial.scan_epoch = requested_scan_epoch
             AND initial.page_ordinal = 1
            WHERE checkpoint.organization_id = requested_organization_id
              AND checkpoint.source_id = requested_source_id
              AND checkpoint.source_version_id = requested_source_version_id
              AND checkpoint.change_page_ref = requested_page_ref;
        END;
        $function$
        """
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION public.{_ACCEPT_FUNCTION}{_ACCEPT_SIGNATURE} FROM PUBLIC"
    )
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(
        f"ALTER FUNCTION public.{_ACCEPT_FUNCTION}{_ACCEPT_SIGNATURE} OWNER TO {_DEFINER}"
    )
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_ACCEPT_FUNCTION}{_ACCEPT_SIGNATURE} TO {_CONTROL}"
    )
    op.execute("RESET ROLE")


def downgrade() -> None:
    """Remove v3 versions after restoring their exact v2 predecessor."""

    blocker = (
        op.get_bind()
        .execute(
            sa.text(
                f"""
                SELECT CASE
                    WHEN EXISTS (SELECT 1 FROM {_PAGE})
                    THEN 'accepted page stream'
                    WHEN EXISTS (
                        SELECT 1
                        FROM source_version AS version
                        JOIN file_acquisition AS acquisition
                          ON acquisition.organization_id = version.organization_id
                         AND acquisition.source_id = version.source_id
                         AND acquisition.source_version_id = version.version_id
                        WHERE version.capability_manifest = '{_V3}'::jsonb
                    ) THEN 'File acquisition lineage'
                    WHEN EXISTS (
                        SELECT 1
                        FROM source_version AS version
                        JOIN file_source_cleanup_intent AS intent
                          ON intent.organization_id = version.organization_id
                         AND intent.source_id = version.source_id
                         AND intent.source_version_id = version.version_id
                        WHERE version.capability_manifest = '{_V3}'::jsonb
                    ) THEN 'File source cleanup lineage'
                    WHEN EXISTS (
                        SELECT 1
                        FROM source_version AS version
                        JOIN action_ticket AS ticket
                          ON ticket.organization_id = version.organization_id
                         AND ticket.source_id = version.source_id
                         AND ticket.source_version_id = version.version_id
                        WHERE version.capability_manifest = '{_V3}'::jsonb
                    ) THEN 'ActionTicket lineage'
                END
                """
            )
        )
        .scalar_one_or_none()
    )
    if blocker is not None:
        raise RuntimeError(
            f"File change-feed downgrade requires no retained {blocker}; "
            "use a forward fix to preserve acquisition checkpoint ordering"
        )
    _set_v3_manual_import_allowed(allowed=False)
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(f"DROP FUNCTION public.{_ACCEPT_FUNCTION}{_ACCEPT_SIGNATURE}")
    op.execute(f"DROP FUNCTION public.{_FUNCTION}{_SIGNATURE}")
    op.execute("RESET ROLE")
    op.drop_constraint(
        "ck_file_source_acquisition_checkpoint_lineage",
        "file_source_acquisition_checkpoint",
        type_="check",
    )
    op.drop_constraint(
        "fk_file_source_acquisition_checkpoint_change_page_exact",
        "file_source_acquisition_checkpoint",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_file_source_acquisition_checkpoint_change_page",
        "file_source_acquisition_checkpoint",
        type_="unique",
    )
    op.drop_column("file_source_acquisition_checkpoint", "change_page_ref")
    op.drop_column("file_source_acquisition_checkpoint", "source_version_id")
    op.create_check_constraint(
        "ck_file_source_acquisition_checkpoint_lineage",
        "file_source_acquisition_checkpoint",
        "(change_kind = 'file_import' AND acquisition_id IS NOT NULL AND job_id IS NOT NULL AND cleanup_intent_id IS NULL AND resource_ref IS NULL AND revision_id IS NULL AND event_ref IS NULL AND event_sequence IS NULL) OR (change_kind = 'file_tombstone' AND acquisition_id IS NULL AND job_id IS NULL AND cleanup_intent_id IS NOT NULL AND resource_ref ~ '^resource:file:[0-9a-f]{64}$' AND revision_id IS NOT NULL AND event_ref ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' AND event_sequence BETWEEN 1 AND 9223372036854775807)",
    )
    op.drop_table(_CHANGE)
    op.drop_table(_PAGE)
    # The prior progress reader is restored by replaying its original body.
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"DROP FUNCTION public.{_READ_PROGRESS}{_READ_PROGRESS_SIGNATURE}"
    )
    op.execute("RESET ROLE")
    op.execute(
        f"""
        CREATE FUNCTION public.{_READ_PROGRESS}(
            requested_organization_id uuid,
            requested_source_id uuid
        ) RETURNS TABLE (
            acquisition_sequence bigint, acquisition_checkpoint_ref text,
            acquisition_change_kind text, acquisition_acquisition_id uuid,
            acquisition_job_id uuid, acquisition_cleanup_intent_id uuid,
            acquisition_resource_ref text, acquisition_revision_id uuid,
            acquisition_event_ref text, acquisition_event_sequence bigint,
            acquisition_accepted_at timestamptz, publish_sequence bigint,
            publish_watermark_ref text, publish_checkpoint_ref text,
            publish_change_kind text, publish_outcome text,
            publish_acquisition_id uuid, publish_job_id uuid,
            publish_cleanup_intent_id uuid, publish_resource_ref text,
            publish_revision_id uuid, publish_event_ref text,
            publish_event_sequence bigint, publish_published_at timestamptz
        )
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$
        BEGIN
            IF SESSION_USER <> '{_CONTROL}'
               OR NULLIF(current_setting('app.organization_id', true), '')::uuid
                    IS DISTINCT FROM requested_organization_id
               OR NOT EXISTS (SELECT 1 FROM public.context_source AS source
                   WHERE source.organization_id = requested_organization_id
                     AND source.source_id = requested_source_id
                     AND source.source_kind = 'file')
            THEN RETURN; END IF;
            RETURN QUERY
            WITH bounds AS (
                SELECT max(checkpoint.sequence) AS latest_sequence,
                       min(checkpoint.sequence) FILTER (WHERE watermark.sequence IS NULL) AS first_missing
                FROM public.file_source_acquisition_checkpoint AS checkpoint
                LEFT JOIN public.file_source_publish_watermark AS watermark
                  ON watermark.organization_id = checkpoint.organization_id
                 AND watermark.source_id = checkpoint.source_id
                 AND watermark.sequence = checkpoint.sequence
                WHERE checkpoint.organization_id = requested_organization_id
                  AND checkpoint.source_id = requested_source_id
            ), visible AS (
                SELECT bounds.latest_sequence,
                       CASE WHEN bounds.latest_sequence IS NULL THEN NULL::bigint
                            WHEN bounds.first_missing IS NULL THEN bounds.latest_sequence
                            ELSE bounds.first_missing - 1 END AS visible_sequence
                FROM bounds
            )
            SELECT latest.sequence, latest.checkpoint_ref, latest.change_kind,
                   latest.acquisition_id, latest.job_id, latest.cleanup_intent_id,
                   latest.resource_ref, latest.revision_id, latest.event_ref,
                   latest.event_sequence, latest.accepted_at,
                   watermark.sequence, watermark.watermark_ref,
                   watermark.checkpoint_ref, watermark.change_kind,
                   watermark.outcome, published.acquisition_id,
                   published.job_id, published.cleanup_intent_id,
                   watermark.resource_ref, watermark.revision_id,
                   published.event_ref, published.event_sequence,
                   watermark.published_at
            FROM visible
            LEFT JOIN public.file_source_acquisition_checkpoint AS latest
              ON latest.organization_id = requested_organization_id
             AND latest.source_id = requested_source_id
             AND latest.sequence = visible.latest_sequence
            LEFT JOIN public.file_source_publish_watermark AS watermark
              ON watermark.organization_id = requested_organization_id
             AND watermark.source_id = requested_source_id
             AND watermark.sequence = visible.visible_sequence
            LEFT JOIN public.file_source_acquisition_checkpoint AS published
              ON published.organization_id = watermark.organization_id
             AND published.source_id = watermark.source_id
             AND published.sequence = watermark.sequence;
        END;
        $function$
        """
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION public.{_READ_PROGRESS}"
        f"{_READ_PROGRESS_SIGNATURE} FROM PUBLIC"
    )
    op.execute(
        f"ALTER FUNCTION public.{_READ_PROGRESS}{_READ_PROGRESS_SIGNATURE} "
        f"OWNER TO {_DEFINER}"
    )
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_READ_PROGRESS}"
        f"{_READ_PROGRESS_SIGNATURE} TO {_CONTROL}"
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")
    op.execute("DROP TRIGGER source_version_immutable ON source_version")
    op.execute(
        f"""
        WITH prior AS (
            SELECT DISTINCT ON (v3.organization_id, v3.source_id)
                v3.organization_id, v3.source_id,
                v2.version_id AS prior_version_id,
                v3.version_id AS v3_version_id
            FROM public.source_version AS v3
            JOIN public.source_version AS v2
              ON v2.organization_id = v3.organization_id
             AND v2.source_id = v3.source_id
             AND v2.capability_manifest = '{_V2}'::jsonb
            WHERE v3.capability_manifest = '{_V3}'::jsonb
            ORDER BY v3.organization_id, v3.source_id,
                     v2.created_at DESC, v2.version_id DESC
        )
        UPDATE public.context_source AS source
        SET active_version_id = prior.prior_version_id,
            disabled_version_id = CASE
                WHEN source.disabled_version_id = prior.v3_version_id
                THEN prior.prior_version_id
                ELSE source.disabled_version_id
            END
        FROM prior
        WHERE source.organization_id = prior.organization_id
          AND source.source_id = prior.source_id
          AND source.active_version_id = prior.v3_version_id
        """
    )
    op.execute(
        f"DELETE FROM public.source_version WHERE capability_manifest = '{_V3}'::jsonb"
    )
    op.execute(
        "CREATE TRIGGER source_version_immutable BEFORE UPDATE OR DELETE "
        "ON source_version FOR EACH ROW EXECUTE FUNCTION "
        "public.source_version_reject_mutation()"
    )
    _install_capability_constraint(_V1, _V2)
