"""Publish structural Markdown Fragments with exact Revision provenance.

Revision ID: 20260723_0012
Revises: 20260722_0011
Create Date: 2026-07-23
"""

# ruff: noqa: E501

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260723_0012"
down_revision: str | None = "20260722_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WORKER = "context_engine_worker"
_DEFINER = "context_engine_worker_lease_definer"
_SIGNATURE = "(uuid, uuid, uuid, text, text, uuid, text, text, text, text, text, jsonb, bigint, bytea, timestamp with time zone, timestamp with time zone)"
_V1_SIGNATURE = "(uuid, uuid, uuid, text, text, uuid, text, text, text, text, text, text, text, text, bigint, bytea, timestamp with time zone, timestamp with time zone)"


def upgrade() -> None:
    """Add immutable structural lineage and one atomic multi-Fragment publisher."""

    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        "ALTER FUNCTION public.context_worker_publish_file_import"
        f"{_V1_SIGNATURE} RENAME TO "
        "context_worker_publish_file_import_v1_internal"
    )
    op.execute(
        "REVOKE EXECUTE ON FUNCTION "
        "public.context_worker_publish_file_import_v1_internal"
        f"{_V1_SIGNATURE} FROM {_WORKER}, PUBLIC"
    )
    op.execute(
        f"""
        CREATE FUNCTION public.context_worker_publish_file_import(
            requested_organization_id uuid, requested_job_id uuid,
            requested_service_principal_id uuid, requested_source_ref text,
            requested_resource_ref text, requested_revision_id uuid,
            requested_fragment_ref text, requested_canonical_text text,
            requested_paragraph text, requested_content_hash text,
            requested_compilation_digest text, requested_compiler_version text,
            requested_config_version text, requested_phrase_digest text,
            requested_signing_key_version bigint, requested_nonce bytea,
            requested_issued_at timestamptz, requested_expires_at timestamptz
        ) RETURNS TABLE (effect_count smallint)
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$
        BEGIN
            IF SESSION_USER <> '{_WORKER}'
               OR requested_compiler_version <> 'context-engine-markdown-v1'
               OR requested_config_version <> 'markdown-config-v1'
            THEN RETURN; END IF;
            RETURN QUERY
            SELECT published.effect_count
            FROM public.context_worker_publish_file_import_v1_internal(
                requested_organization_id, requested_job_id,
                requested_service_principal_id, requested_source_ref,
                requested_resource_ref, requested_revision_id,
                requested_fragment_ref, requested_canonical_text,
                requested_paragraph, requested_content_hash,
                requested_compilation_digest, requested_compiler_version,
                requested_config_version, requested_phrase_digest,
                requested_signing_key_version, requested_nonce,
                requested_issued_at, requested_expires_at
            ) AS published;
        END; $function$
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION "
        f"public.context_worker_publish_file_import{_V1_SIGNATURE} FROM PUBLIC"
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")
    op.execute(
        "ALTER TABLE file_revision_snapshot "
        "ADD COLUMN compilation_document jsonb"
    )
    op.execute(
        """
        ALTER TABLE file_revision_snapshot
        ADD CONSTRAINT ck_file_revision_snapshot_structural_document
        CHECK (
            (
                compilation_document IS NULL
                AND compiler_version = 'context-engine-markdown-v1'
                AND config_version = 'markdown-config-v1'
            ) OR (
                compilation_document IS NOT NULL
                AND compiler_version = 'context-engine-markdown-v2'
                AND config_version = 'markdown-config-v2'
                AND jsonb_typeof(compilation_document) = 'object'
                AND compilation_document->>'canonicalText' = canonical_text
                AND compilation_document->>'contentHash' = content_hash
                AND compilation_document->>'compilationDigest' = compilation_digest
                AND compilation_document#>>'{provenance,compilerVersion}' = compiler_version
                AND compilation_document#>>'{provenance,configVersion}' = config_version
                AND compilation_document#>>'{provenance,canonicalizationProfile}' = 'markdown-structural-units-v2'
                AND compilation_document#>>'{provenance,compilationDigestProfile}' = 'rfc8785-sha256-v2'
            )
        )
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION public.context_worker_publish_structural_file_import(
            requested_organization_id uuid, requested_job_id uuid,
            requested_service_principal_id uuid, requested_source_ref text,
            requested_resource_ref text, requested_revision_id uuid,
            requested_canonical_text text, requested_content_hash text,
            requested_compilation_digest text, requested_compiler_version text,
            requested_config_version text, requested_compilation_document jsonb,
            requested_signing_key_version bigint, requested_nonce bytea,
            requested_issued_at timestamptz, requested_expires_at timestamptz
        ) RETURNS TABLE (effect_count smallint)
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$
        DECLARE
            job_row public.file_import_job%ROWTYPE;
            acquisition_row public.file_acquisition%ROWTYPE;
            now_at timestamptz;
            first_fragment_ref text;
            fragment_count integer;
        BEGIN
            IF SESSION_USER <> '{_WORKER}'
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
            IF fragment_count < 1 OR fragment_count > 4096
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
                            item.fragment#>>'{{position,start,line}}' !~ '^[1-9][0-9]*$',
                            true
                       )
                       OR COALESCE(
                            item.fragment#>>'{{position,start,column}}' !~ '^[1-9][0-9]*$',
                            true
                       )
                       OR COALESCE(
                            item.fragment#>>'{{position,start,byteOffset}}' !~ '^[0-9]+$',
                            true
                       )
                       OR COALESCE(
                            item.fragment#>>'{{position,end,line}}' !~ '^[1-9][0-9]*$',
                            true
                       )
                       OR COALESCE(
                            item.fragment#>>'{{position,end,column}}' !~ '^[1-9][0-9]*$',
                            true
                       )
                       OR COALESCE(
                            item.fragment#>>'{{position,end,byteOffset}}' !~ '^[1-9][0-9]*$',
                            true
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

            PERFORM pg_catalog.set_config(
                'app.organization_id', requested_organization_id::text, true
            );
            PERFORM pg_catalog.set_config(
                'app.worker_job_id', requested_job_id::text, true
            );
            SELECT * INTO job_row FROM public.file_import_job
            WHERE organization_id = requested_organization_id
              AND job_id = requested_job_id
              AND service_principal_id = requested_service_principal_id
              AND source_id::text = requested_source_ref
              AND state = 'running'
              AND signing_key_version = requested_signing_key_version
              AND lease_nonce_digest = public.digest(requested_nonce, 'sha256')
              AND lease_issued_at = requested_issued_at
              AND lease_expires_at = requested_expires_at
              AND pg_catalog.statement_timestamp() < lease_expires_at
              AND EXISTS (
                  SELECT 1 FROM public.service_principal AS principal
                  WHERE principal.organization_id = file_import_job.organization_id
                    AND principal.service_principal_id = file_import_job.service_principal_id
                    AND principal.workload = file_import_job.workload
                    AND principal.worker_audience = file_import_job.worker_audience
                    AND principal.operation = file_import_job.operation
                    AND principal.enabled IS TRUE
              )
            FOR UPDATE;
            IF job_row.job_id IS NULL THEN RETURN; END IF;
            SELECT * INTO acquisition_row FROM public.file_acquisition
            WHERE organization_id = job_row.organization_id
              AND acquisition_id = job_row.acquisition_id;

            now_at := pg_catalog.statement_timestamp();
            SET CONSTRAINTS ALL DEFERRED;
            INSERT INTO public.context_resource (
                organization_id, resource_ref, source_ref,
                active_revision_id, tombstoned
            ) VALUES (
                requested_organization_id, requested_resource_ref,
                job_row.source_id::text, NULL, false
            );
            INSERT INTO public.context_revision (
                organization_id, resource_ref, revision_id
            ) VALUES (
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
                       ),
                       'hex'
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
            INSERT INTO public.resource_access_policy VALUES (
                requested_organization_id, requested_resource_ref,
                acquisition_row.audience_principal_ref, 1, 'allowed', NULL
            );
            INSERT INTO public.membership_resource_field_right VALUES (
                requested_organization_id,
                acquisition_row.audience_membership_id,
                acquisition_row.audience_membership_version,
                requested_resource_ref, 'body'
            );
            UPDATE public.context_resource
            SET active_revision_id = requested_revision_id
            WHERE organization_id = requested_organization_id
              AND resource_ref = requested_resource_ref
              AND active_revision_id IS NULL;
            IF NOT FOUND THEN RETURN; END IF;
            INSERT INTO public.revision_publication_event VALUES (
                requested_organization_id, requested_resource_ref,
                requested_revision_id, 2, 'active', now_at
            );
            SELECT item.fragment->>'fragmentRef' INTO first_fragment_ref
            FROM jsonb_array_elements(
                requested_compilation_document->'fragments'
            ) WITH ORDINALITY AS item(fragment, source_ordinal)
            ORDER BY item.source_ordinal LIMIT 1;
            UPDATE public.file_import_job
            SET state = 'completed', completed_at = now_at,
                resource_ref = requested_resource_ref,
                revision_id = requested_revision_id,
                fragment_ref = first_fragment_ref, effect_count = 1
            WHERE organization_id = requested_organization_id
              AND job_id = requested_job_id AND state = 'running'
            RETURNING file_import_job.effect_count INTO effect_count;
            IF effect_count IS NOT NULL THEN RETURN NEXT; END IF;
            RETURN;
        END; $function$
        """
    )
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(
        "REVOKE ALL ON FUNCTION "
        f"public.context_worker_publish_structural_file_import{_SIGNATURE} "
        "FROM PUBLIC"
    )
    op.execute(
        "ALTER FUNCTION "
        f"public.context_worker_publish_structural_file_import{_SIGNATURE} "
        f"OWNER TO {_DEFINER}"
    )
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        f"public.context_worker_publish_file_import{_V1_SIGNATURE} TO {_WORKER}"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        f"public.context_worker_publish_structural_file_import{_SIGNATURE} "
        f"TO {_WORKER}"
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


def downgrade() -> None:
    """Remove structural publication only when no v2 snapshot would be lost."""

    op.execute("LOCK TABLE file_revision_snapshot IN ACCESS EXCLUSIVE MODE")
    has_structural_snapshots = bool(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM file_revision_snapshot "
                "WHERE compilation_document IS NOT NULL)"
            )
        )
        .scalar_one()
    )
    if has_structural_snapshots:
        raise RuntimeError(
            "structural Markdown downgrade requires no v2 snapshots"
        )
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        "DROP FUNCTION public.context_worker_publish_file_import"
        f"{_V1_SIGNATURE}"
    )
    op.execute(
        "ALTER FUNCTION "
        "public.context_worker_publish_file_import_v1_internal"
        f"{_V1_SIGNATURE} RENAME TO context_worker_publish_file_import"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        f"public.context_worker_publish_file_import{_V1_SIGNATURE} TO {_WORKER}"
    )
    op.execute(
        "DROP FUNCTION "
        f"public.context_worker_publish_structural_file_import{_SIGNATURE}"
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")
    op.execute(
        "ALTER TABLE file_revision_snapshot "
        "DROP CONSTRAINT ck_file_revision_snapshot_structural_document"
    )
    op.execute(
        "ALTER TABLE file_revision_snapshot DROP COLUMN compilation_document"
    )
