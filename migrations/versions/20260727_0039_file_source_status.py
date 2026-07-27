"""Retain compilation refusal categories and expose File source status.

Revision ID: 20260727_0039
Revises: 20260727_0038
Create Date: 2026-07-27
"""

# ruff: noqa: E501

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260727_0039"
down_revision: str | None = "20260727_0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONTROL = "context_engine_control"
_WORKER = "context_engine_worker"
_DEFINER = "context_engine_worker_lease_definer"
_FAIL = "context_worker_fail_file_import_with_category"
_FAIL_SIGNATURE = (
    "(uuid, uuid, uuid, text, text, bigint, bigint, bytea, "
    "timestamp with time zone, timestamp with time zone)"
)
_STATUS = "context_control_read_file_source_status"
_STATUS_SIGNATURE = "(uuid, uuid)"
_MIGRATION_FENCE = "context-engine.file-status-migration-fence"
_UPSTREAM_MIGRATION_FENCES = (
    "context-engine.file-change-scheduling-migration-fence",
    "context-engine.file-dispatch-migration-fence",
)
_CATEGORIES = (
    "invalid_utf8",
    "unsupported_construct",
    "unsupported_document_shape",
)


def _create_category_fail_function() -> None:
    op.execute(
        f"""
        CREATE FUNCTION public.{_FAIL}(
            requested_organization_id uuid, requested_job_id uuid,
            requested_service_principal_id uuid, requested_source_ref text,
            requested_compilation_refusal_category text,
            requested_lease_generation bigint,
            requested_signing_key_version bigint, requested_nonce bytea,
            requested_issued_at timestamptz,
            requested_expires_at timestamptz
        ) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$
        DECLARE changed boolean := false;
        BEGIN
            IF SESSION_USER <> '{_WORKER}' THEN RETURN false; END IF;
            IF requested_compilation_refusal_category IS NULL
               OR requested_compilation_refusal_category NOT IN (
                'invalid_utf8',
                'unsupported_construct',
                'unsupported_document_shape'
            )
            THEN RETURN false; END IF;
            PERFORM pg_catalog.pg_advisory_xact_lock_shared(
                pg_catalog.hashtextextended('{_MIGRATION_FENCE}', 0)
            );
            PERFORM 1
            FROM pg_catalog.pg_attribute AS attribute
            WHERE attribute.attrelid = 'public.file_import_job'::regclass
              AND attribute.attname = 'compilation_refusal_category'
              AND attribute.attnum > 0
              AND attribute.attisdropped IS FALSE;
            IF NOT FOUND THEN RETURN false; END IF;
            changed := public.context_worker_fail_file_import(
                requested_organization_id, requested_job_id,
                requested_service_principal_id, requested_source_ref,
                requested_lease_generation, requested_signing_key_version,
                requested_nonce, requested_issued_at, requested_expires_at
            );
            IF changed IS NOT TRUE THEN RETURN false; END IF;
            UPDATE public.file_import_job AS job
            SET compilation_refusal_category =
                    requested_compilation_refusal_category
            WHERE job.organization_id = requested_organization_id
              AND job.job_id = requested_job_id
              AND job.state = 'failed'
              AND job.compilation_refusal_category IS NULL;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'File compilation refusal was not retained';
            END IF;
            RETURN true;
        END; $function$
        """
    )


def _create_status_function() -> None:
    op.execute(
        f"""
        CREATE FUNCTION public.{_STATUS}(
            requested_organization_id uuid,
            requested_source_id uuid
        ) RETURNS TABLE (
            status_observed_at timestamptz,
            active_resource_count bigint,
            last_successful_acquisition_at timestamptz,
            last_successful_acquisition_age_seconds bigint,
            refusal_path text,
            refusal_category text
        )
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER
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
            PERFORM pg_catalog.set_config(
                'app.file_status_source_id', requested_source_id::text, true
            );
            RETURN QUERY
            WITH status AS (
                SELECT pg_catalog.statement_timestamp() AS observed_at,
                       (
                           SELECT count(*)
                           FROM public.context_resource AS resource
                           WHERE resource.organization_id =
                                 requested_organization_id
                             AND resource.source_ref = requested_source_id::text
                             AND resource.tombstoned IS FALSE
                       ) AS resource_count,
                       (
                           SELECT max(watermark.published_at)
                           FROM public.file_source_publish_watermark AS watermark
                           WHERE watermark.organization_id =
                                 requested_organization_id
                             AND watermark.source_id = requested_source_id
                             AND watermark.change_kind = 'file_import'
                       ) AS succeeded_at
            ), active AS (
                SELECT source.active_version_id
                FROM public.context_source AS source
                WHERE source.organization_id = requested_organization_id
                  AND source.source_id = requested_source_id
            ), terminal AS (
                SELECT page.source_version_id, page.scan_epoch
                FROM active
                JOIN public.file_source_acquisition_checkpoint AS checkpoint
                  ON checkpoint.organization_id = requested_organization_id
                 AND checkpoint.source_id = requested_source_id
                 AND checkpoint.source_version_id = active.active_version_id
                 AND checkpoint.change_kind = 'file_change_page'
                JOIN public.file_source_change_page AS page
                  ON page.organization_id = checkpoint.organization_id
                 AND page.source_id = checkpoint.source_id
                 AND page.source_version_id = checkpoint.source_version_id
                 AND page.page_ref = checkpoint.change_page_ref
                 AND page.complete IS TRUE
                ORDER BY checkpoint.sequence DESC LIMIT 1
            ), current_paths AS (
                SELECT DISTINCT terminal.source_version_id,
                       change.relative_path, change.content_sha256,
                       change.content_length
                FROM terminal
                JOIN public.file_source_change_page AS page
                  ON page.organization_id = requested_organization_id
                 AND page.source_id = requested_source_id
                 AND page.source_version_id = terminal.source_version_id
                 AND page.scan_epoch = terminal.scan_epoch
                JOIN public.file_source_change AS change
                  ON change.organization_id = page.organization_id
                 AND change.source_id = page.source_id
                 AND change.source_version_id = page.source_version_id
                 AND change.page_ref = page.page_ref
                 AND change.change_kind = 'upsert'
            ), refusals AS (
                SELECT path.relative_path, latest.compilation_refusal_category
                FROM current_paths AS path
                JOIN LATERAL (
                    SELECT job.compilation_refusal_category
                    FROM public.file_source_acquisition_checkpoint AS checkpoint
                    JOIN public.file_acquisition AS acquisition
                      ON acquisition.organization_id = checkpoint.organization_id
                     AND acquisition.source_id = checkpoint.source_id
                     AND acquisition.acquisition_id = checkpoint.acquisition_id
                    JOIN public.file_import_job AS job
                      ON job.organization_id = checkpoint.organization_id
                     AND job.source_id = checkpoint.source_id
                     AND job.job_id = checkpoint.job_id
                    WHERE checkpoint.organization_id =
                          requested_organization_id
                      AND checkpoint.source_id = requested_source_id
                      AND checkpoint.change_kind = 'file_import'
                     AND acquisition.source_version_id =
                          path.source_version_id
                      AND acquisition.relative_path = path.relative_path
                      AND acquisition.expected_content_sha256 =
                          path.content_sha256
                      AND acquisition.expected_content_length =
                          path.content_length
                    ORDER BY checkpoint.sequence DESC LIMIT 1
                ) AS latest ON latest.compilation_refusal_category IS NOT NULL
            )
            SELECT status.observed_at,
                   status.resource_count,
                   status.succeeded_at,
                   CASE WHEN status.succeeded_at IS NULL THEN NULL::bigint
                        ELSE pg_catalog.floor(EXTRACT(
                            EPOCH FROM status.observed_at - status.succeeded_at
                        ))::bigint END,
                   refusals.relative_path,
                   refusals.compilation_refusal_category
            FROM status
            LEFT JOIN refusals ON true
            ORDER BY refusals.relative_path COLLATE "C";
        END;
        $function$
        """
    )


def upgrade() -> None:
    """Retain a closed failure category and add one status read projection."""

    op.add_column(
        "file_import_job",
        sa.Column("compilation_refusal_category", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "ck_file_import_job_compilation_refusal_category",
        "file_import_job",
        "compilation_refusal_category IS NULL OR "
        "(state = 'failed' AND compilation_refusal_category IN ("
        + ",".join(f"'{category}'" for category in _CATEGORIES)
        + "))",
    )
    tenant = (
        "organization_id = NULLIF("
        "current_setting('app.organization_id', true), ''"
        ")::uuid"
    )
    op.execute(
        "CREATE POLICY file_import_job_file_status_definer_select "
        "ON file_import_job FOR SELECT TO context_engine_worker_lease_definer "
        f"USING ({tenant} AND source_id = NULLIF("
        "current_setting('app.file_status_source_id', true), ''"
        ")::uuid)"
    )
    op.execute(
        f"GRANT UPDATE (compilation_refusal_category) ON file_import_job TO {_DEFINER}"
    )
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    _create_category_fail_function()
    _create_status_function()
    for function_name, signature in (
        (_FAIL, _FAIL_SIGNATURE),
        (_STATUS, _STATUS_SIGNATURE),
    ):
        op.execute(
            f"REVOKE ALL ON FUNCTION public.{function_name}{signature} FROM PUBLIC"
        )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_FAIL}{_FAIL_SIGNATURE} TO {_WORKER}"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_STATUS}{_STATUS_SIGNATURE} TO {_CONTROL}"
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


def downgrade() -> None:
    """Remove status only when no retained category would be discarded."""

    for migration_fence in (*_UPSTREAM_MIGRATION_FENCES, _MIGRATION_FENCE):
        op.execute(
            "SELECT pg_catalog.pg_advisory_xact_lock("
            f"pg_catalog.hashtextextended('{migration_fence}', 0))"
        )
    retained = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM file_import_job "
                "WHERE compilation_refusal_category IS NOT NULL)"
            )
        )
        .scalar_one()
    )
    if retained:
        raise RuntimeError(
            "File status downgrade requires no retained compilation refusals"
        )
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(f"DROP FUNCTION public.{_STATUS}{_STATUS_SIGNATURE}")
    op.execute(f"DROP FUNCTION public.{_FAIL}{_FAIL_SIGNATURE}")
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")
    op.drop_constraint(
        "ck_file_import_job_compilation_refusal_category",
        "file_import_job",
        type_="check",
    )
    op.execute(
        f"REVOKE UPDATE (compilation_refusal_category) "
        f"ON file_import_job FROM {_DEFINER}"
    )
    op.execute(
        "DROP POLICY file_import_job_file_status_definer_select ON file_import_job"
    )
    op.drop_column("file_import_job", "compilation_refusal_category")
