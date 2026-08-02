# ruff: noqa: E501
"""Bind embedding provider identity to Releases and Fragment vectors.

Revision ID: 20260802_0053
Revises: 20260731_0052
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260802_0053"
down_revision: str | None = "20260731_0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MIGRATOR = "context_engine_migrator"
_WORKER = "context_engine_worker"
_WORKER_DEFINER = "context_engine_worker_lease_definer"
_RELEASE_DEFINER = "context_engine_release_definer"
_TWIN_PROFILE_DOCUMENT = '{"artifactDigest":"d46ebb10dcfe3e7b2e0fb96ca9582c6ccab55863453251528a0325891232533f","batchSize":256,"dimension":384,"documentPrefix":"","modelId":"context-engine/deterministic-embedding-twin","pooling":"shake256-component-l2","precision":"float64-to-float32","queryPrefix":"","revision":"0000000000000000000000000000000000000000","transformationPipeline":"shake256 float components -> l2"}'
_TWIN_PROFILE_DIGEST = "01a741b2802507a5ca52035ad112a50896b2284cd66f294b5f84618f6554ff9b"
_PREPARE_OLD = (
    "context_worker_prepare_file_publication"
    "(uuid,uuid,uuid,text,text,uuid,text,jsonb,jsonb,jsonb,bigint,bigint,bytea,"
    "timestamp with time zone,timestamp with time zone)"
)
_PREPARE_NEW = (
    "context_worker_prepare_file_publication"
    "(uuid,uuid,uuid,text,text,uuid,text,jsonb,jsonb,jsonb,text,bigint,bigint,bytea,"
    "timestamp with time zone,timestamp with time zone)"
)
_CLASSIFY_OLD = (
    "context_worker_classify_file_import_internal"
    "(uuid,uuid,uuid,text,text,text,text,text,text,bigint,bytea,"
    "timestamp with time zone,timestamp with time zone)"
)
_CLASSIFY_NEW = (
    "context_worker_classify_file_import_internal"
    "(uuid,uuid,uuid,text,text,text,text,text,text,text,bigint,bytea,"
    "timestamp with time zone,timestamp with time zone)"
)
_ACQUIRE_OLD = (
    "context_worker_acquire_file_publication"
    "(uuid,uuid,uuid,text,text,uuid,text,text,text,text,text,jsonb,jsonb,bigint,"
    "bigint,bytea,timestamp with time zone,timestamp with time zone)"
)
_ACQUIRE_NEW = (
    "context_worker_acquire_file_publication"
    "(uuid,uuid,uuid,text,text,uuid,text,text,text,text,text,text,jsonb,jsonb,"
    "bigint,bigint,bytea,timestamp with time zone,timestamp with time zone)"
)
_INDEX = (
    "context_worker_index_file_publication"
    "(uuid,uuid,uuid,text,text,uuid,text,jsonb,jsonb,bigint,bigint,bytea,"
    "timestamp with time zone,timestamp with time zone)"
)
_ACTIVATE = (
    "context_worker_activate_recoverable_file_publication"
    "(uuid,uuid,uuid,text,text,uuid,bigint,bigint,bytea,"
    "timestamp with time zone,timestamp with time zone)"
)
_PROMOTE = (
    "context_learning_promote_release"
    "(uuid,text,text,text,text,text,text,text,text,text,text,text,bigint,bytea,"
    "bigint,text,timestamp with time zone,timestamp with time zone,text,text,text)"
)


def _function_definition(regprocedure: str) -> str:
    definition = op.get_bind().execute(
        sa.text(
            "SELECT pg_catalog.pg_get_functiondef("
            f"'public.{regprocedure}'::regprocedure)"
        )
    ).scalar_one()
    if not isinstance(definition, str):
        raise RuntimeError("database function definition is unavailable")
    return definition


def _replace_exact(definition: str, searched: str, replacement: str) -> str:
    if definition.count(searched) != 1:
        raise RuntimeError("database function shape was not recognized")
    return definition.replace(searched, replacement)


def _install(definition: str, owner: str) -> None:
    op.execute(f"GRANT CREATE ON SCHEMA public TO {owner}")
    op.execute(f"SET LOCAL ROLE {owner}")
    op.execute(definition)
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {owner}")


def _replace_prepare(*, add_profile: bool) -> None:
    source, target = (
        (_PREPARE_OLD, _PREPARE_NEW) if add_profile else (_PREPARE_NEW, _PREPARE_OLD)
    )
    definition = _function_definition(source)
    old_declaration = "requested_embedding_document jsonb, requested_lease_generation"
    new_declaration = (
        "requested_embedding_document jsonb, requested_embedding_profile_digest text, "
        "requested_lease_generation"
    )
    old_validation = """THEN RETURN; END IF;
            IF jsonb_typeof(requested_embedding_document) IS DISTINCT FROM 'array'"""
    new_validation = """THEN RETURN; END IF;
            IF requested_embedding_profile_digest !~ '^[0-9a-f]{64}$'
            THEN RETURN; END IF;
            IF jsonb_typeof(requested_embedding_document) IS DISTINCT FROM 'array'"""
    old_insert = """projection_kind, embedding
            )
            SELECT requested_organization_id, requested_resource_ref,
                   requested_revision_id, item.fragment->>'fragmentRef',
                   (item.ordinal - 1)::integer,
                   item.fragment->>'contextualText', 'body',
                   (embedded.item->'embedding')::text::public.vector"""
    new_insert = """projection_kind, embedding, embedding_profile_digest
            )
            SELECT requested_organization_id, requested_resource_ref,
                   requested_revision_id, item.fragment->>'fragmentRef',
                   (item.ordinal - 1)::integer,
                   item.fragment->>'contextualText', 'body',
                   (embedded.item->'embedding')::text::public.vector,
                   requested_embedding_profile_digest"""
    for old, new in (
        (old_declaration, new_declaration),
        (old_validation, new_validation),
        (old_insert, new_insert),
    ):
        definition = _replace_exact(
            definition,
            old if add_profile else new,
            new if add_profile else old,
        )
    _install(definition, _WORKER_DEFINER)
    op.execute(f"SET LOCAL ROLE {_WORKER_DEFINER}")
    op.execute(f"REVOKE ALL ON FUNCTION public.{target} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.{target} TO {_WORKER}")
    op.execute(f"REVOKE EXECUTE ON FUNCTION public.{source} FROM {_WORKER}")
    op.execute(f"DROP FUNCTION public.{source}")
    op.execute("RESET ROLE")


def _replace_signature(
    source: str,
    target: str,
    replacements: tuple[tuple[str, str], ...],
    *,
    grant_worker: bool,
) -> None:
    definition = _function_definition(source)
    for searched, replacement in replacements:
        definition = _replace_exact(definition, searched, replacement)
    _install(definition, _WORKER_DEFINER)
    op.execute(f"SET LOCAL ROLE {_WORKER_DEFINER}")
    op.execute(f"REVOKE ALL ON FUNCTION public.{target} FROM PUBLIC")
    if grant_worker:
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{target} TO {_WORKER}")
        op.execute(f"REVOKE EXECUTE ON FUNCTION public.{source} FROM {_WORKER}")
    op.execute(f"DROP FUNCTION public.{source}")
    op.execute("RESET ROLE")


def _replace_classification(*, add_profile: bool) -> None:
    old_declaration = (
        "requested_config_version text, requested_signing_key_version bigint"
    )
    new_declaration = (
        "requested_config_version text, requested_embedding_profile_digest text, "
        "requested_signing_key_version bigint"
    )
    old_validation = """OR btrim(requested_config_version) = ''
            THEN RETURN; END IF;"""
    new_validation = """OR btrim(requested_config_version) = ''
               OR requested_embedding_profile_digest !~ '^[0-9a-f]{64}$'
            THEN RETURN; END IF;"""
    old_compatibility = """AND (embedded_fragment.embedding IS NULL
                         OR public.vector_dims(embedded_fragment.embedding)
                            <> 384)"""
    new_compatibility = """AND (embedded_fragment.embedding IS NULL
                         OR public.vector_dims(embedded_fragment.embedding)
                            <> 384
                         OR embedded_fragment.embedding_profile_digest
                            IS DISTINCT FROM requested_embedding_profile_digest)"""
    pairs = (
        (old_declaration, new_declaration),
        (old_validation, new_validation),
        (old_compatibility, new_compatibility),
    )
    _replace_signature(
        _CLASSIFY_OLD if add_profile else _CLASSIFY_NEW,
        _CLASSIFY_NEW if add_profile else _CLASSIFY_OLD,
        tuple((old, new) if add_profile else (new, old) for old, new in pairs),
        grant_worker=False,
    )


def _install_legacy_classification_refusal() -> None:
    """Keep old definer callers closed without inventing a profile identity."""

    definition = """
CREATE FUNCTION public.context_worker_classify_file_import_internal(
    requested_organization_id uuid,
    requested_job_id uuid,
    requested_service_principal_id uuid,
    requested_source_ref text,
    requested_resource_ref text,
    requested_canonical_text text,
    requested_content_hash text,
    requested_compiler_version text,
    requested_config_version text,
    requested_signing_key_version bigint,
    requested_nonce bytea,
    requested_issued_at timestamp with time zone,
    requested_expires_at timestamp with time zone
) RETURNS TABLE(
    classification text,
    active_revision_id uuid,
    fragment_refs text[],
    content_identity_digest text,
    reason_digest text
) LANGUAGE plpgsql SECURITY DEFINER
SET search_path TO 'pg_catalog', 'pg_temp'
SET row_security TO 'on'
AS $function$
BEGIN
    RETURN;
END;
$function$
"""
    _install(definition, _WORKER_DEFINER)
    op.execute(f"SET LOCAL ROLE {_WORKER_DEFINER}")
    op.execute(f"REVOKE ALL ON FUNCTION public.{_CLASSIFY_OLD} FROM PUBLIC")
    op.execute("RESET ROLE")


def _drop_legacy_classification_refusal() -> None:
    op.execute(f"SET LOCAL ROLE {_WORKER_DEFINER}")
    op.execute(f"DROP FUNCTION public.{_CLASSIFY_OLD}")
    op.execute("RESET ROLE")


def _replace_acquisition(*, add_profile: bool) -> None:
    old_declaration = (
        "requested_config_version text, requested_compilation_document jsonb"
    )
    new_declaration = (
        "requested_config_version text, requested_embedding_profile_digest text, "
        "requested_compilation_document jsonb"
    )
    old_validation = """OR requested_revision_id IS NULL
               OR btrim(requested_resource_ref) = ''"""
    new_validation = """OR requested_revision_id IS NULL
               OR requested_embedding_profile_digest !~ '^[0-9a-f]{64}$'
               OR btrim(requested_resource_ref) = ''"""
    old_classification = """requested_config_version, requested_signing_key_version,
                requested_nonce"""
    new_classification = """requested_config_version,
                requested_embedding_profile_digest, requested_signing_key_version,
                requested_nonce"""
    old_noop = """AND (fragment.embedding IS NULL
                                 OR public.vector_dims(fragment.embedding)
                                    <> 384)"""
    new_noop = """AND (fragment.embedding IS NULL
                                 OR public.vector_dims(fragment.embedding)
                                    <> 384
                                 OR fragment.embedding_profile_digest
                                    IS DISTINCT FROM requested_embedding_profile_digest)"""
    old_columns = """publication_payload_digest, compiler_version, config_version,
                created_at, updated_at"""
    new_columns = """publication_payload_digest, compiler_version, config_version,
                embedding_profile_digest, created_at, updated_at"""
    old_values = """requested_compiler_version, requested_config_version,
                now_at, now_at"""
    new_values = """requested_compiler_version, requested_config_version,
                requested_embedding_profile_digest, now_at, now_at"""
    pairs = (
        (old_declaration, new_declaration),
        (old_validation, new_validation),
        (old_classification, new_classification),
        (old_noop, new_noop),
        (old_columns, new_columns),
        (old_values, new_values),
    )
    _replace_signature(
        _ACQUIRE_OLD if add_profile else _ACQUIRE_NEW,
        _ACQUIRE_NEW if add_profile else _ACQUIRE_OLD,
        tuple((old, new) if add_profile else (new, old) for old, new in pairs),
        grant_worker=True,
    )


def _replace_guard(regprocedure: str, old: str, new: str, *, add_profile: bool) -> None:
    definition = _function_definition(regprocedure)
    searched, replacement = (old, new) if add_profile else (new, old)
    _install(_replace_exact(definition, searched, replacement), _WORKER_DEFINER)


def _replace_publication_guards(*, add_profile: bool) -> None:
    prepare_old = """AND recovery.publication_payload_digest = ("""
    prepare_new = """AND recovery.embedding_profile_digest =
                  requested_embedding_profile_digest
              AND recovery.publication_payload_digest = ("""
    index_old = """OR public.vector_dims(fragment.embedding) <> 384)"""
    index_new = """OR public.vector_dims(fragment.embedding) <> 384
                           OR fragment.embedding_profile_digest
                              IS DISTINCT FROM recovery_row.embedding_profile_digest)"""
    activate_old = """OR public.vector_dims(fragment.embedding) <> 384)
            ) THEN RETURN; END IF;"""
    activate_new = """OR public.vector_dims(fragment.embedding) <> 384
                       OR fragment.embedding_profile_digest IS DISTINCT FROM (
                            SELECT recovery.embedding_profile_digest
                            FROM public.file_publication_recovery AS recovery
                            WHERE recovery.organization_id = requested_organization_id
                              AND recovery.job_id = requested_job_id
                              AND recovery.revision_id = requested_revision_id
                              AND recovery.checkpoint = 'ready'
                       ))
            ) THEN RETURN; END IF;"""
    _replace_guard(
        _PREPARE_NEW,
        prepare_old,
        prepare_new,
        add_profile=add_profile,
    )
    _replace_guard(_INDEX, index_old, index_new, add_profile=add_profile)
    _replace_guard(_ACTIVATE, activate_old, activate_new, add_profile=add_profile)


def _replace_promotion(*, add_profile_gate: bool) -> None:
    definition = _function_definition(_PROMOTE)
    old = """AND manifest_row.curation_mode = 'curation_off'"""
    new = """AND manifest_row.curation_mode = 'curation_off'
                      AND jsonb_array_length(
                          manifest_row.active_revision_refs
                      ) > 0
                      AND NOT EXISTS (
                           SELECT 1
                           FROM jsonb_array_elements_text(
                               manifest_row.active_revision_refs
                           ) AS selected(revision_ref)
                           WHERE selected.revision_ref !~
                                 '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                              OR NOT EXISTS (
                                   SELECT 1
                                   FROM public.context_fragment AS fragment
                                   WHERE fragment.organization_id =
                                         requested_organization_id
                                     AND fragment.revision_id =
                                         selected.revision_ref::uuid
                                     AND fragment.embedding IS NOT NULL
                                     AND fragment.embedding_profile_digest =
                                         manifest_row.embedding_profile_digest
                              )
                              OR EXISTS (
                                   SELECT 1
                                   FROM public.context_fragment AS fragment
                                   WHERE fragment.organization_id =
                                         requested_organization_id
                                     AND fragment.revision_id =
                                         selected.revision_ref::uuid
                                     AND (
                                         fragment.embedding IS NULL
                                         OR fragment.embedding_profile_digest
                                            IS DISTINCT FROM
                                            manifest_row.embedding_profile_digest
                                     )
                              )
                      )"""
    definition = _replace_exact(
        definition,
        old if add_profile_gate else new,
        new if add_profile_gate else old,
    )
    _install(definition, _RELEASE_DEFINER)


def upgrade() -> None:
    """Persist exact identity and refuse partial or mixed profile activation."""

    op.execute(
        "LOCK TABLE public.release_manifest, "
        "public.file_publication_recovery, public.file_import_job, "
        "public.file_revision_replacement_plan, public.exact_phrase_candidate, "
        "public.revision_publication_event, public.file_revision_snapshot, "
        "public.context_fragment, public.context_revision, "
        "public.resource_access_policy, "
        "public.membership_resource_field_right "
        "IN ACCESS EXCLUSIVE MODE"
    )
    retained_vectors = op.get_bind().execute(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM public.context_fragment WHERE embedding IS NOT NULL"
            ")"
        )
    ).scalar_one()
    if retained_vectors is True:
        raise RuntimeError(
            "embedding profile upgrade requires a provenance-free corpus"
        )
    op.add_column(
        "release_manifest",
        sa.Column(
            "embedding_profile_document",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.literal_column(
                "'" + _TWIN_PROFILE_DOCUMENT + "'::jsonb"
            ),
        ),
    )
    op.add_column(
        "release_manifest",
        sa.Column(
            "embedding_profile_digest",
            sa.Text(),
            nullable=False,
            server_default=_TWIN_PROFILE_DIGEST,
        ),
    )
    op.create_check_constraint(
        "ck_release_manifest_embedding_profile",
        "release_manifest",
        "jsonb_typeof(embedding_profile_document) = 'object' "
        "AND char_length(embedding_profile_digest) = 64 "
        "AND embedding_profile_digest = lower(embedding_profile_digest) "
        "AND embedding_profile_digest ~ '^[0-9a-f]{64}$'",
    )
    op.alter_column("release_manifest", "embedding_profile_document", server_default=None)
    op.alter_column("release_manifest", "embedding_profile_digest", server_default=None)
    op.add_column(
        "context_fragment",
        sa.Column(
            "embedding_profile_digest",
            sa.Text(),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_context_fragment_embedding_profile",
        "context_fragment",
        "(embedding IS NULL AND embedding_profile_digest IS NULL) OR "
        "(embedding IS NOT NULL AND embedding_profile_digest IS NOT NULL "
        "AND char_length(embedding_profile_digest) = 64 "
        "AND embedding_profile_digest = lower(embedding_profile_digest) "
        "AND embedding_profile_digest ~ '^[0-9a-f]{64}$')",
    )
    op.add_column(
        "file_publication_recovery",
        sa.Column(
            "embedding_profile_digest",
            sa.Text(),
            nullable=False,
            server_default=_TWIN_PROFILE_DIGEST,
        ),
    )
    op.create_check_constraint(
        "ck_file_publication_recovery_embedding_profile",
        "file_publication_recovery",
        "char_length(embedding_profile_digest) = 64 "
        "AND embedding_profile_digest = lower(embedding_profile_digest) "
        "AND embedding_profile_digest ~ '^[0-9a-f]{64}$'",
    )
    op.alter_column(
        "file_publication_recovery",
        "embedding_profile_digest",
        server_default=None,
    )
    _replace_classification(add_profile=True)
    _install_legacy_classification_refusal()
    _replace_acquisition(add_profile=True)
    _replace_prepare(add_profile=True)
    _replace_publication_guards(add_profile=True)
    op.execute(f"GRANT SELECT ON TABLE public.context_fragment TO {_RELEASE_DEFINER}")
    op.execute(
        "CREATE POLICY context_fragment_release_definer_select "
        "ON context_fragment FOR SELECT TO context_engine_release_definer "
        "USING (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid)"
    )
    _replace_promotion(add_profile_gate=True)


def downgrade() -> None:
    """Remove profile binding only when no non-twin lineage would be lost."""

    op.execute(
        "LOCK TABLE public.release_manifest, "
        "public.file_publication_recovery, public.file_import_job, "
        "public.file_revision_replacement_plan, public.exact_phrase_candidate, "
        "public.revision_publication_event, public.file_revision_snapshot, "
        "public.context_fragment, public.context_revision, "
        "public.resource_access_policy, "
        "public.membership_resource_field_right "
        "IN ACCESS EXCLUSIVE MODE"
    )
    retained = op.get_bind().execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM release_manifest WHERE embedding_profile_digest <> :digest) "
            "OR EXISTS (SELECT 1 FROM context_fragment WHERE embedding_profile_digest <> :digest)"
        ),
        {"digest": _TWIN_PROFILE_DIGEST},
    ).scalar_one()
    if retained is True:
        raise RuntimeError("embedding profile downgrade requires twin-only lineage")
    _replace_promotion(add_profile_gate=False)
    op.execute("DROP POLICY context_fragment_release_definer_select ON context_fragment")
    op.execute(f"REVOKE SELECT ON TABLE public.context_fragment FROM {_RELEASE_DEFINER}")
    _replace_publication_guards(add_profile=False)
    _replace_prepare(add_profile=False)
    _replace_acquisition(add_profile=False)
    _drop_legacy_classification_refusal()
    _replace_classification(add_profile=False)
    op.drop_constraint(
        "ck_file_publication_recovery_embedding_profile",
        "file_publication_recovery",
        type_="check",
    )
    op.drop_column("file_publication_recovery", "embedding_profile_digest")
    op.drop_constraint(
        "ck_context_fragment_embedding_profile",
        "context_fragment",
        type_="check",
    )
    op.drop_column("context_fragment", "embedding_profile_digest")
    op.drop_constraint(
        "ck_release_manifest_embedding_profile",
        "release_manifest",
        type_="check",
    )
    op.drop_column("release_manifest", "embedding_profile_digest")
    op.drop_column("release_manifest", "embedding_profile_document")
