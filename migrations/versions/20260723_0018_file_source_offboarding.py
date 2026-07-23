"""Disable one File source before asynchronous retained-content cleanup.

Revision ID: 20260723_0018
Revises: 20260723_0017
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260723_0018"
down_revision: str | None = "20260723_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MIGRATOR = "context_engine_migrator"
_CONTROL = "context_engine_control"
_RUNTIME = "context_engine_runtime"
_WORKER = "context_engine_worker"
_WORKER_DEFINER = "context_engine_worker_lease_definer"
_ACCESS_DEFINER = "context_engine_access_policy_definer"
_TABLE = "file_source_cleanup_intent"
_FUNCTION = "context_control_offboard_file_source"
_SIGNATURE = "(uuid, uuid, uuid)"
_RUNTIME_SOURCE_FUNCTION = "context_runtime_file_source_lifecycle_allows"
_RUNTIME_SOURCE_SIGNATURE = "(uuid, text)"
_PREPARE_SIGNATURE = (
    "(uuid, uuid, uuid, uuid, uuid, text, text, uuid, bigint, text, text, uuid)"
)
_MAX_BIGINT = 9223372036854775807
_LEASE_CONSTRAINT = (
    "lease_generation > 0 AND signing_key_version > 0 AND "
    "octet_length(lease_nonce_digest) = 32 AND "
    "lease_issued_at IS NOT NULL AND lease_expires_at > lease_issued_at"
)
_NO_LINEAGE_CONSTRAINT = (
    "resource_ref IS NULL AND revision_id IS NULL AND fragment_ref IS NULL"
)
_DURABLE_IDENTITY_CONSTRAINT = (
    "resource_ref IS NOT NULL AND revision_id IS NOT NULL"
)


def _noncancelled_job_constraint(active_constraint: str | None = None) -> str:
    active = f"{active_constraint} AND " if active_constraint is not None else ""
    return (
        f"(state = 'available' AND {active}lease_generation = 0 AND "
        "signing_key_version IS NULL AND lease_nonce_digest IS NULL AND "
        "lease_issued_at IS NULL AND lease_expires_at IS NULL AND "
        "lease_redeemed_at IS NULL AND recovery_from_state IS NULL AND "
        "failed_at IS NULL AND completed_at IS NULL AND "
        f"{_NO_LINEAGE_CONSTRAINT} AND effect_count = 0) OR "
        f"(state = 'leased' AND {active}{_LEASE_CONSTRAINT} AND "
        "lease_redeemed_at IS NULL AND "
        "failed_at IS NULL AND completed_at IS NULL AND effect_count = 0 AND "
        f"((recovery_from_state IS NULL AND {_NO_LINEAGE_CONSTRAINT}) OR "
        "(recovery_from_state = 'running' AND fragment_ref IS NULL) OR "
        "(recovery_from_state IN ('prepared', 'ready') AND "
        f"{_DURABLE_IDENTITY_CONSTRAINT} AND fragment_ref IS NOT NULL))) OR "
        f"(state = 'running' AND {active}{_LEASE_CONSTRAINT} AND "
        "lease_redeemed_at >= lease_issued_at AND recovery_from_state IS NULL "
        "AND failed_at IS NULL AND completed_at IS NULL AND fragment_ref IS NULL "
        "AND ((resource_ref IS NULL AND revision_id IS NULL) OR "
        f"{_DURABLE_IDENTITY_CONSTRAINT}) AND effect_count = 0) OR "
        f"(state IN ('prepared', 'ready') AND {active}{_LEASE_CONSTRAINT} AND "
        "lease_redeemed_at >= lease_issued_at AND recovery_from_state IS NULL "
        "AND failed_at IS NULL AND completed_at IS NULL AND "
        f"{_DURABLE_IDENTITY_CONSTRAINT} AND fragment_ref IS NOT NULL AND "
        "effect_count = 0) OR "
        f"(state = 'failed' AND {active}{_LEASE_CONSTRAINT} AND "
        "lease_redeemed_at >= lease_issued_at AND recovery_from_state IS NULL "
        "AND failed_at >= lease_redeemed_at AND completed_at IS NULL AND "
        f"{_NO_LINEAGE_CONSTRAINT} AND effect_count = 0) OR "
        f"(state = 'completed' AND {active}{_LEASE_CONSTRAINT} AND "
        "lease_redeemed_at >= lease_issued_at AND recovery_from_state IS NULL "
        "AND failed_at IS NULL AND completed_at >= lease_redeemed_at AND "
        f"{_DURABLE_IDENTITY_CONSTRAINT} AND fragment_ref IS NOT NULL AND "
        "effect_count IN (0, 1))"
    )


def _previous_job_constraint() -> str:
    return _noncancelled_job_constraint()


def _job_constraint() -> str:
    active = (
        "cancelled_from_state IS NULL AND cancelled_at IS NULL AND "
        "cancellation_intent_id IS NULL"
    )
    cancelled = (
        "cancelled_at IS NOT NULL AND cancellation_intent_id IS NOT NULL AND "
        "failed_at IS NULL AND completed_at IS NULL AND effect_count = 0"
    )
    return (
        f"{_noncancelled_job_constraint(active)} OR "
        f"(state = 'cancelled' AND {cancelled} AND ("
        "(cancelled_from_state = 'available' AND lease_generation = 0 AND "
        "signing_key_version IS NULL AND lease_nonce_digest IS NULL AND "
        "lease_issued_at IS NULL AND lease_expires_at IS NULL AND "
        "lease_redeemed_at IS NULL AND recovery_from_state IS NULL AND "
        f"{_NO_LINEAGE_CONSTRAINT}) OR "
        f"(cancelled_from_state = 'leased' AND {_LEASE_CONSTRAINT} AND "
        "lease_redeemed_at IS NULL AND ((recovery_from_state IS NULL AND "
        f"{_NO_LINEAGE_CONSTRAINT}) OR (recovery_from_state = 'running' AND "
        "fragment_ref IS NULL) OR (recovery_from_state IN ('prepared', "
        f"'ready') AND {_DURABLE_IDENTITY_CONSTRAINT} AND "
        "fragment_ref IS NOT NULL))) OR "
        f"(cancelled_from_state = 'running' AND {_LEASE_CONSTRAINT} AND "
        "lease_redeemed_at >= lease_issued_at AND recovery_from_state IS NULL "
        "AND fragment_ref IS NULL AND ((resource_ref IS NULL AND "
        f"revision_id IS NULL) OR {_DURABLE_IDENTITY_CONSTRAINT})) OR "
        "(cancelled_from_state IN ('prepared', 'ready') AND "
        f"{_LEASE_CONSTRAINT} AND lease_redeemed_at >= lease_issued_at AND "
        f"recovery_from_state IS NULL AND {_DURABLE_IDENTITY_CONSTRAINT} AND "
        "fragment_ref IS NOT NULL)))"
    )


def _current_user_actor() -> str:
    return """
context_resource.organization_id = NULLIF(
    current_setting('app.organization_id', true), ''
)::uuid
AND context_resource.tombstoned IS FALSE
AND EXISTS (
    SELECT 1 FROM public.membership AS actor_membership
    WHERE actor_membership.organization_id = context_resource.organization_id
      AND actor_membership.user_id = NULLIF(
          current_setting('app.user_id', true), ''
      )::uuid
      AND current_setting('app.actor_kind', true) = 'user'
      AND NULLIF(current_setting('app.principal_ref', true), '') IS NOT NULL
      AND actor_membership.membership_id = NULLIF(
          current_setting('app.membership_id', true), ''
      )::uuid
      AND actor_membership.membership_version = NULLIF(
          current_setting('app.membership_version', true), ''
      )::bigint
      AND NULLIF(current_setting('app.request_id', true), '') IS NOT NULL
      AND NULLIF(
          current_setting('app.authentication_binding_ref', true), ''
      ) IS NOT NULL
      AND NULLIF(current_setting('app.checked_at', true), '') IS NOT NULL
      AND actor_membership.status = 'active'
      AND actor_membership.valid_from <= NULLIF(
          current_setting('app.checked_at', true), ''
      )::timestamptz
      AND (
          actor_membership.valid_until IS NULL
          OR actor_membership.valid_until > NULLIF(
              current_setting('app.checked_at', true), ''
          )::timestamptz
      )
)
AND public.context_runtime_file_source_lifecycle_allows(
    context_resource.organization_id,
    context_resource.source_ref
)
""".strip()


def _previous_current_user_actor() -> str:
    return _current_user_actor().rsplit(
        "\nAND public.context_runtime_file_source_lifecycle_allows(",
        maxsplit=1,
    )[0]


def upgrade() -> None:
    """Add one atomic source-offboarding and cancellation authority."""

    op.add_column(
        "context_source",
        sa.Column(
            "lifecycle_state",
            sa.Text(),
            nullable=False,
            server_default="active",
        ),
    )
    op.add_column(
        "context_source",
        sa.Column("disabled_version_id", postgresql.UUID(as_uuid=True)),
    )
    op.add_column(
        "context_source",
        sa.Column("disabled_at", sa.DateTime(timezone=True)),
    )
    op.create_check_constraint(
        "ck_context_source_lifecycle",
        "context_source",
        "(lifecycle_state = 'active' AND disabled_version_id IS NULL "
        "AND disabled_at IS NULL) OR (lifecycle_state = 'disabled' "
        "AND disabled_version_id = active_version_id "
        "AND disabled_at IS NOT NULL)",
    )
    op.create_foreign_key(
        "fk_context_source_disabled_version_exact",
        "context_source",
        "source_version",
        ["organization_id", "source_id", "disabled_version_id"],
        ["organization_id", "source_id", "version_id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_index(
        "ix_context_source_runtime_lifecycle",
        "context_source",
        ["organization_id", "source_id", "source_kind", "lifecycle_state"],
    )

    op.create_table(
        _TABLE,
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cleanup_intent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_epoch", sa.BigInteger(), nullable=False),
        sa.Column("cleanup_state", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("cancelled_job_count", sa.BigInteger(), nullable=False),
        sa.Column("retained_resource_count", sa.BigInteger(), nullable=False),
        sa.Column("security_completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "cleanup_intent_id",
            name="pk_file_source_cleanup_intent",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "source_id",
            name="uq_file_source_cleanup_intent_source",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.organization_id"],
            name="fk_file_source_cleanup_intent_organization",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_id", "source_version_id"],
            [
                "source_version.organization_id",
                "source_version.source_id",
                "source_version.version_id",
            ],
            name="fk_file_source_cleanup_intent_version_exact",
        ),
        sa.CheckConstraint(
            f"policy_epoch BETWEEN 1 AND {_MAX_BIGINT}",
            name="ck_file_source_cleanup_intent_epoch",
        ),
        sa.CheckConstraint(
            "cleanup_state = 'pending'",
            name="ck_file_source_cleanup_intent_state",
        ),
        sa.CheckConstraint(
            f"cancelled_job_count BETWEEN 0 AND {_MAX_BIGINT} AND "
            f"retained_resource_count BETWEEN 0 AND {_MAX_BIGINT}",
            name="ck_file_source_cleanup_intent_counts",
        ),
    )
    op.execute(f"REVOKE ALL ON TABLE {_TABLE} FROM PUBLIC")
    for role in (_CONTROL, _RUNTIME, _WORKER):
        op.execute(f"REVOKE ALL ON TABLE {_TABLE} FROM {role}")
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {_TABLE}_migrator_administration ON {_TABLE} "
        f"FOR ALL TO {_MIGRATOR} USING (true) WITH CHECK (true)"
    )
    tenant = (
        "organization_id = NULLIF("
        "current_setting('app.organization_id', true), ''"
        ")::uuid"
    )
    op.execute(
        f"CREATE POLICY {_TABLE}_access_policy_definer_select ON {_TABLE} "
        f"FOR SELECT TO {_ACCESS_DEFINER} USING ({tenant})"
    )
    op.execute(
        f"CREATE POLICY {_TABLE}_access_policy_definer_insert ON {_TABLE} "
        f"FOR INSERT TO {_ACCESS_DEFINER} WITH CHECK ({tenant})"
    )
    op.execute(
        f"CREATE TRIGGER {_TABLE}_immutable BEFORE UPDATE OR DELETE ON {_TABLE} "
        "FOR EACH ROW EXECUTE FUNCTION public.context_content_reject_mutation()"
    )
    op.execute(f"GRANT SELECT, INSERT ON TABLE {_TABLE} TO {_ACCESS_DEFINER}")

    op.add_column(
        "file_import_job",
        sa.Column("cancelled_from_state", sa.Text(), nullable=True),
    )
    op.add_column(
        "file_import_job",
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "file_import_job",
        sa.Column("cancellation_intent_id", postgresql.UUID(as_uuid=True)),
    )
    op.create_foreign_key(
        "fk_file_import_job_source_cancellation_intent",
        "file_import_job",
        _TABLE,
        ["organization_id", "cancellation_intent_id"],
        ["organization_id", "cleanup_intent_id"],
    )
    op.drop_constraint("ck_file_import_job_state", "file_import_job", type_="check")
    op.create_check_constraint(
        "ck_file_import_job_state",
        "file_import_job",
        "state IN ('available', 'leased', 'running', 'prepared', 'ready', "
        "'failed', 'completed', 'cancelled')",
    )
    op.drop_constraint(
        "ck_file_import_job_state_consistency", "file_import_job", type_="check"
    )
    op.create_check_constraint(
        "ck_file_import_job_state_consistency",
        "file_import_job",
        _job_constraint(),
    )

    # Worker functions already select/update the exact job before any effect.
    # Adding active-source truth to those FORCE-RLS policies closes every
    # issuer, redemption, recovery, staging, and activation function together.
    job_binding = (
        "organization_id = NULLIF(current_setting("
        "'app.organization_id', true), '')::uuid "
        "AND job_id = NULLIF(current_setting('app.worker_job_id', true), '')::uuid "
        "AND workload = 'supply.file-import' "
        "AND worker_audience = 'context-engine-worker' "
        "AND operation = 'file.import' AND EXISTS ("
        "SELECT 1 FROM public.context_source AS active_source "
        "WHERE active_source.organization_id = file_import_job.organization_id "
        "AND active_source.source_id = file_import_job.source_id "
        "AND active_source.lifecycle_state = 'active')"
    )
    job_select = job_binding.replace(
        "job_id = NULLIF(current_setting('app.worker_job_id', true), '')::uuid",
        "(job_id = NULLIF(current_setting('app.worker_job_id', true), '')::uuid "
        "OR acquisition_id = NULLIF(current_setting("
        "'app.file_acquisition_id', true), '')::uuid)",
    )
    op.execute("DROP POLICY file_import_job_definer_select ON file_import_job")
    op.execute("DROP POLICY file_import_job_definer_update ON file_import_job")
    op.execute(
        "CREATE POLICY file_import_job_definer_select ON file_import_job "
        f"FOR SELECT TO {_WORKER_DEFINER} USING ({job_select})"
    )
    op.execute(
        "CREATE POLICY file_import_job_definer_update ON file_import_job "
        f"FOR UPDATE TO {_WORKER_DEFINER} USING ({job_binding}) "
        f"WITH CHECK ({job_binding})"
    )
    op.execute(
        "CREATE POLICY file_import_job_access_policy_definer_select "
        "ON file_import_job FOR SELECT TO context_engine_access_policy_definer "
        f"USING ({tenant})"
    )
    op.execute(
        "CREATE POLICY file_import_job_access_policy_definer_update "
        "ON file_import_job FOR UPDATE TO context_engine_access_policy_definer "
        f"USING ({tenant}) WITH CHECK ({tenant})"
    )
    op.execute(f"GRANT SELECT ON TABLE file_import_job TO {_ACCESS_DEFINER}")
    op.execute(
        "GRANT UPDATE (state, cancelled_from_state, cancelled_at, "
        f"cancellation_intent_id) ON TABLE file_import_job TO {_ACCESS_DEFINER}"
    )

    op.execute(
        "CREATE POLICY context_source_access_policy_definer_select "
        "ON context_source FOR SELECT TO context_engine_access_policy_definer "
        f"USING ({tenant})"
    )
    op.execute(
        "CREATE POLICY context_source_access_policy_definer_update "
        "ON context_source FOR UPDATE TO context_engine_access_policy_definer "
        f"USING ({tenant}) WITH CHECK ({tenant})"
    )
    op.execute(f"GRANT SELECT ON TABLE context_source TO {_ACCESS_DEFINER}")
    op.execute(
        "GRANT UPDATE (lifecycle_state, disabled_version_id, disabled_at) "
        f"ON TABLE context_source TO {_ACCESS_DEFINER}"
    )
    active_source_tenant = f"{tenant} AND lifecycle_state = 'active'"
    for command in ("SELECT", "UPDATE"):
        op.execute(
            f"DROP POLICY context_source_file_import_definer_{command.lower()} "
            "ON context_source"
        )
        check = (
            f" WITH CHECK ({active_source_tenant})"
            if command == "UPDATE"
            else ""
        )
        op.execute(
            f"CREATE POLICY context_source_file_import_definer_{command.lower()} "
            f"ON context_source FOR {command} TO {_WORKER_DEFINER} "
            f"USING ({active_source_tenant}){check}"
        )

    # Runtime keeps discovery lineage stale by design. The definer recognizes a
    # canonical File SourceRef, then requires that exact Source to remain active.
    # Older non-File materialized carriers use namespaced, non-UUID references
    # and retain their existing authorization path.
    op.execute(
        f"""
        CREATE FUNCTION public.{_RUNTIME_SOURCE_FUNCTION}(
            requested_organization_id uuid,
            requested_source_ref text
        ) RETURNS boolean
        LANGUAGE plpgsql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE
            requested_source_id uuid;
        BEGIN
            IF SESSION_USER <> '{_RUNTIME}'
               OR requested_organization_id IS NULL
               OR requested_organization_id IS DISTINCT FROM NULLIF(
                    current_setting('app.organization_id', true), ''
               )::uuid
               OR requested_source_ref IS NULL
            THEN RETURN false; END IF;

            IF requested_source_ref !~
                '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-'
                '[0-9a-f]{{4}}-[0-9a-f]{{12}}$'
            THEN
                RETURN true;
            END IF;
            requested_source_id := requested_source_ref::uuid;

            RETURN EXISTS (
                SELECT 1 FROM public.context_source AS source
                WHERE source.organization_id = requested_organization_id
                  AND source.source_id = requested_source_id
                  AND source.source_kind = 'file'
                  AND source.lifecycle_state = 'active'
            );
        END;
        $function$
        """
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION public.{_RUNTIME_SOURCE_FUNCTION}"
        f"{_RUNTIME_SOURCE_SIGNATURE} FROM PUBLIC"
    )
    for role in (_CONTROL, _WORKER):
        op.execute(
            f"REVOKE ALL ON FUNCTION public.{_RUNTIME_SOURCE_FUNCTION}"
            f"{_RUNTIME_SOURCE_SIGNATURE} FROM {role}"
        )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_RUNTIME_SOURCE_FUNCTION}"
        f"{_RUNTIME_SOURCE_SIGNATURE} TO {_RUNTIME}"
    )
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_ACCESS_DEFINER}")
    op.execute(
        f"ALTER FUNCTION public.{_RUNTIME_SOURCE_FUNCTION}"
        f"{_RUNTIME_SOURCE_SIGNATURE} OWNER TO {_ACCESS_DEFINER}"
    )
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_ACCESS_DEFINER}")
    op.execute("DROP POLICY context_resource_current_user_actor ON context_resource")
    op.execute(
        "CREATE POLICY context_resource_current_user_actor ON context_resource "
        "AS PERMISSIVE FOR SELECT TO context_engine_runtime USING ("
        f"{_current_user_actor()})"
    )

    # Retain the earlier implementation only as a private implementation detail.
    # The public wrapper serializes prepare with source offboarding and rechecks
    # active lifecycle under a locked source row.
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_WORKER_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_WORKER_DEFINER}")
    op.execute(
        "ALTER FUNCTION public.context_control_prepare_file_import"
        f"{_PREPARE_SIGNATURE} RENAME TO "
        "context_control_prepare_file_import_pre_offboarding"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION "
        "public.context_control_prepare_file_import_pre_offboarding"
        f"{_PREPARE_SIGNATURE} FROM {_CONTROL}"
    )
    op.execute("RESET ROLE")
    op.execute(
        f"""
        CREATE FUNCTION public.context_control_prepare_file_import(
            requested_organization_id uuid, requested_acquisition_id uuid,
            requested_job_id uuid, requested_activated_version_id uuid,
            requested_source_id uuid, requested_relative_path text,
            requested_audience_principal_ref text,
            requested_audience_membership_id uuid,
            requested_audience_membership_version bigint,
            requested_idempotency_key text, requested_request_digest text,
            requested_service_principal_id uuid
        ) RETURNS TABLE (job_id uuid, service_principal_id uuid)
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        BEGIN
            IF SESSION_USER <> '{_CONTROL}'
               OR requested_organization_id IS NULL
               OR requested_source_id IS NULL
            THEN RETURN; END IF;
            PERFORM pg_catalog.set_config(
                'app.organization_id', requested_organization_id::text, true
            );
            PERFORM pg_catalog.pg_advisory_xact_lock(
                pg_catalog.hashtextextended(
                    'context-engine.file-publication:'
                    || requested_organization_id::text,
                    0
                )
            );
            IF NOT EXISTS (
                SELECT 1 FROM public.context_source AS source
                WHERE source.organization_id = requested_organization_id
                  AND source.source_id = requested_source_id
                  AND source.lifecycle_state = 'active'
                FOR UPDATE
            ) THEN RETURN; END IF;
            RETURN QUERY
            SELECT prepared.job_id, prepared.service_principal_id
            FROM public.context_control_prepare_file_import_pre_offboarding(
                requested_organization_id, requested_acquisition_id,
                requested_job_id, requested_activated_version_id,
                requested_source_id, requested_relative_path,
                requested_audience_principal_ref,
                requested_audience_membership_id,
                requested_audience_membership_version,
                requested_idempotency_key, requested_request_digest,
                requested_service_principal_id
            ) AS prepared;
        END;
        $function$
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.context_control_prepare_file_import"
        f"{_PREPARE_SIGNATURE} FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.context_control_prepare_file_import"
        f"{_PREPARE_SIGNATURE} TO {_CONTROL}"
    )
    op.execute(
        "ALTER FUNCTION public.context_control_prepare_file_import"
        f"{_PREPARE_SIGNATURE} OWNER TO {_WORKER_DEFINER}"
    )
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_WORKER_DEFINER}")

    op.execute(
        f"""
        CREATE FUNCTION public.{_FUNCTION}(
            requested_organization_id uuid,
            requested_source_id uuid,
            requested_cleanup_intent_id uuid
        ) RETURNS TABLE (
            source_id uuid,
            source_version_id uuid,
            policy_epoch bigint,
            cleanup_intent_id uuid,
            cancelled_job_count bigint,
            retained_resource_count bigint,
            security_completed_at timestamptz,
            cleanup_state text
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE
            selected_version_id uuid;
            current_epoch bigint;
            next_epoch bigint;
            cancelled_count bigint;
            retained_count bigint;
            trusted_now timestamptz;
        BEGIN
            IF SESSION_USER <> '{_CONTROL}'
               OR NULLIF(current_setting('app.organization_id', true), '')::uuid
                    IS DISTINCT FROM requested_organization_id
               OR requested_source_id IS NULL
               OR requested_cleanup_intent_id IS NULL
            THEN RETURN; END IF;

            PERFORM pg_catalog.pg_advisory_xact_lock(
                pg_catalog.hashtextextended(
                    'context-engine.file-publication:'
                    || requested_organization_id::text,
                    0
                )
            );

            RETURN QUERY
            SELECT intent.source_id, intent.source_version_id,
                   intent.policy_epoch, intent.cleanup_intent_id,
                   intent.cancelled_job_count, intent.retained_resource_count,
                   intent.security_completed_at, intent.cleanup_state
            FROM public.{_TABLE} AS intent
            JOIN public.context_source AS source
              ON source.organization_id = intent.organization_id
             AND source.source_id = intent.source_id
             AND source.lifecycle_state = 'disabled'
             AND source.disabled_version_id = intent.source_version_id
            WHERE intent.organization_id = requested_organization_id
              AND intent.source_id = requested_source_id;
            IF FOUND THEN RETURN; END IF;

            SELECT source.active_version_id
            INTO selected_version_id
            FROM public.context_source AS source
            WHERE source.organization_id = requested_organization_id
              AND source.source_id = requested_source_id
              AND source.source_kind = 'file'
              AND source.lifecycle_state = 'active'
            FOR UPDATE;
            IF selected_version_id IS NULL THEN RETURN; END IF;

            SELECT epoch.policy_epoch
            INTO current_epoch
            FROM public.organization_policy_epoch AS epoch
            WHERE epoch.organization_id = requested_organization_id
            FOR UPDATE;
            IF current_epoch IS NULL OR current_epoch >= {_MAX_BIGINT} THEN
                RETURN;
            END IF;

            -- Fence every cancellable job before capturing retained lineage.
            -- An in-flight worker that already holds a job lock finishes first;
            -- its committed artifacts are therefore visible to the count below.
            PERFORM 1
            FROM public.file_import_job AS job
            WHERE job.organization_id = requested_organization_id
              AND job.source_id = requested_source_id
              AND job.state IN ('available', 'leased', 'running', 'prepared', 'ready')
            FOR UPDATE;
            SELECT count(*) INTO cancelled_count
            FROM public.file_import_job AS job
            WHERE job.organization_id = requested_organization_id
              AND job.source_id = requested_source_id
              AND job.state IN ('available', 'leased', 'running', 'prepared', 'ready');
            SELECT count(*) INTO retained_count
            FROM public.context_resource AS resource
            WHERE resource.organization_id = requested_organization_id
              AND resource.source_ref = requested_source_id::text;

            trusted_now := pg_catalog.statement_timestamp();
            UPDATE public.context_source AS source
            SET lifecycle_state = 'disabled',
                disabled_version_id = selected_version_id,
                disabled_at = trusted_now
            WHERE source.organization_id = requested_organization_id
              AND source.source_id = requested_source_id
              AND source.active_version_id = selected_version_id
              AND source.lifecycle_state = 'active';
            IF NOT FOUND THEN
                RAISE EXCEPTION USING ERRCODE = '40001',
                    MESSAGE = 'File source offboarding was not accepted';
            END IF;

            UPDATE public.organization_policy_epoch AS epoch
            SET policy_epoch = epoch.policy_epoch + 1
            WHERE epoch.organization_id = requested_organization_id
              AND epoch.policy_epoch = current_epoch
            RETURNING epoch.policy_epoch INTO next_epoch;
            IF next_epoch IS NULL THEN
                RAISE EXCEPTION USING ERRCODE = '40001',
                    MESSAGE = 'File source offboarding was not accepted';
            END IF;

            INSERT INTO public.{_TABLE} (
                organization_id, cleanup_intent_id, source_id,
                source_version_id, policy_epoch, cleanup_state,
                cancelled_job_count, retained_resource_count,
                security_completed_at
            ) VALUES (
                requested_organization_id, requested_cleanup_intent_id,
                requested_source_id, selected_version_id, next_epoch,
                'pending', cancelled_count, retained_count, trusted_now
            );

            UPDATE public.file_import_job AS job
            SET cancelled_from_state = job.state,
                state = 'cancelled',
                cancelled_at = trusted_now,
                cancellation_intent_id = requested_cleanup_intent_id
            WHERE job.organization_id = requested_organization_id
              AND job.source_id = requested_source_id
              AND job.state IN ('available', 'leased', 'running', 'prepared', 'ready');
            IF cancelled_count <> (SELECT count(*) FROM public.file_import_job AS job
                WHERE job.organization_id = requested_organization_id
                  AND job.source_id = requested_source_id
                  AND job.cancellation_intent_id = requested_cleanup_intent_id)
            THEN
                RAISE EXCEPTION USING ERRCODE = '40001',
                    MESSAGE = 'File source job cancellation was not accepted';
            END IF;

            RETURN QUERY
            SELECT intent.source_id, intent.source_version_id,
                   intent.policy_epoch, intent.cleanup_intent_id,
                   intent.cancelled_job_count, intent.retained_resource_count,
                   intent.security_completed_at, intent.cleanup_state
            FROM public.{_TABLE} AS intent
            WHERE intent.organization_id = requested_organization_id
              AND intent.cleanup_intent_id = requested_cleanup_intent_id;
        END;
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_FUNCTION}{_SIGNATURE} FROM PUBLIC")
    for role in (_RUNTIME, _WORKER):
        op.execute(
            f"REVOKE ALL ON FUNCTION public.{_FUNCTION}{_SIGNATURE} FROM {role}"
        )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_FUNCTION}{_SIGNATURE} TO {_CONTROL}"
    )
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_ACCESS_DEFINER}")
    op.execute(
        f"ALTER FUNCTION public.{_FUNCTION}{_SIGNATURE} OWNER TO {_ACCESS_DEFINER}"
    )
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_ACCESS_DEFINER}")


def downgrade() -> None:
    """Remove only unused offboarding machinery; never re-enable a source."""

    op.execute(
        f"""
        DO $block$
        BEGIN
            IF EXISTS (SELECT 1 FROM public.{_TABLE}) THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'cannot downgrade with File source cleanup intent';
            END IF;
        END;
        $block$
        """
    )
    op.execute(f"SET LOCAL ROLE {_ACCESS_DEFINER}")
    op.execute(f"DROP FUNCTION public.{_FUNCTION}{_SIGNATURE}")
    op.execute("RESET ROLE")
    tenant = (
        "organization_id = NULLIF("
        "current_setting('app.organization_id', true), ''"
        ")::uuid"
    )
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_WORKER_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_WORKER_DEFINER}")
    op.execute(
        "DROP FUNCTION public.context_control_prepare_file_import"
        f"{_PREPARE_SIGNATURE}"
    )
    op.execute(
        "ALTER FUNCTION "
        "public.context_control_prepare_file_import_pre_offboarding"
        f"{_PREPARE_SIGNATURE} RENAME TO context_control_prepare_file_import"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.context_control_prepare_file_import"
        f"{_PREPARE_SIGNATURE} TO {_CONTROL}"
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_WORKER_DEFINER}")
    op.execute("DROP POLICY context_resource_current_user_actor ON context_resource")
    op.execute(
        "CREATE POLICY context_resource_current_user_actor ON context_resource "
        "AS PERMISSIVE FOR SELECT TO context_engine_runtime USING ("
        f"{_previous_current_user_actor()})"
    )
    op.execute(f"SET LOCAL ROLE {_ACCESS_DEFINER}")
    op.execute(
        f"DROP FUNCTION public.{_RUNTIME_SOURCE_FUNCTION}"
        f"{_RUNTIME_SOURCE_SIGNATURE}"
    )
    op.execute("RESET ROLE")
    op.execute(
        "DROP POLICY context_source_access_policy_definer_update ON context_source"
    )
    op.execute(
        "DROP POLICY context_source_access_policy_definer_select ON context_source"
    )
    op.execute(
        "REVOKE UPDATE (lifecycle_state, disabled_version_id, disabled_at) "
        f"ON TABLE context_source FROM {_ACCESS_DEFINER}"
    )
    op.execute(f"REVOKE SELECT ON TABLE context_source FROM {_ACCESS_DEFINER}")
    for command in ("SELECT", "UPDATE"):
        op.execute(
            f"DROP POLICY context_source_file_import_definer_{command.lower()} "
            "ON context_source"
        )
        check = f" WITH CHECK ({tenant})" if command == "UPDATE" else ""
        op.execute(
            f"CREATE POLICY context_source_file_import_definer_{command.lower()} "
            f"ON context_source FOR {command} TO {_WORKER_DEFINER} "
            f"USING ({tenant}){check}"
        )
    op.execute(
        "DROP POLICY file_import_job_access_policy_definer_update ON file_import_job"
    )
    op.execute(
        "DROP POLICY file_import_job_access_policy_definer_select ON file_import_job"
    )
    op.execute(
        "REVOKE UPDATE (state, cancelled_from_state, cancelled_at, "
        f"cancellation_intent_id) ON TABLE file_import_job FROM {_ACCESS_DEFINER}"
    )
    op.execute(f"REVOKE SELECT ON TABLE file_import_job FROM {_ACCESS_DEFINER}")
    old_job_binding = (
        "organization_id = NULLIF(current_setting("
        "'app.organization_id', true), '')::uuid "
        "AND job_id = NULLIF(current_setting('app.worker_job_id', true), '')::uuid "
        "AND workload = 'supply.file-import' "
        "AND worker_audience = 'context-engine-worker' "
        "AND operation = 'file.import'"
    )
    old_job_select = old_job_binding.replace(
        "job_id = NULLIF(current_setting('app.worker_job_id', true), '')::uuid",
        "(job_id = NULLIF(current_setting('app.worker_job_id', true), '')::uuid "
        "OR acquisition_id = NULLIF(current_setting("
        "'app.file_acquisition_id', true), '')::uuid)",
    )
    op.execute("DROP POLICY file_import_job_definer_select ON file_import_job")
    op.execute("DROP POLICY file_import_job_definer_update ON file_import_job")
    op.execute(
        "CREATE POLICY file_import_job_definer_select ON file_import_job "
        f"FOR SELECT TO {_WORKER_DEFINER} USING ({old_job_select})"
    )
    op.execute(
        "CREATE POLICY file_import_job_definer_update ON file_import_job "
        f"FOR UPDATE TO {_WORKER_DEFINER} USING ({old_job_binding}) "
        f"WITH CHECK ({old_job_binding})"
    )
    op.drop_constraint(
        "fk_file_import_job_source_cancellation_intent",
        "file_import_job",
        type_="foreignkey",
    )
    op.drop_constraint(
        "ck_file_import_job_state_consistency", "file_import_job", type_="check"
    )
    op.drop_constraint("ck_file_import_job_state", "file_import_job", type_="check")
    op.drop_column("file_import_job", "cancellation_intent_id")
    op.drop_column("file_import_job", "cancelled_at")
    op.drop_column("file_import_job", "cancelled_from_state")
    op.create_check_constraint(
        "ck_file_import_job_state",
        "file_import_job",
        "state IN ('available', 'leased', 'running', 'prepared', 'ready', "
        "'failed', 'completed')",
    )
    op.create_check_constraint(
        "ck_file_import_job_state_consistency",
        "file_import_job",
        _previous_job_constraint(),
    )
    op.drop_table(_TABLE)
    op.drop_constraint(
        "fk_context_source_disabled_version_exact",
        "context_source",
        type_="foreignkey",
    )
    op.drop_index("ix_context_source_runtime_lifecycle", table_name="context_source")
    op.drop_constraint("ck_context_source_lifecycle", "context_source", type_="check")
    op.drop_column("context_source", "disabled_at")
    op.drop_column("context_source", "disabled_version_id")
    op.drop_column("context_source", "lifecycle_state")
