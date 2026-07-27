"""Expose an exact Release-operator-owned candidate snapshot.

Revision ID: 20260727_0040
Revises: 20260727_0039
Create Date: 2026-07-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260727_0040"
down_revision: str | None = "20260727_0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RELEASE_OPERATOR = "context_engine_release_operator"
_DEFINER = "context_engine_release_definer"
_FUNCTION = "context_release_observe_candidate_snapshot"
_SIGNATURE = "(uuid, text, text, text, text)"
_FILE_OPERATION_FENCES = (
    "context-engine.file-change-scheduling-migration-fence",
    "context-engine.file-dispatch-migration-fence",
    "context-engine.file-status-migration-fence",
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
    """Add one tenant-scoped read function for explicit candidate assembly."""

    _acquire_file_operation_fences()
    op.execute(
        f"GRANT SELECT ON TABLE context_resource, context_source TO {_DEFINER}"
    )
    op.execute(
        "CREATE POLICY context_resource_release_definer_select "
        "ON context_resource FOR SELECT TO context_engine_release_definer "
        "USING (organization_id = NULLIF("
        "current_setting('app.organization_id', true), ''"
        ")::uuid)"
    )
    op.execute(
        "CREATE POLICY context_source_release_definer_select "
        "ON context_source FOR SELECT TO context_engine_release_definer "
        "USING (organization_id = NULLIF("
        "current_setting('app.organization_id', true), ''"
        ")::uuid AND source_kind = 'file' AND lifecycle_state = 'active')"
    )
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"""
        CREATE FUNCTION public.{_FUNCTION}(
            requested_organization_id uuid,
            requested_operator_ref text,
            requested_authentication_binding_ref text,
            requested_authority_ref text,
            requested_authority_digest text
        ) RETURNS TABLE (
            active_generation bigint,
            active_manifest_digest text,
            active_revision_refs text[]
        )
        LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE
            observed_generation bigint := 0;
            observed_manifest_digest text := NULL;
            observed_revision_refs text[];
        BEGIN
            IF SESSION_USER <> '{_RELEASE_OPERATOR}'
               OR requested_organization_id IS NULL
            THEN RETURN; END IF;
            PERFORM pg_catalog.set_config(
                'app.organization_id', requested_organization_id::text, true
            );
            IF NOT EXISTS (
                SELECT 1
                FROM public.release_operator_grant AS grant_row
                WHERE grant_row.organization_id = requested_organization_id
                  AND grant_row.operator_ref = requested_operator_ref
                  AND grant_row.authentication_binding_ref =
                      requested_authentication_binding_ref
                  AND grant_row.authority_ref = requested_authority_ref
                  AND grant_row.authority_digest = requested_authority_digest
                  AND grant_row.valid_from <= pg_catalog.statement_timestamp()
                  AND grant_row.expires_at > pg_catalog.statement_timestamp()
                  AND grant_row.revoked_at IS NULL
            ) THEN RETURN; END IF;
            SELECT active.active_generation, active.manifest_digest
            INTO observed_generation, observed_manifest_digest
            FROM public.active_release_manifest AS active
            WHERE active.organization_id = requested_organization_id;
            IF NOT FOUND THEN
                observed_generation := 0;
                observed_manifest_digest := NULL;
            END IF;
            SELECT coalesce(
                pg_catalog.array_agg(
                    DISTINCT resource.active_revision_id::text
                    ORDER BY resource.active_revision_id::text
                ),
                ARRAY[]::text[]
            )
            INTO observed_revision_refs
            FROM public.context_resource AS resource
            JOIN public.context_source AS source
              ON source.organization_id = resource.organization_id
             AND source.source_id::text = resource.source_ref
             AND source.source_kind = 'file'
             AND source.lifecycle_state = 'active'
            WHERE resource.organization_id = requested_organization_id
              AND resource.active_revision_id IS NOT NULL
              AND resource.tombstoned IS FALSE;
            active_generation := observed_generation;
            active_manifest_digest := observed_manifest_digest;
            active_revision_refs := observed_revision_refs;
            RETURN NEXT;
        END;
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_FUNCTION}{_SIGNATURE} FROM PUBLIC")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_FUNCTION}{_SIGNATURE} "
        f"TO {_RELEASE_OPERATOR}"
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


def downgrade() -> None:
    """Remove only the candidate-snapshot observation seam."""

    _acquire_file_operation_fences()
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(f"DROP FUNCTION public.{_FUNCTION}{_SIGNATURE}")
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")
    op.execute(
        "DROP POLICY context_resource_release_definer_select ON context_resource"
    )
    op.execute(
        "DROP POLICY context_source_release_definer_select ON context_source"
    )
    op.execute(f"REVOKE SELECT ON TABLE context_resource FROM {_DEFINER}")
    op.execute(f"REVOKE SELECT ON TABLE context_source FROM {_DEFINER}")
