"""Bind private effects from database-owned delivery evidence.

Revision ID: 20260724_0027
Revises: 20260724_0026
Create Date: 2026-07-24
"""

# ruff: noqa: E501

from collections.abc import Sequence

from alembic import op

revision: str = "20260724_0027"
down_revision: str | None = "20260724_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTION = "context_engine_action"
_DEFINER = "context_engine_action_execute_definer"
_FUNCTION = "context_action_bind_private_delivery_effect"
_SIGNATURE = "(bytea, bytea, bytea, text, bytea, bytea)"


def upgrade() -> None:
    """Create the only public-to-nominal private-effect binding boundary."""

    op.execute(
        f"""
        CREATE FUNCTION public.{_FUNCTION}(
            requested_evidence_digest bytea,
            requested_service_digest bytea,
            requested_consumer_digest bytea,
            requested_request_id text,
            requested_destination_digest bytea,
            requested_purpose_digest bytea
        ) RETURNS TABLE (
            outcome text,
            organization_id uuid,
            user_id uuid,
            membership_id uuid,
            membership_version bigint,
            authenticated_service_ref text,
            authentication_binding_ref text,
            destination_ref text,
            consumer_ref text,
            purpose text,
            audience_digest bytea,
            policy_epoch bigint
        )
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE
            authority_now timestamptz := pg_catalog.clock_timestamp();
            evidence_record public.delivery_evidence%ROWTYPE;
            current_policy_epoch bigint;
        BEGIN
            IF SESSION_USER <> '{_ACTION}'
               OR requested_evidence_digest IS NULL
               OR requested_service_digest IS NULL
               OR requested_consumer_digest IS NULL
               OR requested_destination_digest IS NULL
               OR requested_purpose_digest IS NULL
               OR requested_request_id IS NULL
               OR btrim(requested_request_id) = ''
               OR octet_length(requested_evidence_digest) <> 32
               OR octet_length(requested_service_digest) <> 32
               OR octet_length(requested_consumer_digest) <> 32
               OR octet_length(requested_destination_digest) <> 32
               OR octet_length(requested_purpose_digest) <> 32
            THEN
                RETURN QUERY SELECT 'generic_denied'::text, NULL::uuid,
                    NULL::uuid, NULL::uuid, NULL::bigint, NULL::text,
                    NULL::text, NULL::text, NULL::text, NULL::text,
                    NULL::bytea, NULL::bigint;
                RETURN;
            END IF;

            SELECT evidence.* INTO evidence_record
            FROM public.delivery_evidence AS evidence
            WHERE evidence.evidence_digest = requested_evidence_digest;
            IF NOT FOUND
               OR evidence_record.delivery_kind <> 'private'
               OR evidence_record.profile_ref <> 'private-delivery-evidence-v1'
               OR evidence_record.issued_at > authority_now
               OR evidence_record.expires_at <= authority_now
               OR evidence_record.request_id <> requested_request_id
               OR public.digest(pg_catalog.convert_to(
                    evidence_record.authenticated_service_ref, 'UTF8'), 'sha256')
                    <> requested_service_digest
               OR public.digest(pg_catalog.convert_to(
                    evidence_record.consumer_ref, 'UTF8'), 'sha256')
                    <> requested_consumer_digest
               OR public.digest(pg_catalog.convert_to(
                    evidence_record.destination_ref, 'UTF8'), 'sha256')
                    <> requested_destination_digest
               OR public.digest(pg_catalog.convert_to(
                    evidence_record.purpose, 'UTF8'), 'sha256')
                    <> requested_purpose_digest
               OR NOT EXISTS (
                    SELECT 1 FROM public.membership AS membership
                    WHERE membership.organization_id = evidence_record.organization_id
                      AND membership.user_id = evidence_record.user_id
                      AND membership.membership_id = evidence_record.membership_id
                      AND membership.membership_version = evidence_record.membership_version
                      AND membership.status = 'active'
                      AND membership.valid_from <= authority_now
                      AND (membership.valid_until IS NULL OR membership.valid_until > authority_now)
               )
            THEN
                RETURN QUERY SELECT 'generic_denied'::text, NULL::uuid,
                    NULL::uuid, NULL::uuid, NULL::bigint, NULL::text,
                    NULL::text, NULL::text, NULL::text, NULL::text,
                    NULL::bytea, NULL::bigint;
                RETURN;
            END IF;

            SELECT epoch.policy_epoch INTO current_policy_epoch
            FROM public.organization_policy_epoch AS epoch
            WHERE epoch.organization_id = evidence_record.organization_id;
            IF current_policy_epoch IS DISTINCT FROM evidence_record.policy_epoch THEN
                RETURN QUERY SELECT 'generic_denied'::text, NULL::uuid,
                    NULL::uuid, NULL::uuid, NULL::bigint, NULL::text,
                    NULL::text, NULL::text, NULL::text, NULL::text,
                    NULL::bytea, NULL::bigint;
                RETURN;
            END IF;

            RETURN QUERY SELECT 'bound'::text,
                evidence_record.organization_id,
                evidence_record.user_id,
                evidence_record.membership_id,
                evidence_record.membership_version,
                evidence_record.authenticated_service_ref,
                evidence_record.authentication_binding_ref,
                evidence_record.destination_ref,
                evidence_record.consumer_ref,
                evidence_record.purpose,
                pg_catalog.decode(evidence_record.audience_digest, 'hex'),
                evidence_record.policy_epoch;
        END;
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_FUNCTION}{_SIGNATURE} FROM PUBLIC")
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"ALTER FUNCTION public.{_FUNCTION}{_SIGNATURE} OWNER TO {_DEFINER}")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_FUNCTION}{_SIGNATURE} TO {_ACTION}"
    )
    op.execute("RESET ROLE")


def downgrade() -> None:
    """Remove the derived private-effect binding function."""

    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(f"DROP FUNCTION public.{_FUNCTION}{_SIGNATURE}")
    op.execute("RESET ROLE")
