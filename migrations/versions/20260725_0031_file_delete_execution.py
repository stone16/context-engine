"""Execute current File delete observations through tombstone authority.

Revision ID: 20260725_0031
Revises: 20260725_0030
Create Date: 2026-07-25
"""

# ruff: noqa: E501

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260725_0031"
down_revision: str | None = "20260725_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MIGRATOR = "context_engine_migrator"
_CONTROL = "context_engine_control"
_RUNTIME = "context_engine_runtime"
_WORKER = "context_engine_worker"
_DEFINER = "context_engine_worker_lease_definer"
_TOMBSTONE_DEFINER = "context_engine_access_policy_definer"
_TABLE = "file_delete_observation_execution"
_FUNCTION = "context_control_execute_file_delete_observation"
_SIGNATURE = "(uuid, uuid, uuid, text, smallint, uuid)"
_TOMBSTONE = "context_control_tombstone_file_resource"
_TOMBSTONE_SIGNATURE = "(uuid, uuid, text, text, bigint, uuid)"
_MAX_BIGINT = 9223372036854775807
_V4 = """{"aclEvidenceMode":"mirrored","authorizeAndProject":"unavailable","batchLimits":"available","checkpoint":"available","checkpointSemantics":"available","consistencyGuarantees":"unavailable","contentKinds":["markdown"],"cursorSemantics":"available","declarationVersion":"file-capabilities-v4","deleteObservations":"available","deletion":"unavailable","describeCapabilities":"available","discover":"unavailable","fileSourceAccess":"available","freshness":"unavailable","ingestionJobs":"available","projectionFields":[],"readChanges":"available","resourceKinds":["markdown_document"],"sourceMode":"materialized"}"""


def upgrade() -> None:
    """Bind one current accepted delete to the sole File tombstone effect."""

    op.create_table(
        _TABLE,
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_ref", sa.Text(), nullable=False),
        sa.Column("change_ordinal", sa.SmallInteger(), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("content_length", sa.BigInteger(), nullable=False),
        sa.Column("resource_ref", sa.Text(), nullable=False),
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_ref", sa.Text(), nullable=False),
        sa.Column("event_sequence", sa.BigInteger(), nullable=False),
        sa.Column("policy_epoch", sa.BigInteger(), nullable=False),
        sa.Column("cleanup_intent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tombstoned_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "source_id",
            "source_version_id",
            "page_ref",
            "change_ordinal",
            name="pk_file_delete_observation_execution",
        ),
        sa.ForeignKeyConstraint(
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
            [
                "file_source_change.organization_id",
                "file_source_change.source_id",
                "file_source_change.source_version_id",
                "file_source_change.page_ref",
                "file_source_change.change_ordinal",
                "file_source_change.relative_path",
                "file_source_change.content_sha256",
                "file_source_change.content_length",
            ],
            name="fk_file_delete_observation_execution_exact_change",
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
            name="fk_file_delete_observation_execution_exact_cleanup",
        ),
        sa.CheckConstraint(
            "page_ref ~ '^[0-9a-f]{64}$' AND change_ordinal BETWEEN 1 AND 100",
            name="ck_file_delete_observation_execution_locator",
        ),
        sa.CheckConstraint(
            "relative_path ~ '^[^/\\\\]*\\.[mM][dD]$' "
            "AND relative_path NOT IN ('.', '..') "
            "AND content_sha256 ~ '^[0-9a-f]{64}$' AND content_length >= 0",
            name="ck_file_delete_observation_execution_observation",
        ),
        sa.CheckConstraint(
            "resource_ref ~ '^resource:file:[0-9a-f]{64}$' "
            "AND event_ref ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' "
            f"AND event_sequence BETWEEN 1 AND {_MAX_BIGINT} "
            f"AND policy_epoch BETWEEN 1 AND {_MAX_BIGINT}",
            name="ck_file_delete_observation_execution_effect",
        ),
    )
    op.execute(f"REVOKE ALL ON TABLE {_TABLE} FROM PUBLIC")
    for role in (_CONTROL, _RUNTIME, _WORKER):
        op.execute(f"REVOKE ALL ON TABLE {_TABLE} FROM {role}")
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    tenant = (
        "organization_id = NULLIF("
        "current_setting('app.organization_id', true), ''"
        ")::uuid"
    )
    op.execute(
        f"CREATE POLICY {_TABLE}_migrator_administration ON {_TABLE} "
        f"FOR ALL TO {_MIGRATOR} USING (true) WITH CHECK (true)"
    )
    op.execute(
        f"CREATE POLICY {_TABLE}_definer_select ON {_TABLE} "
        f"FOR SELECT TO {_DEFINER} USING ({tenant})"
    )
    op.execute(
        f"CREATE POLICY {_TABLE}_definer_insert ON {_TABLE} "
        f"FOR INSERT TO {_DEFINER} WITH CHECK ({tenant})"
    )
    op.execute(f"GRANT SELECT, INSERT ON TABLE {_TABLE} TO {_DEFINER}")
    op.execute(
        f"CREATE TRIGGER {_TABLE}_immutable BEFORE UPDATE OR DELETE ON {_TABLE} "
        "FOR EACH ROW EXECUTE FUNCTION public.context_content_reject_mutation()"
    )

    # The wrapper may invoke, but cannot replace, the established #28 effect.
    op.execute(f"SET LOCAL ROLE {_TOMBSTONE_DEFINER}")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_TOMBSTONE}{_TOMBSTONE_SIGNATURE} "
        f"TO {_DEFINER}"
    )
    op.execute("RESET ROLE")
    op.execute(
        f"""
        CREATE FUNCTION public.{_FUNCTION}(
            requested_organization_id uuid,
            requested_source_id uuid,
            requested_source_version_id uuid,
            requested_page_ref text,
            requested_change_ordinal smallint,
            requested_cleanup_intent_id uuid
        ) RETURNS TABLE (
            source_id uuid,
            source_version_id uuid,
            page_ref text,
            change_ordinal smallint,
            resource_ref text,
            revision_id uuid,
            event_ref text,
            event_sequence bigint,
            policy_epoch bigint,
            cleanup_intent_id uuid,
            tombstoned_at timestamptz
        )
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE
            selected_path text;
            selected_content_sha256 text;
            selected_content_length bigint;
            selected_checkpoint_sequence bigint;
            selected_resource_ref text;
            selected_event_ref text;
            effect record;
        BEGIN
            IF SESSION_USER <> '{_CONTROL}'
               OR NULLIF(current_setting('app.organization_id', true), '')::uuid
                    IS DISTINCT FROM requested_organization_id
               OR requested_source_id IS NULL
               OR requested_source_version_id IS NULL
               OR requested_page_ref !~ '^[0-9a-f]{{64}}$'
               OR requested_change_ordinal NOT BETWEEN 1 AND 100
               OR requested_cleanup_intent_id IS NULL
            THEN RETURN; END IF;

            -- An immutable exact binding is the sole replay authority. Replay
            -- remains stable after a later scan or terminal source disablement.
            RETURN QUERY
            SELECT execution.source_id, execution.source_version_id,
                   execution.page_ref, execution.change_ordinal,
                   execution.resource_ref, execution.revision_id,
                   execution.event_ref, execution.event_sequence,
                   execution.policy_epoch, execution.cleanup_intent_id,
                   execution.tombstoned_at
            FROM public.{_TABLE} AS execution
            WHERE execution.organization_id = requested_organization_id
              AND execution.source_id = requested_source_id
              AND execution.source_version_id = requested_source_version_id
              AND execution.page_ref = requested_page_ref
              AND execution.change_ordinal = requested_change_ordinal;
            IF FOUND THEN RETURN; END IF;

            -- Manual tombstones already take publication before their cleanup
            -- trigger takes progress. Any operation needing both must match it.
            PERFORM pg_catalog.pg_advisory_xact_lock(
                pg_catalog.hashtextextended(
                    'context-engine.file-publication:'
                    || requested_organization_id::text,
                    0
                )
            );
            PERFORM pg_catalog.pg_advisory_xact_lock(
                pg_catalog.hashtextextended(
                    'context-engine.file-source-progress:'
                    || requested_organization_id::text || ':'
                    || requested_source_id::text,
                    0
                )
            );

            -- A concurrent exact caller may have committed while this caller
            -- waited at either fence.
            RETURN QUERY
            SELECT execution.source_id, execution.source_version_id,
                   execution.page_ref, execution.change_ordinal,
                   execution.resource_ref, execution.revision_id,
                   execution.event_ref, execution.event_sequence,
                   execution.policy_epoch, execution.cleanup_intent_id,
                   execution.tombstoned_at
            FROM public.{_TABLE} AS execution
            WHERE execution.organization_id = requested_organization_id
              AND execution.source_id = requested_source_id
              AND execution.source_version_id = requested_source_version_id
              AND execution.page_ref = requested_page_ref
              AND execution.change_ordinal = requested_change_ordinal;
            IF FOUND THEN RETURN; END IF;

            -- The selected delete may be on any page, but its scan terminal
            -- must remain both complete and the latest durable File scan head.
            SELECT selected_change.relative_path,
                   selected_change.content_sha256,
                   selected_change.content_length,
                   selected_checkpoint.sequence
            INTO selected_path,
                 selected_content_sha256,
                 selected_content_length,
                 selected_checkpoint_sequence
            FROM public.context_source AS source
            JOIN public.source_version AS version
              ON version.organization_id = source.organization_id
             AND version.source_id = source.source_id
             AND version.version_id = source.active_version_id
            JOIN public.file_source_change AS selected_change
              ON selected_change.organization_id = source.organization_id
             AND selected_change.source_id = source.source_id
             AND selected_change.source_version_id = version.version_id
             AND selected_change.page_ref = requested_page_ref
             AND selected_change.change_ordinal = requested_change_ordinal
             AND selected_change.change_kind = 'delete'
            JOIN public.file_source_change_page AS selected_page
              ON selected_page.organization_id = selected_change.organization_id
             AND selected_page.source_id = selected_change.source_id
             AND selected_page.source_version_id = selected_change.source_version_id
             AND selected_page.page_ref = selected_change.page_ref
            JOIN public.file_source_delete_observation_page AS selected_binding
              ON selected_binding.organization_id = selected_page.organization_id
             AND selected_binding.source_id = selected_page.source_id
             AND selected_binding.source_version_id = selected_page.source_version_id
             AND selected_binding.page_ref = selected_page.page_ref
            JOIN public.file_source_acquisition_checkpoint AS selected_checkpoint
              ON selected_checkpoint.organization_id = selected_page.organization_id
             AND selected_checkpoint.source_id = selected_page.source_id
             AND selected_checkpoint.source_version_id = selected_page.source_version_id
             AND selected_checkpoint.change_page_ref = selected_page.page_ref
             AND selected_checkpoint.change_kind = 'file_change_page'
            JOIN LATERAL (
                SELECT head_page.*
                FROM public.file_source_acquisition_checkpoint AS head_checkpoint
                JOIN public.file_source_change_page AS head_page
                  ON head_page.organization_id = head_checkpoint.organization_id
                 AND head_page.source_id = head_checkpoint.source_id
                 AND head_page.source_version_id = head_checkpoint.source_version_id
                 AND head_page.page_ref = head_checkpoint.change_page_ref
                WHERE head_checkpoint.organization_id = source.organization_id
                  AND head_checkpoint.source_id = source.source_id
                  AND head_checkpoint.change_kind = 'file_change_page'
                ORDER BY head_checkpoint.sequence DESC
                LIMIT 1
            ) AS current_head ON TRUE
            JOIN public.file_source_delete_observation_page AS head_binding
              ON head_binding.organization_id = current_head.organization_id
             AND head_binding.source_id = current_head.source_id
             AND head_binding.source_version_id = current_head.source_version_id
             AND head_binding.page_ref = current_head.page_ref
            JOIN public.file_source_change_page AS baseline_page
              ON baseline_page.organization_id = selected_binding.organization_id
             AND baseline_page.source_id = selected_binding.source_id
             AND baseline_page.source_version_id = selected_binding.source_version_id
             AND baseline_page.page_ref = selected_binding.baseline_page_ref
             AND baseline_page.complete IS TRUE
            WHERE source.organization_id = requested_organization_id
              AND source.source_id = requested_source_id
              AND source.active_version_id = requested_source_version_id
              AND source.lifecycle_state = 'active'
              AND version.capability_manifest = '{_V4}'::jsonb
              AND current_head.source_version_id = requested_source_version_id
              AND current_head.complete IS TRUE
              AND selected_page.scan_epoch = current_head.scan_epoch
              AND selected_binding.baseline_page_ref IS NOT NULL
              AND head_binding.baseline_page_ref =
                  selected_binding.baseline_page_ref
            FOR UPDATE OF source;
            IF selected_path IS NULL OR selected_checkpoint_sequence IS NULL
            THEN RETURN; END IF;

            selected_resource_ref := 'resource:file:' || pg_catalog.encode(
                public.digest(
                    pg_catalog.convert_to(
                        'context-engine.file-resource.v1', 'UTF8'
                    )
                    || pg_catalog.decode('00', 'hex')
                    || pg_catalog.uuid_send(requested_source_id)
                    || pg_catalog.convert_to(selected_path, 'UTF8'),
                    'sha256'
                ),
                'hex'
            );
            selected_event_ref := 'fdo_' || pg_catalog.encode(
                public.digest(
                    pg_catalog.convert_to(
                        'context-engine.file-delete-observation-event.v1',
                        'UTF8'
                    )
                    || pg_catalog.decode('00', 'hex')
                    || pg_catalog.uuid_send(requested_organization_id)
                    || pg_catalog.uuid_send(requested_source_id)
                    || pg_catalog.uuid_send(requested_source_version_id)
                    || pg_catalog.decode(requested_page_ref, 'hex')
                    || pg_catalog.int2send(requested_change_ordinal),
                    'sha256'
                ),
                'hex'
            );

            BEGIN
                SELECT * INTO effect
                FROM public.{_TOMBSTONE}(
                    requested_organization_id,
                    requested_source_id,
                    selected_resource_ref,
                    selected_event_ref,
                    selected_checkpoint_sequence,
                    requested_cleanup_intent_id
                );
                IF NOT FOUND THEN RETURN; END IF;
                IF effect.source_id <> requested_source_id
                   OR effect.resource_ref <> selected_resource_ref
                   OR effect.event_ref <> selected_event_ref
                   OR effect.event_sequence <> selected_checkpoint_sequence
                   OR effect.cleanup_intent_id <> requested_cleanup_intent_id
                THEN
                    -- Abort this subtransaction so a mismatched nested effect
                    -- can never survive even when a direct caller commits.
                    RAISE EXCEPTION USING ERRCODE = 'CE001',
                        MESSAGE = 'File delete execution effect mismatch';
                END IF;
            EXCEPTION WHEN SQLSTATE 'CE001' THEN
                -- A prior independent tombstone remains externally unavailable.
                -- Entering this handler has already rolled back the nested call.
                RETURN;
            END;

            INSERT INTO public.{_TABLE} (
                organization_id, source_id, source_version_id,
                page_ref, change_ordinal, relative_path,
                content_sha256, content_length, resource_ref, revision_id,
                event_ref, event_sequence, policy_epoch,
                cleanup_intent_id, tombstoned_at
            ) VALUES (
                requested_organization_id, requested_source_id,
                requested_source_version_id, requested_page_ref,
                requested_change_ordinal, selected_path,
                selected_content_sha256, selected_content_length,
                effect.resource_ref, effect.revision_id, effect.event_ref,
                effect.event_sequence, effect.policy_epoch,
                effect.cleanup_intent_id, effect.tombstoned_at
            );

            RETURN QUERY
            SELECT execution.source_id, execution.source_version_id,
                   execution.page_ref, execution.change_ordinal,
                   execution.resource_ref, execution.revision_id,
                   execution.event_ref, execution.event_sequence,
                   execution.policy_epoch, execution.cleanup_intent_id,
                   execution.tombstoned_at
            FROM public.{_TABLE} AS execution
            WHERE execution.organization_id = requested_organization_id
              AND execution.source_id = requested_source_id
              AND execution.source_version_id = requested_source_version_id
              AND execution.page_ref = requested_page_ref
              AND execution.change_ordinal = requested_change_ordinal;
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
    """Remove only unused execution machinery; never undo a tombstone."""

    op.execute(f"LOCK TABLE public.{_TABLE} IN ACCESS EXCLUSIVE MODE")
    op.execute(
        f"""
        DO $block$
        BEGIN
            IF EXISTS (SELECT 1 FROM public.{_TABLE}) THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'cannot downgrade with File delete execution';
            END IF;
        END;
        $block$
        """
    )
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(f"DROP FUNCTION public.{_FUNCTION}{_SIGNATURE}")
    op.execute("RESET ROLE")
    op.execute(f"SET LOCAL ROLE {_TOMBSTONE_DEFINER}")
    op.execute(
        f"REVOKE EXECUTE ON FUNCTION public.{_TOMBSTONE}{_TOMBSTONE_SIGNATURE} "
        f"FROM {_DEFINER}"
    )
    op.execute("RESET ROLE")
    op.drop_table(_TABLE)
