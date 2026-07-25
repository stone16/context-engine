"""Schedule the upsert projection of current mixed File pages.

Revision ID: 20260725_0032
Revises: 20260725_0031
Create Date: 2026-07-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260725_0032"
down_revision: str | None = "20260725_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFINER = "context_engine_worker_lease_definer"
_REGPROCEDURE = (
    "public.context_control_schedule_file_change_page("
    "uuid,uuid,uuid,text,text,uuid,bigint,uuid)"
)
_MIGRATION_FENCE = "context-engine.file-change-scheduling-migration-fence"


def upgrade() -> None:
    """Validate the whole page and schedule only its nonempty upsert subset."""

    _replace_schedule_function(include_mixed_upserts=True)


def downgrade() -> None:
    """Restore mixed-page refusal only when no mixed schedule is retained."""

    op.execute(
        "SELECT pg_catalog.pg_advisory_xact_lock("
        f"pg_catalog.hashtextextended('{_MIGRATION_FENCE}', 0))"
    )
    op.execute(
        "LOCK TABLE public.context_source, public.file_acquisition "
        "IN ACCESS EXCLUSIVE MODE"
    )
    has_mixed_schedule = op.get_bind().execute(
        sa.text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM public.file_acquisition AS acquisition
                WHERE acquisition.change_page_ref IS NOT NULL
                  AND EXISTS (
                      SELECT 1
                      FROM public.file_source_change AS change
                      WHERE change.organization_id = acquisition.organization_id
                        AND change.source_id = acquisition.source_id
                        AND change.source_version_id = acquisition.source_version_id
                        AND change.page_ref = acquisition.change_page_ref
                        AND change.change_kind = 'delete'
                  )
            )
            """
        )
    ).scalar_one()
    if has_mixed_schedule:
        raise RuntimeError(
            "mixed File upsert scheduling downgrade requires no retained "
            "acquisition lineage from a mixed page; use a forward fix"
        )
    _replace_schedule_function(include_mixed_upserts=False)
    op.execute(f"REVOKE SELECT ON TABLE public.alembic_version FROM {_DEFINER}")


def _replace_schedule_function(*, include_mixed_upserts: bool) -> None:
    definition = op.get_bind().execute(
        sa.text(
            "SELECT pg_catalog.pg_get_functiondef("
            f"'{_REGPROCEDURE}'::regprocedure)"
        )
    ).scalar_one()
    if not isinstance(definition, str):
        raise RuntimeError("File scheduling function definition is unavailable")

    declaration_old = (
        "            selected_change_count smallint;\n"
        "            selected_scan_epoch uuid;"
    )
    declaration_new = (
        "            selected_change_count smallint;\n"
        "            selected_page_complete boolean;\n"
        "            selected_upsert_count integer;\n"
        "            selected_scan_epoch uuid;"
    )
    page_selection_old = (
        "            SELECT page.change_count, page.scan_epoch\n"
        "            INTO selected_change_count, selected_scan_epoch"
    )
    page_selection_new = (
        "            SELECT page.change_count, page.complete, page.scan_epoch\n"
        "            INTO selected_change_count, selected_page_complete, "
        "selected_scan_epoch"
    )
    kind_old = "change.change_kind <> 'upsert'"
    kind_new = (
        "change.change_kind NOT IN ('upsert', 'delete')\n"
        "                         OR (selected_page_complete IS NOT TRUE\n"
        "                             AND change.change_kind = 'delete')"
    )
    validation_old = (
        "            THEN RETURN; END IF;\n"
        "            IF NOT EXISTS (\n"
        "                SELECT 1 FROM public.membership AS membership"
    )
    validation_new = (
        "            THEN RETURN; END IF;\n"
        "            SELECT count(*)\n"
        "            INTO selected_upsert_count\n"
        "            FROM public.file_source_change AS change\n"
        "            WHERE change.organization_id = requested_organization_id\n"
        "              AND change.source_id = requested_source_id\n"
        "              AND change.source_version_id = requested_source_version_id\n"
        "              AND change.page_ref = requested_page_ref\n"
        "              AND change.change_kind = 'upsert';\n"
        "            IF selected_upsert_count = 0 THEN RETURN; END IF;\n"
        "            IF NOT EXISTS (\n"
        "                SELECT 1 FROM public.membership AS membership"
    )
    entry_old = (
        "            trusted_now := pg_catalog.statement_timestamp();\n"
        "            PERFORM pg_catalog.set_config("
    )
    entry_new = (
        "            PERFORM pg_catalog.pg_advisory_xact_lock_shared(\n"
        "                pg_catalog.hashtextextended(\n"
        f"                    '{_MIGRATION_FENCE}', 0\n"
        "                )\n"
        "            );\n"
        "            BEGIN\n"
        "                PERFORM 1\n"
        "                FROM public.alembic_version AS installed_revision\n"
        "                LIMIT 1;\n"
        "                IF NOT FOUND THEN RETURN; END IF;\n"
        "            EXCEPTION\n"
        "                WHEN insufficient_privilege THEN RETURN;\n"
        "            END;\n"
        "            trusted_now := pg_catalog.statement_timestamp();\n"
        "            PERFORM pg_catalog.set_config("
    )
    count_old = "existing_acquisition_count NOT IN (0, selected_change_count)"
    count_new = "existing_acquisition_count NOT IN (0, selected_upsert_count)"
    first_loop_old = (
        "                          AND change.page_ref = requested_page_ref\n"
        "                        ORDER BY change.change_ordinal"
    )
    first_loop_new = (
        "                          AND change.page_ref = requested_page_ref\n"
        "                          AND change.change_kind = 'upsert'\n"
        "                        ORDER BY change.change_ordinal"
    )
    replay_loops_old = (
        "                  AND change.page_ref = requested_page_ref\n"
        "                ORDER BY change.change_ordinal"
    )
    replay_loops_new = (
        "                  AND change.page_ref = requested_page_ref\n"
        "                  AND change.change_kind = 'upsert'\n"
        "                ORDER BY change.change_ordinal"
    )
    replacements = (
        (
            entry_old if include_mixed_upserts else entry_new,
            entry_new if include_mixed_upserts else entry_old,
            1,
        ),
        (
            declaration_old if include_mixed_upserts else declaration_new,
            declaration_new if include_mixed_upserts else declaration_old,
            1,
        ),
        (
            page_selection_old if include_mixed_upserts else page_selection_new,
            page_selection_new if include_mixed_upserts else page_selection_old,
            1,
        ),
        (
            kind_old if include_mixed_upserts else kind_new,
            kind_new if include_mixed_upserts else kind_old,
            1,
        ),
        (
            validation_old if include_mixed_upserts else validation_new,
            validation_new if include_mixed_upserts else validation_old,
            1,
        ),
        (
            count_old if include_mixed_upserts else count_new,
            count_new if include_mixed_upserts else count_old,
            1,
        ),
        (
            first_loop_old if include_mixed_upserts else first_loop_new,
            first_loop_new if include_mixed_upserts else first_loop_old,
            1,
        ),
        (
            replay_loops_old if include_mixed_upserts else replay_loops_new,
            replay_loops_new if include_mixed_upserts else replay_loops_old,
            2,
        ),
    )
    for searched, replacement, expected_count in replacements:
        actual_count = definition.count(searched)
        if actual_count != expected_count:
            raise RuntimeError(
                "File scheduling function shape changed: expected "
                f"{expected_count} occurrence(s), found {actual_count}"
            )
        definition = definition.replace(searched, replacement)

    if include_mixed_upserts:
        op.execute(
            f"GRANT SELECT ON TABLE public.alembic_version TO {_DEFINER}"
        )
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(sa.text(definition))
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")
