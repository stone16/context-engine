"""Persist and redeem exact private DeliveryEvidenceRef bindings.

Revision ID: 20260723_0019
Revises: 20260723_0018
Create Date: 2026-07-23
"""

# ruff: noqa: E501

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260723_0019"
down_revision: str | None = "20260723_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "delivery_evidence"
_MIGRATOR = "context_engine_migrator"
_IDENTITY = "context_engine_identity"
_RUNTIME = "context_engine_runtime"
_CONTROL = "context_engine_control"
_WORKER = "context_engine_worker"
_LEARNING = "context_engine_learning"
_OPERATOR = "context_engine_security_operator"
_DEFINER = "context_engine_delivery_evidence_definer"
_ISSUE = "context_identity_issue_private_delivery_evidence"
_DELETE_EXPIRED = "context_identity_delete_expired_private_delivery_evidence"
_REDEEM = "context_runtime_redeem_private_delivery_evidence"
_ISSUE_SIGNATURE = "(uuid, bytea, text, text, text, text, uuid, uuid, bigint, text, text, text, text, bigint, timestamptz, timestamptz, text, text)"
_DELETE_EXPIRED_SIGNATURE = "(uuid)"
_REDEEM_SIGNATURE = "(bytea, text, text, text, uuid, uuid, uuid, bigint, text, text, text, text, text, bigint, timestamptz)"


def upgrade() -> None:
    """Create a digest-only function boundary for private delivery evidence."""

    op.create_table(
        _TABLE,
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("digest_profile", sa.Text(), nullable=False),
        sa.Column("delivery_kind", sa.Text(), nullable=False),
        sa.Column("authenticated_service_ref", sa.Text(), nullable=False),
        sa.Column("authentication_binding_ref", sa.Text(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("membership_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("membership_version", sa.BigInteger(), nullable=False),
        sa.Column("destination_ref", sa.Text(), nullable=False),
        sa.Column("consumer_ref", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("audience_digest", sa.Text(), nullable=False),
        sa.Column("policy_epoch", sa.BigInteger(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("logical_resolution_ref", sa.Text(), nullable=False),
        sa.Column("profile_ref", sa.Text(), nullable=False),
        sa.Column("first_redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint(
            "organization_id", "evidence_digest", name="pk_delivery_evidence"
        ),
        sa.UniqueConstraint(
            "evidence_digest", name="uq_delivery_evidence_digest_global"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "authenticated_service_ref",
            "request_id",
            name="uq_delivery_evidence_logical_request",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "logical_resolution_ref",
            name="uq_delivery_evidence_logical_resolution",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.organization_id"],
            name="fk_delivery_evidence_organization",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "membership_id", "membership_version"],
            ["membership.organization_id", "membership.membership_id", "membership.membership_version"],
            name="fk_delivery_evidence_membership_version_same_organization",
        ),
        sa.CheckConstraint(
            "octet_length(evidence_digest) = 32",
            name="ck_delivery_evidence_digest_sha256",
        ),
        sa.CheckConstraint(
            "digest_profile = 'delivery-evidence-ref-sha256-v1'",
            name="ck_delivery_evidence_digest_profile",
        ),
        sa.CheckConstraint(
            "delivery_kind = 'private'",
            name="ck_delivery_evidence_private_kind",
        ),
        sa.CheckConstraint(
            "profile_ref = 'private-delivery-evidence-v1'",
            name="ck_delivery_evidence_profile",
        ),
        sa.CheckConstraint(
            "membership_version > 0 AND policy_epoch > 0",
            name="ck_delivery_evidence_positive_versions",
        ),
        sa.CheckConstraint(
            "audience_digest ~ '^[0-9a-f]{64}$'",
            name="ck_delivery_evidence_audience_digest",
        ),
        sa.CheckConstraint(
            "expires_at > issued_at AND (first_redeemed_at IS NULL OR (first_redeemed_at >= issued_at AND first_redeemed_at < expires_at))",
            name="ck_delivery_evidence_timestamp_order",
        ),
        sa.CheckConstraint(
            "btrim(authenticated_service_ref) <> '' AND btrim(authentication_binding_ref) <> '' AND btrim(request_id) <> '' AND btrim(destination_ref) <> '' AND btrim(consumer_ref) <> '' AND btrim(purpose) <> '' AND btrim(logical_resolution_ref) <> ''",
            name="ck_delivery_evidence_bindings_nonblank",
        ),
    )

    for role in (
        "PUBLIC",
        _IDENTITY,
        _RUNTIME,
        _CONTROL,
        _WORKER,
        _LEARNING,
        _OPERATOR,
        _DEFINER,
    ):
        op.execute(f"REVOKE ALL ON TABLE {_TABLE} FROM {role}")
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY delivery_evidence_migrator_administration ON {_TABLE} FOR ALL TO {_MIGRATOR} USING (true) WITH CHECK (true)"
    )
    op.execute(
        f"CREATE POLICY delivery_evidence_definer_all ON {_TABLE} FOR ALL TO {_DEFINER} USING (true) WITH CHECK (true)"
    )
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {_TABLE} TO {_DEFINER}"
    )
    op.execute(f"GRANT SELECT ON TABLE membership TO {_DEFINER}")
    op.execute(f"GRANT SELECT ON TABLE organization_policy_epoch TO {_DEFINER}")
    op.execute(
        f"CREATE POLICY membership_delivery_evidence_definer_select ON membership FOR SELECT TO {_DEFINER} USING (true)"
    )
    op.execute(
        f"CREATE POLICY organization_policy_epoch_delivery_evidence_definer_select ON organization_policy_epoch FOR SELECT TO {_DEFINER} USING (true)"
    )

    op.execute(
        f"""
        CREATE FUNCTION public.{_ISSUE}(
            requested_organization_id uuid,
            requested_evidence_digest bytea,
            requested_digest_profile text,
            requested_authenticated_service_ref text,
            requested_authentication_binding_ref text,
            requested_request_id text,
            requested_user_id uuid,
            requested_membership_id uuid,
            requested_membership_version bigint,
            requested_destination_ref text,
            requested_consumer_ref text,
            requested_purpose text,
            requested_audience_digest text,
            requested_policy_epoch bigint,
            requested_issued_at timestamptz,
            requested_expires_at timestamptz,
            requested_logical_resolution_ref text,
            requested_profile_ref text
        ) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE
            authority_now timestamptz := pg_catalog.clock_timestamp();
        BEGIN
            IF SESSION_USER <> '{_IDENTITY}'
               OR requested_issued_at > authority_now
               OR requested_expires_at <= authority_now
               OR requested_digest_profile <> 'delivery-evidence-ref-sha256-v1'
               OR requested_profile_ref <> 'private-delivery-evidence-v1'
               OR NOT EXISTS (
                    SELECT 1 FROM public.membership AS membership
                    WHERE membership.organization_id = requested_organization_id
                      AND membership.user_id = requested_user_id
                      AND membership.membership_id = requested_membership_id
                      AND membership.membership_version = requested_membership_version
                      AND membership.status = 'active'
                      AND membership.valid_from <= authority_now
                      AND (membership.valid_until IS NULL OR membership.valid_until > authority_now)
               )
               OR NOT EXISTS (
                    SELECT 1 FROM public.organization_policy_epoch AS epoch
                    WHERE epoch.organization_id = requested_organization_id
                      AND epoch.policy_epoch = requested_policy_epoch
               )
            THEN RETURN false; END IF;
            INSERT INTO public.{_TABLE} (
                organization_id, evidence_digest, digest_profile, delivery_kind,
                authenticated_service_ref, authentication_binding_ref,
                request_id, user_id, membership_id, membership_version,
                destination_ref, consumer_ref, purpose, audience_digest,
                policy_epoch, issued_at, expires_at, logical_resolution_ref,
                profile_ref
            ) VALUES (
                requested_organization_id, requested_evidence_digest,
                requested_digest_profile, 'private',
                requested_authenticated_service_ref,
                requested_authentication_binding_ref, requested_request_id,
                requested_user_id, requested_membership_id,
                requested_membership_version, requested_destination_ref,
                requested_consumer_ref, requested_purpose,
                requested_audience_digest, requested_policy_epoch,
                requested_issued_at, requested_expires_at,
                requested_logical_resolution_ref, requested_profile_ref
            ) ON CONFLICT DO NOTHING;
            RETURN FOUND;
        END;
        $function$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION public.{_DELETE_EXPIRED}(
            requested_organization_id uuid
        ) RETURNS bigint
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE
            deleted_count bigint;
        BEGIN
            IF SESSION_USER <> '{_IDENTITY}' THEN RETURN 0; END IF;
            DELETE FROM public.{_TABLE} AS evidence
            WHERE evidence.organization_id = requested_organization_id
              AND evidence.delivery_kind = 'private'
              AND evidence.expires_at <= pg_catalog.clock_timestamp();
            GET DIAGNOSTICS deleted_count = ROW_COUNT;
            RETURN deleted_count;
        END;
        $function$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION public.{_REDEEM}(
            requested_evidence_digest bytea,
            requested_authenticated_service_ref text,
            requested_authentication_binding_ref text,
            requested_request_id text,
            requested_organization_id uuid,
            requested_user_id uuid,
            requested_membership_id uuid,
            requested_membership_version bigint,
            requested_destination_ref text,
            requested_consumer_ref text,
            requested_delivery_kind text,
            requested_audience_digest text,
            requested_purpose text,
            requested_policy_epoch bigint,
            requested_redeemed_at timestamptz
        ) RETURNS TABLE (
            organization_id uuid, user_id uuid, membership_id uuid,
            membership_version bigint, authenticated_service_ref text,
            authentication_binding_ref text, request_id text,
            destination_ref text, consumer_ref text, purpose text,
            delivery_kind text, audience_digest text, policy_epoch bigint,
            issued_at timestamptz,
            expires_at timestamptz, logical_resolution_ref text,
            profile_ref text
        )
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        BEGIN
            IF SESSION_USER <> '{_RUNTIME}' THEN RETURN; END IF;
            UPDATE public.{_TABLE} AS evidence
            SET first_redeemed_at = COALESCE(evidence.first_redeemed_at, requested_redeemed_at)
            WHERE evidence.organization_id = requested_organization_id
              AND evidence.evidence_digest = requested_evidence_digest
              AND evidence.digest_profile = 'delivery-evidence-ref-sha256-v1'
              AND evidence.delivery_kind = 'private'
              AND evidence.authenticated_service_ref = requested_authenticated_service_ref
              AND evidence.authentication_binding_ref = requested_authentication_binding_ref
              AND evidence.request_id = requested_request_id
              AND evidence.user_id = requested_user_id
              AND evidence.membership_id = requested_membership_id
              AND evidence.membership_version = requested_membership_version
              AND evidence.destination_ref = requested_destination_ref
              AND evidence.consumer_ref = requested_consumer_ref
              AND evidence.delivery_kind = requested_delivery_kind
              AND evidence.audience_digest = requested_audience_digest
              AND evidence.purpose = requested_purpose
              AND evidence.policy_epoch = requested_policy_epoch
              AND evidence.issued_at <= requested_redeemed_at
              AND requested_redeemed_at < evidence.expires_at
              AND EXISTS (
                    SELECT 1 FROM public.membership AS membership
                    WHERE membership.organization_id = evidence.organization_id
                      AND membership.user_id = evidence.user_id
                      AND membership.membership_id = evidence.membership_id
                      AND membership.membership_version = evidence.membership_version
                      AND membership.status = 'active'
                      AND membership.valid_from <= requested_redeemed_at
                      AND (membership.valid_until IS NULL OR membership.valid_until > requested_redeemed_at)
              )
              AND EXISTS (
                    SELECT 1 FROM public.organization_policy_epoch AS epoch
                    WHERE epoch.organization_id = evidence.organization_id
                      AND epoch.policy_epoch = evidence.policy_epoch
              );
            IF NOT FOUND THEN RETURN; END IF;
            RETURN QUERY
            SELECT evidence.organization_id, evidence.user_id,
                   evidence.membership_id, evidence.membership_version,
                   evidence.authenticated_service_ref,
                   evidence.authentication_binding_ref, evidence.request_id,
                   evidence.destination_ref, evidence.consumer_ref,
                   evidence.purpose, evidence.delivery_kind,
                   evidence.audience_digest,
                   evidence.policy_epoch, evidence.issued_at,
                   evidence.expires_at, evidence.logical_resolution_ref,
                   evidence.profile_ref
            FROM public.{_TABLE} AS evidence
            WHERE evidence.organization_id = requested_organization_id
              AND evidence.evidence_digest = requested_evidence_digest
              AND evidence.authenticated_service_ref = requested_authenticated_service_ref
              AND evidence.authentication_binding_ref = requested_authentication_binding_ref
              AND evidence.request_id = requested_request_id
              AND evidence.user_id = requested_user_id
              AND evidence.membership_id = requested_membership_id
              AND evidence.membership_version = requested_membership_version
              AND evidence.destination_ref = requested_destination_ref
              AND evidence.consumer_ref = requested_consumer_ref
              AND evidence.delivery_kind = requested_delivery_kind
              AND evidence.audience_digest = requested_audience_digest
              AND evidence.purpose = requested_purpose
              AND evidence.policy_epoch = requested_policy_epoch
              AND evidence.issued_at <= requested_redeemed_at
              AND requested_redeemed_at < evidence.expires_at;
        END;
        $function$
        """
    )
    for function_name, signature in (
        (_ISSUE, _ISSUE_SIGNATURE),
        (_DELETE_EXPIRED, _DELETE_EXPIRED_SIGNATURE),
        (_REDEEM, _REDEEM_SIGNATURE),
    ):
        op.execute(f"REVOKE ALL ON FUNCTION public.{function_name}{signature} FROM PUBLIC")
        op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
        op.execute(f"ALTER FUNCTION public.{function_name}{signature} OWNER TO {_DEFINER}")
        op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.{_ISSUE}{_ISSUE_SIGNATURE} TO {_IDENTITY}")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.{_DELETE_EXPIRED}{_DELETE_EXPIRED_SIGNATURE} TO {_IDENTITY}")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.{_REDEEM}{_REDEEM_SIGNATURE} TO {_RUNTIME}")
    op.execute("RESET ROLE")


def downgrade() -> None:
    """Remove the carrier only when no evidence row has ever been committed."""

    op.execute(
        f"""
        DO $block$
        BEGIN
            IF EXISTS (SELECT 1 FROM public.{_TABLE}) THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'cannot downgrade with delivery evidence rows';
            END IF;
        END;
        $block$
        """
    )
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(f"DROP FUNCTION public.{_REDEEM}{_REDEEM_SIGNATURE}")
    op.execute(f"DROP FUNCTION public.{_DELETE_EXPIRED}{_DELETE_EXPIRED_SIGNATURE}")
    op.execute(f"DROP FUNCTION public.{_ISSUE}{_ISSUE_SIGNATURE}")
    op.execute("RESET ROLE")
    op.execute("DROP POLICY organization_policy_epoch_delivery_evidence_definer_select ON organization_policy_epoch")
    op.execute("DROP POLICY membership_delivery_evidence_definer_select ON membership")
    op.execute(f"REVOKE SELECT ON TABLE organization_policy_epoch FROM {_DEFINER}")
    op.execute(f"REVOKE SELECT ON TABLE membership FROM {_DEFINER}")
    op.drop_table(_TABLE)
