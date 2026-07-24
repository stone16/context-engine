"""Persist digest-only multi-use CitationOpenRef lineage.

Revision ID: 20260724_0024
Revises: 20260724_0023
Create Date: 2026-07-24
"""

# ruff: noqa: E501

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260724_0024"
down_revision: str | None = "20260724_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "citation_open_locator"
_MIGRATOR = "context_engine_migrator"
_RUNTIME = "context_engine_runtime"
_OPERATOR = "context_engine_security_operator"
_DEFINER = "context_engine_citation_definer"
_ISSUE = "context_runtime_issue_citation_open_ref"
_REDEEM = "context_runtime_redeem_citation_open_ref"
_DELETE_EXPIRED = "context_security_delete_expired_citation_open_lineage"
_ISSUE_SIGNATURE = "(uuid, bytea, text, text, text, text, uuid, text, timestamptz, timestamptz, text, text, timestamptz)"
_REDEEM_SIGNATURE = "(uuid, bytea, text, timestamptz)"
_DELETE_EXPIRED_SIGNATURE = "(uuid)"


def upgrade() -> None:
    """Create a function-only content-free citation locator boundary."""

    op.create_table(
        _TABLE,
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("locator_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("digest_profile", sa.Text(), nullable=False),
        sa.Column("package_ref", sa.Text(), nullable=False),
        sa.Column("evidence_ref", sa.Text(), nullable=False),
        sa.Column("resource_ref", sa.Text(), nullable=False),
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fragment_ref", sa.Text(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("profile_ref", sa.Text(), nullable=False),
        sa.Column("retention_policy_ref", sa.Text(), nullable=False),
        sa.Column("retain_until", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id", "locator_digest", name="pk_citation_open_locator"
        ),
        sa.UniqueConstraint(
            "locator_digest", name="uq_citation_open_locator_digest_global"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.organization_id"],
            name="fk_citation_open_locator_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "resource_ref", "revision_id", "fragment_ref"],
            [
                "context_fragment.organization_id",
                "context_fragment.resource_ref",
                "context_fragment.revision_id",
                "context_fragment.fragment_ref",
            ],
            name="fk_citation_open_locator_fragment_lineage",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "octet_length(locator_digest) = 32",
            name="ck_citation_open_locator_digest_sha256",
        ),
        sa.CheckConstraint(
            "digest_profile = 'citation-open-ref-sha256-v1'",
            name="ck_citation_open_locator_digest_profile",
        ),
        sa.CheckConstraint(
            "package_ref ~ '^pkg_[0-9a-f]{32}$'",
            name="ck_citation_open_locator_package_ref",
        ),
        sa.CheckConstraint(
            "evidence_ref ~ '^ev_[0-9a-f]{64}$'",
            name="ck_citation_open_locator_evidence_ref",
        ),
        sa.CheckConstraint(
            "btrim(resource_ref) <> '' AND btrim(fragment_ref) <> ''",
            name="ck_citation_open_locator_fragment_refs_nonblank",
        ),
        sa.CheckConstraint(
            "profile_ref = 'private-citation-open-v1' AND retention_policy_ref = 'citation-locator-retention-v1'",
            name="ck_citation_open_locator_profiles",
        ),
        sa.CheckConstraint(
            "expires_at > issued_at AND expires_at <= issued_at + interval '10 minutes' AND retain_until = issued_at + interval '30 days'",
            name="ck_citation_open_locator_time_windows",
        ),
    )
    for role in ("PUBLIC", _RUNTIME, _OPERATOR, _DEFINER):
        op.execute(f"REVOKE ALL ON TABLE {_TABLE} FROM {role}")
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY citation_open_locator_migrator_administration ON {_TABLE} FOR ALL TO {_MIGRATOR} USING (true) WITH CHECK (true)"
    )
    op.execute(
        f"CREATE POLICY citation_open_locator_definer_select ON {_TABLE} FOR SELECT TO {_DEFINER} USING (true)"
    )
    op.execute(
        f"CREATE POLICY citation_open_locator_definer_insert ON {_TABLE} FOR INSERT TO {_DEFINER} WITH CHECK (true)"
    )
    op.execute(
        f"CREATE POLICY citation_open_locator_definer_delete ON {_TABLE} FOR DELETE TO {_DEFINER} USING (true)"
    )
    op.execute(f"GRANT SELECT, INSERT, DELETE ON TABLE {_TABLE} TO {_DEFINER}")
    op.execute(f"GRANT SELECT ON TABLE context_resource TO {_DEFINER}")
    op.execute(f"GRANT SELECT ON TABLE context_fragment TO {_DEFINER}")
    op.execute(f"GRANT SELECT ON TABLE membership TO {_DEFINER}")
    op.execute(
        "CREATE POLICY context_resource_citation_definer_select ON context_resource FOR SELECT TO context_engine_citation_definer USING (true)"
    )
    op.execute(
        "CREATE POLICY context_fragment_citation_definer_select ON context_fragment FOR SELECT TO context_engine_citation_definer USING (true)"
    )
    op.execute(
        "CREATE POLICY membership_citation_definer_select ON membership FOR SELECT TO context_engine_citation_definer USING (true)"
    )

    op.execute(
        f"""
        CREATE FUNCTION public.{_ISSUE}(
            requested_organization_id uuid, requested_locator_digest bytea,
            requested_digest_profile text, requested_package_ref text,
            requested_evidence_ref text, requested_resource_ref text,
            requested_revision_id uuid, requested_fragment_ref text,
            requested_issued_at timestamptz, requested_expires_at timestamptz,
            requested_profile_ref text, requested_retention_policy_ref text,
            requested_retain_until timestamptz
        ) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE authority_now timestamptz := pg_catalog.clock_timestamp();
        BEGIN
            IF SESSION_USER <> '{_RUNTIME}'
               OR requested_organization_id IS DISTINCT FROM NULLIF(
                    current_setting('app.organization_id', true), ''
                  )::uuid
               OR current_setting('app.actor_kind', true) <> 'user'
               OR requested_locator_digest IS NULL
               OR octet_length(requested_locator_digest) <> 32
               OR requested_digest_profile <> 'citation-open-ref-sha256-v1'
               OR requested_profile_ref <> 'private-citation-open-v1'
               OR requested_retention_policy_ref <> 'citation-locator-retention-v1'
               OR requested_issued_at > authority_now + interval '5 seconds'
               OR requested_expires_at <= authority_now
               OR requested_expires_at > requested_issued_at + interval '10 minutes'
               OR requested_retain_until <> requested_issued_at + interval '30 days'
               OR NOT EXISTS (
                    SELECT 1 FROM public.membership AS actor_membership
                    WHERE actor_membership.organization_id = requested_organization_id
                      AND actor_membership.user_id = NULLIF(
                            current_setting('app.user_id', true), ''
                          )::uuid
                      AND actor_membership.membership_id = NULLIF(
                            current_setting('app.membership_id', true), ''
                          )::uuid
                      AND actor_membership.membership_version = NULLIF(
                            current_setting('app.membership_version', true), ''
                          )::bigint
                      AND actor_membership.status = 'active'
                      AND actor_membership.valid_from <= authority_now
                      AND (actor_membership.valid_until IS NULL OR actor_membership.valid_until > authority_now)
               )
               OR NOT EXISTS (
                    SELECT 1 FROM public.context_resource AS resource
                    WHERE resource.organization_id = requested_organization_id
                      AND resource.resource_ref = requested_resource_ref
                      AND resource.active_revision_id = requested_revision_id
                      AND resource.tombstoned IS FALSE
               )
               OR NOT EXISTS (
                    SELECT 1 FROM public.context_fragment AS fragment
                    WHERE fragment.organization_id = requested_organization_id
                      AND fragment.resource_ref = requested_resource_ref
                      AND fragment.revision_id = requested_revision_id
                      AND fragment.fragment_ref = requested_fragment_ref
               )
            THEN RETURN false; END IF;
            INSERT INTO public.{_TABLE} (
                organization_id, locator_digest, digest_profile, package_ref,
                evidence_ref, resource_ref, revision_id, fragment_ref,
                issued_at, expires_at, profile_ref, retention_policy_ref,
                retain_until
            ) VALUES (
                requested_organization_id, requested_locator_digest,
                requested_digest_profile, requested_package_ref,
                requested_evidence_ref, requested_resource_ref,
                requested_revision_id, requested_fragment_ref,
                requested_issued_at, requested_expires_at,
                requested_profile_ref, requested_retention_policy_ref,
                requested_retain_until
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
        DECLARE deleted_count bigint;
        BEGIN
            IF SESSION_USER <> '{_OPERATOR}'
               OR requested_organization_id IS NULL
            THEN RETURN 0; END IF;
            DELETE FROM public.{_TABLE} AS locator
            WHERE locator.organization_id = requested_organization_id
              AND locator.retain_until <= pg_catalog.clock_timestamp();
            GET DIAGNOSTICS deleted_count = ROW_COUNT;
            RETURN deleted_count;
        END;
        $function$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION public.{_REDEEM}(
            requested_organization_id uuid, requested_locator_digest bytea,
            requested_digest_profile text, requested_opened_at timestamptz
        ) RETURNS TABLE (
            source_ref text, resource_ref text, revision_id uuid,
            fragment_ref text, package_ref text, evidence_ref text
        )
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE authority_now timestamptz := pg_catalog.clock_timestamp();
        BEGIN
            IF SESSION_USER <> '{_RUNTIME}'
               OR requested_organization_id IS DISTINCT FROM NULLIF(
                    current_setting('app.organization_id', true), ''
                  )::uuid
               OR current_setting('app.actor_kind', true) <> 'user'
               OR requested_locator_digest IS NULL
               OR octet_length(requested_locator_digest) <> 32
               OR requested_digest_profile <> 'citation-open-ref-sha256-v1'
               OR requested_opened_at IS NULL
               OR requested_opened_at > authority_now + interval '5 seconds'
               OR NOT EXISTS (
                    SELECT 1 FROM public.membership AS actor_membership
                    WHERE actor_membership.organization_id = requested_organization_id
                      AND actor_membership.user_id = NULLIF(
                            current_setting('app.user_id', true), ''
                          )::uuid
                      AND actor_membership.membership_id = NULLIF(
                            current_setting('app.membership_id', true), ''
                          )::uuid
                      AND actor_membership.membership_version = NULLIF(
                            current_setting('app.membership_version', true), ''
                          )::bigint
                      AND actor_membership.status = 'active'
                      AND actor_membership.valid_from <= authority_now
                      AND (actor_membership.valid_until IS NULL OR actor_membership.valid_until > authority_now)
               )
            THEN RETURN; END IF;
            RETURN QUERY
            SELECT resource.source_ref, locator.resource_ref,
                   locator.revision_id, locator.fragment_ref,
                   locator.package_ref, locator.evidence_ref
            FROM public.{_TABLE} AS locator
            JOIN public.context_resource AS resource
              ON resource.organization_id = locator.organization_id
             AND resource.resource_ref = locator.resource_ref
             AND resource.active_revision_id = locator.revision_id
             AND resource.tombstoned IS FALSE
            JOIN public.context_fragment AS fragment
              ON fragment.organization_id = locator.organization_id
             AND fragment.resource_ref = locator.resource_ref
             AND fragment.revision_id = locator.revision_id
             AND fragment.fragment_ref = locator.fragment_ref
            WHERE locator.organization_id = requested_organization_id
              AND locator.locator_digest = requested_locator_digest
              AND locator.digest_profile = requested_digest_profile
              AND locator.issued_at <= authority_now
              AND authority_now < locator.expires_at;
        END;
        $function$
        """
    )
    for function_name, signature in (
        (_ISSUE, _ISSUE_SIGNATURE),
        (_DELETE_EXPIRED, _DELETE_EXPIRED_SIGNATURE),
        (_REDEEM, _REDEEM_SIGNATURE),
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
        f"GRANT EXECUTE ON FUNCTION public.{_ISSUE}{_ISSUE_SIGNATURE} TO {_RUNTIME}"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_REDEEM}{_REDEEM_SIGNATURE} TO {_RUNTIME}"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_DELETE_EXPIRED}{_DELETE_EXPIRED_SIGNATURE} TO {_OPERATOR}"
    )
    op.execute("RESET ROLE")


def downgrade() -> None:
    """Remove the carrier only when no locator lineage remains."""

    op.execute(
        f"""
        DO $block$ BEGIN
          IF EXISTS (SELECT 1 FROM public.{_TABLE})
          THEN RAISE EXCEPTION USING ERRCODE = '55000',
                 MESSAGE = 'cannot downgrade with citation locator rows';
          END IF;
        END; $block$
        """
    )
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(f"DROP FUNCTION public.{_REDEEM}{_REDEEM_SIGNATURE}")
    op.execute(f"DROP FUNCTION public.{_DELETE_EXPIRED}{_DELETE_EXPIRED_SIGNATURE}")
    op.execute(f"DROP FUNCTION public.{_ISSUE}{_ISSUE_SIGNATURE}")
    op.execute("RESET ROLE")
    op.execute("DROP POLICY context_fragment_citation_definer_select ON context_fragment")
    op.execute("DROP POLICY context_resource_citation_definer_select ON context_resource")
    op.execute("DROP POLICY membership_citation_definer_select ON membership")
    op.execute(f"REVOKE SELECT ON TABLE membership FROM {_DEFINER}")
    op.execute(f"REVOKE SELECT ON TABLE context_fragment FROM {_DEFINER}")
    op.execute(f"REVOKE SELECT ON TABLE context_resource FROM {_DEFINER}")
    op.drop_table(_TABLE)
