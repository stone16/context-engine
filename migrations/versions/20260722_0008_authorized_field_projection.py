"""Add Membership-bound structured field projection authority.

Revision ID: 20260722_0008
Revises: 20260722_0007
Create Date: 2026-07-22

Legacy Fragment bodies remain the default storage shape, but they become
visible to Runtime only through an explicit ``body`` right.  Structured field
values live in a separate immutable table whose forced-RLS policy requires the
exact current Membership version and matching Resource/field right before a
value can cross the database boundary.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260722_0008"
down_revision: str | None = "20260722_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MIGRATOR_ROLE = "context_engine_migrator"
_CONTROL_ROLE = "context_engine_control"
_ACCESS_POLICY_DEFINER_ROLE = "context_engine_access_policy_definer"
_WORKER_LEASE_DEFINER_ROLE = "context_engine_worker_lease_definer"
_CONTEXT_RUN_READER_DEFINER_ROLE = "context_engine_context_run_reader_definer"
_RUNTIME_ROLE = "context_engine_runtime"
_WORKER_ROLE = "context_engine_worker"
_SECURITY_OPERATOR_ROLE = "context_engine_security_operator"
_FRAGMENT_TABLE = "context_fragment"
_FIELD_TABLE = "context_fragment_field"
_RIGHT_TABLE = "membership_resource_field_right"
_IMMUTABILITY_FUNCTION = "public.context_content_reject_mutation"
_FIELD_PARENT_GUARD_FUNCTION = "public.context_fragment_field_require_fields_parent"
_FIELD_PARENT_GUARD_TRIGGER = "context_fragment_field_fields_parent_guard"
_RIGHT_MUTATION_LOCK_FUNCTION = (
    "public.membership_resource_field_right_lock_mutation"
)
_RIGHT_MUTATION_LOCK_TRIGGER = "membership_resource_field_right_mutation_lock"
_PYTHON_ISSPACE_CODE_POINTS = (
    "U&'\\0009\\000A\\000B\\000C\\000D\\001C\\001D\\001E\\001F\\0020"
    "\\0085\\00A0\\1680\\2000\\2001\\2002\\2003\\2004\\2005\\2006"
    "\\2007\\2008\\2009\\200A\\2028\\2029\\202F\\205F\\3000'"
)
_FIELD_REF_EXPRESSION = "field_ref ~ '^[a-z][a-z0-9_]{0,63}$'"
_MAX_PROJECTED_FIELDS = 64
_PACKAGE_DIGEST_PROFILE_CONSTRAINT = "ck_context_run_package_digest_profile"

_CURRENT_USER_ACTOR = """
{table_name}.organization_id = NULLIF(
    current_setting('app.organization_id', true), ''
)::uuid
AND current_setting('app.actor_kind', true) = 'user'
AND NULLIF(current_setting('app.principal_ref', true), '') IS NOT NULL
AND NULLIF(current_setting('app.request_id', true), '') IS NOT NULL
AND NULLIF(
    current_setting('app.authentication_binding_ref', true), ''
) IS NOT NULL
AND NULLIF(current_setting('app.checked_at', true), '') IS NOT NULL
AND EXISTS (
    SELECT 1
    FROM public.membership AS actor_membership
    WHERE actor_membership.organization_id = {table_name}.organization_id
      AND actor_membership.organization_id = NULLIF(
          current_setting('app.organization_id', true), ''
      )::uuid
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


def _exact_right(table_name: str, field_expression: str) -> str:
    return f"""
EXISTS (
    SELECT 1
    FROM public.{_RIGHT_TABLE} AS field_right
    WHERE field_right.organization_id = {table_name}.organization_id
      AND field_right.membership_id = NULLIF(
          current_setting('app.membership_id', true), ''
      )::uuid
      AND field_right.membership_version = NULLIF(
          current_setting('app.membership_version', true), ''
      )::bigint
      AND field_right.resource_ref = {table_name}.resource_ref
      AND field_right.field_ref = {field_expression}
)
""".strip()


def _current_resource_access(table_name: str) -> str:
    return f"""
EXISTS (
    SELECT 1
    FROM public.resource_access_policy AS current_access
    WHERE current_access.organization_id = {table_name}.organization_id
      AND current_access.resource_ref = {table_name}.resource_ref
      AND current_access.principal_ref = current_setting(
          'app.principal_ref', true
      )
      AND current_access.access_state = 'allowed'
)
""".strip()


def _live_resource(table_name: str) -> str:
    return f"""
EXISTS (
    SELECT 1
    FROM public.context_resource AS live_resource
    WHERE live_resource.organization_id = {table_name}.organization_id
      AND live_resource.resource_ref = {table_name}.resource_ref
      AND live_resource.tombstoned IS FALSE
)
""".strip()


def _fragment_runtime_expression() -> str:
    actor = _CURRENT_USER_ACTOR.format(table_name=_FRAGMENT_TABLE)
    active = _ACTIVE_REVISION.format(table_name=_FRAGMENT_TABLE)
    current_resource_access = _current_resource_access(_FRAGMENT_TABLE)
    body_right = _exact_right(_FRAGMENT_TABLE, "'body'")
    return (
        f"{actor}\nAND {active}\nAND {current_resource_access}\n"
        "AND (\n"
        "    context_fragment.projection_kind = 'fields'\n"
        "    OR (\n"
        "        context_fragment.projection_kind = 'body'\n"
        f"        AND {body_right}\n"
        "    )\n"
        ")"
    )


def _field_runtime_expression() -> str:
    actor = _CURRENT_USER_ACTOR.format(table_name=_FIELD_TABLE)
    active = _ACTIVE_REVISION.format(table_name=_FIELD_TABLE)
    right = _exact_right(_FIELD_TABLE, "context_fragment_field.field_ref")
    current_resource_access = _current_resource_access(_FIELD_TABLE)
    return f"{actor}\nAND {active}\nAND {right}\nAND {current_resource_access}"


def _right_runtime_expression() -> str:
    actor = _CURRENT_USER_ACTOR.format(table_name=_RIGHT_TABLE)
    live_resource = _live_resource(_RIGHT_TABLE)
    current_resource_access = _current_resource_access(_RIGHT_TABLE)
    return f"""
{actor}
AND membership_resource_field_right.membership_id = NULLIF(
    current_setting('app.membership_id', true), ''
)::uuid
AND membership_resource_field_right.membership_version = NULLIF(
    current_setting('app.membership_version', true), ''
)::bigint
AND {live_resource}
AND {current_resource_access}
""".strip()


def _legacy_fragment_runtime_expression() -> str:
    actor = _CURRENT_USER_ACTOR.format(table_name=_FRAGMENT_TABLE)
    active = _ACTIVE_REVISION.format(table_name=_FRAGMENT_TABLE)
    return f"{actor}\nAND {active}"


def upgrade() -> None:
    """Create the fail-closed structured projection persistence boundary."""

    op.create_unique_constraint(
        "uq_membership_organization_id_version",
        "membership",
        ["organization_id", "membership_id", "membership_version"],
    )
    op.drop_constraint(
        _PACKAGE_DIGEST_PROFILE_CONSTRAINT,
        "context_run",
        type_="check",
    )
    op.create_check_constraint(
        _PACKAGE_DIGEST_PROFILE_CONSTRAINT,
        "context_run",
        "package_digest_profile IN ("
        "'context-package-canonical-json-v1', "
        "'context-package-canonical-json-v2'"
        ")",
    )

    op.add_column(
        _FRAGMENT_TABLE,
        sa.Column(
            "projection_kind",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'body'"),
        ),
    )
    op.alter_column(_FRAGMENT_TABLE, "content", existing_type=sa.Text(), nullable=True)
    op.drop_constraint(
        "ck_context_fragment_content_nonblank",
        _FRAGMENT_TABLE,
        type_="check",
    )
    op.create_check_constraint(
        "ck_context_fragment_projection_kind",
        _FRAGMENT_TABLE,
        "projection_kind IN ('body', 'fields')",
    )
    op.create_check_constraint(
        "ck_context_fragment_projection_payload",
        _FRAGMENT_TABLE,
        "(projection_kind = 'body' AND content IS NOT NULL AND "
        f"translate(content, {_PYTHON_ISSPACE_CODE_POINTS}, '') <> '') OR "
        "(projection_kind = 'fields' AND content IS NULL)",
    )

    op.create_table(
        _FIELD_TABLE,
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_ref", sa.Text(), nullable=False),
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fragment_ref", sa.Text(), nullable=False),
        sa.Column("field_ref", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("field_value", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "resource_ref",
            "revision_id",
            "fragment_ref",
            "field_ref",
            name="pk_context_fragment_field",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "resource_ref",
            "revision_id",
            "fragment_ref",
            "ordinal",
            name="uq_context_fragment_field_parent_ordinal",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "resource_ref", "revision_id", "fragment_ref"],
            [
                "context_fragment.organization_id",
                "context_fragment.resource_ref",
                "context_fragment.revision_id",
                "context_fragment.fragment_ref",
            ],
            name="fk_context_fragment_field_parent_same_organization",
        ),
        sa.CheckConstraint(
            f"ordinal BETWEEN 0 AND {_MAX_PROJECTED_FIELDS - 1}",
            name="ck_context_fragment_field_ordinal_bounded",
        ),
        sa.CheckConstraint(
            f"{_FIELD_REF_EXPRESSION} AND field_ref <> 'body'",
            name="ck_context_fragment_field_ref",
        ),
        sa.CheckConstraint(
            f"translate(field_value, {_PYTHON_ISSPACE_CODE_POINTS}, '') <> ''",
            name="ck_context_fragment_field_value_nonblank",
        ),
    )
    op.create_table(
        _RIGHT_TABLE,
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("membership_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("membership_version", sa.BigInteger(), nullable=False),
        sa.Column("resource_ref", sa.Text(), nullable=False),
        sa.Column("field_ref", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "membership_id",
            "membership_version",
            "resource_ref",
            "field_ref",
            name="pk_membership_resource_field_right",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "membership_id", "membership_version"],
            [
                "membership.organization_id",
                "membership.membership_id",
                "membership.membership_version",
            ],
            name="fk_membership_field_right_membership_version",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "resource_ref"],
            ["context_resource.organization_id", "context_resource.resource_ref"],
            name="fk_membership_field_right_resource_same_organization",
        ),
        sa.CheckConstraint(
            "membership_version > 0",
            name="ck_membership_resource_field_right_version_positive",
        ),
        sa.CheckConstraint(
            _FIELD_REF_EXPRESSION,
            name="ck_membership_resource_field_right_field_ref",
        ),
    )

    op.execute(
        f"""
        CREATE FUNCTION {_RIGHT_MUTATION_LOCK_FUNCTION}()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            old_organization_id uuid;
            new_organization_id uuid;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                old_organization_id := OLD.organization_id;
            END IF;
            IF TG_OP <> 'DELETE' THEN
                new_organization_id := NEW.organization_id;
            END IF;

            IF old_organization_id IS NOT NULL
               AND new_organization_id IS NOT NULL
               AND old_organization_id <> new_organization_id THEN
                PERFORM pg_catalog.pg_advisory_xact_lock(
                    pg_catalog.hashtextextended(
                        'context-engine.field-rights:' ||
                        LEAST(
                            old_organization_id::text,
                            new_organization_id::text
                        ),
                        0
                    )
                );
                PERFORM pg_catalog.pg_advisory_xact_lock(
                    pg_catalog.hashtextextended(
                        'context-engine.field-rights:' ||
                        GREATEST(
                            old_organization_id::text,
                            new_organization_id::text
                        ),
                        0
                    )
                );
            ELSE
                PERFORM pg_catalog.pg_advisory_xact_lock(
                    pg_catalog.hashtextextended(
                        'context-engine.field-rights:' ||
                        COALESCE(
                            new_organization_id,
                            old_organization_id
                        )::text,
                        0
                    )
                );
            END IF;

            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $function$
        """
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION {_RIGHT_MUTATION_LOCK_FUNCTION}() FROM PUBLIC"
    )
    for role_name in (
        _CONTROL_ROLE,
        _ACCESS_POLICY_DEFINER_ROLE,
        _WORKER_LEASE_DEFINER_ROLE,
        _CONTEXT_RUN_READER_DEFINER_ROLE,
        _RUNTIME_ROLE,
        _WORKER_ROLE,
        _SECURITY_OPERATOR_ROLE,
    ):
        op.execute(
            f"REVOKE ALL ON FUNCTION {_RIGHT_MUTATION_LOCK_FUNCTION}() "
            f"FROM {role_name}"
        )
    op.execute(
        f"CREATE TRIGGER {_RIGHT_MUTATION_LOCK_TRIGGER} "
        f"BEFORE INSERT OR UPDATE OR DELETE ON public.{_RIGHT_TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION {_RIGHT_MUTATION_LOCK_FUNCTION}()"
    )

    op.execute(
        f"""
        CREATE FUNCTION {_FIELD_PARENT_GUARD_FUNCTION}()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog
        AS $function$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM public.context_fragment AS parent_fragment
                WHERE parent_fragment.organization_id = NEW.organization_id
                  AND parent_fragment.resource_ref = NEW.resource_ref
                  AND parent_fragment.revision_id = NEW.revision_id
                  AND parent_fragment.fragment_ref = NEW.fragment_ref
                  AND parent_fragment.projection_kind = 'fields'
            ) THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'ContextFragmentField requires a fields Fragment parent',
                    CONSTRAINT = 'ck_context_fragment_field_fields_parent';
            END IF;
            RETURN NEW;
        END;
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION {_FIELD_PARENT_GUARD_FUNCTION}() FROM PUBLIC")
    for role_name in (
        _CONTROL_ROLE,
        _ACCESS_POLICY_DEFINER_ROLE,
        _WORKER_LEASE_DEFINER_ROLE,
        _CONTEXT_RUN_READER_DEFINER_ROLE,
        _RUNTIME_ROLE,
        _WORKER_ROLE,
        _SECURITY_OPERATOR_ROLE,
    ):
        op.execute(
            f"REVOKE ALL ON FUNCTION {_FIELD_PARENT_GUARD_FUNCTION}() FROM {role_name}"
        )
    op.execute(
        f"CREATE TRIGGER {_FIELD_PARENT_GUARD_TRIGGER} "
        f"BEFORE INSERT ON public.{_FIELD_TABLE} FOR EACH ROW "
        f"EXECUTE FUNCTION {_FIELD_PARENT_GUARD_FUNCTION}()"
    )
    op.execute(
        "CREATE TRIGGER context_fragment_field_reject_mutation "
        f"BEFORE UPDATE OR DELETE ON public.{_FIELD_TABLE} FOR EACH ROW "
        f"EXECUTE FUNCTION {_IMMUTABILITY_FUNCTION}()"
    )

    for table_name in (_FIELD_TABLE, _RIGHT_TABLE):
        op.execute(f"REVOKE ALL ON TABLE {table_name} FROM PUBLIC")
        for role_name in (
            _CONTROL_ROLE,
            _ACCESS_POLICY_DEFINER_ROLE,
            _WORKER_LEASE_DEFINER_ROLE,
            _CONTEXT_RUN_READER_DEFINER_ROLE,
            _RUNTIME_ROLE,
            _WORKER_ROLE,
            _SECURITY_OPERATOR_ROLE,
        ):
            op.execute(f"REVOKE ALL ON TABLE {table_name} FROM {role_name}")
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
        runtime_expression = (
            _field_runtime_expression()
            if table_name == _FIELD_TABLE
            else _right_runtime_expression()
        )
        op.execute(
            f"CREATE POLICY {table_name}_current_user_actor "
            f"ON {table_name} AS PERMISSIVE FOR SELECT TO {_RUNTIME_ROLE} "
            f"USING ({runtime_expression})"
        )
        op.execute(
            f"CREATE POLICY {table_name}_migrator_administration "
            f"ON {table_name} AS PERMISSIVE FOR ALL TO {_MIGRATOR_ROLE} "
            "USING (true) WITH CHECK (true)"
        )
        op.execute(f"GRANT SELECT ON TABLE {table_name} TO {_RUNTIME_ROLE}")

    op.execute("DROP POLICY context_fragment_current_user_actor ON context_fragment")
    op.execute(
        "CREATE POLICY context_fragment_current_user_actor "
        "ON context_fragment AS PERMISSIVE FOR SELECT "
        f"TO {_RUNTIME_ROLE} USING ({_fragment_runtime_expression()})"
    )


def downgrade() -> None:
    """Remove Issue #48 only when doing so cannot broaden stored content."""

    connection = op.get_bind()
    # Close the check-to-DDL race: no Fragment insert may commit after the
    # emptiness decision and before the legacy policy/shape is restored.
    connection.execute(
        sa.text(
            "LOCK TABLE public.context_fragment IN ACCESS EXCLUSIVE MODE"
        )
    )
    if connection.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM public.context_fragment)")
    ).scalar_one():
        raise RuntimeError(
            "Issue #48 downgrade requires an empty content schema; "
            "use a forward fix for stored Fragment data"
        )

    op.execute("DROP POLICY context_fragment_current_user_actor ON context_fragment")
    op.execute(
        "CREATE POLICY context_fragment_current_user_actor "
        "ON context_fragment AS PERMISSIVE FOR SELECT "
        f"TO {_RUNTIME_ROLE} USING ({_legacy_fragment_runtime_expression()})"
    )
    op.drop_table(_FIELD_TABLE)
    op.drop_table(_RIGHT_TABLE)
    op.execute(f"DROP FUNCTION IF EXISTS {_RIGHT_MUTATION_LOCK_FUNCTION}()")
    op.execute(f"DROP FUNCTION {_FIELD_PARENT_GUARD_FUNCTION}()")
    op.drop_constraint(
        "ck_context_fragment_projection_payload",
        _FRAGMENT_TABLE,
        type_="check",
    )
    op.drop_constraint(
        "ck_context_fragment_projection_kind",
        _FRAGMENT_TABLE,
        type_="check",
    )
    op.alter_column(_FRAGMENT_TABLE, "content", existing_type=sa.Text(), nullable=False)
    op.create_check_constraint(
        "ck_context_fragment_content_nonblank",
        _FRAGMENT_TABLE,
        f"translate(content, {_PYTHON_ISSPACE_CODE_POINTS}, '') <> ''",
    )
    op.drop_column(_FRAGMENT_TABLE, "projection_kind")
    op.drop_constraint(
        "uq_membership_organization_id_version",
        "membership",
        type_="unique",
    )
    # Keep the v1|v2 constraint so historical v2 ContextRun lineage remains
    # readable after a schema-only rollback. Older code continues to write v1.
