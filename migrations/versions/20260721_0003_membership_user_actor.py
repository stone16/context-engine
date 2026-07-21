"""Add User/Membership identity and require a current UserActor for tenant rows.

Revision ID: 20260721_0003
Revises: 20260720_0002
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260721_0003"
down_revision: str | None = "20260720_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MIGRATOR_ROLE = "context_engine_migrator"
_RUNTIME_ROLE = "context_engine_runtime"
_WORKER_ROLE = "context_engine_worker"
_RECORD_POLICY = "organization_record_organization_isolation"
_MEMBERSHIP_RUNTIME_POLICY = "membership_current_user_actor"
_MEMBERSHIP_MIGRATOR_POLICY = "membership_migrator_administration"
_RECORD_MIGRATOR_POLICY = "organization_record_migrator_administration"
_WRITE_CONTEXT_GUARD_FUNCTION = "public.organization_record_require_write_context"

_CURRENT_MEMBERSHIP_EXPRESSION = """
organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid
AND current_setting('app.actor_kind', true) = 'user'
AND user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
AND NULLIF(current_setting('app.principal_ref', true), '') IS NOT NULL
AND membership_id = NULLIF(current_setting('app.membership_id', true), '')::uuid
AND membership_version = NULLIF(
    current_setting('app.membership_version', true), ''
)::bigint
AND NULLIF(current_setting('app.request_id', true), '') IS NOT NULL
AND NULLIF(
    current_setting('app.authentication_binding_ref', true), ''
) IS NOT NULL
AND NULLIF(current_setting('app.checked_at', true), '') IS NOT NULL
AND status = 'active'
AND valid_from <= NULLIF(
    current_setting('app.checked_at', true), ''
)::timestamptz
AND (
    valid_until IS NULL
    OR valid_until > NULLIF(
        current_setting('app.checked_at', true), ''
    )::timestamptz
)
""".strip()

_CURRENT_RECORD_MEMBERSHIP_EXPRESSION = """
organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid
AND EXISTS (
    SELECT 1
    FROM public.membership AS actor_membership
    WHERE actor_membership.organization_id = organization_record.organization_id
      AND actor_membership.organization_id = NULLIF(
          current_setting('app.organization_id', true), ''
      )::uuid
      AND current_setting('app.actor_kind', true) = 'user'
      AND actor_membership.user_id = NULLIF(
          current_setting('app.user_id', true), ''
      )::uuid
      AND NULLIF(current_setting('app.principal_ref', true), '') IS NOT NULL
      AND actor_membership.membership_id = NULLIF(
          current_setting('app.membership_id', true), ''
      )::uuid
      AND actor_membership.membership_version = NULLIF(
          current_setting('app.membership_version', true), ''
      )::bigint
      AND NULLIF(current_setting('app.request_id', true), '') IS NOT NULL
      AND NULLIF(
          current_setting('app.authentication_binding_ref', true), ''
      ) IS NOT NULL
      AND NULLIF(current_setting('app.checked_at', true), '') IS NOT NULL
      AND actor_membership.status = 'active'
      AND actor_membership.valid_from <= NULLIF(
          current_setting('app.checked_at', true), ''
      )::timestamptz
      AND (
          actor_membership.valid_until IS NULL
          OR actor_membership.valid_until > NULLIF(
              current_setting('app.checked_at', true), ''
          )::timestamptz
      )
)
""".strip()


def upgrade() -> None:
    """Create the minimum current-Membership authority and activate UserActor RLS."""

    op.create_table(
        "user_account",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id", name="pk_user_account"),
    )
    op.create_table(
        "membership",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("membership_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("membership_version", sa.BigInteger(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "membership_id",
            name="pk_membership",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "user_id",
            name="uq_membership_organization_user",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.organization_id"],
            name="fk_membership_organization",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user_account.user_id"],
            name="fk_membership_user_account",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'inactive', 'revoked')",
            name="ck_membership_status",
        ),
        sa.CheckConstraint(
            "membership_version > 0",
            name="ck_membership_version_positive",
        ),
        sa.CheckConstraint(
            "valid_until IS NULL OR valid_until > valid_from",
            name="ck_membership_valid_interval",
        ),
    )

    for table_name in ("user_account", "membership"):
        op.execute(f"REVOKE ALL ON TABLE {table_name} FROM PUBLIC")
        op.execute(f"REVOKE ALL ON TABLE {table_name} FROM {_RUNTIME_ROLE}")
        op.execute(f"REVOKE ALL ON TABLE {table_name} FROM {_WORKER_ROLE}")

    op.execute("ALTER TABLE membership ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE membership FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {_MEMBERSHIP_RUNTIME_POLICY} "
        "ON membership AS PERMISSIVE FOR SELECT "
        f"TO {_RUNTIME_ROLE} USING ({_CURRENT_MEMBERSHIP_EXPRESSION})"
    )
    op.execute(
        f"CREATE POLICY {_MEMBERSHIP_MIGRATOR_POLICY} "
        "ON membership AS PERMISSIVE FOR ALL "
        f"TO {_MIGRATOR_ROLE} USING (true) WITH CHECK (true)"
    )
    op.execute(f"GRANT SELECT ON TABLE membership TO {_RUNTIME_ROLE}")

    op.execute(f"DROP POLICY {_RECORD_POLICY} ON organization_record")
    op.execute(
        f"CREATE POLICY {_RECORD_POLICY} "
        "ON organization_record AS PERMISSIVE FOR ALL "
        f"TO {_RUNTIME_ROLE} "
        f"USING ({_CURRENT_RECORD_MEMBERSHIP_EXPRESSION}) "
        f"WITH CHECK ({_CURRENT_RECORD_MEMBERSHIP_EXPRESSION})"
    )
    op.execute(
        f"CREATE POLICY {_RECORD_MIGRATOR_POLICY} "
        "ON organization_record AS PERMISSIVE FOR ALL "
        f"TO {_MIGRATOR_ROLE} USING (true) WITH CHECK (true)"
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {_WRITE_CONTEXT_GUARD_FUNCTION}()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog
        AS $function$
        BEGIN
            IF current_user = '{_MIGRATOR_ROLE}' THEN
                RETURN NULL;
            END IF;
            IF NOT EXISTS (
                SELECT 1
                FROM public.membership AS actor_membership
                WHERE {_CURRENT_MEMBERSHIP_EXPRESSION}
            ) THEN
                RAISE EXCEPTION USING
                    ERRCODE = '42501',
                    MESSAGE =
                        'current UserActor Membership is required for tenant writes';
            END IF;
            RETURN NULL;
        END;
        $function$
        """
    )


def downgrade() -> None:
    """Restore the Organization-only Issue #8 evidence boundary."""

    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {_WRITE_CONTEXT_GUARD_FUNCTION}()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog
        AS $function$
        BEGIN
            IF NULLIF(
                current_setting('app.organization_id', true),
                ''
            ) IS NULL THEN
                RAISE EXCEPTION USING
                    ERRCODE = '42501',
                    MESSAGE = 'organization context is required for tenant writes';
            END IF;
            RETURN NULL;
        END;
        $function$
        """
    )
    op.execute(f"DROP POLICY {_RECORD_MIGRATOR_POLICY} ON organization_record")
    op.execute(f"DROP POLICY {_RECORD_POLICY} ON organization_record")
    tenant_expression = (
        "organization_id = NULLIF("
        "current_setting('app.organization_id', true), ''"
        ")::uuid"
    )
    op.execute(
        f"CREATE POLICY {_RECORD_POLICY} "
        "ON organization_record AS PERMISSIVE FOR ALL "
        f"TO {_RUNTIME_ROLE} USING ({tenant_expression}) "
        f"WITH CHECK ({tenant_expression})"
    )
    op.execute(f"REVOKE SELECT ON TABLE membership FROM {_RUNTIME_ROLE}")
    op.drop_table("membership")
    op.drop_table("user_account")
