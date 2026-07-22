"""Make unchanged File acquisitions auditable publication no-ops.

Revision ID: 20260724_0013
Revises: 20260723_0012
Create Date: 2026-07-24
"""

# ruff: noqa: E501

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260724_0013"
down_revision: str | None = "20260723_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WORKER = "context_engine_worker"
_DEFINER = "context_engine_worker_lease_definer"
_V1_SIGNATURE = "(uuid, uuid, uuid, text, text, uuid, text, text, text, text, text, text, text, text, bigint, bytea, timestamp with time zone, timestamp with time zone)"
_V2_SIGNATURE = "(uuid, uuid, uuid, text, text, uuid, text, text, text, text, text, jsonb, bigint, bytea, timestamp with time zone, timestamp with time zone)"
_COMMON_SIGNATURE = "(uuid, uuid, uuid, text, text, text, text, text, text, bigint, bytea, timestamp with time zone, timestamp with time zone)"


def _tenant_table(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_migrator_administration ON {table} "
        "FOR ALL TO context_engine_migrator USING (true) WITH CHECK (true)"
    )
    tenant = (
        "organization_id = NULLIF("
        "current_setting('app.organization_id', true), ''"
        ")::uuid"
    )
    op.execute(
        f"CREATE POLICY {table}_file_import_definer_select ON {table} "
        f"FOR SELECT TO {_DEFINER} USING ({tenant})"
    )
    op.execute(
        f"CREATE POLICY {table}_file_import_definer_insert ON {table} "
        f"FOR INSERT TO {_DEFINER} WITH CHECK ({tenant})"
    )


def _immutable(table: str) -> None:
    op.execute(
        f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION context_content_reject_mutation()"
    )


def upgrade() -> None:
    """Add one database-arbitrated no-op outcome before publication work."""

    op.create_unique_constraint(
        "uq_file_acquisition_identity_source",
        "file_acquisition",
        ["organization_id", "acquisition_id", "source_id"],
    )
    op.create_table(
        "file_resource_ingestion_guard",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_ref", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "source_id",
            "resource_ref",
            name="pk_file_resource_ingestion_guard",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "resource_ref",
            name="uq_file_resource_ingestion_guard_resource",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_id"],
            ["context_source.organization_id", "context_source.source_id"],
            name="fk_file_resource_ingestion_guard_source_same_organization",
        ),
        sa.CheckConstraint(
            "btrim(resource_ref) <> ''",
            name="ck_file_resource_ingestion_guard_resource_nonblank",
        ),
    )
    op.create_table(
        "file_acquisition_result",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("acquisition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_ref", sa.Text(), nullable=False),
        sa.Column("active_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("content_identity_digest", sa.Text(), nullable=False),
        sa.Column("reason_code", sa.Text(), nullable=True),
        sa.Column("reason_digest", sa.Text(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "acquisition_id",
            name="pk_file_acquisition_result",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "acquisition_id", "source_id"],
            [
                "file_acquisition.organization_id",
                "file_acquisition.acquisition_id",
                "file_acquisition.source_id",
            ],
            name="fk_file_acquisition_result_acquisition_source_same_organization",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_id", "resource_ref"],
            [
                "file_resource_ingestion_guard.organization_id",
                "file_resource_ingestion_guard.source_id",
                "file_resource_ingestion_guard.resource_ref",
            ],
            name="fk_file_acquisition_result_guard_same_organization",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "resource_ref", "active_revision_id"],
            [
                "context_revision.organization_id",
                "context_revision.resource_ref",
                "context_revision.revision_id",
            ],
            name="fk_file_acquisition_result_revision_same_organization",
        ),
        sa.CheckConstraint(
            "content_identity_digest ~ '^[0-9a-f]{64}$'",
            name="ck_file_acquisition_result_identity_digest",
        ),
        sa.CheckConstraint(
            "outcome = 'unchanged' "
            "AND reason_code = 'active-content-identity-match' "
            "AND reason_digest ~ '^[0-9a-f]{64}$'",
            name="ck_file_acquisition_result_outcome",
        ),
    )
    for table in ("file_resource_ingestion_guard", "file_acquisition_result"):
        _tenant_table(table)
        _immutable(table)
    tenant = (
        "organization_id = NULLIF("
        "current_setting('app.organization_id', true), ''"
        ")::uuid"
    )
    op.execute(
        "CREATE POLICY file_resource_ingestion_guard_file_import_definer_update "
        "ON file_resource_ingestion_guard FOR UPDATE "
        f"TO {_DEFINER} USING ({tenant}) WITH CHECK ({tenant})"
    )

    op.execute(
        "ALTER TABLE file_import_job "
        "DROP CONSTRAINT ck_file_import_job_state_consistency"
    )
    op.execute(
        """
        ALTER TABLE file_import_job
        ADD CONSTRAINT ck_file_import_job_state_consistency CHECK (
            (state = 'available' AND signing_key_version IS NULL
                AND lease_nonce_digest IS NULL AND lease_issued_at IS NULL
                AND lease_expires_at IS NULL AND lease_redeemed_at IS NULL
                AND failed_at IS NULL AND completed_at IS NULL
                AND resource_ref IS NULL AND revision_id IS NULL
                AND fragment_ref IS NULL AND effect_count = 0)
            OR (state = 'leased' AND signing_key_version > 0
                AND octet_length(lease_nonce_digest) = 32
                AND lease_issued_at IS NOT NULL
                AND lease_expires_at > lease_issued_at
                AND lease_redeemed_at IS NULL AND failed_at IS NULL
                AND completed_at IS NULL AND resource_ref IS NULL
                AND revision_id IS NULL AND fragment_ref IS NULL
                AND effect_count = 0)
            OR (state = 'running' AND signing_key_version > 0
                AND octet_length(lease_nonce_digest) = 32
                AND lease_issued_at IS NOT NULL
                AND lease_expires_at > lease_issued_at
                AND lease_redeemed_at >= lease_issued_at
                AND failed_at IS NULL AND completed_at IS NULL
                AND resource_ref IS NULL AND revision_id IS NULL
                AND fragment_ref IS NULL AND effect_count = 0)
            OR (state = 'failed' AND signing_key_version > 0
                AND octet_length(lease_nonce_digest) = 32
                AND lease_issued_at IS NOT NULL
                AND lease_expires_at > lease_issued_at
                AND lease_redeemed_at >= lease_issued_at
                AND failed_at >= lease_redeemed_at
                AND completed_at IS NULL AND resource_ref IS NULL
                AND revision_id IS NULL AND fragment_ref IS NULL
                AND effect_count = 0)
            OR (state = 'completed' AND signing_key_version > 0
                AND octet_length(lease_nonce_digest) = 32
                AND lease_issued_at IS NOT NULL
                AND lease_expires_at > lease_issued_at
                AND lease_redeemed_at >= lease_issued_at
                AND failed_at IS NULL AND completed_at >= lease_redeemed_at
                AND resource_ref IS NOT NULL AND revision_id IS NOT NULL
                AND fragment_ref IS NOT NULL AND effect_count IN (0, 1))
        )
        """
    )

    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"REVOKE EXECUTE ON FUNCTION "
        f"public.context_worker_publish_file_import{_V1_SIGNATURE} "
        f"FROM {_WORKER}"
    )
    op.execute(
        f"REVOKE EXECUTE ON FUNCTION "
        f"public.context_worker_publish_structural_file_import{_V2_SIGNATURE} "
        f"FROM {_WORKER}"
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")
    op.execute(
        f"GRANT SELECT, INSERT ON TABLE "
        "file_resource_ingestion_guard, file_acquisition_result "
        f"TO {_DEFINER}"
    )
    op.execute(
        "GRANT UPDATE (resource_ref) ON TABLE "
        f"file_resource_ingestion_guard TO {_DEFINER}"
    )
    for table in (
        "file_revision_snapshot",
        "context_fragment",
        "revision_publication_event",
        "exact_phrase_candidate",
        "resource_access_policy",
        "membership_resource_field_right",
    ):
        op.execute(
            f"CREATE POLICY {table}_file_noop_definer_select ON {table} "
            f"FOR SELECT TO {_DEFINER} USING ({tenant})"
        )
    op.execute(
        "GRANT SELECT ON TABLE file_revision_snapshot, context_fragment, "
        "revision_publication_event, exact_phrase_candidate, "
        "resource_access_policy, membership_resource_field_right "
        f"TO {_DEFINER}"
    )

    op.execute(
        f"""
        CREATE FUNCTION public.context_worker_classify_file_import_internal(
            requested_organization_id uuid, requested_job_id uuid,
            requested_service_principal_id uuid, requested_source_ref text,
            requested_resource_ref text, requested_canonical_text text,
            requested_content_hash text, requested_compiler_version text,
            requested_config_version text,
            requested_signing_key_version bigint, requested_nonce bytea,
            requested_issued_at timestamptz, requested_expires_at timestamptz
        ) RETURNS TABLE (
            classification text, active_revision_id uuid,
            fragment_refs text[], content_identity_digest text,
            reason_digest text
        )
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$
        DECLARE
            job_row public.file_import_job%ROWTYPE;
            active_revision uuid;
            active_fragments text[];
            identity_digest text;
            no_op_digest text;
            now_at timestamptz;
        BEGIN
            IF SESSION_USER <> '{_WORKER}'
               OR requested_content_hash !~ '^[0-9a-f]{{64}}$'
               OR encode(
                    public.digest(
                        pg_catalog.convert_to(requested_canonical_text, 'UTF8'),
                        'sha256'
                    ), 'hex'
                  ) <> requested_content_hash
               OR btrim(requested_resource_ref) = ''
               OR btrim(requested_compiler_version) = ''
               OR btrim(requested_config_version) = ''
            THEN RETURN; END IF;

            PERFORM pg_catalog.set_config(
                'app.organization_id', requested_organization_id::text, true
            );
            PERFORM pg_catalog.set_config(
                'app.worker_job_id', requested_job_id::text, true
            );
            now_at := pg_catalog.statement_timestamp();
            SELECT * INTO job_row FROM public.file_import_job AS job
            WHERE job.organization_id = requested_organization_id
              AND job.job_id = requested_job_id
              AND job.service_principal_id = requested_service_principal_id
              AND job.source_id::text = requested_source_ref
              AND job.state = 'running'
              AND job.signing_key_version = requested_signing_key_version
              AND job.lease_nonce_digest = public.digest(requested_nonce, 'sha256')
              AND job.lease_issued_at = requested_issued_at
              AND job.lease_expires_at = requested_expires_at
              AND now_at < job.lease_expires_at
              AND EXISTS (
                  SELECT 1 FROM public.service_principal AS principal
                  WHERE principal.organization_id = job.organization_id
                    AND principal.service_principal_id = job.service_principal_id
                    AND principal.workload = job.workload
                    AND principal.worker_audience = job.worker_audience
                    AND principal.operation = job.operation
                    AND principal.enabled IS TRUE
              )
            FOR UPDATE;
            IF job_row.job_id IS NULL THEN RETURN; END IF;

            INSERT INTO public.file_resource_ingestion_guard (
                organization_id, source_id, resource_ref, created_at
            ) VALUES (
                requested_organization_id, job_row.source_id,
                requested_resource_ref, now_at
            ) ON CONFLICT (organization_id, resource_ref) DO NOTHING;
            PERFORM 1 FROM public.file_resource_ingestion_guard AS guard
            WHERE guard.organization_id = requested_organization_id
              AND guard.source_id = job_row.source_id
              AND guard.resource_ref = requested_resource_ref
            FOR UPDATE;
            IF NOT FOUND THEN RETURN; END IF;

            identity_digest := encode(
                public.digest(
                    pg_catalog.convert_to(
                        'context-engine.file-content-identity.v1', 'UTF8'
                    ) || decode('00', 'hex')
                    || pg_catalog.uuid_send(requested_organization_id)
                    || pg_catalog.uuid_send(job_row.source_id)
                    || pg_catalog.convert_to(requested_resource_ref, 'UTF8')
                    || decode('00', 'hex')
                    || pg_catalog.convert_to(requested_content_hash, 'UTF8')
                    || decode('00', 'hex')
                    || pg_catalog.convert_to(requested_compiler_version, 'UTF8')
                    || decode('00', 'hex')
                    || pg_catalog.convert_to(requested_config_version, 'UTF8'),
                    'sha256'
                ), 'hex'
            );

            SELECT resource.active_revision_id,
                   array_agg(fragment.fragment_ref ORDER BY fragment.ordinal)
            INTO active_revision, active_fragments
            FROM public.context_resource AS resource
            JOIN public.file_revision_snapshot AS snapshot
              ON snapshot.organization_id = resource.organization_id
             AND snapshot.resource_ref = resource.resource_ref
             AND snapshot.revision_id = resource.active_revision_id
            JOIN public.context_fragment AS fragment
              ON fragment.organization_id = resource.organization_id
             AND fragment.resource_ref = resource.resource_ref
             AND fragment.revision_id = resource.active_revision_id
            WHERE resource.organization_id = requested_organization_id
              AND resource.source_ref = requested_source_ref
              AND resource.resource_ref = requested_resource_ref
              AND resource.tombstoned IS FALSE
              AND snapshot.content_hash = requested_content_hash
              AND snapshot.compiler_version = requested_compiler_version
              AND snapshot.config_version = requested_config_version
              AND (
                  SELECT array_agg(event.state ORDER BY event.ordinal)
                  FROM public.revision_publication_event AS event
                  WHERE event.organization_id = resource.organization_id
                    AND event.resource_ref = resource.resource_ref
                    AND event.revision_id = resource.active_revision_id
              ) = ARRAY['prepared', 'indexed', 'active']::text[]
              AND EXISTS (
                  SELECT 1
                  FROM public.file_acquisition AS acquisition
                  JOIN public.membership AS audience_membership
                    ON audience_membership.organization_id =
                        acquisition.organization_id
                   AND audience_membership.membership_id =
                        acquisition.audience_membership_id
                   AND audience_membership.membership_version =
                        acquisition.audience_membership_version
                   AND audience_membership.status = 'active'
                   AND audience_membership.valid_from <= now_at
                   AND (
                        audience_membership.valid_until IS NULL
                        OR audience_membership.valid_until > now_at
                   )
                  JOIN public.resource_access_policy AS access_policy
                    ON access_policy.organization_id =
                        acquisition.organization_id
                   AND access_policy.resource_ref = requested_resource_ref
                   AND access_policy.principal_ref =
                        acquisition.audience_principal_ref
                   AND access_policy.access_state = 'allowed'
                  JOIN public.membership_resource_field_right AS field_right
                    ON field_right.organization_id =
                        acquisition.organization_id
                   AND field_right.membership_id =
                        acquisition.audience_membership_id
                   AND field_right.membership_version =
                        acquisition.audience_membership_version
                   AND field_right.resource_ref = requested_resource_ref
                   AND field_right.field_ref = 'body'
                  WHERE acquisition.organization_id =
                      requested_organization_id
                    AND acquisition.acquisition_id = job_row.acquisition_id
                    AND acquisition.source_id = job_row.source_id
              )
            GROUP BY resource.active_revision_id;

            IF active_revision IS NOT NULL THEN
                no_op_digest := encode(
                    public.digest(
                        pg_catalog.convert_to(
                            'context-engine.file-no-op-reason.v1', 'UTF8'
                        ) || decode('00', 'hex')
                        || pg_catalog.convert_to(
                            'active-content-identity-match', 'UTF8'
                        ) || decode('00', 'hex')
                        || decode(identity_digest, 'hex')
                        || pg_catalog.uuid_send(active_revision),
                        'sha256'
                    ), 'hex'
                );
                INSERT INTO public.file_acquisition_result (
                    organization_id, acquisition_id, source_id,
                    resource_ref, active_revision_id, outcome,
                    content_identity_digest, reason_code, reason_digest,
                    observed_at
                ) VALUES (
                    requested_organization_id, job_row.acquisition_id,
                    job_row.source_id, requested_resource_ref, active_revision,
                    'unchanged', identity_digest,
                    'active-content-identity-match', no_op_digest, now_at
                );
                UPDATE public.file_import_job AS job
                SET state = 'completed', completed_at = now_at,
                    resource_ref = requested_resource_ref,
                    revision_id = active_revision,
                    fragment_ref = active_fragments[1], effect_count = 0
                WHERE job.organization_id = requested_organization_id
                  AND job.job_id = requested_job_id AND job.state = 'running';
                IF NOT FOUND THEN RETURN; END IF;
                RETURN QUERY SELECT 'unchanged'::text, active_revision,
                    active_fragments, identity_digest, no_op_digest;
                RETURN;
            END IF;

            IF EXISTS (
                SELECT 1 FROM public.context_resource AS resource
                WHERE resource.organization_id = requested_organization_id
                  AND resource.resource_ref = requested_resource_ref
                  AND resource.active_revision_id IS NOT NULL
            ) THEN
                RETURN QUERY SELECT 'changed'::text, NULL::uuid, NULL::text[],
                    identity_digest, NULL::text;
                RETURN;
            END IF;

            RETURN QUERY SELECT 'publish'::text, NULL::uuid, NULL::text[],
                identity_digest, NULL::text;
        END; $function$
        """
    )

    op.execute(
        f"""
        CREATE FUNCTION public.context_worker_publish_file_import_v2(
            requested_organization_id uuid, requested_job_id uuid,
            requested_service_principal_id uuid, requested_source_ref text,
            requested_resource_ref text, requested_revision_id uuid,
            requested_fragment_ref text, requested_canonical_text text,
            requested_paragraph text, requested_content_hash text,
            requested_compilation_digest text, requested_compiler_version text,
            requested_config_version text, requested_phrase_digest text,
            requested_signing_key_version bigint, requested_nonce bytea,
            requested_issued_at timestamptz, requested_expires_at timestamptz
        ) RETURNS TABLE (
            effect_count smallint, outcome text, active_revision_id uuid,
            fragment_refs text[], content_identity_digest text,
            reason_digest text
        )
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$
        DECLARE decision record; published_effect smallint;
        BEGIN
            IF SESSION_USER <> '{_WORKER}'
               OR requested_compiler_version <> 'context-engine-markdown-v1'
               OR requested_config_version <> 'markdown-config-v1'
               OR requested_compilation_digest !~ '^[0-9a-f]{{64}}$'
               OR requested_phrase_digest !~ '^[0-9a-f]{{64}}$'
            THEN RETURN; END IF;
            SELECT * INTO decision
            FROM public.context_worker_classify_file_import_internal(
                requested_organization_id, requested_job_id,
                requested_service_principal_id, requested_source_ref,
                requested_resource_ref, requested_canonical_text,
                requested_content_hash, requested_compiler_version,
                requested_config_version, requested_signing_key_version,
                requested_nonce, requested_issued_at, requested_expires_at
            );
            IF decision.classification = 'unchanged' THEN
                IF NOT EXISTS (
                    SELECT 1
                    FROM public.file_revision_snapshot AS snapshot
                    JOIN public.context_fragment AS fragment
                      ON fragment.organization_id = snapshot.organization_id
                     AND fragment.resource_ref = snapshot.resource_ref
                     AND fragment.revision_id = snapshot.revision_id
                    JOIN public.exact_phrase_candidate AS candidate
                      ON candidate.organization_id = fragment.organization_id
                     AND candidate.resource_ref = fragment.resource_ref
                     AND candidate.revision_id = fragment.revision_id
                     AND candidate.fragment_ref = fragment.fragment_ref
                    WHERE snapshot.organization_id = requested_organization_id
                      AND snapshot.resource_ref = requested_resource_ref
                      AND snapshot.revision_id = decision.active_revision_id
                      AND snapshot.compilation_digest =
                          requested_compilation_digest
                      AND fragment.fragment_ref = requested_fragment_ref
                      AND fragment.ordinal = 0
                      AND fragment.content = requested_paragraph
                      AND fragment.projection_kind = 'body'
                      AND candidate.source_ref = requested_source_ref
                      AND candidate.phrase_digest = requested_phrase_digest
                      AND (
                          SELECT count(*)
                          FROM public.context_fragment AS active_fragment
                          WHERE active_fragment.organization_id =
                              requested_organization_id
                            AND active_fragment.resource_ref =
                              requested_resource_ref
                            AND active_fragment.revision_id =
                              decision.active_revision_id
                      ) = 1
                      AND (
                          SELECT count(*)
                          FROM public.exact_phrase_candidate AS active_candidate
                          WHERE active_candidate.organization_id =
                              requested_organization_id
                            AND active_candidate.resource_ref =
                              requested_resource_ref
                            AND active_candidate.revision_id =
                              decision.active_revision_id
                      ) = 1
                ) THEN
                    RAISE EXCEPTION 'v1 no-op payload is not the active artifact'
                        USING ERRCODE = '22023';
                END IF;
                RETURN QUERY SELECT 0::smallint, decision.classification,
                    decision.active_revision_id, decision.fragment_refs,
                    decision.content_identity_digest, decision.reason_digest;
                RETURN;
            ELSIF decision.classification IS DISTINCT FROM 'publish' THEN
                RETURN;
            END IF;
            SELECT legacy.effect_count INTO published_effect
            FROM public.context_worker_publish_file_import(
                requested_organization_id, requested_job_id,
                requested_service_principal_id, requested_source_ref,
                requested_resource_ref, requested_revision_id,
                requested_fragment_ref, requested_canonical_text,
                requested_paragraph, requested_content_hash,
                requested_compilation_digest, requested_compiler_version,
                requested_config_version, requested_phrase_digest,
                requested_signing_key_version, requested_nonce,
                requested_issued_at, requested_expires_at
            ) AS legacy;
            IF published_effect IS DISTINCT FROM 1 THEN RETURN; END IF;
            RETURN QUERY SELECT 1::smallint, 'published'::text,
                requested_revision_id, ARRAY[requested_fragment_ref]::text[],
                decision.content_identity_digest, NULL::text;
        END; $function$
        """
    )

    op.execute(
        f"""
        CREATE FUNCTION public.context_worker_publish_structural_file_import_v2(
            requested_organization_id uuid, requested_job_id uuid,
            requested_service_principal_id uuid, requested_source_ref text,
            requested_resource_ref text, requested_revision_id uuid,
            requested_canonical_text text, requested_content_hash text,
            requested_compilation_digest text, requested_compiler_version text,
            requested_config_version text, requested_compilation_document jsonb,
            requested_signing_key_version bigint, requested_nonce bytea,
            requested_issued_at timestamptz, requested_expires_at timestamptz
        ) RETURNS TABLE (
            effect_count smallint, outcome text, active_revision_id uuid,
            fragment_refs text[], content_identity_digest text,
            reason_digest text
        )
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$
        DECLARE
            decision record;
            published_effect smallint;
            published_fragments text[];
        BEGIN
            IF SESSION_USER <> '{_WORKER}'
               OR requested_compiler_version <> 'context-engine-markdown-v2'
               OR requested_config_version <> 'markdown-config-v2'
               OR requested_compilation_digest !~ '^[0-9a-f]{{64}}$'
               OR jsonb_typeof(requested_compilation_document)
                    IS DISTINCT FROM 'object'
               OR requested_compilation_document->>'canonicalText'
                    IS DISTINCT FROM requested_canonical_text
               OR requested_compilation_document->>'contentHash'
                    IS DISTINCT FROM requested_content_hash
               OR requested_compilation_document->>'compilationDigest'
                    IS DISTINCT FROM requested_compilation_digest
               OR requested_compilation_document#>>'{{provenance,compilerVersion}}'
                    IS DISTINCT FROM requested_compiler_version
               OR requested_compilation_document#>>'{{provenance,configVersion}}'
                    IS DISTINCT FROM requested_config_version
               OR jsonb_typeof(requested_compilation_document->'fragments')
                    IS DISTINCT FROM 'array'
            THEN RETURN; END IF;
            SELECT * INTO decision
            FROM public.context_worker_classify_file_import_internal(
                requested_organization_id, requested_job_id,
                requested_service_principal_id, requested_source_ref,
                requested_resource_ref, requested_canonical_text,
                requested_content_hash, requested_compiler_version,
                requested_config_version, requested_signing_key_version,
                requested_nonce, requested_issued_at, requested_expires_at
            );
            IF decision.classification = 'unchanged' THEN
                IF NOT EXISTS (
                    SELECT 1
                    FROM public.file_revision_snapshot AS snapshot
                    WHERE snapshot.organization_id = requested_organization_id
                      AND snapshot.resource_ref = requested_resource_ref
                      AND snapshot.revision_id = decision.active_revision_id
                      AND snapshot.compilation_digest =
                          requested_compilation_digest
                      AND snapshot.compilation_document =
                          requested_compilation_document
                      AND (
                          SELECT count(*)
                          FROM public.context_fragment AS active_fragment
                          WHERE active_fragment.organization_id =
                              requested_organization_id
                            AND active_fragment.resource_ref =
                              requested_resource_ref
                            AND active_fragment.revision_id =
                              decision.active_revision_id
                      ) = jsonb_array_length(
                          requested_compilation_document->'fragments'
                      )
                      AND NOT EXISTS (
                          SELECT 1
                          FROM jsonb_array_elements(
                              requested_compilation_document->'fragments'
                          ) WITH ORDINALITY AS expected(fragment, ordinal)
                          WHERE NOT EXISTS (
                              SELECT 1
                              FROM public.context_fragment AS active_fragment
                              WHERE active_fragment.organization_id =
                                  requested_organization_id
                                AND active_fragment.resource_ref =
                                  requested_resource_ref
                                AND active_fragment.revision_id =
                                  decision.active_revision_id
                                AND active_fragment.fragment_ref =
                                  expected.fragment->>'fragmentRef'
                                AND active_fragment.ordinal =
                                  (expected.ordinal - 1)::integer
                                AND active_fragment.content =
                                  expected.fragment->>'contextualText'
                                AND active_fragment.projection_kind = 'body'
                          )
                      )
                      AND NOT EXISTS (
                          SELECT 1
                          FROM jsonb_array_elements(
                              requested_compilation_document->'fragments'
                          ) AS expected(fragment)
                          CROSS JOIN LATERAL jsonb_array_elements_text(
                              expected.fragment->'searchPhrases'
                          ) AS phrase(value)
                          WHERE NOT EXISTS (
                              SELECT 1
                              FROM public.exact_phrase_candidate AS candidate
                              WHERE candidate.organization_id =
                                  requested_organization_id
                                AND candidate.source_ref = requested_source_ref
                                AND candidate.resource_ref =
                                  requested_resource_ref
                                AND candidate.revision_id =
                                  decision.active_revision_id
                                AND candidate.fragment_ref =
                                  expected.fragment->>'fragmentRef'
                                AND candidate.phrase_digest = encode(
                                    public.digest(
                                        pg_catalog.convert_to(
                                            'context-engine.exact-phrase.v1',
                                            'UTF8'
                                        ) || decode('00', 'hex')
                                        || pg_catalog.convert_to(
                                            phrase.value, 'UTF8'
                                        ),
                                        'sha256'
                                    ),
                                    'hex'
                                )
                          )
                      )
                      AND (
                          SELECT count(*)
                          FROM public.exact_phrase_candidate AS active_candidate
                          WHERE active_candidate.organization_id =
                              requested_organization_id
                            AND active_candidate.resource_ref =
                              requested_resource_ref
                            AND active_candidate.revision_id =
                              decision.active_revision_id
                      ) = (
                          SELECT count(*)
                          FROM jsonb_array_elements(
                              requested_compilation_document->'fragments'
                          ) AS expected(fragment)
                          CROSS JOIN LATERAL jsonb_array_elements_text(
                              expected.fragment->'searchPhrases'
                          ) AS phrase(value)
                      )
                ) THEN
                    RAISE EXCEPTION 'v2 no-op payload is not the active artifact'
                        USING ERRCODE = '22023';
                END IF;
                RETURN QUERY SELECT 0::smallint, decision.classification,
                    decision.active_revision_id, decision.fragment_refs,
                    decision.content_identity_digest, decision.reason_digest;
                RETURN;
            ELSIF decision.classification IS DISTINCT FROM 'publish' THEN
                RETURN;
            END IF;
            SELECT legacy.effect_count INTO published_effect
            FROM public.context_worker_publish_structural_file_import(
                requested_organization_id, requested_job_id,
                requested_service_principal_id, requested_source_ref,
                requested_resource_ref, requested_revision_id,
                requested_canonical_text, requested_content_hash,
                requested_compilation_digest, requested_compiler_version,
                requested_config_version, requested_compilation_document,
                requested_signing_key_version, requested_nonce,
                requested_issued_at, requested_expires_at
            ) AS legacy;
            IF published_effect IS DISTINCT FROM 1 THEN RETURN; END IF;
            SELECT array_agg(fragment.fragment_ref ORDER BY fragment.ordinal)
            INTO published_fragments
            FROM public.context_fragment AS fragment
            WHERE fragment.organization_id = requested_organization_id
              AND fragment.resource_ref = requested_resource_ref
              AND fragment.revision_id = requested_revision_id;
            IF published_fragments IS NULL THEN RETURN; END IF;
            RETURN QUERY SELECT 1::smallint, 'published'::text,
                requested_revision_id, published_fragments,
                decision.content_identity_digest, NULL::text;
        END; $function$
        """
    )

    new_functions = (
        (
            "context_worker_classify_file_import_internal",
            _COMMON_SIGNATURE,
            None,
        ),
        ("context_worker_publish_file_import_v2", _V1_SIGNATURE, _WORKER),
        (
            "context_worker_publish_structural_file_import_v2",
            _V2_SIGNATURE,
            _WORKER,
        ),
    )
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    for name, signature, _grantee in new_functions:
        op.execute(f"REVOKE ALL ON FUNCTION public.{name}{signature} FROM PUBLIC")
        op.execute(f"ALTER FUNCTION public.{name}{signature} OWNER TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    for name, signature, grantee in new_functions:
        if grantee is not None:
            op.execute(
                f"GRANT EXECUTE ON FUNCTION public.{name}{signature} TO {grantee}"
            )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


def downgrade() -> None:
    """Remove no-op arbitration only when no unchanged result would be lost."""

    unchanged_results = bool(
        op.get_bind()
        .execute(sa.text("SELECT EXISTS (SELECT 1 FROM file_acquisition_result)"))
        .scalar_one()
    )
    if unchanged_results:
        raise RuntimeError("File no-op downgrade requires no unchanged results")

    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"REVOKE EXECUTE ON FUNCTION "
        f"public.context_worker_publish_file_import_v2{_V1_SIGNATURE} "
        f"FROM {_WORKER}"
    )
    op.execute(
        f"REVOKE EXECUTE ON FUNCTION "
        "public.context_worker_publish_structural_file_import_v2"
        f"{_V2_SIGNATURE} FROM {_WORKER}"
    )
    op.execute(
        f"DROP FUNCTION public.context_worker_publish_file_import_v2{_V1_SIGNATURE}"
    )
    op.execute(
        "DROP FUNCTION public.context_worker_publish_structural_file_import_v2"
        f"{_V2_SIGNATURE}"
    )
    op.execute(
        "DROP FUNCTION public.context_worker_classify_file_import_internal"
        f"{_COMMON_SIGNATURE}"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION "
        f"public.context_worker_publish_file_import{_V1_SIGNATURE} TO {_WORKER}"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION "
        f"public.context_worker_publish_structural_file_import{_V2_SIGNATURE} "
        f"TO {_WORKER}"
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")

    for table in (
        "file_revision_snapshot",
        "context_fragment",
        "revision_publication_event",
        "exact_phrase_candidate",
        "resource_access_policy",
        "membership_resource_field_right",
    ):
        op.execute(f"DROP POLICY {table}_file_noop_definer_select ON {table}")

    op.execute(
        "ALTER TABLE file_import_job "
        "DROP CONSTRAINT ck_file_import_job_state_consistency"
    )
    op.execute(
        """
        ALTER TABLE file_import_job
        ADD CONSTRAINT ck_file_import_job_state_consistency CHECK (
            (state = 'available' AND signing_key_version IS NULL
                AND lease_nonce_digest IS NULL AND lease_issued_at IS NULL
                AND lease_expires_at IS NULL AND lease_redeemed_at IS NULL
                AND failed_at IS NULL AND completed_at IS NULL
                AND resource_ref IS NULL AND revision_id IS NULL
                AND fragment_ref IS NULL AND effect_count = 0)
            OR (state = 'leased' AND signing_key_version > 0
                AND octet_length(lease_nonce_digest) = 32
                AND lease_issued_at IS NOT NULL
                AND lease_expires_at > lease_issued_at
                AND lease_redeemed_at IS NULL AND failed_at IS NULL
                AND completed_at IS NULL AND resource_ref IS NULL
                AND revision_id IS NULL AND fragment_ref IS NULL
                AND effect_count = 0)
            OR (state = 'running' AND signing_key_version > 0
                AND octet_length(lease_nonce_digest) = 32
                AND lease_issued_at IS NOT NULL
                AND lease_expires_at > lease_issued_at
                AND lease_redeemed_at >= lease_issued_at
                AND failed_at IS NULL AND completed_at IS NULL
                AND resource_ref IS NULL AND revision_id IS NULL
                AND fragment_ref IS NULL AND effect_count = 0)
            OR (state = 'failed' AND signing_key_version > 0
                AND octet_length(lease_nonce_digest) = 32
                AND lease_issued_at IS NOT NULL
                AND lease_expires_at > lease_issued_at
                AND lease_redeemed_at >= lease_issued_at
                AND failed_at >= lease_redeemed_at
                AND completed_at IS NULL AND resource_ref IS NULL
                AND revision_id IS NULL AND fragment_ref IS NULL
                AND effect_count = 0)
            OR (state = 'completed' AND signing_key_version > 0
                AND octet_length(lease_nonce_digest) = 32
                AND lease_issued_at IS NOT NULL
                AND lease_expires_at > lease_issued_at
                AND lease_redeemed_at >= lease_issued_at
                AND failed_at IS NULL AND completed_at >= lease_redeemed_at
                AND resource_ref IS NOT NULL AND revision_id IS NOT NULL
                AND fragment_ref IS NOT NULL AND effect_count = 1)
        )
        """
    )
    op.drop_table("file_acquisition_result")
    op.drop_table("file_resource_ingestion_guard")
    op.drop_constraint(
        "uq_file_acquisition_identity_source",
        "file_acquisition",
        type_="unique",
    )
