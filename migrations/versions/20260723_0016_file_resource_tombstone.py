"""Tombstone one File Resource before asynchronous cleanup.

Revision ID: 20260723_0016
Revises: 20260723_0015
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260723_0016"
down_revision: str | None = "20260723_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MIGRATOR = "context_engine_migrator"
_CONTROL = "context_engine_control"
_RUNTIME = "context_engine_runtime"
_WORKER = "context_engine_worker"
_DEFINER = "context_engine_access_policy_definer"
_TABLE = "file_resource_cleanup_intent"
_FUNCTION = "context_control_tombstone_file_resource"
_SIGNATURE = "(uuid, uuid, text, text, bigint, uuid)"
_MAX_BIGINT = 9223372036854775807


def upgrade() -> None:
    """Add the authoritative tombstone and cleanup-intent boundary."""

    op.create_table(
        _TABLE,
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cleanup_intent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_ref", sa.Text(), nullable=False),
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_ref", sa.Text(), nullable=False),
        sa.Column("event_sequence", sa.BigInteger(), nullable=False),
        sa.Column("policy_epoch", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("tombstoned_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "cleanup_intent_id",
            name="pk_file_resource_cleanup_intent",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "resource_ref",
            name="uq_file_resource_cleanup_intent_resource",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "event_ref",
            name="uq_file_resource_cleanup_intent_event",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_id"],
            ["context_source.organization_id", "context_source.source_id"],
            name="fk_file_resource_cleanup_intent_source_same_organization",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "resource_ref", "revision_id"],
            [
                "context_revision.organization_id",
                "context_revision.resource_ref",
                "context_revision.revision_id",
            ],
            name="fk_file_resource_cleanup_intent_revision_same_organization",
        ),
        sa.CheckConstraint(
            "resource_ref ~ '^resource:file:[0-9a-f]{64}$'",
            name="ck_file_resource_cleanup_intent_resource_ref",
        ),
        sa.CheckConstraint(
            "event_ref ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'",
            name="ck_file_resource_cleanup_intent_event_ref",
        ),
        sa.CheckConstraint(
            f"event_sequence BETWEEN 1 AND {_MAX_BIGINT}",
            name="ck_file_resource_cleanup_intent_event_sequence",
        ),
        sa.CheckConstraint(
            f"policy_epoch BETWEEN 1 AND {_MAX_BIGINT}",
            name="ck_file_resource_cleanup_intent_policy_epoch",
        ),
        sa.CheckConstraint(
            "state = 'pending'",
            name="ck_file_resource_cleanup_intent_state",
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
        f"FOR SELECT TO {_DEFINER} USING ({tenant})"
    )
    op.execute(
        f"CREATE POLICY {_TABLE}_access_policy_definer_insert ON {_TABLE} "
        f"FOR INSERT TO {_DEFINER} WITH CHECK ({tenant})"
    )
    op.execute(
        f"CREATE TRIGGER {_TABLE}_immutable BEFORE UPDATE OR DELETE ON {_TABLE} "
        "FOR EACH ROW EXECUTE FUNCTION public.context_content_reject_mutation()"
    )
    op.execute(f"GRANT SELECT, INSERT ON TABLE {_TABLE} TO {_DEFINER}")

    op.execute(
        "CREATE POLICY context_resource_access_policy_definer_select "
        "ON context_resource FOR SELECT TO context_engine_access_policy_definer "
        f"USING ({tenant})"
    )
    op.execute(
        "CREATE POLICY context_resource_access_policy_definer_update "
        "ON context_resource FOR UPDATE TO context_engine_access_policy_definer "
        f"USING ({tenant}) WITH CHECK ({tenant})"
    )
    op.execute(f"GRANT SELECT ON TABLE context_resource TO {_DEFINER}")
    op.execute(f"GRANT UPDATE (tombstoned) ON TABLE context_resource TO {_DEFINER}")

    op.execute(
        f"""
        CREATE FUNCTION public.{_FUNCTION}(
            requested_organization_id uuid,
            requested_source_id uuid,
            requested_resource_ref text,
            requested_event_ref text,
            requested_event_sequence bigint,
            requested_cleanup_intent_id uuid
        ) RETURNS TABLE (
            source_id uuid,
            resource_ref text,
            revision_id uuid,
            event_ref text,
            event_sequence bigint,
            policy_epoch bigint,
            cleanup_intent_id uuid,
            tombstoned_at timestamptz
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE
            current_epoch bigint;
            active_revision uuid;
            next_epoch bigint;
            trusted_now timestamptz;
        BEGIN
            IF SESSION_USER <> '{_CONTROL}'
               OR NULLIF(current_setting('app.organization_id', true), '')::uuid
                    IS DISTINCT FROM requested_organization_id
               OR requested_source_id IS NULL
               OR requested_cleanup_intent_id IS NULL
               OR requested_resource_ref IS NULL
               OR requested_event_ref IS NULL
               OR requested_event_sequence IS NULL
               OR requested_resource_ref !~ '^resource:file:[0-9a-f]{{64}}$'
               OR requested_event_ref !~
                    '^[A-Za-z0-9][A-Za-z0-9._-]{{0,127}}$'
               OR requested_event_sequence NOT BETWEEN 1 AND {_MAX_BIGINT}
            THEN RETURN; END IF;

            PERFORM pg_catalog.pg_advisory_xact_lock(
                pg_catalog.hashtextextended(
                    'context-engine.file-publication:'
                    || requested_organization_id::text,
                    0
                )
            );

            SELECT epoch.policy_epoch
            INTO current_epoch
            FROM public.organization_policy_epoch AS epoch
            WHERE epoch.organization_id = requested_organization_id
            FOR UPDATE;
            IF current_epoch IS NULL THEN RETURN; END IF;

            -- Bind an Organization-scoped event before applying the target's
            -- terminal replay rule. A target cannot absorb an event already
            -- committed for a different target.
            RETURN QUERY
            SELECT intent.source_id, intent.resource_ref, intent.revision_id,
                   intent.event_ref, intent.event_sequence,
                   intent.policy_epoch, intent.cleanup_intent_id,
                   intent.tombstoned_at
            FROM public.{_TABLE} AS intent
            WHERE intent.organization_id = requested_organization_id
              AND intent.event_ref = requested_event_ref
              AND intent.source_id = requested_source_id
              AND intent.resource_ref = requested_resource_ref
              AND EXISTS (
                  SELECT 1 FROM public.context_resource AS resource
                  WHERE resource.organization_id = intent.organization_id
                    AND resource.resource_ref = intent.resource_ref
                    AND resource.source_ref = intent.source_id::text
                    AND resource.active_revision_id = intent.revision_id
                    AND resource.tombstoned IS TRUE
              );
            IF FOUND THEN RETURN; END IF;

            IF EXISTS (
                SELECT 1 FROM public.{_TABLE} AS intent
                WHERE intent.organization_id = requested_organization_id
                  AND intent.event_ref = requested_event_ref
            ) THEN RETURN; END IF;

            RETURN QUERY
            SELECT intent.source_id, intent.resource_ref, intent.revision_id,
                   intent.event_ref, intent.event_sequence,
                   intent.policy_epoch, intent.cleanup_intent_id,
                   intent.tombstoned_at
            FROM public.{_TABLE} AS intent
            WHERE intent.organization_id = requested_organization_id
              AND intent.source_id = requested_source_id
              AND intent.resource_ref = requested_resource_ref
              AND EXISTS (
                  SELECT 1 FROM public.context_resource AS resource
                  WHERE resource.organization_id = intent.organization_id
                    AND resource.resource_ref = intent.resource_ref
                    AND resource.source_ref = intent.source_id::text
                    AND resource.active_revision_id = intent.revision_id
                    AND resource.tombstoned IS TRUE
              );
            IF FOUND THEN RETURN; END IF;

            IF current_epoch >= {_MAX_BIGINT} THEN RETURN; END IF;

            SELECT resource.active_revision_id
            INTO active_revision
            FROM public.context_resource AS resource
            WHERE resource.organization_id = requested_organization_id
              AND resource.source_ref = requested_source_id::text
              AND resource.resource_ref = requested_resource_ref
              AND resource.active_revision_id IS NOT NULL
              AND resource.tombstoned IS FALSE
            FOR UPDATE;
            IF active_revision IS NULL THEN RETURN; END IF;

            trusted_now := pg_catalog.statement_timestamp();
            UPDATE public.context_resource AS resource
            SET tombstoned = true
            WHERE resource.organization_id = requested_organization_id
              AND resource.source_ref = requested_source_id::text
              AND resource.resource_ref = requested_resource_ref
              AND resource.active_revision_id = active_revision
              AND resource.tombstoned IS FALSE;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING ERRCODE = '40001',
                    MESSAGE = 'File Resource tombstone was not accepted';
            END IF;

            UPDATE public.organization_policy_epoch AS epoch
            SET policy_epoch = epoch.policy_epoch + 1
            WHERE epoch.organization_id = requested_organization_id
              AND epoch.policy_epoch = current_epoch
            RETURNING epoch.policy_epoch INTO next_epoch;
            IF next_epoch IS NULL THEN
                RAISE EXCEPTION USING ERRCODE = '40001',
                    MESSAGE = 'File Resource tombstone was not accepted';
            END IF;

            INSERT INTO public.{_TABLE} (
                organization_id, cleanup_intent_id, source_id,
                resource_ref, revision_id, event_ref, event_sequence,
                policy_epoch, state, tombstoned_at
            ) VALUES (
                requested_organization_id, requested_cleanup_intent_id,
                requested_source_id, requested_resource_ref, active_revision,
                requested_event_ref, requested_event_sequence, next_epoch,
                'pending', trusted_now
            );

            RETURN QUERY
            SELECT intent.source_id, intent.resource_ref, intent.revision_id,
                   intent.event_ref, intent.event_sequence,
                   intent.policy_epoch, intent.cleanup_intent_id,
                   intent.tombstoned_at
            FROM public.{_TABLE} AS intent
            WHERE intent.organization_id = requested_organization_id
              AND intent.cleanup_intent_id = requested_cleanup_intent_id;
        END;
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_FUNCTION}{_SIGNATURE} FROM PUBLIC")
    for role in (_RUNTIME, _WORKER):
        op.execute(f"REVOKE ALL ON FUNCTION public.{_FUNCTION}{_SIGNATURE} FROM {role}")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_FUNCTION}{_SIGNATURE} TO {_CONTROL}"
    )
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"ALTER FUNCTION public.{_FUNCTION}{_SIGNATURE} OWNER TO {_DEFINER}")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


def downgrade() -> None:
    """Remove only unused tombstone machinery; never restore deleted content."""

    op.execute(
        f"""
        DO $block$
        BEGIN
            IF EXISTS (SELECT 1 FROM public.{_TABLE}) THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'cannot downgrade with File tombstone cleanup intent';
            END IF;
        END;
        $block$
        """
    )
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(f"DROP FUNCTION public.{_FUNCTION}{_SIGNATURE}")
    op.execute("RESET ROLE")
    op.execute(
        "DROP POLICY context_resource_access_policy_definer_update ON context_resource"
    )
    op.execute(
        "DROP POLICY context_resource_access_policy_definer_select ON context_resource"
    )
    op.execute(f"REVOKE UPDATE (tombstoned) ON TABLE context_resource FROM {_DEFINER}")
    op.execute(f"REVOKE SELECT ON TABLE context_resource FROM {_DEFINER}")
    op.drop_table(_TABLE)
