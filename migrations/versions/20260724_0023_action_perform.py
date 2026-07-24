"""Persist one-shot private ActionPlane execution and reconciliation.

Revision ID: 20260724_0023
Revises: 20260723_0022
Create Date: 2026-07-24
"""

# ruff: noqa: E501

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260724_0023"
down_revision: str | None = "20260723_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MIGRATOR = "context_engine_migrator"
_ACTION = "context_engine_action"
_DEFINER = "context_engine_action_execute_definer"
_BEGIN = "context_action_begin_private_effect"
_COMPLETE = "context_action_complete_private_effect"
_RECONCILE = "context_action_reconcile_private_effect"
_RECEIPT_IMMUTABILITY = "context_action_reject_receipt_mutation"
_BEGIN_SIGNATURE = "(uuid, text, text, text, text, bytea, bytea, bytea, bigint, integer, text, bytea, bytea, bytea, bytea, bytea, bytea, timestamptz, timestamptz, bytea, text, bigint)"
_COMPLETE_SIGNATURE = "(uuid, text, text, text, bytea, timestamptz, text, text, bigint)"
_RECONCILE_SIGNATURE = "(uuid, text, text, bytea, timestamptz, text, bytea, text, bigint)"
_REFERENCE_TABLES = (
    "delivery_evidence",
    "membership",
    "organization_policy_epoch",
    "context_source",
    "source_version",
)


def _secure_execution_table(
    table_name: str,
    *,
    reads: bool,
    inserts: bool,
    updates: bool,
) -> None:
    for role in ("PUBLIC", _ACTION, _DEFINER):
        op.execute(f"REVOKE ALL ON TABLE public.{table_name} FROM {role}")
    op.execute(f"ALTER TABLE public.{table_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE public.{table_name} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table_name}_migrator_administration "
        f"ON public.{table_name} FOR ALL TO {_MIGRATOR} "
        "USING (true) WITH CHECK (true)"
    )
    if reads:
        op.execute(
            f"CREATE POLICY {table_name}_action_execute_definer_select "
            f"ON public.{table_name} FOR SELECT TO {_DEFINER} USING (true)"
        )
        op.execute(f"GRANT SELECT ON TABLE public.{table_name} TO {_DEFINER}")
    if inserts:
        op.execute(
            f"CREATE POLICY {table_name}_action_execute_definer_insert "
            f"ON public.{table_name} FOR INSERT TO {_DEFINER} WITH CHECK (true)"
        )
        op.execute(f"GRANT INSERT ON TABLE public.{table_name} TO {_DEFINER}")
    if updates:
        op.execute(
            f"CREATE POLICY {table_name}_action_execute_definer_update "
            f"ON public.{table_name} FOR UPDATE TO {_DEFINER} "
            "USING (true) WITH CHECK (true)"
        )
        op.execute(f"GRANT UPDATE ON TABLE public.{table_name} TO {_DEFINER}")


def _return_columns() -> str:
    return """
        outcome text,
        provider_attempt_ref text,
        destination_ref text,
        receipt_ref text,
        organization_id uuid,
        delivery_attempt_ref text,
        ticket_ref text,
        operation text,
        destination_digest bytea,
        audience_digest bytea,
        payload_digest bytea,
        idempotency_digest bytea,
        provider_effect_digest bytea,
        applied_at timestamptz
    """


def upgrade() -> None:
    """Add one-shot execution, receipts, and monotonic reconciliation."""

    op.add_column(
        "action_ticket",
        sa.Column("ticket_bearer_digest", postgresql.BYTEA(), nullable=True),
    )
    op.drop_constraint("ck_action_ticket_profiles", "action_ticket", type_="check")
    op.create_check_constraint(
        "ck_action_ticket_profiles",
        "action_ticket",
        "approval_tier = 'preapproved_private_delivery_v1' AND "
        "profile_ref = 'private-action-prepare-v1' AND "
        "state IN ('prepared', 'in_flight', 'ambiguous', 'applied', 'rejected') "
        "AND retention_policy_ref = 'action-digest-audit-retention-v1'",
    )
    op.create_check_constraint(
        "ck_action_ticket_bearer_digest",
        "action_ticket",
        "ticket_bearer_digest IS NULL OR octet_length(ticket_bearer_digest) = 32",
    )

    op.create_table(
        "action_provider_attempt",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_attempt_ref", sa.Text(), nullable=False),
        sa.Column("ticket_ref", sa.Text(), nullable=False),
        sa.Column("delivery_attempt_ref", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("destination_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("audience_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("payload_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("idempotency_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("provider_effect_digest", postgresql.BYTEA(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retention_policy_ref", sa.Text(), nullable=False),
        sa.Column("retain_until", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "provider_attempt_ref",
            name="pk_action_provider_attempt",
        ),
        sa.UniqueConstraint(
            "provider_attempt_ref",
            name="uq_action_provider_attempt_ref_global",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "ticket_ref",
            name="uq_action_provider_attempt_ticket",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "ticket_ref"],
            ["action_ticket.organization_id", "action_ticket.ticket_ref"],
            name="fk_action_provider_attempt_ticket",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "delivery_attempt_ref"],
            [
                "action_delivery_attempt.organization_id",
                "action_delivery_attempt.delivery_attempt_ref",
            ],
            name="fk_action_provider_attempt_delivery_attempt",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "provider_attempt_ref ~ '^pat_[0-9a-f]{32}$'",
            name="ck_action_provider_attempt_ref",
        ),
        sa.CheckConstraint(
            "octet_length(destination_digest) = 32 AND "
            "octet_length(audience_digest) = 32 AND "
            "octet_length(payload_digest) = 32 AND "
            "octet_length(idempotency_digest) = 32 AND "
            "(provider_effect_digest IS NULL OR "
            "octet_length(provider_effect_digest) = 32)",
            name="ck_action_provider_attempt_digests",
        ),
        sa.CheckConstraint(
            "state IN ('in_flight', 'ambiguous', 'applied', 'rejected') AND "
            "((state IN ('in_flight', 'ambiguous') AND terminal_at IS NULL AND "
            "provider_effect_digest IS NULL) OR "
            "(state = 'rejected' AND terminal_at IS NOT NULL AND "
            "provider_effect_digest IS NULL) OR "
            "(state = 'applied' AND terminal_at IS NOT NULL AND "
            "provider_effect_digest IS NOT NULL))",
            name="ck_action_provider_attempt_state",
        ),
        sa.CheckConstraint(
            "retention_policy_ref = 'action-digest-audit-retention-v1' AND "
            "retain_until > started_at",
            name="ck_action_provider_attempt_retention",
        ),
    )
    op.create_table(
        "action_receipt",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("receipt_ref", sa.Text(), nullable=False),
        sa.Column("provider_attempt_ref", sa.Text(), nullable=False),
        sa.Column("ticket_ref", sa.Text(), nullable=False),
        sa.Column("delivery_attempt_ref", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("destination_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("audience_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("payload_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("idempotency_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("provider_effect_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retention_policy_ref", sa.Text(), nullable=False),
        sa.Column("retain_until", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id", "receipt_ref", name="pk_action_receipt"
        ),
        sa.UniqueConstraint("receipt_ref", name="uq_action_receipt_ref_global"),
        sa.UniqueConstraint(
            "organization_id",
            "provider_attempt_ref",
            name="uq_action_receipt_provider_attempt",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "provider_attempt_ref"],
            [
                "action_provider_attempt.organization_id",
                "action_provider_attempt.provider_attempt_ref",
            ],
            name="fk_action_receipt_provider_attempt",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "receipt_ref ~ '^acr_[0-9a-f]{32}$'",
            name="ck_action_receipt_ref",
        ),
        sa.CheckConstraint(
            "octet_length(destination_digest) = 32 AND "
            "octet_length(audience_digest) = 32 AND "
            "octet_length(payload_digest) = 32 AND "
            "octet_length(idempotency_digest) = 32 AND "
            "octet_length(provider_effect_digest) = 32",
            name="ck_action_receipt_digests",
        ),
        sa.CheckConstraint(
            "applied_at <= recorded_at + interval '5 seconds' AND "
            "retention_policy_ref = 'action-digest-audit-retention-v1' AND "
            "retain_until > recorded_at",
            name="ck_action_receipt_retention",
        ),
    )
    op.create_table(
        "action_reconciliation",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_attempt_ref", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("decision_digest", postgresql.BYTEA(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retention_policy_ref", sa.Text(), nullable=False),
        sa.Column("retain_until", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "provider_attempt_ref",
            name="pk_action_reconciliation",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "provider_attempt_ref"],
            [
                "action_provider_attempt.organization_id",
                "action_provider_attempt.provider_attempt_ref",
            ],
            name="fk_action_reconciliation_provider_attempt",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'applied', 'rejected') AND "
            "((state = 'pending' AND decision_digest IS NULL AND "
            "reconciled_at IS NULL) OR "
            "(state IN ('applied', 'rejected') AND decision_digest IS NOT NULL "
            "AND reconciled_at IS NOT NULL))",
            name="ck_action_reconciliation_state",
        ),
        sa.CheckConstraint(
            "decision_digest IS NULL OR octet_length(decision_digest) = 32",
            name="ck_action_reconciliation_digest",
        ),
        sa.CheckConstraint(
            "retention_policy_ref = 'action-digest-audit-retention-v1' AND "
            "retain_until > created_at",
            name="ck_action_reconciliation_retention",
        ),
    )
    op.create_table(
        "action_perform_audit",
        sa.Column(
            "audit_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retention_policy_ref", sa.Text(), nullable=False),
        sa.Column("retain_until", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id", "audit_id", name="pk_action_perform_audit"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.organization_id"],
            name="fk_action_perform_audit_organization",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "octet_length(decision_digest) = 32",
            name="ck_action_perform_audit_decision_digest",
        ),
        sa.CheckConstraint(
            "category IN ('sender_required', 'applied', 'already_applied', "
            "'rejected', 'reconciliation_required', 'reconciled_applied', "
            "'reconciled_rejected')",
            name="ck_action_perform_audit_category",
        ),
        sa.CheckConstraint(
            "retention_policy_ref = 'action-digest-audit-retention-v1' AND "
            "retain_until > recorded_at",
            name="ck_action_perform_audit_retention",
        ),
    )

    _secure_execution_table(
        "action_provider_attempt", reads=True, inserts=True, updates=True
    )
    _secure_execution_table("action_receipt", reads=True, inserts=True, updates=False)
    _secure_execution_table(
        "action_reconciliation", reads=True, inserts=True, updates=True
    )
    _secure_execution_table(
        "action_perform_audit", reads=False, inserts=True, updates=False
    )
    op.execute(
        f"""
        CREATE FUNCTION public.{_RECEIPT_IMMUTABILITY}()
        RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog
        AS $function$
        BEGIN
            RAISE EXCEPTION USING ERRCODE = '55000',
                MESSAGE = 'ActionReceipt is immutable';
        END;
        $function$
        """
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION public.{_RECEIPT_IMMUTABILITY}() FROM PUBLIC"
    )
    op.execute(
        "CREATE TRIGGER action_receipt_reject_mutation "
        "BEFORE UPDATE OR DELETE ON public.action_receipt FOR EACH ROW "
        f"EXECUTE FUNCTION public.{_RECEIPT_IMMUTABILITY}()"
    )

    op.execute(
        f"CREATE POLICY action_delivery_attempt_action_execute_definer_select "
        f"ON public.action_delivery_attempt FOR SELECT TO {_DEFINER} USING (true)"
    )
    op.execute(f"GRANT SELECT ON TABLE public.action_delivery_attempt TO {_DEFINER}")
    op.execute(
        f"CREATE POLICY action_ticket_action_execute_definer_select "
        f"ON public.action_ticket FOR SELECT TO {_DEFINER} USING (true)"
    )
    op.execute(
        f"CREATE POLICY action_ticket_action_execute_definer_update "
        f"ON public.action_ticket FOR UPDATE TO {_DEFINER} "
        "USING (true) WITH CHECK (true)"
    )
    op.execute(f"GRANT SELECT, UPDATE ON TABLE public.action_ticket TO {_DEFINER}")
    for table_name in _REFERENCE_TABLES:
        op.execute(f"GRANT SELECT ON TABLE public.{table_name} TO {_DEFINER}")
        op.execute(
            f"CREATE POLICY {table_name}_action_execute_definer_select "
            f"ON public.{table_name} FOR SELECT TO {_DEFINER} USING (true)"
        )

    op.execute(
        f"""
        CREATE FUNCTION public.{_BEGIN}(
            requested_organization_id uuid,
            requested_ticket_ref text,
            requested_delivery_attempt_ref text,
            requested_operation text,
            requested_ticket_audience text,
            requested_payload_digest bytea,
            requested_idempotency_digest bytea,
            requested_approval_digest bytea,
            requested_policy_epoch bigint,
            requested_signing_key_version integer,
            requested_profile_ref text,
            requested_service_digest bytea,
            requested_destination_digest bytea,
            requested_audience_digest bytea,
            requested_identity_digest bytea,
            requested_purpose_digest bytea,
            requested_source_context_digest bytea,
            requested_issued_at timestamptz,
            requested_expires_at timestamptz,
            requested_ticket_bearer_digest bytea,
            proposed_provider_attempt_ref text,
            requested_retention_seconds bigint
        ) RETURNS TABLE ({_return_columns()})
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE
            authority_now timestamptz := pg_catalog.clock_timestamp();
            authority_retain_until timestamptz;
            attempt_record public.action_delivery_attempt%ROWTYPE;
            ticket_record public.action_ticket%ROWTYPE;
            provider_record public.action_provider_attempt%ROWTYPE;
            receipt_record public.action_receipt%ROWTYPE;
            evidence_record public.delivery_evidence%ROWTYPE;
            current_policy_epoch bigint;
            decision_digest bytea;
            expected_source_context_digest bytea;
        BEGIN
            IF SESSION_USER <> '{_ACTION}'
               OR requested_organization_id IS NULL
               OR requested_ticket_ref IS NULL
               OR requested_ticket_ref !~ '^act_[0-9a-f]{{32}}$'
               OR requested_delivery_attempt_ref IS NULL
               OR requested_delivery_attempt_ref !~ '^dla_[0-9a-f]{{32}}$'
               OR proposed_provider_attempt_ref IS NULL
               OR proposed_provider_attempt_ref !~ '^pat_[0-9a-f]{{32}}$'
               OR requested_operation IS NULL
               OR requested_operation NOT IN (
                    'create_placeholder', 'finalize_reply',
                    'send_private_followup'
               )
               OR requested_ticket_audience IS NULL
               OR requested_profile_ref IS DISTINCT FROM
                    'private-action-prepare-v1'
               OR requested_policy_epoch IS NULL
               OR requested_policy_epoch <= 0
               OR requested_signing_key_version IS NULL
               OR requested_signing_key_version <= 0
               OR requested_retention_seconds IS NULL
               OR requested_retention_seconds NOT BETWEEN 2 AND 31536000
               OR requested_payload_digest IS NULL
               OR octet_length(requested_payload_digest) <> 32
               OR requested_idempotency_digest IS NULL
               OR octet_length(requested_idempotency_digest) <> 32
               OR requested_approval_digest IS NULL
               OR octet_length(requested_approval_digest) <> 32
               OR requested_service_digest IS NULL
               OR octet_length(requested_service_digest) <> 32
               OR requested_destination_digest IS NULL
               OR octet_length(requested_destination_digest) <> 32
               OR requested_audience_digest IS NULL
               OR octet_length(requested_audience_digest) <> 32
               OR requested_identity_digest IS NULL
               OR octet_length(requested_identity_digest) <> 32
               OR requested_purpose_digest IS NULL
               OR octet_length(requested_purpose_digest) <> 32
               OR (requested_source_context_digest IS NOT NULL AND
                   octet_length(requested_source_context_digest) <> 32)
               OR requested_issued_at IS NULL
               OR requested_expires_at IS NULL
               OR requested_ticket_bearer_digest IS NULL
               OR octet_length(requested_ticket_bearer_digest) <> 32
            THEN
                RETURN QUERY SELECT 'rejected'::text, NULL::text, NULL::text,
                    NULL::text, NULL::uuid, NULL::text, NULL::text, NULL::text,
                    NULL::bytea, NULL::bytea, NULL::bytea, NULL::bytea,
                    NULL::bytea, NULL::timestamptz;
                RETURN;
            END IF;

            PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
                'action-perform:' || requested_organization_id::text || ':' ||
                requested_ticket_ref, 0
            ));
            SELECT ticket.* INTO ticket_record
            FROM public.action_ticket AS ticket
            WHERE ticket.organization_id = requested_organization_id
              AND ticket.ticket_ref = requested_ticket_ref;
            IF NOT FOUND THEN
                RETURN QUERY SELECT 'rejected'::text, NULL::text, NULL::text,
                    NULL::text, NULL::uuid, NULL::text, NULL::text, NULL::text,
                    NULL::bytea, NULL::bytea, NULL::bytea, NULL::bytea,
                    NULL::bytea, NULL::timestamptz;
                RETURN;
            END IF;
            SELECT attempt.* INTO STRICT attempt_record
            FROM public.action_delivery_attempt AS attempt
            WHERE attempt.organization_id = ticket_record.organization_id
              AND attempt.delivery_attempt_ref = ticket_record.delivery_attempt_ref;

            decision_digest := public.digest(pg_catalog.convert_to(
                requested_organization_id::text || ':' || requested_ticket_ref || ':' ||
                pg_catalog.encode(requested_ticket_bearer_digest, 'hex'), 'UTF8'
            ), 'sha256');
            authority_retain_until := authority_now + pg_catalog.make_interval(
                secs => requested_retention_seconds
            );
            IF ticket_record.source_id IS NOT NULL THEN
                expected_source_context_digest := public.digest(
                    pg_catalog.convert_to(
                        'context-engine.action-binding.v1', 'UTF8'
                    ) || pg_catalog.decode('00', 'hex') ||
                    pg_catalog.convert_to('source', 'UTF8') ||
                    pg_catalog.decode('00', 'hex') ||
                    pg_catalog.convert_to(pg_catalog.octet_length(
                        pg_catalog.convert_to(ticket_record.source_id::text, 'UTF8')
                    )::text, 'UTF8') || pg_catalog.decode('00', 'hex') ||
                    pg_catalog.convert_to(ticket_record.source_id::text, 'UTF8') ||
                    pg_catalog.convert_to(pg_catalog.octet_length(
                        pg_catalog.convert_to(
                            ticket_record.source_version_id::text, 'UTF8'
                        )
                    )::text, 'UTF8') || pg_catalog.decode('00', 'hex') ||
                    pg_catalog.convert_to(
                        ticket_record.source_version_id::text, 'UTF8'
                    ),
                    'sha256'
                );
            END IF;
            IF ticket_record.delivery_attempt_ref IS DISTINCT FROM
                    requested_delivery_attempt_ref
               OR ticket_record.operation IS DISTINCT FROM requested_operation
               OR ticket_record.ticket_audience IS DISTINCT FROM
                    requested_ticket_audience
               OR ticket_record.payload_digest IS DISTINCT FROM
                    requested_payload_digest
               OR ticket_record.idempotency_digest IS DISTINCT FROM
                    requested_idempotency_digest
               OR ticket_record.approval_digest IS DISTINCT FROM
                    requested_approval_digest
               OR ticket_record.policy_epoch IS DISTINCT FROM
                    requested_policy_epoch
               OR ticket_record.signing_key_version IS DISTINCT FROM
                    requested_signing_key_version
               OR ticket_record.profile_ref IS DISTINCT FROM requested_profile_ref
               OR attempt_record.authenticated_service_digest IS DISTINCT FROM
                    requested_service_digest
               OR attempt_record.destination_digest IS DISTINCT FROM
                    requested_destination_digest
               OR attempt_record.audience_digest IS DISTINCT FROM
                    requested_audience_digest
               OR attempt_record.identity_digest IS DISTINCT FROM
                    requested_identity_digest
               OR attempt_record.purpose_digest IS DISTINCT FROM
                    requested_purpose_digest
               OR expected_source_context_digest IS DISTINCT FROM
                    requested_source_context_digest
               OR pg_catalog.date_trunc(
                    'milliseconds', ticket_record.issued_at
               ) IS DISTINCT FROM requested_issued_at
               OR pg_catalog.date_trunc(
                    'milliseconds', ticket_record.expires_at
               ) IS DISTINCT FROM requested_expires_at
               OR (
                    ticket_record.ticket_bearer_digest IS NOT NULL AND
                    ticket_record.ticket_bearer_digest IS DISTINCT FROM
                        requested_ticket_bearer_digest
               )
            THEN
                INSERT INTO public.action_perform_audit (
                    organization_id, decision_digest, category, recorded_at,
                    retention_policy_ref, retain_until
                ) VALUES (
                    requested_organization_id, decision_digest, 'rejected',
                    authority_now, 'action-digest-audit-retention-v1',
                    authority_retain_until
                );
                RETURN QUERY SELECT 'rejected'::text, NULL::text, NULL::text,
                    NULL::text, NULL::uuid, NULL::text, NULL::text, NULL::text,
                    NULL::bytea, NULL::bytea, NULL::bytea, NULL::bytea,
                    NULL::bytea, NULL::timestamptz;
                RETURN;
            END IF;

            SELECT provider.* INTO provider_record
            FROM public.action_provider_attempt AS provider
            WHERE provider.organization_id = requested_organization_id
              AND provider.ticket_ref = requested_ticket_ref;
            IF ticket_record.state = 'applied' AND FOUND THEN
                SELECT receipt.* INTO STRICT receipt_record
                FROM public.action_receipt AS receipt
                WHERE receipt.organization_id = provider_record.organization_id
                  AND receipt.provider_attempt_ref = provider_record.provider_attempt_ref;
                INSERT INTO public.action_perform_audit (
                    organization_id, decision_digest, category, recorded_at,
                    retention_policy_ref, retain_until
                ) VALUES (
                    requested_organization_id, decision_digest, 'already_applied',
                    authority_now, 'action-digest-audit-retention-v1',
                    authority_retain_until
                );
                RETURN QUERY SELECT 'already_applied'::text,
                    receipt_record.provider_attempt_ref, NULL::text,
                    receipt_record.receipt_ref, receipt_record.organization_id,
                    receipt_record.delivery_attempt_ref, receipt_record.ticket_ref,
                    receipt_record.operation, receipt_record.destination_digest,
                    receipt_record.audience_digest, receipt_record.payload_digest,
                    receipt_record.idempotency_digest,
                    receipt_record.provider_effect_digest, receipt_record.applied_at;
                RETURN;
            ELSIF ticket_record.state IN ('in_flight', 'ambiguous') AND FOUND THEN
                INSERT INTO public.action_perform_audit (
                    organization_id, decision_digest, category, recorded_at,
                    retention_policy_ref, retain_until
                ) VALUES (
                    requested_organization_id, decision_digest,
                    'reconciliation_required', authority_now,
                    'action-digest-audit-retention-v1', authority_retain_until
                );
                RETURN QUERY SELECT 'reconciliation_required'::text,
                    provider_record.provider_attempt_ref, NULL::text, NULL::text,
                    NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::bytea,
                    NULL::bytea, NULL::bytea, NULL::bytea, NULL::bytea,
                    NULL::timestamptz;
                RETURN;
            ELSIF ticket_record.state = 'rejected' THEN
                INSERT INTO public.action_perform_audit (
                    organization_id, decision_digest, category, recorded_at,
                    retention_policy_ref, retain_until
                ) VALUES (
                    requested_organization_id, decision_digest, 'rejected',
                    authority_now, 'action-digest-audit-retention-v1',
                    authority_retain_until
                );
                RETURN QUERY SELECT 'provider_rejected'::text,
                    provider_record.provider_attempt_ref, NULL::text, NULL::text,
                    NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::bytea,
                    NULL::bytea, NULL::bytea, NULL::bytea, NULL::bytea,
                    NULL::timestamptz;
                RETURN;
            ELSIF ticket_record.state <> 'prepared' OR FOUND THEN
                INSERT INTO public.action_perform_audit (
                    organization_id, decision_digest, category, recorded_at,
                    retention_policy_ref, retain_until
                ) VALUES (
                    requested_organization_id, decision_digest, 'rejected',
                    authority_now, 'action-digest-audit-retention-v1',
                    authority_retain_until
                );
                RETURN QUERY SELECT 'rejected'::text, NULL::text, NULL::text,
                    NULL::text, NULL::uuid, NULL::text, NULL::text, NULL::text,
                    NULL::bytea, NULL::bytea, NULL::bytea, NULL::bytea,
                    NULL::bytea, NULL::timestamptz;
                RETURN;
            END IF;

            IF ticket_record.expires_at <= authority_now THEN
                INSERT INTO public.action_perform_audit (
                    organization_id, decision_digest, category, recorded_at,
                    retention_policy_ref, retain_until
                ) VALUES (
                    requested_organization_id, decision_digest, 'rejected',
                    authority_now, 'action-digest-audit-retention-v1',
                    authority_retain_until
                );
                RETURN QUERY SELECT 'rejected'::text, NULL::text, NULL::text,
                    NULL::text, NULL::uuid, NULL::text, NULL::text, NULL::text,
                    NULL::bytea, NULL::bytea, NULL::bytea, NULL::bytea,
                    NULL::bytea, NULL::timestamptz;
                RETURN;
            END IF;
            SELECT epoch.policy_epoch INTO current_policy_epoch
            FROM public.organization_policy_epoch AS epoch
            WHERE epoch.organization_id = requested_organization_id;
            IF current_policy_epoch IS DISTINCT FROM requested_policy_epoch
               OR NOT EXISTS (
                    SELECT 1 FROM public.membership AS membership
                    WHERE membership.organization_id = attempt_record.organization_id
                      AND membership.membership_id = attempt_record.membership_id
                      AND membership.user_id = attempt_record.user_id
                      AND membership.membership_version = attempt_record.membership_version
                      AND membership.status = 'active'
                      AND membership.valid_from <= authority_now
                      AND (membership.valid_until IS NULL OR
                           membership.valid_until > authority_now)
               )
               OR (
                    ticket_record.source_id IS NOT NULL AND NOT EXISTS (
                        SELECT 1 FROM public.context_source AS source
                        JOIN public.source_version AS version
                          ON version.organization_id = source.organization_id
                         AND version.source_id = source.source_id
                         AND version.version_id = ticket_record.source_version_id
                        WHERE source.organization_id = requested_organization_id
                          AND source.source_id = ticket_record.source_id
                          AND source.active_version_id = ticket_record.source_version_id
                          AND source.lifecycle_state = 'active'
                    )
               )
            THEN
                INSERT INTO public.action_perform_audit (
                    organization_id, decision_digest, category, recorded_at,
                    retention_policy_ref, retain_until
                ) VALUES (
                    requested_organization_id, decision_digest, 'rejected',
                    authority_now, 'action-digest-audit-retention-v1',
                    authority_retain_until
                );
                RETURN QUERY SELECT 'rejected'::text, NULL::text, NULL::text,
                    NULL::text, NULL::uuid, NULL::text, NULL::text, NULL::text,
                    NULL::bytea, NULL::bytea, NULL::bytea, NULL::bytea,
                    NULL::bytea, NULL::timestamptz;
                RETURN;
            END IF;
            SELECT evidence.* INTO evidence_record
            FROM public.delivery_evidence AS evidence
            WHERE evidence.organization_id = requested_organization_id
              AND evidence.evidence_digest = attempt_record.delivery_evidence_digest;
            IF NOT FOUND
               OR evidence_record.expires_at <= authority_now
               OR public.digest(pg_catalog.convert_to(
                    evidence_record.destination_ref, 'UTF8'), 'sha256') <>
                    requested_destination_digest
               OR pg_catalog.decode(evidence_record.audience_digest, 'hex') <>
                    requested_audience_digest
            THEN
                INSERT INTO public.action_perform_audit (
                    organization_id, decision_digest, category, recorded_at,
                    retention_policy_ref, retain_until
                ) VALUES (
                    requested_organization_id, decision_digest, 'rejected',
                    authority_now, 'action-digest-audit-retention-v1',
                    authority_retain_until
                );
                RETURN QUERY SELECT 'rejected'::text, NULL::text, NULL::text,
                    NULL::text, NULL::uuid, NULL::text, NULL::text, NULL::text,
                    NULL::bytea, NULL::bytea, NULL::bytea, NULL::bytea,
                    NULL::bytea, NULL::timestamptz;
                RETURN;
            END IF;

            IF NOT pg_catalog.pg_try_advisory_lock(pg_catalog.hashtextextended(
                'action-ticket-sender-session:' ||
                requested_organization_id::text || ':' ||
                requested_ticket_ref, 0
            )) THEN
                INSERT INTO public.action_perform_audit (
                    organization_id, decision_digest, category, recorded_at,
                    retention_policy_ref, retain_until
                ) VALUES (
                    requested_organization_id, decision_digest, 'rejected',
                    authority_now, 'action-digest-audit-retention-v1',
                    authority_retain_until
                );
                RETURN QUERY SELECT 'rejected'::text, NULL::text, NULL::text,
                    NULL::text, NULL::uuid, NULL::text, NULL::text, NULL::text,
                    NULL::bytea, NULL::bytea, NULL::bytea, NULL::bytea,
                    NULL::bytea, NULL::timestamptz;
                RETURN;
            END IF;

            BEGIN
                INSERT INTO public.action_provider_attempt (
                    organization_id, provider_attempt_ref, ticket_ref,
                    delivery_attempt_ref, operation, destination_digest,
                    audience_digest, payload_digest, idempotency_digest, state,
                    started_at, retention_policy_ref, retain_until
                ) VALUES (
                    requested_organization_id, proposed_provider_attempt_ref,
                    requested_ticket_ref, requested_delivery_attempt_ref,
                    requested_operation, requested_destination_digest,
                    requested_audience_digest, requested_payload_digest,
                    requested_idempotency_digest, 'in_flight', authority_now,
                    'action-digest-audit-retention-v1', authority_retain_until
                );
                UPDATE public.action_ticket AS target_ticket SET
                    state = 'in_flight',
                    ticket_bearer_digest = requested_ticket_bearer_digest
                WHERE target_ticket.organization_id = requested_organization_id
                  AND target_ticket.ticket_ref = requested_ticket_ref;
                INSERT INTO public.action_perform_audit (
                    organization_id, decision_digest, category, recorded_at,
                    retention_policy_ref, retain_until
                ) VALUES (
                    requested_organization_id, decision_digest, 'sender_required',
                    authority_now, 'action-digest-audit-retention-v1',
                    authority_retain_until
                );
            EXCEPTION
                WHEN unique_violation OR serialization_failure OR deadlock_detected THEN
                    IF NOT pg_catalog.pg_advisory_unlock(
                        pg_catalog.hashtextextended(
                            'action-ticket-sender-session:' ||
                            requested_organization_id::text || ':' ||
                            requested_ticket_ref, 0
                        )
                    ) THEN
                        RAISE EXCEPTION 'could not release action Sender session lock'
                            USING ERRCODE = 'internal_error';
                    END IF;
                    INSERT INTO public.action_perform_audit (
                        organization_id, decision_digest, category, recorded_at,
                        retention_policy_ref, retain_until
                    ) VALUES (
                        requested_organization_id, decision_digest, 'rejected',
                        authority_now, 'action-digest-audit-retention-v1',
                        authority_retain_until
                    );
                    RETURN QUERY SELECT 'rejected'::text, NULL::text, NULL::text,
                        NULL::text, NULL::uuid, NULL::text, NULL::text, NULL::text,
                        NULL::bytea, NULL::bytea, NULL::bytea, NULL::bytea,
                        NULL::bytea, NULL::timestamptz;
                    RETURN;
                WHEN OTHERS THEN
                    PERFORM pg_catalog.pg_advisory_unlock(
                        pg_catalog.hashtextextended(
                            'action-ticket-sender-session:' ||
                            requested_organization_id::text || ':' ||
                            requested_ticket_ref, 0
                        )
                    );
                    RAISE;
            END;
            RETURN QUERY SELECT 'sender_required'::text,
                proposed_provider_attempt_ref, evidence_record.destination_ref,
                NULL::text, NULL::uuid, NULL::text, NULL::text, NULL::text,
                NULL::bytea, NULL::bytea, NULL::bytea, NULL::bytea,
                NULL::bytea, NULL::timestamptz;
        END;
        $function$
        """
    )

    op.execute(
        f"""
        CREATE FUNCTION public.{_COMPLETE}(
            requested_organization_id uuid,
            requested_ticket_ref text,
            requested_provider_attempt_ref text,
            requested_sender_outcome text,
            requested_provider_effect_digest bytea,
            requested_applied_at timestamptz,
            proposed_receipt_ref text,
            requested_retention_policy_ref text,
            requested_retention_seconds bigint
        ) RETURNS TABLE ({_return_columns()})
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE
            authority_now timestamptz := pg_catalog.clock_timestamp();
            authority_retain_until timestamptz;
            provider_record public.action_provider_attempt%ROWTYPE;
            receipt_record public.action_receipt%ROWTYPE;
            decision_digest bytea;
        BEGIN
            IF SESSION_USER <> '{_ACTION}'
               OR requested_organization_id IS NULL
               OR requested_provider_attempt_ref IS NULL
               OR requested_provider_attempt_ref !~ '^pat_[0-9a-f]{{32}}$'
               OR requested_ticket_ref IS NULL
               OR requested_ticket_ref !~ '^act_[0-9a-f]{{32}}$'
               OR requested_sender_outcome IS NULL
               OR requested_sender_outcome NOT IN ('applied', 'ambiguous', 'rejected')
               OR requested_retention_policy_ref IS DISTINCT FROM
                    'action-digest-audit-retention-v1'
               OR requested_retention_seconds IS NULL
               OR requested_retention_seconds NOT BETWEEN 2 AND 31536000
               OR (requested_sender_outcome = 'applied' AND (
                    proposed_receipt_ref IS NULL
                    OR proposed_receipt_ref !~ '^acr_[0-9a-f]{{32}}$'
                    OR requested_provider_effect_digest IS NULL
                    OR octet_length(requested_provider_effect_digest) <> 32
                    OR requested_applied_at IS NULL
                    OR requested_applied_at > authority_now +
                        pg_catalog.make_interval(secs => 5)
               ))
               OR (requested_sender_outcome <> 'applied' AND (
                    requested_provider_effect_digest IS NOT NULL
                    OR requested_applied_at IS NOT NULL
               ))
            THEN
                RETURN QUERY SELECT 'reconciliation_required'::text,
                    requested_provider_attempt_ref, NULL::text, NULL::text,
                    NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::bytea,
                    NULL::bytea, NULL::bytea, NULL::bytea, NULL::bytea,
                    NULL::timestamptz;
                RETURN;
            END IF;
            PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
                'action-provider-attempt:' || requested_organization_id::text || ':' ||
                requested_provider_attempt_ref, 0
            ));
            SELECT provider.* INTO provider_record
            FROM public.action_provider_attempt AS provider
            WHERE provider.organization_id = requested_organization_id
              AND provider.provider_attempt_ref = requested_provider_attempt_ref
              AND provider.ticket_ref = requested_ticket_ref;
            IF NOT FOUND THEN
                RETURN QUERY SELECT 'reconciliation_required'::text,
                    requested_provider_attempt_ref, NULL::text, NULL::text,
                    NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::bytea,
                    NULL::bytea, NULL::bytea, NULL::bytea, NULL::bytea,
                    NULL::timestamptz;
                RETURN;
            END IF;
            authority_retain_until := authority_now + pg_catalog.make_interval(
                secs => requested_retention_seconds
            );
            decision_digest := public.digest(pg_catalog.convert_to(
                requested_organization_id::text || ':' ||
                requested_provider_attempt_ref || ':' || requested_sender_outcome,
                'UTF8'
            ), 'sha256');
            IF provider_record.state = 'applied' THEN
                SELECT receipt.* INTO STRICT receipt_record
                FROM public.action_receipt AS receipt
                WHERE receipt.organization_id = provider_record.organization_id
                  AND receipt.provider_attempt_ref = provider_record.provider_attempt_ref;
                INSERT INTO public.action_perform_audit (
                    organization_id, decision_digest, category, recorded_at,
                    retention_policy_ref, retain_until
                ) VALUES (
                    requested_organization_id, decision_digest, 'already_applied',
                    authority_now, requested_retention_policy_ref,
                    authority_retain_until
                );
                RETURN QUERY SELECT 'applied'::text,
                    receipt_record.provider_attempt_ref, NULL::text,
                    receipt_record.receipt_ref, receipt_record.organization_id,
                    receipt_record.delivery_attempt_ref, receipt_record.ticket_ref,
                    receipt_record.operation, receipt_record.destination_digest,
                    receipt_record.audience_digest, receipt_record.payload_digest,
                    receipt_record.idempotency_digest,
                    receipt_record.provider_effect_digest, receipt_record.applied_at;
                RETURN;
            ELSIF provider_record.state = 'ambiguous' THEN
                INSERT INTO public.action_perform_audit (
                    organization_id, decision_digest, category, recorded_at,
                    retention_policy_ref, retain_until
                ) VALUES (
                    requested_organization_id, decision_digest,
                    'reconciliation_required', authority_now,
                    requested_retention_policy_ref, authority_retain_until
                );
                RETURN QUERY SELECT 'reconciliation_required'::text,
                    provider_record.provider_attempt_ref, NULL::text, NULL::text,
                    NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::bytea,
                    NULL::bytea, NULL::bytea, NULL::bytea, NULL::bytea,
                    NULL::timestamptz;
                RETURN;
            ELSIF provider_record.state = 'rejected' THEN
                INSERT INTO public.action_perform_audit (
                    organization_id, decision_digest, category, recorded_at,
                    retention_policy_ref, retain_until
                ) VALUES (
                    requested_organization_id, decision_digest, 'rejected',
                    authority_now, requested_retention_policy_ref,
                    authority_retain_until
                );
                RETURN QUERY SELECT 'rejected'::text,
                    provider_record.provider_attempt_ref, NULL::text, NULL::text,
                    NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::bytea,
                    NULL::bytea, NULL::bytea, NULL::bytea, NULL::bytea,
                    NULL::timestamptz;
                RETURN;
            END IF;

            IF requested_sender_outcome = 'applied' THEN
                UPDATE public.action_provider_attempt AS target_provider SET
                    state = 'applied',
                    provider_effect_digest = requested_provider_effect_digest,
                    terminal_at = authority_now
                WHERE target_provider.organization_id = requested_organization_id
                  AND target_provider.provider_attempt_ref = requested_provider_attempt_ref;
                UPDATE public.action_ticket AS target_ticket SET state = 'applied'
                WHERE target_ticket.organization_id = requested_organization_id
                  AND target_ticket.ticket_ref = requested_ticket_ref;
                INSERT INTO public.action_receipt (
                    organization_id, receipt_ref, provider_attempt_ref, ticket_ref,
                    delivery_attempt_ref, operation, destination_digest,
                    audience_digest, payload_digest, idempotency_digest,
                    provider_effect_digest, applied_at, recorded_at,
                    retention_policy_ref, retain_until
                ) VALUES (
                    requested_organization_id, proposed_receipt_ref,
                    requested_provider_attempt_ref, requested_ticket_ref,
                    provider_record.delivery_attempt_ref, provider_record.operation,
                    provider_record.destination_digest, provider_record.audience_digest,
                    provider_record.payload_digest, provider_record.idempotency_digest,
                    requested_provider_effect_digest, requested_applied_at,
                    authority_now, requested_retention_policy_ref,
                    authority_retain_until
                ) RETURNING * INTO receipt_record;
                INSERT INTO public.action_perform_audit (
                    organization_id, decision_digest, category, recorded_at,
                    retention_policy_ref, retain_until
                ) VALUES (
                    requested_organization_id, decision_digest, 'applied',
                    authority_now, requested_retention_policy_ref,
                    authority_retain_until
                );
                RETURN QUERY SELECT 'applied'::text,
                    receipt_record.provider_attempt_ref, NULL::text,
                    receipt_record.receipt_ref, receipt_record.organization_id,
                    receipt_record.delivery_attempt_ref, receipt_record.ticket_ref,
                    receipt_record.operation, receipt_record.destination_digest,
                    receipt_record.audience_digest, receipt_record.payload_digest,
                    receipt_record.idempotency_digest,
                    receipt_record.provider_effect_digest, receipt_record.applied_at;
                RETURN;
            ELSIF requested_sender_outcome = 'rejected' THEN
                UPDATE public.action_provider_attempt AS target_provider SET
                    state = 'rejected', terminal_at = authority_now
                WHERE target_provider.organization_id = requested_organization_id
                  AND target_provider.provider_attempt_ref = requested_provider_attempt_ref;
                UPDATE public.action_ticket AS target_ticket SET state = 'rejected'
                WHERE target_ticket.organization_id = requested_organization_id
                  AND target_ticket.ticket_ref = requested_ticket_ref;
                INSERT INTO public.action_perform_audit (
                    organization_id, decision_digest, category, recorded_at,
                    retention_policy_ref, retain_until
                ) VALUES (
                    requested_organization_id, decision_digest, 'rejected',
                    authority_now, requested_retention_policy_ref,
                    authority_retain_until
                );
                RETURN QUERY SELECT 'rejected'::text,
                    requested_provider_attempt_ref, NULL::text, NULL::text,
                    NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::bytea,
                    NULL::bytea, NULL::bytea, NULL::bytea, NULL::bytea,
                    NULL::timestamptz;
                RETURN;
            ELSE
                UPDATE public.action_provider_attempt AS target_provider
                SET state = 'ambiguous'
                WHERE target_provider.organization_id = requested_organization_id
                  AND target_provider.provider_attempt_ref = requested_provider_attempt_ref;
                UPDATE public.action_ticket AS target_ticket SET state = 'ambiguous'
                WHERE target_ticket.organization_id = requested_organization_id
                  AND target_ticket.ticket_ref = requested_ticket_ref;
                INSERT INTO public.action_reconciliation (
                    organization_id, provider_attempt_ref, state, created_at,
                    retention_policy_ref, retain_until
                ) VALUES (
                    requested_organization_id, requested_provider_attempt_ref,
                    'pending', authority_now, requested_retention_policy_ref,
                    authority_retain_until
                );
                INSERT INTO public.action_perform_audit (
                    organization_id, decision_digest, category, recorded_at,
                    retention_policy_ref, retain_until
                ) VALUES (
                    requested_organization_id, decision_digest,
                    'reconciliation_required', authority_now,
                    requested_retention_policy_ref, authority_retain_until
                );
                RETURN QUERY SELECT 'reconciliation_required'::text,
                    requested_provider_attempt_ref, NULL::text, NULL::text,
                    NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::bytea,
                    NULL::bytea, NULL::bytea, NULL::bytea, NULL::bytea,
                    NULL::timestamptz;
                RETURN;
            END IF;
        END;
        $function$
        """
    )

    op.execute(
        f"""
        CREATE FUNCTION public.{_RECONCILE}(
            requested_organization_id uuid,
            requested_provider_attempt_ref text,
            requested_disposition text,
            requested_provider_effect_digest bytea,
            requested_applied_at timestamptz,
            proposed_receipt_ref text,
            requested_decision_digest bytea,
            requested_retention_policy_ref text,
            requested_retention_seconds bigint
        ) RETURNS TABLE ({_return_columns()})
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE
            authority_now timestamptz := pg_catalog.clock_timestamp();
            authority_retain_until timestamptz;
            provider_record public.action_provider_attempt%ROWTYPE;
            reconciliation_record public.action_reconciliation%ROWTYPE;
            receipt_record public.action_receipt%ROWTYPE;
        BEGIN
            IF SESSION_USER <> '{_ACTION}'
               OR requested_organization_id IS NULL
               OR requested_provider_attempt_ref IS NULL
               OR requested_provider_attempt_ref !~ '^pat_[0-9a-f]{{32}}$'
               OR requested_disposition IS NULL
               OR requested_disposition NOT IN ('applied', 'rejected')
               OR requested_decision_digest IS NULL
               OR octet_length(requested_decision_digest) <> 32
               OR requested_retention_policy_ref IS DISTINCT FROM
                    'action-digest-audit-retention-v1'
               OR requested_retention_seconds IS NULL
               OR requested_retention_seconds NOT BETWEEN 2 AND 31536000
               OR (requested_disposition = 'applied' AND (
                    proposed_receipt_ref IS NULL
                    OR proposed_receipt_ref !~ '^acr_[0-9a-f]{{32}}$'
                    OR requested_provider_effect_digest IS NULL
                    OR octet_length(requested_provider_effect_digest) <> 32
                    OR requested_applied_at IS NULL
                    OR requested_applied_at > authority_now +
                        pg_catalog.make_interval(secs => 5)
               ))
               OR (requested_disposition = 'rejected' AND (
                    requested_provider_effect_digest IS NOT NULL
                    OR requested_applied_at IS NOT NULL
               ))
            THEN
                RETURN QUERY SELECT 'rejected'::text,
                    requested_provider_attempt_ref, NULL::text, NULL::text,
                    NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::bytea,
                    NULL::bytea, NULL::bytea, NULL::bytea, NULL::bytea,
                    NULL::timestamptz;
                RETURN;
            END IF;
            PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
                'action-provider-attempt:' || requested_organization_id::text || ':' ||
                requested_provider_attempt_ref, 0
            ));
            SELECT provider.* INTO provider_record
            FROM public.action_provider_attempt AS provider
            WHERE provider.organization_id = requested_organization_id
              AND provider.provider_attempt_ref = requested_provider_attempt_ref;
            SELECT reconciliation.* INTO reconciliation_record
            FROM public.action_reconciliation AS reconciliation
            WHERE reconciliation.organization_id = requested_organization_id
              AND reconciliation.provider_attempt_ref = requested_provider_attempt_ref;
            IF provider_record.provider_attempt_ref IS NULL THEN
                RETURN QUERY SELECT 'rejected'::text,
                    requested_provider_attempt_ref, NULL::text, NULL::text,
                    NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::bytea,
                    NULL::bytea, NULL::bytea, NULL::bytea, NULL::bytea,
                    NULL::timestamptz;
                RETURN;
            END IF;
            authority_retain_until := authority_now + pg_catalog.make_interval(
                secs => requested_retention_seconds
            );
            IF NOT pg_catalog.pg_try_advisory_xact_lock(pg_catalog.hashtextextended(
                'action-ticket-sender-session:' ||
                requested_organization_id::text || ':' ||
                provider_record.ticket_ref, 0
            )) THEN
                INSERT INTO public.action_perform_audit (
                    organization_id, decision_digest, category, recorded_at,
                    retention_policy_ref, retain_until
                ) VALUES (
                    requested_organization_id, requested_decision_digest, 'rejected',
                    authority_now, requested_retention_policy_ref,
                    authority_retain_until
                );
                RETURN QUERY SELECT 'rejected'::text,
                    requested_provider_attempt_ref, NULL::text, NULL::text,
                    NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::bytea,
                    NULL::bytea, NULL::bytea, NULL::bytea, NULL::bytea,
                    NULL::timestamptz;
                RETURN;
            END IF;
            IF reconciliation_record.provider_attempt_ref IS NULL
               AND provider_record.state = 'in_flight'
            THEN
                INSERT INTO public.action_reconciliation (
                    organization_id, provider_attempt_ref, state, created_at,
                    retention_policy_ref, retain_until
                ) VALUES (
                    requested_organization_id, requested_provider_attempt_ref,
                    'pending', authority_now, requested_retention_policy_ref,
                    authority_retain_until
                ) RETURNING * INTO reconciliation_record;
            ELSIF reconciliation_record.provider_attempt_ref IS NULL THEN
                INSERT INTO public.action_perform_audit (
                    organization_id, decision_digest, category, recorded_at,
                    retention_policy_ref, retain_until
                ) VALUES (
                    requested_organization_id, requested_decision_digest, 'rejected',
                    authority_now, requested_retention_policy_ref,
                    authority_retain_until
                );
                RETURN QUERY SELECT 'rejected'::text,
                    requested_provider_attempt_ref, NULL::text, NULL::text,
                    NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::bytea,
                    NULL::bytea, NULL::bytea, NULL::bytea, NULL::bytea,
                    NULL::timestamptz;
                RETURN;
            END IF;
            IF reconciliation_record.state = 'applied' THEN
                IF requested_disposition IS DISTINCT FROM 'applied'
                   OR reconciliation_record.decision_digest IS DISTINCT FROM
                        requested_decision_digest
                   OR provider_record.provider_effect_digest IS DISTINCT FROM
                        requested_provider_effect_digest
                THEN
                    INSERT INTO public.action_perform_audit (
                        organization_id, decision_digest, category, recorded_at,
                        retention_policy_ref, retain_until
                    ) VALUES (
                        requested_organization_id, requested_decision_digest,
                        'rejected', authority_now, requested_retention_policy_ref,
                        authority_retain_until
                    );
                    RETURN QUERY SELECT 'rejected'::text,
                        requested_provider_attempt_ref, NULL::text, NULL::text,
                        NULL::uuid, NULL::text, NULL::text, NULL::text,
                        NULL::bytea, NULL::bytea, NULL::bytea, NULL::bytea,
                        NULL::bytea, NULL::timestamptz;
                    RETURN;
                END IF;
                SELECT receipt.* INTO STRICT receipt_record
                FROM public.action_receipt AS receipt
                WHERE receipt.organization_id = requested_organization_id
                  AND receipt.provider_attempt_ref = requested_provider_attempt_ref;
                INSERT INTO public.action_perform_audit (
                    organization_id, decision_digest, category, recorded_at,
                    retention_policy_ref, retain_until
                ) VALUES (
                    requested_organization_id, requested_decision_digest,
                    'already_applied', authority_now,
                    requested_retention_policy_ref, authority_retain_until
                );
                RETURN QUERY SELECT 'already_applied'::text,
                    receipt_record.provider_attempt_ref, NULL::text,
                    receipt_record.receipt_ref, receipt_record.organization_id,
                    receipt_record.delivery_attempt_ref, receipt_record.ticket_ref,
                    receipt_record.operation, receipt_record.destination_digest,
                    receipt_record.audience_digest, receipt_record.payload_digest,
                    receipt_record.idempotency_digest,
                    receipt_record.provider_effect_digest, receipt_record.applied_at;
                RETURN;
            ELSIF reconciliation_record.state = 'rejected' THEN
                INSERT INTO public.action_perform_audit (
                    organization_id, decision_digest, category, recorded_at,
                    retention_policy_ref, retain_until
                ) VALUES (
                    requested_organization_id, requested_decision_digest, 'rejected',
                    authority_now, requested_retention_policy_ref,
                    authority_retain_until
                );
                IF requested_disposition = 'rejected' AND
                   reconciliation_record.decision_digest = requested_decision_digest
                THEN
                    RETURN QUERY SELECT 'provider_rejected'::text,
                        requested_provider_attempt_ref, NULL::text, NULL::text,
                        NULL::uuid, NULL::text, NULL::text, NULL::text,
                        NULL::bytea, NULL::bytea, NULL::bytea, NULL::bytea,
                        NULL::bytea, NULL::timestamptz;
                ELSE
                    RETURN QUERY SELECT 'rejected'::text,
                        requested_provider_attempt_ref, NULL::text, NULL::text,
                        NULL::uuid, NULL::text, NULL::text, NULL::text,
                        NULL::bytea, NULL::bytea, NULL::bytea, NULL::bytea,
                        NULL::bytea, NULL::timestamptz;
                END IF;
                RETURN;
            ELSIF provider_record.state NOT IN ('in_flight', 'ambiguous')
               OR reconciliation_record.state <> 'pending'
            THEN
                INSERT INTO public.action_perform_audit (
                    organization_id, decision_digest, category, recorded_at,
                    retention_policy_ref, retain_until
                ) VALUES (
                    requested_organization_id, requested_decision_digest, 'rejected',
                    authority_now, requested_retention_policy_ref,
                    authority_retain_until
                );
                RETURN QUERY SELECT 'rejected'::text,
                    requested_provider_attempt_ref, NULL::text, NULL::text,
                    NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::bytea,
                    NULL::bytea, NULL::bytea, NULL::bytea, NULL::bytea,
                    NULL::timestamptz;
                RETURN;
            END IF;

            IF requested_disposition = 'applied' THEN
                UPDATE public.action_provider_attempt AS target_provider SET
                    state = 'applied',
                    provider_effect_digest = requested_provider_effect_digest,
                    terminal_at = authority_now
                WHERE target_provider.organization_id = requested_organization_id
                  AND target_provider.provider_attempt_ref = requested_provider_attempt_ref;
                UPDATE public.action_ticket AS target_ticket SET state = 'applied'
                WHERE target_ticket.organization_id = requested_organization_id
                  AND target_ticket.ticket_ref = provider_record.ticket_ref;
                UPDATE public.action_reconciliation AS target_reconciliation SET
                    state = 'applied', decision_digest = requested_decision_digest,
                    reconciled_at = authority_now
                WHERE target_reconciliation.organization_id = requested_organization_id
                  AND target_reconciliation.provider_attempt_ref = requested_provider_attempt_ref;
                INSERT INTO public.action_receipt (
                    organization_id, receipt_ref, provider_attempt_ref, ticket_ref,
                    delivery_attempt_ref, operation, destination_digest,
                    audience_digest, payload_digest, idempotency_digest,
                    provider_effect_digest, applied_at, recorded_at,
                    retention_policy_ref, retain_until
                ) VALUES (
                    requested_organization_id, proposed_receipt_ref,
                    requested_provider_attempt_ref, provider_record.ticket_ref,
                    provider_record.delivery_attempt_ref, provider_record.operation,
                    provider_record.destination_digest, provider_record.audience_digest,
                    provider_record.payload_digest, provider_record.idempotency_digest,
                    requested_provider_effect_digest, requested_applied_at,
                    authority_now, requested_retention_policy_ref,
                    authority_retain_until
                ) RETURNING * INTO receipt_record;
                INSERT INTO public.action_perform_audit (
                    organization_id, decision_digest, category, recorded_at,
                    retention_policy_ref, retain_until
                ) VALUES (
                    requested_organization_id, requested_decision_digest,
                    'reconciled_applied', authority_now,
                    requested_retention_policy_ref, authority_retain_until
                );
                RETURN QUERY SELECT 'already_applied'::text,
                    receipt_record.provider_attempt_ref, NULL::text,
                    receipt_record.receipt_ref, receipt_record.organization_id,
                    receipt_record.delivery_attempt_ref, receipt_record.ticket_ref,
                    receipt_record.operation, receipt_record.destination_digest,
                    receipt_record.audience_digest, receipt_record.payload_digest,
                    receipt_record.idempotency_digest,
                    receipt_record.provider_effect_digest, receipt_record.applied_at;
                RETURN;
            ELSE
                UPDATE public.action_provider_attempt AS target_provider SET
                    state = 'rejected', terminal_at = authority_now
                WHERE target_provider.organization_id = requested_organization_id
                  AND target_provider.provider_attempt_ref = requested_provider_attempt_ref;
                UPDATE public.action_ticket AS target_ticket SET state = 'rejected'
                WHERE target_ticket.organization_id = requested_organization_id
                  AND target_ticket.ticket_ref = provider_record.ticket_ref;
                UPDATE public.action_reconciliation AS target_reconciliation SET
                    state = 'rejected', decision_digest = requested_decision_digest,
                    reconciled_at = authority_now
                WHERE target_reconciliation.organization_id = requested_organization_id
                  AND target_reconciliation.provider_attempt_ref = requested_provider_attempt_ref;
                INSERT INTO public.action_perform_audit (
                    organization_id, decision_digest, category, recorded_at,
                    retention_policy_ref, retain_until
                ) VALUES (
                    requested_organization_id, requested_decision_digest,
                    'reconciled_rejected', authority_now,
                    requested_retention_policy_ref, authority_retain_until
                );
                RETURN QUERY SELECT 'provider_rejected'::text,
                    requested_provider_attempt_ref, NULL::text, NULL::text,
                    NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::bytea,
                    NULL::bytea, NULL::bytea, NULL::bytea, NULL::bytea,
                    NULL::timestamptz;
                RETURN;
            END IF;
        END;
        $function$
        """
    )

    for function_name, signature in (
        (_BEGIN, _BEGIN_SIGNATURE),
        (_COMPLETE, _COMPLETE_SIGNATURE),
        (_RECONCILE, _RECONCILE_SIGNATURE),
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
            f"GRANT EXECUTE ON FUNCTION public.{function_name}{signature} TO {_ACTION}"
        )
        op.execute("RESET ROLE")


def downgrade() -> None:
    """Refuse to erase retained execution, receipt, or reconciliation rows."""

    op.execute(
        """
        DO $block$ BEGIN
          IF EXISTS (SELECT 1 FROM public.action_provider_attempt)
             OR EXISTS (SELECT 1 FROM public.action_receipt)
             OR EXISTS (SELECT 1 FROM public.action_reconciliation)
             OR EXISTS (SELECT 1 FROM public.action_perform_audit)
          THEN RAISE EXCEPTION USING ERRCODE = '55000',
                 MESSAGE = 'cannot downgrade with retained ActionPlane execution rows';
          END IF;
        END; $block$
        """
    )
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    for function_name, signature in (
        (_RECONCILE, _RECONCILE_SIGNATURE),
        (_COMPLETE, _COMPLETE_SIGNATURE),
        (_BEGIN, _BEGIN_SIGNATURE),
    ):
        op.execute(f"DROP FUNCTION public.{function_name}{signature}")
    op.execute("RESET ROLE")
    op.execute("DROP TRIGGER action_receipt_reject_mutation ON action_receipt")
    op.execute(f"DROP FUNCTION public.{_RECEIPT_IMMUTABILITY}()")
    for table_name in reversed(_REFERENCE_TABLES):
        op.execute(
            f"DROP POLICY {table_name}_action_execute_definer_select "
            f"ON public.{table_name}"
        )
        op.execute(f"REVOKE SELECT ON TABLE public.{table_name} FROM {_DEFINER}")
    op.execute(
        "DROP POLICY action_ticket_action_execute_definer_update "
        "ON public.action_ticket"
    )
    op.execute(
        "DROP POLICY action_ticket_action_execute_definer_select "
        "ON public.action_ticket"
    )
    op.execute(f"REVOKE SELECT, UPDATE ON TABLE public.action_ticket FROM {_DEFINER}")
    op.execute(
        "DROP POLICY action_delivery_attempt_action_execute_definer_select "
        "ON public.action_delivery_attempt"
    )
    op.execute(f"REVOKE SELECT ON TABLE public.action_delivery_attempt FROM {_DEFINER}")
    op.drop_table("action_perform_audit")
    op.drop_table("action_reconciliation")
    op.drop_table("action_receipt")
    op.drop_table("action_provider_attempt")
    op.drop_constraint("ck_action_ticket_bearer_digest", "action_ticket", type_="check")
    op.drop_constraint("ck_action_ticket_profiles", "action_ticket", type_="check")
    op.create_check_constraint(
        "ck_action_ticket_profiles",
        "action_ticket",
        "approval_tier = 'preapproved_private_delivery_v1' AND "
        "profile_ref = 'private-action-prepare-v1' AND state = 'prepared' AND "
        "retention_policy_ref = 'action-digest-audit-retention-v1'",
    )
    op.drop_column("action_ticket", "ticket_bearer_digest")
