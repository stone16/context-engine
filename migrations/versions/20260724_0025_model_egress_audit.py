"""Persist restricted model-generation outcome audit.

Revision ID: 20260724_0025
Revises: 20260724_0024
Create Date: 2026-07-24
"""

# ruff: noqa: E501

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260724_0025"
down_revision: str | None = "20260724_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "model_egress_audit"
_MIGRATOR = "context_engine_migrator"
_EGRESS = "context_engine_egress"
_OPERATOR = "context_engine_security_operator"
_DEFINER = "context_engine_egress_grant_definer"
_RECORD = "context_egress_record_model_outcome"
_CLEANUP = "context_security_delete_expired_model_egress_audit"
_RECORD_SIGNATURE = "(uuid, bytea, bytea, bytea, bytea, bytea, text, bigint, bigint, bigint, bigint, text, bigint, text)"
_CLEANUP_SIGNATURE = "(uuid)"


def upgrade() -> None:
    """Create function-only outcome recording and operator cleanup."""

    op.create_table(
        _TABLE,
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "audit_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("grant_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("package_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("payload_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("question_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("answer_payload_digest", postgresql.BYTEA(), nullable=True),
        sa.Column("outcome_category", sa.Text(), nullable=False),
        sa.Column("provider_calls", sa.BigInteger(), nullable=False),
        sa.Column("cost_microunits", sa.BigInteger(), nullable=False),
        sa.Column("elapsed_ms", sa.BigInteger(), nullable=False),
        sa.Column("output_bytes", sa.BigInteger(), nullable=False),
        sa.Column("profile_ref", sa.Text(), nullable=False),
        sa.Column("audit_profile_ref", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retain_until", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id", "audit_id", name="pk_model_egress_audit"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "grant_digest",
            name="uq_model_egress_audit_exact_grant",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "grant_digest"],
            ["egress_grant.organization_id", "egress_grant.grant_digest"],
            name="fk_model_egress_audit_exact_grant",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "octet_length(grant_digest) = 32 AND octet_length(package_digest) = 32 AND octet_length(payload_digest) = 32 AND octet_length(question_digest) = 32 AND (answer_payload_digest IS NULL OR octet_length(answer_payload_digest) = 32)",
            name="ck_model_egress_audit_sha256_digests",
        ),
        sa.CheckConstraint(
            "outcome_category IN ('generated', 'output_rejected', 'provider_unavailable')",
            name="ck_model_egress_audit_outcome_category",
        ),
        sa.CheckConstraint(
            "provider_calls = 1 AND cost_microunits >= 0 AND elapsed_ms >= 0 AND output_bytes >= 0",
            name="ck_model_egress_audit_usage",
        ),
        sa.CheckConstraint(
            "((outcome_category = 'generated' AND answer_payload_digest IS NOT NULL) OR (outcome_category <> 'generated' AND answer_payload_digest IS NULL))",
            name="ck_model_egress_audit_answer_digest",
        ),
        sa.CheckConstraint(
            "btrim(profile_ref) <> '' AND audit_profile_ref = 'model-generation-audit-v1'",
            name="ck_model_egress_audit_profiles",
        ),
        sa.CheckConstraint(
            "retain_until = recorded_at + interval '30 days'",
            name="ck_model_egress_audit_retention",
        ),
    )
    for role in ("PUBLIC", _EGRESS, _OPERATOR, _DEFINER):
        op.execute(f"REVOKE ALL ON TABLE {_TABLE} FROM {role}")
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY model_egress_audit_migrator_administration ON {_TABLE} FOR ALL TO {_MIGRATOR} USING (true) WITH CHECK (true)"
    )
    op.execute(
        f"CREATE POLICY model_egress_audit_definer_select ON {_TABLE} FOR SELECT TO {_DEFINER} USING (true)"
    )
    op.execute(
        f"CREATE POLICY model_egress_audit_definer_insert ON {_TABLE} FOR INSERT TO {_DEFINER} WITH CHECK (true)"
    )
    op.execute(
        f"CREATE POLICY model_egress_audit_definer_delete ON {_TABLE} FOR DELETE TO {_DEFINER} USING (true)"
    )
    op.execute(f"GRANT SELECT, INSERT, DELETE ON TABLE {_TABLE} TO {_DEFINER}")

    op.execute(
        f"""
        CREATE FUNCTION public.{_RECORD}(
            requested_organization_id uuid, requested_grant_digest bytea,
            requested_package_digest bytea, requested_payload_digest bytea,
            requested_question_digest bytea, requested_answer_payload_digest bytea,
            requested_outcome_category text, requested_provider_calls bigint,
            requested_cost_microunits bigint, requested_elapsed_ms bigint,
            requested_output_bytes bigint, requested_profile_ref text,
            requested_retention_seconds bigint, requested_audit_profile_ref text
        ) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE authority_now timestamptz := pg_catalog.clock_timestamp();
        BEGIN
            IF SESSION_USER <> '{_EGRESS}'
               OR requested_organization_id IS NULL
               OR requested_grant_digest IS NULL
               OR requested_package_digest IS NULL
               OR requested_payload_digest IS NULL
               OR requested_question_digest IS NULL
               OR octet_length(requested_grant_digest) <> 32
               OR octet_length(requested_package_digest) <> 32
               OR octet_length(requested_payload_digest) <> 32
               OR octet_length(requested_question_digest) <> 32
               OR (requested_answer_payload_digest IS NOT NULL AND octet_length(requested_answer_payload_digest) <> 32)
               OR requested_profile_ref IS NULL
               OR btrim(requested_profile_ref) = ''
               OR requested_outcome_category NOT IN ('generated', 'output_rejected', 'provider_unavailable')
               OR (requested_outcome_category = 'generated') IS DISTINCT FROM (requested_answer_payload_digest IS NOT NULL)
               OR requested_provider_calls <> 1
               OR requested_cost_microunits < 0
               OR requested_elapsed_ms < 0
               OR requested_output_bytes < 0
               OR requested_retention_seconds <> 2592000
               OR requested_audit_profile_ref <> 'model-generation-audit-v1'
               OR NOT EXISTS (
                    SELECT 1 FROM public.egress_grant AS grant_record
                    WHERE grant_record.organization_id = requested_organization_id
                      AND grant_record.grant_digest = requested_grant_digest
                      AND grant_record.hop_kind = 'model'
                      AND grant_record.package_digest = requested_package_digest
                      AND grant_record.payload_digest = requested_payload_digest
                      AND grant_record.profile_ref = requested_profile_ref
                      AND grant_record.consumed_at IS NOT NULL
               )
            THEN RETURN false; END IF;
            INSERT INTO public.{_TABLE} (
                organization_id, grant_digest, package_digest, payload_digest,
                question_digest, answer_payload_digest, outcome_category,
                provider_calls, cost_microunits, elapsed_ms, output_bytes,
                profile_ref, audit_profile_ref, recorded_at, retain_until
            ) VALUES (
                requested_organization_id, requested_grant_digest,
                requested_package_digest, requested_payload_digest,
                requested_question_digest, requested_answer_payload_digest,
                requested_outcome_category, requested_provider_calls,
                requested_cost_microunits, requested_elapsed_ms,
                requested_output_bytes, requested_profile_ref,
                requested_audit_profile_ref, authority_now,
                authority_now + interval '30 days'
            ) ON CONFLICT DO NOTHING;
            RETURN FOUND;
        END;
        $function$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION public.{_CLEANUP}(
            requested_organization_id uuid
        ) RETURNS bigint
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE deleted_count bigint;
        BEGIN
            IF SESSION_USER <> '{_OPERATOR}'
               OR requested_organization_id IS NULL
            THEN RETURN 0; END IF;
            DELETE FROM public.{_TABLE} AS audit
            WHERE audit.organization_id = requested_organization_id
              AND audit.retain_until <= pg_catalog.clock_timestamp();
            GET DIAGNOSTICS deleted_count = ROW_COUNT;
            RETURN deleted_count;
        END;
        $function$
        """
    )
    for function_name, signature in (
        (_RECORD, _RECORD_SIGNATURE),
        (_CLEANUP, _CLEANUP_SIGNATURE),
    ):
        op.execute(
            f"REVOKE ALL ON FUNCTION public.{function_name}{signature} FROM PUBLIC"
        )
        op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
        op.execute(
            f"ALTER FUNCTION public.{function_name}{signature} OWNER TO {_DEFINER}"
        )
        op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_RECORD}{_RECORD_SIGNATURE} TO {_EGRESS}"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_CLEANUP}{_CLEANUP_SIGNATURE} TO {_OPERATOR}"
    )
    op.execute("RESET ROLE")


def downgrade() -> None:
    """Refuse to erase retained model-generation audit."""

    op.execute(
        f"""
        DO $block$ BEGIN
          IF EXISTS (SELECT 1 FROM public.{_TABLE})
          THEN RAISE EXCEPTION USING ERRCODE = '55000',
                 MESSAGE = 'cannot downgrade with model egress audit rows';
          END IF;
        END; $block$
        """
    )
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(f"DROP FUNCTION public.{_CLEANUP}{_CLEANUP_SIGNATURE}")
    op.execute(f"DROP FUNCTION public.{_RECORD}{_RECORD_SIGNATURE}")
    op.execute("RESET ROLE")
    op.drop_table(_TABLE)
