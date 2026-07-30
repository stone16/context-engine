"""Add proof-bound Minimal UI projections and evidence capture.

Revision ID: 20260730_0044
Revises: 20260730_0043
Create Date: 2026-07-30
"""

# ruff: noqa: E501

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260730_0044"
down_revision: str | None = "20260730_0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MIGRATOR = "context_engine_migrator"
_CONTROL = "context_engine_control"
_RUNTIME = "context_engine_runtime"
_WORKER_DEFINER = "context_engine_worker_lease_definer"
_ACCESS_DEFINER = "context_engine_access_policy_definer"
_RUN_READER_DEFINER = "context_engine_context_run_reader_definer"
_MAX = (1 << 63) - 1
_EXACT_IMPORT = "context_control_prepare_exact_file_import"
_EXACT_IMPORT_SIGNATURE = (
    "(uuid,uuid,uuid,uuid,uuid,text,text,uuid,uuid,bigint,text,text,uuid,"
    "text,bigint,text,text,text)"
)
_READ_ARTICLE = "context_control_read_article_policy"
_READ_ARTICLE_SIGNATURE = "(uuid,text)"
_CHANGE_ARTICLE = "context_control_change_article_policy"
_CHANGE_ARTICLE_SIGNATURE = (
    "(uuid,text,bigint,bigint,text,text[],text,uuid,uuid,bigint)"
)
_CAPTURE_FEEDBACK = "context_runtime_capture_context_feedback"
_CAPTURE_FEEDBACK_SIGNATURE = "(uuid,text,text,uuid,uuid,bigint,text,text,text)"
_REDEEM = "context_worker_redeem_file_import"
_REDEEM_SIGNATURE = (
    "(uuid,uuid,uuid,text,bigint,bigint,bytea,timestamp with time zone,"
    "timestamp with time zone)"
)
_FILE_OPERATION_FENCES = (
    "context-engine.file-change-scheduling-migration-fence",
    "context-engine.file-dispatch-migration-fence",
    "context-engine.file-status-migration-fence",
)


def _join_file_operation_fences() -> None:
    connection = op.get_bind()
    for migration_fence in _FILE_OPERATION_FENCES:
        connection.execute(
            sa.text(
                "SELECT pg_catalog.pg_advisory_xact_lock("
                "pg_catalog.hashtextextended(:migration_fence, 0))"
            ),
            {"migration_fence": migration_fence},
        )


def _replace_redeem_ui_fields(*, install: bool) -> None:
    connection = op.get_bind()
    definition = connection.execute(
        sa.text(
            "SELECT pg_catalog.pg_get_functiondef(CAST(:procedure AS regprocedure))"
        ),
        {"procedure": f"public.{_REDEEM}{_REDEEM_SIGNATURE}"},
    ).scalar_one()
    if not isinstance(definition, str):
        raise RuntimeError("File redemption definition is unavailable")
    base_return = "expected_content_length bigint)"
    ui_return = (
        "expected_content_length bigint, ui_preview_digest text, "
        "expected_fragment_digest text, compiler_config_version text)"
    )
    base_select = (
        "acquisition.expected_content_sha256, acquisition.expected_content_length"
    )
    ui_select = (
        base_select + ", acquisition.ui_preview_digest, "
        "acquisition.expected_fragment_digest, "
        "acquisition.compiler_config_version"
    )
    searched_return, replacement_return = (
        (base_return, ui_return) if install else (ui_return, base_return)
    )
    searched_select, replacement_select = (
        (base_select, ui_select) if install else (ui_select, base_select)
    )
    if definition.count(searched_return) != 1 or definition.count(searched_select) != 1:
        raise RuntimeError("File redemption shape was not recognized")
    replacement = definition.replace(searched_return, replacement_return).replace(
        searched_select, replacement_select
    )
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_WORKER_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_WORKER_DEFINER}")
    op.execute(f"DROP FUNCTION public.{_REDEEM}{_REDEEM_SIGNATURE}")
    op.execute(replacement)
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_REDEEM}{_REDEEM_SIGNATURE} "
        "TO context_engine_worker"
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_WORKER_DEFINER}")


def _create_exact_import() -> None:
    op.execute(
        f"""
        CREATE FUNCTION public.{_EXACT_IMPORT}(
            requested_organization_id uuid, requested_acquisition_id uuid,
            requested_job_id uuid, requested_activated_version_id uuid,
            requested_source_id uuid, requested_relative_path text,
            requested_audience_principal_ref text,
            requested_audience_user_id uuid,
            requested_audience_membership_id uuid,
            requested_audience_membership_version bigint,
            requested_idempotency_key text, requested_request_digest text,
            requested_service_principal_id uuid,
            requested_expected_content_sha256 text,
            requested_expected_content_length bigint,
            requested_expected_fragment_digest text,
            requested_compiler_config_version text,
            requested_preview_digest text
        ) RETURNS TABLE (job_id uuid, service_principal_id uuid)
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE selected_version_id uuid; selected_acquisition_id uuid;
                selected_membership_id uuid;
                selected_root_ref text; selected_capabilities jsonb;
                trusted_now timestamptz;
        BEGIN
            IF SESSION_USER <> '{_CONTROL}'
               OR NULLIF(current_setting('app.organization_id', true), '')::uuid
                    IS DISTINCT FROM requested_organization_id
               OR requested_expected_content_sha256 !~ '^[0-9a-f]{{64}}$'
               OR requested_expected_fragment_digest !~ '^[0-9a-f]{{64}}$'
               OR requested_preview_digest !~ '^[0-9a-f]{{64}}$'
               OR requested_expected_content_length < 0
               OR requested_compiler_config_version <> 'markdown-config-v1'
            THEN RETURN; END IF;
            trusted_now := pg_catalog.statement_timestamp();
            PERFORM pg_catalog.pg_advisory_xact_lock(
                pg_catalog.hashtextextended(
                    'context-engine.file-publication:'
                    || requested_organization_id::text, 0
                )
            );
            PERFORM pg_catalog.set_config(
                'app.worker_job_id', requested_job_id::text, true
            );
            SELECT version.version_id, version.root_ref,
                   version.capability_manifest
            INTO selected_version_id, selected_root_ref, selected_capabilities
            FROM public.context_source AS source
            JOIN public.source_version AS version
              ON version.organization_id = source.organization_id
             AND version.source_id = source.source_id
             AND version.version_id = source.active_version_id
            WHERE source.organization_id = requested_organization_id
              AND source.source_id = requested_source_id
              AND source.lifecycle_state = 'active'
              AND version.capability_manifest->>'declarationVersion'
                    IN ('file-capabilities-v1', 'file-capabilities-v2')
            FOR UPDATE OF source;
            SELECT audience_membership.membership_id
            INTO selected_membership_id
            FROM public.membership AS audience_membership
                WHERE audience_membership.organization_id = requested_organization_id
                  AND audience_membership.membership_id = requested_audience_membership_id
                  AND audience_membership.user_id = requested_audience_user_id
                  AND audience_membership.membership_version = requested_audience_membership_version
                  AND audience_membership.status = 'active'
                  AND audience_membership.valid_from <= trusted_now
                  AND (audience_membership.valid_until IS NULL
                       OR audience_membership.valid_until > trusted_now);
            IF selected_version_id IS NULL
               OR selected_membership_id IS NULL
               OR NOT EXISTS (
                SELECT 1 FROM public.service_principal AS receiver
                WHERE receiver.organization_id = requested_organization_id
                  AND receiver.service_principal_id = requested_service_principal_id
                  AND receiver.workload = 'supply.file-import'
                  AND receiver.worker_audience = 'context-engine-worker'
                  AND receiver.operation = 'file.import'
                  AND receiver.enabled IS TRUE
            ) THEN RETURN; END IF;

            IF selected_capabilities->>'declarationVersion'
               = 'file-capabilities-v1' THEN
                INSERT INTO public.source_version (
                    organization_id, source_id, version_id, source_kind,
                    root_ref, capability_manifest, created_at
                ) VALUES (
                    requested_organization_id, requested_source_id,
                    requested_activated_version_id, 'file', selected_root_ref,
                    jsonb_set(jsonb_set(jsonb_set(
                        selected_capabilities, '{{declarationVersion}}',
                        '"file-capabilities-v2"'::jsonb),
                        '{{fileSourceAccess}}', '"available"'::jsonb),
                        '{{ingestionJobs}}', '"available"'::jsonb),
                    trusted_now
                );
                UPDATE public.context_source
                SET active_version_id = requested_activated_version_id
                WHERE organization_id = requested_organization_id
                  AND source_id = requested_source_id
                  AND active_version_id = selected_version_id
                  AND lifecycle_state = 'active';
                IF NOT FOUND THEN RETURN; END IF;
                selected_version_id := requested_activated_version_id;
            ELSIF selected_capabilities->>'fileSourceAccess' <> 'available'
               OR selected_capabilities->>'ingestionJobs' <> 'available'
            THEN RETURN; END IF;

            INSERT INTO public.file_acquisition (
                organization_id, acquisition_id, source_id, source_version_id,
                relative_path, audience_principal_ref, audience_membership_id,
                audience_membership_version, idempotency_key, request_digest,
                created_at, expected_content_sha256, expected_content_length,
                ui_preview_digest, expected_fragment_digest,
                compiler_config_version
            ) VALUES (
                requested_organization_id, requested_acquisition_id,
                requested_source_id, selected_version_id,
                requested_relative_path, requested_audience_principal_ref,
                requested_audience_membership_id,
                requested_audience_membership_version,
                requested_idempotency_key, requested_request_digest, trusted_now,
                requested_expected_content_sha256,
                requested_expected_content_length, requested_preview_digest,
                requested_expected_fragment_digest,
                requested_compiler_config_version
            ) ON CONFLICT (
                organization_id, source_id, idempotency_key
            ) DO NOTHING;
            SELECT acquisition.acquisition_id INTO selected_acquisition_id
            FROM public.file_acquisition AS acquisition
            WHERE acquisition.organization_id = requested_organization_id
              AND acquisition.source_id = requested_source_id
              AND acquisition.idempotency_key = requested_idempotency_key
              AND acquisition.request_digest = requested_request_digest
              AND acquisition.relative_path = requested_relative_path
              AND acquisition.audience_principal_ref = requested_audience_principal_ref
              AND acquisition.audience_membership_id = requested_audience_membership_id
              AND acquisition.audience_membership_version = requested_audience_membership_version
              AND acquisition.expected_content_sha256 = requested_expected_content_sha256
              AND acquisition.expected_content_length = requested_expected_content_length
              AND acquisition.ui_preview_digest = requested_preview_digest
              AND acquisition.expected_fragment_digest = requested_expected_fragment_digest
              AND acquisition.compiler_config_version = requested_compiler_config_version;
            IF selected_acquisition_id IS NULL THEN RETURN; END IF;
            PERFORM pg_catalog.set_config(
                'app.file_acquisition_id', selected_acquisition_id::text, true
            );
            INSERT INTO public.file_import_job (
                organization_id, job_id, acquisition_id, source_id,
                service_principal_id, workload, worker_audience, actor_kind,
                operation, state, created_at
            ) VALUES (
                requested_organization_id, requested_job_id,
                selected_acquisition_id, requested_source_id,
                requested_service_principal_id, 'supply.file-import',
                'context-engine-worker', 'service', 'file.import',
                'available', trusted_now
            ) ON CONFLICT (organization_id, acquisition_id) DO NOTHING;
            SELECT job.job_id INTO requested_job_id
            FROM public.file_import_job AS job
            WHERE job.organization_id = requested_organization_id
              AND job.acquisition_id = selected_acquisition_id
              AND job.service_principal_id = requested_service_principal_id;
            IF requested_job_id IS NULL THEN RETURN; END IF;
            PERFORM pg_catalog.set_config(
                'app.worker_job_id', requested_job_id::text, true
            );
            RETURN QUERY SELECT job.job_id, job.service_principal_id
            FROM public.file_import_job AS job
            WHERE job.organization_id = requested_organization_id
              AND job.acquisition_id = selected_acquisition_id
              AND job.service_principal_id = requested_service_principal_id;
        END; $function$
        """
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION public.{_EXACT_IMPORT}"
        f"{_EXACT_IMPORT_SIGNATURE} FROM PUBLIC"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_EXACT_IMPORT}"
        f"{_EXACT_IMPORT_SIGNATURE} TO {_CONTROL}"
    )
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_WORKER_DEFINER}")
    op.execute(
        f"ALTER FUNCTION public.{_EXACT_IMPORT}{_EXACT_IMPORT_SIGNATURE} "
        f"OWNER TO {_WORKER_DEFINER}"
    )
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_WORKER_DEFINER}")


def _create_article_functions() -> None:
    op.execute(
        f"CREATE POLICY membership_ui_access_definer_select ON membership "
        f"FOR SELECT TO {_ACCESS_DEFINER} USING ("
        "organization_id = NULLIF(current_setting('app.ui_actor_organization_id', true), '')::uuid "
        "AND user_id = NULLIF(current_setting('app.ui_actor_user_id', true), '')::uuid "
        "AND membership_id = NULLIF(current_setting('app.ui_actor_membership_id', true), '')::uuid "
        "AND membership_version = NULLIF(current_setting('app.ui_actor_membership_version', true), '')::bigint "
        "AND current_setting('app.ui_actor_mode', true) = 'article_policy_change')"
    )
    op.execute(f"GRANT SELECT ON TABLE membership TO {_ACCESS_DEFINER}")
    for command, expression in (
        (
            "INSERT",
            "WITH CHECK (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid)",
        ),
        (
            "UPDATE",
            "USING (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid) "
            "WITH CHECK (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid)",
        ),
    ):
        op.execute(
            f"CREATE POLICY article_explicit_policy_setting_access_definer_{command.lower()} "
            f"ON article_explicit_policy_setting FOR {command} TO {_ACCESS_DEFINER} {expression}"
        )
    op.execute(
        f"GRANT INSERT, UPDATE ON TABLE article_explicit_policy_setting TO {_ACCESS_DEFINER}"
    )
    op.execute(
        f"""
        CREATE FUNCTION public.{_READ_ARTICLE}(
            requested_organization_id uuid, requested_resource_ref text
        ) RETURNS TABLE (
            resource_ref text, source_ref text, policy_version bigint,
            local_policy_kind text, local_group_refs text[], policy_kind text,
            group_refs text[], published boolean, resolution_rung text,
            policy_epoch bigint
        ) LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path = pg_catalog SET row_security = on AS $function$
        BEGIN
            IF SESSION_USER <> '{_CONTROL}'
               OR NULLIF(current_setting('app.organization_id', true), '')::uuid
                    IS DISTINCT FROM requested_organization_id
               OR requested_resource_ref IS NULL
            THEN RETURN; END IF;
            RETURN QUERY
            SELECT policy.resource_ref, resource.source_ref,
                   policy.policy_version, policy.local_policy_kind,
                   policy.local_group_refs, policy.policy_kind,
                   policy.group_refs, policy.published,
                   policy.resolution_rung, epoch.policy_epoch
            FROM public.article_access_policy AS policy
            JOIN public.context_resource AS resource
              ON resource.organization_id = policy.organization_id
             AND resource.resource_ref = policy.resource_ref
             AND resource.tombstoned IS FALSE
            JOIN public.organization_policy_epoch AS epoch
              ON epoch.organization_id = policy.organization_id
            WHERE policy.organization_id = requested_organization_id
              AND policy.resource_ref = requested_resource_ref;
        END; $function$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION public.{_CHANGE_ARTICLE}(
            requested_organization_id uuid, requested_resource_ref text,
            expected_policy_version bigint, expected_policy_epoch bigint,
            requested_policy_kind text, requested_group_refs text[],
            requested_preview_digest text, requested_user_id uuid,
            requested_membership_id uuid,
            requested_membership_version bigint
        ) RETURNS TABLE (policy_version bigint, policy_epoch bigint)
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog SET row_security = on AS $function$
        DECLARE resource_row public.context_resource%ROWTYPE;
                policy_row public.article_access_policy%ROWTYPE;
                observation public.article_source_acl_observation%ROWTYPE;
                effective_kind text; effective_groups text[] := ARRAY[]::text[];
                next_version bigint; next_epoch bigint;
                selected_membership_id uuid; trusted_now timestamptz;
        BEGIN
            IF SESSION_USER <> '{_CONTROL}'
               OR NULLIF(current_setting('app.organization_id', true), '')::uuid
                    IS DISTINCT FROM requested_organization_id
               OR requested_preview_digest !~ '^[0-9a-f]{{64}}$'
               OR expected_policy_version NOT BETWEEN 1 AND {_MAX}
               OR expected_policy_epoch NOT BETWEEN 1 AND {_MAX}
               OR requested_membership_version NOT BETWEEN 1 AND {_MAX}
               OR requested_group_refs IS NULL
               OR NOT ((requested_policy_kind IN ('private','organization')
                        AND cardinality(requested_group_refs) = 0)
                    OR (requested_policy_kind = 'groups'
                        AND cardinality(requested_group_refs) > 0))
               OR EXISTS (
                    SELECT 1 FROM unnest(requested_group_refs) requested(group_ref)
                    WHERE NOT EXISTS (
                        SELECT 1 FROM public.article_access_group AS owned
                        WHERE owned.organization_id = requested_organization_id
                          AND owned.group_ref = requested.group_ref))
            THEN RETURN; END IF;
            trusted_now := pg_catalog.statement_timestamp();
            PERFORM pg_catalog.pg_advisory_xact_lock(
                pg_catalog.hashtextextended(
                    'context-engine.file-publication:'
                    || requested_organization_id::text, 0
                )
            );
            PERFORM pg_catalog.set_config(
                'app.ui_actor_mode', 'article_policy_change', true
            );
            PERFORM pg_catalog.set_config(
                'app.ui_actor_organization_id',
                requested_organization_id::text, true
            );
            PERFORM pg_catalog.set_config(
                'app.ui_actor_user_id', requested_user_id::text, true
            );
            PERFORM pg_catalog.set_config(
                'app.ui_actor_membership_id',
                requested_membership_id::text, true
            );
            PERFORM pg_catalog.set_config(
                'app.ui_actor_membership_version',
                requested_membership_version::text, true
            );
            SELECT membership.membership_id INTO selected_membership_id
            FROM public.membership AS membership
            WHERE membership.organization_id = requested_organization_id
              AND membership.user_id = requested_user_id
              AND membership.membership_id = requested_membership_id
              AND membership.membership_version = requested_membership_version
              AND membership.status = 'active'
              AND membership.valid_from <= trusted_now
              AND (membership.valid_until IS NULL
                   OR membership.valid_until > trusted_now);
            IF selected_membership_id IS NULL THEN RETURN; END IF;
            SELECT * INTO resource_row
            FROM public.context_resource AS resource
            WHERE resource.organization_id = requested_organization_id
              AND resource.resource_ref = requested_resource_ref
              AND resource.tombstoned IS FALSE
            FOR UPDATE;
            SELECT * INTO policy_row
            FROM public.article_access_policy AS policy
            WHERE policy.organization_id = requested_organization_id
              AND policy.resource_ref = requested_resource_ref
            FOR UPDATE;
            SELECT epoch.policy_epoch + 1 INTO next_epoch
            FROM public.organization_policy_epoch AS epoch
            WHERE epoch.organization_id = requested_organization_id
              AND epoch.policy_epoch = expected_policy_epoch
              AND epoch.policy_epoch < {_MAX}
            FOR UPDATE;
            IF resource_row.resource_ref IS NULL
               OR policy_row.resource_ref IS NULL
               OR policy_row.policy_version <> expected_policy_version
               OR policy_row.policy_version >= {_MAX}
               OR next_epoch IS NULL
            THEN RETURN; END IF;
            SELECT * INTO observation
            FROM public.article_source_acl_observation AS source_acl
            WHERE source_acl.organization_id = requested_organization_id
              AND source_acl.resource_ref = requested_resource_ref
              AND source_acl.source_ref = resource_row.source_ref
            FOR UPDATE;
            IF observation.resource_ref IS NOT NULL
               AND observation.observation_status = 'resolved' THEN
                IF requested_policy_kind = 'private'
                   OR observation.policy_kind = 'private' THEN
                    effective_kind := 'private';
                ELSIF requested_policy_kind = 'organization' THEN
                    effective_kind := observation.policy_kind;
                    effective_groups := observation.group_refs;
                ELSIF observation.policy_kind = 'organization' THEN
                    effective_kind := requested_policy_kind;
                    effective_groups := requested_group_refs;
                ELSE
                    SELECT COALESCE(array_agg(group_ref ORDER BY group_ref),
                                    ARRAY[]::text[])
                    INTO effective_groups
                    FROM (SELECT unnest(requested_group_refs) AS group_ref
                          INTERSECT
                          SELECT unnest(observation.group_refs)) AS shared;
                    IF cardinality(effective_groups) > 0 THEN
                        effective_kind := 'groups';
                    END IF;
                END IF;
            END IF;
            INSERT INTO public.article_explicit_policy_setting (
                organization_id, source_ref, resource_ref,
                policy_kind, group_refs
            ) VALUES (
                requested_organization_id, resource_row.source_ref,
                requested_resource_ref, requested_policy_kind,
                requested_group_refs
            ) ON CONFLICT (organization_id, resource_ref) DO UPDATE
            SET source_ref = EXCLUDED.source_ref,
                policy_kind = EXCLUDED.policy_kind,
                group_refs = EXCLUDED.group_refs;
            next_version := policy_row.policy_version + 1;
            UPDATE public.article_access_policy AS policy
            SET policy_version = next_version,
                local_policy_kind = requested_policy_kind,
                local_group_refs = requested_group_refs,
                policy_kind = effective_kind,
                group_refs = effective_groups,
                published = effective_kind IS NOT NULL,
                resolution_rung = 'explicit_article',
                fixed_at_policy_epoch = next_epoch
            WHERE policy.organization_id = requested_organization_id
              AND policy.resource_ref = requested_resource_ref
              AND policy.policy_version = expected_policy_version;
            IF NOT FOUND THEN RETURN; END IF;
            UPDATE public.organization_policy_epoch AS epoch
            SET policy_epoch = next_epoch
            WHERE epoch.organization_id = requested_organization_id
              AND epoch.policy_epoch = expected_policy_epoch;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING ERRCODE = '40001',
                    MESSAGE = 'Article policy confirmation was not accepted';
            END IF;
            RETURN QUERY SELECT next_version, next_epoch;
        END; $function$
        """
    )
    for name, signature in (
        (_READ_ARTICLE, _READ_ARTICLE_SIGNATURE),
        (_CHANGE_ARTICLE, _CHANGE_ARTICLE_SIGNATURE),
    ):
        op.execute(f"REVOKE ALL ON FUNCTION public.{name}{signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{name}{signature} TO {_CONTROL}")
        op.execute(f"GRANT CREATE ON SCHEMA public TO {_ACCESS_DEFINER}")
        op.execute(
            f"ALTER FUNCTION public.{name}{signature} OWNER TO {_ACCESS_DEFINER}"
        )
        op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_ACCESS_DEFINER}")


def _create_feedback() -> None:
    op.create_table(
        "context_feedback",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("feedback_ref", sa.Text(), nullable=False),
        sa.Column("run_ref", sa.Text(), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("membership_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("membership_version", sa.BigInteger(), nullable=False),
        sa.Column("principal_ref", sa.Text(), nullable=False),
        sa.Column("rating", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("statement_timestamp()"),
        ),
        sa.PrimaryKeyConstraint(
            "organization_id", "feedback_ref", name="pk_context_feedback"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "run_ref",
            "membership_id",
            "membership_version",
            name="uq_context_feedback_actor_run",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_ref"],
            ["context_run.organization_id", "context_run.run_ref"],
            name="fk_context_feedback_run_same_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "membership_id", "membership_version"],
            [
                "membership.organization_id",
                "membership.membership_id",
                "membership.membership_version",
            ],
            name="fk_context_feedback_membership_same_organization",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "feedback_ref ~ '^fb_[0-9a-f]{64}$'",
            name="ck_context_feedback_ref",
        ),
        sa.CheckConstraint(
            f"membership_version BETWEEN 1 AND {_MAX}",
            name="ck_context_feedback_membership_version",
        ),
        sa.CheckConstraint(
            "btrim(run_ref) <> '' AND char_length(run_ref) <= 256 "
            "AND btrim(principal_ref) <> ''",
            name="ck_context_feedback_refs",
        ),
        sa.CheckConstraint(
            "rating IN ('helpful','not_helpful')",
            name="ck_context_feedback_rating",
        ),
        sa.CheckConstraint(
            "note IS NULL OR (btrim(note) <> '' AND char_length(note) <= 1000)",
            name="ck_context_feedback_note",
        ),
    )
    for role in (
        "PUBLIC",
        _CONTROL,
        "context_engine_runtime",
        _RUN_READER_DEFINER,
    ):
        op.execute(f"REVOKE ALL ON TABLE context_feedback FROM {role}")
    op.execute("ALTER TABLE context_feedback ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE context_feedback FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY context_feedback_migrator_administration "
        f"ON context_feedback FOR ALL TO {_MIGRATOR} USING (true) WITH CHECK (true)"
    )
    feedback_context = """
        context_feedback.organization_id = NULLIF(
            current_setting('app.ui_feedback_organization_id', true), ''
        )::uuid
        AND context_feedback.run_ref = current_setting(
            'app.ui_feedback_run_ref', true
        )
        AND context_feedback.membership_id = NULLIF(
            current_setting('app.ui_feedback_membership_id', true), ''
        )::uuid
        AND context_feedback.membership_version = NULLIF(
            current_setting('app.ui_feedback_membership_version', true), ''
        )::bigint
        AND current_setting('app.ui_feedback_mode', true) = 'capture'
    """
    op.execute(
        "CREATE POLICY context_feedback_ui_definer_insert ON context_feedback "
        f"FOR INSERT TO {_RUN_READER_DEFINER} WITH CHECK ({feedback_context})"
    )
    op.execute(
        "CREATE POLICY context_feedback_ui_definer_select ON context_feedback "
        f"FOR SELECT TO {_RUN_READER_DEFINER} USING ({feedback_context})"
    )
    run_context = """
        context_run.organization_id = NULLIF(
            current_setting('app.ui_feedback_organization_id', true), ''
        )::uuid
        AND context_run.run_ref = current_setting(
            'app.ui_feedback_run_ref', true
        )
        AND context_run.user_id = NULLIF(
            current_setting('app.ui_feedback_user_id', true), ''
        )::uuid
        AND context_run.membership_id = NULLIF(
            current_setting('app.ui_feedback_membership_id', true), ''
        )::uuid
        AND context_run.membership_version = NULLIF(
            current_setting('app.ui_feedback_membership_version', true), ''
        )::bigint
        AND context_run.principal_ref = current_setting(
            'app.ui_feedback_principal_ref', true
        )
        AND current_setting('app.ui_feedback_mode', true) = 'capture'
    """
    op.execute(
        "CREATE POLICY context_run_ui_feedback_definer_select ON context_run "
        f"FOR SELECT TO {_RUN_READER_DEFINER} USING ({run_context})"
    )
    feedback_membership_context = """
        membership.organization_id = NULLIF(
            current_setting('app.ui_feedback_organization_id', true), ''
        )::uuid
        AND membership.user_id = NULLIF(
            current_setting('app.ui_feedback_user_id', true), ''
        )::uuid
        AND membership.membership_id = NULLIF(
            current_setting('app.ui_feedback_membership_id', true), ''
        )::uuid
        AND membership.membership_version = NULLIF(
            current_setting('app.ui_feedback_membership_version', true), ''
        )::bigint
        AND current_setting('app.ui_feedback_mode', true) = 'capture'
    """
    op.execute(
        "CREATE POLICY membership_ui_feedback_definer_select ON membership "
        f"FOR SELECT TO {_RUN_READER_DEFINER} "
        f"USING ({feedback_membership_context})"
    )
    op.execute(
        f"GRANT SELECT, INSERT ON TABLE context_feedback TO {_RUN_READER_DEFINER}"
    )
    op.execute(f"GRANT SELECT ON TABLE membership TO {_RUN_READER_DEFINER}")
    op.execute(
        f"""
        CREATE FUNCTION public.{_CAPTURE_FEEDBACK}(
            requested_organization_id uuid, requested_feedback_ref text,
            requested_run_ref text, requested_user_id uuid,
            requested_membership_id uuid,
            requested_membership_version bigint,
            requested_principal_ref text, requested_rating text,
            requested_note text
        ) RETURNS text LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog SET row_security = on AS $function$
        DECLARE recorded_ref text;
        BEGIN
            IF SESSION_USER <> '{_RUNTIME}'
               OR NULLIF(current_setting('app.organization_id', true), '')::uuid
                    IS DISTINCT FROM requested_organization_id
               OR current_setting('app.actor_kind', true) <> 'user'
               OR NULLIF(current_setting('app.user_id', true), '')::uuid
                    IS DISTINCT FROM requested_user_id
               OR NULLIF(current_setting('app.membership_id', true), '')::uuid
                    IS DISTINCT FROM requested_membership_id
               OR NULLIF(
                    current_setting('app.membership_version', true), ''
                  )::bigint IS DISTINCT FROM requested_membership_version
               OR current_setting('app.principal_ref', true)
                    IS DISTINCT FROM requested_principal_ref
               OR requested_feedback_ref !~ '^fb_[0-9a-f]{{64}}$'
               OR requested_rating NOT IN ('helpful','not_helpful')
               OR requested_membership_version NOT BETWEEN 1 AND {_MAX}
               OR requested_note IS NOT NULL AND (
                    btrim(requested_note) = ''
                    OR char_length(requested_note) > 1000)
            THEN RETURN NULL; END IF;
            PERFORM pg_catalog.set_config(
                'app.ui_feedback_mode', 'capture', true
            );
            PERFORM pg_catalog.set_config(
                'app.ui_feedback_organization_id',
                requested_organization_id::text, true
            );
            PERFORM pg_catalog.set_config(
                'app.ui_feedback_run_ref', requested_run_ref, true
            );
            PERFORM pg_catalog.set_config(
                'app.ui_feedback_user_id', requested_user_id::text, true
            );
            PERFORM pg_catalog.set_config(
                'app.ui_feedback_membership_id',
                requested_membership_id::text, true
            );
            PERFORM pg_catalog.set_config(
                'app.ui_feedback_membership_version',
                requested_membership_version::text, true
            );
            PERFORM pg_catalog.set_config(
                'app.ui_feedback_principal_ref', requested_principal_ref, true
            );
            IF NOT EXISTS (
                SELECT 1 FROM public.membership AS actor_membership
                WHERE actor_membership.organization_id =
                        requested_organization_id
                  AND actor_membership.user_id = requested_user_id
                  AND actor_membership.membership_id = requested_membership_id
                  AND actor_membership.membership_version =
                        requested_membership_version
                  AND actor_membership.status = 'active'
                  AND actor_membership.valid_from <=
                        pg_catalog.clock_timestamp()
                  AND (
                        actor_membership.valid_until IS NULL
                        OR actor_membership.valid_until >
                            pg_catalog.clock_timestamp()
                  )
            ) THEN RETURN NULL; END IF;
            IF NOT EXISTS (
                SELECT 1 FROM public.context_run AS run
                WHERE run.organization_id = requested_organization_id
                  AND run.run_ref = requested_run_ref
                  AND run.user_id = requested_user_id
                  AND run.membership_id = requested_membership_id
                  AND run.membership_version = requested_membership_version
                  AND run.principal_ref = requested_principal_ref
            ) THEN RETURN NULL; END IF;
            INSERT INTO public.context_feedback (
                organization_id, feedback_ref, run_ref, user_id,
                membership_id, membership_version, principal_ref,
                rating, note, recorded_at
            ) VALUES (
                requested_organization_id, requested_feedback_ref,
                requested_run_ref, requested_user_id,
                requested_membership_id, requested_membership_version,
                requested_principal_ref, requested_rating, requested_note,
                pg_catalog.statement_timestamp()
            ) ON CONFLICT (
                organization_id, run_ref, membership_id, membership_version
            ) DO NOTHING;
            SELECT feedback.feedback_ref INTO recorded_ref
            FROM public.context_feedback AS feedback
            WHERE feedback.organization_id = requested_organization_id
              AND feedback.run_ref = requested_run_ref
              AND feedback.membership_id = requested_membership_id
              AND feedback.membership_version = requested_membership_version;
            RETURN recorded_ref;
        END; $function$
        """
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION public.{_CAPTURE_FEEDBACK}"
        f"{_CAPTURE_FEEDBACK_SIGNATURE} FROM PUBLIC"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_CAPTURE_FEEDBACK}"
        f"{_CAPTURE_FEEDBACK_SIGNATURE} TO {_RUNTIME}"
    )
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_RUN_READER_DEFINER}")
    op.execute(
        f"ALTER FUNCTION public.{_CAPTURE_FEEDBACK}{_CAPTURE_FEEDBACK_SIGNATURE} "
        f"OWNER TO {_RUN_READER_DEFINER}"
    )
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_RUN_READER_DEFINER}")


def upgrade() -> None:
    """Add exact confirmation, Article edit, and evidence-only feedback."""

    _join_file_operation_fences()
    op.add_column("file_acquisition", sa.Column("ui_preview_digest", sa.Text()))
    op.add_column("file_acquisition", sa.Column("expected_fragment_digest", sa.Text()))
    op.add_column("file_acquisition", sa.Column("compiler_config_version", sa.Text()))
    op.drop_constraint(
        "ck_file_acquisition_change_observation",
        "file_acquisition",
        type_="check",
    )
    op.create_check_constraint(
        "ck_file_acquisition_change_observation",
        "file_acquisition",
        "((change_page_ref IS NULL AND change_ordinal IS NULL "
        "AND expected_content_sha256 IS NULL AND expected_content_length IS NULL "
        "AND ui_preview_digest IS NULL AND expected_fragment_digest IS NULL "
        "AND compiler_config_version IS NULL) OR "
        "(change_page_ref IS NOT NULL AND change_ordinal BETWEEN 1 AND 100 "
        "AND expected_content_sha256 ~ '^[0-9a-f]{64}$' "
        "AND expected_content_length >= 0 AND ui_preview_digest IS NULL "
        "AND expected_fragment_digest IS NULL AND compiler_config_version IS NULL) OR "
        "(change_page_ref IS NULL AND change_ordinal IS NULL "
        "AND expected_content_sha256 ~ '^[0-9a-f]{64}$' "
        "AND expected_content_length >= 0 "
        "AND ui_preview_digest ~ '^[0-9a-f]{64}$' "
        "AND expected_fragment_digest ~ '^[0-9a-f]{64}$' "
        "AND compiler_config_version = 'markdown-config-v1'))",
    )
    _replace_redeem_ui_fields(install=True)
    _create_exact_import()
    _create_article_functions()
    _create_feedback()


def downgrade() -> None:
    """Remove the M1 UI-specific persistence seams."""

    _join_file_operation_fences()
    op.execute("LOCK TABLE file_acquisition IN ACCESS EXCLUSIVE MODE")
    op.execute(f"DROP FUNCTION public.{_CAPTURE_FEEDBACK}{_CAPTURE_FEEDBACK_SIGNATURE}")
    op.execute(f"REVOKE SELECT ON TABLE membership FROM {_RUN_READER_DEFINER}")
    op.execute("DROP POLICY membership_ui_feedback_definer_select ON membership")
    op.execute("DROP POLICY context_run_ui_feedback_definer_select ON context_run")
    op.drop_table("context_feedback")
    for name, signature in (
        (_CHANGE_ARTICLE, _CHANGE_ARTICLE_SIGNATURE),
        (_READ_ARTICLE, _READ_ARTICLE_SIGNATURE),
    ):
        op.execute(f"DROP FUNCTION public.{name}{signature}")
    op.execute(
        "REVOKE INSERT, UPDATE ON TABLE article_explicit_policy_setting "
        f"FROM {_ACCESS_DEFINER}"
    )
    op.execute(f"REVOKE SELECT ON TABLE membership FROM {_ACCESS_DEFINER}")
    op.execute("DROP POLICY membership_ui_access_definer_select ON membership")
    op.execute(
        "DROP POLICY article_explicit_policy_setting_access_definer_update "
        "ON article_explicit_policy_setting"
    )
    op.execute(
        "DROP POLICY article_explicit_policy_setting_access_definer_insert "
        "ON article_explicit_policy_setting"
    )
    op.execute(f"DROP FUNCTION public.{_EXACT_IMPORT}{_EXACT_IMPORT_SIGNATURE}")
    _replace_redeem_ui_fields(install=False)
    op.drop_constraint(
        "ck_file_acquisition_change_observation",
        "file_acquisition",
        type_="check",
    )
    op.create_check_constraint(
        "ck_file_acquisition_change_observation",
        "file_acquisition",
        "(change_page_ref IS NULL AND change_ordinal IS NULL "
        "AND expected_content_sha256 IS NULL "
        "AND expected_content_length IS NULL) OR "
        "(change_page_ref IS NOT NULL "
        "AND change_ordinal BETWEEN 1 AND 100 "
        "AND expected_content_sha256 ~ '^[0-9a-f]{64}$' "
        "AND expected_content_length >= 0)",
    )
    op.drop_column("file_acquisition", "compiler_config_version")
    op.drop_column("file_acquisition", "expected_fragment_digest")
    op.drop_column("file_acquisition", "ui_preview_digest")
