"""Add Organization Policy Epoch and atomic Resource access revocation.

Revision ID: 20260721_0005
Revises: 20260721_0004
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260721_0005"
down_revision: str | None = "20260721_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MIGRATOR_ROLE = "context_engine_migrator"
_CONTROL_ROLE = "context_engine_control"
_ACCESS_POLICY_DEFINER_ROLE = "context_engine_access_policy_definer"
_RUNTIME_ROLE = "context_engine_runtime"
_WORKER_ROLE = "context_engine_worker"
_MAX_SIGNED_BIGINT = 2**63 - 1
_EPOCH_TABLE = "organization_policy_epoch"
_ACCESS_TABLE = "resource_access_policy"
_INITIALIZE_EPOCH_FUNCTION = "public.organization_initialize_policy_epoch"
_INITIALIZE_EPOCH_TRIGGER = "organization_initialize_policy_epoch"
_REVOKE_ACCESS_FUNCTION = "public.context_control_revoke_resource_access"

_CURRENT_USER_ACTOR = """
{table_name}.organization_id = NULLIF(
    current_setting('app.organization_id', true), ''
)::uuid
AND EXISTS (
    SELECT 1
    FROM public.membership AS actor_membership
    WHERE actor_membership.organization_id = {table_name}.organization_id
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


def _runtime_expression(table_name: str) -> str:
    expression = _CURRENT_USER_ACTOR.format(table_name=table_name)
    if table_name == _ACCESS_TABLE:
        expression = (
            f"{expression}\n"
            "AND resource_access_policy.principal_ref = "
            "current_setting('app.principal_ref', true)\n"
            "AND resource_access_policy.access_state = 'allowed'"
        )
    return expression


def _control_definer_expression(table_name: str) -> str:
    return (
        f"{table_name}.organization_id = NULLIF("
        "current_setting('app.organization_id', true), ''"
        ")::uuid"
    )


def upgrade() -> None:
    """Create the Organization-level revocation linearization boundary."""

    op.create_table(
        _EPOCH_TABLE,
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "policy_epoch",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.PrimaryKeyConstraint(
            "organization_id",
            name="pk_organization_policy_epoch",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.organization_id"],
            name="fk_organization_policy_epoch_organization",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            f"policy_epoch BETWEEN 1 AND {_MAX_SIGNED_BIGINT}",
            name="ck_organization_policy_epoch_positive_signed_bigint",
        ),
    )
    op.execute(
        "INSERT INTO organization_policy_epoch (organization_id, policy_epoch) "
        "SELECT organization_id, 1 FROM organization"
    )
    op.execute(
        f"""
        CREATE FUNCTION {_INITIALIZE_EPOCH_FUNCTION}()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog
        AS $function$
        BEGIN
            INSERT INTO public.organization_policy_epoch (
                organization_id,
                policy_epoch
            ) VALUES (NEW.organization_id, 1);
            RETURN NULL;
        END;
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION {_INITIALIZE_EPOCH_FUNCTION}() FROM PUBLIC")
    op.execute(
        f"CREATE TRIGGER {_INITIALIZE_EPOCH_TRIGGER} "
        "AFTER INSERT ON public.organization FOR EACH ROW "
        f"EXECUTE FUNCTION {_INITIALIZE_EPOCH_FUNCTION}()"
    )

    op.create_table(
        _ACCESS_TABLE,
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_ref", sa.Text(), nullable=False),
        sa.Column("principal_ref", sa.Text(), nullable=False),
        sa.Column("access_version", sa.BigInteger(), nullable=False),
        sa.Column("access_state", sa.Text(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "resource_ref",
            "principal_ref",
            name="pk_resource_access_policy",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.organization_id"],
            name="fk_resource_access_policy_organization",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "resource_ref"],
            ["context_resource.organization_id", "context_resource.resource_ref"],
            name="fk_resource_access_policy_resource_same_organization",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            f"access_version BETWEEN 1 AND {_MAX_SIGNED_BIGINT}",
            name="ck_resource_access_policy_version_positive_signed_bigint",
        ),
        sa.CheckConstraint(
            "btrim(resource_ref) <> ''",
            name="ck_resource_access_policy_resource_ref_nonblank",
        ),
        sa.CheckConstraint(
            "btrim(principal_ref) <> ''",
            name="ck_resource_access_policy_principal_ref_nonblank",
        ),
        sa.CheckConstraint(
            "access_state IN ('allowed', 'revoked')",
            name="ck_resource_access_policy_state",
        ),
        sa.CheckConstraint(
            "(access_state = 'allowed' AND revoked_at IS NULL) "
            "OR (access_state = 'revoked' AND revoked_at IS NOT NULL)",
            name="ck_resource_access_policy_state_timestamp",
        ),
    )

    for table_name in (_EPOCH_TABLE, _ACCESS_TABLE):
        op.execute(f"REVOKE ALL ON TABLE {table_name} FROM PUBLIC")
        op.execute(f"REVOKE ALL ON TABLE {table_name} FROM {_CONTROL_ROLE}")
        op.execute(f"REVOKE ALL ON TABLE {table_name} FROM {_RUNTIME_ROLE}")
        op.execute(f"REVOKE ALL ON TABLE {table_name} FROM {_WORKER_ROLE}")
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table_name}_current_user_actor "
            f"ON {table_name} AS PERMISSIVE FOR SELECT TO {_RUNTIME_ROLE} "
            f"USING ({_runtime_expression(table_name)})"
        )
        op.execute(
            f"CREATE POLICY {table_name}_migrator_administration "
            f"ON {table_name} AS PERMISSIVE FOR ALL TO {_MIGRATOR_ROLE} "
            "USING (true) WITH CHECK (true)"
        )
        definer_expression = _control_definer_expression(table_name)
        op.execute(
            f"CREATE POLICY {table_name}_access_policy_definer_select "
            f"ON {table_name} AS PERMISSIVE FOR SELECT "
            f"TO {_ACCESS_POLICY_DEFINER_ROLE} "
            f"USING ({definer_expression})"
        )
        op.execute(
            f"CREATE POLICY {table_name}_access_policy_definer_update "
            f"ON {table_name} AS PERMISSIVE FOR UPDATE "
            f"TO {_ACCESS_POLICY_DEFINER_ROLE} "
            f"USING ({definer_expression}) "
            f"WITH CHECK ({definer_expression})"
        )
        op.execute(f"GRANT SELECT ON TABLE {table_name} TO {_RUNTIME_ROLE}")
        op.execute(
            f"GRANT SELECT, UPDATE ON TABLE {table_name} "
            f"TO {_ACCESS_POLICY_DEFINER_ROLE}"
        )

    op.execute(
        f"""
        CREATE FUNCTION {_REVOKE_ACCESS_FUNCTION}(
            requested_organization_id uuid,
            requested_resource_ref text,
            requested_principal_ref text,
            expected_access_version bigint
        ) RETURNS bigint
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            current_epoch bigint;
            updated_access_version bigint;
            next_epoch bigint;
        BEGIN
            IF session_user <> '{_CONTROL_ROLE}'
               OR NULLIF(
                    current_setting('app.organization_id', true), ''
               )::uuid IS DISTINCT FROM requested_organization_id THEN
                RAISE EXCEPTION USING
                    ERRCODE = '42501',
                    MESSAGE = 'access change was not accepted';
            END IF;

            SELECT epoch.policy_epoch
            INTO current_epoch
            FROM public.organization_policy_epoch AS epoch
            WHERE epoch.organization_id = requested_organization_id
            FOR UPDATE;

            IF current_epoch IS NULL
               OR current_epoch >= {_MAX_SIGNED_BIGINT} THEN
                RAISE EXCEPTION USING
                    ERRCODE = 'P0001',
                    MESSAGE = 'access change was not accepted';
            END IF;

            UPDATE public.resource_access_policy AS access
            SET access_state = 'revoked',
                access_version = access.access_version + 1,
                revoked_at = statement_timestamp()
            WHERE access.organization_id = requested_organization_id
              AND access.resource_ref = requested_resource_ref
              AND access.principal_ref = requested_principal_ref
              AND access.access_state = 'allowed'
              AND access.access_version = expected_access_version
              AND access.access_version < {_MAX_SIGNED_BIGINT}
            RETURNING access.access_version INTO updated_access_version;

            IF updated_access_version IS NULL THEN
                RAISE EXCEPTION USING
                    ERRCODE = 'P0001',
                    MESSAGE = 'access change was not accepted';
            END IF;

            UPDATE public.organization_policy_epoch AS epoch
            SET policy_epoch = epoch.policy_epoch + 1
            WHERE epoch.organization_id = requested_organization_id
              AND epoch.policy_epoch = current_epoch
            RETURNING epoch.policy_epoch INTO next_epoch;

            IF next_epoch IS NULL THEN
                RAISE EXCEPTION USING
                    ERRCODE = '40001',
                    MESSAGE = 'access change was not accepted';
            END IF;
            RETURN next_epoch;
        END;
        $function$
        """
    )
    signature = "(uuid, text, text, bigint)"
    op.execute(
        f"REVOKE ALL ON FUNCTION {_REVOKE_ACCESS_FUNCTION}{signature} FROM PUBLIC"
    )
    for role in (_RUNTIME_ROLE, _WORKER_ROLE):
        op.execute(
            f"REVOKE ALL ON FUNCTION {_REVOKE_ACCESS_FUNCTION}{signature} "
            f"FROM {role}"
        )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION {_REVOKE_ACCESS_FUNCTION}{signature} "
        f"TO {_CONTROL_ROLE}"
    )
    op.execute(
        f"GRANT CREATE ON SCHEMA public TO {_ACCESS_POLICY_DEFINER_ROLE}"
    )
    op.execute(
        f"ALTER FUNCTION {_REVOKE_ACCESS_FUNCTION}{signature} "
        f"OWNER TO {_ACCESS_POLICY_DEFINER_ROLE}"
    )
    op.execute(
        f"REVOKE CREATE ON SCHEMA public FROM {_ACCESS_POLICY_DEFINER_ROLE}"
    )


def downgrade() -> None:
    """Remove only the Issue #15 Organization Policy Epoch boundary."""

    op.execute(f"SET LOCAL ROLE {_ACCESS_POLICY_DEFINER_ROLE}")
    op.execute(
        f"DROP FUNCTION {_REVOKE_ACCESS_FUNCTION}(uuid, text, text, bigint)"
    )
    op.execute("RESET ROLE")
    op.drop_table(_ACCESS_TABLE)
    op.execute(
        f"DROP TRIGGER {_INITIALIZE_EPOCH_TRIGGER} ON public.organization"
    )
    op.execute(f"DROP FUNCTION {_INITIALIZE_EPOCH_FUNCTION}()")
    op.drop_table(_EPOCH_TABLE)
