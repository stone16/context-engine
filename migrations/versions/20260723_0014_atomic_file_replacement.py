"""Stage and atomically activate one changed File Revision.

Revision ID: 20260723_0014
Revises: 20260724_0013
Create Date: 2026-07-23
"""

# ruff: noqa: E501

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260723_0014"
down_revision: str | None = "20260724_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WORKER = "context_engine_worker"
_DEFINER = "context_engine_worker_lease_definer"
_STAGE_V1_SIGNATURE = "(uuid, uuid, uuid, text, text, uuid, text, text, text, text, text, text, text, text, bigint, bytea, timestamp with time zone, timestamp with time zone)"
_STAGE_V2_SIGNATURE = "(uuid, uuid, uuid, text, text, uuid, text, text, text, text, text, jsonb, bigint, bytea, timestamp with time zone, timestamp with time zone)"
_ACTIVATE_SIGNATURE = "(uuid, uuid, uuid, text, text, uuid, uuid, bigint, bytea, timestamp with time zone, timestamp with time zone)"


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
    """Add a durable ready boundary and atomic active-pointer replacement."""

    op.create_table(
        "file_revision_replacement_plan",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_ref", sa.Text(), nullable=False),
        sa.Column(
            "previous_revision_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "replacement_revision_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("acquisition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_identity_digest", sa.Text(), nullable=False),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "resource_ref",
            "replacement_revision_id",
            name="pk_file_revision_replacement_plan",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "job_id",
            name="uq_file_revision_replacement_plan_job",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "resource_ref",
            "previous_revision_id",
            name="uq_file_revision_replacement_plan_previous",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_id", "resource_ref"],
            [
                "file_resource_ingestion_guard.organization_id",
                "file_resource_ingestion_guard.source_id",
                "file_resource_ingestion_guard.resource_ref",
            ],
            name="fk_file_revision_replacement_plan_guard_same_organization",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "resource_ref", "previous_revision_id"],
            [
                "context_revision.organization_id",
                "context_revision.resource_ref",
                "context_revision.revision_id",
            ],
            name="fk_file_revision_replacement_plan_previous_same_organization",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "resource_ref", "replacement_revision_id"],
            [
                "context_revision.organization_id",
                "context_revision.resource_ref",
                "context_revision.revision_id",
            ],
            name="fk_file_revision_replacement_plan_replacement_same_organization",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "acquisition_id", "source_id"],
            [
                "file_acquisition.organization_id",
                "file_acquisition.acquisition_id",
                "file_acquisition.source_id",
            ],
            name="fk_file_revision_replacement_plan_acquisition_same_organization",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id"],
            ["file_import_job.organization_id", "file_import_job.job_id"],
            name="fk_file_revision_replacement_plan_job_same_organization",
        ),
        sa.CheckConstraint(
            "previous_revision_id <> replacement_revision_id",
            name="ck_file_revision_replacement_plan_distinct_revisions",
        ),
        sa.CheckConstraint(
            "content_identity_digest ~ '^[0-9a-f]{64}$'",
            name="ck_file_revision_replacement_plan_identity_digest",
        ),
    )
    op.create_table(
        "file_revision_supersession",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_ref", sa.Text(), nullable=False),
        sa.Column(
            "superseded_revision_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "replacement_revision_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("acquisition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("retention_state", sa.Text(), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "resource_ref",
            "superseded_revision_id",
            name="pk_file_revision_supersession",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "resource_ref",
            "replacement_revision_id",
            name="uq_file_revision_supersession_replacement",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "resource_ref", "replacement_revision_id"],
            [
                "file_revision_replacement_plan.organization_id",
                "file_revision_replacement_plan.resource_ref",
                "file_revision_replacement_plan.replacement_revision_id",
            ],
            name="fk_file_revision_supersession_plan_same_organization",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "resource_ref", "superseded_revision_id"],
            [
                "context_revision.organization_id",
                "context_revision.resource_ref",
                "context_revision.revision_id",
            ],
            name="fk_file_revision_supersession_previous_same_organization",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "acquisition_id"],
            ["file_acquisition.organization_id", "file_acquisition.acquisition_id"],
            name="fk_file_revision_supersession_acquisition_same_organization",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id"],
            ["file_import_job.organization_id", "file_import_job.job_id"],
            name="fk_file_revision_supersession_job_same_organization",
        ),
        sa.CheckConstraint(
            "superseded_revision_id <> replacement_revision_id",
            name="ck_file_revision_supersession_distinct_revisions",
        ),
        sa.CheckConstraint(
            "retention_state = 'retained_until_explicit_cleanup'",
            name="ck_file_revision_supersession_retention_state",
        ),
    )
    for table in (
        "file_revision_replacement_plan",
        "file_revision_supersession",
    ):
        _tenant_table(table)
        _immutable(table)

    op.drop_constraint(
        "ck_file_import_job_state", "file_import_job", type_="check"
    )
    op.create_check_constraint(
        "ck_file_import_job_state",
        "file_import_job",
        "state IN ('available', 'leased', 'running', 'ready', 'failed', 'completed')",
    )
    op.drop_constraint(
        "ck_file_import_job_state_consistency",
        "file_import_job",
        type_="check",
    )
    op.create_check_constraint(
        "ck_file_import_job_state_consistency",
        "file_import_job",
        "(state = 'available' AND signing_key_version IS NULL AND lease_nonce_digest IS NULL AND lease_issued_at IS NULL AND lease_expires_at IS NULL AND lease_redeemed_at IS NULL AND failed_at IS NULL AND completed_at IS NULL AND resource_ref IS NULL AND revision_id IS NULL AND fragment_ref IS NULL AND effect_count = 0) OR "
        "(state = 'leased' AND signing_key_version > 0 AND octet_length(lease_nonce_digest) = 32 AND lease_issued_at IS NOT NULL AND lease_expires_at > lease_issued_at AND lease_redeemed_at IS NULL AND failed_at IS NULL AND completed_at IS NULL AND resource_ref IS NULL AND revision_id IS NULL AND fragment_ref IS NULL AND effect_count = 0) OR "
        "(state = 'running' AND signing_key_version > 0 AND octet_length(lease_nonce_digest) = 32 AND lease_issued_at IS NOT NULL AND lease_expires_at > lease_issued_at AND lease_redeemed_at >= lease_issued_at AND failed_at IS NULL AND completed_at IS NULL AND resource_ref IS NULL AND revision_id IS NULL AND fragment_ref IS NULL AND effect_count = 0) OR "
        "(state = 'ready' AND signing_key_version > 0 AND octet_length(lease_nonce_digest) = 32 AND lease_issued_at IS NOT NULL AND lease_expires_at > lease_issued_at AND lease_redeemed_at >= lease_issued_at AND failed_at IS NULL AND completed_at IS NULL AND resource_ref IS NOT NULL AND revision_id IS NOT NULL AND fragment_ref IS NOT NULL AND effect_count = 0) OR "
        "(state = 'failed' AND signing_key_version > 0 AND octet_length(lease_nonce_digest) = 32 AND lease_issued_at IS NOT NULL AND lease_expires_at > lease_issued_at AND lease_redeemed_at >= lease_issued_at AND failed_at >= lease_redeemed_at AND completed_at IS NULL AND resource_ref IS NULL AND revision_id IS NULL AND fragment_ref IS NULL AND effect_count = 0) OR "
        "(state = 'completed' AND signing_key_version > 0 AND octet_length(lease_nonce_digest) = 32 AND lease_issued_at IS NOT NULL AND lease_expires_at > lease_issued_at AND lease_redeemed_at >= lease_issued_at AND failed_at IS NULL AND completed_at >= lease_redeemed_at AND resource_ref IS NOT NULL AND revision_id IS NOT NULL AND fragment_ref IS NOT NULL AND effect_count IN (0, 1))",
    )

    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"""
        CREATE FUNCTION public.context_worker_stage_file_replacement(
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
            previous_revision_id uuid, replacement_revision_id uuid,
            fragment_refs text[], content_identity_digest text
        )
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$
        DECLARE
            decision record;
            job_row public.file_import_job%ROWTYPE;
            old_revision uuid;
            now_at timestamptz;
        BEGIN
            IF SESSION_USER <> '{_WORKER}'
               OR requested_revision_id IS NULL
               OR requested_fragment_ref <> 'fragment:paragraph:1'
               OR btrim(requested_paragraph) = ''
               OR requested_content_hash !~ '^[0-9a-f]{{64}}$'
               OR requested_compilation_digest !~ '^[0-9a-f]{{64}}$'
               OR requested_phrase_digest !~ '^[0-9a-f]{{64}}$'
               OR requested_compiler_version <> 'context-engine-markdown-v1'
               OR requested_config_version <> 'markdown-config-v1'
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
            IF decision.classification IS DISTINCT FROM 'changed' THEN
                RETURN;
            END IF;
            now_at := pg_catalog.statement_timestamp();
            SELECT * INTO job_row FROM public.file_import_job AS job
            WHERE job.organization_id = requested_organization_id
              AND job.job_id = requested_job_id
              AND job.state = 'running'
            FOR UPDATE;
            IF job_row.job_id IS NULL THEN RETURN; END IF;
            IF NOT EXISTS (
                SELECT 1
                FROM public.file_acquisition AS acquisition
                JOIN public.membership AS audience_membership
                  ON audience_membership.organization_id = acquisition.organization_id
                 AND audience_membership.membership_id = acquisition.audience_membership_id
                 AND audience_membership.membership_version = acquisition.audience_membership_version
                 AND audience_membership.status = 'active'
                 AND audience_membership.valid_from <= now_at
                 AND (audience_membership.valid_until IS NULL OR audience_membership.valid_until > now_at)
                JOIN public.resource_access_policy AS access_policy
                  ON access_policy.organization_id = acquisition.organization_id
                 AND access_policy.resource_ref = requested_resource_ref
                 AND access_policy.principal_ref = acquisition.audience_principal_ref
                 AND access_policy.access_state = 'allowed'
                JOIN public.membership_resource_field_right AS field_right
                  ON field_right.organization_id = acquisition.organization_id
                 AND field_right.membership_id = acquisition.audience_membership_id
                 AND field_right.membership_version = acquisition.audience_membership_version
                 AND field_right.resource_ref = requested_resource_ref
                 AND field_right.field_ref = 'body'
                WHERE acquisition.organization_id = requested_organization_id
                  AND acquisition.acquisition_id = job_row.acquisition_id
                  AND acquisition.source_id = job_row.source_id
            ) THEN RETURN; END IF;
            SELECT resource.active_revision_id INTO old_revision
            FROM public.context_resource AS resource
            JOIN public.file_revision_snapshot AS snapshot
              ON snapshot.organization_id = resource.organization_id
             AND snapshot.resource_ref = resource.resource_ref
             AND snapshot.revision_id = resource.active_revision_id
            WHERE resource.organization_id = requested_organization_id
              AND resource.source_ref = requested_source_ref
              AND resource.resource_ref = requested_resource_ref
              AND resource.active_revision_id IS NOT NULL
              AND resource.tombstoned IS FALSE
              AND (
                  SELECT array_agg(event.state ORDER BY event.ordinal)
                  FROM public.revision_publication_event AS event
                  WHERE event.organization_id = resource.organization_id
                    AND event.resource_ref = resource.resource_ref
                    AND event.revision_id = resource.active_revision_id
              ) = ARRAY['prepared', 'indexed', 'active']::text[]
              AND EXISTS (
                  SELECT 1 FROM public.context_fragment AS fragment
                  WHERE fragment.organization_id = resource.organization_id
                    AND fragment.resource_ref = resource.resource_ref
                    AND fragment.revision_id = resource.active_revision_id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM public.context_fragment AS fragment
                  WHERE fragment.organization_id = resource.organization_id
                    AND fragment.resource_ref = resource.resource_ref
                    AND fragment.revision_id = resource.active_revision_id
                    AND NOT EXISTS (
                        SELECT 1 FROM public.exact_phrase_candidate AS candidate
                        WHERE candidate.organization_id = fragment.organization_id
                          AND candidate.resource_ref = fragment.resource_ref
                          AND candidate.revision_id = fragment.revision_id
                          AND candidate.fragment_ref = fragment.fragment_ref
                    )
              )
            FOR UPDATE OF resource;
            IF old_revision IS NULL OR old_revision = requested_revision_id THEN
                RETURN;
            END IF;

            INSERT INTO public.context_revision VALUES (
                requested_organization_id, requested_resource_ref,
                requested_revision_id
            );
            INSERT INTO public.file_revision_snapshot (
                organization_id, resource_ref, revision_id, acquisition_id,
                canonical_text, content_hash, compilation_digest,
                compiler_version, config_version, compilation_document
            ) VALUES (
                requested_organization_id, requested_resource_ref,
                requested_revision_id, job_row.acquisition_id,
                requested_canonical_text, requested_content_hash,
                requested_compilation_digest, requested_compiler_version,
                requested_config_version, NULL
            );
            INSERT INTO public.context_fragment VALUES (
                requested_organization_id, requested_resource_ref,
                requested_revision_id, requested_fragment_ref, 0,
                requested_paragraph, 'body'
            );
            INSERT INTO public.revision_publication_event VALUES (
                requested_organization_id, requested_resource_ref,
                requested_revision_id, 0, 'prepared', now_at
            );
            INSERT INTO public.exact_phrase_candidate VALUES (
                requested_organization_id, requested_phrase_digest,
                requested_source_ref, requested_resource_ref,
                requested_revision_id, requested_fragment_ref
            );
            INSERT INTO public.revision_publication_event VALUES (
                requested_organization_id, requested_resource_ref,
                requested_revision_id, 1, 'indexed', now_at
            );
            INSERT INTO public.file_revision_replacement_plan (
                organization_id, source_id, resource_ref,
                previous_revision_id, replacement_revision_id,
                acquisition_id, job_id, content_identity_digest, prepared_at
            ) VALUES (
                requested_organization_id, job_row.source_id,
                requested_resource_ref, old_revision, requested_revision_id,
                job_row.acquisition_id, requested_job_id,
                decision.content_identity_digest, now_at
            );
            UPDATE public.file_import_job
            SET state = 'ready', resource_ref = requested_resource_ref,
                revision_id = requested_revision_id,
                fragment_ref = requested_fragment_ref
            WHERE organization_id = requested_organization_id
              AND job_id = requested_job_id AND state = 'running';
            IF NOT FOUND THEN RETURN; END IF;
            RETURN QUERY SELECT old_revision, requested_revision_id,
                ARRAY[requested_fragment_ref]::text[],
                decision.content_identity_digest;
        END; $function$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION public.context_worker_stage_structural_file_replacement(
            requested_organization_id uuid, requested_job_id uuid,
            requested_service_principal_id uuid, requested_source_ref text,
            requested_resource_ref text, requested_revision_id uuid,
            requested_canonical_text text, requested_content_hash text,
            requested_compilation_digest text, requested_compiler_version text,
            requested_config_version text, requested_compilation_document jsonb,
            requested_signing_key_version bigint, requested_nonce bytea,
            requested_issued_at timestamptz, requested_expires_at timestamptz
        ) RETURNS TABLE (
            previous_revision_id uuid, replacement_revision_id uuid,
            fragment_refs text[], content_identity_digest text
        )
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$
        DECLARE
            decision record;
            job_row public.file_import_job%ROWTYPE;
            old_revision uuid;
            first_fragment text;
            ready_fragments text[];
            now_at timestamptz;
            fragment_count integer;
        BEGIN
            IF SESSION_USER <> '{_WORKER}'
               OR requested_revision_id IS NULL
               OR requested_content_hash !~ '^[0-9a-f]{{64}}$'
               OR requested_compilation_digest !~ '^[0-9a-f]{{64}}$'
               OR requested_compiler_version <> 'context-engine-markdown-v2'
               OR requested_config_version <> 'markdown-config-v2'
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
               OR requested_compilation_document#>>'{{provenance,canonicalizationProfile}}'
                    IS DISTINCT FROM 'markdown-structural-units-v2'
               OR requested_compilation_document#>>'{{provenance,compilationDigestProfile}}'
                    IS DISTINCT FROM 'rfc8785-sha256-v2'
               OR jsonb_typeof(requested_compilation_document->'sections')
                    IS DISTINCT FROM 'array'
               OR jsonb_typeof(requested_compilation_document->'fragments')
                    IS DISTINCT FROM 'array'
            THEN RETURN; END IF;

            fragment_count := jsonb_array_length(
                requested_compilation_document->'fragments'
            );
            IF fragment_count NOT BETWEEN 1 AND 4096
               OR jsonb_array_length(requested_compilation_document->'sections')
                    <> fragment_count
               OR EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(
                        requested_compilation_document->'fragments'
                    ) WITH ORDINALITY AS item(fragment, source_ordinal)
                    WHERE jsonb_typeof(item.fragment) IS DISTINCT FROM 'object'
                       OR COALESCE(
                            item.fragment->>'fragmentRef'
                                !~ '^fragment:(heading|paragraph|list|fenced_code|table):[1-9][0-9]*$',
                            true
                       )
                       OR COALESCE(
                            item.fragment->>'kind'
                                NOT IN ('heading', 'paragraph', 'list', 'fenced_code', 'table'),
                            true
                       )
                       OR CASE
                            WHEN jsonb_typeof(item.fragment->'path') = 'array'
                            THEN
                                jsonb_array_length(item.fragment->'path') < 2
                                OR EXISTS (
                                    SELECT 1 FROM jsonb_array_elements_text(
                                        item.fragment->'path'
                                    ) AS path(segment)
                                    WHERE btrim(path.segment) = ''
                                       OR path.segment <> btrim(path.segment)
                                )
                            ELSE true
                          END
                       OR jsonb_typeof(item.fragment->'position')
                            IS DISTINCT FROM 'object'
                       OR COALESCE(
                            item.fragment#>>'{{position,start,line}}' !~ '^[1-9][0-9]*$', true
                       )
                       OR COALESCE(
                            item.fragment#>>'{{position,start,column}}' !~ '^[1-9][0-9]*$', true
                       )
                       OR COALESCE(
                            item.fragment#>>'{{position,start,byteOffset}}' !~ '^[0-9]+$', true
                       )
                       OR COALESCE(
                            item.fragment#>>'{{position,end,line}}' !~ '^[1-9][0-9]*$', true
                       )
                       OR COALESCE(
                            item.fragment#>>'{{position,end,column}}' !~ '^[1-9][0-9]*$', true
                       )
                       OR COALESCE(
                            item.fragment#>>'{{position,end,byteOffset}}' !~ '^[1-9][0-9]*$', true
                       )
                       OR CASE
                            WHEN item.fragment#>>'{{position,start,byteOffset}}'
                                    ~ '^[0-9]+$'
                             AND item.fragment#>>'{{position,end,byteOffset}}'
                                    ~ '^[1-9][0-9]*$'
                            THEN
                                (item.fragment#>>'{{position,start,byteOffset}}')::numeric
                                >=
                                (item.fragment#>>'{{position,end,byteOffset}}')::numeric
                            ELSE true
                          END
                       OR COALESCE(
                            translate(
                                item.fragment->>'sourceText',
                                U&'\\0009\\000A\\000B\\000C\\000D\\001C\\001D\\001E\\001F\\0020\\0085\\00A0\\1680\\2000\\2001\\2002\\2003\\2004\\2005\\2006\\2007\\2008\\2009\\200A\\2028\\2029\\202F\\205F\\3000',
                                ''
                            ) = '',
                            true
                       )
                       OR COALESCE(
                            translate(
                                item.fragment->>'contextualText',
                                U&'\\0009\\000A\\000B\\000C\\000D\\001C\\001D\\001E\\001F\\0020\\0085\\00A0\\1680\\2000\\2001\\2002\\2003\\2004\\2005\\2006\\2007\\2008\\2009\\200A\\2028\\2029\\202F\\205F\\3000',
                                ''
                            ) = '',
                            true
                       )
                       OR CASE
                            WHEN jsonb_typeof(
                                item.fragment->'searchPhrases'
                            ) = 'array'
                            THEN
                                jsonb_array_length(
                                    item.fragment->'searchPhrases'
                                ) NOT BETWEEN 1 AND 4096
                                OR EXISTS (
                                    SELECT 1 FROM jsonb_array_elements_text(
                                        item.fragment->'searchPhrases'
                                    ) AS phrase(value)
                                    WHERE translate(
                                        phrase.value,
                                        U&'\\0009\\000A\\000B\\000C\\000D\\001C\\001D\\001E\\001F\\0020\\0085\\00A0\\1680\\2000\\2001\\2002\\2003\\2004\\2005\\2006\\2007\\2008\\2009\\200A\\2028\\2029\\202F\\205F\\3000',
                                        ''
                                    ) = ''
                                )
                                OR (
                                    SELECT count(DISTINCT phrase.value)
                                    FROM jsonb_array_elements_text(
                                        item.fragment->'searchPhrases'
                                    ) AS phrase(value)
                                ) <> jsonb_array_length(
                                    item.fragment->'searchPhrases'
                                )
                            ELSE true
                          END
                )
               OR EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(
                        requested_compilation_document->'fragments'
                    ) WITH ORDINALITY AS fragment_item(fragment, ordinal)
                    JOIN jsonb_array_elements(
                        requested_compilation_document->'sections'
                    ) WITH ORDINALITY AS section_item(section, ordinal)
                    USING (ordinal)
                    WHERE fragment_item.fragment->'kind'
                            IS DISTINCT FROM section_item.section->'kind'
                       OR fragment_item.fragment->'path'
                            IS DISTINCT FROM section_item.section->'path'
                       OR fragment_item.fragment->'position'
                            IS DISTINCT FROM section_item.section->'position'
                )
               OR (
                    SELECT count(DISTINCT item.fragment->>'fragmentRef')
                    FROM jsonb_array_elements(
                        requested_compilation_document->'fragments'
                    ) AS item(fragment)
               ) <> fragment_count
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
            IF decision.classification IS DISTINCT FROM 'changed' THEN
                RETURN;
            END IF;

            now_at := pg_catalog.statement_timestamp();
            SELECT * INTO job_row FROM public.file_import_job AS job
            WHERE job.organization_id = requested_organization_id
              AND job.job_id = requested_job_id
              AND job.state = 'running'
            FOR UPDATE;
            IF job_row.job_id IS NULL THEN RETURN; END IF;
            IF NOT EXISTS (
                SELECT 1
                FROM public.file_acquisition AS acquisition
                JOIN public.membership AS audience_membership
                  ON audience_membership.organization_id = acquisition.organization_id
                 AND audience_membership.membership_id = acquisition.audience_membership_id
                 AND audience_membership.membership_version = acquisition.audience_membership_version
                 AND audience_membership.status = 'active'
                 AND audience_membership.valid_from <= now_at
                 AND (audience_membership.valid_until IS NULL OR audience_membership.valid_until > now_at)
                JOIN public.resource_access_policy AS access_policy
                  ON access_policy.organization_id = acquisition.organization_id
                 AND access_policy.resource_ref = requested_resource_ref
                 AND access_policy.principal_ref = acquisition.audience_principal_ref
                 AND access_policy.access_state = 'allowed'
                JOIN public.membership_resource_field_right AS field_right
                  ON field_right.organization_id = acquisition.organization_id
                 AND field_right.membership_id = acquisition.audience_membership_id
                 AND field_right.membership_version = acquisition.audience_membership_version
                 AND field_right.resource_ref = requested_resource_ref
                 AND field_right.field_ref = 'body'
                WHERE acquisition.organization_id = requested_organization_id
                  AND acquisition.acquisition_id = job_row.acquisition_id
                  AND acquisition.source_id = job_row.source_id
            ) THEN RETURN; END IF;
            SELECT resource.active_revision_id INTO old_revision
            FROM public.context_resource AS resource
            JOIN public.file_revision_snapshot AS snapshot
              ON snapshot.organization_id = resource.organization_id
             AND snapshot.resource_ref = resource.resource_ref
             AND snapshot.revision_id = resource.active_revision_id
            WHERE resource.organization_id = requested_organization_id
              AND resource.source_ref = requested_source_ref
              AND resource.resource_ref = requested_resource_ref
              AND resource.active_revision_id IS NOT NULL
              AND resource.tombstoned IS FALSE
              AND (
                  SELECT array_agg(event.state ORDER BY event.ordinal)
                  FROM public.revision_publication_event AS event
                  WHERE event.organization_id = resource.organization_id
                    AND event.resource_ref = resource.resource_ref
                    AND event.revision_id = resource.active_revision_id
              ) = ARRAY['prepared', 'indexed', 'active']::text[]
              AND EXISTS (
                  SELECT 1 FROM public.context_fragment AS fragment
                  WHERE fragment.organization_id = resource.organization_id
                    AND fragment.resource_ref = resource.resource_ref
                    AND fragment.revision_id = resource.active_revision_id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM public.context_fragment AS fragment
                  WHERE fragment.organization_id = resource.organization_id
                    AND fragment.resource_ref = resource.resource_ref
                    AND fragment.revision_id = resource.active_revision_id
                    AND NOT EXISTS (
                        SELECT 1 FROM public.exact_phrase_candidate AS candidate
                        WHERE candidate.organization_id = fragment.organization_id
                          AND candidate.resource_ref = fragment.resource_ref
                          AND candidate.revision_id = fragment.revision_id
                          AND candidate.fragment_ref = fragment.fragment_ref
                    )
              )
            FOR UPDATE OF resource;
            IF old_revision IS NULL OR old_revision = requested_revision_id THEN
                RETURN;
            END IF;

            INSERT INTO public.context_revision VALUES (
                requested_organization_id, requested_resource_ref,
                requested_revision_id
            );
            INSERT INTO public.file_revision_snapshot (
                organization_id, resource_ref, revision_id, acquisition_id,
                canonical_text, content_hash, compilation_digest,
                compiler_version, config_version, compilation_document
            ) VALUES (
                requested_organization_id, requested_resource_ref,
                requested_revision_id, job_row.acquisition_id,
                requested_canonical_text, requested_content_hash,
                requested_compilation_digest, requested_compiler_version,
                requested_config_version, requested_compilation_document
            );
            INSERT INTO public.context_fragment (
                organization_id, resource_ref, revision_id, fragment_ref,
                ordinal, content, projection_kind
            )
            SELECT requested_organization_id, requested_resource_ref,
                   requested_revision_id, item.fragment->>'fragmentRef',
                   (item.source_ordinal - 1)::integer,
                   item.fragment->>'contextualText', 'body'
            FROM jsonb_array_elements(
                requested_compilation_document->'fragments'
            ) WITH ORDINALITY AS item(fragment, source_ordinal)
            ORDER BY item.source_ordinal;
            INSERT INTO public.revision_publication_event VALUES (
                requested_organization_id, requested_resource_ref,
                requested_revision_id, 0, 'prepared', now_at
            );
            INSERT INTO public.exact_phrase_candidate (
                organization_id, phrase_digest, source_ref, resource_ref,
                revision_id, fragment_ref
            )
            SELECT requested_organization_id,
                   encode(
                       public.digest(
                           convert_to('context-engine.exact-phrase.v1', 'UTF8')
                           || decode('00', 'hex')
                           || convert_to(phrase.value, 'UTF8'),
                           'sha256'
                       ), 'hex'
                   ),
                   job_row.source_id::text, requested_resource_ref,
                   requested_revision_id, item.fragment->>'fragmentRef'
            FROM jsonb_array_elements(
                requested_compilation_document->'fragments'
            ) WITH ORDINALITY AS item(fragment, source_ordinal)
            CROSS JOIN LATERAL jsonb_array_elements_text(
                item.fragment->'searchPhrases'
            ) WITH ORDINALITY AS phrase(value, phrase_ordinal)
            ORDER BY item.source_ordinal, phrase.phrase_ordinal;
            INSERT INTO public.revision_publication_event VALUES (
                requested_organization_id, requested_resource_ref,
                requested_revision_id, 1, 'indexed', now_at
            );
            SELECT array_agg(fragment_ref ORDER BY ordinal)
            INTO ready_fragments
            FROM public.context_fragment
            WHERE organization_id = requested_organization_id
              AND resource_ref = requested_resource_ref
              AND revision_id = requested_revision_id;
            SELECT fragment_ref INTO first_fragment
            FROM public.context_fragment
            WHERE organization_id = requested_organization_id
              AND resource_ref = requested_resource_ref
              AND revision_id = requested_revision_id
            ORDER BY ordinal LIMIT 1;
            INSERT INTO public.file_revision_replacement_plan (
                organization_id, source_id, resource_ref,
                previous_revision_id, replacement_revision_id,
                acquisition_id, job_id, content_identity_digest, prepared_at
            ) VALUES (
                requested_organization_id, job_row.source_id,
                requested_resource_ref, old_revision, requested_revision_id,
                job_row.acquisition_id, requested_job_id,
                decision.content_identity_digest, now_at
            );
            UPDATE public.file_import_job
            SET state = 'ready', resource_ref = requested_resource_ref,
                revision_id = requested_revision_id,
                fragment_ref = first_fragment
            WHERE organization_id = requested_organization_id
              AND job_id = requested_job_id AND state = 'running';
            IF NOT FOUND THEN RETURN; END IF;
            RETURN QUERY SELECT old_revision, requested_revision_id,
                ready_fragments, decision.content_identity_digest;
        END; $function$
        """
    )

    op.execute(
        f"""
        CREATE FUNCTION public.context_worker_activate_file_replacement(
            requested_organization_id uuid, requested_job_id uuid,
            requested_service_principal_id uuid, requested_source_ref text,
            requested_resource_ref text, requested_previous_revision_id uuid,
            requested_replacement_revision_id uuid,
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
            job_row public.file_import_job%ROWTYPE;
            plan_row public.file_revision_replacement_plan%ROWTYPE;
            active_fragments text[];
            now_at timestamptz;
        BEGIN
            IF SESSION_USER <> '{_WORKER}' THEN RETURN; END IF;
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
              AND job.state = 'ready'
              AND job.resource_ref = requested_resource_ref
              AND job.revision_id = requested_replacement_revision_id
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

            PERFORM 1 FROM public.file_resource_ingestion_guard AS guard
            WHERE guard.organization_id = requested_organization_id
              AND guard.source_id = job_row.source_id
              AND guard.resource_ref = requested_resource_ref
            FOR UPDATE;
            IF NOT FOUND THEN RETURN; END IF;
            SELECT * INTO plan_row
            FROM public.file_revision_replacement_plan AS plan
            WHERE plan.organization_id = requested_organization_id
              AND plan.resource_ref = requested_resource_ref
              AND plan.previous_revision_id = requested_previous_revision_id
              AND plan.replacement_revision_id = requested_replacement_revision_id
              AND plan.job_id = requested_job_id
              AND plan.acquisition_id = job_row.acquisition_id;
            IF plan_row.job_id IS NULL THEN RETURN; END IF;
            PERFORM pg_catalog.pg_advisory_xact_lock(
                pg_catalog.hashtextextended(
                    'context-engine.file-replacement:'
                    || requested_organization_id::text || ':'
                    || requested_resource_ref,
                    0
                )
            );
            PERFORM pg_catalog.pg_advisory_xact_lock(
                pg_catalog.hashtextextended(
                    'context-engine.file-publication:'
                    || requested_organization_id::text,
                    0
                )
            );
            IF NOT EXISTS (
                SELECT 1
                FROM public.context_resource AS resource
                JOIN public.file_revision_snapshot AS snapshot
                  ON snapshot.organization_id = resource.organization_id
                 AND snapshot.resource_ref = resource.resource_ref
                 AND snapshot.revision_id = requested_replacement_revision_id
                WHERE resource.organization_id = requested_organization_id
                  AND resource.source_ref = requested_source_ref
                  AND resource.resource_ref = requested_resource_ref
                  AND resource.active_revision_id = requested_previous_revision_id
                  AND resource.tombstoned IS FALSE
                  AND (
                      SELECT array_agg(event.state ORDER BY event.ordinal)
                      FROM public.revision_publication_event AS event
                      WHERE event.organization_id = resource.organization_id
                        AND event.resource_ref = resource.resource_ref
                        AND event.revision_id = requested_replacement_revision_id
                  ) = ARRAY['prepared', 'indexed']::text[]
                  AND EXISTS (
                      SELECT 1 FROM public.context_fragment AS fragment
                      WHERE fragment.organization_id = resource.organization_id
                        AND fragment.resource_ref = resource.resource_ref
                        AND fragment.revision_id = requested_replacement_revision_id
                  )
                  AND EXISTS (
                      SELECT 1 FROM public.exact_phrase_candidate AS candidate
                      WHERE candidate.organization_id = resource.organization_id
                        AND candidate.resource_ref = resource.resource_ref
                        AND candidate.revision_id = requested_replacement_revision_id
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM public.context_fragment AS fragment
                      WHERE fragment.organization_id = resource.organization_id
                        AND fragment.resource_ref = resource.resource_ref
                        AND fragment.revision_id = requested_replacement_revision_id
                        AND NOT EXISTS (
                            SELECT 1 FROM public.exact_phrase_candidate AS candidate
                            WHERE candidate.organization_id = fragment.organization_id
                              AND candidate.resource_ref = fragment.resource_ref
                              AND candidate.revision_id = fragment.revision_id
                              AND candidate.fragment_ref = fragment.fragment_ref
                        )
                  )
                  AND EXISTS (
                      SELECT 1
                      FROM public.file_acquisition AS acquisition
                      JOIN public.membership AS audience_membership
                        ON audience_membership.organization_id = acquisition.organization_id
                       AND audience_membership.membership_id = acquisition.audience_membership_id
                       AND audience_membership.membership_version = acquisition.audience_membership_version
                       AND audience_membership.status = 'active'
                       AND audience_membership.valid_from <= now_at
                       AND (audience_membership.valid_until IS NULL OR audience_membership.valid_until > now_at)
                      JOIN public.resource_access_policy AS access_policy
                        ON access_policy.organization_id = acquisition.organization_id
                       AND access_policy.resource_ref = requested_resource_ref
                       AND access_policy.principal_ref = acquisition.audience_principal_ref
                       AND access_policy.access_state = 'allowed'
                      JOIN public.membership_resource_field_right AS field_right
                        ON field_right.organization_id = acquisition.organization_id
                       AND field_right.membership_id = acquisition.audience_membership_id
                       AND field_right.membership_version = acquisition.audience_membership_version
                       AND field_right.resource_ref = requested_resource_ref
                       AND field_right.field_ref = 'body'
                      WHERE acquisition.organization_id = requested_organization_id
                        AND acquisition.acquisition_id = job_row.acquisition_id
                        AND acquisition.source_id = job_row.source_id
                  )
            ) THEN RETURN; END IF;

            SELECT array_agg(fragment_ref ORDER BY ordinal)
            INTO active_fragments
            FROM public.context_fragment
            WHERE organization_id = requested_organization_id
              AND resource_ref = requested_resource_ref
              AND revision_id = requested_replacement_revision_id;
            UPDATE public.context_resource AS resource
            SET active_revision_id = requested_replacement_revision_id
            WHERE resource.organization_id = requested_organization_id
              AND resource.resource_ref = requested_resource_ref
              AND resource.active_revision_id = requested_previous_revision_id;
            IF NOT FOUND THEN RETURN; END IF;
            INSERT INTO public.revision_publication_event VALUES (
                requested_organization_id, requested_resource_ref,
                requested_replacement_revision_id, 2, 'active', now_at
            );
            INSERT INTO public.file_revision_supersession (
                organization_id, resource_ref, superseded_revision_id,
                replacement_revision_id, acquisition_id, job_id,
                retention_state, activated_at
            ) VALUES (
                requested_organization_id, requested_resource_ref,
                requested_previous_revision_id,
                requested_replacement_revision_id, job_row.acquisition_id,
                requested_job_id, 'retained_until_explicit_cleanup', now_at
            );
            UPDATE public.file_import_job
            SET state = 'completed', completed_at = now_at, effect_count = 1
            WHERE organization_id = requested_organization_id
              AND job_id = requested_job_id AND state = 'ready';
            IF NOT FOUND THEN RETURN; END IF;
            RETURN QUERY SELECT 1::smallint, 'replaced'::text,
                requested_replacement_revision_id, active_fragments,
                plan_row.content_identity_digest, NULL::text;
        END; $function$
        """
    )
    for name, signature in (
        ("context_worker_stage_file_replacement", _STAGE_V1_SIGNATURE),
        ("context_worker_stage_structural_file_replacement", _STAGE_V2_SIGNATURE),
        ("context_worker_activate_file_replacement", _ACTIVATE_SIGNATURE),
    ):
        op.execute(
            f"REVOKE ALL ON FUNCTION public.{name}{signature} FROM PUBLIC"
        )
        op.execute(
            f"ALTER FUNCTION public.{name}{signature} OWNER TO {_DEFINER}"
        )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")
    op.execute(
        "GRANT SELECT, INSERT ON TABLE file_revision_replacement_plan, "
        f"file_revision_supersession TO {_DEFINER}"
    )
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        f"public.context_worker_stage_file_replacement{_STAGE_V1_SIGNATURE} "
        f"TO {_WORKER}"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        f"public.context_worker_stage_structural_file_replacement{_STAGE_V2_SIGNATURE} "
        f"TO {_WORKER}"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        f"public.context_worker_activate_file_replacement{_ACTIVATE_SIGNATURE} "
        f"TO {_WORKER}"
    )
    op.execute("RESET ROLE")


def downgrade() -> None:
    """Remove replacement publication only when no durable plan exists."""

    op.execute(
        "LOCK TABLE file_revision_replacement_plan, "
        "file_revision_supersession IN ACCESS EXCLUSIVE MODE"
    )
    bind = op.get_bind()
    if bind.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM file_revision_replacement_plan)")
    ).scalar_one():
        raise RuntimeError("File replacement downgrade requires no prepared plans")
    op.execute(
        "DROP FUNCTION public.context_worker_activate_file_replacement"
        f"{_ACTIVATE_SIGNATURE}"
    )
    op.execute(
        "DROP FUNCTION public.context_worker_stage_structural_file_replacement"
        f"{_STAGE_V2_SIGNATURE}"
    )
    op.execute(
        "DROP FUNCTION public.context_worker_stage_file_replacement"
        f"{_STAGE_V1_SIGNATURE}"
    )
    op.drop_constraint(
        "ck_file_import_job_state_consistency", "file_import_job", type_="check"
    )
    op.create_check_constraint(
        "ck_file_import_job_state_consistency",
        "file_import_job",
        "(state = 'available' AND signing_key_version IS NULL AND lease_nonce_digest IS NULL AND lease_issued_at IS NULL AND lease_expires_at IS NULL AND lease_redeemed_at IS NULL AND failed_at IS NULL AND completed_at IS NULL AND resource_ref IS NULL AND revision_id IS NULL AND fragment_ref IS NULL AND effect_count = 0) OR "
        "(state = 'leased' AND signing_key_version > 0 AND octet_length(lease_nonce_digest) = 32 AND lease_issued_at IS NOT NULL AND lease_expires_at > lease_issued_at AND lease_redeemed_at IS NULL AND failed_at IS NULL AND completed_at IS NULL AND resource_ref IS NULL AND revision_id IS NULL AND fragment_ref IS NULL AND effect_count = 0) OR "
        "(state = 'running' AND signing_key_version > 0 AND octet_length(lease_nonce_digest) = 32 AND lease_issued_at IS NOT NULL AND lease_expires_at > lease_issued_at AND lease_redeemed_at >= lease_issued_at AND failed_at IS NULL AND completed_at IS NULL AND resource_ref IS NULL AND revision_id IS NULL AND fragment_ref IS NULL AND effect_count = 0) OR "
        "(state = 'failed' AND signing_key_version > 0 AND octet_length(lease_nonce_digest) = 32 AND lease_issued_at IS NOT NULL AND lease_expires_at > lease_issued_at AND lease_redeemed_at >= lease_issued_at AND failed_at >= lease_redeemed_at AND completed_at IS NULL AND resource_ref IS NULL AND revision_id IS NULL AND fragment_ref IS NULL AND effect_count = 0) OR "
        "(state = 'completed' AND signing_key_version > 0 AND octet_length(lease_nonce_digest) = 32 AND lease_issued_at IS NOT NULL AND lease_expires_at > lease_issued_at AND lease_redeemed_at >= lease_issued_at AND failed_at IS NULL AND completed_at >= lease_redeemed_at AND resource_ref IS NOT NULL AND revision_id IS NOT NULL AND fragment_ref IS NOT NULL AND effect_count IN (0, 1))",
    )
    op.drop_constraint(
        "ck_file_import_job_state", "file_import_job", type_="check"
    )
    op.create_check_constraint(
        "ck_file_import_job_state",
        "file_import_job",
        "state IN ('available', 'leased', 'running', 'failed', 'completed')",
    )
    op.drop_table("file_revision_supersession")
    op.drop_table("file_revision_replacement_plan")
