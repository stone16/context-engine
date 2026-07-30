"""Record configured File scan bounds and closed bound refusals.

Revision ID: 20260730_0045
Revises: 20260730_0044
Create Date: 2026-07-30
"""

# ruff: noqa: E501

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_0045"
down_revision: str | None = "20260730_0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONTROL = "context_engine_control"
_DEFINER = "context_engine_worker_lease_definer"
_OLD_ACCEPT = "context_control_accept_file_delete_observation_page"
_OLD_ACCEPT_SIGNATURE = (
    "(uuid,uuid,uuid,text,uuid,smallint,text,text,text,bigint,uuid,jsonb,boolean,jsonb)"
)
_ACCEPT = "context_control_accept_bounded_file_delete_observation_page"
_ACCEPT_SIGNATURE = (
    "(uuid,uuid,uuid,text,uuid,smallint,text,text,text,bigint,uuid,jsonb,boolean,integer,jsonb)"
)
_REPORT = "context_control_report_file_scan_bound_refusal"
_REPORT_SIGNATURE = "(uuid,uuid,integer)"
_CLEAR = "context_control_clear_file_scan_bound_refusal"
_CLEAR_SIGNATURE = "(uuid,uuid)"
_READ = "context_control_read_file_scan_bound_status"
_READ_SIGNATURE = "(uuid,uuid)"
_BOUND_TRIGGER = "context_file_change_set_scan_bound"
_DEFAULT_BOUND = 10_000
_MAX_BOUND = 15_000
_STATUS_MIGRATION_FENCE = "context-engine.file-status-migration-fence"
_UPSTREAM_MIGRATION_FENCES = (
    "context-engine.file-change-scheduling-migration-fence",
    "context-engine.file-dispatch-migration-fence",
)


def _shared_status_fence() -> str:
    return f"""
            PERFORM pg_catalog.pg_advisory_xact_lock_shared(
                pg_catalog.hashtextextended('{_STATUS_MIGRATION_FENCE}', 0)
            );
            PERFORM 1
            FROM pg_catalog.pg_attribute AS attribute
            WHERE attribute.attrelid = 'public.context_source'::regclass
              AND attribute.attname = 'file_scan_refusal_category'
              AND attribute.attnum > 0
              AND attribute.attisdropped IS FALSE;
            IF NOT FOUND THEN RETURN; END IF;
"""


def _acquire_exclusive_status_fences() -> None:
    for migration_fence in (
        *_UPSTREAM_MIGRATION_FENCES,
        _STATUS_MIGRATION_FENCE,
    ):
        op.execute(
            "SELECT pg_catalog.pg_advisory_xact_lock("
            f"pg_catalog.hashtextextended('{migration_fence}', 0))"
        )


def _set_absolute_acceptance_ceiling(*, raised: bool) -> None:
    """Move the existing durable function's absolute fence in both directions."""

    old, new = ((_DEFAULT_BOUND, _MAX_BOUND) if raised else (_MAX_BOUND, _DEFAULT_BOUND))
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"""
        DO $block$
        DECLARE
            definition text;
            replacement_definition text;
            searched text := E'> {old}\\n';
            replacement_text text := E'> {new}\\n';
        BEGIN
            definition := pg_catalog.pg_get_functiondef(
                'public.{_OLD_ACCEPT}{_OLD_ACCEPT_SIGNATURE}'::regprocedure
            );
            IF pg_catalog.strpos(definition, replacement_text) > 0 THEN
                RETURN;
            END IF;
            replacement_definition := pg_catalog.replace(
                definition, searched, replacement_text
            );
            IF replacement_definition = definition THEN
                RAISE EXCEPTION 'File baseline durable fence was not recognized';
            END IF;
            EXECUTE replacement_definition;
        END;
        $block$
        """
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


def upgrade() -> None:
    """Add per-scan provenance while keeping ADR-0065's default unchanged."""

    _acquire_exclusive_status_fences()
    op.add_column(
        "file_source_change_page",
        sa.Column(
            "scan_bound",
            sa.Integer(),
            nullable=False,
            server_default=str(_DEFAULT_BOUND),
        ),
    )
    op.create_check_constraint(
        "ck_file_source_change_page_scan_bound",
        "file_source_change_page",
        f"scan_bound BETWEEN 1 AND {_MAX_BOUND}",
    )
    op.add_column(
        "context_source",
        sa.Column("file_scan_refusal_category", sa.Text(), nullable=True),
    )
    op.add_column(
        "context_source",
        sa.Column("file_scan_refusal_bound", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_context_source_file_scan_refusal",
        "context_source",
        "(file_scan_refusal_category IS NULL AND file_scan_refusal_bound IS NULL) OR "
        "(file_scan_refusal_category = 'scan_bound_exceeded' AND "
        f"file_scan_refusal_bound BETWEEN 1 AND {_MAX_BOUND})",
    )
    op.execute(
        "GRANT UPDATE (file_scan_refusal_category, file_scan_refusal_bound) "
        f"ON TABLE context_source TO {_DEFINER}"
    )
    _set_absolute_acceptance_ceiling(raised=True)
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"""
        CREATE FUNCTION public.{_BOUND_TRIGGER}()
        RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog
        AS $function$
        DECLARE configured text;
        BEGIN
            configured := NULLIF(current_setting('app.file_scan_bound', true), '');
            NEW.scan_bound := COALESCE(configured::integer, {_DEFAULT_BOUND});
            RETURN NEW;
        END;
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_BOUND_TRIGGER}() FROM PUBLIC")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_BOUND_TRIGGER}() "
        f"TO context_engine_migrator, {_DEFINER}"
    )
    op.execute("RESET ROLE")
    op.execute(
        "CREATE TRIGGER file_source_change_page_set_scan_bound "
        "BEFORE INSERT ON file_source_change_page FOR EACH ROW "
        f"EXECUTE FUNCTION public.{_BOUND_TRIGGER}()"
    )
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"""
        CREATE FUNCTION public.{_REPORT}(
            requested_organization_id uuid,
            requested_source_id uuid,
            requested_scan_bound integer
        ) RETURNS TABLE (refusal_category text, scan_bound integer)
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        BEGIN
            IF SESSION_USER <> '{_CONTROL}'
               OR requested_organization_id IS NULL
               OR requested_source_id IS NULL
               OR requested_scan_bound NOT BETWEEN 1 AND {_MAX_BOUND}
            THEN RETURN; END IF;
            {_shared_status_fence()}
            PERFORM pg_catalog.set_config(
                'app.organization_id', requested_organization_id::text, true
            );
            UPDATE public.context_source AS source
            SET file_scan_refusal_category = 'scan_bound_exceeded',
                file_scan_refusal_bound = requested_scan_bound
            WHERE source.organization_id = requested_organization_id
              AND source.source_id = requested_source_id
              AND source.source_kind = 'file'
              AND source.lifecycle_state = 'active';
            IF NOT FOUND THEN RETURN; END IF;
            refusal_category := 'scan_bound_exceeded';
            scan_bound := requested_scan_bound;
            RETURN NEXT;
        END;
        $function$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION public.{_CLEAR}(
            requested_organization_id uuid,
            requested_source_id uuid
        ) RETURNS TABLE (cleared boolean)
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        BEGIN
            IF SESSION_USER <> '{_CONTROL}'
               OR requested_organization_id IS NULL
               OR requested_source_id IS NULL
            THEN RETURN; END IF;
            {_shared_status_fence()}
            PERFORM pg_catalog.set_config(
                'app.organization_id', requested_organization_id::text, true
            );
            UPDATE public.context_source AS source
            SET file_scan_refusal_category = NULL,
                file_scan_refusal_bound = NULL
            WHERE source.organization_id = requested_organization_id
              AND source.source_id = requested_source_id
              AND source.source_kind = 'file'
              AND source.lifecycle_state = 'active';
            IF NOT FOUND THEN RETURN; END IF;
            cleared := true;
            RETURN NEXT;
        END;
        $function$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION public.{_READ}(
            requested_organization_id uuid,
            requested_source_id uuid
        ) RETURNS TABLE (
            head_scan_bound integer,
            baseline_scan_bound integer,
            baseline_parent_scan_bound integer,
            refusal_category text,
            refusal_scan_bound integer
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
            WITH selected_source AS (
                SELECT source.active_version_id,
                       source.file_scan_refusal_category,
                       source.file_scan_refusal_bound
                FROM public.context_source AS source
                WHERE source.organization_id = requested_organization_id
                  AND source.source_id = requested_source_id
                  AND source.source_kind = 'file'
            ), head AS (
                SELECT page.scan_bound, page.complete
                FROM selected_source
                JOIN public.file_source_acquisition_checkpoint AS checkpoint
                  ON checkpoint.organization_id = requested_organization_id
                 AND checkpoint.source_id = requested_source_id
                 AND checkpoint.source_version_id =
                       selected_source.active_version_id
                 AND checkpoint.change_kind = 'file_change_page'
                JOIN public.file_source_change_page AS page
                  ON page.organization_id = checkpoint.organization_id
                 AND page.source_id = checkpoint.source_id
                 AND page.source_version_id = checkpoint.source_version_id
                 AND page.page_ref = checkpoint.change_page_ref
                ORDER BY checkpoint.sequence DESC LIMIT 1
            ), baseline AS (
                SELECT page.scan_bound, binding.baseline_page_ref,
                       page.organization_id, page.source_id,
                       page.source_version_id
                FROM selected_source
                JOIN public.file_source_acquisition_checkpoint AS checkpoint
                  ON checkpoint.organization_id = requested_organization_id
                 AND checkpoint.source_id = requested_source_id
                 AND checkpoint.source_version_id =
                       selected_source.active_version_id
                 AND checkpoint.change_kind = 'file_change_page'
                JOIN public.file_source_change_page AS page
                  ON page.organization_id = checkpoint.organization_id
                 AND page.source_id = checkpoint.source_id
                 AND page.source_version_id = checkpoint.source_version_id
                 AND page.page_ref = checkpoint.change_page_ref
                 AND page.complete IS TRUE
                JOIN public.file_source_delete_observation_page AS binding
                  ON binding.organization_id = page.organization_id
                 AND binding.source_id = page.source_id
                 AND binding.source_version_id = page.source_version_id
                 AND binding.page_ref = page.page_ref
                ORDER BY checkpoint.sequence DESC LIMIT 1
            ), parent AS (
                SELECT page.scan_bound
                FROM baseline
                JOIN public.file_source_change_page AS page
                  ON page.organization_id = baseline.organization_id
                 AND page.source_id = baseline.source_id
                 AND page.source_version_id = baseline.source_version_id
                 AND page.page_ref = baseline.baseline_page_ref
            )
            SELECT head.scan_bound, baseline.scan_bound, parent.scan_bound,
                   selected_source.file_scan_refusal_category,
                   selected_source.file_scan_refusal_bound
            FROM selected_source
            LEFT JOIN head ON true
            LEFT JOIN baseline ON true
            LEFT JOIN parent ON true;
        END;
        $function$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION public.{_ACCEPT}(
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
            requested_complete boolean,
            requested_scan_bound integer,
            requested_baseline jsonb
        ) RETURNS TABLE (
            source_id uuid, source_version_id uuid, page_ref text,
            checkpoint_ref text, sequence bigint, change_count smallint,
            complete boolean, accepted_at timestamptz,
            superseded_scan_epoch uuid, page_limit smallint,
            scan_bound integer
        )
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE result record;
                existing_page boolean := false;
        BEGIN
            IF SESSION_USER <> '{_CONTROL}'
               OR requested_scan_bound IS NULL
               OR requested_scan_bound NOT BETWEEN 1 AND {_MAX_BOUND}
               OR pg_catalog.jsonb_typeof(requested_changes) <> 'array'
            THEN RETURN; END IF;
            {_shared_status_fence()}
            PERFORM pg_catalog.set_config(
                'app.organization_id', requested_organization_id::text, true
            );
            PERFORM pg_catalog.pg_advisory_xact_lock(
                pg_catalog.hashtextextended(
                    'context-engine.file-source-progress:'
                    || requested_organization_id::text || ':'
                    || requested_source_id::text,
                    0
                )
            );
            SELECT EXISTS (
                SELECT 1 FROM public.file_source_change_page AS page
                WHERE page.organization_id = requested_organization_id
                  AND page.source_id = requested_source_id
                  AND page.source_version_id = requested_source_version_id
                  AND page.page_ref = requested_page_ref
            ) INTO existing_page;
            IF EXISTS (
                    SELECT 1 FROM public.file_source_change_page AS page
                    WHERE page.organization_id = requested_organization_id
                      AND page.source_id = requested_source_id
                      AND page.source_version_id = requested_source_version_id
                      AND page.scan_ref = requested_scan_ref
                      AND page.scan_epoch = requested_scan_epoch
                      AND page.scan_bound <> requested_scan_bound
               ) OR (
                    SELECT COALESCE(sum(page.change_count), 0)
                    FROM public.file_source_change_page AS page
                    WHERE page.organization_id = requested_organization_id
                      AND page.source_id = requested_source_id
                      AND page.source_version_id = requested_source_version_id
                      AND page.scan_ref = requested_scan_ref
                      AND page.scan_epoch = requested_scan_epoch
                      AND page.page_ref <> requested_page_ref
               ) + pg_catalog.jsonb_array_length(requested_changes)
                   > requested_scan_bound
            THEN RETURN; END IF;
            PERFORM pg_catalog.set_config(
                'app.file_scan_bound', requested_scan_bound::text, true
            );
            SELECT * INTO result
            FROM public.{_OLD_ACCEPT}(
                requested_organization_id, requested_source_id,
                requested_source_version_id, requested_scan_ref,
                requested_scan_epoch, requested_page_limit,
                requested_page_ref, requested_predecessor_page_ref,
                requested_predecessor_checkpoint_ref,
                requested_predecessor_sequence,
                requested_superseded_scan_epoch, requested_changes,
                requested_complete, requested_baseline
            );
            IF NOT FOUND THEN RETURN; END IF;
            IF NOT EXISTS (
                SELECT 1 FROM public.file_source_change_page AS page
                WHERE page.organization_id = requested_organization_id
                  AND page.source_id = requested_source_id
                  AND page.page_ref = result.page_ref
                  AND page.scan_bound = requested_scan_bound
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'File scan bound provenance was not retained';
            END IF;
            IF requested_complete AND existing_page IS FALSE THEN
                UPDATE public.context_source AS source
                SET file_scan_refusal_category = NULL,
                    file_scan_refusal_bound = NULL
                WHERE source.organization_id = requested_organization_id
                  AND source.source_id = requested_source_id
                  AND source.lifecycle_state = 'active';
            END IF;
            source_id := result.source_id;
            source_version_id := result.source_version_id;
            page_ref := result.page_ref;
            checkpoint_ref := result.checkpoint_ref;
            sequence := result.sequence;
            change_count := result.change_count;
            complete := result.complete;
            accepted_at := result.accepted_at;
            superseded_scan_epoch := result.superseded_scan_epoch;
            page_limit := result.page_limit;
            scan_bound := requested_scan_bound;
            RETURN NEXT;
        END;
        $function$
        """
    )
    for function_name, signature in (
        (_REPORT, _REPORT_SIGNATURE),
        (_CLEAR, _CLEAR_SIGNATURE),
        (_READ, _READ_SIGNATURE),
        (_ACCEPT, _ACCEPT_SIGNATURE),
    ):
        op.execute(
            f"REVOKE ALL ON FUNCTION public.{function_name}{signature} FROM PUBLIC"
        )
        op.execute(
            f"ALTER FUNCTION public.{function_name}{signature} OWNER TO {_DEFINER}"
        )
    op.execute(
        f"REVOKE EXECUTE ON FUNCTION public.{_OLD_ACCEPT}{_OLD_ACCEPT_SIGNATURE} "
        f"FROM {_CONTROL}"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_REPORT}{_REPORT_SIGNATURE} TO {_CONTROL}"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_CLEAR}{_CLEAR_SIGNATURE} TO {_CONTROL}"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_READ}{_READ_SIGNATURE} TO {_CONTROL}"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_ACCEPT}{_ACCEPT_SIGNATURE} TO {_CONTROL}"
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


def downgrade() -> None:
    """Drop provenance only when every retained value is the historical default."""

    _acquire_exclusive_status_fences()
    op.execute(
        "LOCK TABLE public.context_source, public.file_source_change_page "
        "IN ACCESS EXCLUSIVE MODE"
    )
    retained = op.get_bind().execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM file_source_change_page "
            f"WHERE scan_bound <> {_DEFAULT_BOUND}) OR EXISTS ("
            "SELECT 1 FROM context_source "
            "WHERE file_scan_refusal_category IS NOT NULL)"
        )
    ).scalar_one()
    if retained:
        raise RuntimeError(
            "File scan bound downgrade requires default-only retained provenance"
        )
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    for function_name, signature in (
        (_ACCEPT, _ACCEPT_SIGNATURE),
        (_READ, _READ_SIGNATURE),
        (_CLEAR, _CLEAR_SIGNATURE),
        (_REPORT, _REPORT_SIGNATURE),
    ):
        op.execute(f"DROP FUNCTION public.{function_name}{signature}")
    op.execute(f"DROP FUNCTION public.{_BOUND_TRIGGER}() CASCADE")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_OLD_ACCEPT}{_OLD_ACCEPT_SIGNATURE} "
        f"TO {_CONTROL}"
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")
    _set_absolute_acceptance_ceiling(raised=False)
    op.execute(
        "REVOKE UPDATE (file_scan_refusal_category, file_scan_refusal_bound) "
        f"ON TABLE context_source FROM {_DEFINER}"
    )
    op.drop_constraint(
        "ck_context_source_file_scan_refusal", "context_source", type_="check"
    )
    op.drop_column("context_source", "file_scan_refusal_bound")
    op.drop_column("context_source", "file_scan_refusal_category")
    op.drop_constraint(
        "ck_file_source_change_page_scan_bound",
        "file_source_change_page",
        type_="check",
    )
    op.drop_column("file_source_change_page", "scan_bound")
