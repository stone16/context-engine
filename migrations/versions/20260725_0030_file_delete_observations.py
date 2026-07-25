"""Detect deleted File paths in durable change pages.

Revision ID: 20260725_0030
Revises: 20260725_0029
Create Date: 2026-07-25
"""

# ruff: noqa: E501

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260725_0030"
down_revision: str | None = "20260725_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONTROL = "context_engine_control"
_DEFINER = "context_engine_worker_lease_definer"
_PAGE = "file_source_change_page"
_CHANGE = "file_source_change"
_BINDING = "file_source_delete_observation_page"
_ACTIVATE = "context_control_activate_file_delete_observations"
_ACTIVATE_SIGNATURE = "(uuid, uuid, uuid)"
_ACCEPT_V3 = "context_control_accept_file_change_page"
_ACCEPT_V3_SIGNATURE = (
    "(uuid, uuid, uuid, text, uuid, smallint, text, text, text, bigint, uuid, "
    "jsonb, boolean)"
)
_ACCEPT_INTERNAL = "context_internal_accept_file_delete_observation_page"
_ACCEPT_V4 = "context_control_accept_file_delete_observation_page"
_ACCEPT_V4_SIGNATURE = (
    "(uuid, uuid, uuid, text, uuid, smallint, text, text, text, bigint, uuid, "
    "jsonb, boolean, jsonb)"
)
_BASELINE = "context_control_read_complete_file_change_baseline"
_BASELINE_SIGNATURE = "(uuid, uuid)"
_PROGRESS = "context_control_read_file_source_progress"
_PROGRESS_SIGNATURE = "(uuid, uuid)"
_CAPABILITY_TRIGGER = "context_file_change_require_capability_binding"
_MAX_BASELINE_ENTRIES = 10_000

_V1 = """{"aclEvidenceMode":"mirrored","authorizeAndProject":"unavailable","batchLimits":"unavailable","checkpoint":"unavailable","checkpointSemantics":"unavailable","consistencyGuarantees":"unavailable","contentKinds":["markdown"],"cursorSemantics":"unavailable","declarationVersion":"file-capabilities-v1","deletion":"unavailable","describeCapabilities":"unavailable","discover":"unavailable","fileSourceAccess":"unavailable","freshness":"unavailable","ingestionJobs":"unavailable","projectionFields":[],"readChanges":"unavailable","resourceKinds":["markdown_document"],"sourceMode":"materialized"}"""
_V2 = """{"aclEvidenceMode":"mirrored","authorizeAndProject":"unavailable","batchLimits":"unavailable","checkpoint":"unavailable","checkpointSemantics":"unavailable","consistencyGuarantees":"unavailable","contentKinds":["markdown"],"cursorSemantics":"unavailable","declarationVersion":"file-capabilities-v2","deletion":"unavailable","describeCapabilities":"unavailable","discover":"unavailable","fileSourceAccess":"available","freshness":"unavailable","ingestionJobs":"available","projectionFields":[],"readChanges":"unavailable","resourceKinds":["markdown_document"],"sourceMode":"materialized"}"""
_V3 = """{"aclEvidenceMode":"mirrored","authorizeAndProject":"unavailable","batchLimits":"available","checkpoint":"available","checkpointSemantics":"available","consistencyGuarantees":"unavailable","contentKinds":["markdown"],"cursorSemantics":"available","declarationVersion":"file-capabilities-v3","deletion":"unavailable","describeCapabilities":"available","discover":"unavailable","fileSourceAccess":"available","freshness":"unavailable","ingestionJobs":"available","projectionFields":[],"readChanges":"available","resourceKinds":["markdown_document"],"sourceMode":"materialized"}"""
_V4 = """{"aclEvidenceMode":"mirrored","authorizeAndProject":"unavailable","batchLimits":"available","checkpoint":"available","checkpointSemantics":"available","consistencyGuarantees":"unavailable","contentKinds":["markdown"],"cursorSemantics":"available","declarationVersion":"file-capabilities-v4","deleteObservations":"available","deletion":"unavailable","describeCapabilities":"available","discover":"unavailable","fileSourceAccess":"available","freshness":"unavailable","ingestionJobs":"available","projectionFields":[],"readChanges":"available","resourceKinds":["markdown_document"],"sourceMode":"materialized"}"""


def _install_capability_constraint(*documents: str) -> None:
    op.drop_constraint(
        "ck_source_version_file_capabilities",
        "source_version",
        type_="check",
    )
    allowed = ", ".join(f"'{document}'::jsonb" for document in documents)
    op.create_check_constraint(
        "ck_source_version_file_capabilities",
        "source_version",
        f"capability_manifest IN ({allowed})",
    )


def _replace_function_text(
    regprocedure: str,
    searched: str,
    replacement: str,
) -> None:
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"""
        DO $block$
        DECLARE
            definition text;
            replacement_definition text;
        BEGIN
            definition := pg_catalog.pg_get_functiondef(
                'public.{regprocedure}'::regprocedure
            );
            replacement_definition := pg_catalog.replace(
                definition, $search${searched}$search$, $replacement${replacement}$replacement$
            );
            IF replacement_definition = definition THEN
                RAISE EXCEPTION 'Function predicate was not recognized: {regprocedure}';
            END IF;
            EXECUTE replacement_definition;
        END;
        $block$
        """
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


def _extend_existing_file_paths(*, include_v4: bool) -> None:
    prior = "IN ('file-capabilities-v1', 'file-capabilities-v2', 'file-capabilities-v3')"
    current = "IN ('file-capabilities-v1', 'file-capabilities-v2', 'file-capabilities-v3', 'file-capabilities-v4')"
    searched, replacement = (prior, current) if include_v4 else (current, prior)
    _replace_function_text(
        "context_control_prepare_file_import_pre_offboarding(uuid,uuid,uuid,uuid,uuid,text,text,uuid,bigint,text,text,uuid)",
        searched,
        replacement,
    )
    schedule_prior = f"version.capability_manifest = '{_V3}'::jsonb"
    schedule_current = (
        "version.capability_manifest IN "
        f"('{_V3}'::jsonb, '{_V4}'::jsonb)"
    )
    searched, replacement = (
        (schedule_prior, schedule_current)
        if include_v4
        else (schedule_current, schedule_prior)
    )
    _replace_function_text(
        "context_control_schedule_file_change_page(uuid,uuid,uuid,text,text,uuid,bigint,uuid)",
        searched,
        replacement,
    )


def upgrade() -> None:
    """Activate v4 observation only; tombstone execution remains separate."""

    _install_capability_constraint(_V1, _V2, _V3, _V4)
    _extend_existing_file_paths(include_v4=True)
    op.create_table(
        _BINDING,
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("source_version_id", sa.Uuid(), nullable=False),
        sa.Column("page_ref", sa.Text(), nullable=False),
        sa.Column("baseline_page_ref", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "source_id",
            "source_version_id",
            "page_ref",
            name="pk_file_source_delete_observation_page",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_id", "source_version_id", "page_ref"],
            [
                f"{_PAGE}.organization_id",
                f"{_PAGE}.source_id",
                f"{_PAGE}.source_version_id",
                f"{_PAGE}.page_ref",
            ],
            name="fk_file_source_delete_observation_page_exact",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            [
                "organization_id",
                "source_id",
                "source_version_id",
                "baseline_page_ref",
            ],
            [
                f"{_PAGE}.organization_id",
                f"{_PAGE}.source_id",
                f"{_PAGE}.source_version_id",
                f"{_PAGE}.page_ref",
            ],
            name="fk_file_source_delete_observation_baseline_exact",
        ),
        sa.CheckConstraint(
            "baseline_page_ref IS NULL OR "
            "(baseline_page_ref ~ '^[0-9a-f]{64}$' AND baseline_page_ref <> page_ref)",
            name="ck_file_source_delete_observation_baseline",
        ),
    )
    op.execute(f"REVOKE ALL ON TABLE {_BINDING} FROM PUBLIC")
    for role in (_CONTROL, "context_engine_runtime", "context_engine_worker"):
        op.execute(f"REVOKE ALL ON TABLE {_BINDING} FROM {role}")
    op.execute(f"ALTER TABLE {_BINDING} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_BINDING} FORCE ROW LEVEL SECURITY")
    tenant = (
        "organization_id = NULLIF("
        "current_setting('app.organization_id', true), ''"
        ")::uuid"
    )
    op.execute(
        f"CREATE POLICY {_BINDING}_migrator_administration ON {_BINDING} "
        "FOR ALL TO context_engine_migrator USING (true) WITH CHECK (true)"
    )
    op.execute(
        f"CREATE POLICY {_BINDING}_definer_select ON {_BINDING} "
        f"FOR SELECT TO {_DEFINER} USING ({tenant})"
    )
    op.execute(
        f"CREATE POLICY {_BINDING}_definer_insert ON {_BINDING} "
        f"FOR INSERT TO {_DEFINER} WITH CHECK ({tenant})"
    )
    op.execute(
        f"CREATE POLICY {_BINDING}_definer_delete ON {_BINDING} "
        f"FOR DELETE TO {_DEFINER} USING ({tenant})"
    )
    op.execute(f"GRANT SELECT, INSERT, DELETE ON TABLE {_BINDING} TO {_DEFINER}")

    op.drop_constraint(
        "ck_file_source_change_kind_ordinal",
        _CHANGE,
        type_="check",
    )
    op.create_check_constraint(
        "ck_file_source_change_kind_ordinal",
        _CHANGE,
        "change_ordinal BETWEEN 1 AND 100 "
        "AND change_kind IN ('upsert', 'delete')",
    )
    _create_capability_trigger()
    _create_activate_function()
    _create_internal_accept_function()
    _create_v4_accept_function()
    _create_baseline_read_function()
    _set_progress_read_volatility(stable=True)


def _create_capability_trigger() -> None:
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"""
        CREATE FUNCTION public.{_CAPABILITY_TRIGGER}()
        RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE
            selected_capabilities jsonb;
            has_v4_binding boolean;
        BEGIN
            IF NULLIF(current_setting('app.organization_id', true), '')::uuid
                 IS DISTINCT FROM NEW.organization_id
            THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'File page tenant context is not trusted';
            END IF;
            SELECT version.capability_manifest
            INTO selected_capabilities
            FROM public.source_version AS version
            WHERE version.organization_id = NEW.organization_id
              AND version.source_id = NEW.source_id
              AND version.version_id = NEW.source_version_id;
            SELECT EXISTS (
                SELECT 1 FROM public.{_BINDING} AS binding
                WHERE binding.organization_id = NEW.organization_id
                  AND binding.source_id = NEW.source_id
                  AND binding.source_version_id = NEW.source_version_id
                  AND binding.page_ref = NEW.page_ref
            ) INTO has_v4_binding;
            IF selected_capabilities = '{_V4}'::jsonb THEN
                IF has_v4_binding IS NOT TRUE THEN
                    RAISE EXCEPTION USING ERRCODE = '55000',
                        MESSAGE = 'v4 File page lacks delete-observation binding';
                END IF;
            ELSIF selected_capabilities = '{_V3}'::jsonb THEN
                IF has_v4_binding IS TRUE THEN
                    RAISE EXCEPTION USING ERRCODE = '55000',
                        MESSAGE = 'v3 File page cannot carry delete observations';
                END IF;
                IF TG_TABLE_NAME = '{_CHANGE}' THEN
                    IF NEW.change_kind <> 'upsert' THEN
                        RAISE EXCEPTION USING ERRCODE = '55000',
                            MESSAGE = 'v3 File page cannot carry delete observations';
                    END IF;
                END IF;
            ELSE
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'File page capability is not active';
            END IF;
            RETURN NEW;
        END;
        $function$
        """
    )
    op.execute("RESET ROLE")
    op.execute(
        f"REVOKE ALL ON FUNCTION public.{_CAPABILITY_TRIGGER}() FROM PUBLIC"
    )
    op.execute(
        f"CREATE TRIGGER {_PAGE}_capability_binding "
        f"BEFORE INSERT ON {_PAGE} FOR EACH ROW "
        f"EXECUTE FUNCTION public.{_CAPABILITY_TRIGGER}()"
    )
    op.execute(
        f"CREATE TRIGGER {_CHANGE}_capability_binding "
        f"BEFORE INSERT ON {_CHANGE} FOR EACH ROW "
        f"EXECUTE FUNCTION public.{_CAPABILITY_TRIGGER}()"
    )
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


def _create_activate_function() -> None:
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"""
        CREATE FUNCTION public.{_ACTIVATE}(
            requested_organization_id uuid,
            requested_source_id uuid,
            requested_activated_version_id uuid
        ) RETURNS TABLE (activated_version_id uuid)
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE
            selected_version_id uuid;
            selected_root_ref text;
            selected_capabilities jsonb;
            trusted_now timestamptz;
        BEGIN
            IF SESSION_USER <> '{_CONTROL}'
               OR requested_organization_id IS NULL
               OR requested_source_id IS NULL
               OR requested_activated_version_id IS NULL
            THEN RETURN; END IF;
            PERFORM pg_catalog.set_config(
                'app.organization_id', requested_organization_id::text, true
            );
            SELECT source.active_version_id, version.root_ref,
                   version.capability_manifest
            INTO selected_version_id, selected_root_ref, selected_capabilities
            FROM public.context_source AS source
            JOIN public.source_version AS version
              ON version.organization_id = source.organization_id
             AND version.source_id = source.source_id
             AND version.version_id = source.active_version_id
            WHERE source.organization_id = requested_organization_id
              AND source.source_id = requested_source_id
              AND source.source_kind = 'file'
              AND source.lifecycle_state = 'active'
            FOR UPDATE OF source;
            IF selected_version_id IS NULL THEN RETURN; END IF;
            IF selected_capabilities = '{_V4}'::jsonb THEN
                activated_version_id := selected_version_id;
                RETURN NEXT;
                RETURN;
            END IF;
            IF selected_capabilities <> '{_V3}'::jsonb THEN RETURN; END IF;
            trusted_now := pg_catalog.statement_timestamp();
            INSERT INTO public.source_version (
                organization_id, source_id, version_id, source_kind,
                root_ref, capability_manifest, created_at
            ) VALUES (
                requested_organization_id, requested_source_id,
                requested_activated_version_id, 'file', selected_root_ref,
                '{_V4}'::jsonb, trusted_now
            );
            UPDATE public.context_source AS source
            SET active_version_id = requested_activated_version_id
            WHERE source.organization_id = requested_organization_id
              AND source.source_id = requested_source_id
              AND source.active_version_id = selected_version_id
              AND source.lifecycle_state = 'active';
            IF NOT FOUND THEN RETURN; END IF;
            activated_version_id := requested_activated_version_id;
            RETURN NEXT;
        END;
        $function$
        """
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE ALL ON FUNCTION public.{_ACTIVATE}{_ACTIVATE_SIGNATURE} FROM PUBLIC")
    op.execute(f"ALTER FUNCTION public.{_ACTIVATE}{_ACTIVATE_SIGNATURE} OWNER TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.{_ACTIVATE}{_ACTIVATE_SIGNATURE} TO {_CONTROL}")
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


def _create_internal_accept_function() -> None:
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"""
        DO $block$
        DECLARE
            definition text;
            replacement_definition text;
        BEGIN
            definition := pg_catalog.pg_get_functiondef(
                'public.{_ACCEPT_V3}{_ACCEPT_V3_SIGNATURE}'::regprocedure
            );
            replacement_definition := pg_catalog.replace(
                definition,
                'FUNCTION public.{_ACCEPT_V3}(',
                'FUNCTION public.{_ACCEPT_INTERNAL}('
            );
            replacement_definition := pg_catalog.replace(
                replacement_definition,
                $search$version.capability_manifest = '{_V3}'::jsonb$search$,
                $replacement$version.capability_manifest = '{_V4}'::jsonb$replacement$
            );
            IF replacement_definition = definition
               OR replacement_definition NOT LIKE '%{_ACCEPT_INTERNAL}%'
               OR replacement_definition NOT LIKE '%file-capabilities-v4%'
            THEN
                RAISE EXCEPTION 'File page accept function was not recognized';
            END IF;
            EXECUTE replacement_definition;
        END;
        $block$
        """
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE ALL ON FUNCTION public.{_ACCEPT_INTERNAL}{_ACCEPT_V3_SIGNATURE} FROM PUBLIC")
    op.execute(f"REVOKE ALL ON FUNCTION public.{_ACCEPT_INTERNAL}{_ACCEPT_V3_SIGNATURE} FROM {_CONTROL}")
    op.execute(f"ALTER FUNCTION public.{_ACCEPT_INTERNAL}{_ACCEPT_V3_SIGNATURE} OWNER TO {_DEFINER}")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


def _create_v4_accept_function() -> None:
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"""
        CREATE FUNCTION public.{_ACCEPT_V4}(
            requested_organization_id uuid,
            requested_source_id uuid,
            requested_source_version_id uuid,
            requested_scan_ref text,
            requested_scan_epoch uuid,
            requested_page_limit smallint,
            requested_page_ref text,
            requested_predecessor_page_ref text,
            requested_predecessor_checkpoint_ref text,
            requested_predecessor_sequence bigint,
            requested_superseded_scan_epoch uuid,
            requested_changes jsonb,
            requested_complete boolean,
            requested_baseline jsonb
        ) RETURNS TABLE (
            source_id uuid, source_version_id uuid, page_ref text,
            checkpoint_ref text, sequence bigint, change_count smallint,
            complete boolean, accepted_at timestamptz,
            superseded_scan_epoch uuid, page_limit smallint
        )
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE
            requested_baseline_page_ref text;
            expected_baseline_page_ref text;
            existing_baseline_page_ref text;
            binding_inserted boolean := false;
            existing_page boolean;
            existing_scan_change_count bigint;
            result record;
        BEGIN
            IF SESSION_USER <> '{_CONTROL}'
               OR requested_organization_id IS NULL
               OR requested_source_id IS NULL
               OR requested_source_version_id IS NULL
               OR pg_catalog.jsonb_typeof(requested_changes) <> 'array'
            THEN RETURN; END IF;
            PERFORM pg_catalog.set_config(
                'app.organization_id', requested_organization_id::text, true
            );
            IF requested_baseline IS NOT NULL THEN
                IF pg_catalog.jsonb_typeof(requested_baseline) <> 'object'
                   OR (SELECT pg_catalog.array_agg(key ORDER BY key)
                       FROM pg_catalog.jsonb_object_keys(requested_baseline) AS item(key))
                      <> ARRAY['checkpointRef','pageRef','scanEpoch','scanRef','sequence','sourceVersionId']::text[]
                   OR requested_baseline->>'checkpointRef' !~ '^facp_[0-9a-f]{{64}}$'
                   OR requested_baseline->>'pageRef' !~ '^[0-9a-f]{{64}}$'
                   OR requested_baseline->>'scanRef' !~ '^[0-9a-f]{{64}}$'
                   OR requested_baseline->>'sequence' !~ '^[1-9][0-9]{{0,18}}$'
                   OR requested_baseline->>'sourceVersionId' <> requested_source_version_id::text
                THEN RETURN; END IF;
                SELECT page.page_ref
                INTO requested_baseline_page_ref
                FROM public.{_PAGE} AS page
                JOIN public.file_source_acquisition_checkpoint AS checkpoint
                  ON checkpoint.organization_id = page.organization_id
                 AND checkpoint.source_id = page.source_id
                 AND checkpoint.source_version_id = page.source_version_id
                 AND checkpoint.change_page_ref = page.page_ref
                 AND checkpoint.change_kind = 'file_change_page'
                JOIN public.{_BINDING} AS binding
                  ON binding.organization_id = page.organization_id
                 AND binding.source_id = page.source_id
                 AND binding.source_version_id = page.source_version_id
                 AND binding.page_ref = page.page_ref
                WHERE page.organization_id = requested_organization_id
                  AND page.source_id = requested_source_id
                  AND page.source_version_id = requested_source_version_id
                  AND page.page_ref = requested_baseline->>'pageRef'
                  AND page.scan_ref = requested_baseline->>'scanRef'
                  AND page.scan_epoch::text = requested_baseline->>'scanEpoch'
                  AND checkpoint.checkpoint_ref = requested_baseline->>'checkpointRef'
                  AND checkpoint.sequence = (requested_baseline->>'sequence')::bigint
                  AND page.complete IS TRUE;
                IF requested_baseline_page_ref IS NULL THEN RETURN; END IF;
            END IF;
            IF EXISTS (
                SELECT 1
                FROM pg_catalog.jsonb_array_elements(requested_changes)
                     AS supplied(element)
                WHERE supplied.element->>'kind' = 'delete'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM public.{_PAGE} AS baseline_page
                      JOIN public.{_CHANGE} AS baseline_change
                        ON baseline_change.organization_id =
                           baseline_page.organization_id
                       AND baseline_change.source_id = baseline_page.source_id
                       AND baseline_change.source_version_id =
                           baseline_page.source_version_id
                       AND baseline_change.page_ref = baseline_page.page_ref
                      WHERE baseline_page.organization_id =
                            requested_organization_id
                        AND baseline_page.source_id = requested_source_id
                        AND baseline_page.source_version_id =
                            requested_source_version_id
                        AND baseline_page.scan_epoch = (
                            SELECT parent.scan_epoch
                            FROM public.{_PAGE} AS parent
                            WHERE parent.organization_id =
                                  requested_organization_id
                              AND parent.source_id = requested_source_id
                              AND parent.source_version_id =
                                  requested_source_version_id
                              AND parent.page_ref = requested_baseline_page_ref
                        )
                        AND baseline_change.change_kind = 'upsert'
                        AND baseline_change.relative_path =
                            supplied.element->>'path'
                        AND baseline_change.content_sha256 =
                            supplied.element->>'contentSha256'
                        AND baseline_change.content_length =
                            (supplied.element->>'contentLength')::bigint
                  )
            ) THEN RETURN; END IF;
            SELECT binding.baseline_page_ref
            INTO existing_baseline_page_ref
            FROM public.{_BINDING} AS binding
            WHERE binding.organization_id = requested_organization_id
              AND binding.source_id = requested_source_id
              AND binding.source_version_id = requested_source_version_id
              AND binding.page_ref = requested_page_ref;
            existing_page := FOUND;
            IF existing_page THEN
                IF existing_baseline_page_ref IS DISTINCT FROM requested_baseline_page_ref
                THEN RETURN; END IF;
            ELSE
                IF requested_predecessor_page_ref IS NULL THEN
                    SELECT page.page_ref
                    INTO expected_baseline_page_ref
                    FROM public.file_source_acquisition_checkpoint AS checkpoint
                    JOIN public.{_PAGE} AS page
                      ON page.organization_id = checkpoint.organization_id
                     AND page.source_id = checkpoint.source_id
                     AND page.source_version_id = checkpoint.source_version_id
                     AND page.page_ref = checkpoint.change_page_ref
                    JOIN public.{_BINDING} AS binding
                      ON binding.organization_id = page.organization_id
                     AND binding.source_id = page.source_id
                     AND binding.source_version_id = page.source_version_id
                     AND binding.page_ref = page.page_ref
                    WHERE checkpoint.organization_id = requested_organization_id
                      AND checkpoint.source_id = requested_source_id
                      AND checkpoint.source_version_id = requested_source_version_id
                      AND checkpoint.change_kind = 'file_change_page'
                      AND page.complete IS TRUE
                    ORDER BY checkpoint.sequence DESC LIMIT 1;
                ELSE
                    SELECT binding.baseline_page_ref
                    INTO expected_baseline_page_ref
                    FROM public.{_PAGE} AS initial
                    JOIN public.{_BINDING} AS binding
                      ON binding.organization_id = initial.organization_id
                     AND binding.source_id = initial.source_id
                     AND binding.source_version_id = initial.source_version_id
                     AND binding.page_ref = initial.page_ref
                    WHERE initial.organization_id = requested_organization_id
                      AND initial.source_id = requested_source_id
                      AND initial.source_version_id = requested_source_version_id
                      AND initial.scan_ref = requested_scan_ref
                      AND initial.scan_epoch = requested_scan_epoch
                      AND initial.page_ordinal = 1;
                    IF NOT FOUND THEN RETURN; END IF;
                END IF;
                IF expected_baseline_page_ref IS DISTINCT FROM requested_baseline_page_ref
                THEN RETURN; END IF;
                IF requested_baseline_page_ref IS NULL AND EXISTS (
                    SELECT 1 FROM pg_catalog.jsonb_array_elements(requested_changes)
                    AS item(element)
                    WHERE item.element->>'kind' = 'delete'
                ) THEN RETURN; END IF;
                SELECT COALESCE(sum(page.change_count), 0)
                INTO existing_scan_change_count
                FROM public.{_PAGE} AS page
                WHERE page.organization_id = requested_organization_id
                  AND page.source_id = requested_source_id
                  AND page.source_version_id = requested_source_version_id
                  AND page.scan_ref = requested_scan_ref
                  AND page.scan_epoch = requested_scan_epoch;
                IF existing_scan_change_count
                   + pg_catalog.jsonb_array_length(requested_changes)
                   > {_MAX_BASELINE_ENTRIES}
                THEN RETURN; END IF;
                INSERT INTO public.{_BINDING} (
                    organization_id, source_id, source_version_id,
                    page_ref, baseline_page_ref
                ) VALUES (
                    requested_organization_id, requested_source_id,
                    requested_source_version_id, requested_page_ref,
                    requested_baseline_page_ref
                );
                binding_inserted := true;
            END IF;
            SELECT * INTO result
            FROM public.{_ACCEPT_INTERNAL}(
                requested_organization_id, requested_source_id,
                requested_source_version_id, requested_scan_ref,
                requested_scan_epoch, requested_page_limit,
                requested_page_ref, requested_predecessor_page_ref,
                requested_predecessor_checkpoint_ref,
                requested_predecessor_sequence,
                requested_superseded_scan_epoch, requested_changes,
                requested_complete
            );
            IF NOT FOUND THEN
                IF binding_inserted THEN
                    DELETE FROM public.{_BINDING} AS binding
                    WHERE binding.organization_id = requested_organization_id
                      AND binding.source_id = requested_source_id
                      AND binding.source_version_id = requested_source_version_id
                      AND binding.page_ref = requested_page_ref;
                END IF;
                RETURN;
            END IF;
            source_id := result.source_id;
            source_version_id := result.source_version_id;
            page_ref := result.page_ref;
            checkpoint_ref := result.checkpoint_ref;
            sequence := result.sequence;
            change_count := result.change_count;
            complete := result.complete;
            accepted_at := result.accepted_at;
            superseded_scan_epoch := result.superseded_scan_epoch;
            page_limit := result.page_limit;
            RETURN NEXT;
        END;
        $function$
        """
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE ALL ON FUNCTION public.{_ACCEPT_V4}{_ACCEPT_V4_SIGNATURE} FROM PUBLIC")
    op.execute(f"ALTER FUNCTION public.{_ACCEPT_V4}{_ACCEPT_V4_SIGNATURE} OWNER TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.{_ACCEPT_V4}{_ACCEPT_V4_SIGNATURE} TO {_CONTROL}")
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


def _create_baseline_read_function() -> None:
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"""
        CREATE FUNCTION public.{_BASELINE}(
            requested_organization_id uuid,
            requested_source_id uuid
        ) RETURNS TABLE (
            baseline_source_version_id uuid,
            baseline_scan_ref text,
            baseline_scan_epoch uuid,
            baseline_page_ref text,
            baseline_checkpoint_ref text,
            baseline_sequence bigint,
            baseline_parent_scan_ref text,
            baseline_parent_scan_epoch uuid,
            baseline_parent_page_ref text,
            baseline_parent_checkpoint_ref text,
            baseline_parent_sequence bigint,
            baseline_entry_kind text,
            baseline_entry_path text,
            baseline_entry_content_sha256 text,
            baseline_entry_content_length bigint
        )
        LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        BEGIN
            IF SESSION_USER <> '{_CONTROL}'
               OR NULLIF(current_setting('app.organization_id', true), '')::uuid
                    IS DISTINCT FROM requested_organization_id
            THEN RETURN; END IF;
            RETURN QUERY
            WITH active AS (
                SELECT source.active_version_id
                FROM public.context_source AS source
                JOIN public.source_version AS version
                  ON version.organization_id = source.organization_id
                 AND version.source_id = source.source_id
                 AND version.version_id = source.active_version_id
                WHERE source.organization_id = requested_organization_id
                  AND source.source_id = requested_source_id
                  AND source.lifecycle_state = 'active'
                  AND version.capability_manifest = '{_V4}'::jsonb
            ), terminal AS (
                SELECT page.*, checkpoint.checkpoint_ref, checkpoint.sequence,
                       binding.baseline_page_ref
                FROM active
                JOIN public.file_source_acquisition_checkpoint AS checkpoint
                  ON checkpoint.organization_id = requested_organization_id
                 AND checkpoint.source_id = requested_source_id
                 AND checkpoint.source_version_id = active.active_version_id
                 AND checkpoint.change_kind = 'file_change_page'
                JOIN public.{_PAGE} AS page
                  ON page.organization_id = checkpoint.organization_id
                 AND page.source_id = checkpoint.source_id
                 AND page.source_version_id = checkpoint.source_version_id
                 AND page.page_ref = checkpoint.change_page_ref
                 AND page.complete IS TRUE
                JOIN public.{_BINDING} AS binding
                  ON binding.organization_id = page.organization_id
                 AND binding.source_id = page.source_id
                 AND binding.source_version_id = page.source_version_id
                 AND binding.page_ref = page.page_ref
                ORDER BY checkpoint.sequence DESC LIMIT 1
            ), parent AS (
                SELECT page.scan_ref, page.scan_epoch, page.page_ref,
                       checkpoint.checkpoint_ref, checkpoint.sequence
                FROM terminal
                JOIN public.{_PAGE} AS page
                  ON page.organization_id = terminal.organization_id
                 AND page.source_id = terminal.source_id
                 AND page.source_version_id = terminal.source_version_id
                 AND page.page_ref = terminal.baseline_page_ref
                JOIN public.file_source_acquisition_checkpoint AS checkpoint
                  ON checkpoint.organization_id = page.organization_id
                 AND checkpoint.source_id = page.source_id
                 AND checkpoint.source_version_id = page.source_version_id
                 AND checkpoint.change_page_ref = page.page_ref
                 AND checkpoint.change_kind = 'file_change_page'
            )
            SELECT terminal.source_version_id, terminal.scan_ref,
                   terminal.scan_epoch, terminal.page_ref,
                   terminal.checkpoint_ref, terminal.sequence,
                   parent.scan_ref, parent.scan_epoch, parent.page_ref,
                   parent.checkpoint_ref, parent.sequence,
                   change.change_kind, change.relative_path,
                   change.content_sha256, change.content_length
            FROM terminal
            LEFT JOIN parent ON true
            LEFT JOIN public.{_PAGE} AS scan_page
              ON scan_page.organization_id = terminal.organization_id
             AND scan_page.source_id = terminal.source_id
             AND scan_page.source_version_id = terminal.source_version_id
             AND scan_page.scan_epoch = terminal.scan_epoch
            LEFT JOIN public.{_CHANGE} AS change
              ON change.organization_id = scan_page.organization_id
             AND change.source_id = scan_page.source_id
             AND change.source_version_id = scan_page.source_version_id
             AND change.page_ref = scan_page.page_ref
            ORDER BY scan_page.page_ordinal, change.change_ordinal;
        END;
        $function$
        """
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE ALL ON FUNCTION public.{_BASELINE}{_BASELINE_SIGNATURE} FROM PUBLIC")
    op.execute(f"ALTER FUNCTION public.{_BASELINE}{_BASELINE_SIGNATURE} OWNER TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.{_BASELINE}{_BASELINE_SIGNATURE} TO {_CONTROL}")
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


def _set_progress_read_volatility(*, stable: bool) -> None:
    volatility = "STABLE" if stable else "VOLATILE"
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"ALTER FUNCTION public.{_PROGRESS}{_PROGRESS_SIGNATURE} {volatility}"
    )
    op.execute("RESET ROLE")


def downgrade() -> None:
    """Remove v4 only when no accepted v4 page or v4 work lineage exists."""

    # Preserve the predecessor's file_acquisition serialization seam when a
    # caller downgrades through both revisions in one transaction.
    op.execute("LOCK TABLE file_acquisition IN ACCESS EXCLUSIVE MODE")
    op.execute(
        "LOCK TABLE context_source, source_version, "
        "file_source_delete_observation_page, file_source_cleanup_intent "
        "IN ACCESS EXCLUSIVE MODE"
    )
    blocker = (
        op.get_bind()
        .execute(
            sa.text(
                f"""
                SELECT CASE
                    WHEN EXISTS (SELECT 1 FROM {_BINDING})
                    THEN 'accepted v4 page'
                    WHEN EXISTS (
                        SELECT 1 FROM source_version AS version
                        JOIN file_acquisition AS acquisition
                          ON acquisition.organization_id = version.organization_id
                         AND acquisition.source_id = version.source_id
                         AND acquisition.source_version_id = version.version_id
                        WHERE version.capability_manifest = '{_V4}'::jsonb
                    ) THEN 'v4 File acquisition lineage'
                    WHEN EXISTS (
                        SELECT 1 FROM source_version AS version
                        JOIN file_source_cleanup_intent AS intent
                          ON intent.organization_id = version.organization_id
                         AND intent.source_id = version.source_id
                         AND intent.source_version_id = version.version_id
                        WHERE version.capability_manifest = '{_V4}'::jsonb
                    ) THEN 'v4 File cleanup lineage'
                    WHEN EXISTS (
                        SELECT 1 FROM source_version AS version
                        JOIN action_ticket AS ticket
                          ON ticket.organization_id = version.organization_id
                         AND ticket.source_id = version.source_id
                         AND ticket.source_version_id = version.version_id
                        WHERE version.capability_manifest = '{_V4}'::jsonb
                    ) THEN 'v4 ActionTicket lineage'
                END
                """
            )
        )
        .scalar_one()
    )
    if blocker is not None:
        raise RuntimeError(
            f"File delete observation downgrade requires no {blocker}; use a forward fix"
        )
    op.execute(
        f"""
        UPDATE context_source AS source
        SET active_version_id = predecessor.version_id
        FROM source_version AS current
        JOIN LATERAL (
            SELECT candidate.version_id
            FROM source_version AS candidate
            WHERE candidate.organization_id = current.organization_id
              AND candidate.source_id = current.source_id
              AND candidate.root_ref = current.root_ref
              AND candidate.capability_manifest = '{_V3}'::jsonb
              AND candidate.created_at <= current.created_at
            ORDER BY candidate.created_at DESC, candidate.version_id DESC
            LIMIT 1
        ) AS predecessor ON true
        WHERE source.organization_id = current.organization_id
          AND source.source_id = current.source_id
          AND source.active_version_id = current.version_id
          AND current.capability_manifest = '{_V4}'::jsonb
        """
    )
    active_v4 = op.get_bind().execute(
        sa.text(
            f"""
            SELECT EXISTS (
                SELECT 1 FROM context_source AS source
                JOIN source_version AS version
                  ON version.organization_id = source.organization_id
                 AND version.source_id = source.source_id
                 AND version.version_id = source.active_version_id
                WHERE version.capability_manifest = '{_V4}'::jsonb
            )
            """
        )
    ).scalar_one()
    if active_v4:
        raise RuntimeError(
            "File delete observation downgrade could not restore every v3 predecessor"
        )
    # SourceVersion is immutable during normal operation.  The migrator is the
    # sole authority allowed to remove the now-unrepresentable v4 contract
    # after all accepted v4 lineage has been vetoed and active sources have
    # been restored to their exact v3 predecessors.  PostgreSQL transactional
    # DDL restores the trigger state if any later downgrade statement fails.
    op.execute("ALTER TABLE source_version DISABLE TRIGGER source_version_immutable")
    op.execute(
        f"DELETE FROM source_version WHERE capability_manifest = '{_V4}'::jsonb"
    )
    # The delete can queue initially-deferred FK checks.  Drain them before
    # changing trigger state again so rollback behavior is independent of how
    # much v4 SourceVersion history the current database contains.
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    op.execute("ALTER TABLE source_version ENABLE TRIGGER source_version_immutable")
    _extend_existing_file_paths(include_v4=False)
    op.execute(f"DROP TRIGGER {_CHANGE}_capability_binding ON {_CHANGE}")
    op.execute(f"DROP TRIGGER {_PAGE}_capability_binding ON {_PAGE}")
    _set_progress_read_volatility(stable=False)
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(f"DROP FUNCTION public.{_BASELINE}{_BASELINE_SIGNATURE}")
    op.execute(f"DROP FUNCTION public.{_ACCEPT_V4}{_ACCEPT_V4_SIGNATURE}")
    op.execute(f"DROP FUNCTION public.{_ACCEPT_INTERNAL}{_ACCEPT_V3_SIGNATURE}")
    op.execute(f"DROP FUNCTION public.{_ACTIVATE}{_ACTIVATE_SIGNATURE}")
    op.execute(f"DROP FUNCTION public.{_CAPABILITY_TRIGGER}()")
    op.execute("RESET ROLE")
    op.drop_constraint(
        "ck_file_source_change_kind_ordinal",
        _CHANGE,
        type_="check",
    )
    op.create_check_constraint(
        "ck_file_source_change_kind_ordinal",
        _CHANGE,
        "change_ordinal BETWEEN 1 AND 100 AND change_kind = 'upsert'",
    )
    op.drop_table(_BINDING)
    _install_capability_constraint(_V1, _V2, _V3)
