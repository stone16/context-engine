"""Separate File acquisition checkpoints from publish watermarks.

Revision ID: 20260723_0017
Revises: 20260723_0016
Create Date: 2026-07-23
"""

# ruff: noqa: E501

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260723_0017"
down_revision: str | None = "20260723_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MIGRATOR = "context_engine_migrator"
_CONTROL = "context_engine_control"
_RUNTIME = "context_engine_runtime"
_WORKER = "context_engine_worker"
_DEFINER = "context_engine_worker_lease_definer"
_CHECKPOINT = "file_source_acquisition_checkpoint"
_WATERMARK = "file_source_publish_watermark"
_READ_FUNCTION = "context_control_read_file_source_progress"
_READ_SIGNATURE = "(uuid, uuid)"
_MAX_BIGINT = 9223372036854775807


def _tenant_append_only(table: str) -> None:
    op.execute(f"REVOKE ALL ON TABLE {table} FROM PUBLIC")
    for role in (_CONTROL, _RUNTIME, _WORKER):
        op.execute(f"REVOKE ALL ON TABLE {table} FROM {role}")
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_migrator_administration ON {table} "
        f"FOR ALL TO {_MIGRATOR} USING (true) WITH CHECK (true)"
    )
    tenant = (
        "organization_id = NULLIF("
        "current_setting('app.organization_id', true), ''"
        ")::uuid"
    )
    op.execute(
        f"CREATE POLICY {table}_file_progress_definer_select ON {table} "
        f"FOR SELECT TO {_DEFINER} USING ({tenant})"
    )
    op.execute(
        f"CREATE POLICY {table}_file_progress_definer_insert ON {table} "
        f"FOR INSERT TO {_DEFINER} WITH CHECK ({tenant})"
    )
    op.execute(f"GRANT SELECT, INSERT ON TABLE {table} TO {_DEFINER}")
    op.execute(
        f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION public.context_content_reject_mutation()"
    )


def upgrade() -> None:
    """Persist both progress signals at their exact durable boundaries."""

    op.create_unique_constraint(
        "uq_file_import_job_progress_lineage",
        "file_import_job",
        ["organization_id", "job_id", "acquisition_id", "source_id"],
    )
    op.create_unique_constraint(
        "uq_file_resource_cleanup_intent_progress_lineage",
        "file_resource_cleanup_intent",
        [
            "organization_id",
            "cleanup_intent_id",
            "source_id",
            "resource_ref",
            "revision_id",
        ],
    )
    op.create_table(
        _CHECKPOINT,
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("checkpoint_ref", sa.Text(), nullable=False),
        sa.Column("change_kind", sa.Text(), nullable=False),
        sa.Column("acquisition_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cleanup_intent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resource_ref", sa.Text(), nullable=True),
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_ref", sa.Text(), nullable=True),
        sa.Column("event_sequence", sa.BigInteger(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "source_id",
            "sequence",
            name="pk_file_source_acquisition_checkpoint",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "source_id",
            "sequence",
            "checkpoint_ref",
            "change_kind",
            name="uq_file_source_acquisition_checkpoint_exact",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "checkpoint_ref",
            name="uq_file_source_acquisition_checkpoint_ref",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "acquisition_id",
            name="uq_file_source_acquisition_checkpoint_acquisition",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "cleanup_intent_id",
            name="uq_file_source_acquisition_checkpoint_cleanup",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_id"],
            ["context_source.organization_id", "context_source.source_id"],
            name="fk_file_source_acquisition_checkpoint_source_same_organization",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id", "acquisition_id", "source_id"],
            [
                "file_import_job.organization_id",
                "file_import_job.job_id",
                "file_import_job.acquisition_id",
                "file_import_job.source_id",
            ],
            name="fk_file_source_acquisition_checkpoint_job_exact",
        ),
        sa.ForeignKeyConstraint(
            [
                "organization_id",
                "cleanup_intent_id",
                "source_id",
                "resource_ref",
                "revision_id",
            ],
            [
                "file_resource_cleanup_intent.organization_id",
                "file_resource_cleanup_intent.cleanup_intent_id",
                "file_resource_cleanup_intent.source_id",
                "file_resource_cleanup_intent.resource_ref",
                "file_resource_cleanup_intent.revision_id",
            ],
            name="fk_file_source_acquisition_checkpoint_cleanup_exact",
        ),
        sa.CheckConstraint(
            f"sequence BETWEEN 1 AND {_MAX_BIGINT}",
            name="ck_file_source_acquisition_checkpoint_sequence",
        ),
        sa.CheckConstraint(
            "checkpoint_ref ~ '^facp_[0-9a-f]{64}$'",
            name="ck_file_source_acquisition_checkpoint_ref",
        ),
        sa.CheckConstraint(
            "(change_kind = 'file_import' AND acquisition_id IS NOT NULL "
            "AND job_id IS NOT NULL AND cleanup_intent_id IS NULL "
            "AND resource_ref IS NULL AND revision_id IS NULL "
            "AND event_ref IS NULL AND event_sequence IS NULL) OR "
            "(change_kind = 'file_tombstone' AND acquisition_id IS NULL "
            "AND job_id IS NULL AND cleanup_intent_id IS NOT NULL "
            "AND resource_ref ~ '^resource:file:[0-9a-f]{64}$' "
            "AND revision_id IS NOT NULL "
            "AND event_ref ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' "
            f"AND event_sequence BETWEEN 1 AND {_MAX_BIGINT})",
            name="ck_file_source_acquisition_checkpoint_lineage",
        ),
    )
    op.create_table(
        _WATERMARK,
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("watermark_ref", sa.Text(), nullable=False),
        sa.Column("checkpoint_ref", sa.Text(), nullable=False),
        sa.Column("change_kind", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("resource_ref", sa.Text(), nullable=False),
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "source_id",
            "sequence",
            name="pk_file_source_publish_watermark",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "watermark_ref",
            name="uq_file_source_publish_watermark_ref",
        ),
        sa.ForeignKeyConstraint(
            [
                "organization_id",
                "source_id",
                "sequence",
                "checkpoint_ref",
                "change_kind",
            ],
            [
                f"{_CHECKPOINT}.organization_id",
                f"{_CHECKPOINT}.source_id",
                f"{_CHECKPOINT}.sequence",
                f"{_CHECKPOINT}.checkpoint_ref",
                f"{_CHECKPOINT}.change_kind",
            ],
            name="fk_file_source_publish_watermark_checkpoint_exact",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "resource_ref", "revision_id"],
            [
                "context_revision.organization_id",
                "context_revision.resource_ref",
                "context_revision.revision_id",
            ],
            name="fk_file_source_publish_watermark_revision_same_organization",
        ),
        sa.CheckConstraint(
            f"sequence BETWEEN 1 AND {_MAX_BIGINT}",
            name="ck_file_source_publish_watermark_sequence",
        ),
        sa.CheckConstraint(
            "watermark_ref ~ '^fpwm_[0-9a-f]{64}$'",
            name="ck_file_source_publish_watermark_ref",
        ),
        sa.CheckConstraint(
            "btrim(resource_ref) <> '' AND char_length(resource_ref) <= 512 "
            "AND resource_ref !~ '[[:cntrl:]]'",
            name="ck_file_source_publish_watermark_resource",
        ),
        sa.CheckConstraint(
            "(change_kind = 'file_import' AND outcome IN "
            "('published', 'replaced', 'unchanged')) OR "
            "(change_kind = 'file_tombstone' AND outcome = 'tombstoned')",
            name="ck_file_source_publish_watermark_outcome",
        ),
    )
    _tenant_append_only(_CHECKPOINT)
    _tenant_append_only(_WATERMARK)

    _backfill_progress()
    _create_progress_functions()
    _create_progress_triggers()


def _backfill_progress() -> None:
    op.execute(
        f"""
        WITH changes AS (
            SELECT acquisition.organization_id,
                   acquisition.source_id,
                   'file_import'::text AS change_kind,
                   acquisition.acquisition_id AS carrier_id,
                   acquisition.created_at AS accepted_at,
                   acquisition.acquisition_id,
                   job.job_id,
                   NULL::uuid AS cleanup_intent_id,
                   NULL::text AS resource_ref,
                   NULL::uuid AS revision_id,
                   NULL::text AS event_ref,
                   NULL::bigint AS event_sequence
            FROM public.file_acquisition AS acquisition
            JOIN public.file_import_job AS job
              ON job.organization_id = acquisition.organization_id
             AND job.acquisition_id = acquisition.acquisition_id
             AND job.source_id = acquisition.source_id
            UNION ALL
            SELECT intent.organization_id,
                   intent.source_id,
                   'file_tombstone'::text,
                   intent.cleanup_intent_id,
                   intent.tombstoned_at,
                   NULL::uuid,
                   NULL::uuid,
                   intent.cleanup_intent_id,
                   intent.resource_ref,
                   intent.revision_id,
                   intent.event_ref,
                   intent.event_sequence
            FROM public.file_resource_cleanup_intent AS intent
        ), ranked AS (
            SELECT changes.*,
                   row_number() OVER (
                       PARTITION BY organization_id, source_id
                       ORDER BY accepted_at, change_kind, carrier_id
                   ) AS sequence
            FROM changes
        )
        INSERT INTO public.{_CHECKPOINT} (
            organization_id, source_id, sequence, checkpoint_ref,
            change_kind, acquisition_id, job_id, cleanup_intent_id,
            resource_ref, revision_id, event_ref, event_sequence, accepted_at
        )
        SELECT organization_id, source_id, sequence,
               'facp_' || encode(public.digest(
                   convert_to('context-engine.file-acquisition-checkpoint.v1', 'UTF8')
                   || decode('00', 'hex') || uuid_send(organization_id)
                   || uuid_send(source_id) || int8send(sequence)
                   || uuid_send(carrier_id), 'sha256'
               ), 'hex'),
               change_kind, acquisition_id, job_id, cleanup_intent_id,
               resource_ref, revision_id, event_ref, event_sequence, accepted_at
        FROM ranked
        """
    )
    op.execute(
        f"""
        WITH completed AS (
            SELECT checkpoint.organization_id, checkpoint.source_id,
                   checkpoint.sequence, checkpoint.checkpoint_ref,
                   checkpoint.change_kind, 'unchanged'::text AS outcome,
                   result.resource_ref,
                   result.active_revision_id AS revision_id,
                   result.observed_at AS published_at
            FROM public.{_CHECKPOINT} AS checkpoint
            JOIN public.file_acquisition_result AS result
              ON result.organization_id = checkpoint.organization_id
             AND result.acquisition_id = checkpoint.acquisition_id
            WHERE checkpoint.change_kind = 'file_import'
            UNION ALL
            SELECT checkpoint.organization_id, checkpoint.source_id,
                   checkpoint.sequence, checkpoint.checkpoint_ref,
                   checkpoint.change_kind,
                   CASE WHEN EXISTS (
                       SELECT 1
                       FROM public.file_revision_replacement_plan AS plan
                       WHERE plan.organization_id = checkpoint.organization_id
                         AND plan.acquisition_id = checkpoint.acquisition_id
                         AND plan.replacement_revision_id = snapshot.revision_id
                   ) THEN 'replaced'::text ELSE 'published'::text END,
                   snapshot.resource_ref, snapshot.revision_id,
                   event.recorded_at
            FROM public.{_CHECKPOINT} AS checkpoint
            JOIN public.file_revision_snapshot AS snapshot
              ON snapshot.organization_id = checkpoint.organization_id
             AND snapshot.acquisition_id = checkpoint.acquisition_id
            JOIN public.revision_publication_event AS event
              ON event.organization_id = snapshot.organization_id
             AND event.resource_ref = snapshot.resource_ref
             AND event.revision_id = snapshot.revision_id
             AND event.state = 'active'
            WHERE checkpoint.change_kind = 'file_import'
            UNION ALL
            SELECT checkpoint.organization_id, checkpoint.source_id,
                   checkpoint.sequence, checkpoint.checkpoint_ref,
                   checkpoint.change_kind, 'tombstoned'::text,
                   checkpoint.resource_ref, checkpoint.revision_id,
                   checkpoint.accepted_at
            FROM public.{_CHECKPOINT} AS checkpoint
            WHERE checkpoint.change_kind = 'file_tombstone'
        )
        INSERT INTO public.{_WATERMARK} (
            organization_id, source_id, sequence, watermark_ref,
            checkpoint_ref, change_kind, outcome, resource_ref,
            revision_id, published_at
        )
        SELECT organization_id, source_id, sequence,
               'fpwm_' || encode(public.digest(
                   convert_to('context-engine.file-publish-watermark.v1', 'UTF8')
                   || decode('00', 'hex') || uuid_send(organization_id)
                   || uuid_send(source_id) || int8send(sequence)
                   || convert_to(outcome, 'UTF8') || decode('00', 'hex')
                   || convert_to(resource_ref, 'UTF8') || uuid_send(revision_id),
                   'sha256'
               ), 'hex'),
               checkpoint_ref, change_kind, outcome, resource_ref,
               revision_id, published_at
        FROM completed
        ON CONFLICT (organization_id, source_id, sequence) DO NOTHING
        """
    )


def _create_progress_functions() -> None:
    op.execute(
        f"""
        CREATE FUNCTION public.context_file_source_append_publish_watermark(
            requested_organization_id uuid,
            requested_source_id uuid,
            requested_sequence bigint,
            requested_change_kind text,
            requested_outcome text,
            requested_resource_ref text,
            requested_revision_id uuid,
            requested_published_at timestamptz
        ) RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE selected_checkpoint_ref text;
        BEGIN
            SELECT checkpoint.checkpoint_ref
            INTO selected_checkpoint_ref
            FROM public.{_CHECKPOINT} AS checkpoint
            WHERE checkpoint.organization_id = requested_organization_id
              AND checkpoint.source_id = requested_source_id
              AND checkpoint.sequence = requested_sequence
              AND checkpoint.change_kind = requested_change_kind;
            IF selected_checkpoint_ref IS NULL THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'File publish outcome has no accepted checkpoint';
            END IF;
            INSERT INTO public.{_WATERMARK} (
                organization_id, source_id, sequence, watermark_ref,
                checkpoint_ref, change_kind, outcome, resource_ref,
                revision_id, published_at
            ) VALUES (
                requested_organization_id, requested_source_id,
                requested_sequence,
                'fpwm_' || encode(public.digest(
                    convert_to('context-engine.file-publish-watermark.v1', 'UTF8')
                    || decode('00', 'hex')
                    || uuid_send(requested_organization_id)
                    || uuid_send(requested_source_id)
                    || int8send(requested_sequence)
                    || convert_to(requested_outcome, 'UTF8')
                    || decode('00', 'hex')
                    || convert_to(requested_resource_ref, 'UTF8')
                    || uuid_send(requested_revision_id), 'sha256'
                ), 'hex'),
                selected_checkpoint_ref, requested_change_kind,
                requested_outcome, requested_resource_ref,
                requested_revision_id, requested_published_at
            ) ON CONFLICT (organization_id, source_id, sequence) DO NOTHING;
        END;
        $function$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION public.{_READ_FUNCTION}(
            requested_organization_id uuid,
            requested_source_id uuid
        ) RETURNS TABLE (
            acquisition_sequence bigint,
            acquisition_checkpoint_ref text,
            acquisition_change_kind text,
            acquisition_acquisition_id uuid,
            acquisition_job_id uuid,
            acquisition_cleanup_intent_id uuid,
            acquisition_resource_ref text,
            acquisition_revision_id uuid,
            acquisition_event_ref text,
            acquisition_event_sequence bigint,
            acquisition_accepted_at timestamptz,
            publish_sequence bigint,
            publish_watermark_ref text,
            publish_checkpoint_ref text,
            publish_change_kind text,
            publish_outcome text,
            publish_acquisition_id uuid,
            publish_job_id uuid,
            publish_cleanup_intent_id uuid,
            publish_resource_ref text,
            publish_revision_id uuid,
            publish_event_ref text,
            publish_event_sequence bigint,
            publish_published_at timestamptz
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        BEGIN
            IF SESSION_USER <> '{_CONTROL}'
               OR NULLIF(current_setting('app.organization_id', true), '')::uuid
                    IS DISTINCT FROM requested_organization_id
               OR NOT EXISTS (
                   SELECT 1 FROM public.context_source AS source
                   WHERE source.organization_id = requested_organization_id
                     AND source.source_id = requested_source_id
                     AND source.source_kind = 'file'
               )
            THEN RETURN; END IF;
            RETURN QUERY
            WITH bounds AS (
                SELECT max(checkpoint.sequence) AS latest_sequence,
                       min(checkpoint.sequence) FILTER (
                           WHERE watermark.sequence IS NULL
                       ) AS first_missing
                FROM public.{_CHECKPOINT} AS checkpoint
                LEFT JOIN public.{_WATERMARK} AS watermark
                  ON watermark.organization_id = checkpoint.organization_id
                 AND watermark.source_id = checkpoint.source_id
                 AND watermark.sequence = checkpoint.sequence
                WHERE checkpoint.organization_id = requested_organization_id
                  AND checkpoint.source_id = requested_source_id
            ), visible AS (
                SELECT bounds.latest_sequence,
                       CASE
                         WHEN bounds.latest_sequence IS NULL THEN NULL::bigint
                         WHEN bounds.first_missing IS NULL
                           THEN bounds.latest_sequence
                         ELSE bounds.first_missing - 1
                       END AS visible_sequence
                FROM bounds
            )
            SELECT latest.sequence, latest.checkpoint_ref,
                   latest.change_kind, latest.acquisition_id,
                   latest.job_id, latest.cleanup_intent_id,
                   latest.resource_ref, latest.revision_id,
                   latest.event_ref, latest.event_sequence,
                   latest.accepted_at,
                   watermark.sequence, watermark.watermark_ref,
                   watermark.checkpoint_ref, watermark.change_kind,
                   watermark.outcome, published.acquisition_id,
                   published.job_id, published.cleanup_intent_id,
                   watermark.resource_ref, watermark.revision_id,
                   published.event_ref, published.event_sequence,
                   watermark.published_at
            FROM visible
            LEFT JOIN public.{_CHECKPOINT} AS latest
              ON latest.organization_id = requested_organization_id
             AND latest.source_id = requested_source_id
             AND latest.sequence = visible.latest_sequence
            LEFT JOIN public.{_WATERMARK} AS watermark
              ON watermark.organization_id = requested_organization_id
             AND watermark.source_id = requested_source_id
             AND watermark.sequence = visible.visible_sequence
            LEFT JOIN public.{_CHECKPOINT} AS published
              ON published.organization_id = watermark.organization_id
             AND published.source_id = watermark.source_id
             AND published.sequence = watermark.sequence;
        END;
        $function$
        """
    )
    for function_name, signature in (
        (
            "context_file_source_append_publish_watermark",
            "(uuid, uuid, bigint, text, text, text, uuid, timestamp with time zone)",
        ),
        (_READ_FUNCTION, _READ_SIGNATURE),
    ):
        op.execute(
            f"REVOKE ALL ON FUNCTION public.{function_name}{signature} FROM PUBLIC"
        )
        if function_name == _READ_FUNCTION:
            op.execute(
                f"GRANT EXECUTE ON FUNCTION public.{function_name}{signature} "
                f"TO {_CONTROL}"
            )
        op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
        op.execute(
            f"ALTER FUNCTION public.{function_name}{signature} OWNER TO {_DEFINER}"
        )
        op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


def _create_progress_triggers() -> None:
    op.execute(
        f"""
        CREATE FUNCTION public.context_file_source_checkpoint_import_job()
        RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$
        DECLARE next_sequence bigint;
        BEGIN
            PERFORM pg_advisory_xact_lock(hashtextextended(
                'context-engine.file-source-progress:'
                || NEW.organization_id::text || ':' || NEW.source_id::text, 0
            ));
            SELECT COALESCE(max(checkpoint.sequence), 0) + 1
            INTO next_sequence
            FROM public.{_CHECKPOINT} AS checkpoint
            WHERE checkpoint.organization_id = NEW.organization_id
              AND checkpoint.source_id = NEW.source_id;
            IF next_sequence NOT BETWEEN 1 AND {_MAX_BIGINT} THEN
                RAISE EXCEPTION USING ERRCODE = '22003',
                    MESSAGE = 'File acquisition checkpoint sequence exhausted';
            END IF;
            INSERT INTO public.{_CHECKPOINT} (
                organization_id, source_id, sequence, checkpoint_ref,
                change_kind, acquisition_id, job_id, accepted_at
            )
            SELECT NEW.organization_id, NEW.source_id, next_sequence,
                   'facp_' || encode(public.digest(
                       convert_to('context-engine.file-acquisition-checkpoint.v1', 'UTF8')
                       || decode('00', 'hex')
                       || uuid_send(NEW.organization_id)
                       || uuid_send(NEW.source_id)
                       || int8send(next_sequence)
                       || uuid_send(NEW.job_id), 'sha256'
                   ), 'hex'),
                   'file_import', NEW.acquisition_id, NEW.job_id,
                   acquisition.created_at
            FROM public.file_acquisition AS acquisition
            WHERE acquisition.organization_id = NEW.organization_id
              AND acquisition.acquisition_id = NEW.acquisition_id
              AND acquisition.source_id = NEW.source_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'File import job has no accepted acquisition';
            END IF;
            RETURN NULL;
        END;
        $function$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION public.context_file_source_publish_active_revision()
        RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$
        DECLARE selected_source_id uuid;
                selected_sequence bigint;
                selected_outcome text;
        BEGIN
            SELECT checkpoint.source_id, checkpoint.sequence,
                   CASE WHEN EXISTS (
                       SELECT 1
                       FROM public.file_revision_replacement_plan AS plan
                       WHERE plan.organization_id = NEW.organization_id
                         AND plan.replacement_revision_id = NEW.revision_id
                         AND plan.resource_ref = NEW.resource_ref
                   ) THEN 'replaced'::text ELSE 'published'::text END
            INTO selected_source_id, selected_sequence, selected_outcome
            FROM public.file_revision_snapshot AS snapshot
            JOIN public.{_CHECKPOINT} AS checkpoint
              ON checkpoint.organization_id = snapshot.organization_id
             AND checkpoint.acquisition_id = snapshot.acquisition_id
             AND checkpoint.change_kind = 'file_import'
            WHERE snapshot.organization_id = NEW.organization_id
              AND snapshot.resource_ref = NEW.resource_ref
              AND snapshot.revision_id = NEW.revision_id;
            IF selected_sequence IS NULL THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'Active File Revision has no accepted checkpoint';
            END IF;
            PERFORM public.context_file_source_append_publish_watermark(
                NEW.organization_id, selected_source_id, selected_sequence,
                'file_import', selected_outcome, NEW.resource_ref,
                NEW.revision_id, NEW.recorded_at
            );
            RETURN NULL;
        END;
        $function$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION public.context_file_source_publish_unchanged_acquisition()
        RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$
        DECLARE selected_sequence bigint;
        BEGIN
            SELECT checkpoint.sequence INTO selected_sequence
            FROM public.{_CHECKPOINT} AS checkpoint
            WHERE checkpoint.organization_id = NEW.organization_id
              AND checkpoint.source_id = NEW.source_id
              AND checkpoint.acquisition_id = NEW.acquisition_id
              AND checkpoint.change_kind = 'file_import';
            IF selected_sequence IS NULL THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'Unchanged File acquisition has no checkpoint';
            END IF;
            PERFORM public.context_file_source_append_publish_watermark(
                NEW.organization_id, NEW.source_id, selected_sequence,
                'file_import', 'unchanged', NEW.resource_ref,
                NEW.active_revision_id, NEW.observed_at
            );
            RETURN NULL;
        END;
        $function$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION public.context_file_source_checkpoint_tombstone()
        RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$
        DECLARE next_sequence bigint;
        BEGIN
            PERFORM pg_advisory_xact_lock(hashtextextended(
                'context-engine.file-source-progress:'
                || NEW.organization_id::text || ':' || NEW.source_id::text, 0
            ));
            SELECT COALESCE(max(checkpoint.sequence), 0) + 1
            INTO next_sequence
            FROM public.{_CHECKPOINT} AS checkpoint
            WHERE checkpoint.organization_id = NEW.organization_id
              AND checkpoint.source_id = NEW.source_id;
            IF next_sequence NOT BETWEEN 1 AND {_MAX_BIGINT} THEN
                RAISE EXCEPTION USING ERRCODE = '22003',
                    MESSAGE = 'File acquisition checkpoint sequence exhausted';
            END IF;
            INSERT INTO public.{_CHECKPOINT} (
                organization_id, source_id, sequence, checkpoint_ref,
                change_kind, cleanup_intent_id, resource_ref, revision_id,
                event_ref, event_sequence, accepted_at
            ) VALUES (
                NEW.organization_id, NEW.source_id, next_sequence,
                'facp_' || encode(public.digest(
                    convert_to('context-engine.file-acquisition-checkpoint.v1', 'UTF8')
                    || decode('00', 'hex')
                    || uuid_send(NEW.organization_id)
                    || uuid_send(NEW.source_id) || int8send(next_sequence)
                    || uuid_send(NEW.cleanup_intent_id), 'sha256'
                ), 'hex'),
                'file_tombstone', NEW.cleanup_intent_id,
                NEW.resource_ref, NEW.revision_id,
                NEW.event_ref, NEW.event_sequence, NEW.tombstoned_at
            );
            PERFORM public.context_file_source_append_publish_watermark(
                NEW.organization_id, NEW.source_id, next_sequence,
                'file_tombstone', 'tombstoned', NEW.resource_ref,
                NEW.revision_id, NEW.tombstoned_at
            );
            RETURN NULL;
        END;
        $function$
        """
    )
    trigger_functions = (
        "context_file_source_checkpoint_import_job",
        "context_file_source_publish_active_revision",
        "context_file_source_publish_unchanged_acquisition",
        "context_file_source_checkpoint_tombstone",
    )
    for function_name in trigger_functions:
        op.execute(f"REVOKE ALL ON FUNCTION public.{function_name}() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER file_import_job_source_checkpoint "
        "AFTER INSERT ON file_import_job FOR EACH ROW "
        "EXECUTE FUNCTION public.context_file_source_checkpoint_import_job()"
    )
    op.execute(
        "CREATE TRIGGER revision_publication_event_source_watermark "
        "AFTER INSERT ON revision_publication_event FOR EACH ROW "
        "WHEN (NEW.state = 'active') "
        "EXECUTE FUNCTION public.context_file_source_publish_active_revision()"
    )
    op.execute(
        "CREATE TRIGGER file_acquisition_result_source_watermark "
        "AFTER INSERT ON file_acquisition_result FOR EACH ROW "
        "EXECUTE FUNCTION public.context_file_source_publish_unchanged_acquisition()"
    )
    op.execute(
        "CREATE TRIGGER file_resource_cleanup_intent_source_progress "
        "AFTER INSERT ON file_resource_cleanup_intent FOR EACH ROW "
        "EXECUTE FUNCTION public.context_file_source_checkpoint_tombstone()"
    )
    for function_name in trigger_functions:
        op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
        op.execute(f"ALTER FUNCTION public.{function_name}() OWNER TO {_DEFINER}")
        op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


def downgrade() -> None:
    """Drop only progress derived from retained durable source lineage."""

    op.execute("DROP TRIGGER file_resource_cleanup_intent_source_progress ON file_resource_cleanup_intent")
    op.execute("DROP TRIGGER file_acquisition_result_source_watermark ON file_acquisition_result")
    op.execute("DROP TRIGGER revision_publication_event_source_watermark ON revision_publication_event")
    op.execute("DROP TRIGGER file_import_job_source_checkpoint ON file_import_job")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    for function_name in (
        "context_file_source_checkpoint_tombstone",
        "context_file_source_publish_unchanged_acquisition",
        "context_file_source_publish_active_revision",
        "context_file_source_checkpoint_import_job",
    ):
        op.execute(f"DROP FUNCTION public.{function_name}()")
    op.execute(f"DROP FUNCTION public.{_READ_FUNCTION}{_READ_SIGNATURE}")
    op.execute(
        "DROP FUNCTION public.context_file_source_append_publish_watermark"
        "(uuid, uuid, bigint, text, text, text, uuid, timestamp with time zone)"
    )
    op.execute("RESET ROLE")
    op.drop_table(_WATERMARK)
    op.drop_table(_CHECKPOINT)
    op.drop_constraint(
        "uq_file_resource_cleanup_intent_progress_lineage",
        "file_resource_cleanup_intent",
        type_="unique",
    )
    op.drop_constraint(
        "uq_file_import_job_progress_lineage",
        "file_import_job",
        type_="unique",
    )
