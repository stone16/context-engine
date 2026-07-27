"""Expose accepted current-scan pages that still require scheduling.

Revision ID: 20260727_0038
Revises: 20260727_0037
Create Date: 2026-07-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260727_0038"
down_revision: str | None = "20260727_0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONTROL = "context_engine_control"
_DEFINER = "context_engine_worker_lease_definer"
_FUNCTION = "context_control_read_pending_file_change_schedules"
_SIGNATURE = "(uuid, uuid)"
_V4 = "file-capabilities-v4"


def upgrade() -> None:
    """Add one tenant-scoped read model for restart-safe scheduling handoff."""

    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"""
        CREATE FUNCTION public.{_FUNCTION}(
            requested_organization_id uuid,
            requested_source_id uuid
        ) RETURNS TABLE (
            pending_source_version_id uuid,
            pending_page_ref text
        )
        LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        BEGIN
            IF SESSION_USER <> '{_CONTROL}'
               OR NULLIF(current_setting('app.organization_id', true), '')::uuid
                    IS DISTINCT FROM requested_organization_id
            THEN RETURN; END IF;
            RETURN QUERY
            WITH active AS (
                SELECT source.active_version_id
                FROM public.context_source AS source
                JOIN public.source_version AS version
                  ON version.organization_id = source.organization_id
                 AND version.source_id = source.source_id
                 AND version.version_id = source.active_version_id
                WHERE source.organization_id = requested_organization_id
                  AND source.source_id = requested_source_id
                  AND source.lifecycle_state = 'active'
                  AND version.capability_manifest->>'declarationVersion'
                        = '{_V4}'
            ), head AS (
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
                ORDER BY checkpoint.sequence DESC LIMIT 1
            )
            SELECT page.source_version_id, page.page_ref
            FROM head
            JOIN public.file_source_change_page AS page
              ON page.organization_id = requested_organization_id
             AND page.source_id = requested_source_id
             AND page.source_version_id = head.source_version_id
             AND page.scan_epoch = head.scan_epoch
            JOIN public.file_source_delete_observation_page AS binding
              ON binding.organization_id = page.organization_id
             AND binding.source_id = page.source_id
             AND binding.source_version_id = page.source_version_id
             AND binding.page_ref = page.page_ref
            LEFT JOIN public.file_source_change_page AS baseline_terminal
              ON baseline_terminal.organization_id = binding.organization_id
             AND baseline_terminal.source_id = binding.source_id
             AND baseline_terminal.source_version_id = binding.source_version_id
             AND baseline_terminal.page_ref = binding.baseline_page_ref
            WHERE EXISTS (
                SELECT 1
                FROM public.file_source_change AS change
                WHERE change.organization_id = page.organization_id
                  AND change.source_id = page.source_id
                  AND change.source_version_id = page.source_version_id
                  AND change.page_ref = page.page_ref
                  AND change.change_kind = 'upsert'
                  AND (
                      baseline_terminal.page_ref IS NULL
                      OR NOT EXISTS (
                          SELECT 1
                          FROM public.file_source_change_page AS baseline_page
                          JOIN public.file_source_change AS baseline_change
                            ON baseline_change.organization_id =
                               baseline_page.organization_id
                           AND baseline_change.source_id = baseline_page.source_id
                           AND baseline_change.source_version_id =
                               baseline_page.source_version_id
                           AND baseline_change.page_ref = baseline_page.page_ref
                          WHERE baseline_page.organization_id =
                                baseline_terminal.organization_id
                            AND baseline_page.source_id =
                                baseline_terminal.source_id
                            AND baseline_page.source_version_id =
                                baseline_terminal.source_version_id
                            AND baseline_page.scan_epoch =
                                baseline_terminal.scan_epoch
                            AND baseline_change.change_kind = 'upsert'
                            AND baseline_change.relative_path =
                                change.relative_path
                            AND baseline_change.content_sha256 =
                                change.content_sha256
                            AND baseline_change.content_length =
                                change.content_length
                      )
                  )
            )
              AND page.page_limit = 1
              AND NOT EXISTS (
                  SELECT 1
                  FROM public.file_acquisition AS acquisition
                  WHERE acquisition.organization_id = page.organization_id
                    AND acquisition.source_id = page.source_id
                    AND acquisition.source_version_id = page.source_version_id
                    AND acquisition.change_page_ref = page.page_ref
              )
            ORDER BY page.page_ordinal;
        END;
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_FUNCTION}{_SIGNATURE} FROM PUBLIC")
    op.execute(f"ALTER FUNCTION public.{_FUNCTION}{_SIGNATURE} OWNER TO {_DEFINER}")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_FUNCTION}{_SIGNATURE} TO {_CONTROL}"
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


def downgrade() -> None:
    """Remove the read model without changing accepted pages or jobs."""

    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(f"DROP FUNCTION public.{_FUNCTION}{_SIGNATURE}")
    op.execute("RESET ROLE")
