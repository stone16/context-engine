"""Bind captured feedback to exact authorized delivery lineage.

Revision ID: 20260731_0048
Revises: 20260730_0047
Create Date: 2026-07-31
"""

# ruff: noqa: E501

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260731_0048"
down_revision: str | None = "20260730_0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEARNING = "context_engine_learning"
_DEFINER = "context_engine_context_run_reader_definer"
_OPERATOR = "context_engine_security_operator"
_MAX = (1 << 63) - 1
_READ = "context_learning_read_feedback_evidence"
_READ_SIGNATURE = "(uuid,text)"
_OPERATOR_READ = "read_context_run_by_operator_ticket"
_OPERATOR_READ_SIGNATURE = "(text,uuid,text)"


def _replace_operator_reader(*, install: bool) -> None:
    definition = op.get_bind().execute(
        sa.text(
            "SELECT pg_catalog.pg_get_functiondef(CAST(:procedure AS regprocedure))"
        ),
        {"procedure": f"public.{_OPERATOR_READ}{_OPERATOR_READ_SIGNATURE}"},
    ).scalar_one()
    if not isinstance(definition, str):
        raise RuntimeError("ContextRun reader definition is unavailable")
    old_return = (
        "outcome text, package_digest_profile text, package_digest text, "
        "package_retention_mode text, authorized_evidence_refs jsonb,"
    )
    new_return = (
        "outcome text, package_ref text, package_digest_profile text, "
        "package_digest text, release_ref text, release_generation bigint, "
        "package_retention_mode text, authorized_evidence_refs jsonb, "
        "authorized_citation_lineage jsonb,"
    )
    old_select = (
        "run.outcome,\n                run.package_digest_profile, "
        "run.package_digest,\n                run.package_retention_mode, "
        "run.authorized_evidence_refs,"
    )
    new_select = (
        "run.outcome,\n                run.package_ref, "
        "run.package_digest_profile, run.package_digest,\n                "
        "run.release_ref, run.release_generation,\n                "
        "run.package_retention_mode, run.authorized_evidence_refs,\n                "
        "run.authorized_citation_lineage,"
    )
    searched_return, replacement_return = (
        (old_return, new_return) if install else (new_return, old_return)
    )
    searched_select, replacement_select = (
        (old_select, new_select) if install else (new_select, old_select)
    )
    if definition.count(searched_return) != 1 or definition.count(searched_select) != 1:
        raise RuntimeError("ContextRun reader shape was not recognized")
    replacement = definition.replace(searched_return, replacement_return).replace(
        searched_select, replacement_select
    )
    replacement = replacement.replace("CREATE OR REPLACE FUNCTION", "CREATE FUNCTION", 1)
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(f"DROP FUNCTION public.{_OPERATOR_READ}{_OPERATOR_READ_SIGNATURE}")
    op.execute(replacement)
    op.execute(
        f"REVOKE ALL ON FUNCTION public.{_OPERATOR_READ}"
        f"{_OPERATOR_READ_SIGNATURE} FROM PUBLIC"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_OPERATOR_READ}"
        f"{_OPERATOR_READ_SIGNATURE} TO {_OPERATOR}"
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


def upgrade() -> None:
    """Retain exact authorized lineage and expose one Learning-only inbox read."""

    op.add_column("context_run", sa.Column("package_ref", sa.Text()))
    op.add_column("context_run", sa.Column("release_ref", sa.Text()))
    op.add_column("context_run", sa.Column("release_generation", sa.BigInteger()))
    op.add_column(
        "context_run",
        sa.Column(
            "authorized_citation_lineage",
            postgresql.JSONB(astext_type=sa.Text()),
        ),
    )
    op.create_check_constraint(
        "ck_context_run_feedback_lineage_complete",
        "context_run",
        "(package_ref IS NULL AND release_ref IS NULL "
        "AND release_generation IS NULL "
        "AND authorized_citation_lineage IS NULL) OR ("
        "package_ref ~ '^pkg_[0-9a-f]{32}$' "
        "AND release_ref ~ '^rel_[0-9a-f]{64}$' "
        f"AND release_generation BETWEEN 1 AND {_MAX} "
        "AND jsonb_typeof(authorized_citation_lineage) = 'array' "
        "AND jsonb_array_length(authorized_citation_lineage) = "
        "jsonb_array_length(authorized_evidence_refs) "
        "AND jsonb_path_query_array(authorized_citation_lineage, "
        "'$[*].evidenceRef') = authorized_evidence_refs "
        "AND NOT jsonb_path_exists(authorized_citation_lineage, "
        "'$[*] ? (@.type() != \"object\" "
        "|| !exists(@.evidenceRef) || !exists(@.sourceRef) "
        "|| !exists(@.resourceRef) || !exists(@.revisionRef) "
        "|| !exists(@.fragmentRef) "
        "|| !(@.evidenceRef like_regex \"^ev_[0-9a-f]{64}$\") "
        "|| @.sourceRef.type() != \"string\" "
        "|| @.resourceRef.type() != \"string\" "
        "|| @.revisionRef.type() != \"string\" "
        "|| @.fragmentRef.type() != \"string\" "
        "|| !(@.sourceRef like_regex \".*[^\\\\s\\\\u00a0\\\\u1680"
        "\\\\u2000-\\\\u200a\\\\u2028\\\\u2029\\\\u202f"
        "\\\\u205f\\\\u3000].*\") "
        "|| !(@.resourceRef like_regex \".*[^\\\\s\\\\u00a0\\\\u1680"
        "\\\\u2000-\\\\u200a\\\\u2028\\\\u2029\\\\u202f"
        "\\\\u205f\\\\u3000].*\") "
        "|| !(@.revisionRef like_regex \".*[^\\\\s\\\\u00a0\\\\u1680"
        "\\\\u2000-\\\\u200a\\\\u2028\\\\u2029\\\\u202f"
        "\\\\u205f\\\\u3000].*\") "
        "|| !(@.fragmentRef like_regex \".*[^\\\\s\\\\u00a0\\\\u1680"
        "\\\\u2000-\\\\u200a\\\\u2028\\\\u2029\\\\u202f"
        "\\\\u205f\\\\u3000].*\"))') "
        "AND NOT jsonb_path_exists(authorized_citation_lineage, "
        "'$[*].keyvalue() ? (@.key != \"evidenceRef\" "
        "&& @.key != \"sourceRef\" && @.key != \"resourceRef\" "
        "&& @.key != \"revisionRef\" && @.key != \"fragmentRef\")'))",
    )
    _replace_operator_reader(install=True)
    op.execute(
        "CREATE POLICY context_feedback_learning_definer_select "
        "ON context_feedback FOR SELECT TO "
        f"{_DEFINER} USING ("
        "context_feedback.organization_id = NULLIF(current_setting("
        "'app.learning_feedback_organization_id', true), '')::uuid "
        "AND context_feedback.feedback_ref = current_setting("
        "'app.learning_feedback_ref', true) "
        "AND current_setting('app.learning_feedback_mode', true) = 'read')"
    )
    op.execute(
        "CREATE POLICY context_run_learning_feedback_definer_select "
        "ON context_run FOR SELECT TO "
        f"{_DEFINER} USING ("
        "context_run.organization_id = NULLIF(current_setting("
        "'app.learning_feedback_organization_id', true), '')::uuid "
        "AND current_setting('app.learning_feedback_mode', true) = 'read' "
        "AND EXISTS (SELECT 1 FROM public.context_feedback AS feedback "
        "WHERE feedback.organization_id = context_run.organization_id "
        "AND feedback.run_ref = context_run.run_ref "
        "AND feedback.feedback_ref = current_setting("
        "'app.learning_feedback_ref', true)))"
    )
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"""
        CREATE FUNCTION public.{_READ}(
            requested_organization_id uuid,
            requested_feedback_ref text
        ) RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path = pg_catalog SET row_security = on AS $function$
        DECLARE projection jsonb;
        BEGIN
            IF SESSION_USER <> '{_LEARNING}'
               OR requested_organization_id IS NULL
               OR requested_feedback_ref !~ '^fb_[0-9a-f]{{64}}$'
            THEN RETURN NULL; END IF;
            PERFORM pg_catalog.set_config(
                'app.learning_feedback_mode', 'read', true
            );
            PERFORM pg_catalog.set_config(
                'app.learning_feedback_organization_id',
                requested_organization_id::text, true
            );
            PERFORM pg_catalog.set_config(
                'app.learning_feedback_ref', requested_feedback_ref, true
            );
            SELECT pg_catalog.jsonb_build_object(
                'citations', run.authorized_citation_lineage,
                'feedbackRef', feedback.feedback_ref,
                'note', feedback.note,
                'organizationId', feedback.organization_id::text,
                'packageDigest', run.package_digest,
                'packageRef', run.package_ref,
                'rating', feedback.rating,
                'recordedAt', pg_catalog.to_char(
                    feedback.recorded_at AT TIME ZONE 'UTC',
                    'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                ),
                'releaseGeneration', run.release_generation,
                'releaseRef', run.release_ref,
                'runRef', run.run_ref,
                'schemaVersion', 'context-engine-feedback-evidence-v1'
            ) INTO projection
            FROM public.context_feedback AS feedback
            JOIN public.context_run AS run
              ON run.organization_id = feedback.organization_id
             AND run.run_ref = feedback.run_ref
            WHERE feedback.organization_id = requested_organization_id
              AND feedback.feedback_ref = requested_feedback_ref
              AND run.outcome = 'delivered_authorized'
              AND run.package_ref IS NOT NULL
              AND run.release_ref IS NOT NULL
              AND run.release_generation IS NOT NULL
              AND run.authorized_citation_lineage IS NOT NULL
              AND pg_catalog.jsonb_array_length(
                    run.authorized_citation_lineage) > 0;
            RETURN projection;
        END; $function$
        """
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION public.{_READ}{_READ_SIGNATURE} FROM PUBLIC"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_READ}{_READ_SIGNATURE} TO {_LEARNING}"
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


def downgrade() -> None:
    """Remove the inbox only when no exact feedback lineage would be lost."""

    op.execute(
        "DO $block$ BEGIN IF EXISTS (SELECT 1 FROM context_feedback) "
        "OR EXISTS (SELECT 1 FROM context_run WHERE package_ref IS NOT NULL) "
        "THEN RAISE EXCEPTION USING ERRCODE = '55000', "
        "MESSAGE = 'cannot downgrade with feedback curation lineage'; "
        "END IF; END; $block$"
    )
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(f"DROP FUNCTION public.{_READ}{_READ_SIGNATURE}")
    op.execute("RESET ROLE")
    op.execute(
        "DROP POLICY context_run_learning_feedback_definer_select ON context_run"
    )
    op.execute(
        "DROP POLICY context_feedback_learning_definer_select ON context_feedback"
    )
    _replace_operator_reader(install=False)
    op.drop_constraint(
        "ck_context_run_feedback_lineage_complete",
        "context_run",
        type_="check",
    )
    op.drop_column("context_run", "authorized_citation_lineage")
    op.drop_column("context_run", "release_generation")
    op.drop_column("context_run", "release_ref")
    op.drop_column("context_run", "package_ref")
