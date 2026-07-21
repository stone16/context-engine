"""Add immutable Resource/Revision/Fragment content lineage.

Revision ID: 20260721_0004
Revises: 20260721_0003
Create Date: 2026-07-21

The Resource-to-active-Revision pointer closes a composite-FK cycle.  The
pointer constraint is therefore added after both tables exist and is deferred
until transaction commit, allowing a fixture or future publisher to insert the
Resource and its immutable Revision atomically.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260721_0004"
down_revision: str | None = "20260721_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MIGRATOR_ROLE = "context_engine_migrator"
_RUNTIME_ROLE = "context_engine_runtime"
_WORKER_ROLE = "context_engine_worker"
_CONTENT_TABLES = ("context_resource", "context_revision", "context_fragment")
_IMMUTABILITY_FUNCTION = "public.context_content_reject_mutation"
_PYTHON_ISSPACE_CODE_POINTS = (
    "U&'\\0009\\000A\\000B\\000C\\000D\\001C\\001D\\001E\\001F\\0020"
    "\\0085\\00A0\\1680\\2000\\2001\\2002\\2003\\2004\\2005\\2006"
    "\\2007\\2008\\2009\\200A\\2028\\2029\\202F\\205F\\3000'"
)

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

_ACTIVE_REVISION = """
EXISTS (
    SELECT 1
    FROM public.context_resource AS active_resource
    WHERE active_resource.organization_id = {table_name}.organization_id
      AND active_resource.resource_ref = {table_name}.resource_ref
      AND active_resource.active_revision_id = {table_name}.revision_id
      AND active_resource.tombstoned IS FALSE
)
""".strip()


def _runtime_expression(
    table_name: str,
    *,
    resource_must_be_live: bool,
    active_only: bool,
) -> str:
    expression = _CURRENT_USER_ACTOR.format(table_name=table_name)
    if resource_must_be_live:
        expression = f"{expression}\nAND {table_name}.tombstoned IS FALSE"
    if active_only:
        expression = (
            f"{expression}\nAND {_ACTIVE_REVISION.format(table_name=table_name)}"
        )
    return expression


def upgrade() -> None:
    """Create the minimal immutable active-content persistence boundary."""

    op.create_table(
        "context_resource",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_ref", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column(
            "active_revision_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "tombstoned",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "resource_ref",
            name="pk_context_resource",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.organization_id"],
            name="fk_context_resource_organization",
        ),
    )
    op.create_table(
        "context_revision",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_ref", sa.Text(), nullable=False),
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "resource_ref",
            "revision_id",
            name="pk_context_revision",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.organization_id"],
            name="fk_context_revision_organization",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "resource_ref"],
            [
                "context_resource.organization_id",
                "context_resource.resource_ref",
            ],
            name="fk_context_revision_resource_same_organization",
        ),
    )
    op.create_foreign_key(
        "fk_context_resource_active_revision_same_organization",
        "context_resource",
        "context_revision",
        ["organization_id", "resource_ref", "active_revision_id"],
        ["organization_id", "resource_ref", "revision_id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_table(
        "context_fragment",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_ref", sa.Text(), nullable=False),
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fragment_ref", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "resource_ref",
            "revision_id",
            "fragment_ref",
            name="pk_context_fragment",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "resource_ref",
            "revision_id",
            "ordinal",
            name="uq_context_fragment_revision_ordinal",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.organization_id"],
            name="fk_context_fragment_organization",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "resource_ref", "revision_id"],
            [
                "context_revision.organization_id",
                "context_revision.resource_ref",
                "context_revision.revision_id",
            ],
            name="fk_context_fragment_revision_same_organization",
        ),
        sa.CheckConstraint(
            "ordinal >= 0",
            name="ck_context_fragment_ordinal_nonnegative",
        ),
        sa.CheckConstraint(
            f"translate(content, {_PYTHON_ISSPACE_CODE_POINTS}, '') <> ''",
            name="ck_context_fragment_content_nonblank",
        ),
    )

    for table_name in _CONTENT_TABLES:
        op.execute(f"REVOKE ALL ON TABLE {table_name} FROM PUBLIC")
        op.execute(f"REVOKE ALL ON TABLE {table_name} FROM {_RUNTIME_ROLE}")
        op.execute(f"REVOKE ALL ON TABLE {table_name} FROM {_WORKER_ROLE}")
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
        runtime_expression = _runtime_expression(
            table_name,
            resource_must_be_live=table_name == "context_resource",
            active_only=table_name != "context_resource",
        )
        op.execute(
            f"CREATE POLICY {table_name}_current_user_actor "
            f"ON {table_name} AS PERMISSIVE FOR SELECT "
            f"TO {_RUNTIME_ROLE} "
            "USING ("
            f"{runtime_expression}"
            ")"
        )
        op.execute(
            f"CREATE POLICY {table_name}_migrator_administration "
            f"ON {table_name} AS PERMISSIVE FOR ALL "
            f"TO {_MIGRATOR_ROLE} USING (true) WITH CHECK (true)"
        )
        op.execute(f"GRANT SELECT ON TABLE {table_name} TO {_RUNTIME_ROLE}")

    op.execute(
        f"""
        CREATE FUNCTION {_IMMUTABILITY_FUNCTION}()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog
        AS $function$
        BEGIN
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                MESSAGE = 'ContextRevision and ContextFragment rows are immutable';
        END;
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION {_IMMUTABILITY_FUNCTION}() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER context_revision_reject_mutation "
        "BEFORE UPDATE OR DELETE ON public.context_revision "
        "FOR EACH ROW "
        f"EXECUTE FUNCTION {_IMMUTABILITY_FUNCTION}()"
    )
    op.execute(
        "CREATE TRIGGER context_fragment_reject_mutation "
        "BEFORE UPDATE OR DELETE ON public.context_fragment "
        "FOR EACH ROW "
        f"EXECUTE FUNCTION {_IMMUTABILITY_FUNCTION}()"
    )


def downgrade() -> None:
    """Remove only the Issue #13 synthetic content persistence boundary."""

    op.drop_table("context_fragment")
    op.drop_constraint(
        "fk_context_resource_active_revision_same_organization",
        "context_resource",
        type_="foreignkey",
    )
    op.drop_table("context_revision")
    op.drop_table("context_resource")
    op.execute(f"DROP FUNCTION {_IMMUTABILITY_FUNCTION}()")
