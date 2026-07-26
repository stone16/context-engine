"""Reclaim expired scheduled File imports through bounded automatic leases.

Revision ID: 20260726_0034
Revises: 20260725_0033
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260726_0034"
down_revision: str | None = "20260725_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEDULER = "context_engine_scheduler"
_DEFINER = "context_engine_file_dispatch_definer"
_FUNCTION = "context_scheduler_claim_file_import"
_FIRST_FUNCTION = "context_scheduler_claim_first_file_import"
_SIGNATURE = "(bigint, bytea, text[])"
_TTL_SECONDS = 300
_MAX_GENERATION = 4
_BASE_BACKOFF_SECONDS = 30
_MAX_BIGINT = 9_223_372_036_854_775_807
_MIGRATION_FENCE = "context-engine.file-dispatch-migration-fence"
_SCHEDULING_MIGRATION_FENCE = "context-engine.file-change-scheduling-migration-fence"
_V3 = "file-capabilities-v3"
_V4 = "file-capabilities-v4"


def upgrade() -> None:
    """Prefer one eligible expired attempt before delegating first dispatch."""

    connection = op.get_bind()
    for migration_fence in (_SCHEDULING_MIGRATION_FENCE, _MIGRATION_FENCE):
        connection.execute(
            sa.text(
                "SELECT pg_catalog.pg_advisory_xact_lock("
                "pg_catalog.hashtextextended(:migration_fence, 0))"
            ),
            {"migration_fence": migration_fence},
        )
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"ALTER FUNCTION public.{_FUNCTION}{_SIGNATURE} "
        f"RENAME TO {_FIRST_FUNCTION}"
    )
    op.execute(
        f"REVOKE EXECUTE ON FUNCTION public.{_FIRST_FUNCTION}{_SIGNATURE} "
        f"FROM {_SCHEDULER}"
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")
    op.execute(
        "CREATE INDEX ix_file_import_job_dispatch_expired ON file_import_job "
        "(lease_expires_at, lease_generation, organization_id, source_id, job_id) "
        "WHERE dispatch_claimed IS TRUE "
        "AND state IN ('leased', 'running', 'prepared', 'ready') "
        f"AND lease_generation BETWEEN 1 AND {_MAX_GENERATION - 1}"
    )
    op.execute(
        "CREATE POLICY file_import_job_event_file_dispatch_definer_select "
        "ON file_import_job_event FOR SELECT "
        f"TO {_DEFINER} USING (organization_id = "
        "NULLIF(current_setting('app.organization_id', true), '')::uuid)"
    )
    op.execute(
        "CREATE POLICY file_import_job_event_file_dispatch_definer_insert "
        "ON file_import_job_event FOR INSERT "
        f"TO {_DEFINER} WITH CHECK (organization_id = "
        "NULLIF(current_setting('app.organization_id', true), '')::uuid)"
    )
    op.execute(f"GRANT SELECT, INSERT ON file_import_job_event TO {_DEFINER}")
    op.execute(
        "GRANT UPDATE (recovery_from_state, lease_redeemed_at) "
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
            selected_state text;
            selected_resume_state text;
            selected_generation bigint;
            next_generation bigint;
            next_ordinal bigint;
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
            SELECT job.organization_id, job.job_id, job.source_id,
                   job.state, job.recovery_from_state, job.lease_generation
            INTO selected_organization_id, selected_job_id, selected_source_id,
                 selected_state, selected_resume_state, selected_generation
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
            WHERE job.dispatch_claimed IS TRUE
              AND job.state IN ('leased', 'running', 'prepared', 'ready')
              AND job.lease_generation BETWEEN 1 AND {_MAX_GENERATION - 1}
              AND job.lease_expires_at
                  + pg_catalog.make_interval(secs =>
                        {_BASE_BACKOFF_SECONDS}
                        * (1::bigint << (job.lease_generation - 1)::integer)
                    ) <= authority_checked_at
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
            ORDER BY job.lease_expires_at
                         + pg_catalog.make_interval(secs =>
                               {_BASE_BACKOFF_SECONDS}
                               * (1::bigint << (job.lease_generation - 1)::integer)
                           ),
                     scheduled.accepted_at, scheduled.sequence,
                     accepted_page.page_ordinal, accepted_change.change_ordinal,
                     job.organization_id, job.source_id, job.job_id
            FOR UPDATE OF job SKIP LOCKED
            LIMIT 1;
            IF NOT FOUND THEN
                RETURN QUERY SELECT *
                FROM public.{_FIRST_FUNCTION}(
                    requested_signing_key_version,
                    requested_nonce,
                    configured_root_refs
                );
                RETURN;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM public.source_version AS selected_version
                WHERE selected_version.organization_id = selected_organization_id
                  AND selected_version.source_id = selected_source_id
                  AND selected_version.root_ref = ANY(configured_root_refs)
            ) THEN RETURN; END IF;
            PERFORM pg_catalog.pg_advisory_xact_lock(
                pg_catalog.hashtextextended(
                    'context-engine.file-source-progress:'
                    || selected_organization_id::text || ':'
                    || selected_source_id::text, 0
                )
            );
            PERFORM pg_catalog.set_config(
                'app.organization_id', selected_organization_id::text, true
            );
            PERFORM pg_catalog.set_config(
                'app.worker_job_id', selected_job_id::text, true
            );
            authority_checked_at := pg_catalog.clock_timestamp();
            minted_at := pg_catalog.date_trunc(
                'second', pg_catalog.clock_timestamp()
            );
            IF NOT EXISTS (
                SELECT 1
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
                 AND accepted_change.content_sha256 =
                     acquisition.expected_content_sha256
                 AND accepted_change.content_length =
                     acquisition.expected_content_length
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
                 AND audience.membership_version =
                     acquisition.audience_membership_version
                JOIN public.service_principal AS receiver
                  ON receiver.organization_id = job.organization_id
                 AND receiver.service_principal_id = job.service_principal_id
                 AND receiver.workload = job.workload
                 AND receiver.worker_audience = job.worker_audience
                 AND receiver.operation = job.operation
                WHERE job.organization_id = selected_organization_id
                  AND job.job_id = selected_job_id
                  AND job.source_id = selected_source_id
                  AND job.state = selected_state
                  AND job.recovery_from_state IS NOT DISTINCT FROM selected_resume_state
                  AND job.lease_generation = selected_generation
                  AND job.dispatch_claimed IS TRUE
                  AND job.lease_expires_at
                      + pg_catalog.make_interval(secs =>
                            {_BASE_BACKOFF_SECONDS}
                            * (1::bigint << (job.lease_generation - 1)::integer)
                        ) <= authority_checked_at
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
                       AND current_page.page_ref = current_checkpoint.change_page_ref
                      WHERE current_checkpoint.organization_id =
                            acquisition.organization_id
                        AND current_checkpoint.source_id = acquisition.source_id
                        AND current_checkpoint.change_kind = 'file_change_page'
                      ORDER BY current_checkpoint.sequence DESC
                      LIMIT 1
                  )
                FOR UPDATE OF source, audience, receiver SKIP LOCKED
            ) THEN RETURN; END IF;
            IF selected_state <> 'leased' THEN
                selected_resume_state := selected_state;
            END IF;
            next_generation := selected_generation + 1;
            SELECT COALESCE(max(event.ordinal), -1) + 1 INTO next_ordinal
            FROM public.file_import_job_event AS event
            WHERE event.organization_id = selected_organization_id
              AND event.job_id = selected_job_id;
            INSERT INTO public.file_import_job_event (
                organization_id, job_id, ordinal, event_type, boundary,
                lease_generation, state_at_event, revision_id,
                reason_digest, occurred_at
            ) SELECT
                selected_organization_id, selected_job_id, next_ordinal,
                'reclaimed',
                CASE COALESCE(selected_resume_state, 'running')
                    WHEN 'running' THEN 'acquired'
                    WHEN 'prepared' THEN 'prepared'
                    ELSE 'indexed'
                END,
                next_generation, selected_state, job.revision_id,
                encode(public.digest(
                    convert_to('context-engine.file-reclaim.v1', 'UTF8')
                    || decode('00', 'hex')
                    || uuid_send(selected_organization_id)
                    || uuid_send(selected_job_id)
                    || int8send(next_generation),
                    'sha256'
                ), 'hex'), minted_at
            FROM public.file_import_job AS job
            WHERE job.organization_id = selected_organization_id
              AND job.job_id = selected_job_id;
            RETURN QUERY
            UPDATE public.file_import_job AS job
            SET state = 'leased',
                lease_generation = next_generation,
                recovery_from_state = selected_resume_state,
                signing_key_version = requested_signing_key_version,
                lease_nonce_digest = public.digest(requested_nonce, 'sha256'),
                lease_issued_at = minted_at,
                lease_expires_at = minted_at
                    + pg_catalog.make_interval(secs => {_TTL_SECONDS}),
                lease_redeemed_at = NULL
            WHERE job.organization_id = selected_organization_id
              AND job.job_id = selected_job_id
              AND job.state = selected_state
              AND job.lease_generation = selected_generation
            RETURNING job.organization_id, job.job_id, job.source_id,
                      job.service_principal_id, job.lease_generation,
                      job.lease_issued_at, job.lease_expires_at;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'File reclaim lost its exact job fence'
                    USING ERRCODE = '40001';
            END IF;
        END;
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_FUNCTION}{_SIGNATURE} FROM PUBLIC")
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"ALTER FUNCTION public.{_FUNCTION}{_SIGNATURE} OWNER TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_FUNCTION}{_SIGNATURE} TO {_SCHEDULER}"
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


def downgrade() -> None:
    """Remove automatic reclaim only when it has minted no later generation."""

    connection = op.get_bind()
    for migration_fence in (_SCHEDULING_MIGRATION_FENCE, _MIGRATION_FENCE):
        connection.execute(
            sa.text(
                "SELECT pg_catalog.pg_advisory_xact_lock("
                "pg_catalog.hashtextextended(:migration_fence, 0))"
            ),
            {"migration_fence": migration_fence},
        )
    reclaimed = connection.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM public.file_import_job "
            "WHERE dispatch_claimed IS TRUE AND lease_generation > 1)"
        )
    ).scalar_one()
    if reclaimed:
        raise RuntimeError(
            "automatic File reclaim downgrade requires no retained "
            "higher-generation lease; use a forward fix"
        )
    op.execute(f"DROP FUNCTION public.{_FUNCTION}{_SIGNATURE}")
    op.execute(
        "REVOKE UPDATE (recovery_from_state, lease_redeemed_at) "
        f"ON file_import_job FROM {_DEFINER}"
    )
    op.execute(f"REVOKE SELECT, INSERT ON file_import_job_event FROM {_DEFINER}")
    op.execute(
        "DROP POLICY file_import_job_event_file_dispatch_definer_insert "
        "ON file_import_job_event"
    )
    op.execute(
        "DROP POLICY file_import_job_event_file_dispatch_definer_select "
        "ON file_import_job_event"
    )
    op.drop_index(
        "ix_file_import_job_dispatch_expired",
        table_name="file_import_job",
    )
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"ALTER FUNCTION public.{_FIRST_FUNCTION}{_SIGNATURE} RENAME TO {_FUNCTION}"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_FUNCTION}{_SIGNATURE} TO {_SCHEDULER}"
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")
