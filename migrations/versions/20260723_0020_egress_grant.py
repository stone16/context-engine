"""Persist digest-only one-shot EgressGrant state and restricted audit.

Revision ID: 20260723_0020
Revises: 20260723_0019
Create Date: 2026-07-23
"""

# ruff: noqa: E501

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260723_0020"
down_revision: str | None = "20260723_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MIGRATOR = "context_engine_migrator"
_RUNTIME = "context_engine_runtime"
_EGRESS = "context_engine_egress"
_DEFINER = "context_engine_egress_grant_definer"
_ISSUE = "context_runtime_issue_egress_grant"
_REDEEM = "context_egress_redeem_grant"
_ISSUE_SIGNATURE = "(uuid, bytea, text, text, bytea, bytea, text, bytea, bigint, text, text, text, text, text, text, text, text, text, timestamptz, timestamptz, text, text, text)"
_REDEEM_SIGNATURE = "(uuid, bytea, text, text, bytea, bytea, text, bytea, bigint, text, text, text, text, text, text, text, text, text, text)"


def upgrade() -> None:
    """Create function-only Runtime issuance and egress redemption boundaries."""

    op.create_table(
        "egress_grant",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("grant_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("digest_profile", sa.Text(), nullable=False),
        sa.Column("hop_kind", sa.Text(), nullable=False),
        sa.Column("package_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("payload_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("audience_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("policy_epoch", sa.BigInteger(), nullable=False),
        sa.Column("retention_policy_ref", sa.Text(), nullable=False),
        sa.Column("sensitivity_policy_ref", sa.Text(), nullable=False),
        sa.Column("issuer_ref", sa.Text(), nullable=False),
        sa.Column("consumer_ref", sa.Text(), nullable=False),
        sa.Column("provider_ref", sa.Text(), nullable=True),
        sa.Column("model_ref", sa.Text(), nullable=True),
        sa.Column("channel_ref", sa.Text(), nullable=True),
        sa.Column("destination_ref", sa.Text(), nullable=True),
        sa.Column("region_ref", sa.Text(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("profile_ref", sa.Text(), nullable=False),
        sa.Column("grant_profile_ref", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("organization_id", "grant_digest", name="pk_egress_grant"),
        sa.UniqueConstraint("grant_digest", name="uq_egress_grant_digest_global"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organization.organization_id"],
            name="fk_egress_grant_organization", ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "octet_length(grant_digest) = 32 AND octet_length(package_digest) = 32 AND octet_length(payload_digest) = 32 AND octet_length(audience_digest) = 32",
            name="ck_egress_grant_sha256_digests",
        ),
        sa.CheckConstraint(
            "digest_profile = 'egress-grant-locator-sha256-v1' AND grant_profile_ref = 'egress-grant-v1'",
            name="ck_egress_grant_profiles",
        ),
        sa.CheckConstraint("policy_epoch > 0", name="ck_egress_grant_positive_epoch"),
        sa.CheckConstraint(
            "expires_at > issued_at AND (consumed_at IS NULL OR (consumed_at >= issued_at AND consumed_at < expires_at))",
            name="ck_egress_grant_timestamp_order",
        ),
        sa.CheckConstraint(
            "(hop_kind = 'model' AND provider_ref IS NOT NULL AND model_ref IS NOT NULL AND channel_ref IS NULL AND destination_ref IS NULL) OR (hop_kind = 'channel' AND provider_ref IS NULL AND model_ref IS NULL AND channel_ref IS NOT NULL AND destination_ref IS NOT NULL)",
            name="ck_egress_grant_exact_hop_variant",
        ),
        sa.CheckConstraint(
            "btrim(purpose) <> '' AND btrim(retention_policy_ref) <> '' AND btrim(sensitivity_policy_ref) <> '' AND btrim(issuer_ref) <> '' AND btrim(consumer_ref) <> '' AND btrim(region_ref) <> '' AND btrim(profile_ref) <> ''",
            name="ck_egress_grant_bindings_nonblank",
        ),
    )
    op.create_table(
        "egress_audit",
        sa.Column("audit_id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("grant_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("payload_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id", "audit_id", name="pk_egress_audit"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "grant_digest"],
            ["egress_grant.organization_id", "egress_grant.grant_digest"],
            name="fk_egress_audit_exact_grant", ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "octet_length(grant_digest) = 32 AND octet_length(payload_digest) = 32",
            name="ck_egress_audit_sha256_digests",
        ),
        sa.CheckConstraint(
            "category IN ('issued', 'consumed', 'not_available')",
            name="ck_egress_audit_restricted_category",
        ),
    )

    for table_name in ("egress_grant", "egress_audit"):
        for role in ("PUBLIC", _RUNTIME, _EGRESS, _DEFINER):
            op.execute(f"REVOKE ALL ON TABLE {table_name} FROM {role}")
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table_name}_migrator_administration ON {table_name} FOR ALL TO {_MIGRATOR} USING (true) WITH CHECK (true)"
        )
        op.execute(
            f"CREATE POLICY {table_name}_definer_all ON {table_name} FOR ALL TO {_DEFINER} USING (true) WITH CHECK (true)"
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {table_name} TO {_DEFINER}")
    op.execute(f"GRANT SELECT ON TABLE organization_policy_epoch TO {_DEFINER}")
    op.execute(f"GRANT SELECT ON TABLE membership TO {_DEFINER}")
    op.execute(
        f"CREATE POLICY organization_policy_epoch_egress_definer_select ON organization_policy_epoch FOR SELECT TO {_DEFINER} USING (true)"
    )
    op.execute(
        f"CREATE POLICY membership_egress_definer_select ON membership FOR SELECT TO {_DEFINER} USING (true)"
    )

    op.execute(
        f"""
        CREATE FUNCTION public.{_ISSUE}(
            requested_organization_id uuid, requested_grant_digest bytea,
            requested_digest_profile text, requested_hop_kind text,
            requested_package_digest bytea, requested_payload_digest bytea,
            requested_purpose text, requested_audience_digest bytea,
            requested_policy_epoch bigint, requested_retention_policy_ref text,
            requested_sensitivity_policy_ref text, requested_issuer_ref text,
            requested_consumer_ref text, requested_provider_ref text,
            requested_model_ref text, requested_channel_ref text,
            requested_destination_ref text, requested_region_ref text,
            requested_issued_at timestamptz, requested_expires_at timestamptz,
            requested_profile_ref text, requested_grant_profile_ref text,
            requested_category text
        ) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE authority_now timestamptz := pg_catalog.clock_timestamp();
        BEGIN
            IF SESSION_USER <> '{_RUNTIME}'
               OR requested_digest_profile <> 'egress-grant-locator-sha256-v1'
               OR requested_grant_profile_ref <> 'egress-grant-v1'
               OR requested_category <> 'issued'
               OR requested_issued_at > authority_now
               OR requested_expires_at <= authority_now
               OR requested_organization_id <> NULLIF(current_setting('app.organization_id', true), '')::uuid
               OR requested_policy_epoch <= 0
               OR NOT EXISTS (
                    SELECT 1 FROM public.organization_policy_epoch AS epoch
                    WHERE epoch.organization_id = requested_organization_id
                      AND epoch.policy_epoch = requested_policy_epoch
               )
               OR NOT EXISTS (
                    SELECT 1 FROM public.membership AS membership
                    WHERE membership.organization_id = requested_organization_id
                      AND membership.user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
                      AND membership.membership_id = NULLIF(current_setting('app.membership_id', true), '')::uuid
                      AND membership.membership_version = NULLIF(current_setting('app.membership_version', true), '')::bigint
                      AND membership.status = 'active'
                      AND membership.valid_from <= authority_now
                      AND (membership.valid_until IS NULL OR membership.valid_until > authority_now)
               )
            THEN RETURN false; END IF;
            INSERT INTO public.egress_grant (
                organization_id, grant_digest, digest_profile, hop_kind,
                package_digest, payload_digest, purpose, audience_digest,
                policy_epoch, retention_policy_ref, sensitivity_policy_ref,
                issuer_ref, consumer_ref, provider_ref, model_ref, channel_ref,
                destination_ref, region_ref, issued_at, expires_at, profile_ref,
                grant_profile_ref
            ) VALUES (
                requested_organization_id, requested_grant_digest,
                requested_digest_profile, requested_hop_kind,
                requested_package_digest, requested_payload_digest,
                requested_purpose, requested_audience_digest,
                requested_policy_epoch, requested_retention_policy_ref,
                requested_sensitivity_policy_ref, requested_issuer_ref,
                requested_consumer_ref, requested_provider_ref,
                requested_model_ref, requested_channel_ref,
                requested_destination_ref, requested_region_ref,
                requested_issued_at, requested_expires_at,
                requested_profile_ref, requested_grant_profile_ref
            ) ON CONFLICT DO NOTHING;
            IF NOT FOUND THEN RETURN false; END IF;
            INSERT INTO public.egress_audit (
                organization_id, grant_digest, payload_digest, category, recorded_at
            ) VALUES (
                requested_organization_id, requested_grant_digest,
                requested_payload_digest, 'issued', authority_now
            );
            RETURN true;
        END;
        $function$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION public.{_REDEEM}(
            requested_organization_id uuid, requested_grant_digest bytea,
            requested_digest_profile text, requested_hop_kind text,
            requested_package_digest bytea, requested_payload_digest bytea,
            requested_purpose text, requested_audience_digest bytea,
            requested_policy_epoch bigint, requested_retention_policy_ref text,
            requested_sensitivity_policy_ref text, requested_issuer_ref text,
            requested_consumer_ref text, requested_provider_ref text,
            requested_model_ref text, requested_channel_ref text,
            requested_destination_ref text, requested_region_ref text,
            requested_profile_ref text
        ) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE authority_now timestamptz := pg_catalog.clock_timestamp();
        DECLARE stored_payload_digest bytea;
        BEGIN
            IF SESSION_USER <> '{_EGRESS}' THEN RETURN false; END IF;
            UPDATE public.egress_grant AS grant_record
            SET consumed_at = authority_now
            WHERE grant_record.organization_id = requested_organization_id
              AND grant_record.grant_digest = requested_grant_digest
              AND grant_record.digest_profile = requested_digest_profile
              AND grant_record.hop_kind = requested_hop_kind
              AND grant_record.package_digest = requested_package_digest
              AND grant_record.payload_digest = requested_payload_digest
              AND grant_record.purpose = requested_purpose
              AND grant_record.audience_digest = requested_audience_digest
              AND grant_record.policy_epoch = requested_policy_epoch
              AND grant_record.retention_policy_ref = requested_retention_policy_ref
              AND grant_record.sensitivity_policy_ref = requested_sensitivity_policy_ref
              AND grant_record.issuer_ref = requested_issuer_ref
              AND grant_record.consumer_ref = requested_consumer_ref
              AND grant_record.provider_ref IS NOT DISTINCT FROM requested_provider_ref
              AND grant_record.model_ref IS NOT DISTINCT FROM requested_model_ref
              AND grant_record.channel_ref IS NOT DISTINCT FROM requested_channel_ref
              AND grant_record.destination_ref IS NOT DISTINCT FROM requested_destination_ref
              AND grant_record.region_ref = requested_region_ref
              AND grant_record.profile_ref = requested_profile_ref
              AND grant_record.consumed_at IS NULL
              AND grant_record.issued_at <= authority_now
              AND authority_now < grant_record.expires_at
              AND EXISTS (
                    SELECT 1 FROM public.organization_policy_epoch AS epoch
                    WHERE epoch.organization_id = grant_record.organization_id
                      AND epoch.policy_epoch = grant_record.policy_epoch
              )
            RETURNING grant_record.payload_digest INTO stored_payload_digest;
            IF FOUND THEN
                INSERT INTO public.egress_audit (
                    organization_id, grant_digest, payload_digest, category, recorded_at
                ) VALUES (
                    requested_organization_id, requested_grant_digest,
                    stored_payload_digest, 'consumed', authority_now
                );
                RETURN true;
            END IF;
            IF EXISTS (
                SELECT 1 FROM public.egress_grant AS grant_record
                WHERE grant_record.organization_id = requested_organization_id
                  AND grant_record.grant_digest = requested_grant_digest
            ) THEN
                INSERT INTO public.egress_audit (
                    organization_id, grant_digest, payload_digest, category, recorded_at
                ) SELECT requested_organization_id, requested_grant_digest,
                         grant_record.payload_digest, 'not_available', authority_now
                  FROM public.egress_grant AS grant_record
                 WHERE grant_record.organization_id = requested_organization_id
                   AND grant_record.grant_digest = requested_grant_digest;
            END IF;
            RETURN false;
        END;
        $function$
        """
    )
    for function_name, signature in (
        (_ISSUE, _ISSUE_SIGNATURE), (_REDEEM, _REDEEM_SIGNATURE)
    ):
        op.execute(f"REVOKE ALL ON FUNCTION public.{function_name}{signature} FROM PUBLIC")
        op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
        op.execute(f"ALTER FUNCTION public.{function_name}{signature} OWNER TO {_DEFINER}")
        op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.{_ISSUE}{_ISSUE_SIGNATURE} TO {_RUNTIME}")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.{_REDEEM}{_REDEEM_SIGNATURE} TO {_EGRESS}")
    op.execute("RESET ROLE")


def downgrade() -> None:
    """Refuse to erase retained egress audit/state rows."""

    op.execute(
        """
        DO $block$ BEGIN
          IF EXISTS (SELECT 1 FROM public.egress_grant)
             OR EXISTS (SELECT 1 FROM public.egress_audit)
          THEN RAISE EXCEPTION USING ERRCODE = '55000',
                 MESSAGE = 'cannot downgrade with egress grant audit rows';
          END IF;
        END; $block$
        """
    )
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(f"DROP FUNCTION public.{_REDEEM}{_REDEEM_SIGNATURE}")
    op.execute(f"DROP FUNCTION public.{_ISSUE}{_ISSUE_SIGNATURE}")
    op.execute("RESET ROLE")
    op.execute("DROP POLICY organization_policy_epoch_egress_definer_select ON organization_policy_epoch")
    op.execute("DROP POLICY membership_egress_definer_select ON membership")
    op.execute(f"REVOKE SELECT ON TABLE organization_policy_epoch FROM {_DEFINER}")
    op.execute(f"REVOKE SELECT ON TABLE membership FROM {_DEFINER}")
    op.drop_table("egress_audit")
    op.drop_table("egress_grant")
