"""Persist one exact private ActionPlane prepare authority.

Revision ID: 20260723_0022
Revises: 20260723_0021
Create Date: 2026-07-23
"""

# ruff: noqa: E501

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260723_0022"
down_revision: str | None = "20260723_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MIGRATOR = "context_engine_migrator"
_ACTION = "context_engine_action"
_DEFINER = "context_engine_action_prepare_definer"
_FUNCTION = "context_action_prepare_private_effect"
_SIGNATURE = "(uuid, bytea, bytea, bytea, uuid, uuid, bigint, bytea, bytea, bytea, bytea, bytea, bigint, text, text, bytea, bytea, bytea, text, text, text, text, integer, bigint, uuid, uuid, text, bigint)"
_REFERENCE_TABLES = (
    "delivery_evidence",
    "membership",
    "organization_policy_epoch",
    "context_source",
    "source_version",
)


def _secure_action_table(
    table_name: str,
    *,
    definer_reads: bool,
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
    if definer_reads:
        op.execute(
            f"CREATE POLICY {table_name}_action_prepare_definer_select "
            f"ON public.{table_name} FOR SELECT TO {_DEFINER} USING (true)"
        )
        op.execute(f"GRANT SELECT ON TABLE public.{table_name} TO {_DEFINER}")
    op.execute(
        f"CREATE POLICY {table_name}_action_prepare_definer_insert "
        f"ON public.{table_name} FOR INSERT TO {_DEFINER} WITH CHECK (true)"
    )
    op.execute(f"GRANT INSERT ON TABLE public.{table_name} TO {_DEFINER}")


def upgrade() -> None:
    """Create a function-only, digest-only private prepare boundary."""

    op.create_table(
        "action_delivery_attempt",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("delivery_attempt_ref", sa.Text(), nullable=False),
        sa.Column("authenticated_service_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("delivery_evidence_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("authentication_binding_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("membership_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("membership_version", sa.BigInteger(), nullable=False),
        sa.Column("destination_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("consumer_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("purpose_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("audience_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("identity_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("policy_epoch", sa.BigInteger(), nullable=False),
        sa.Column("profile_ref", sa.Text(), nullable=False),
        sa.Column("retention_policy_ref", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retain_until", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "delivery_attempt_ref",
            name="pk_action_delivery_attempt",
        ),
        sa.UniqueConstraint(
            "delivery_attempt_ref", name="uq_action_delivery_attempt_ref_global"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.organization_id"],
            name="fk_action_delivery_attempt_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "membership_id", "membership_version"],
            [
                "membership.organization_id",
                "membership.membership_id",
                "membership.membership_version",
            ],
            name="fk_action_delivery_attempt_membership_version",
        ),
        sa.CheckConstraint(
            "delivery_attempt_ref ~ '^dla_[0-9a-f]{32}$'",
            name="ck_action_delivery_attempt_ref",
        ),
        sa.CheckConstraint(
            "octet_length(authenticated_service_digest) = 32 AND "
            "octet_length(delivery_evidence_digest) = 32 AND "
            "octet_length(authentication_binding_digest) = 32 AND "
            "octet_length(destination_digest) = 32 AND "
            "octet_length(consumer_digest) = 32 AND "
            "octet_length(purpose_digest) = 32 AND "
            "octet_length(audience_digest) = 32 AND "
            "octet_length(identity_digest) = 32",
            name="ck_action_delivery_attempt_sha256_digests",
        ),
        sa.CheckConstraint(
            "membership_version > 0 AND policy_epoch > 0",
            name="ck_action_delivery_attempt_positive_versions",
        ),
        sa.CheckConstraint(
            "profile_ref = 'private-action-prepare-v1' AND "
            "retention_policy_ref = 'action-digest-audit-retention-v1'",
            name="ck_action_delivery_attempt_profiles",
        ),
        sa.CheckConstraint(
            "retain_until > created_at",
            name="ck_action_delivery_attempt_retention_window",
        ),
    )
    op.create_table(
        "action_ticket",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ticket_ref", sa.Text(), nullable=False),
        sa.Column("delivery_attempt_ref", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("ticket_audience", sa.Text(), nullable=False),
        sa.Column("payload_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("idempotency_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("approval_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("approval_tier", sa.Text(), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("policy_epoch", sa.BigInteger(), nullable=False),
        sa.Column("signing_key_version", sa.Integer(), nullable=False),
        sa.Column("profile_ref", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retention_policy_ref", sa.Text(), nullable=False),
        sa.Column("retain_until", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("organization_id", "ticket_ref", name="pk_action_ticket"),
        sa.UniqueConstraint("ticket_ref", name="uq_action_ticket_ref_global"),
        sa.UniqueConstraint(
            "organization_id",
            "idempotency_digest",
            name="uq_action_ticket_prepare_idempotency",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "delivery_attempt_ref"],
            [
                "action_delivery_attempt.organization_id",
                "action_delivery_attempt.delivery_attempt_ref",
            ],
            name="fk_action_ticket_delivery_attempt",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_id", "source_version_id"],
            [
                "source_version.organization_id",
                "source_version.source_id",
                "source_version.version_id",
            ],
            name="fk_action_ticket_source_version",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "ticket_ref ~ '^act_[0-9a-f]{32}$'",
            name="ck_action_ticket_ref",
        ),
        sa.CheckConstraint(
            "octet_length(payload_digest) = 32 AND "
            "octet_length(idempotency_digest) = 32 AND "
            "octet_length(approval_digest) = 32",
            name="ck_action_ticket_sha256_digests",
        ),
        sa.CheckConstraint(
            "(operation = 'create_placeholder' AND "
            "ticket_audience = 'private-effect:create-placeholder') OR "
            "(operation = 'finalize_reply' AND "
            "ticket_audience = 'private-effect:finalize-reply') OR "
            "(operation = 'send_private_followup' AND "
            "ticket_audience = 'private-effect:send-private-followup')",
            name="ck_action_ticket_operation_audience",
        ),
        sa.CheckConstraint(
            "(source_id IS NULL AND source_version_id IS NULL) OR "
            "(source_id IS NOT NULL AND source_version_id IS NOT NULL)",
            name="ck_action_ticket_source_context_pair",
        ),
        sa.CheckConstraint(
            "approval_tier = 'preapproved_private_delivery_v1' AND "
            "profile_ref = 'private-action-prepare-v1' AND state = 'prepared' AND "
            "retention_policy_ref = 'action-digest-audit-retention-v1'",
            name="ck_action_ticket_profiles",
        ),
        sa.CheckConstraint(
            "policy_epoch > 0 AND signing_key_version > 0",
            name="ck_action_ticket_positive_versions",
        ),
        sa.CheckConstraint(
            "expires_at > issued_at AND expires_at <= issued_at + interval '5 minutes' "
            "AND retain_until > expires_at",
            name="ck_action_ticket_time_windows",
        ),
    )
    op.create_table(
        "action_prepare_audit",
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
            "organization_id", "audit_id", name="pk_action_prepare_audit"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.organization_id"],
            name="fk_action_prepare_audit_organization",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "octet_length(decision_digest) = 32",
            name="ck_action_prepare_audit_decision_digest",
        ),
        sa.CheckConstraint(
            "category IN ('prepared', 'idempotent', 'generic_denied', "
            "'audience_changed')",
            name="ck_action_prepare_audit_category",
        ),
        sa.CheckConstraint(
            "retention_policy_ref = 'action-digest-audit-retention-v1' "
            "AND retain_until > recorded_at",
            name="ck_action_prepare_audit_retention",
        ),
    )

    _secure_action_table("action_delivery_attempt", definer_reads=True)
    _secure_action_table("action_ticket", definer_reads=True)
    _secure_action_table("action_prepare_audit", definer_reads=False)

    for table_name in _REFERENCE_TABLES:
        op.execute(f"GRANT SELECT ON TABLE public.{table_name} TO {_DEFINER}")
        op.execute(
            f"CREATE POLICY {table_name}_action_prepare_definer_select "
            f"ON public.{table_name} FOR SELECT TO {_DEFINER} USING (true)"
        )

    op.execute(
        f"""
        CREATE FUNCTION public.{_FUNCTION}(
            requested_organization_id uuid,
            requested_service_digest bytea,
            requested_evidence_digest bytea,
            requested_authentication_binding_digest bytea,
            requested_user_id uuid,
            requested_membership_id uuid,
            requested_membership_version bigint,
            requested_destination_digest bytea,
            requested_consumer_digest bytea,
            requested_purpose_digest bytea,
            requested_audience_digest bytea,
            requested_identity_digest bytea,
            requested_policy_epoch bigint,
            requested_operation text,
            requested_ticket_audience text,
            requested_payload_digest bytea,
            requested_idempotency_digest bytea,
            requested_approval_digest bytea,
            requested_approval_tier text,
            requested_delivery_attempt_ref text,
            requested_ticket_ref text,
            requested_profile_ref text,
            requested_signing_key_version integer,
            requested_ttl_seconds bigint,
            requested_source_id uuid,
            requested_source_version_id uuid,
            requested_retention_policy_ref text,
            requested_retention_seconds bigint
        ) RETURNS TABLE (
            outcome text,
            delivery_attempt_ref text,
            ticket_ref text,
            issued_at timestamptz,
            expires_at timestamptz,
            idempotent boolean
        )
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE
            authority_now timestamptz := pg_catalog.clock_timestamp();
            authority_expires_at timestamptz;
            authority_retain_until timestamptz;
            expected_audience text;
            expected_identity_digest bytea;
            expected_approval_digest bytea;
            decision_digest bytea;
            evidence_record public.delivery_evidence%ROWTYPE;
            attempt_record public.action_delivery_attempt%ROWTYPE;
            ticket_record public.action_ticket%ROWTYPE;
            current_policy_epoch bigint;
        BEGIN
            CASE requested_operation
                WHEN 'create_placeholder' THEN
                    expected_audience := 'private-effect:create-placeholder';
                WHEN 'finalize_reply' THEN
                    expected_audience := 'private-effect:finalize-reply';
                WHEN 'send_private_followup' THEN
                    expected_audience := 'private-effect:send-private-followup';
                ELSE
                    RETURN QUERY SELECT 'generic_denied'::text, NULL::text,
                        NULL::text, NULL::timestamptz, NULL::timestamptz, false;
                    RETURN;
            END CASE;

            IF SESSION_USER <> '{_ACTION}'
               OR requested_ticket_audience <> expected_audience
               OR requested_profile_ref <> 'private-action-prepare-v1'
               OR requested_approval_tier <> 'preapproved_private_delivery_v1'
               OR requested_retention_policy_ref <>
                    'action-digest-audit-retention-v1'
               OR requested_ttl_seconds NOT BETWEEN 1 AND 300
               OR requested_retention_seconds NOT BETWEEN
                    requested_ttl_seconds + 1 AND 31536000
               OR requested_membership_version <= 0
               OR requested_policy_epoch <= 0
               OR requested_signing_key_version <= 0
               OR requested_delivery_attempt_ref !~ '^dla_[0-9a-f]{{32}}$'
               OR requested_ticket_ref !~ '^act_[0-9a-f]{{32}}$'
               OR octet_length(requested_service_digest) <> 32
               OR octet_length(requested_evidence_digest) <> 32
               OR octet_length(requested_authentication_binding_digest) <> 32
               OR octet_length(requested_destination_digest) <> 32
               OR octet_length(requested_consumer_digest) <> 32
               OR octet_length(requested_purpose_digest) <> 32
               OR octet_length(requested_audience_digest) <> 32
               OR octet_length(requested_identity_digest) <> 32
               OR octet_length(requested_payload_digest) <> 32
               OR octet_length(requested_idempotency_digest) <> 32
               OR octet_length(requested_approval_digest) <> 32
               OR (requested_source_id IS NULL) <>
                    (requested_source_version_id IS NULL)
            THEN
                RETURN QUERY SELECT 'generic_denied'::text, NULL::text,
                    NULL::text, NULL::timestamptz, NULL::timestamptz, false;
                RETURN;
            END IF;

            SELECT evidence.* INTO evidence_record
            FROM public.delivery_evidence AS evidence
            WHERE evidence.organization_id = requested_organization_id
              AND evidence.evidence_digest = requested_evidence_digest;
            IF NOT FOUND THEN
                RETURN QUERY SELECT 'generic_denied'::text, NULL::text,
                    NULL::text, NULL::timestamptz, NULL::timestamptz, false;
                RETURN;
            END IF;

            expected_identity_digest := public.digest(
                pg_catalog.convert_to(
                    'context-engine.action-binding.v1', 'UTF8'
                ) || pg_catalog.decode('00', 'hex') ||
                pg_catalog.convert_to('identity', 'UTF8') ||
                pg_catalog.decode('00', 'hex') ||
                pg_catalog.convert_to('36', 'UTF8') ||
                pg_catalog.decode('00', 'hex') ||
                pg_catalog.convert_to(requested_organization_id::text, 'UTF8') ||
                pg_catalog.convert_to('36', 'UTF8') ||
                pg_catalog.decode('00', 'hex') ||
                pg_catalog.convert_to(requested_user_id::text, 'UTF8') ||
                pg_catalog.convert_to('36', 'UTF8') ||
                pg_catalog.decode('00', 'hex') ||
                pg_catalog.convert_to(requested_membership_id::text, 'UTF8') ||
                pg_catalog.convert_to(
                    octet_length(pg_catalog.convert_to(
                        requested_membership_version::text, 'UTF8'
                    ))::text,
                    'UTF8'
                ) || pg_catalog.decode('00', 'hex') ||
                pg_catalog.convert_to(
                    requested_membership_version::text, 'UTF8'
                ) ||
                pg_catalog.convert_to(
                    octet_length(pg_catalog.convert_to(
                        evidence_record.authentication_binding_ref, 'UTF8'
                    ))::text,
                    'UTF8'
                ) || pg_catalog.decode('00', 'hex') ||
                pg_catalog.convert_to(
                    evidence_record.authentication_binding_ref, 'UTF8'
                ),
                'sha256'
            );

            decision_digest := public.digest(
                pg_catalog.convert_to(
                    requested_organization_id::text || ':' ||
                    requested_operation || ':' ||
                    pg_catalog.encode(requested_idempotency_digest, 'hex') || ':' ||
                    pg_catalog.encode(requested_payload_digest, 'hex'),
                    'UTF8'
                ),
                'sha256'
            );
            expected_approval_digest := public.digest(
                pg_catalog.convert_to(
                    'context-engine.action-binding.v1', 'UTF8'
                ) || pg_catalog.decode('00', 'hex') ||
                pg_catalog.convert_to('approval', 'UTF8') ||
                pg_catalog.decode('00', 'hex') ||
                pg_catalog.convert_to(
                    octet_length(pg_catalog.convert_to(
                        requested_approval_tier, 'UTF8'
                    ))::text, 'UTF8'
                ) || pg_catalog.decode('00', 'hex') ||
                pg_catalog.convert_to(requested_approval_tier, 'UTF8'),
                'sha256'
            );

            IF evidence_record.delivery_kind <> 'private'
               OR evidence_record.profile_ref <> 'private-delivery-evidence-v1'
               OR evidence_record.expires_at <= authority_now
               OR evidence_record.issued_at > authority_now
               OR public.digest(pg_catalog.convert_to(
                    evidence_record.authenticated_service_ref, 'UTF8'), 'sha256')
                    <> requested_service_digest
               OR public.digest(pg_catalog.convert_to(
                    evidence_record.authentication_binding_ref, 'UTF8'), 'sha256')
                    <> requested_authentication_binding_digest
               OR evidence_record.user_id <> requested_user_id
               OR evidence_record.membership_id <> requested_membership_id
               OR evidence_record.membership_version <>
                    requested_membership_version
               OR public.digest(pg_catalog.convert_to(
                    evidence_record.destination_ref, 'UTF8'), 'sha256')
                    <> requested_destination_digest
               OR public.digest(pg_catalog.convert_to(
                    evidence_record.consumer_ref, 'UTF8'), 'sha256')
                    <> requested_consumer_digest
               OR public.digest(pg_catalog.convert_to(
                    evidence_record.purpose, 'UTF8'), 'sha256')
                    <> requested_purpose_digest
               OR evidence_record.policy_epoch <> requested_policy_epoch
               OR expected_identity_digest IS DISTINCT FROM requested_identity_digest
               OR expected_approval_digest <> requested_approval_digest
            THEN
                INSERT INTO public.action_prepare_audit (
                    organization_id, decision_digest, category, recorded_at,
                    retention_policy_ref, retain_until
                ) VALUES (
                    requested_organization_id, decision_digest,
                    'generic_denied', authority_now,
                    requested_retention_policy_ref,
                    authority_now + pg_catalog.make_interval(
                        secs => requested_retention_seconds
                    )
                );
                RETURN QUERY SELECT 'generic_denied'::text, NULL::text,
                    NULL::text, NULL::timestamptz, NULL::timestamptz, false;
                RETURN;
            END IF;

            IF pg_catalog.decode(evidence_record.audience_digest, 'hex') <>
                    requested_audience_digest
               OR NOT EXISTS (
                    SELECT 1 FROM public.membership AS membership
                    WHERE membership.organization_id = requested_organization_id
                      AND membership.user_id = requested_user_id
                      AND membership.membership_id = requested_membership_id
                      AND membership.membership_version =
                            requested_membership_version
                      AND membership.status = 'active'
                      AND membership.valid_from <= authority_now
                      AND (membership.valid_until IS NULL OR
                           membership.valid_until > authority_now)
               )
            THEN
                INSERT INTO public.action_prepare_audit (
                    organization_id, decision_digest, category, recorded_at,
                    retention_policy_ref, retain_until
                ) VALUES (
                    requested_organization_id, decision_digest,
                    'audience_changed', authority_now,
                    requested_retention_policy_ref,
                    authority_now + pg_catalog.make_interval(
                        secs => requested_retention_seconds
                    )
                );
                RETURN QUERY SELECT 'audience_changed'::text, NULL::text,
                    NULL::text, NULL::timestamptz, NULL::timestamptz, false;
                RETURN;
            END IF;

            SELECT epoch.policy_epoch INTO current_policy_epoch
            FROM public.organization_policy_epoch AS epoch
            WHERE epoch.organization_id = requested_organization_id;
            IF current_policy_epoch IS DISTINCT FROM requested_policy_epoch
               OR (
                    requested_source_id IS NOT NULL AND NOT EXISTS (
                        SELECT 1
                        FROM public.context_source AS source
                        JOIN public.source_version AS version
                          ON version.organization_id = source.organization_id
                         AND version.source_id = source.source_id
                         AND version.version_id = requested_source_version_id
                        WHERE source.organization_id = requested_organization_id
                          AND source.source_id = requested_source_id
                          AND source.active_version_id =
                                requested_source_version_id
                          AND source.lifecycle_state = 'active'
                    )
               )
            THEN
                INSERT INTO public.action_prepare_audit (
                    organization_id, decision_digest, category, recorded_at,
                    retention_policy_ref, retain_until
                ) VALUES (
                    requested_organization_id, decision_digest,
                    'generic_denied', authority_now,
                    requested_retention_policy_ref,
                    authority_now + pg_catalog.make_interval(
                        secs => requested_retention_seconds
                    )
                );
                RETURN QUERY SELECT 'generic_denied'::text, NULL::text,
                    NULL::text, NULL::timestamptz, NULL::timestamptz, false;
                RETURN;
            END IF;

            PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
                'action-attempt:' || requested_organization_id::text || ':' ||
                requested_delivery_attempt_ref, 0
            ));
            PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
                'action-idempotency:' || requested_organization_id::text || ':' ||
                pg_catalog.encode(requested_idempotency_digest, 'hex'), 0
            ));

            SELECT ticket.* INTO ticket_record
            FROM public.action_ticket AS ticket
            WHERE ticket.organization_id = requested_organization_id
              AND ticket.idempotency_digest = requested_idempotency_digest;
            IF FOUND THEN
                SELECT attempt.* INTO STRICT attempt_record
                FROM public.action_delivery_attempt AS attempt
                WHERE attempt.organization_id = ticket_record.organization_id
                  AND attempt.delivery_attempt_ref =
                        ticket_record.delivery_attempt_ref;
                IF ticket_record.delivery_attempt_ref <>
                        requested_delivery_attempt_ref
                   OR ticket_record.operation <> requested_operation
                   OR ticket_record.ticket_audience <> requested_ticket_audience
                   OR ticket_record.payload_digest <> requested_payload_digest
                   OR ticket_record.approval_digest <> requested_approval_digest
                   OR ticket_record.approval_tier <> requested_approval_tier
                   OR ticket_record.policy_epoch <> requested_policy_epoch
                   OR ticket_record.signing_key_version <>
                        requested_signing_key_version
                   OR ticket_record.profile_ref <> requested_profile_ref
                   OR ticket_record.source_id IS DISTINCT FROM requested_source_id
                   OR ticket_record.source_version_id IS DISTINCT FROM
                        requested_source_version_id
                   OR ticket_record.expires_at <= authority_now
                   OR attempt_record.authenticated_service_digest <>
                        requested_service_digest
                   OR attempt_record.delivery_evidence_digest <>
                        requested_evidence_digest
                   OR attempt_record.authentication_binding_digest <>
                        requested_authentication_binding_digest
                   OR attempt_record.user_id <> requested_user_id
                   OR attempt_record.membership_id <> requested_membership_id
                   OR attempt_record.membership_version <>
                        requested_membership_version
                   OR attempt_record.destination_digest <>
                        requested_destination_digest
                   OR attempt_record.consumer_digest <> requested_consumer_digest
                   OR attempt_record.purpose_digest <> requested_purpose_digest
                   OR attempt_record.audience_digest <> requested_audience_digest
                   OR attempt_record.identity_digest <> requested_identity_digest
                   OR attempt_record.policy_epoch <> requested_policy_epoch
                THEN
                    INSERT INTO public.action_prepare_audit (
                        organization_id, decision_digest, category, recorded_at,
                        retention_policy_ref, retain_until
                    ) VALUES (
                        requested_organization_id, decision_digest,
                        'generic_denied', authority_now,
                        requested_retention_policy_ref,
                        authority_now + pg_catalog.make_interval(
                            secs => requested_retention_seconds
                        )
                    );
                    RETURN QUERY SELECT 'generic_denied'::text, NULL::text,
                        NULL::text, NULL::timestamptz, NULL::timestamptz, false;
                    RETURN;
                END IF;
                INSERT INTO public.action_prepare_audit (
                    organization_id, decision_digest, category, recorded_at,
                    retention_policy_ref, retain_until
                ) VALUES (
                    requested_organization_id, decision_digest, 'idempotent',
                    authority_now, requested_retention_policy_ref,
                    authority_now + pg_catalog.make_interval(
                        secs => requested_retention_seconds
                    )
                );
                RETURN QUERY SELECT 'prepared'::text,
                    ticket_record.delivery_attempt_ref,
                    ticket_record.ticket_ref, ticket_record.issued_at,
                    ticket_record.expires_at, true;
                RETURN;
            END IF;

            SELECT attempt.* INTO attempt_record
            FROM public.action_delivery_attempt AS attempt
            WHERE attempt.organization_id = requested_organization_id
              AND attempt.delivery_attempt_ref = requested_delivery_attempt_ref;
            IF FOUND AND (
                attempt_record.authenticated_service_digest <>
                    requested_service_digest
                OR attempt_record.delivery_evidence_digest <>
                    requested_evidence_digest
                OR attempt_record.authentication_binding_digest <>
                    requested_authentication_binding_digest
                OR attempt_record.user_id <> requested_user_id
                OR attempt_record.membership_id <> requested_membership_id
                OR attempt_record.membership_version <>
                    requested_membership_version
                OR attempt_record.destination_digest <> requested_destination_digest
                OR attempt_record.consumer_digest <> requested_consumer_digest
                OR attempt_record.purpose_digest <> requested_purpose_digest
                OR attempt_record.audience_digest <> requested_audience_digest
                OR attempt_record.identity_digest <> requested_identity_digest
                OR attempt_record.policy_epoch <> requested_policy_epoch
                OR attempt_record.profile_ref <> requested_profile_ref
            ) THEN
                INSERT INTO public.action_prepare_audit (
                    organization_id, decision_digest, category, recorded_at,
                    retention_policy_ref, retain_until
                ) VALUES (
                    requested_organization_id, decision_digest,
                    'generic_denied', authority_now,
                    requested_retention_policy_ref,
                    authority_now + pg_catalog.make_interval(
                        secs => requested_retention_seconds
                    )
                );
                RETURN QUERY SELECT 'generic_denied'::text, NULL::text,
                    NULL::text, NULL::timestamptz, NULL::timestamptz, false;
                RETURN;
            ELSIF NOT FOUND THEN
                authority_retain_until := authority_now +
                    pg_catalog.make_interval(secs => requested_retention_seconds);
                INSERT INTO public.action_delivery_attempt (
                    organization_id, delivery_attempt_ref,
                    authenticated_service_digest, delivery_evidence_digest,
                    authentication_binding_digest, user_id, membership_id,
                    membership_version, destination_digest, consumer_digest,
                    purpose_digest, audience_digest, identity_digest,
                    policy_epoch, profile_ref, retention_policy_ref,
                    created_at, retain_until
                ) VALUES (
                    requested_organization_id, requested_delivery_attempt_ref,
                    requested_service_digest, requested_evidence_digest,
                    requested_authentication_binding_digest, requested_user_id,
                    requested_membership_id, requested_membership_version,
                    requested_destination_digest, requested_consumer_digest,
                    requested_purpose_digest, requested_audience_digest,
                    requested_identity_digest, requested_policy_epoch,
                    requested_profile_ref, requested_retention_policy_ref,
                    authority_now, authority_retain_until
                );
            END IF;

            authority_expires_at := authority_now +
                pg_catalog.make_interval(secs => requested_ttl_seconds);
            authority_retain_until := authority_now +
                pg_catalog.make_interval(secs => requested_retention_seconds);
            INSERT INTO public.action_ticket (
                organization_id, ticket_ref, delivery_attempt_ref, operation,
                ticket_audience, payload_digest, idempotency_digest,
                approval_digest, approval_tier, source_id, source_version_id,
                policy_epoch, signing_key_version, profile_ref, state,
                issued_at, expires_at, retention_policy_ref, retain_until
            ) VALUES (
                requested_organization_id, requested_ticket_ref,
                requested_delivery_attempt_ref, requested_operation,
                requested_ticket_audience, requested_payload_digest,
                requested_idempotency_digest, requested_approval_digest,
                requested_approval_tier, requested_source_id,
                requested_source_version_id, requested_policy_epoch,
                requested_signing_key_version, requested_profile_ref,
                'prepared', authority_now, authority_expires_at,
                requested_retention_policy_ref, authority_retain_until
            );
            INSERT INTO public.action_prepare_audit (
                organization_id, decision_digest, category, recorded_at,
                retention_policy_ref, retain_until
            ) VALUES (
                requested_organization_id, decision_digest, 'prepared',
                authority_now, requested_retention_policy_ref,
                authority_retain_until
            );
            RETURN QUERY SELECT 'prepared'::text,
                requested_delivery_attempt_ref, requested_ticket_ref,
                authority_now, authority_expires_at, false;
        EXCEPTION
            WHEN unique_violation OR serialization_failure OR deadlock_detected THEN
                RETURN QUERY SELECT 'retryable_unavailable'::text, NULL::text,
                    NULL::text, NULL::timestamptz, NULL::timestamptz, false;
        END;
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_FUNCTION}{_SIGNATURE} FROM PUBLIC")
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"ALTER FUNCTION public.{_FUNCTION}{_SIGNATURE} OWNER TO {_DEFINER}")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.{_FUNCTION}{_SIGNATURE} TO {_ACTION}")
    op.execute("RESET ROLE")


def downgrade() -> None:
    """Refuse to erase retained action authority or audit rows."""

    op.execute(
        """
        DO $block$ BEGIN
          IF EXISTS (SELECT 1 FROM public.action_delivery_attempt)
             OR EXISTS (SELECT 1 FROM public.action_ticket)
             OR EXISTS (SELECT 1 FROM public.action_prepare_audit)
          THEN RAISE EXCEPTION USING ERRCODE = '55000',
                 MESSAGE = 'cannot downgrade with retained ActionPlane rows';
          END IF;
        END; $block$
        """
    )
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(f"DROP FUNCTION public.{_FUNCTION}{_SIGNATURE}")
    op.execute("RESET ROLE")
    for table_name in reversed(_REFERENCE_TABLES):
        op.execute(
            f"DROP POLICY {table_name}_action_prepare_definer_select "
            f"ON public.{table_name}"
        )
        op.execute(f"REVOKE SELECT ON TABLE public.{table_name} FROM {_DEFINER}")
    op.drop_table("action_prepare_audit")
    op.drop_table("action_ticket")
    op.drop_table("action_delivery_attempt")
