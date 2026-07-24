"""Persist one restricted private BotDelivery receipt.

Revision ID: 20260724_0026
Revises: 20260724_0025
Create Date: 2026-07-24
"""

# ruff: noqa: E501

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260724_0026"
down_revision: str | None = "20260724_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "private_delivery_audit"
_MIGRATOR = "context_engine_migrator"
_ACTION = "context_engine_action"
_OPERATOR = "context_engine_security_operator"
_DEFINER = "context_engine_action_execute_definer"
_RECORD = "context_action_record_private_delivery_outcome"
_CLEANUP = "context_security_delete_expired_private_delivery_audit"
_RECORD_SIGNATURE = "(uuid, text, text, bytea, text, text, text, bigint, text)"
_CLEANUP_SIGNATURE = "(uuid)"


def upgrade() -> None:
    """Create a function-only digest receipt linked to exact applied effects."""

    op.create_table(
        _TABLE,
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("audit_ref", sa.Text(), nullable=False),
        sa.Column("delivery_attempt_ref", sa.Text(), nullable=False),
        sa.Column("package_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("placeholder_receipt_ref", sa.Text(), nullable=False),
        sa.Column("final_receipt_ref", sa.Text(), nullable=False),
        sa.Column("final_status", sa.Text(), nullable=False),
        sa.Column("audit_profile_ref", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retain_until", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id", "audit_ref", name="pk_private_delivery_audit"
        ),
        sa.UniqueConstraint(
            "audit_ref", name="uq_private_delivery_audit_ref_global"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "delivery_attempt_ref",
            name="uq_private_delivery_audit_attempt",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "delivery_attempt_ref"],
            [
                "action_delivery_attempt.organization_id",
                "action_delivery_attempt.delivery_attempt_ref",
            ],
            name="fk_private_delivery_audit_attempt",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "placeholder_receipt_ref"],
            ["action_receipt.organization_id", "action_receipt.receipt_ref"],
            name="fk_private_delivery_audit_placeholder_receipt",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "final_receipt_ref"],
            ["action_receipt.organization_id", "action_receipt.receipt_ref"],
            name="fk_private_delivery_audit_final_receipt",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "audit_ref ~ '^bda_[0-9a-f]{32}$' AND delivery_attempt_ref ~ '^dla_[0-9a-f]{32}$'",
            name="ck_private_delivery_audit_refs",
        ),
        sa.CheckConstraint(
            "octet_length(package_digest) = 32",
            name="ck_private_delivery_audit_package_digest",
        ),
        sa.CheckConstraint(
            "placeholder_receipt_ref <> final_receipt_ref AND final_status IN ('finalized', 'private_followup')",
            name="ck_private_delivery_audit_effects",
        ),
        sa.CheckConstraint(
            "audit_profile_ref = 'private-delivery-audit-v1' AND retain_until = recorded_at + interval '30 days'",
            name="ck_private_delivery_audit_retention",
        ),
    )
    for role in ("PUBLIC", _ACTION, _OPERATOR, _DEFINER):
        op.execute(f"REVOKE ALL ON TABLE public.{_TABLE} FROM {role}")
    op.execute(f"ALTER TABLE public.{_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE public.{_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY private_delivery_audit_migrator_administration ON public.{_TABLE} FOR ALL TO {_MIGRATOR} USING (true) WITH CHECK (true)"
    )
    op.execute(
        f"CREATE POLICY private_delivery_audit_action_execute_definer_select ON public.{_TABLE} FOR SELECT TO {_DEFINER} USING (true)"
    )
    op.execute(
        f"CREATE POLICY private_delivery_audit_action_execute_definer_insert ON public.{_TABLE} FOR INSERT TO {_DEFINER} WITH CHECK (true)"
    )
    op.execute(
        f"CREATE POLICY private_delivery_audit_action_execute_definer_delete ON public.{_TABLE} FOR DELETE TO {_DEFINER} USING (true)"
    )
    op.execute(f"GRANT SELECT, INSERT, DELETE ON TABLE public.{_TABLE} TO {_DEFINER}")

    op.execute(
        f"""
        CREATE FUNCTION public.{_RECORD}(
            requested_organization_id uuid, requested_audit_ref text,
            requested_delivery_attempt_ref text, requested_package_digest bytea,
            requested_placeholder_receipt_ref text, requested_final_receipt_ref text,
            requested_final_status text, requested_retention_seconds bigint,
            requested_audit_profile_ref text
        ) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE
            authority_now timestamptz := pg_catalog.clock_timestamp();
            existing_record public.{_TABLE}%ROWTYPE;
            inserted_count bigint;
        BEGIN
            IF SESSION_USER <> '{_ACTION}'
               OR requested_organization_id IS NULL
               OR requested_audit_ref !~ '^bda_[0-9a-f]{{32}}$'
               OR requested_delivery_attempt_ref !~ '^dla_[0-9a-f]{{32}}$'
               OR requested_package_digest IS NULL
               OR octet_length(requested_package_digest) <> 32
               OR requested_placeholder_receipt_ref IS NULL
               OR requested_final_receipt_ref IS NULL
               OR requested_placeholder_receipt_ref = requested_final_receipt_ref
               OR requested_final_status NOT IN ('finalized', 'private_followup')
               OR requested_retention_seconds <> 2592000
               OR requested_audit_profile_ref <> 'private-delivery-audit-v1'
               OR NOT EXISTS (
                    SELECT 1 FROM public.action_delivery_attempt AS attempt
                    WHERE attempt.organization_id = requested_organization_id
                      AND attempt.delivery_attempt_ref = requested_delivery_attempt_ref
               )
               OR NOT EXISTS (
                    SELECT 1 FROM public.action_receipt AS placeholder_receipt
                    WHERE placeholder_receipt.organization_id = requested_organization_id
                      AND placeholder_receipt.receipt_ref = requested_placeholder_receipt_ref
                      AND placeholder_receipt.delivery_attempt_ref = requested_delivery_attempt_ref
                      AND placeholder_receipt.operation = 'create_placeholder'
               )
               OR NOT EXISTS (
                    SELECT 1 FROM public.action_receipt AS final_receipt
                    WHERE final_receipt.organization_id = requested_organization_id
                      AND final_receipt.receipt_ref = requested_final_receipt_ref
                      AND final_receipt.delivery_attempt_ref = requested_delivery_attempt_ref
                      AND (
                        (requested_final_status = 'finalized' AND final_receipt.operation = 'finalize_reply')
                        OR (requested_final_status = 'private_followup' AND final_receipt.operation = 'send_private_followup')
                      )
               )
            THEN RETURN false; END IF;

            INSERT INTO public.{_TABLE} (
                organization_id, audit_ref, delivery_attempt_ref, package_digest,
                placeholder_receipt_ref, final_receipt_ref, final_status,
                audit_profile_ref, recorded_at, retain_until
            ) VALUES (
                requested_organization_id, requested_audit_ref,
                requested_delivery_attempt_ref, requested_package_digest,
                requested_placeholder_receipt_ref, requested_final_receipt_ref,
                requested_final_status, requested_audit_profile_ref,
                authority_now, authority_now + interval '30 days'
            ) ON CONFLICT DO NOTHING;
            GET DIAGNOSTICS inserted_count = ROW_COUNT;

            IF inserted_count = 1 THEN RETURN true; END IF;

            SELECT audit.* INTO existing_record
            FROM public.{_TABLE} AS audit
            WHERE audit.organization_id = requested_organization_id
              AND audit.delivery_attempt_ref = requested_delivery_attempt_ref;
            RETURN FOUND
               AND existing_record.audit_ref = requested_audit_ref
               AND existing_record.package_digest = requested_package_digest
               AND existing_record.placeholder_receipt_ref = requested_placeholder_receipt_ref
               AND existing_record.final_receipt_ref = requested_final_receipt_ref
               AND existing_record.final_status = requested_final_status
               AND existing_record.audit_profile_ref = requested_audit_profile_ref;
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
            IF SESSION_USER <> '{_OPERATOR}' OR requested_organization_id IS NULL
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
        f"GRANT EXECUTE ON FUNCTION public.{_RECORD}{_RECORD_SIGNATURE} TO {_ACTION}"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_CLEANUP}{_CLEANUP_SIGNATURE} TO {_OPERATOR}"
    )
    op.execute("RESET ROLE")


def downgrade() -> None:
    """Refuse to erase retained delivery receipts."""

    op.execute(
        f"""
        DO $block$ BEGIN
          IF EXISTS (SELECT 1 FROM public.{_TABLE})
          THEN RAISE EXCEPTION USING ERRCODE = '55000',
                 MESSAGE = 'cannot downgrade with retained private delivery audit rows';
          END IF;
        END; $block$
        """
    )
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(f"DROP FUNCTION public.{_CLEANUP}{_CLEANUP_SIGNATURE}")
    op.execute(f"DROP FUNCTION public.{_RECORD}{_RECORD_SIGNATURE}")
    op.execute("RESET ROLE")
    op.drop_table(_TABLE)
