"""Permit canonical nested Markdown paths in the File publication lineage.

Revision ID: 20260726_0035
Revises: 20260726_0034
Create Date: 2026-07-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260726_0035"
down_revision: str | None = "20260726_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NESTED_MARKDOWN_PATH = (
    "relative_path ~ '^([^/\\\\]+/)*[^/\\\\]*\\.[mM][dD]$' "
    "AND relative_path !~ '(^|/)(\\.|\\.\\.)(/|$)' "
    "AND relative_path = btrim(relative_path) "
    "AND char_length(relative_path) <= 255 "
    "AND relative_path !~ '[\\u0001-\\u001f]' "
    "AND relative_path !~ "
    "'^[[:space:]\\u0085\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]|"
    "[[:space:]\\u0085\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]$'"
)
_FLAT_MARKDOWN_PATH = (
    "relative_path ~ '^[^/\\\\]*\\.[mM][dD]$' "
    "AND relative_path NOT IN ('.', '..') "
    "AND relative_path = btrim(relative_path) "
    "AND char_length(relative_path) <= 255 "
    "AND relative_path !~ '[\\u0001-\\u001f]' "
    "AND relative_path !~ "
    "'^[[:space:]\\u0085\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]|"
    "[[:space:]\\u0085\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]$'"
)
_DEFINER = "context_engine_worker_lease_definer"
_ACCEPT_SIGNATURE = (
    "(uuid,uuid,uuid,text,uuid,smallint,text,text,text,bigint,uuid,jsonb,boolean)"
)
_ACCEPT_FUNCTIONS = (
    f"context_control_accept_file_change_page{_ACCEPT_SIGNATURE}",
    f"context_internal_accept_file_delete_observation_page{_ACCEPT_SIGNATURE}",
)
_FILE_OPERATION_FENCES = (
    "context-engine.file-change-scheduling-migration-fence",
    "context-engine.file-dispatch-migration-fence",
)
_PATH_CONSTRAINTS = (
    (
        "file_acquisition",
        "ck_file_acquisition_one_markdown_filename",
        "",
    ),
    (
        "file_source_change",
        "ck_file_source_change_markdown_path",
        "",
    ),
    (
        "file_delete_observation_execution",
        "ck_file_delete_observation_execution_observation",
        " AND content_sha256 ~ '^[0-9a-f]{64}$' AND content_length >= 0",
    ),
)
_FLAT_FUNCTION_PREDICATE = (
    "item.element->>'path' !~ '^[^/\\\\]*\\.[mM][dD]$'\n"
    "                   OR item.element->>'path' IN ('.', '..')"
)
_NESTED_FUNCTION_PREDICATE = (
    "item.element->>'path' !~ '^([^/\\\\]+/)*[^/\\\\]*\\.[mM][dD]$'\n"
    "                   OR item.element->>'path' ~ "
    "'(^|/)(\\.|\\.\\.)(/|$)'"
)


def _replace_accept_path_predicate(searched: str, replacement: str) -> None:
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    for regprocedure in _ACCEPT_FUNCTIONS:
        op.execute(
            f"""
            DO $block$
            DECLARE
                definition text;
                replacement_definition text;
            BEGIN
                definition := pg_catalog.pg_get_functiondef(
                    'public.{regprocedure}'::regprocedure
                );
                replacement_definition := pg_catalog.replace(
                    definition,
                    $search${searched}$search$,
                    $replacement${replacement}$replacement$
                );
                IF replacement_definition = definition THEN
                    RAISE EXCEPTION
                        'File accept path predicate was not recognized';
                END IF;
                EXECUTE replacement_definition;
            END;
            $block$
            """
        )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


def _replace_constraints(path_predicate: str) -> None:
    for table_name, constraint_name, additional_predicate in _PATH_CONSTRAINTS:
        op.drop_constraint(constraint_name, table_name, type_="check")
        op.create_check_constraint(
            constraint_name,
            table_name,
            f"{path_predicate}{additional_predicate}",
        )


def _acquire_file_operation_fences() -> None:
    connection = op.get_bind()
    for migration_fence in _FILE_OPERATION_FENCES:
        connection.exec_driver_sql(
            "SELECT pg_catalog.pg_advisory_xact_lock("
            "pg_catalog.hashtextextended(%s, 0))",
            (migration_fence,),
        )


def upgrade() -> None:
    """Align every durable File path constraint with recursive acquisition."""

    _acquire_file_operation_fences()
    _replace_constraints(_NESTED_MARKDOWN_PATH)
    _replace_accept_path_predicate(
        _FLAT_FUNCTION_PREDICATE,
        _NESTED_FUNCTION_PREDICATE,
    )


def downgrade() -> None:
    """Restore flat paths only when no nested publication fact remains."""

    _acquire_file_operation_fences()
    connection = op.get_bind()
    nested_count = connection.exec_driver_sql(
        """
        SELECT
          (SELECT count(*) FROM file_acquisition
           WHERE strpos(relative_path, '/') > 0) +
          (SELECT count(*) FROM file_source_change
           WHERE strpos(relative_path, '/') > 0) +
          (SELECT count(*) FROM file_delete_observation_execution
           WHERE strpos(relative_path, '/') > 0)
        """
    ).scalar_one()
    if nested_count:
        raise RuntimeError(
            "recursive File path downgrade requires no retained nested lineage"
        )
    _replace_accept_path_predicate(
        _NESTED_FUNCTION_PREDICATE,
        _FLAT_FUNCTION_PREDICATE,
    )
    _replace_constraints(_FLAT_MARKDOWN_PATH)
