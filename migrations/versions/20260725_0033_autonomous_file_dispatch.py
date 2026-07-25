"""Claim current scheduled File imports through exact first-attempt leases.

Revision ID: 20260725_0033
Revises: 20260725_0032
Create Date: 2026-07-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260725_0033"
down_revision: str | None = "20260725_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEDULER = "context_engine_scheduler"
_DEFINER = "context_engine_file_dispatch_definer"
_FUNCTION = "context_scheduler_claim_file_import"
_SIGNATURE = "(bigint, bytea, text[])"
_TTL_SECONDS = 300
_MIGRATION_FENCE = "context-engine.file-dispatch-migration-fence"
_SCHEDULING_MIGRATION_FENCE = "context-engine.file-change-scheduling-migration-fence"
_MAX_BIGINT = 9_223_372_036_854_775_807
_V3 = "file-capabilities-v3"
_V4 = "file-capabilities-v4"


def upgrade() -> None:
    """Install one cross-tenant selector available only through the scheduler call."""

    op.add_column(
        "file_import_job",
        sa.Column(
            "dispatch_claimed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    for table in (
        "context_source",
        "source_version",
        "membership",
        "service_principal",
        "file_acquisition",
        "file_import_job",
        "file_source_change_page",
        "file_source_change",
        "file_source_acquisition_checkpoint",
    ):
        op.execute(
            f"CREATE POLICY {table}_file_dispatch_definer_select ON {table} "
            f"FOR SELECT TO {_DEFINER} USING (true)"
        )
        op.execute(f"GRANT SELECT ON TABLE {table} TO {_DEFINER}")
    op.execute(
        "CREATE POLICY file_import_job_file_dispatch_definer_update "
        f"ON file_import_job FOR UPDATE TO {_DEFINER} USING (true) "
        "WITH CHECK (true)"
    )
    for table in ("context_source", "membership", "service_principal"):
        op.execute(
            f"CREATE POLICY {table}_file_dispatch_definer_update ON {table} "
            f"FOR UPDATE TO {_DEFINER} USING (true) WITH CHECK (true)"
        )
    op.execute(
        f"GRANT UPDATE (lifecycle_state, active_version_id) "
        f"ON context_source TO {_DEFINER}"
    )
    op.execute(
        f"GRANT UPDATE (status, valid_from, valid_until) ON membership TO {_DEFINER}"
    )
    op.execute(f"GRANT UPDATE (enabled) ON service_principal TO {_DEFINER}")
    op.execute(
        "GRANT UPDATE (state, signing_key_version, lease_nonce_digest, "
        "lease_issued_at, lease_expires_at, lease_generation, dispatch_claimed) "
        f"ON file_import_job TO {_DEFINER}"
    )
    op.execute(
        f"""
        CREATE FUNCTION public.{_FUNCTION}(
            requested_signing_key_version bigint,
            requested_nonce bytea,
            configured_root_refs text[]
        ) RETURNS TABLE (
            organization_id uuid,
            job_id uuid,
            source_id uuid,
            service_principal_id uuid,
            lease_generation bigint,
            issued_at timestamptz,
            expires_at timestamptz
        )
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE
            authority_checked_at timestamptz;
            minted_at timestamptz;
            selected_organization_id uuid;
            selected_job_id uuid;
            selected_source_id uuid;
        BEGIN
            IF requested_signing_key_version IS NULL
               OR requested_nonce IS NULL
               OR configured_root_refs IS NULL
               OR SESSION_USER <> '{_SCHEDULER}'
               OR requested_signing_key_version NOT BETWEEN 1 AND {_MAX_BIGINT}
               OR pg_catalog.octet_length(requested_nonce) <> 32
               OR pg_catalog.cardinality(configured_root_refs) < 1
               OR EXISTS (
                    SELECT 1 FROM pg_catalog.unnest(configured_root_refs) AS root_ref
                    WHERE root_ref !~ '^[A-Za-z0-9][A-Za-z0-9._-]{{0,127}}$'
                       OR root_ref IN ('.', '..')
               )
               OR pg_catalog.cardinality(configured_root_refs) <> (
                    SELECT count(DISTINCT root_ref)
                    FROM pg_catalog.unnest(configured_root_refs) AS root_ref
               )
            THEN RETURN; END IF;
            PERFORM pg_catalog.pg_advisory_xact_lock_shared(
                pg_catalog.hashtextextended('{_MIGRATION_FENCE}', 0)
            );
            authority_checked_at := pg_catalog.clock_timestamp();
            SELECT job.organization_id, job.job_id, job.source_id
            INTO selected_organization_id, selected_job_id, selected_source_id
            FROM public.file_import_job AS job
            JOIN public.file_acquisition AS acquisition
              ON acquisition.organization_id = job.organization_id
             AND acquisition.acquisition_id = job.acquisition_id
             AND acquisition.source_id = job.source_id
            JOIN public.file_source_change_page AS accepted_page
              ON accepted_page.organization_id = acquisition.organization_id
             AND accepted_page.source_id = acquisition.source_id
             AND accepted_page.source_version_id = acquisition.source_version_id
             AND accepted_page.page_ref = acquisition.change_page_ref
            JOIN public.file_source_change AS accepted_change
              ON accepted_change.organization_id = acquisition.organization_id
             AND accepted_change.source_id = acquisition.source_id
             AND accepted_change.source_version_id = acquisition.source_version_id
             AND accepted_change.page_ref = acquisition.change_page_ref
             AND accepted_change.change_ordinal = acquisition.change_ordinal
             AND accepted_change.relative_path = acquisition.relative_path
             AND accepted_change.content_sha256 = acquisition.expected_content_sha256
             AND accepted_change.content_length = acquisition.expected_content_length
            JOIN public.file_source_acquisition_checkpoint AS scheduled
              ON scheduled.organization_id = job.organization_id
             AND scheduled.source_id = job.source_id
             AND scheduled.acquisition_id = job.acquisition_id
             AND scheduled.job_id = job.job_id
             AND scheduled.change_kind = 'file_import'
            JOIN public.context_source AS source
              ON source.organization_id = acquisition.organization_id
             AND source.source_id = acquisition.source_id
             AND source.active_version_id = acquisition.source_version_id
            JOIN public.source_version AS version
              ON version.organization_id = source.organization_id
             AND version.source_id = source.source_id
             AND version.version_id = source.active_version_id
            JOIN public.membership AS audience
              ON audience.organization_id = acquisition.organization_id
             AND audience.membership_id = acquisition.audience_membership_id
             AND audience.membership_version = acquisition.audience_membership_version
            JOIN public.service_principal AS receiver
              ON receiver.organization_id = job.organization_id
             AND receiver.service_principal_id = job.service_principal_id
             AND receiver.workload = job.workload
             AND receiver.worker_audience = job.worker_audience
             AND receiver.operation = job.operation
            WHERE job.state = 'available'
              AND job.lease_generation = 0
              AND job.workload = 'supply.file-import'
              AND job.worker_audience = 'context-engine-worker'
              AND job.actor_kind = 'service'
              AND job.operation = 'file.import'
              AND acquisition.change_page_ref IS NOT NULL
              AND accepted_change.change_kind = 'upsert'
              AND source.source_kind = 'file'
              AND source.lifecycle_state = 'active'
              AND version.capability_manifest->>'declarationVersion'
                  IN ('{_V3}', '{_V4}')
              AND audience.status = 'active'
              AND audience.valid_from <= authority_checked_at
              AND (audience.valid_until IS NULL
                   OR audience.valid_until > authority_checked_at)
              AND receiver.enabled IS TRUE
              AND accepted_page.scan_epoch = (
                  SELECT current_page.scan_epoch
                  FROM public.file_source_acquisition_checkpoint AS current_checkpoint
                  JOIN public.file_source_change_page AS current_page
                    ON current_page.organization_id = current_checkpoint.organization_id
                   AND current_page.source_id = current_checkpoint.source_id
                   AND current_page.source_version_id =
                       current_checkpoint.source_version_id
                   AND current_page.page_ref = current_checkpoint.change_page_ref
                  WHERE current_checkpoint.organization_id = acquisition.organization_id
                    AND current_checkpoint.source_id = acquisition.source_id
                    AND current_checkpoint.change_kind = 'file_change_page'
                  ORDER BY current_checkpoint.sequence DESC
                  LIMIT 1
              )
            ORDER BY scheduled.accepted_at, scheduled.sequence,
                     accepted_page.page_ordinal, accepted_change.change_ordinal,
                     job.organization_id, job.source_id, job.job_id
            FOR UPDATE OF job SKIP LOCKED
            LIMIT 1;
            IF NOT FOUND THEN RETURN; END IF;
            -- The configured capability set is an all-or-nothing assertion,
            -- never a routing filter.  Selection above always chooses the
            -- globally oldest eligible row first; an omitted root returns
            -- content-free no-work and cannot redirect the claim elsewhere.
            IF NOT EXISTS (
                SELECT 1
                FROM public.source_version AS selected_version
                WHERE selected_version.organization_id =
                      selected_organization_id
                  AND selected_version.source_id = selected_source_id
                  AND selected_version.root_ref = ANY(configured_root_refs)
            ) THEN RETURN; END IF;

            -- Page acceptance owns this same exclusive Organization/Source
            -- progress lock.  Take it before mutable authority row locks, then
            -- use a fresh READ COMMITTED statement snapshot below so a scan
            -- accepted while this selector waited makes the old job ineligible.
            PERFORM pg_catalog.pg_advisory_xact_lock(
                pg_catalog.hashtextextended(
                    'context-engine.file-source-progress:'
                    || selected_organization_id::text || ':'
                    || selected_source_id::text, 0
                )
            );
            authority_checked_at := pg_catalog.clock_timestamp();
            minted_at := pg_catalog.date_trunc(
                'second', pg_catalog.clock_timestamp()
            );

            RETURN QUERY
            WITH candidate AS (
                SELECT job.organization_id, job.job_id
                FROM public.file_import_job AS job
                JOIN public.file_acquisition AS acquisition
                  ON acquisition.organization_id = job.organization_id
                 AND acquisition.acquisition_id = job.acquisition_id
                 AND acquisition.source_id = job.source_id
                JOIN public.file_source_change_page AS accepted_page
                  ON accepted_page.organization_id = acquisition.organization_id
                 AND accepted_page.source_id = acquisition.source_id
                 AND accepted_page.source_version_id =
                     acquisition.source_version_id
                 AND accepted_page.page_ref = acquisition.change_page_ref
                JOIN public.file_source_change AS accepted_change
                  ON accepted_change.organization_id = acquisition.organization_id
                 AND accepted_change.source_id = acquisition.source_id
                 AND accepted_change.source_version_id =
                     acquisition.source_version_id
                 AND accepted_change.page_ref = acquisition.change_page_ref
                 AND accepted_change.change_ordinal = acquisition.change_ordinal
                 AND accepted_change.relative_path = acquisition.relative_path
                 AND accepted_change.content_sha256 =
                     acquisition.expected_content_sha256
                 AND accepted_change.content_length =
                     acquisition.expected_content_length
                JOIN public.file_source_acquisition_checkpoint AS scheduled
                  ON scheduled.organization_id = job.organization_id
                 AND scheduled.source_id = job.source_id
                 AND scheduled.acquisition_id = job.acquisition_id
                 AND scheduled.job_id = job.job_id
                 AND scheduled.change_kind = 'file_import'
                JOIN public.context_source AS source
                  ON source.organization_id = acquisition.organization_id
                 AND source.source_id = acquisition.source_id
                 AND source.active_version_id = acquisition.source_version_id
                JOIN public.source_version AS version
                  ON version.organization_id = source.organization_id
                 AND version.source_id = source.source_id
                 AND version.version_id = source.active_version_id
                JOIN public.membership AS audience
                  ON audience.organization_id = acquisition.organization_id
                 AND audience.membership_id =
                     acquisition.audience_membership_id
                 AND audience.membership_version =
                     acquisition.audience_membership_version
                JOIN public.service_principal AS receiver
                  ON receiver.organization_id = job.organization_id
                 AND receiver.service_principal_id = job.service_principal_id
                 AND receiver.workload = job.workload
                 AND receiver.worker_audience = job.worker_audience
                 AND receiver.operation = job.operation
                WHERE job.state = 'available'
                  AND job.lease_generation = 0
                  AND job.organization_id = selected_organization_id
                  AND job.job_id = selected_job_id
                  AND job.source_id = selected_source_id
                  AND job.workload = 'supply.file-import'
                  AND job.worker_audience = 'context-engine-worker'
                  AND job.actor_kind = 'service'
                  AND job.operation = 'file.import'
                  AND acquisition.change_page_ref IS NOT NULL
                  AND accepted_change.change_kind = 'upsert'
                  AND source.source_kind = 'file'
                  AND source.lifecycle_state = 'active'
                  AND version.capability_manifest->>'declarationVersion'
                      IN ('{_V3}', '{_V4}')
                  AND version.root_ref = ANY(configured_root_refs)
                  AND audience.status = 'active'
                  AND audience.valid_from <= authority_checked_at
                  AND (audience.valid_until IS NULL
                       OR audience.valid_until > authority_checked_at)
                  AND receiver.enabled IS TRUE
                  AND accepted_page.scan_epoch = (
                      SELECT current_page.scan_epoch
                      FROM public.file_source_acquisition_checkpoint
                           AS current_checkpoint
                      JOIN public.file_source_change_page AS current_page
                        ON current_page.organization_id =
                           current_checkpoint.organization_id
                       AND current_page.source_id = current_checkpoint.source_id
                       AND current_page.source_version_id =
                           current_checkpoint.source_version_id
                       AND current_page.page_ref =
                           current_checkpoint.change_page_ref
                      WHERE current_checkpoint.organization_id =
                            acquisition.organization_id
                        AND current_checkpoint.source_id = acquisition.source_id
                        AND current_checkpoint.change_kind = 'file_change_page'
                      ORDER BY current_checkpoint.sequence DESC
                      LIMIT 1
                  )
                FOR UPDATE OF source, audience, receiver SKIP LOCKED
            ), claimed AS (
                UPDATE public.file_import_job AS job
                SET state = 'leased',
                    signing_key_version = requested_signing_key_version,
                    lease_nonce_digest = public.digest(requested_nonce, 'sha256'),
                    lease_issued_at = minted_at,
                    lease_expires_at = minted_at
                        + pg_catalog.make_interval(secs => {_TTL_SECONDS}),
                    lease_generation = 1,
                    dispatch_claimed = true
                FROM candidate
                WHERE job.organization_id = candidate.organization_id
                  AND job.job_id = candidate.job_id
                  AND job.state = 'available'
                  AND job.lease_generation = 0
                RETURNING job.organization_id, job.job_id, job.source_id,
                          job.service_principal_id, job.lease_generation,
                          job.lease_issued_at, job.lease_expires_at
            )
            SELECT claimed.organization_id, claimed.job_id, claimed.source_id,
                   claimed.service_principal_id, claimed.lease_generation,
                   claimed.lease_issued_at, claimed.lease_expires_at
            FROM claimed;
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
        f"GRANT EXECUTE ON FUNCTION public.{_FUNCTION}{_SIGNATURE} TO {_SCHEDULER}"
    )
    op.execute("RESET ROLE")


def downgrade() -> None:
    """Remove dispatch only when it has never issued a generation-one lease."""

    connection = op.get_bind()
    # Acquire the upstream scheduling fence before DROP POLICY takes relation
    # locks.  This preserves the established migration lock order for an
    # in-flight page scheduler and every older downgrade in the same Alembic
    # transaction.
    connection.execute(
        sa.text(
            "SELECT pg_catalog.pg_advisory_xact_lock("
            "pg_catalog.hashtextextended(:migration_fence, 0))"
        ),
        {"migration_fence": _SCHEDULING_MIGRATION_FENCE},
    )
    connection.execute(
        sa.text(
            "SELECT pg_catalog.pg_advisory_xact_lock("
            "pg_catalog.hashtextextended(:migration_fence, 0))"
        ),
        {"migration_fence": _MIGRATION_FENCE},
    )
    claimed = connection.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM public.file_import_job "
            "WHERE dispatch_claimed IS TRUE)"
        )
    ).scalar_one()
    if claimed:
        raise RuntimeError(
            "autonomous File dispatch downgrade requires no retained "
            "generation-one lease; use a forward fix"
        )
    # Preserve the established scheduling migration relation-lock order.  An
    # in-flight manual acquisition must be observed before authority-policy
    # relations such as Membership are touched by this downgrade.
    op.execute("LOCK TABLE file_acquisition IN ACCESS EXCLUSIVE MODE")
    op.execute(f"DROP FUNCTION public.{_FUNCTION}{_SIGNATURE}")
    op.execute(
        "REVOKE UPDATE (state, signing_key_version, lease_nonce_digest, "
        "lease_issued_at, lease_expires_at, lease_generation, dispatch_claimed) "
        f"ON file_import_job FROM {_DEFINER}"
    )
    op.execute(
        f"REVOKE UPDATE (lifecycle_state, active_version_id) "
        f"ON context_source FROM {_DEFINER}"
    )
    op.execute(
        f"REVOKE UPDATE (status, valid_from, valid_until) ON membership FROM {_DEFINER}"
    )
    op.execute(f"REVOKE UPDATE (enabled) ON service_principal FROM {_DEFINER}")
    op.execute(
        "DROP POLICY file_import_job_file_dispatch_definer_update ON file_import_job"
    )
    for table in ("context_source", "membership", "service_principal"):
        op.execute(f"DROP POLICY {table}_file_dispatch_definer_update ON {table}")
    # Request this relation lock first, before touching other policy tables, so
    # the established scheduling downgrade remains first in the lock order.
    for table in (
        "file_acquisition",
        "context_source",
        "source_version",
        "membership",
        "service_principal",
        "file_import_job",
        "file_source_change_page",
        "file_source_change",
        "file_source_acquisition_checkpoint",
    ):
        op.execute(f"DROP POLICY {table}_file_dispatch_definer_select ON {table}")
        op.execute(f"REVOKE SELECT ON TABLE {table} FROM {_DEFINER}")
    op.drop_column("file_import_job", "dispatch_claimed")
