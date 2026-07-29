"""Add the exact-job Supply execution and opaque checkpoint boundary.

Revision ID: 20260729_0041
Revises: 20260727_0040
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260729_0041"
down_revision: str | None = "20260727_0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MIGRATOR = "context_engine_migrator"
_CONTROL = "context_engine_control"
_WORKER = "context_engine_worker"
_DEFINER = "context_engine_worker_lease_definer"
_WORKLOAD = "supply.connector"
_AUDIENCE = "context-engine-connector-runner"
_OPERATION = "connector.execute"
_FILE_OPERATION_FENCES = (
    "context-engine.file-change-scheduling-migration-fence",
    "context-engine.file-dispatch-migration-fence",
    "context-engine.file-status-migration-fence",
)
_TABLES = (
    "supply_connector_job",
    "supply_connector_staged_page",
    "supply_connector_accepted_page",
    "supply_connector_checkpoint",
)


def _acquire_file_operation_fences() -> None:
    connection = op.get_bind()
    for migration_fence in _FILE_OPERATION_FENCES:
        connection.exec_driver_sql(
            "SELECT pg_catalog.pg_advisory_xact_lock("
            "pg_catalog.hashtextextended(%s, 0))",
            (migration_fence,),
        )


def _secure_table(table_name: str, *, write_commands: tuple[str, ...]) -> None:
    for role in ("PUBLIC", _CONTROL, _WORKER):
        op.execute(f"REVOKE ALL ON TABLE {table_name} FROM {role}")
    op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table_name}_migrator_administration ON {table_name} "
        f"FOR ALL TO {_MIGRATOR} USING (true) WITH CHECK (true)"
    )
    tenant = (
        "organization_id = NULLIF("
        "current_setting('app.organization_id', true), ''"
        ")::uuid AND worker_job_id = NULLIF("
        "current_setting('app.worker_job_id', true), ''"
        ")::uuid"
    )
    op.execute(
        f"CREATE POLICY {table_name}_supply_definer_select ON {table_name} "
        f"FOR SELECT TO {_DEFINER} USING ({tenant})"
    )
    if "INSERT" in write_commands:
        op.execute(
            f"CREATE POLICY {table_name}_supply_definer_insert ON {table_name} "
            f"FOR INSERT TO {_DEFINER} WITH CHECK ({tenant})"
        )
    if "UPDATE" in write_commands:
        op.execute(
            f"CREATE POLICY {table_name}_supply_definer_update ON {table_name} "
            f"FOR UPDATE TO {_DEFINER} USING ({tenant}) WITH CHECK ({tenant})"
        )


def _create_tables() -> None:
    op.create_table(
        "supply_connector_job",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("worker_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "service_principal_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("workload", sa.Text(), nullable=False),
        sa.Column("worker_audience", sa.Text(), nullable=False),
        sa.Column("actor_kind", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("lease_generation", sa.BigInteger(), nullable=False),
        sa.Column("signing_key_version", sa.BigInteger(), nullable=True),
        sa.Column("lease_nonce_digest", postgresql.BYTEA(), nullable=True),
        sa.Column("lease_issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("policy_epoch", sa.BigInteger(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column(
            "allowed_source_version_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=True,
        ),
        sa.Column("allowed_operations", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column(
            "service_actor_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id", "worker_job_id", name="pk_supply_connector_job"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "source_id",
            "source_version_id",
            "worker_job_id",
            name="uq_supply_connector_job_exact_binding",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_id", "source_version_id"],
            [
                "source_version.organization_id",
                "source_version.source_id",
                "source_version.version_id",
            ],
            name="fk_supply_connector_job_source_version_exact",
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
            name="fk_supply_connector_job_service_actor_exact",
        ),
        sa.CheckConstraint(
            f"workload = '{_WORKLOAD}' AND worker_audience = '{_AUDIENCE}' "
            f"AND actor_kind = 'service' AND operation = '{_OPERATION}'",
            name="ck_supply_connector_job_service_actor",
        ),
        sa.CheckConstraint(
            "state IN ('available', 'leased', 'running', 'completed')",
            name="ck_supply_connector_job_state",
        ),
        sa.CheckConstraint(
            "lease_generation >= 0 AND lease_generation <= 9223372036854775807",
            name="ck_supply_connector_job_lease_generation",
        ),
        sa.CheckConstraint(
            "(state = 'available' AND lease_generation = 0 "
            "AND signing_key_version IS NULL AND lease_nonce_digest IS NULL "
            "AND lease_issued_at IS NULL AND lease_expires_at IS NULL "
            "AND policy_epoch IS NULL AND idempotency_key IS NULL "
            "AND allowed_source_version_ids IS NULL "
            "AND allowed_operations IS NULL "
            "AND service_actor_expires_at IS NULL AND redeemed_at IS NULL "
            "AND completed_at IS NULL) OR "
            "(state IN ('leased', 'running') AND lease_generation > 0 "
            "AND signing_key_version > 0 "
            "AND octet_length(lease_nonce_digest) = 32 "
            "AND lease_issued_at IS NOT NULL "
            "AND lease_expires_at > lease_issued_at "
            "AND policy_epoch > 0 "
            "AND idempotency_key ~ '^[0-9a-f]{64}$' "
            "AND allowed_source_version_ids = ARRAY[source_version_id] "
            "AND allowed_operations = ARRAY['connector.execute']::text[] "
            "AND service_actor_expires_at >= lease_expires_at "
            "AND redeemed_at IS NULL "
            "AND completed_at IS NULL) OR "
            "(state = 'completed' AND lease_generation > 0 "
            "AND signing_key_version > 0 "
            "AND octet_length(lease_nonce_digest) = 32 "
            "AND lease_issued_at IS NOT NULL "
            "AND lease_expires_at > lease_issued_at "
            "AND policy_epoch > 0 "
            "AND idempotency_key ~ '^[0-9a-f]{64}$' "
            "AND allowed_source_version_ids = ARRAY[source_version_id] "
            "AND allowed_operations = ARRAY['connector.execute']::text[] "
            "AND service_actor_expires_at >= lease_expires_at "
            "AND redeemed_at = completed_at "
            "AND completed_at >= lease_issued_at)",
            name="ck_supply_connector_job_lease_state",
        ),
    )
    op.create_table(
        "supply_connector_staged_page",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("worker_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_ref", sa.Text(), nullable=False),
        sa.Column("page_payload", postgresql.BYTEA(), nullable=False),
        sa.Column("payload_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("checkpoint_proposal_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("staged_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "source_version_id",
            "worker_job_id",
            "page_ref",
            name="pk_supply_connector_staged_page",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "source_id",
            "source_version_id",
            "worker_job_id",
            "page_ref",
            name="uq_supply_connector_staged_page_exact",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_id", "source_version_id", "worker_job_id"],
            [
                "supply_connector_job.organization_id",
                "supply_connector_job.source_id",
                "supply_connector_job.source_version_id",
                "supply_connector_job.worker_job_id",
            ],
            name="fk_supply_connector_staged_page_job_exact",
        ),
        sa.CheckConstraint(
            "btrim(page_ref) <> '' AND char_length(page_ref) <= 512 "
            "AND page_ref !~ '[[:space:]]'",
            name="ck_supply_connector_staged_page_ref",
        ),
        sa.CheckConstraint(
            "octet_length(page_payload) BETWEEN 1 AND 268435456",
            name="ck_supply_connector_staged_page_payload_bounds",
        ),
        sa.CheckConstraint(
            "octet_length(payload_digest) = 32",
            name="ck_supply_connector_staged_page_payload_digest",
        ),
        sa.CheckConstraint(
            "octet_length(checkpoint_proposal_digest) = 32",
            name="ck_supply_connector_staged_page_checkpoint_digest",
        ),
    )
    op.create_table(
        "supply_connector_accepted_page",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("worker_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("accepted_ordinal", sa.BigInteger(), nullable=False),
        sa.Column("page_ref", sa.Text(), nullable=False),
        sa.Column("checkpoint_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "worker_job_id",
            "accepted_ordinal",
            name="pk_supply_connector_accepted_page",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "source_version_id",
            "worker_job_id",
            "page_ref",
            name="uq_supply_connector_accepted_page_replay",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "source_id",
            "source_version_id",
            "worker_job_id",
            "accepted_ordinal",
            "page_ref",
            name="uq_supply_connector_accepted_page_exact_lineage",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_id", "source_version_id", "worker_job_id"],
            [
                "supply_connector_job.organization_id",
                "supply_connector_job.source_id",
                "supply_connector_job.source_version_id",
                "supply_connector_job.worker_job_id",
            ],
            name="fk_supply_connector_accepted_page_job_exact",
        ),
        sa.ForeignKeyConstraint(
            [
                "organization_id",
                "source_id",
                "source_version_id",
                "worker_job_id",
                "page_ref",
            ],
            [
                "supply_connector_staged_page.organization_id",
                "supply_connector_staged_page.source_id",
                "supply_connector_staged_page.source_version_id",
                "supply_connector_staged_page.worker_job_id",
                "supply_connector_staged_page.page_ref",
            ],
            name="fk_supply_connector_accepted_page_staged_exact",
        ),
        sa.CheckConstraint(
            "accepted_ordinal > 0 AND accepted_ordinal <= 9223372036854775807",
            name="ck_supply_connector_accepted_page_ordinal",
        ),
        sa.CheckConstraint(
            "btrim(page_ref) <> '' AND char_length(page_ref) <= 512 "
            "AND page_ref !~ '[[:space:]]'",
            name="ck_supply_connector_accepted_page_ref",
        ),
        sa.CheckConstraint(
            "octet_length(checkpoint_digest) = 32",
            name="ck_supply_connector_accepted_page_checkpoint_digest",
        ),
    )
    op.create_table(
        "supply_connector_checkpoint",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("worker_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("accepted_ordinal", sa.BigInteger(), nullable=False),
        sa.Column("accepted_page_ref", sa.Text(), nullable=False),
        sa.Column("opaque_checkpoint", postgresql.BYTEA(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "source_version_id",
            "worker_job_id",
            name="pk_supply_connector_checkpoint",
        ),
        sa.ForeignKeyConstraint(
            [
                "organization_id",
                "source_id",
                "source_version_id",
                "worker_job_id",
                "accepted_ordinal",
                "accepted_page_ref",
            ],
            [
                "supply_connector_accepted_page.organization_id",
                "supply_connector_accepted_page.source_id",
                "supply_connector_accepted_page.source_version_id",
                "supply_connector_accepted_page.worker_job_id",
                "supply_connector_accepted_page.accepted_ordinal",
                "supply_connector_accepted_page.page_ref",
            ],
            name="fk_supply_connector_checkpoint_accepted_page_exact",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_id", "source_version_id", "worker_job_id"],
            [
                "supply_connector_job.organization_id",
                "supply_connector_job.source_id",
                "supply_connector_job.source_version_id",
                "supply_connector_job.worker_job_id",
            ],
            name="fk_supply_connector_checkpoint_job_exact",
        ),
        sa.CheckConstraint(
            "accepted_ordinal > 0 AND accepted_ordinal <= 9223372036854775807",
            name="ck_supply_connector_checkpoint_ordinal",
        ),
        sa.CheckConstraint(
            "btrim(accepted_page_ref) <> '' "
            "AND char_length(accepted_page_ref) <= 512 "
            "AND accepted_page_ref !~ '[[:space:]]'",
            name="ck_supply_connector_checkpoint_page_ref",
        ),
        sa.CheckConstraint(
            "octet_length(opaque_checkpoint) BETWEEN 1 AND 1048576",
            name="ck_supply_connector_checkpoint_opaque_bounds",
        ),
    )


def _create_functions() -> None:
    actor_context = f"""
        PERFORM set_config('app.actor_kind', 'service', true);
        PERFORM set_config('app.service_principal_id',
            requested_service_principal_id::text, true);
        PERFORM set_config('app.workload', '{_WORKLOAD}', true);
        PERFORM set_config('app.worker_audience', '{_AUDIENCE}', true);
        PERFORM set_config('app.operation', '{_OPERATION}', true);
        PERFORM set_config('app.allowed_source_version_ids',
            array_to_string(requested_allowed_source_version_ids, ','), true);
        PERFORM set_config('app.allowed_operations',
            array_to_string(requested_allowed_operations, ','), true);
        PERFORM set_config('app.policy_epoch',
            requested_policy_epoch::text, true);
        PERFORM set_config('app.service_actor_expires_at',
            requested_service_actor_expires_at::text, true);
        PERFORM set_config('app.worker_lease_idempotency_key',
            requested_idempotency_key, true);
    """
    op.execute(
        f"""
        CREATE FUNCTION public.context_supply_issue_connector_lease(
            requested_organization_id uuid,
            requested_source_id uuid,
            requested_source_version_id uuid,
            requested_worker_job_id uuid,
            requested_service_principal_id uuid,
            requested_signing_key_version bigint,
            requested_nonce bytea,
            requested_lease_ttl_seconds integer
        ) RETURNS TABLE (
            issued_at timestamptz,
            expires_at timestamptz,
            lease_generation bigint,
            policy_epoch bigint,
            idempotency_key text,
            service_actor_expires_at timestamptz
        )
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE now_at timestamptz := date_trunc('second', clock_timestamp());
        BEGIN
            IF session_user <> '{_CONTROL}'
               OR requested_organization_id IS NULL
               OR requested_source_id IS NULL
               OR requested_source_version_id IS NULL
               OR requested_worker_job_id IS NULL
               OR requested_service_principal_id IS NULL
               OR requested_signing_key_version <= 0
               OR octet_length(requested_nonce) <> 32
               OR requested_lease_ttl_seconds NOT BETWEEN 1 AND 3600 THEN
                RETURN;
            END IF;
            PERFORM set_config(
                'app.organization_id', requested_organization_id::text, true
            );
            PERFORM set_config(
                'app.worker_job_id', requested_worker_job_id::text, true
            );
            RETURN QUERY
            UPDATE public.supply_connector_job AS job
               SET state = 'leased',
                   lease_generation = job.lease_generation + 1,
                   signing_key_version = requested_signing_key_version,
                   lease_nonce_digest = digest(requested_nonce, 'sha256'),
                   lease_issued_at = now_at,
                   lease_expires_at = now_at
                       + make_interval(secs => requested_lease_ttl_seconds),
                   policy_epoch = epoch.policy_epoch,
                   idempotency_key = encode(
                       digest(
                           requested_organization_id::text || ':' ||
                           requested_worker_job_id::text || ':' ||
                           (job.lease_generation + 1)::text,
                           'sha256'
                       ),
                       'hex'
                   ),
                   allowed_source_version_ids = ARRAY[
                       requested_source_version_id
                   ],
                   allowed_operations = ARRAY['connector.execute']::text[],
                   service_actor_expires_at = now_at
                       + make_interval(secs => requested_lease_ttl_seconds),
                   redeemed_at = NULL,
                   completed_at = NULL
              FROM public.organization_policy_epoch AS epoch
             WHERE job.organization_id = requested_organization_id
               AND job.source_id = requested_source_id
               AND job.source_version_id = requested_source_version_id
               AND job.worker_job_id = requested_worker_job_id
               AND job.service_principal_id = requested_service_principal_id
               AND job.workload = '{_WORKLOAD}'
               AND job.worker_audience = '{_AUDIENCE}'
               AND job.actor_kind = 'service'
               AND job.operation = '{_OPERATION}'
               AND job.state = 'available'
               AND epoch.organization_id = job.organization_id
               AND EXISTS (
                   SELECT 1 FROM public.service_principal AS actor
                   WHERE actor.organization_id = job.organization_id
                     AND actor.service_principal_id = job.service_principal_id
                     AND actor.workload = job.workload
                     AND actor.worker_audience = job.worker_audience
                     AND actor.operation = job.operation
                     AND actor.enabled IS TRUE
               )
               AND EXISTS (
                   SELECT 1 FROM public.context_source AS source
                   WHERE source.organization_id = job.organization_id
                     AND source.source_id = job.source_id
                     AND source.active_version_id = job.source_version_id
                     AND source.lifecycle_state = 'active'
               )
             RETURNING job.lease_issued_at, job.lease_expires_at,
                       job.lease_generation, job.policy_epoch,
                       job.idempotency_key, job.service_actor_expires_at;
        END;
        $function$
        """
    )
    verification = f"""
        job.organization_id = requested_organization_id
        AND job.source_version_id = requested_source_version_id
        AND job.worker_job_id = requested_worker_job_id
        AND job.service_principal_id = requested_service_principal_id
        AND job.workload = '{_WORKLOAD}'
        AND job.worker_audience = '{_AUDIENCE}'
        AND job.actor_kind = 'service'
        AND job.operation = '{_OPERATION}'
        AND job.lease_generation = requested_lease_generation
        AND job.signing_key_version = requested_signing_key_version
        AND job.lease_nonce_digest = digest(requested_nonce, 'sha256')
        AND job.lease_issued_at = requested_issued_at
        AND job.lease_expires_at = requested_expires_at
        AND job.policy_epoch = requested_policy_epoch
        AND job.idempotency_key = requested_idempotency_key
        AND job.allowed_source_version_ids = requested_allowed_source_version_ids
        AND job.allowed_operations = requested_allowed_operations
        AND job.service_actor_expires_at = requested_service_actor_expires_at
        AND clock_timestamp() < job.lease_expires_at
        AND clock_timestamp() < job.service_actor_expires_at
        AND job.allowed_source_version_ids = ARRAY[requested_source_version_id]
        AND job.allowed_operations = ARRAY['connector.execute']::text[]
        AND job.idempotency_key IS NOT NULL
        AND EXISTS (
            SELECT 1 FROM public.organization_policy_epoch AS epoch
            WHERE epoch.organization_id = job.organization_id
              AND epoch.policy_epoch = job.policy_epoch
        )
        AND EXISTS (
            SELECT 1 FROM public.service_principal AS actor
            WHERE actor.organization_id = job.organization_id
              AND actor.service_principal_id = job.service_principal_id
              AND actor.workload = job.workload
              AND actor.worker_audience = job.worker_audience
              AND actor.operation = job.operation
              AND actor.enabled IS TRUE
        )
        AND EXISTS (
            SELECT 1 FROM public.context_source AS source
            WHERE source.organization_id = job.organization_id
              AND source.source_id = job.source_id
              AND source.active_version_id = job.source_version_id
              AND source.lifecycle_state = 'active'
        )
    """
    op.execute(
        f"""
        CREATE FUNCTION public.context_supply_load_staged_connector_page(
            requested_organization_id uuid,
            requested_source_version_id uuid,
            requested_worker_job_id uuid,
            requested_service_principal_id uuid,
            requested_page_ref text,
            requested_lease_generation bigint,
            requested_signing_key_version bigint,
            requested_nonce bytea,
            requested_issued_at timestamptz,
            requested_expires_at timestamptz,
            requested_policy_epoch bigint,
            requested_idempotency_key text,
            requested_allowed_source_version_ids uuid[],
            requested_allowed_operations text[],
            requested_service_actor_expires_at timestamptz
        ) RETURNS TABLE (page_payload bytea)
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF session_user <> '{_WORKER}' THEN RETURN; END IF;
            PERFORM set_config(
                'app.organization_id', requested_organization_id::text, true
            );
            PERFORM set_config(
                'app.worker_job_id', requested_worker_job_id::text, true
            );
            {actor_context}
            RETURN QUERY
            SELECT staged.page_payload
              FROM public.supply_connector_job AS job
              JOIN public.supply_connector_staged_page AS staged
                ON staged.organization_id = job.organization_id
               AND staged.source_id = job.source_id
               AND staged.source_version_id = job.source_version_id
               AND staged.worker_job_id = job.worker_job_id
             WHERE {verification}
               AND job.state IN ('leased', 'running', 'completed')
               AND staged.page_ref = requested_page_ref;
        END;
        $function$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION public.context_supply_load_connector_checkpoint(
            requested_organization_id uuid,
            requested_source_version_id uuid,
            requested_worker_job_id uuid,
            requested_service_principal_id uuid,
            requested_lease_generation bigint,
            requested_signing_key_version bigint,
            requested_nonce bytea,
            requested_issued_at timestamptz,
            requested_expires_at timestamptz,
            requested_policy_epoch bigint,
            requested_idempotency_key text,
            requested_allowed_source_version_ids uuid[],
            requested_allowed_operations text[],
            requested_service_actor_expires_at timestamptz
        ) RETURNS TABLE (opaque_checkpoint bytea, job_state text)
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF session_user <> '{_WORKER}' THEN RETURN; END IF;
            PERFORM set_config(
                'app.organization_id', requested_organization_id::text, true
            );
            PERFORM set_config(
                'app.worker_job_id', requested_worker_job_id::text, true
            );
            {actor_context}
            RETURN QUERY
            SELECT checkpoint.opaque_checkpoint, job.state
              FROM public.supply_connector_job AS job
              LEFT JOIN public.supply_connector_checkpoint AS checkpoint
                ON checkpoint.organization_id = job.organization_id
               AND checkpoint.source_id = job.source_id
               AND checkpoint.source_version_id = job.source_version_id
               AND checkpoint.worker_job_id = job.worker_job_id
             WHERE {verification}
               AND job.state IN ('leased', 'running', 'completed');
        END;
        $function$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION public.context_supply_accept_connector_page(
            requested_organization_id uuid,
            requested_source_version_id uuid,
            requested_worker_job_id uuid,
            requested_service_principal_id uuid,
            requested_page_ref text,
            requested_page_payload bytea,
            requested_lease_generation bigint,
            requested_signing_key_version bigint,
            requested_nonce bytea,
            requested_issued_at timestamptz,
            requested_expires_at timestamptz,
            requested_policy_epoch bigint,
            requested_idempotency_key text,
            requested_allowed_source_version_ids uuid[],
            requested_allowed_operations text[],
            requested_service_actor_expires_at timestamptz
        ) RETURNS TABLE (accepted_ordinal bigint)
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE job_row public.supply_connector_job%ROWTYPE;
        DECLARE ordinal bigint;
        DECLARE prior_digest bytea;
        DECLARE staged_payload_digest bytea;
        DECLARE staged_checkpoint bytea;
        DECLARE staged_checkpoint_digest bytea;
        DECLARE staged_terminal boolean;
        DECLARE payload_document jsonb;
        DECLARE now_at timestamptz := clock_timestamp();
        BEGIN
            IF session_user <> '{_WORKER}'
               OR btrim(requested_page_ref) = ''
               OR char_length(requested_page_ref) > 512
               OR requested_page_ref ~ '[[:space:]]'
               OR octet_length(requested_page_payload)
                    NOT BETWEEN 1 AND 268435456 THEN
                RETURN;
            END IF;
            BEGIN
                payload_document := convert_from(
                    requested_page_payload, 'UTF8'
                )::jsonb;
                IF payload_document->'binding'->>'organization_id'
                        <> requested_organization_id::text
                   OR payload_document->'binding'->>'source_version_id'
                        <> requested_source_version_id::text
                   OR payload_document->'binding'->>'worker_job_id'
                        <> requested_worker_job_id::text
                   OR payload_document->>'page_ref' <> requested_page_ref
                   OR jsonb_typeof(payload_document->'terminal') <> 'boolean'
                THEN RETURN; END IF;
                staged_checkpoint := decode(
                    payload_document->>'checkpoint_proposal', 'base64'
                );
                staged_terminal := (payload_document->>'terminal')::boolean;
                staged_payload_digest := digest(
                    requested_page_payload, 'sha256'
                );
                staged_checkpoint_digest := digest(
                    staged_checkpoint, 'sha256'
                );
            EXCEPTION WHEN OTHERS THEN
                RETURN;
            END;
            IF octet_length(staged_checkpoint) NOT BETWEEN 1 AND 1048576
            THEN RETURN; END IF;
            PERFORM set_config(
                'app.organization_id', requested_organization_id::text, true
            );
            PERFORM set_config(
                'app.worker_job_id', requested_worker_job_id::text, true
            );
            {actor_context}
            SELECT job.* INTO job_row
              FROM public.supply_connector_job AS job
             WHERE {verification}
               AND job.state IN ('leased', 'running')
             FOR UPDATE OF job;
            IF NOT FOUND THEN RETURN; END IF;

            INSERT INTO public.supply_connector_staged_page (
                organization_id, source_id, source_version_id, worker_job_id,
                page_ref, page_payload, payload_digest,
                checkpoint_proposal_digest, staged_at
            ) VALUES (
                requested_organization_id, job_row.source_id,
                requested_source_version_id, requested_worker_job_id,
                requested_page_ref, requested_page_payload,
                staged_payload_digest, staged_checkpoint_digest, now_at
            ) ON CONFLICT (
                organization_id, source_version_id, worker_job_id, page_ref
            ) DO NOTHING;
            IF NOT EXISTS (
                SELECT 1 FROM public.supply_connector_staged_page AS staged
                WHERE staged.organization_id = requested_organization_id
                  AND staged.source_id = job_row.source_id
                  AND staged.source_version_id = requested_source_version_id
                  AND staged.worker_job_id = requested_worker_job_id
                  AND staged.page_ref = requested_page_ref
                  AND staged.payload_digest = staged_payload_digest
                  AND staged.checkpoint_proposal_digest = staged_checkpoint_digest
            ) THEN RETURN; END IF;

            SELECT page.accepted_ordinal, page.checkpoint_digest
              INTO ordinal, prior_digest
              FROM public.supply_connector_accepted_page AS page
             WHERE page.organization_id = requested_organization_id
               AND page.source_version_id = requested_source_version_id
               AND page.worker_job_id = requested_worker_job_id
               AND page.page_ref = requested_page_ref;
            IF FOUND THEN
                IF prior_digest <> staged_checkpoint_digest
                THEN RETURN;
                END IF;
                RETURN QUERY SELECT ordinal;
                RETURN;
            END IF;

            SELECT COALESCE(max(page.accepted_ordinal), 0) + 1 INTO ordinal
              FROM public.supply_connector_accepted_page AS page
             WHERE page.organization_id = requested_organization_id
               AND page.worker_job_id = requested_worker_job_id;
            INSERT INTO public.supply_connector_accepted_page (
                organization_id, source_id, source_version_id, worker_job_id,
                accepted_ordinal, page_ref, checkpoint_digest, accepted_at
            ) VALUES (
                requested_organization_id, job_row.source_id,
                requested_source_version_id, requested_worker_job_id,
                ordinal, requested_page_ref,
                staged_checkpoint_digest, now_at
            );
            INSERT INTO public.supply_connector_checkpoint (
                organization_id, source_id, source_version_id, worker_job_id,
                accepted_ordinal, accepted_page_ref, opaque_checkpoint,
                updated_at
            ) VALUES (
                requested_organization_id, job_row.source_id,
                requested_source_version_id, requested_worker_job_id,
                ordinal, requested_page_ref, staged_checkpoint,
                now_at
            ) ON CONFLICT (
                organization_id, source_version_id, worker_job_id
            ) DO UPDATE SET
                source_id = EXCLUDED.source_id,
                accepted_ordinal = EXCLUDED.accepted_ordinal,
                accepted_page_ref = EXCLUDED.accepted_page_ref,
                opaque_checkpoint = EXCLUDED.opaque_checkpoint,
                updated_at = EXCLUDED.updated_at;
            UPDATE public.supply_connector_job AS job
               SET state = CASE
                       WHEN staged_terminal THEN 'completed' ELSE 'running'
                   END,
                   completed_at = CASE
                       WHEN staged_terminal THEN now_at ELSE NULL
                   END,
                   redeemed_at = CASE
                       WHEN staged_terminal THEN now_at ELSE NULL
                   END
             WHERE job.organization_id = requested_organization_id
               AND job.worker_job_id = requested_worker_job_id;
            RETURN QUERY SELECT ordinal;
        END;
        $function$
        """
    )


def upgrade() -> None:
    """Create the CE-owned runner execution seam and pre-publication staging."""

    _acquire_file_operation_fences()
    for name in (
        "ck_service_principal_workload_issue17",
        "ck_service_principal_worker_audience_issue17",
        "ck_service_principal_operation_noop_complete",
        "ck_service_principal_workload_operation_binding",
    ):
        op.drop_constraint(name, "service_principal", type_="check")
    op.create_check_constraint(
        "ck_service_principal_workload_issue17",
        "service_principal",
        "workload IN ('supply.noop', 'supply.file-import', 'supply.connector')",
    )
    op.create_check_constraint(
        "ck_service_principal_worker_audience_issue17",
        "service_principal",
        "worker_audience IN ("
        "'context-engine-worker', 'context-engine-connector-runner'"
        ")",
    )
    op.create_check_constraint(
        "ck_service_principal_operation_noop_complete",
        "service_principal",
        "operation IN ('noop.complete', 'file.import', 'connector.execute')",
    )
    op.create_check_constraint(
        "ck_service_principal_workload_operation_binding",
        "service_principal",
        "(workload = 'supply.noop' "
        "AND worker_audience = 'context-engine-worker' "
        "AND operation = 'noop.complete') OR "
        "(workload = 'supply.file-import' "
        "AND worker_audience = 'context-engine-worker' "
        "AND operation = 'file.import') OR "
        "(workload = 'supply.connector' "
        "AND worker_audience = 'context-engine-connector-runner' "
        "AND operation = 'connector.execute')",
    )
    op.execute(
        "CREATE POLICY service_principal_supply_connector_definer_select "
        "ON service_principal FOR SELECT TO context_engine_worker_lease_definer "
        "USING (organization_id = NULLIF(current_setting("
        "'app.organization_id', true), '')::uuid "
        "AND workload = 'supply.connector' "
        "AND worker_audience = 'context-engine-connector-runner' "
        "AND operation = 'connector.execute' AND enabled IS TRUE)"
    )
    op.execute(
        "CREATE POLICY organization_policy_epoch_supply_connector_definer_select "
        "ON organization_policy_epoch FOR SELECT "
        "TO context_engine_worker_lease_definer USING ("
        "organization_id = NULLIF(current_setting("
        "'app.organization_id', true), '')::uuid)"
    )
    _create_tables()
    _secure_table("supply_connector_job", write_commands=("UPDATE",))
    _secure_table("supply_connector_staged_page", write_commands=("INSERT",))
    _secure_table("supply_connector_accepted_page", write_commands=("INSERT",))
    _secure_table(
        "supply_connector_checkpoint",
        write_commands=("INSERT", "UPDATE"),
    )
    op.execute(f"GRANT SELECT, UPDATE ON TABLE supply_connector_job TO {_DEFINER}")
    op.execute(f"GRANT SELECT ON TABLE organization_policy_epoch TO {_DEFINER}")
    op.execute(
        f"GRANT SELECT, INSERT ON TABLE supply_connector_staged_page TO {_DEFINER}"
    )
    op.execute(
        f"GRANT SELECT, INSERT ON TABLE supply_connector_accepted_page TO {_DEFINER}"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON TABLE supply_connector_checkpoint "
        f"TO {_DEFINER}"
    )
    _create_functions()
    issue_signature = (
        "public.context_supply_issue_connector_lease"
        "(uuid,uuid,uuid,uuid,uuid,bigint,bytea,integer)"
    )
    load_signature = (
        "public.context_supply_load_connector_checkpoint"
        "(uuid,uuid,uuid,uuid,bigint,bigint,bytea,timestamp with time zone,"
        "timestamp with time zone,bigint,text,uuid[],text[],"
        "timestamp with time zone)"
    )
    load_staged_signature = (
        "public.context_supply_load_staged_connector_page"
        "(uuid,uuid,uuid,uuid,text,bigint,bigint,bytea,"
        "timestamp with time zone,timestamp with time zone,bigint,text,"
        "uuid[],text[],timestamp with time zone)"
    )
    accept_signature = (
        "public.context_supply_accept_connector_page"
        "(uuid,uuid,uuid,uuid,text,bytea,bigint,bigint,bytea,"
        "timestamp with time zone,timestamp with time zone,bigint,text,"
        "uuid[],text[],timestamp with time zone)"
    )
    signatures = (
        issue_signature,
        load_signature,
        load_staged_signature,
        accept_signature,
    )
    for signature in signatures:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    for signature in signatures:
        op.execute(f"ALTER FUNCTION {signature} OWNER TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(f"GRANT EXECUTE ON FUNCTION {issue_signature} TO {_CONTROL}")
    op.execute(f"GRANT EXECUTE ON FUNCTION {load_signature} TO {_WORKER}")
    op.execute(f"GRANT EXECUTE ON FUNCTION {load_staged_signature} TO {_WORKER}")
    op.execute(f"GRANT EXECUTE ON FUNCTION {accept_signature} TO {_WORKER}")
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


def downgrade() -> None:
    """Remove only the Supply connector execution/checkpoint boundary."""

    _acquire_file_operation_fences()
    op.execute("LOCK TABLE public.file_acquisition IN ACCESS EXCLUSIVE MODE")
    op.execute(
        "DROP FUNCTION public.context_supply_accept_connector_page"
        "(uuid,uuid,uuid,uuid,text,bytea,bigint,bigint,bytea,"
        "timestamp with time zone,timestamp with time zone,bigint,text,"
        "uuid[],text[],timestamp with time zone)"
    )
    op.execute(
        "DROP FUNCTION public.context_supply_load_staged_connector_page"
        "(uuid,uuid,uuid,uuid,text,bigint,bigint,bytea,"
        "timestamp with time zone,timestamp with time zone,bigint,text,"
        "uuid[],text[],timestamp with time zone)"
    )
    op.execute(
        "DROP FUNCTION public.context_supply_load_connector_checkpoint"
        "(uuid,uuid,uuid,uuid,bigint,bigint,bytea,timestamp with time zone,"
        "timestamp with time zone,bigint,text,uuid[],text[],"
        "timestamp with time zone)"
    )
    op.execute(
        "DROP FUNCTION public.context_supply_issue_connector_lease"
        "(uuid,uuid,uuid,uuid,uuid,bigint,bytea,integer)"
    )
    for table_name in reversed(_TABLES):
        op.drop_table(table_name)
    op.execute(
        "DROP POLICY organization_policy_epoch_supply_connector_definer_select "
        "ON organization_policy_epoch"
    )
    op.execute(f"REVOKE SELECT ON TABLE organization_policy_epoch FROM {_DEFINER}")
    op.execute(
        "DROP POLICY service_principal_supply_connector_definer_select "
        "ON service_principal"
    )
    for name in (
        "ck_service_principal_workload_issue17",
        "ck_service_principal_worker_audience_issue17",
        "ck_service_principal_operation_noop_complete",
        "ck_service_principal_workload_operation_binding",
    ):
        op.drop_constraint(name, "service_principal", type_="check")
    op.create_check_constraint(
        "ck_service_principal_workload_issue17",
        "service_principal",
        "workload IN ('supply.noop', 'supply.file-import')",
    )
    op.create_check_constraint(
        "ck_service_principal_worker_audience_issue17",
        "service_principal",
        "worker_audience = 'context-engine-worker'",
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
