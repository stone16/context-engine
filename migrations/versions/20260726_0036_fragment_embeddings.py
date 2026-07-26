"""Persist Supply-side Fragment embeddings before Revision activation.

Revision ID: 20260726_0036
Revises: 20260726_0035
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260726_0036"
down_revision: str | None = "20260726_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFINER = "context_engine_worker_lease_definer"
_WORKER = "context_engine_worker"
_DIMENSION = 384
_PREPARE = "context_worker_prepare_file_publication"
_OLD_PREPARE_SIGNATURE = (
    "(uuid,uuid,uuid,text,text,uuid,text,jsonb,jsonb,bigint,bigint,bytea,"
    "timestamp with time zone,timestamp with time zone)"
)
_NEW_PREPARE_SIGNATURE = (
    "(uuid,uuid,uuid,text,text,uuid,text,jsonb,jsonb,jsonb,bigint,bigint,bytea,"
    "timestamp with time zone,timestamp with time zone)"
)
_INDEX_REGPROCEDURE = (
    "context_worker_index_file_publication"
    "(uuid,uuid,uuid,text,text,uuid,text,jsonb,jsonb,bigint,bigint,bytea,"
    "timestamp with time zone,timestamp with time zone)"
)
_ACQUIRE_REGPROCEDURE = (
    "context_worker_acquire_file_publication"
    "(uuid,uuid,uuid,text,text,uuid,text,text,text,text,text,jsonb,jsonb,"
    "bigint,bigint,bytea,timestamp with time zone,timestamp with time zone)"
)
_CLASSIFY_REGPROCEDURE = (
    "context_worker_classify_file_import_internal"
    "(uuid,uuid,uuid,text,text,text,text,text,text,bigint,bytea,"
    "timestamp with time zone,timestamp with time zone)"
)
_ACTIVATE_REGPROCEDURE = (
    "context_worker_activate_recoverable_file_publication"
    "(uuid,uuid,uuid,text,text,uuid,bigint,bigint,bytea,"
    "timestamp with time zone,timestamp with time zone)"
)
_SIGNATURE_OLD = "requested_artifact_document jsonb, requested_lease_generation"
_SIGNATURE_NEW = (
    "requested_artifact_document jsonb, requested_embedding_document jsonb, "
    "requested_lease_generation"
)
_ENTRY_ANCHOR = (
    "THEN RETURN; END IF;\n"
    "            PERFORM pg_catalog.set_config('app.organization_id'"
)
_EMBEDDING_VALIDATION = f"""THEN RETURN; END IF;
            IF jsonb_typeof(requested_embedding_document) IS DISTINCT FROM 'array'
               OR jsonb_array_length(requested_embedding_document)
                    <> jsonb_array_length(requested_artifact_document)
            THEN RETURN; END IF;
            IF EXISTS (
                SELECT 1
                FROM jsonb_array_elements(requested_embedding_document)
                    WITH ORDINALITY AS embedded(item, ordinal)
                JOIN jsonb_array_elements(requested_artifact_document)
                    WITH ORDINALITY AS artifact(item, ordinal)
                USING (ordinal)
                WHERE jsonb_typeof(embedded.item) IS DISTINCT FROM 'object'
                   OR embedded.item->>'fragmentRef'
                        IS DISTINCT FROM artifact.item->>'fragmentRef'
                   OR jsonb_typeof(embedded.item->'embedding')
                        IS DISTINCT FROM 'array'
                   OR jsonb_array_length(embedded.item->'embedding') <> {_DIMENSION}
                   OR EXISTS (
                        SELECT 1
                        FROM jsonb_array_elements(embedded.item->'embedding')
                            AS component(value)
                        WHERE jsonb_typeof(component.value) <> 'number'
                           OR char_length(component.value::text) > 64
                           OR component.value::text
                                !~ '^-?[0-9]+(\\.[0-9]+)?([eE][+-]?[0-9]+)?$'
                           OR CASE
                                WHEN jsonb_typeof(component.value) = 'number'
                                 AND char_length(component.value::text) <= 64
                                 AND component.value::text
                                      ~ '^-?[0-9]+(\\.[0-9]+)?([eE][+-]?[0-9]+)?$'
                                THEN abs((component.value::text)::numeric) > 1.0e30
                                ELSE false
                              END
                   )
                   OR NOT EXISTS (
                        SELECT 1
                        FROM jsonb_array_elements(embedded.item->'embedding')
                            AS component(value)
                        WHERE CASE
                                WHEN jsonb_typeof(component.value) = 'number'
                                 AND char_length(component.value::text) <= 64
                                 AND component.value::text
                                      ~ '^-?[0-9]+(\\.[0-9]+)?([eE][+-]?[0-9]+)?$'
                                THEN abs((component.value::text)::numeric)
                                     > 7.006492321624085e-46
                                ELSE false
                              END
                   )
            ) THEN RETURN; END IF;
            PERFORM pg_catalog.set_config('app.organization_id'"""
_INSERT_OLD = """            INSERT INTO public.context_fragment (
                organization_id, resource_ref, revision_id, fragment_ref,
                ordinal, content, projection_kind
            )
            SELECT requested_organization_id, requested_resource_ref,
                   requested_revision_id, item.fragment->>'fragmentRef',
                   (item.ordinal - 1)::integer,
                   item.fragment->>'contextualText', 'body'
            FROM jsonb_array_elements(requested_artifact_document)
                WITH ORDINALITY AS item(fragment, ordinal)
            ORDER BY item.ordinal;"""
_INSERT_NEW = """            INSERT INTO public.context_fragment (
                organization_id, resource_ref, revision_id, fragment_ref,
                ordinal, content, projection_kind, embedding
            )
            SELECT requested_organization_id, requested_resource_ref,
                   requested_revision_id, item.fragment->>'fragmentRef',
                   (item.ordinal - 1)::integer,
                   item.fragment->>'contextualText', 'body',
                   (embedded.item->'embedding')::text::public.vector
            FROM jsonb_array_elements(requested_artifact_document)
                WITH ORDINALITY AS item(fragment, ordinal)
            JOIN jsonb_array_elements(requested_embedding_document)
                WITH ORDINALITY AS embedded(item, ordinal)
            USING (ordinal)
            ORDER BY item.ordinal;"""
_INDEX_ANCHOR = """            IF recovery_row.job_id IS NULL
               OR NOT EXISTS ("""
_NOOP_ANCHOR = """                      AND NOT EXISTS (
                          SELECT 1 FROM jsonb_array_elements(
                              requested_artifact_document
                          ) WITH ORDINALITY AS expected(fragment, ordinal)"""
_NOOP_WITH_EMBEDDINGS = f"""                      AND NOT EXISTS (
                          SELECT 1 FROM public.context_fragment AS fragment
                          WHERE fragment.organization_id = requested_organization_id
                            AND fragment.resource_ref = requested_resource_ref
                            AND fragment.revision_id = decision.active_revision_id
                            AND (fragment.embedding IS NULL
                                 OR public.vector_dims(fragment.embedding)
                                    <> {_DIMENSION})
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM jsonb_array_elements(
                              requested_artifact_document
                          ) WITH ORDINALITY AS expected(fragment, ordinal)"""
_CLASSIFY_ANCHOR = """
              AND snapshot.config_version = requested_config_version
              AND (
                  SELECT array_agg(event.state ORDER BY event.ordinal)"""
_CLASSIFY_WITH_EMBEDDINGS = f"""
              AND snapshot.config_version = requested_config_version
              AND NOT EXISTS (
                  SELECT 1 FROM public.context_fragment AS embedded_fragment
                  WHERE embedded_fragment.organization_id = resource.organization_id
                    AND embedded_fragment.resource_ref = resource.resource_ref
                    AND embedded_fragment.revision_id = resource.active_revision_id
                    AND (embedded_fragment.embedding IS NULL
                         OR public.vector_dims(embedded_fragment.embedding)
                            <> {_DIMENSION})
              )
              AND (
                  SELECT array_agg(event.state ORDER BY event.ordinal)"""
_INDEX_WITH_EMBEDDINGS = f"""            IF recovery_row.job_id IS NULL
               OR EXISTS (
                    SELECT 1 FROM public.context_fragment AS fragment
                    WHERE fragment.organization_id = requested_organization_id
                      AND fragment.resource_ref = requested_resource_ref
                      AND fragment.revision_id = requested_revision_id
                      AND (fragment.embedding IS NULL
                           OR public.vector_dims(fragment.embedding) <> {_DIMENSION})
               )
               OR NOT EXISTS ("""
_ACTIVATE_ANCHOR = """            RETURN QUERY
            SELECT wrapped.effect_count"""
_ACTIVATE_WITH_EMBEDDINGS = f"""            IF EXISTS (
                SELECT 1 FROM public.context_fragment AS fragment
                WHERE fragment.organization_id = requested_organization_id
                  AND fragment.resource_ref = requested_resource_ref
                  AND fragment.revision_id = requested_revision_id
                  AND (fragment.embedding IS NULL
                       OR public.vector_dims(fragment.embedding) <> {_DIMENSION})
            ) THEN RETURN; END IF;
            RETURN QUERY
            SELECT wrapped.effect_count"""


def _rewind_unembedded_recovery() -> None:
    """Return pre-0036 staged Revisions to the provider-backed boundary."""

    op.execute(
        "LOCK TABLE public.file_publication_recovery, public.file_import_job, "
        "public.file_revision_replacement_plan, public.exact_phrase_candidate, "
        "public.revision_publication_event, public.file_revision_snapshot, "
        "public.context_fragment, public.context_revision, "
        "public.resource_access_policy, "
        "public.membership_resource_field_right "
        "IN ACCESS EXCLUSIVE MODE"
    )
    op.execute(
        "ALTER TABLE public.file_revision_replacement_plan DISABLE TRIGGER "
        "file_revision_replacement_plan_immutable"
    )
    op.execute(
        "ALTER TABLE public.exact_phrase_candidate DISABLE TRIGGER "
        "exact_phrase_candidate_immutable"
    )
    op.execute(
        "ALTER TABLE public.revision_publication_event DISABLE TRIGGER "
        "revision_publication_event_immutable"
    )
    op.execute(
        "ALTER TABLE public.file_revision_snapshot DISABLE TRIGGER "
        "file_revision_snapshot_immutable"
    )
    op.execute(
        "ALTER TABLE public.context_fragment DISABLE TRIGGER "
        "context_fragment_reject_mutation"
    )
    op.execute(
        "ALTER TABLE public.context_revision DISABLE TRIGGER "
        "context_revision_reject_mutation"
    )
    op.execute(
        "ALTER TABLE public.membership_resource_field_right DISABLE TRIGGER "
        "membership_resource_field_right_mutation_lock"
    )
    op.execute(
        """
        CREATE TEMPORARY TABLE context_embedding_rewind
        ON COMMIT DROP AS
        SELECT recovery.organization_id, recovery.job_id,
               recovery.resource_ref, recovery.revision_id,
               recovery.previous_revision_id, recovery.publication_kind
        FROM public.file_publication_recovery AS recovery
        JOIN public.file_import_job AS job
          ON job.organization_id = recovery.organization_id
         AND job.job_id = recovery.job_id
         AND job.resource_ref = recovery.resource_ref
         AND job.revision_id = recovery.revision_id
        WHERE recovery.checkpoint IN ('prepared', 'ready')
          AND (
              job.state IN ('prepared', 'ready')
              OR (
                  job.state = 'leased'
                  AND job.recovery_from_state IN ('prepared', 'ready')
              )
          )
          AND NOT EXISTS (
              SELECT 1 FROM public.context_resource AS resource
              WHERE resource.organization_id = recovery.organization_id
                AND resource.resource_ref = recovery.resource_ref
                AND resource.active_revision_id = recovery.revision_id
          )
        """
    )
    op.execute(
        """
        DELETE FROM public.membership_resource_field_right AS field_right
        USING context_embedding_rewind AS rewind
        WHERE rewind.publication_kind = 'initial'
          AND field_right.organization_id = rewind.organization_id
          AND field_right.resource_ref = rewind.resource_ref
        """
    )
    op.execute(
        """
        DELETE FROM public.resource_access_policy AS access_policy
        USING context_embedding_rewind AS rewind
        WHERE rewind.publication_kind = 'initial'
          AND access_policy.organization_id = rewind.organization_id
          AND access_policy.resource_ref = rewind.resource_ref
        """
    )
    op.execute(
        """
        DELETE FROM public.exact_phrase_candidate AS candidate
        USING context_embedding_rewind AS rewind
        WHERE candidate.organization_id = rewind.organization_id
          AND candidate.resource_ref = rewind.resource_ref
          AND candidate.revision_id = rewind.revision_id
        """
    )
    op.execute(
        """
        DELETE FROM public.revision_publication_event AS event
        USING context_embedding_rewind AS rewind
        WHERE event.organization_id = rewind.organization_id
          AND event.resource_ref = rewind.resource_ref
          AND event.revision_id = rewind.revision_id
        """
    )
    op.execute(
        """
        DELETE FROM public.file_revision_replacement_plan AS plan
        USING context_embedding_rewind AS rewind
        WHERE plan.organization_id = rewind.organization_id
          AND plan.resource_ref = rewind.resource_ref
          AND plan.replacement_revision_id = rewind.revision_id
        """
    )
    op.execute(
        """
        DELETE FROM public.context_fragment AS fragment
        USING context_embedding_rewind AS rewind
        WHERE fragment.organization_id = rewind.organization_id
          AND fragment.resource_ref = rewind.resource_ref
          AND fragment.revision_id = rewind.revision_id
        """
    )
    op.execute(
        """
        DELETE FROM public.file_revision_snapshot AS snapshot
        USING context_embedding_rewind AS rewind
        WHERE snapshot.organization_id = rewind.organization_id
          AND snapshot.resource_ref = rewind.resource_ref
          AND snapshot.revision_id = rewind.revision_id
        """
    )
    op.execute(
        """
        DELETE FROM public.context_revision AS revision
        USING context_embedding_rewind AS rewind
        WHERE revision.organization_id = rewind.organization_id
          AND revision.resource_ref = rewind.resource_ref
          AND revision.revision_id = rewind.revision_id
        """
    )
    op.execute(
        """
        UPDATE public.file_publication_recovery AS recovery
        SET checkpoint = 'acquired', updated_at = pg_catalog.statement_timestamp()
        FROM context_embedding_rewind AS rewind
        WHERE recovery.organization_id = rewind.organization_id
          AND recovery.job_id = rewind.job_id
        """
    )
    op.execute(
        """
        UPDATE public.file_import_job AS job
        SET state = CASE
                WHEN job.state = 'leased' THEN 'leased'
                ELSE 'running'
            END,
            recovery_from_state = CASE
                WHEN job.state = 'leased' THEN 'running'
                ELSE NULL
            END,
            fragment_ref = NULL
        FROM context_embedding_rewind AS rewind
        WHERE job.organization_id = rewind.organization_id
          AND job.job_id = rewind.job_id
        """
    )
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    op.execute(
        "ALTER TABLE public.membership_resource_field_right ENABLE TRIGGER "
        "membership_resource_field_right_mutation_lock"
    )
    op.execute(
        "ALTER TABLE public.context_revision ENABLE TRIGGER "
        "context_revision_reject_mutation"
    )
    op.execute(
        "ALTER TABLE public.context_fragment ENABLE TRIGGER "
        "context_fragment_reject_mutation"
    )
    op.execute(
        "ALTER TABLE public.file_revision_snapshot ENABLE TRIGGER "
        "file_revision_snapshot_immutable"
    )
    op.execute(
        "ALTER TABLE public.revision_publication_event ENABLE TRIGGER "
        "revision_publication_event_immutable"
    )
    op.execute(
        "ALTER TABLE public.exact_phrase_candidate ENABLE TRIGGER "
        "exact_phrase_candidate_immutable"
    )
    op.execute(
        "ALTER TABLE public.file_revision_replacement_plan ENABLE TRIGGER "
        "file_revision_replacement_plan_immutable"
    )


def _function_definition(regprocedure: str) -> str:
    definition = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT pg_catalog.pg_get_functiondef("
                f"'public.{regprocedure}'::regprocedure)"
            )
        )
        .scalar_one()
    )
    if not isinstance(definition, str):
        raise RuntimeError("File publication function definition is unavailable")
    return definition


def _replace_exact(definition: str, searched: str, replacement: str) -> str:
    if definition.count(searched) != 1:
        raise RuntimeError("File publication function shape was not recognized")
    return definition.replace(searched, replacement)


def _install_definition(definition: str) -> None:
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(definition)
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


def _replace_prepare(*, add_embeddings: bool) -> None:
    old_signature, new_signature = (
        (_OLD_PREPARE_SIGNATURE, _NEW_PREPARE_SIGNATURE)
        if add_embeddings
        else (_NEW_PREPARE_SIGNATURE, _OLD_PREPARE_SIGNATURE)
    )
    definition = _function_definition(f"{_PREPARE}{old_signature}")
    replacements = (
        (
            (_SIGNATURE_OLD, _SIGNATURE_NEW),
            (_ENTRY_ANCHOR, _EMBEDDING_VALIDATION),
            (_INSERT_OLD, _INSERT_NEW),
        )
        if add_embeddings
        else (
            (_SIGNATURE_NEW, _SIGNATURE_OLD),
            (_EMBEDDING_VALIDATION, _ENTRY_ANCHOR),
            (_INSERT_NEW, _INSERT_OLD),
        )
    )
    for searched, replacement in replacements:
        definition = _replace_exact(definition, searched, replacement)
    _install_definition(definition)
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(f"REVOKE ALL ON FUNCTION public.{_PREPARE}{new_signature} FROM PUBLIC")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_PREPARE}{new_signature} TO {_WORKER}"
    )
    op.execute(
        f"REVOKE EXECUTE ON FUNCTION public.{_PREPARE}{old_signature} FROM {_WORKER}"
    )
    op.execute(f"DROP FUNCTION public.{_PREPARE}{old_signature}")
    op.execute("RESET ROLE")


def _replace_guard(
    regprocedure: str,
    *,
    add_embeddings: bool,
    old: str,
    new: str,
) -> None:
    definition = _function_definition(regprocedure)
    searched, replacement = (old, new) if add_embeddings else (new, old)
    _install_definition(_replace_exact(definition, searched, replacement))


def upgrade() -> None:
    """Store one fixed-dimension vector with each newly published Fragment."""

    op.add_column(
        "context_fragment",
        sa.Column("embedding", sa.Text(), nullable=True),
    )
    op.execute(
        "ALTER TABLE public.context_fragment "
        f"ALTER COLUMN embedding TYPE public.vector({_DIMENSION}) "
        "USING embedding::public.vector"
    )
    op.execute(
        "CREATE INDEX ix_context_fragment_embedding_hnsw "
        "ON public.context_fragment USING hnsw "
        "(embedding public.vector_cosine_ops) WHERE embedding IS NOT NULL"
    )
    _rewind_unembedded_recovery()
    _replace_prepare(add_embeddings=True)
    _replace_guard(
        _CLASSIFY_REGPROCEDURE,
        add_embeddings=True,
        old=_CLASSIFY_ANCHOR,
        new=_CLASSIFY_WITH_EMBEDDINGS,
    )
    _replace_guard(
        _ACQUIRE_REGPROCEDURE,
        add_embeddings=True,
        old=_NOOP_ANCHOR,
        new=_NOOP_WITH_EMBEDDINGS,
    )
    _replace_guard(
        _INDEX_REGPROCEDURE,
        add_embeddings=True,
        old=_INDEX_ANCHOR,
        new=_INDEX_WITH_EMBEDDINGS,
    )
    _replace_guard(
        _ACTIVATE_REGPROCEDURE,
        add_embeddings=True,
        old=_ACTIVATE_ANCHOR,
        new=_ACTIVATE_WITH_EMBEDDINGS,
    )


def downgrade() -> None:
    """Restore vector-free publication while preserving Fragment content."""

    op.execute("LOCK TABLE public.context_fragment IN ACCESS EXCLUSIVE MODE")
    _replace_guard(
        _ACTIVATE_REGPROCEDURE,
        add_embeddings=False,
        old=_ACTIVATE_ANCHOR,
        new=_ACTIVATE_WITH_EMBEDDINGS,
    )
    _replace_guard(
        _INDEX_REGPROCEDURE,
        add_embeddings=False,
        old=_INDEX_ANCHOR,
        new=_INDEX_WITH_EMBEDDINGS,
    )
    _replace_guard(
        _ACQUIRE_REGPROCEDURE,
        add_embeddings=False,
        old=_NOOP_ANCHOR,
        new=_NOOP_WITH_EMBEDDINGS,
    )
    _replace_guard(
        _CLASSIFY_REGPROCEDURE,
        add_embeddings=False,
        old=_CLASSIFY_ANCHOR,
        new=_CLASSIFY_WITH_EMBEDDINGS,
    )
    _replace_prepare(add_embeddings=False)
    op.drop_index("ix_context_fragment_embedding_hnsw", table_name="context_fragment")
    op.drop_column("context_fragment", "embedding")
