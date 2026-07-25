"""Schedule accepted File changes through exact import jobs.

Revision ID: 20260725_0029
Revises: 20260725_0028
Create Date: 2026-07-25
"""

# ruff: noqa: E501

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260725_0029"
down_revision: str | None = "20260725_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONTROL = "context_engine_control"
_DEFINER = "context_engine_worker_lease_definer"
_FUNCTION = "context_control_schedule_file_change_page"
_SIGNATURE = "(uuid, uuid, uuid, text, text, uuid, bigint, uuid)"
_WORKLOAD = "supply.file-import"
_AUDIENCE = "context-engine-worker"
_OPERATION = "file.import"
_WORKER = "context_engine_worker"
_REDEEM = "context_worker_redeem_file_import"
_REDEEM_SIGNATURE = "(uuid, uuid, uuid, text, bigint, bigint, bytea, timestamp with time zone, timestamp with time zone)"
_PUBLICATION_FENCE = "context_file_source_fence_scheduled_publication_epoch"
_PUBLICATION_FENCE_TRIGGER = "file_source_publish_watermark_current_scheduled_epoch"
_V3 = """{"aclEvidenceMode":"mirrored","authorizeAndProject":"unavailable","batchLimits":"available","checkpoint":"available","checkpointSemantics":"available","consistencyGuarantees":"unavailable","contentKinds":["markdown"],"cursorSemantics":"available","declarationVersion":"file-capabilities-v3","deletion":"unavailable","describeCapabilities":"available","discover":"unavailable","fileSourceAccess":"available","freshness":"unavailable","ingestionJobs":"available","projectionFields":[],"readChanges":"available","resourceKinds":["markdown_document"],"sourceMode":"materialized"}"""
_REQUEST_DIGEST_EXPRESSION = """
pg_catalog.encode(
    public.digest(
        pg_catalog.convert_to(
            'context-engine.schedule-file-change.v1', 'UTF8'
        ) || pg_catalog.decode('00', 'hex')
        || pg_catalog.uuid_send(requested_organization_id)
        || pg_catalog.uuid_send(requested_source_id)
        || pg_catalog.uuid_send(requested_source_version_id)
        || pg_catalog.decode(requested_page_ref, 'hex')
        || pg_catalog.int2send(current_change.change_ordinal)
        || pg_catalog.int4send(pg_catalog.octet_length(
            requested_audience_principal_ref
        ))
        || pg_catalog.convert_to(
            requested_audience_principal_ref, 'UTF8'
        )
        || pg_catalog.uuid_send(requested_audience_membership_id)
        || pg_catalog.int8send(requested_audience_membership_version)
        || pg_catalog.int4send(pg_catalog.octet_length(
            current_change.relative_path
        ))
        || pg_catalog.convert_to(current_change.relative_path, 'UTF8')
        || pg_catalog.decode(current_change.content_sha256, 'hex')
        || pg_catalog.int8send(current_change.content_length),
        'sha256'
    ),
    'hex'
)
""".strip()


def upgrade() -> None:
    """Bind accepted observations to all-or-none existing File import jobs."""

    op.drop_constraint(
        "ck_file_acquisition_one_markdown_filename",
        "file_acquisition",
        type_="check",
    )
    op.create_check_constraint(
        "ck_file_acquisition_one_markdown_filename",
        "file_acquisition",
        "relative_path ~ '^[^/\\\\]*\\.[mM][dD]$' AND relative_path NOT IN ('.', '..')",
    )
    op.create_unique_constraint(
        "uq_file_source_change_exact_observation",
        "file_source_change",
        [
            "organization_id",
            "source_id",
            "source_version_id",
            "page_ref",
            "change_ordinal",
            "relative_path",
            "content_sha256",
            "content_length",
        ],
    )
    op.add_column(
        "file_acquisition",
        sa.Column("change_page_ref", sa.Text(), nullable=True),
    )
    op.add_column(
        "file_acquisition",
        sa.Column("change_ordinal", sa.SmallInteger(), nullable=True),
    )
    op.add_column(
        "file_acquisition",
        sa.Column("expected_content_sha256", sa.Text(), nullable=True),
    )
    op.add_column(
        "file_acquisition",
        sa.Column("expected_content_length", sa.BigInteger(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_file_acquisition_change_observation",
        "file_acquisition",
        ["organization_id", "source_id", "change_page_ref", "change_ordinal"],
    )
    op.create_foreign_key(
        "fk_file_acquisition_change_observation_exact",
        "file_acquisition",
        "file_source_change",
        [
            "organization_id",
            "source_id",
            "source_version_id",
            "change_page_ref",
            "change_ordinal",
            "relative_path",
            "expected_content_sha256",
            "expected_content_length",
        ],
        [
            "organization_id",
            "source_id",
            "source_version_id",
            "page_ref",
            "change_ordinal",
            "relative_path",
            "content_sha256",
            "content_length",
        ],
    )
    op.create_check_constraint(
        "ck_file_acquisition_change_observation",
        "file_acquisition",
        "(change_page_ref IS NULL AND change_ordinal IS NULL "
        "AND expected_content_sha256 IS NULL "
        "AND expected_content_length IS NULL) OR "
        "(change_page_ref ~ '^[0-9a-f]{64}$' "
        "AND change_ordinal BETWEEN 1 AND 100 "
        "AND expected_content_sha256 ~ '^[0-9a-f]{64}$' "
        "AND expected_content_length >= 0)",
    )
    op.execute(
        f"""
        CREATE FUNCTION public.{_FUNCTION}(
            requested_organization_id uuid,
            requested_source_id uuid,
            requested_source_version_id uuid,
            requested_page_ref text,
            requested_audience_principal_ref text,
            requested_audience_membership_id uuid,
            requested_audience_membership_version bigint,
            requested_service_principal_id uuid
        ) RETURNS TABLE (
            change_ordinal smallint,
            relative_path text,
            content_sha256 text,
            content_length bigint,
            job_id uuid,
            service_principal_id uuid
        )
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE
            trusted_now timestamptz;
            selected_change_count smallint;
            selected_scan_epoch uuid;
            stored_change_count integer;
            existing_acquisition_count integer;
            current_change record;
            current_acquisition_id uuid;
            current_job_id uuid;
            current_request_digest text;
            current_idempotency_key text;
        BEGIN
            IF SESSION_USER <> '{_CONTROL}'
               OR requested_organization_id IS NULL
               OR requested_source_id IS NULL
               OR requested_source_version_id IS NULL
               OR requested_page_ref !~ '^[0-9a-f]{{64}}$'
               OR requested_audience_principal_ref
                    !~ '^[^[:space:]]{{1,255}}$'
               OR requested_audience_membership_id IS NULL
               OR requested_audience_membership_version
                    NOT BETWEEN 1 AND 9223372036854775807
               OR requested_service_principal_id IS NULL
            THEN RETURN; END IF;
            trusted_now := pg_catalog.statement_timestamp();
            PERFORM pg_catalog.set_config(
                'app.organization_id', requested_organization_id::text, true
            );
            PERFORM pg_catalog.pg_advisory_xact_lock(
                pg_catalog.hashtextextended(
                    'context-engine.file-source-progress:'
                    || requested_organization_id::text || ':'
                    || requested_source_id::text, 0
                )
            );
            PERFORM 1
            FROM public.context_source AS source
            JOIN public.source_version AS version
              ON version.organization_id = source.organization_id
             AND version.source_id = source.source_id
             AND version.version_id = source.active_version_id
            WHERE source.organization_id = requested_organization_id
              AND source.source_id = requested_source_id
              AND source.active_version_id = requested_source_version_id
              AND source.source_kind = 'file'
              AND source.lifecycle_state = 'active'
              AND version.capability_manifest = '{_V3}'::jsonb
            FOR UPDATE OF source;
            IF NOT FOUND THEN RETURN; END IF;
            SELECT page.change_count, page.scan_epoch
            INTO selected_change_count, selected_scan_epoch
            FROM public.file_source_change_page AS page
            WHERE page.organization_id = requested_organization_id
              AND page.source_id = requested_source_id
              AND page.source_version_id = requested_source_version_id
              AND page.page_ref = requested_page_ref
              AND page.change_count > 0;
            IF selected_change_count IS NULL THEN RETURN; END IF;
            SELECT count(*)
            INTO stored_change_count
            FROM public.file_source_change AS change
            WHERE change.organization_id = requested_organization_id
              AND change.source_id = requested_source_id
              AND change.source_version_id = requested_source_version_id
              AND change.page_ref = requested_page_ref;
            IF stored_change_count <> selected_change_count
               OR EXISTS (
                   SELECT 1
                   FROM public.file_source_change AS change
                   WHERE change.organization_id = requested_organization_id
                     AND change.source_id = requested_source_id
                     AND change.source_version_id = requested_source_version_id
                     AND change.page_ref = requested_page_ref
                     AND (
                         change.change_kind <> 'upsert'
                         OR change.change_ordinal NOT BETWEEN 1
                             AND selected_change_count
                     )
               )
               OR EXISTS (
                   SELECT 1
                   FROM pg_catalog.generate_series(
                       1, selected_change_count
                   ) AS expected(ordinal)
                   WHERE NOT EXISTS (
                       SELECT 1
                       FROM public.file_source_change AS change
                       WHERE change.organization_id = requested_organization_id
                         AND change.source_id = requested_source_id
                         AND change.source_version_id = requested_source_version_id
                         AND change.page_ref = requested_page_ref
                         AND change.change_ordinal = expected.ordinal
                   )
               )
            THEN RETURN; END IF;
            IF NOT EXISTS (
                SELECT 1 FROM public.membership AS membership
                WHERE membership.organization_id = requested_organization_id
                  AND membership.membership_id = requested_audience_membership_id
                  AND membership.membership_version = requested_audience_membership_version
                  AND membership.status = 'active'
                  AND membership.valid_from <= trusted_now
                  AND (membership.valid_until IS NULL
                       OR membership.valid_until > trusted_now)
            ) OR NOT EXISTS (
                SELECT 1 FROM public.service_principal AS receiver
                WHERE receiver.organization_id = requested_organization_id
                  AND receiver.service_principal_id = requested_service_principal_id
                  AND receiver.workload = '{_WORKLOAD}'
                  AND receiver.worker_audience = '{_AUDIENCE}'
                  AND receiver.operation = '{_OPERATION}'
                  AND receiver.enabled IS TRUE
            ) THEN RETURN; END IF;
            SELECT count(*)
            INTO existing_acquisition_count
            FROM public.file_acquisition AS acquisition
            WHERE acquisition.organization_id = requested_organization_id
              AND acquisition.source_id = requested_source_id
              AND acquisition.change_page_ref = requested_page_ref;
            IF existing_acquisition_count NOT IN (0, selected_change_count)
            THEN RETURN; END IF;

            IF existing_acquisition_count = 0 THEN
                IF selected_scan_epoch IS DISTINCT FROM (
                    SELECT current_page.scan_epoch
                    FROM public.file_source_acquisition_checkpoint AS checkpoint
                    JOIN public.file_source_change_page AS current_page
                      ON current_page.organization_id = checkpoint.organization_id
                     AND current_page.source_id = checkpoint.source_id
                     AND current_page.source_version_id = checkpoint.source_version_id
                     AND current_page.page_ref = checkpoint.change_page_ref
                    WHERE checkpoint.organization_id = requested_organization_id
                      AND checkpoint.source_id = requested_source_id
                      AND checkpoint.change_kind = 'file_change_page'
                    ORDER BY checkpoint.sequence DESC
                    LIMIT 1
                ) THEN RETURN; END IF;
                BEGIN
                    FOR current_change IN
                        SELECT change.change_ordinal, change.relative_path,
                               change.content_sha256, change.content_length
                        FROM public.file_source_change AS change
                        WHERE change.organization_id = requested_organization_id
                          AND change.source_id = requested_source_id
                          AND change.source_version_id = requested_source_version_id
                          AND change.page_ref = requested_page_ref
                        ORDER BY change.change_ordinal
                    LOOP
                        current_acquisition_id := pg_catalog.gen_random_uuid();
                        current_job_id := pg_catalog.gen_random_uuid();
                        current_idempotency_key :=
                            'change:' || requested_page_ref || ':'
                            || current_change.change_ordinal::text;
                        current_request_digest := {_REQUEST_DIGEST_EXPRESSION};
                        INSERT INTO public.file_acquisition (
                            organization_id, acquisition_id, source_id,
                            source_version_id, relative_path,
                            audience_principal_ref, audience_membership_id,
                            audience_membership_version, idempotency_key,
                            request_digest, created_at, change_page_ref,
                            change_ordinal, expected_content_sha256,
                            expected_content_length
                        ) VALUES (
                            requested_organization_id, current_acquisition_id,
                            requested_source_id, requested_source_version_id,
                            current_change.relative_path,
                            requested_audience_principal_ref,
                            requested_audience_membership_id,
                            requested_audience_membership_version,
                            current_idempotency_key, current_request_digest,
                            trusted_now, requested_page_ref,
                            current_change.change_ordinal,
                            current_change.content_sha256,
                            current_change.content_length
                        );
                        PERFORM pg_catalog.set_config(
                            'app.file_acquisition_id',
                            current_acquisition_id::text, true
                        );
                        PERFORM pg_catalog.set_config(
                            'app.worker_job_id', current_job_id::text, true
                        );
                        INSERT INTO public.file_import_job (
                            organization_id, job_id, acquisition_id, source_id,
                            service_principal_id, workload, worker_audience,
                            actor_kind, operation, state, created_at
                        ) VALUES (
                            requested_organization_id, current_job_id,
                            current_acquisition_id, requested_source_id,
                            requested_service_principal_id, '{_WORKLOAD}',
                            '{_AUDIENCE}', 'service', '{_OPERATION}',
                            'available', trusted_now
                        );
                    END LOOP;
                EXCEPTION WHEN unique_violation OR foreign_key_violation
                    OR check_violation THEN
                    RETURN;
                END;
            END IF;

            FOR current_change IN
                SELECT change.change_ordinal, change.relative_path,
                       change.content_sha256, change.content_length
                FROM public.file_source_change AS change
                WHERE change.organization_id = requested_organization_id
                  AND change.source_id = requested_source_id
                  AND change.source_version_id = requested_source_version_id
                  AND change.page_ref = requested_page_ref
                ORDER BY change.change_ordinal
            LOOP
                current_idempotency_key :=
                    'change:' || requested_page_ref || ':'
                    || current_change.change_ordinal::text;
                current_request_digest := {_REQUEST_DIGEST_EXPRESSION};
                SELECT acquisition.acquisition_id
                INTO current_acquisition_id
                FROM public.file_acquisition AS acquisition
                WHERE acquisition.organization_id = requested_organization_id
                  AND acquisition.source_id = requested_source_id
                  AND acquisition.source_version_id = requested_source_version_id
                  AND acquisition.change_page_ref = requested_page_ref
                  AND acquisition.change_ordinal = current_change.change_ordinal
                  AND acquisition.relative_path = current_change.relative_path
                  AND acquisition.expected_content_sha256 = current_change.content_sha256
                  AND acquisition.expected_content_length = current_change.content_length
                  AND acquisition.audience_principal_ref = requested_audience_principal_ref
                  AND acquisition.audience_membership_id = requested_audience_membership_id
                  AND acquisition.audience_membership_version = requested_audience_membership_version
                  AND acquisition.idempotency_key = current_idempotency_key
                  AND acquisition.request_digest = current_request_digest;
                IF current_acquisition_id IS NULL THEN RETURN; END IF;
                PERFORM pg_catalog.set_config(
                    'app.file_acquisition_id', current_acquisition_id::text, true
                );
                SELECT job.job_id
                INTO current_job_id
                FROM public.file_import_job AS job
                WHERE job.organization_id = requested_organization_id
                  AND job.acquisition_id = current_acquisition_id
                  AND job.source_id = requested_source_id
                  AND job.service_principal_id = requested_service_principal_id
                  AND job.workload = '{_WORKLOAD}'
                  AND job.worker_audience = '{_AUDIENCE}'
                  AND job.operation = '{_OPERATION}';
                IF current_job_id IS NULL THEN RETURN; END IF;
                PERFORM pg_catalog.set_config(
                    'app.worker_job_id', current_job_id::text, true
                );
            END LOOP;
            FOR current_change IN
                SELECT change.change_ordinal, change.relative_path,
                       change.content_sha256, change.content_length
                FROM public.file_source_change AS change
                WHERE change.organization_id = requested_organization_id
                  AND change.source_id = requested_source_id
                  AND change.source_version_id = requested_source_version_id
                  AND change.page_ref = requested_page_ref
                ORDER BY change.change_ordinal
            LOOP
                SELECT acquisition.acquisition_id
                INTO current_acquisition_id
                FROM public.file_acquisition AS acquisition
                WHERE acquisition.organization_id = requested_organization_id
                  AND acquisition.source_id = requested_source_id
                  AND acquisition.source_version_id = requested_source_version_id
                  AND acquisition.change_page_ref = requested_page_ref
                  AND acquisition.change_ordinal = current_change.change_ordinal;
                PERFORM pg_catalog.set_config(
                    'app.file_acquisition_id', current_acquisition_id::text, true
                );
                SELECT job.job_id
                INTO current_job_id
                FROM public.file_import_job AS job
                WHERE job.organization_id = requested_organization_id
                  AND job.acquisition_id = current_acquisition_id
                  AND job.source_id = requested_source_id
                  AND job.service_principal_id = requested_service_principal_id
                  AND job.workload = '{_WORKLOAD}'
                  AND job.worker_audience = '{_AUDIENCE}'
                  AND job.operation = '{_OPERATION}';
                IF current_job_id IS NULL THEN
                    RAISE EXCEPTION USING ERRCODE = '55000',
                        MESSAGE = 'validated File schedule changed';
                END IF;
                change_ordinal := current_change.change_ordinal;
                relative_path := current_change.relative_path;
                content_sha256 := current_change.content_sha256;
                content_length := current_change.content_length;
                job_id := current_job_id;
                service_principal_id := requested_service_principal_id;
                RETURN NEXT;
            END LOOP;
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
        f"GRANT EXECUTE ON FUNCTION public.{_FUNCTION}{_SIGNATURE} TO {_CONTROL}"
    )
    op.execute("RESET ROLE")
    _replace_redeem_function(include_observation=True)
    _create_publication_epoch_fence()


def downgrade() -> None:
    """Remove scheduling only when retained acquisitions fit the prior schema."""

    op.execute("LOCK TABLE public.file_acquisition IN ACCESS EXCLUSIVE MODE")
    blocker = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT CASE "
                "WHEN EXISTS (SELECT 1 FROM file_acquisition "
                "WHERE change_page_ref IS NOT NULL) "
                "THEN 'accepted_lineage' "
                "WHEN EXISTS (SELECT 1 FROM file_acquisition "
                "WHERE relative_path ~ '^\\.[mM][dD]$') "
                "THEN 'newer_manual_path' "
                "END"
            )
        )
        .scalar_one()
    )
    if blocker == "accepted_lineage":
        raise RuntimeError(
            "File change scheduling downgrade requires no retained "
            "accepted-change acquisition lineage; use a forward fix"
        )
    if blocker == "newer_manual_path":
        raise RuntimeError(
            "File change scheduling downgrade requires no newer manual "
            "File import paths; use a forward fix"
        )
    _drop_publication_epoch_fence()
    _replace_redeem_function(include_observation=False)
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(f"DROP FUNCTION public.{_FUNCTION}{_SIGNATURE}")
    op.execute("RESET ROLE")
    op.drop_constraint(
        "ck_file_acquisition_change_observation",
        "file_acquisition",
        type_="check",
    )
    op.drop_constraint(
        "fk_file_acquisition_change_observation_exact",
        "file_acquisition",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_file_acquisition_change_observation",
        "file_acquisition",
        type_="unique",
    )
    op.drop_column("file_acquisition", "expected_content_length")
    op.drop_column("file_acquisition", "expected_content_sha256")
    op.drop_column("file_acquisition", "change_ordinal")
    op.drop_column("file_acquisition", "change_page_ref")
    op.drop_constraint(
        "uq_file_source_change_exact_observation",
        "file_source_change",
        type_="unique",
    )
    op.drop_constraint(
        "ck_file_acquisition_one_markdown_filename",
        "file_acquisition",
        type_="check",
    )
    op.create_check_constraint(
        "ck_file_acquisition_one_markdown_filename",
        "file_acquisition",
        "relative_path ~ '^[^/\\\\]+\\.[mM][dD]$' AND relative_path NOT IN ('.', '..')",
    )


def _replace_redeem_function(*, include_observation: bool) -> None:
    observation_returns = (
        ", expected_content_sha256 text, expected_content_length bigint"
        if include_observation
        else ""
    )
    observation_select = (
        ", acquisition.expected_content_sha256, acquisition.expected_content_length"
        if include_observation
        else ""
    )
    observation_declaration = (
        "selected_change_page_ref text;" if include_observation else ""
    )
    current_epoch_guard = (
        """
            SELECT acquisition.change_page_ref
            INTO selected_change_page_ref
            FROM public.file_import_job AS selected_job
            JOIN public.file_acquisition AS acquisition
              ON acquisition.organization_id = selected_job.organization_id
             AND acquisition.acquisition_id = selected_job.acquisition_id
            WHERE selected_job.organization_id = requested_organization_id
              AND selected_job.job_id = requested_job_id
              AND selected_job.service_principal_id =
                  requested_service_principal_id
              AND selected_job.source_id::text = requested_source_ref
              AND selected_job.state = 'leased'
              AND selected_job.lease_generation = requested_lease_generation
              AND selected_job.signing_key_version =
                  requested_signing_key_version
              AND selected_job.lease_nonce_digest =
                  public.digest(requested_nonce, 'sha256')
              AND selected_job.lease_issued_at = requested_issued_at
              AND selected_job.lease_expires_at = requested_expires_at
              AND redeemed_at >= selected_job.lease_issued_at
              AND redeemed_at < selected_job.lease_expires_at
              AND EXISTS (
                  SELECT 1 FROM public.service_principal AS principal
                  WHERE principal.organization_id =
                        selected_job.organization_id
                    AND principal.service_principal_id =
                        selected_job.service_principal_id
                    AND principal.workload = selected_job.workload
                    AND principal.worker_audience =
                        selected_job.worker_audience
                    AND principal.operation = selected_job.operation
                    AND principal.enabled IS TRUE
              );
            IF selected_change_page_ref IS NOT NULL THEN
                PERFORM pg_catalog.pg_advisory_xact_lock(
                    pg_catalog.hashtextextended(
                        'context-engine.file-source-progress:'
                        || requested_organization_id::text || ':'
                        || requested_source_ref, 0
                    )
                );
                redeemed_at := pg_catalog.clock_timestamp();
                IF NOT EXISTS (
                      SELECT 1
                      FROM public.file_import_job AS selected_job
                      JOIN public.file_acquisition AS acquisition
                        ON acquisition.organization_id =
                           selected_job.organization_id
                       AND acquisition.acquisition_id =
                           selected_job.acquisition_id
                      JOIN public.file_source_change_page AS accepted_page
                        ON accepted_page.organization_id =
                           acquisition.organization_id
                       AND accepted_page.source_id = acquisition.source_id
                       AND accepted_page.source_version_id =
                           acquisition.source_version_id
                       AND accepted_page.page_ref = acquisition.change_page_ref
                      WHERE selected_job.organization_id =
                            requested_organization_id
                        AND selected_job.job_id = requested_job_id
                        AND selected_job.source_id::text = requested_source_ref
                        AND acquisition.change_page_ref =
                            selected_change_page_ref
                        AND accepted_page.scan_epoch = (
                            SELECT current_page.scan_epoch
                            FROM public.file_source_acquisition_checkpoint
                                 AS checkpoint
                            JOIN public.file_source_change_page AS current_page
                              ON current_page.organization_id =
                                 checkpoint.organization_id
                             AND current_page.source_id = checkpoint.source_id
                             AND current_page.source_version_id =
                                 checkpoint.source_version_id
                             AND current_page.page_ref =
                                 checkpoint.change_page_ref
                            WHERE checkpoint.organization_id =
                                  acquisition.organization_id
                              AND checkpoint.source_id = acquisition.source_id
                              AND checkpoint.change_kind = 'file_change_page'
                            ORDER BY checkpoint.sequence DESC
                            LIMIT 1
                        )
                  )
                THEN
                    RAISE EXCEPTION USING ERRCODE = '55000',
                        MESSAGE = 'scheduled File redemption scan epoch changed';
                END IF;
            END IF;
        """
        if include_observation
        else ""
    )
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(f"DROP FUNCTION public.{_REDEEM}{_REDEEM_SIGNATURE}")
    op.execute("RESET ROLE")
    op.execute(
        f"""
        CREATE FUNCTION public.{_REDEEM}(
            requested_organization_id uuid, requested_job_id uuid,
            requested_service_principal_id uuid, requested_source_ref text,
            requested_lease_generation bigint,
            requested_signing_key_version bigint, requested_nonce bytea,
            requested_issued_at timestamptz, requested_expires_at timestamptz
        ) RETURNS TABLE (
            source_ref text, root_ref text, relative_path text,
            audience_principal_ref text, audience_membership_id uuid,
            audience_membership_version bigint, acquisition_id uuid
            {observation_returns}
        )
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$
        DECLARE
            redeemed_at timestamptz;
            {observation_declaration}
        BEGIN
            IF SESSION_USER <> '{_WORKER}' THEN RETURN; END IF;
            PERFORM pg_catalog.set_config(
                'app.organization_id', requested_organization_id::text, true
            );
            PERFORM pg_catalog.set_config(
                'app.worker_job_id', requested_job_id::text, true
            );
            redeemed_at := pg_catalog.statement_timestamp();
            {current_epoch_guard}
            UPDATE public.file_import_job AS job
            SET state = COALESCE(job.recovery_from_state, 'running'),
                recovery_from_state = NULL,
                lease_redeemed_at = redeemed_at
            WHERE job.organization_id = requested_organization_id
              AND job.job_id = requested_job_id
              AND job.service_principal_id = requested_service_principal_id
              AND job.source_id::text = requested_source_ref
              AND job.state = 'leased'
              AND job.lease_generation = requested_lease_generation
              AND job.signing_key_version = requested_signing_key_version
              AND job.lease_nonce_digest = public.digest(requested_nonce, 'sha256')
              AND job.lease_issued_at = requested_issued_at
              AND job.lease_expires_at = requested_expires_at
              AND redeemed_at >= job.lease_issued_at
              AND redeemed_at < job.lease_expires_at
              AND EXISTS (
                  SELECT 1 FROM public.service_principal AS principal
                  WHERE principal.organization_id = job.organization_id
                    AND principal.service_principal_id = job.service_principal_id
                    AND principal.workload = job.workload
                    AND principal.worker_audience = job.worker_audience
                    AND principal.operation = job.operation
                    AND principal.enabled IS TRUE
            );
            IF NOT FOUND THEN RETURN; END IF;
            RETURN QUERY
            SELECT job.source_id::text, version.root_ref,
                   acquisition.relative_path,
                   acquisition.audience_principal_ref,
                   acquisition.audience_membership_id,
                   acquisition.audience_membership_version,
                   acquisition.acquisition_id
                   {observation_select}
            FROM public.file_import_job AS job
            JOIN public.file_acquisition AS acquisition
              ON acquisition.organization_id = job.organization_id
             AND acquisition.acquisition_id = job.acquisition_id
            JOIN public.source_version AS version
              ON version.organization_id = acquisition.organization_id
             AND version.source_id = acquisition.source_id
             AND version.version_id = acquisition.source_version_id
            WHERE job.organization_id = requested_organization_id
              AND job.job_id = requested_job_id;
        END; $function$
        """
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION public.{_REDEEM}{_REDEEM_SIGNATURE} FROM PUBLIC"
    )
    op.execute(
        f"ALTER FUNCTION public.{_REDEEM}{_REDEEM_SIGNATURE} OWNER TO {_DEFINER}"
    )
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_REDEEM}{_REDEEM_SIGNATURE} TO {_WORKER}"
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


def _create_publication_epoch_fence() -> None:
    """Fence scheduled visibility in the same transaction as its watermark."""

    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"""
        CREATE FUNCTION public.{_PUBLICATION_FENCE}()
        RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$
        DECLARE
            selected_acquisition_id uuid;
            selected_change_page_ref text;
            selected_scan_epoch uuid;
            current_scan_epoch uuid;
        BEGIN
            IF NEW.change_kind <> 'file_import' THEN RETURN NEW; END IF;
            PERFORM pg_catalog.set_config(
                'app.organization_id', NEW.organization_id::text, true
            );
            SELECT acquisition.acquisition_id,
                   acquisition.change_page_ref
            INTO selected_acquisition_id,
                 selected_change_page_ref
            FROM public.file_source_acquisition_checkpoint AS checkpoint
            JOIN public.file_acquisition AS acquisition
              ON acquisition.organization_id = checkpoint.organization_id
             AND acquisition.acquisition_id = checkpoint.acquisition_id
            WHERE checkpoint.organization_id = NEW.organization_id
              AND checkpoint.source_id = NEW.source_id
              AND checkpoint.sequence = NEW.sequence
              AND checkpoint.checkpoint_ref = NEW.checkpoint_ref
              AND checkpoint.change_kind = NEW.change_kind;
            IF selected_acquisition_id IS NULL THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'File publication lineage is unavailable';
            END IF;
            IF selected_change_page_ref IS NULL THEN RETURN NEW; END IF;
            PERFORM pg_catalog.pg_advisory_xact_lock(
                pg_catalog.hashtextextended(
                    'context-engine.file-source-progress:'
                    || NEW.organization_id::text || ':'
                    || NEW.source_id::text, 0
                )
            );
            SELECT acquisition.acquisition_id,
                   acquisition.change_page_ref,
                   accepted_page.scan_epoch
            INTO selected_acquisition_id,
                 selected_change_page_ref,
                 selected_scan_epoch
            FROM public.file_source_acquisition_checkpoint AS checkpoint
            JOIN public.file_acquisition AS acquisition
              ON acquisition.organization_id = checkpoint.organization_id
             AND acquisition.acquisition_id = checkpoint.acquisition_id
            LEFT JOIN public.file_source_change_page AS accepted_page
              ON accepted_page.organization_id = acquisition.organization_id
             AND accepted_page.source_id = acquisition.source_id
             AND accepted_page.source_version_id = acquisition.source_version_id
             AND accepted_page.page_ref = acquisition.change_page_ref
            WHERE checkpoint.organization_id = NEW.organization_id
              AND checkpoint.source_id = NEW.source_id
              AND checkpoint.sequence = NEW.sequence
              AND checkpoint.checkpoint_ref = NEW.checkpoint_ref
              AND checkpoint.change_kind = NEW.change_kind;
            IF selected_acquisition_id IS NULL
               OR selected_change_page_ref IS NULL
            THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'scheduled File publication lineage changed';
            END IF;
            SELECT current_page.scan_epoch
            INTO current_scan_epoch
            FROM public.file_source_acquisition_checkpoint AS checkpoint
            JOIN public.file_source_change_page AS current_page
              ON current_page.organization_id = checkpoint.organization_id
             AND current_page.source_id = checkpoint.source_id
             AND current_page.source_version_id = checkpoint.source_version_id
             AND current_page.page_ref = checkpoint.change_page_ref
            WHERE checkpoint.organization_id = NEW.organization_id
              AND checkpoint.source_id = NEW.source_id
              AND checkpoint.change_kind = 'file_change_page'
            ORDER BY checkpoint.sequence DESC
            LIMIT 1;
            IF selected_scan_epoch IS NULL
               OR selected_scan_epoch IS DISTINCT FROM current_scan_epoch
            THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'scheduled File publication scan epoch changed';
            END IF;
            RETURN NEW;
        END;
        $function$
        """
    )
    op.execute("RESET ROLE")
    op.execute(
        f"""
        CREATE TRIGGER {_PUBLICATION_FENCE_TRIGGER}
        BEFORE INSERT ON public.file_source_publish_watermark
        FOR EACH ROW EXECUTE FUNCTION public.{_PUBLICATION_FENCE}()
        """
    )
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(f"REVOKE ALL ON FUNCTION public.{_PUBLICATION_FENCE}() FROM PUBLIC")
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


def _drop_publication_epoch_fence() -> None:
    op.execute(
        f"DROP TRIGGER {_PUBLICATION_FENCE_TRIGGER} "
        "ON public.file_source_publish_watermark"
    )
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(f"DROP FUNCTION public.{_PUBLICATION_FENCE}()")
    op.execute("RESET ROLE")
