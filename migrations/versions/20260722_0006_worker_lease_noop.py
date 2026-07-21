"""Add registered ServiceActors and the persistent no-op WorkerLease job.

Revision ID: 20260722_0006
Revises: 20260721_0005
Create Date: 2026-07-22

This bounded carrier proves only the Issue #17 no-op lease lifecycle.  It does
not persist credentials, source payloads, or any of the deferred full Supply
job authority dimensions.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260722_0006"
down_revision: str | None = "20260721_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MIGRATOR_ROLE = "context_engine_migrator"
_CONTROL_ROLE = "context_engine_control"
_WORKER_LEASE_DEFINER_ROLE = "context_engine_worker_lease_definer"
_RUNTIME_ROLE = "context_engine_runtime"
_WORKER_ROLE = "context_engine_worker"
_SERVICE_PRINCIPAL_TABLE = "service_principal"
_JOB_TABLE = "worker_noop_job"
_ISSUE_LEASE_FUNCTION = "public.context_worker_issue_noop_lease"
_COMPLETE_JOB_FUNCTION = "public.context_worker_complete_noop_job"
_ISSUE_LEASE_SIGNATURE = "(uuid, uuid, uuid, text, text, bigint, bytea, integer)"
_COMPLETE_JOB_SIGNATURE = (
    "(uuid, uuid, uuid, text, text, text, bigint, bytea, "
    "timestamp with time zone, timestamp with time zone)"
)
_MAX_SIGNED_BIGINT = 2**63 - 1
_MAX_LEASE_TTL_SECONDS = 3600
_PYTHON_ISSPACE_CODE_POINTS = (
    "U&'\\0009\\000A\\000B\\000C\\000D\\001C\\001D\\001E\\001F\\0020"
    "\\0085\\00A0\\1680\\2000\\2001\\2002\\2003\\2004\\2005\\2006"
    "\\2007\\2008\\2009\\200A\\2028\\2029\\202F\\205F\\3000'"
)

_SERVICE_ACTOR_BINDING = """
{table_name}.organization_id = NULLIF(
    current_setting('app.organization_id', true), ''
)::uuid
AND current_setting('app.actor_kind', true) = 'service'
AND {table_name}.service_principal_id = NULLIF(
    current_setting('app.service_principal_id', true), ''
)::uuid
AND {table_name}.workload = current_setting('app.workload', true)
AND {table_name}.worker_audience = current_setting(
    'app.worker_audience', true
)
AND {table_name}.operation = current_setting('app.operation', true)
AND {table_name}.operation = 'noop.complete'
""".strip()


def _worker_expression(table_name: str) -> str:
    expression = _SERVICE_ACTOR_BINDING.format(table_name=table_name)
    if table_name == _SERVICE_PRINCIPAL_TABLE:
        return f"{expression}\nAND {table_name}.enabled IS TRUE"
    return (
        f"{expression}\n"
        f"AND {table_name}.job_id = NULLIF(\n"
        "    current_setting('app.worker_job_id', true), ''\n"
        ")::uuid\n"
        "AND EXISTS (\n"
        "    SELECT 1\n"
        "    FROM public.service_principal AS active_service_principal\n"
        "    WHERE active_service_principal.organization_id = "
        f"{table_name}.organization_id\n"
        "      AND active_service_principal.service_principal_id = "
        f"{table_name}.service_principal_id\n"
        f"      AND active_service_principal.workload = {table_name}.workload\n"
        "      AND active_service_principal.worker_audience = "
        f"{table_name}.worker_audience\n"
        f"      AND active_service_principal.operation = {table_name}.operation\n"
        "      AND active_service_principal.enabled IS TRUE\n"
        ")"
    )


def upgrade() -> None:
    """Create the exact-actor, exact-job persistent no-op lease boundary."""

    op.create_table(
        _SERVICE_PRINCIPAL_TABLE,
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "service_principal_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("workload", sa.Text(), nullable=False),
        sa.Column("worker_audience", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "service_principal_id",
            name="pk_service_principal",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "service_principal_id",
            "workload",
            "worker_audience",
            "operation",
            name="uq_service_principal_worker_binding",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.organization_id"],
            name="fk_service_principal_organization",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            f"translate(workload, {_PYTHON_ISSPACE_CODE_POINTS}, '') <> '' "
            "AND char_length(workload) <= 128 "
            "AND octet_length(workload) <= 512",
            name="ck_service_principal_workload_bounds",
        ),
        sa.CheckConstraint(
            f"translate(worker_audience, {_PYTHON_ISSPACE_CODE_POINTS}, '') <> '' "
            "AND char_length(worker_audience) <= 255 "
            "AND octet_length(worker_audience) <= 1020",
            name="ck_service_principal_worker_audience_bounds",
        ),
        sa.CheckConstraint(
            "operation = 'noop.complete'",
            name="ck_service_principal_operation_noop_complete",
        ),
    )

    op.create_table(
        _JOB_TABLE,
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "service_principal_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
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
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "effect_count",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "job_id",
            name="pk_worker_noop_job",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.organization_id"],
            name="fk_worker_noop_job_organization",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            [
                "organization_id",
                "service_principal_id",
                "workload",
                "worker_audience",
                "operation",
            ],
            [
                "service_principal.organization_id",
                "service_principal.service_principal_id",
                "service_principal.workload",
                "service_principal.worker_audience",
                "service_principal.operation",
            ],
            name="fk_worker_noop_job_service_principal_binding",
        ),
        sa.CheckConstraint(
            f"translate(workload, {_PYTHON_ISSPACE_CODE_POINTS}, '') <> '' "
            "AND char_length(workload) <= 128 "
            "AND octet_length(workload) <= 512",
            name="ck_worker_noop_job_workload_bounds",
        ),
        sa.CheckConstraint(
            f"translate(worker_audience, {_PYTHON_ISSPACE_CODE_POINTS}, '') <> '' "
            "AND char_length(worker_audience) <= 255 "
            "AND octet_length(worker_audience) <= 1020",
            name="ck_worker_noop_job_worker_audience_bounds",
        ),
        sa.CheckConstraint(
            "actor_kind = 'service'",
            name="ck_worker_noop_job_actor_kind_service",
        ),
        sa.CheckConstraint(
            "operation = 'noop.complete'",
            name="ck_worker_noop_job_operation_noop_complete",
        ),
        sa.CheckConstraint(
            "state IN ('available', 'leased', 'completed')",
            name="ck_worker_noop_job_state",
        ),
        sa.CheckConstraint(
            "signing_key_version IS NULL OR signing_key_version > 0",
            name="ck_worker_noop_job_signing_key_version_positive",
        ),
        sa.CheckConstraint(
            "lease_nonce_digest IS NULL OR octet_length(lease_nonce_digest) = 32",
            name="ck_worker_noop_job_nonce_sha256_length",
        ),
        sa.CheckConstraint(
            "(state = 'available' "
            "AND signing_key_version IS NULL "
            "AND lease_nonce_digest IS NULL "
            "AND lease_issued_at IS NULL "
            "AND lease_expires_at IS NULL "
            "AND lease_redeemed_at IS NULL "
            "AND completed_at IS NULL "
            "AND effect_count = 0) "
            "OR (state = 'leased' "
            "AND signing_key_version IS NOT NULL "
            "AND lease_nonce_digest IS NOT NULL "
            "AND lease_issued_at IS NOT NULL "
            "AND lease_expires_at > lease_issued_at "
            "AND lease_redeemed_at IS NULL "
            "AND completed_at IS NULL "
            "AND effect_count = 0) "
            "OR (state = 'completed' "
            "AND signing_key_version IS NOT NULL "
            "AND lease_nonce_digest IS NOT NULL "
            "AND lease_issued_at IS NOT NULL "
            "AND lease_expires_at > lease_issued_at "
            "AND lease_redeemed_at IS NOT NULL "
            "AND completed_at IS NOT NULL "
            "AND lease_redeemed_at >= lease_issued_at "
            "AND completed_at >= lease_redeemed_at "
            "AND effect_count = 1)",
            name="ck_worker_noop_job_state_consistency",
        ),
    )

    for table_name in (_SERVICE_PRINCIPAL_TABLE, _JOB_TABLE):
        op.execute(f"REVOKE ALL ON TABLE {table_name} FROM PUBLIC")
        op.execute(f"REVOKE ALL ON TABLE {table_name} FROM {_RUNTIME_ROLE}")
        op.execute(f"REVOKE ALL ON TABLE {table_name} FROM {_CONTROL_ROLE}")
        op.execute(f"REVOKE ALL ON TABLE {table_name} FROM {_WORKER_ROLE}")
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table_name}_migrator_administration "
            f"ON {table_name} AS PERMISSIVE FOR ALL TO {_MIGRATOR_ROLE} "
            "USING (true) WITH CHECK (true)"
        )

    service_principal_expression = _worker_expression(_SERVICE_PRINCIPAL_TABLE)
    op.execute(
        "CREATE POLICY service_principal_current_service_actor "
        "ON service_principal AS PERMISSIVE FOR SELECT "
        f"TO {_WORKER_ROLE} USING ({service_principal_expression})"
    )
    op.execute(
        "CREATE POLICY service_principal_worker_lease_definer_select "
        "ON service_principal AS PERMISSIVE FOR SELECT "
        f"TO {_WORKER_LEASE_DEFINER_ROLE} "
        f"USING ({service_principal_expression})"
    )

    job_expression = _worker_expression(_JOB_TABLE)
    op.execute(
        "CREATE POLICY worker_noop_job_current_service_actor_select "
        "ON worker_noop_job AS PERMISSIVE FOR SELECT "
        f"TO {_WORKER_ROLE} USING ({job_expression})"
    )
    op.execute(
        "CREATE POLICY worker_noop_job_worker_lease_definer_select "
        "ON worker_noop_job AS PERMISSIVE FOR SELECT "
        f"TO {_WORKER_LEASE_DEFINER_ROLE} USING ({job_expression})"
    )
    op.execute(
        "CREATE POLICY worker_noop_job_worker_lease_definer_update "
        "ON worker_noop_job AS PERMISSIVE FOR UPDATE "
        f"TO {_WORKER_LEASE_DEFINER_ROLE} USING ({job_expression}) "
        f"WITH CHECK ({job_expression})"
    )

    op.execute(
        f"GRANT SELECT ON TABLE {_SERVICE_PRINCIPAL_TABLE} TO {_WORKER_ROLE}"
    )
    op.execute(
        f"GRANT SELECT ON TABLE {_JOB_TABLE} TO {_WORKER_ROLE}"
    )
    op.execute(
        f"GRANT SELECT ON TABLE {_SERVICE_PRINCIPAL_TABLE}, {_JOB_TABLE} "
        f"TO {_WORKER_LEASE_DEFINER_ROLE}"
    )
    op.execute(
        "GRANT UPDATE (state, signing_key_version, lease_nonce_digest, "
        "lease_issued_at, lease_expires_at, lease_redeemed_at, completed_at, "
        f"effect_count) ON TABLE {_JOB_TABLE} "
        f"TO {_WORKER_LEASE_DEFINER_ROLE}"
    )

    op.execute(
        f"""
        CREATE FUNCTION {_ISSUE_LEASE_FUNCTION}(
            requested_organization_id uuid,
            requested_job_id uuid,
            requested_service_principal_id uuid,
            requested_workload text,
            requested_worker_audience text,
            requested_signing_key_version bigint,
            requested_nonce bytea,
            requested_ttl_seconds integer
        ) RETURNS TABLE (issued_at timestamptz, expires_at timestamptz)
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE
            database_issued_at timestamptz;
            database_expires_at timestamptz;
        BEGIN
            IF SESSION_USER <> '{_CONTROL_ROLE}' THEN
                RAISE EXCEPTION USING
                    ERRCODE = '42501',
                    MESSAGE = 'worker lease operation was not accepted';
            END IF;
            IF requested_organization_id IS NULL
               OR requested_job_id IS NULL
               OR requested_service_principal_id IS NULL
               OR requested_workload IS NULL
               OR requested_worker_audience IS NULL
               OR requested_signing_key_version IS NULL
               OR requested_signing_key_version NOT BETWEEN 1 AND {_MAX_SIGNED_BIGINT}
               OR requested_nonce IS NULL
               OR pg_catalog.octet_length(requested_nonce) <> 32
               OR requested_ttl_seconds IS NULL
               OR requested_ttl_seconds NOT BETWEEN 1 AND {_MAX_LEASE_TTL_SECONDS}
            THEN
                RETURN;
            END IF;

            PERFORM pg_catalog.set_config(
                'app.organization_id', requested_organization_id::text, true
            );
            PERFORM pg_catalog.set_config('app.actor_kind', 'service', true);
            PERFORM pg_catalog.set_config(
                'app.service_principal_id',
                requested_service_principal_id::text,
                true
            );
            PERFORM pg_catalog.set_config(
                'app.workload', requested_workload, true
            );
            PERFORM pg_catalog.set_config(
                'app.worker_audience', requested_worker_audience, true
            );
            PERFORM pg_catalog.set_config(
                'app.operation', 'noop.complete', true
            );
            PERFORM pg_catalog.set_config(
                'app.worker_job_id', requested_job_id::text, true
            );

            database_issued_at := pg_catalog.date_trunc(
                'second', pg_catalog.transaction_timestamp()
            );
            database_expires_at := database_issued_at + pg_catalog.make_interval(
                secs => requested_ttl_seconds
            );

            UPDATE public.worker_noop_job AS job
            SET state = 'leased',
                signing_key_version = requested_signing_key_version,
                lease_nonce_digest = public.digest(requested_nonce, 'sha256'),
                lease_issued_at = database_issued_at,
                lease_expires_at = database_expires_at
            WHERE job.organization_id = requested_organization_id
              AND job.job_id = requested_job_id
              AND job.service_principal_id = requested_service_principal_id
              AND job.workload = requested_workload
              AND job.worker_audience = requested_worker_audience
              AND job.actor_kind = 'service'
              AND job.operation = 'noop.complete'
              AND job.state = 'available'
              AND job.effect_count = 0
              AND EXISTS (
                  SELECT 1
                  FROM public.service_principal AS principal
                  WHERE principal.organization_id = job.organization_id
                    AND principal.service_principal_id = job.service_principal_id
                    AND principal.workload = job.workload
                    AND principal.worker_audience = job.worker_audience
                    AND principal.operation = job.operation
                    AND principal.enabled IS TRUE
              )
            RETURNING job.lease_issued_at, job.lease_expires_at
            INTO issued_at, expires_at;

            IF issued_at IS NOT NULL THEN
                RETURN NEXT;
            END IF;
            RETURN;
        END;
        $function$
        """
    )

    op.execute(
        f"""
        CREATE FUNCTION {_COMPLETE_JOB_FUNCTION}(
            requested_organization_id uuid,
            requested_job_id uuid,
            requested_service_principal_id uuid,
            requested_workload text,
            requested_worker_audience text,
            requested_operation text,
            requested_signing_key_version bigint,
            requested_nonce bytea,
            requested_issued_at timestamptz,
            requested_expires_at timestamptz
        ) RETURNS TABLE (effect_count smallint)
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE
            database_redeemed_at timestamptz;
        BEGIN
            IF SESSION_USER <> '{_WORKER_ROLE}' THEN
                RAISE EXCEPTION USING
                    ERRCODE = '42501',
                    MESSAGE = 'worker lease operation was not accepted';
            END IF;
            IF requested_organization_id IS NULL
               OR requested_job_id IS NULL
               OR requested_service_principal_id IS NULL
               OR requested_workload IS NULL
               OR requested_worker_audience IS NULL
               OR requested_operation IS DISTINCT FROM 'noop.complete'
               OR requested_signing_key_version IS NULL
               OR requested_signing_key_version NOT BETWEEN 1 AND {_MAX_SIGNED_BIGINT}
               OR requested_nonce IS NULL
               OR pg_catalog.octet_length(requested_nonce) <> 32
               OR requested_issued_at IS NULL
               OR requested_expires_at IS NULL
            THEN
                RETURN;
            END IF;

            PERFORM pg_catalog.set_config(
                'app.organization_id', requested_organization_id::text, true
            );
            PERFORM pg_catalog.set_config('app.actor_kind', 'service', true);
            PERFORM pg_catalog.set_config(
                'app.service_principal_id',
                requested_service_principal_id::text,
                true
            );
            PERFORM pg_catalog.set_config(
                'app.workload', requested_workload, true
            );
            PERFORM pg_catalog.set_config(
                'app.worker_audience', requested_worker_audience, true
            );
            PERFORM pg_catalog.set_config(
                'app.operation', requested_operation, true
            );
            PERFORM pg_catalog.set_config(
                'app.worker_job_id', requested_job_id::text, true
            );

            database_redeemed_at := pg_catalog.statement_timestamp();

            UPDATE public.worker_noop_job AS job
            SET state = 'completed',
                lease_redeemed_at = database_redeemed_at,
                completed_at = database_redeemed_at,
                effect_count = 1
            WHERE job.organization_id = requested_organization_id
              AND job.job_id = requested_job_id
              AND job.service_principal_id = requested_service_principal_id
              AND job.workload = requested_workload
              AND job.worker_audience = requested_worker_audience
              AND job.actor_kind = 'service'
              AND job.operation = requested_operation
              AND job.state = 'leased'
              AND job.effect_count = 0
              AND job.signing_key_version = requested_signing_key_version
              AND job.lease_nonce_digest = public.digest(requested_nonce, 'sha256')
              AND job.lease_issued_at = requested_issued_at
              AND job.lease_expires_at = requested_expires_at
              AND database_redeemed_at >= job.lease_issued_at
              AND database_redeemed_at < job.lease_expires_at
              AND EXISTS (
                  SELECT 1
                  FROM public.service_principal AS principal
                  WHERE principal.organization_id = job.organization_id
                    AND principal.service_principal_id = job.service_principal_id
                    AND principal.workload = job.workload
                    AND principal.worker_audience = job.worker_audience
                    AND principal.operation = job.operation
                    AND principal.enabled IS TRUE
              )
            RETURNING job.effect_count INTO effect_count;

            IF effect_count IS NOT NULL THEN
                RETURN NEXT;
            END IF;
            RETURN;
        END;
        $function$
        """
    )

    for function_name, signature in (
        (_ISSUE_LEASE_FUNCTION, _ISSUE_LEASE_SIGNATURE),
        (_COMPLETE_JOB_FUNCTION, _COMPLETE_JOB_SIGNATURE),
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {function_name}{signature} FROM PUBLIC")
        for role in (_CONTROL_ROLE, _RUNTIME_ROLE, _WORKER_ROLE):
            op.execute(
                f"REVOKE ALL ON FUNCTION {function_name}{signature} FROM {role}"
            )

    op.execute(f"GRANT CREATE ON SCHEMA public TO {_WORKER_LEASE_DEFINER_ROLE}")
    for function_name, signature in (
        (_ISSUE_LEASE_FUNCTION, _ISSUE_LEASE_SIGNATURE),
        (_COMPLETE_JOB_FUNCTION, _COMPLETE_JOB_SIGNATURE),
    ):
        op.execute(
            f"ALTER FUNCTION {function_name}{signature} "
            f"OWNER TO {_WORKER_LEASE_DEFINER_ROLE}"
        )
    op.execute(f"SET LOCAL ROLE {_WORKER_LEASE_DEFINER_ROLE}")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION {_ISSUE_LEASE_FUNCTION}"
        f"{_ISSUE_LEASE_SIGNATURE} TO {_CONTROL_ROLE}"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION {_COMPLETE_JOB_FUNCTION}"
        f"{_COMPLETE_JOB_SIGNATURE} TO {_WORKER_ROLE}"
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_WORKER_LEASE_DEFINER_ROLE}")


def downgrade() -> None:
    """Remove only the bounded Issue #17 no-op WorkerLease persistence."""

    op.execute(f"SET LOCAL ROLE {_WORKER_LEASE_DEFINER_ROLE}")
    op.execute(
        f"DROP FUNCTION {_COMPLETE_JOB_FUNCTION}{_COMPLETE_JOB_SIGNATURE}"
    )
    op.execute(f"DROP FUNCTION {_ISSUE_LEASE_FUNCTION}{_ISSUE_LEASE_SIGNATURE}")
    op.execute("RESET ROLE")
    op.drop_table(_JOB_TABLE)
    op.drop_table(_SERVICE_PRINCIPAL_TABLE)
