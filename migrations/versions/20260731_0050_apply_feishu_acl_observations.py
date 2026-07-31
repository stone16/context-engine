"""Apply accepted Feishu ACL observations with one Policy Epoch advance.

Revision ID: 20260731_0050
Revises: 20260731_0049
Create Date: 2026-07-31
"""

# ruff: noqa: E501

from collections.abc import Sequence

from alembic import op

revision: str = "20260731_0050"
down_revision: str | None = "20260731_0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONTROL = "context_engine_control"
_DEFINER = "context_engine_access_policy_definer"
_RUNTIME = "context_engine_runtime"
_WORKER = "context_engine_worker"
_FUNCTION = "context_control_apply_feishu_acl_observation"
_SIGNATURE = "(uuid,uuid,uuid,text,text,boolean)"
_MAX = 9223372036854775807
_V1 = """{"aclEvidenceMode":"mirrored","authorizeAndProject":"unavailable","batchLimits":"unavailable","checkpoint":"unavailable","checkpointSemantics":"unavailable","consistencyGuarantees":"unavailable","contentKinds":["markdown"],"cursorSemantics":"unavailable","declarationVersion":"file-capabilities-v1","deletion":"unavailable","describeCapabilities":"unavailable","discover":"unavailable","fileSourceAccess":"unavailable","freshness":"unavailable","ingestionJobs":"unavailable","projectionFields":[],"readChanges":"unavailable","resourceKinds":["markdown_document"],"sourceMode":"materialized"}"""
_V2 = """{"aclEvidenceMode":"mirrored","authorizeAndProject":"unavailable","batchLimits":"unavailable","checkpoint":"unavailable","checkpointSemantics":"unavailable","consistencyGuarantees":"unavailable","contentKinds":["markdown"],"cursorSemantics":"unavailable","declarationVersion":"file-capabilities-v2","deletion":"unavailable","describeCapabilities":"unavailable","discover":"unavailable","fileSourceAccess":"available","freshness":"unavailable","ingestionJobs":"available","projectionFields":[],"readChanges":"unavailable","resourceKinds":["markdown_document"],"sourceMode":"materialized"}"""
_V3 = """{"aclEvidenceMode":"mirrored","authorizeAndProject":"unavailable","batchLimits":"available","checkpoint":"available","checkpointSemantics":"available","consistencyGuarantees":"unavailable","contentKinds":["markdown"],"cursorSemantics":"available","declarationVersion":"file-capabilities-v3","deletion":"unavailable","describeCapabilities":"available","discover":"unavailable","fileSourceAccess":"available","freshness":"unavailable","ingestionJobs":"available","projectionFields":[],"readChanges":"available","resourceKinds":["markdown_document"],"sourceMode":"materialized"}"""
_V4 = """{"aclEvidenceMode":"mirrored","authorizeAndProject":"unavailable","batchLimits":"available","checkpoint":"available","checkpointSemantics":"available","consistencyGuarantees":"unavailable","contentKinds":["markdown"],"cursorSemantics":"available","declarationVersion":"file-capabilities-v4","deleteObservations":"available","deletion":"unavailable","describeCapabilities":"available","discover":"unavailable","fileSourceAccess":"available","freshness":"unavailable","ingestionJobs":"available","projectionFields":[],"readChanges":"available","resourceKinds":["markdown_document"],"sourceMode":"materialized"}"""
_FEISHU_CAPABILITIES = """{"aclEvidenceMode":"mirrored","authorizeAndProject":"unavailable","batchLimits":"available","checkpoint":"available","checkpointSemantics":"available","contentKinds":["markdown"],"consistencyGuarantees":"unavailable","cursorSemantics":"available","declarationVersion":"feishu-docs-capabilities-v1","deleteObservations":"available","deletion":"unavailable","describeCapabilities":"available","discover":"unavailable","fileSourceAccess":"unavailable","freshness":"available","ingestionJobs":"available","liveNetwork":"not_active","projectionFields":[],"readChanges":"available","resourceKinds":["markdown_document"],"sourceMode":"materialized"}"""


def _file_capability_documents() -> str:
    return ", ".join(f"'{value}'::jsonb" for value in (_V1, _V2, _V3, _V4))


def _replace_runtime_source_functions(*, admit_feishu: bool) -> None:
    source_kind = (
        "source.source_kind IN ('file', 'feishu_docs')"
        if admit_feishu
        else "source.source_kind = 'file'"
    )
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.context_runtime_file_source_lifecycle_allows(
            requested_organization_id uuid,
            requested_source_ref text
        ) RETURNS boolean
        LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE requested_source_id uuid;
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
            THEN RETURN true; END IF;
            requested_source_id := requested_source_ref::uuid;
            RETURN EXISTS (
                SELECT 1 FROM public.context_source AS source
                WHERE source.organization_id = requested_organization_id
                  AND source.source_id = requested_source_id
                  AND {source_kind}
                  AND source.lifecycle_state = 'active'
            );
        END;
        $function$
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.context_runtime_article_source_version_allows(
            requested_organization_id uuid,
            requested_resource_ref text,
            expected_source_version_ref uuid
        ) RETURNS boolean
        LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path = pg_catalog
        SET row_security = on
        AS $function$
        DECLARE trusted_source_ref text;
        BEGIN
            IF SESSION_USER <> '{_RUNTIME}'
               OR requested_organization_id IS NULL
               OR requested_organization_id IS DISTINCT FROM NULLIF(
                    current_setting('app.organization_id', true), ''
               )::uuid
               OR requested_resource_ref IS NULL
            THEN RETURN false; END IF;
            SELECT resource.source_ref INTO trusted_source_ref
            FROM public.context_resource AS resource
            WHERE resource.organization_id = requested_organization_id
              AND resource.resource_ref = requested_resource_ref;
            IF trusted_source_ref IS NULL THEN RETURN false; END IF;
            IF trusted_source_ref !~
                '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-'
                '[0-9a-f]{{4}}-[0-9a-f]{{12}}$'
            THEN RETURN expected_source_version_ref IS NULL; END IF;
            IF expected_source_version_ref IS NULL THEN RETURN false; END IF;
            RETURN EXISTS (
                SELECT 1 FROM public.context_source AS source
                WHERE source.organization_id = requested_organization_id
                  AND source.source_id = trusted_source_ref::uuid
                  AND {source_kind}
                  AND source.lifecycle_state = 'active'
                  AND source.active_version_id = expected_source_version_ref
            );
        END;
        $function$
        """
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


def _replace_file_access_policy_trigger(*, require_file_source: bool) -> None:
    """Keep the legacy access-row observer from competing with Feishu Control."""

    source_kind_guard = (
        "AND source.source_kind = 'file'" if require_file_source else ""
    )
    missing_source_guard = (
        "IF active_source_id IS NULL THEN RETURN NULL; END IF;"
        if require_file_source
        else ""
    )
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.article_access_policy_fix_from_file_access_grant()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog SET row_security = on
        AS $function$
        DECLARE
            source_value text;
            declared_mode text := 'mirrored';
            active_source_id uuid;
            active_source_version_ref uuid;
            observation_time timestamptz := pg_catalog.statement_timestamp();
            prior_policy public.article_access_policy%ROWTYPE;
            next_epoch bigint;
        BEGIN
            PERFORM pg_catalog.set_config(
                'app.organization_id', NEW.organization_id::text, true
            );
            SELECT source_ref INTO source_value FROM public.context_resource
            WHERE organization_id = NEW.organization_id
              AND resource_ref = NEW.resource_ref;
            IF source_value IS NULL THEN RETURN NULL; END IF;
            IF source_value ~
                '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-'
                '[0-9a-f]{{4}}-[0-9a-f]{{12}}$'
            THEN
                SELECT source.source_id, version.version_id,
                       version.capability_manifest->>'aclEvidenceMode'
                INTO active_source_id, active_source_version_ref, declared_mode
                FROM public.context_source AS source
                JOIN public.source_version AS version
                  ON version.organization_id = source.organization_id
                 AND version.source_id = source.source_id
                 AND version.version_id = source.active_version_id
                WHERE source.organization_id = NEW.organization_id
                  AND source.source_id = source_value::uuid
                  AND source.lifecycle_state = 'active'
                  {source_kind_guard};
                {missing_source_guard}
                IF declared_mode <> 'mirrored' THEN RETURN NULL; END IF;
            END IF;
            IF NEW.access_state = 'allowed' THEN
                INSERT INTO public.article_source_acl_observation (
                    organization_id, source_ref, resource_ref, evidence_mode,
                    observation_status, policy_kind, group_refs,
                    observation_version, observed_at, source_id,
                    source_version_ref, acl_as_of, declared_lag_seconds
                ) VALUES (
                    NEW.organization_id, source_value, NEW.resource_ref,
                    declared_mode, 'resolved', 'private', ARRAY[]::text[], 1,
                    observation_time, active_source_id, active_source_version_ref,
                    observation_time, 0
                ) ON CONFLICT (organization_id, resource_ref) DO NOTHING;
                PERFORM public.context_fix_article_access_policy(
                    NEW.organization_id, NEW.resource_ref
                );
                RETURN NULL;
            END IF;
            IF TG_OP = 'UPDATE' AND OLD.access_state = 'allowed'
               AND NEW.access_state = 'revoked' THEN
                IF EXISTS (
                    SELECT 1
                    FROM public.resource_access_policy AS remaining_access
                    WHERE remaining_access.organization_id = NEW.organization_id
                      AND remaining_access.resource_ref = NEW.resource_ref
                      AND remaining_access.access_state = 'allowed'
                ) THEN
                    RETURN NULL;
                END IF;
                SELECT * INTO prior_policy
                FROM public.article_access_policy AS policy
                WHERE policy.organization_id = NEW.organization_id
                  AND policy.resource_ref = NEW.resource_ref
                FOR UPDATE;
                IF prior_policy.resource_ref IS NULL
                   OR prior_policy.policy_version >= {_MAX}
                   OR prior_policy.published IS FALSE THEN RETURN NULL; END IF;
                SELECT epoch.policy_epoch + 1 INTO next_epoch
                FROM public.organization_policy_epoch AS epoch
                WHERE epoch.organization_id = NEW.organization_id
                  AND epoch.policy_epoch < {_MAX};
                IF next_epoch IS NULL THEN
                    RAISE EXCEPTION USING ERRCODE = '40001',
                        MESSAGE = 'Article policy revocation was not accepted';
                END IF;
                UPDATE public.article_source_acl_observation AS observation
                SET observation_status = 'failed', policy_kind = NULL,
                    group_refs = ARRAY[]::text[],
                    observation_version = observation.observation_version + 1,
                    observed_at = observation_time,
                    acl_as_of = observation_time,
                    declared_lag_seconds = 0
                WHERE observation.organization_id = NEW.organization_id
                  AND observation.resource_ref = NEW.resource_ref
                  AND observation.observation_version < {_MAX};
                UPDATE public.article_access_policy AS policy
                SET policy_version = policy.policy_version + 1,
                    policy_kind = NULL, group_refs = ARRAY[]::text[],
                    published = false, source_observation_status = 'failed',
                    source_observation_version =
                        COALESCE(policy.source_observation_version, 0) + 1,
                    source_acl_as_of = observation_time,
                    source_declared_lag_seconds = 0,
                    fixed_at_policy_epoch = next_epoch
                WHERE policy.organization_id = NEW.organization_id
                  AND policy.resource_ref = NEW.resource_ref;
            END IF;
            RETURN NULL;
        END; $function$
        """
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


def upgrade() -> None:
    """Admit the twin-only Feishu source and its atomic ACL Control function."""

    op.drop_constraint("ck_context_source_kind_file", "context_source", type_="check")
    op.create_check_constraint(
        "ck_context_source_kind",
        "context_source",
        "source_kind IN ('file', 'feishu_docs')",
    )
    op.drop_constraint("ck_source_version_kind_file", "source_version", type_="check")
    op.create_check_constraint(
        "ck_source_version_kind",
        "source_version",
        "source_kind IN ('file', 'feishu_docs')",
    )
    op.drop_constraint(
        "ck_source_version_file_capabilities", "source_version", type_="check"
    )
    op.create_check_constraint(
        "ck_source_version_capabilities",
        "source_version",
        "(source_kind = 'file' AND capability_manifest IN "
        f"({_file_capability_documents()})) OR "
        f"(source_kind = 'feishu_docs' AND capability_manifest = '{_FEISHU_CAPABILITIES}'::jsonb)",
    )
    _replace_runtime_source_functions(admit_feishu=True)
    _replace_file_access_policy_trigger(require_file_source=True)

    tenant = (
        "organization_id = NULLIF("
        "current_setting('app.organization_id', true), '')::uuid"
    )
    for table_name in (
        "supply_connector_job",
        "supply_connector_accepted_page",
        "supply_connector_staged_page",
    ):
        op.execute(
            f"CREATE POLICY {table_name}_feishu_acl_definer_select "
            f"ON {table_name} FOR SELECT TO {_DEFINER} USING ({tenant})"
        )
        op.execute(f"GRANT SELECT ON TABLE {table_name} TO {_DEFINER}")
    op.execute(
        "CREATE POLICY resource_access_policy_feishu_acl_definer_insert "
        f"ON resource_access_policy FOR INSERT TO {_DEFINER} WITH CHECK ({tenant})"
    )
    op.execute(f"GRANT INSERT ON TABLE resource_access_policy TO {_DEFINER}")

    op.execute(
        f"""
        CREATE FUNCTION public.{_FUNCTION}(
            requested_organization_id uuid,
            requested_source_version_id uuid,
            requested_worker_job_id uuid,
            requested_page_ref text,
            requested_document_ref text,
            requested_delete_observation boolean
        ) RETURNS TABLE (
            observation_version bigint,
            policy_epoch bigint,
            published boolean,
            tombstoned boolean
        )
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public
        SET row_security = on
        AS $function$
        DECLARE source_id uuid;
        DECLARE staged_payload jsonb;
        DECLARE envelope jsonb;
        DECLARE acl jsonb;
        DECLARE artifact jsonb;
        DECLARE artifact_status text;
        DECLARE artifact_policy_kind text;
        DECLARE artifact_groups text[] := ARRAY[]::text[];
        DECLARE artifact_principals text[] := ARRAY[]::text[];
        DECLARE observed_at timestamptz;
        DECLARE prior_observation public.article_source_acl_observation%ROWTYPE;
        DECLARE next_observation_version bigint;
        DECLARE current_epoch bigint;
        DECLARE next_epoch bigint;
        DECLARE article public.context_resource%ROWTYPE;
        DECLARE existing_policy public.article_access_policy%ROWTYPE;
        DECLARE local_kind text;
        DECLARE local_groups text[] := ARRAY[]::text[];
        DECLARE effective_kind text;
        DECLARE effective_groups text[] := ARRAY[]::text[];
        DECLARE result_published boolean := false;
        DECLARE result_tombstoned boolean := false;
        BEGIN
            IF session_user <> '{_CONTROL}'
               OR NULLIF(current_setting('app.organization_id', true), '')::uuid
                    IS DISTINCT FROM requested_organization_id
               OR requested_page_ref IS NULL
               OR requested_document_ref IS NULL
               OR requested_delete_observation IS NULL
            THEN RETURN; END IF;

            PERFORM pg_catalog.pg_advisory_xact_lock(
                pg_catalog.hashtextextended(
                    'context-engine.article-policy:' || requested_organization_id::text,
                    0
                )
            );

            SELECT job.source_id, pg_catalog.convert_from(staged.page_payload, 'UTF8')::jsonb
            INTO source_id, staged_payload
            FROM public.supply_connector_job AS job
            JOIN public.supply_connector_accepted_page AS accepted
              ON accepted.organization_id = job.organization_id
             AND accepted.source_id = job.source_id
             AND accepted.source_version_id = job.source_version_id
             AND accepted.worker_job_id = job.worker_job_id
            JOIN public.supply_connector_staged_page AS staged
              ON staged.organization_id = accepted.organization_id
             AND staged.source_id = accepted.source_id
             AND staged.source_version_id = accepted.source_version_id
             AND staged.worker_job_id = accepted.worker_job_id
             AND staged.page_ref = accepted.page_ref
            JOIN public.context_source AS source
              ON source.organization_id = job.organization_id
             AND source.source_id = job.source_id
             AND source.active_version_id = job.source_version_id
             AND source.lifecycle_state = 'active'
             AND source.source_kind = 'feishu_docs'
            JOIN public.source_version AS version
              ON version.organization_id = source.organization_id
             AND version.source_id = source.source_id
             AND version.version_id = source.active_version_id
             AND version.source_kind = 'feishu_docs'
             AND version.capability_manifest = '{_FEISHU_CAPABILITIES}'::jsonb
            WHERE job.organization_id = requested_organization_id
              AND job.source_version_id = requested_source_version_id
              AND job.worker_job_id = requested_worker_job_id
              AND accepted.page_ref = requested_page_ref;
            IF source_id IS NULL THEN RETURN; END IF;

            IF requested_delete_observation THEN
                SELECT value INTO envelope
                FROM jsonb_array_elements(staged_payload->'deleted_document_refs')
                WHERE value->>'document_ref' = requested_document_ref;
            ELSE
                SELECT value INTO envelope
                FROM jsonb_array_elements(staged_payload->'documents')
                WHERE value->>'document_ref' = requested_document_ref;
            END IF;
            IF envelope IS NULL THEN RETURN; END IF;
            acl := envelope->'acl_observation';
            IF acl->>'evidence_class' IS DISTINCT FROM 'mirrored'
               OR acl->>'organization_id' IS DISTINCT FROM requested_organization_id::text
               OR jsonb_typeof(acl->'evidence_payload') IS DISTINCT FROM 'string'
            THEN RETURN; END IF;
            BEGIN
                observed_at := (acl->>'observed_at')::timestamptz;
                artifact := pg_catalog.convert_from(
                    pg_catalog.decode(acl->>'evidence_payload', 'base64'), 'UTF8'
                )::jsonb;
            EXCEPTION WHEN data_exception THEN RETURN;
            END;
            IF artifact->>'schema_version' IS DISTINCT FROM 'feishu-acl-observation-v1'
               OR artifact->>'document_ref' IS DISTINCT FROM requested_document_ref
               OR artifact->'flattening'->>'artifact_version'
                    IS DISTINCT FROM 'feishu-group-flattening-v1'
               OR artifact->'flattening'->>'digest' !~ '^[0-9a-f]{{64}}$'
               OR jsonb_typeof(artifact->'flattening'->'local_group_refs')
                    IS DISTINCT FROM 'array'
               OR jsonb_typeof(artifact->'flattening'->'local_principal_refs')
                    IS DISTINCT FROM 'array'
               OR jsonb_typeof(artifact->'flattening'->'unresolved_group_refs')
                    IS DISTINCT FROM 'array'
            THEN RETURN; END IF;
            artifact_status := artifact->>'status';
            artifact_policy_kind := artifact->>'policy_kind';
            SELECT COALESCE(pg_catalog.array_agg(value ORDER BY value), ARRAY[]::text[])
            INTO artifact_groups
            FROM jsonb_array_elements_text(
                artifact->'flattening'->'local_group_refs'
            ) AS item(value);
            SELECT COALESCE(pg_catalog.array_agg(value ORDER BY value), ARRAY[]::text[])
            INTO artifact_principals
            FROM jsonb_array_elements_text(
                artifact->'flattening'->'local_principal_refs'
            ) AS item(value);
            IF artifact_status NOT IN ('resolved', 'failed', 'unresolved_group')
               OR (artifact_status = 'resolved'
                   AND artifact_policy_kind NOT IN ('private', 'organization', 'groups'))
               OR (artifact_status <> 'resolved' AND artifact_policy_kind IS NOT NULL)
               OR (artifact_policy_kind = 'groups' AND cardinality(artifact_groups) = 0)
               OR (artifact_policy_kind <> 'groups' AND cardinality(artifact_groups) <> 0)
               OR (artifact_status = 'unresolved_group' AND jsonb_array_length(
                    artifact->'flattening'->'unresolved_group_refs') = 0)
               OR EXISTS (
                    SELECT 1 FROM pg_catalog.unnest(artifact_principals) AS value
                    WHERE pg_catalog.btrim(value) = '' OR value ~ '[[:space:]]'
               )
               OR EXISTS (
                    SELECT 1 FROM pg_catalog.unnest(artifact_groups) AS requested(group_ref)
                    WHERE NOT EXISTS (
                        SELECT 1 FROM public.article_access_group AS owned
                        WHERE owned.organization_id = requested_organization_id
                          AND owned.group_ref = requested.group_ref
                    )
               )
            THEN
                artifact_status := 'unresolved_group';
                artifact_policy_kind := NULL;
                artifact_groups := ARRAY[]::text[];
                artifact_principals := ARRAY[]::text[];
            END IF;

            SELECT * INTO prior_observation
            FROM public.article_source_acl_observation AS observation
            WHERE observation.organization_id = requested_organization_id
              AND observation.resource_ref = requested_document_ref
            FOR UPDATE;
            IF prior_observation.resource_ref IS NOT NULL
               AND observed_at <= prior_observation.observed_at
            THEN RETURN; END IF;
            next_observation_version := COALESCE(
                prior_observation.observation_version + 1, 1
            );
            IF next_observation_version > {_MAX} THEN RETURN; END IF;

            SELECT * INTO article
            FROM public.context_resource AS resource
            WHERE resource.organization_id = requested_organization_id
              AND resource.source_ref = source_id::text
              AND resource.resource_ref = requested_document_ref
            FOR UPDATE;
            IF article.resource_ref IS NOT NULL THEN
                SELECT * INTO existing_policy
                FROM public.article_access_policy AS policy
                WHERE policy.organization_id = requested_organization_id
                  AND policy.resource_ref = requested_document_ref
                FOR UPDATE;
                IF existing_policy.resource_ref IS NOT NULL THEN
                    local_kind := existing_policy.local_policy_kind;
                    local_groups := existing_policy.local_group_refs;
                END IF;
            END IF;

            SELECT epoch.policy_epoch INTO current_epoch
            FROM public.organization_policy_epoch AS epoch
            WHERE epoch.organization_id = requested_organization_id
            FOR UPDATE;
            IF current_epoch IS NULL OR current_epoch >= {_MAX} THEN RETURN; END IF;

            IF article.resource_ref IS NOT NULL
               AND existing_policy.resource_ref IS NOT NULL
               AND NOT requested_delete_observation
               AND artifact_status = 'resolved'
               AND artifact_policy_kind = 'private'
            THEN
                INSERT INTO public.resource_access_policy (
                    organization_id, resource_ref, principal_ref,
                    access_version, access_state, revoked_at
                )
                SELECT requested_organization_id, requested_document_ref,
                       principal_ref, 1, 'allowed', NULL
                FROM pg_catalog.unnest(artifact_principals)
                     AS requested(principal_ref)
                ON CONFLICT (organization_id, resource_ref, principal_ref)
                DO UPDATE SET
                    access_state = 'allowed',
                    access_version = resource_access_policy.access_version + 1,
                    revoked_at = NULL
                WHERE resource_access_policy.access_state = 'revoked'
                  AND resource_access_policy.access_version < {_MAX};
                UPDATE public.resource_access_policy AS access
                SET access_state = 'revoked',
                    access_version = access.access_version + 1,
                    revoked_at = pg_catalog.statement_timestamp()
                WHERE access.organization_id = requested_organization_id
                  AND access.resource_ref = requested_document_ref
                  AND access.access_state = 'allowed'
                  AND NOT (access.principal_ref = ANY(artifact_principals))
                  AND access.access_version < {_MAX};
            END IF;

            INSERT INTO public.article_source_acl_observation (
                organization_id, source_ref, source_id, source_version_ref,
                resource_ref, evidence_mode, observation_status, policy_kind,
                group_refs, observation_version, observed_at, acl_as_of,
                declared_lag_seconds
            ) VALUES (
                requested_organization_id, source_id::text, source_id,
                requested_source_version_id, requested_document_ref, 'mirrored',
                CASE WHEN requested_delete_observation THEN 'failed'
                     ELSE artifact_status END,
                CASE WHEN requested_delete_observation THEN NULL
                     ELSE artifact_policy_kind END,
                CASE WHEN requested_delete_observation THEN ARRAY[]::text[]
                     ELSE artifact_groups END,
                next_observation_version, observed_at, observed_at, 0
            ) ON CONFLICT (organization_id, resource_ref) DO UPDATE SET
                source_ref = EXCLUDED.source_ref,
                source_id = EXCLUDED.source_id,
                source_version_ref = EXCLUDED.source_version_ref,
                evidence_mode = EXCLUDED.evidence_mode,
                observation_status = EXCLUDED.observation_status,
                policy_kind = EXCLUDED.policy_kind,
                group_refs = EXCLUDED.group_refs,
                observation_version = EXCLUDED.observation_version,
                observed_at = EXCLUDED.observed_at,
                acl_as_of = EXCLUDED.acl_as_of,
                declared_lag_seconds = EXCLUDED.declared_lag_seconds;

            IF article.resource_ref IS NOT NULL THEN
                IF requested_delete_observation THEN
                    UPDATE public.context_resource AS resource
                    SET tombstoned = true
                    WHERE resource.organization_id = requested_organization_id
                      AND resource.resource_ref = requested_document_ref
                      AND resource.tombstoned IS FALSE;
                    result_tombstoned := true;
                ELSIF existing_policy.resource_ref IS NOT NULL THEN
                    IF artifact_status <> 'resolved' OR local_kind IS NULL THEN
                        effective_kind := NULL;
                        effective_groups := ARRAY[]::text[];
                    ELSIF local_kind = 'private' OR artifact_policy_kind = 'private' THEN
                        effective_kind := 'private';
                    ELSIF local_kind = 'organization' THEN
                        effective_kind := artifact_policy_kind;
                        effective_groups := artifact_groups;
                    ELSIF artifact_policy_kind = 'organization' THEN
                        effective_kind := local_kind;
                        effective_groups := local_groups;
                    ELSE
                        SELECT COALESCE(pg_catalog.array_agg(value ORDER BY value), ARRAY[]::text[])
                        INTO effective_groups
                        FROM (
                            SELECT pg_catalog.unnest(local_groups) AS value
                            INTERSECT
                            SELECT pg_catalog.unnest(artifact_groups) AS value
                        ) AS shared;
                        IF cardinality(effective_groups) > 0 THEN
                            effective_kind := 'groups';
                        END IF;
                    END IF;
                    UPDATE public.article_access_policy AS policy
                    SET policy_version = policy.policy_version + 1,
                        policy_kind = effective_kind,
                        group_refs = effective_groups,
                        published = effective_kind IS NOT NULL,
                        source_observation_status = artifact_status,
                        source_observation_version = next_observation_version,
                        source_version_ref = requested_source_version_id,
                        source_acl_as_of = observed_at,
                        source_declared_lag_seconds = 0,
                        fixed_at_policy_epoch = current_epoch + 1
                    WHERE policy.organization_id = requested_organization_id
                      AND policy.resource_ref = requested_document_ref
                      AND policy.policy_version < {_MAX};
                    IF NOT FOUND THEN
                        RAISE EXCEPTION USING ERRCODE = '40001',
                            MESSAGE = 'Feishu ACL observation was not accepted';
                    END IF;
                    result_published := effective_kind IS NOT NULL;
                END IF;
            END IF;

            UPDATE public.organization_policy_epoch AS epoch
            SET policy_epoch = epoch.policy_epoch + 1
            WHERE epoch.organization_id = requested_organization_id
              AND epoch.policy_epoch = current_epoch
            RETURNING epoch.policy_epoch INTO next_epoch;
            IF next_epoch IS NULL THEN
                RAISE EXCEPTION USING ERRCODE = '40001',
                    MESSAGE = 'Feishu ACL observation was not accepted';
            END IF;
            RETURN QUERY SELECT next_observation_version, next_epoch,
                                result_published, result_tombstoned;
        END;
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_FUNCTION}{_SIGNATURE} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.{_FUNCTION}{_SIGNATURE} TO {_CONTROL}")
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"ALTER FUNCTION public.{_FUNCTION}{_SIGNATURE} OWNER TO {_DEFINER}")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


def downgrade() -> None:
    """Remove Feishu admission only when no Feishu durable state exists."""

    op.execute(
        """
        DO $block$
        BEGIN
            IF EXISTS (SELECT 1 FROM context_source WHERE source_kind = 'feishu_docs')
               OR EXISTS (SELECT 1 FROM source_version WHERE source_kind = 'feishu_docs')
            THEN RAISE EXCEPTION USING ERRCODE = '55000',
                MESSAGE = 'cannot downgrade with Feishu source state';
            END IF;
        END;
        $block$
        """
    )
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(f"DROP FUNCTION public.{_FUNCTION}{_SIGNATURE}")
    op.execute("RESET ROLE")
    _replace_file_access_policy_trigger(require_file_source=False)
    _replace_runtime_source_functions(admit_feishu=False)
    op.execute(f"REVOKE INSERT ON TABLE resource_access_policy FROM {_DEFINER}")
    op.execute(
        "DROP POLICY resource_access_policy_feishu_acl_definer_insert "
        "ON resource_access_policy"
    )
    for table_name in (
        "supply_connector_staged_page",
        "supply_connector_accepted_page",
        "supply_connector_job",
    ):
        op.execute(f"REVOKE SELECT ON TABLE {table_name} FROM {_DEFINER}")
        op.execute(
            f"DROP POLICY {table_name}_feishu_acl_definer_select ON {table_name}"
        )
    op.drop_constraint("ck_source_version_capabilities", "source_version", type_="check")
    op.create_check_constraint(
        "ck_source_version_file_capabilities",
        "source_version",
        f"capability_manifest IN ({_file_capability_documents()})",
    )
    op.drop_constraint("ck_source_version_kind", "source_version", type_="check")
    op.create_check_constraint(
        "ck_source_version_kind_file", "source_version", "source_kind = 'file'"
    )
    op.drop_constraint("ck_context_source_kind", "context_source", type_="check")
    op.create_check_constraint(
        "ck_context_source_kind_file", "context_source", "source_kind = 'file'"
    )
