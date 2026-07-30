"""Fix versioned Article access policy at first ingestion.

Revision ID: 20260730_0041
Revises: 20260727_0040
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260730_0041"
down_revision: str | None = "20260727_0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MIGRATOR = "context_engine_migrator"
_CONTROL = "context_engine_control"
_RUNTIME = "context_engine_runtime"
_WORKER = "context_engine_worker"
_WORKER_DEFINER = "context_engine_worker_lease_definer"
_ACCESS_DEFINER = "context_engine_access_policy_definer"
_RUNTIME_SOURCE_VERSION_FUNCTION = "context_runtime_article_source_version_allows"
_RUNTIME_SOURCE_VERSION_SIGNATURE = "(uuid,text,uuid)"
_SOURCE_VERSION_WRITER_PROCEDURES = (
    "context_control_activate_file_change_feed(uuid,uuid,uuid)",
    "context_control_activate_file_delete_observations(uuid,uuid,uuid)",
)
_SOURCE_VERSION_PUBLICATION_FENCE = """\
            PERFORM pg_catalog.pg_advisory_xact_lock(
                pg_catalog.hashtextextended(
                    'context-engine.file-publication:'
                    || requested_organization_id::text,
                    0
                )
            );
"""
_FILE_OPERATION_FENCES = (
    "context-engine.file-change-scheduling-migration-fence",
    "context-engine.file-dispatch-migration-fence",
    "context-engine.file-status-migration-fence",
)
_MAX = (1 << 63) - 1
_TABLES = (
    "article_access_group",
    "article_access_group_membership",
    "organization_article_policy_default",
    "source_article_policy_default",
    "article_explicit_policy_setting",
    "article_source_acl_observation",
    "article_access_policy",
)
_ACCESS_DEFINER_OPERATIONS = {
    "article_access_group": ("SELECT",),
    "article_access_group_membership": (),
    "organization_article_policy_default": ("SELECT", "UPDATE"),
    "source_article_policy_default": ("SELECT", "INSERT", "UPDATE"),
    "article_explicit_policy_setting": ("SELECT",),
    "article_source_acl_observation": ("SELECT", "INSERT", "UPDATE"),
    "article_access_policy": ("SELECT", "INSERT", "UPDATE"),
}
_TENANT = (
    "organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid"
)
_CURRENT_USER_ACTOR = """
{table_name}.organization_id = NULLIF(
    current_setting('app.organization_id', true), ''
)::uuid
AND current_setting('app.actor_kind', true) = 'user'
AND NULLIF(current_setting('app.principal_ref', true), '') IS NOT NULL
AND NULLIF(current_setting('app.request_id', true), '') IS NOT NULL
AND NULLIF(current_setting('app.authentication_binding_ref', true), '') IS NOT NULL
AND NULLIF(current_setting('app.checked_at', true), '') IS NOT NULL
AND EXISTS (
    SELECT 1
    FROM public.membership AS actor_membership
    WHERE actor_membership.organization_id = {table_name}.organization_id
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


def _exact_field_right(table_name: str, field_expression: str) -> str:
    return f"""
EXISTS (
    SELECT 1
    FROM public.membership_resource_field_right AS field_right
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


def _article_access(table_name: str) -> str:
    return f"""
EXISTS (
    SELECT 1
    FROM public.article_access_policy AS article_policy
    WHERE article_policy.organization_id = {table_name}.organization_id
      AND article_policy.resource_ref = {table_name}.resource_ref
)
""".strip()


def _legacy_resource_access(table_name: str) -> str:
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


def _fragment_runtime_expression(*, article: bool) -> str:
    table_name = "context_fragment"
    actor = _CURRENT_USER_ACTOR.format(table_name=table_name)
    active = _ACTIVE_REVISION.format(table_name=table_name)
    access = (
        _article_access(table_name) if article else _legacy_resource_access(table_name)
    )
    body_right = _exact_field_right(table_name, "'body'")
    return (
        f"{actor}\nAND {active}\nAND {access}\n"
        "AND (context_fragment.projection_kind = 'fields' OR ("
        "context_fragment.projection_kind = 'body' AND "
        f"{body_right}))"
    )


def _field_runtime_expression(*, article: bool) -> str:
    table_name = "context_fragment_field"
    actor = _CURRENT_USER_ACTOR.format(table_name=table_name)
    active = _ACTIVE_REVISION.format(table_name=table_name)
    access = (
        _article_access(table_name) if article else _legacy_resource_access(table_name)
    )
    right = _exact_field_right(table_name, "context_fragment_field.field_ref")
    return f"{actor}\nAND {active}\nAND {right}\nAND {access}"


def _field_right_runtime_expression(*, article: bool) -> str:
    table_name = "membership_resource_field_right"
    actor = _CURRENT_USER_ACTOR.format(table_name=table_name)
    access = (
        _article_access(table_name) if article else _legacy_resource_access(table_name)
    )
    return f"""
{actor}
AND membership_resource_field_right.membership_id = NULLIF(
    current_setting('app.membership_id', true), ''
)::uuid
AND membership_resource_field_right.membership_version = NULLIF(
    current_setting('app.membership_version', true), ''
)::bigint
AND EXISTS (
    SELECT 1 FROM public.context_resource AS live_resource
    WHERE live_resource.organization_id =
        membership_resource_field_right.organization_id
      AND live_resource.resource_ref =
        membership_resource_field_right.resource_ref
      AND live_resource.tombstoned IS FALSE
)
AND {access}
""".strip()


def _policy_shape(prefix: str) -> str:
    return (
        f"(({prefix}policy_kind IS NULL AND cardinality({prefix}group_refs) = 0) OR "
        f"({prefix}policy_kind IN ('private', 'organization') AND "
        f"cardinality({prefix}group_refs) = 0) OR "
        f"({prefix}policy_kind = 'groups' AND cardinality({prefix}group_refs) > 0))"
    )


def _secure_table(table_name: str) -> None:
    for role in (
        "PUBLIC",
        _CONTROL,
        _RUNTIME,
        _WORKER,
        _WORKER_DEFINER,
        _ACCESS_DEFINER,
    ):
        op.execute(f"REVOKE ALL ON TABLE {table_name} FROM {role}")
    op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table_name}_migrator_administration ON {table_name} "
        f"FOR ALL TO {_MIGRATOR} USING (true) WITH CHECK (true)"
    )
    for command in _ACCESS_DEFINER_OPERATIONS[table_name]:
        command_lower = command.lower()
        if command == "SELECT":
            expression = f"USING ({_TENANT})"
        elif command == "INSERT":
            expression = f"WITH CHECK ({_TENANT})"
        else:
            expression = f"USING ({_TENANT}) WITH CHECK ({_TENANT})"
        op.execute(
            f"CREATE POLICY {table_name}_access_definer_{command_lower} "
            f"ON {table_name} FOR {command} TO {_ACCESS_DEFINER} {expression}"
        )


def _rewrite_source_version_writer_fences(*, install: bool) -> None:
    marker = "            SELECT source.active_version_id, version.root_ref,\n"
    searched = marker if install else _SOURCE_VERSION_PUBLICATION_FENCE + marker
    replacement = _SOURCE_VERSION_PUBLICATION_FENCE + marker if install else marker
    connection = op.get_bind()
    for procedure in _SOURCE_VERSION_WRITER_PROCEDURES:
        definition = connection.execute(
            sa.text(
                "SELECT pg_catalog.pg_get_functiondef("
                "CAST(:procedure AS regprocedure))"
            ),
            {"procedure": f"public.{procedure}"},
        ).scalar_one()
        if not isinstance(definition, str) or definition.count(searched) != 1:
            raise RuntimeError(
                f"SourceVersion writer shape was not recognized: {procedure}"
            )
        op.execute(f"GRANT CREATE ON SCHEMA public TO {_WORKER_DEFINER}")
        op.execute(f"SET LOCAL ROLE {_WORKER_DEFINER}")
        op.execute(definition.replace(searched, replacement))
        op.execute("RESET ROLE")
        op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_WORKER_DEFINER}")


def upgrade() -> None:
    """Add Resource-keyed visibility without any Fragment ACL state."""

    op.create_table(
        "article_access_group",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("group_ref", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id", "group_ref", name="pk_article_access_group"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.organization_id"],
            name="fk_article_access_group_organization",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "btrim(group_ref) <> '' AND char_length(group_ref) <= 256 "
            "AND group_ref !~ '[[:space:]]'",
            name="ck_article_access_group_ref",
        ),
    )
    op.create_table(
        "article_access_group_membership",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("group_ref", sa.Text(), nullable=False),
        sa.Column("membership_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("membership_version", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "group_ref",
            "membership_id",
            "membership_version",
            name="pk_article_access_group_membership",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "group_ref"],
            ["article_access_group.organization_id", "article_access_group.group_ref"],
            name="fk_article_access_group_membership_group_same_organization",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "membership_id", "membership_version"],
            [
                "membership.organization_id",
                "membership.membership_id",
                "membership.membership_version",
            ],
            name="fk_article_access_group_membership_current_membership",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            f"membership_version BETWEEN 1 AND {_MAX}",
            name="ck_article_access_group_membership_version",
        ),
    )
    op.create_table(
        "organization_article_policy_default",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_kind", sa.Text(), nullable=True),
        sa.Column(
            "group_refs",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("ARRAY[]::text[]"),
        ),
        sa.Column(
            "default_version", sa.BigInteger(), nullable=False, server_default="1"
        ),
        sa.PrimaryKeyConstraint(
            "organization_id", name="pk_organization_article_policy_default"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.organization_id"],
            name="fk_organization_article_policy_default_organization",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            _policy_shape(""), name="ck_organization_article_policy_default_shape"
        ),
        sa.CheckConstraint(
            f"default_version BETWEEN 1 AND {_MAX}",
            name="ck_organization_article_policy_default_version",
        ),
    )
    op.create_table(
        "source_article_policy_default",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column("policy_kind", sa.Text(), nullable=True),
        sa.Column(
            "group_refs",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("ARRAY[]::text[]"),
        ),
        sa.Column("default_version", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id", "source_ref", name="pk_source_article_policy_default"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.organization_id"],
            name="fk_source_article_policy_default_organization",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "btrim(source_ref) <> ''", name="ck_source_article_policy_default_source"
        ),
        sa.CheckConstraint(
            _policy_shape(""), name="ck_source_article_policy_default_shape"
        ),
        sa.CheckConstraint(
            f"default_version BETWEEN 1 AND {_MAX}",
            name="ck_source_article_policy_default_version",
        ),
    )
    op.create_table(
        "article_explicit_policy_setting",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column("resource_ref", sa.Text(), nullable=False),
        sa.Column("policy_kind", sa.Text(), nullable=False),
        sa.Column(
            "group_refs",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("ARRAY[]::text[]"),
        ),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "resource_ref",
            name="pk_article_explicit_policy_setting",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.organization_id"],
            name="fk_article_explicit_policy_setting_organization",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "btrim(source_ref) <> '' AND btrim(resource_ref) <> ''",
            name="ck_article_explicit_policy_setting_refs",
        ),
        sa.CheckConstraint(
            _policy_shape(""), name="ck_article_explicit_policy_setting_shape"
        ),
    )
    op.create_table(
        "article_source_acl_observation",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_version_ref", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resource_ref", sa.Text(), nullable=False),
        sa.Column("evidence_mode", sa.Text(), nullable=False),
        sa.Column("observation_status", sa.Text(), nullable=False),
        sa.Column("policy_kind", sa.Text(), nullable=True),
        sa.Column(
            "group_refs",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("ARRAY[]::text[]"),
        ),
        sa.Column("observation_version", sa.BigInteger(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acl_as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column("declared_lag_seconds", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "resource_ref",
            name="pk_article_source_acl_observation",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.organization_id"],
            name="fk_article_source_acl_observation_organization",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_id", "source_version_ref"],
            [
                "source_version.organization_id",
                "source_version.source_id",
                "source_version.version_id",
            ],
            name="fk_article_source_acl_observation_source_version",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "evidence_mode = 'mirrored'",
            name="ck_article_source_acl_observation_mode",
        ),
        sa.CheckConstraint(
            "observation_status IN "
            "('resolved', 'missing', 'failed', 'unresolved_group')",
            name="ck_article_source_acl_observation_status",
        ),
        sa.CheckConstraint(
            "(observation_status = 'resolved' AND policy_kind IS NOT NULL "
            f"AND {_policy_shape('')}) OR "
            "(observation_status <> 'resolved' AND policy_kind IS NULL "
            "AND cardinality(group_refs) = 0)",
            name="ck_article_source_acl_observation_result",
        ),
        sa.CheckConstraint(
            f"observation_version BETWEEN 1 AND {_MAX}",
            name="ck_article_source_acl_observation_version",
        ),
        sa.CheckConstraint(
            "(source_id IS NULL AND source_version_ref IS NULL "
            "AND ((acl_as_of IS NULL AND declared_lag_seconds IS NULL) OR "
            "(acl_as_of IS NOT NULL AND declared_lag_seconds = 0))) OR "
            "(source_id IS NOT NULL AND source_id::text = source_ref "
            "AND source_version_ref IS NOT NULL AND acl_as_of IS NOT NULL "
            "AND declared_lag_seconds = 0 AND observed_at >= acl_as_of)",
            name="ck_article_source_acl_observation_mirrored_evidence",
        ),
    )
    op.create_table(
        "article_access_policy",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_ref", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.BigInteger(), nullable=False),
        sa.Column("local_policy_kind", sa.Text(), nullable=True),
        sa.Column("local_group_refs", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("policy_kind", sa.Text(), nullable=True),
        sa.Column("group_refs", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("published", sa.Boolean(), nullable=False),
        sa.Column("resolution_rung", sa.Text(), nullable=False),
        sa.Column("source_evidence_mode", sa.Text(), nullable=False),
        sa.Column("source_observation_status", sa.Text(), nullable=False),
        sa.Column("source_observation_version", sa.BigInteger(), nullable=True),
        sa.Column("source_version_ref", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_acl_as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_declared_lag_seconds", sa.BigInteger(), nullable=True),
        sa.Column("fixed_at_policy_epoch", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id", "resource_ref", name="pk_article_access_policy"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "resource_ref"],
            ["context_resource.organization_id", "context_resource.resource_ref"],
            name="fk_article_access_policy_resource_same_organization",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            f"policy_version BETWEEN 1 AND {_MAX}",
            name="ck_article_access_policy_version",
        ),
        sa.CheckConstraint(
            _policy_shape("local_"), name="ck_article_access_policy_local_shape"
        ),
        sa.CheckConstraint(
            "(published IS TRUE AND policy_kind IS NOT NULL "
            f"AND {_policy_shape('')}) OR "
            "(published IS FALSE AND policy_kind IS NULL "
            "AND cardinality(group_refs) = 0)",
            name="ck_article_access_policy_effective_shape",
        ),
        sa.CheckConstraint(
            "resolution_rung IN "
            "('explicit_article', 'source_default', 'tenant_default', 'isolation')",
            name="ck_article_access_policy_resolution_rung",
        ),
        sa.CheckConstraint(
            "source_evidence_mode = 'mirrored'",
            name="ck_article_access_policy_source_mode",
        ),
        sa.CheckConstraint(
            "source_observation_status IN "
            "('resolved', 'missing', 'failed', 'unresolved_group')",
            name="ck_article_access_policy_source_status",
        ),
        sa.CheckConstraint(
            f"fixed_at_policy_epoch BETWEEN 1 AND {_MAX}",
            name="ck_article_access_policy_fixed_epoch",
        ),
        sa.CheckConstraint(
            "(source_version_ref IS NULL AND ((source_acl_as_of IS NULL "
            "AND source_declared_lag_seconds IS NULL) OR "
            "(source_acl_as_of IS NOT NULL "
            "AND source_declared_lag_seconds = 0))) OR "
            "(source_version_ref IS NOT NULL AND source_acl_as_of IS NOT NULL "
            "AND source_declared_lag_seconds = 0)",
            name="ck_article_access_policy_mirrored_evidence",
        ),
    )

    for table_name in _TABLES:
        _secure_table(table_name)
    op.execute(
        f"""
        CREATE FUNCTION public.{_RUNTIME_SOURCE_VERSION_FUNCTION}(
            requested_organization_id uuid,
            requested_resource_ref text,
            expected_source_version_ref uuid
        ) RETURNS boolean
        LANGUAGE plpgsql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        SET row_security = on
        AS $function$
        DECLARE
            trusted_source_ref text;
        BEGIN
            IF SESSION_USER <> '{_RUNTIME}'
               OR requested_organization_id IS NULL
               OR requested_organization_id IS DISTINCT FROM NULLIF(
                    current_setting('app.organization_id', true), ''
               )::uuid
               OR requested_resource_ref IS NULL
            THEN RETURN false; END IF;

            SELECT resource.source_ref INTO trusted_source_ref
            FROM public.context_resource AS resource
            WHERE resource.organization_id = requested_organization_id
              AND resource.resource_ref = requested_resource_ref;
            IF trusted_source_ref IS NULL THEN RETURN false; END IF;
            IF trusted_source_ref !~
                    '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-'
                    '[0-9a-f]{{4}}-[0-9a-f]{{12}}$'
            THEN RETURN expected_source_version_ref IS NULL; END IF;
            IF expected_source_version_ref IS NULL THEN RETURN false; END IF;

            RETURN EXISTS (
                SELECT 1 FROM public.context_source AS source
                WHERE source.organization_id = requested_organization_id
                  AND source.source_id = trusted_source_ref::uuid
                  AND source.source_kind = 'file'
                  AND source.lifecycle_state = 'active'
                  AND source.active_version_id = expected_source_version_ref
            );
        END;
        $function$
        """
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION public.{_RUNTIME_SOURCE_VERSION_FUNCTION}"
        f"{_RUNTIME_SOURCE_VERSION_SIGNATURE} FROM PUBLIC"
    )
    for role in (_CONTROL, _WORKER):
        op.execute(
            f"REVOKE ALL ON FUNCTION public.{_RUNTIME_SOURCE_VERSION_FUNCTION}"
            f"{_RUNTIME_SOURCE_VERSION_SIGNATURE} FROM {role}"
        )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_RUNTIME_SOURCE_VERSION_FUNCTION}"
        f"{_RUNTIME_SOURCE_VERSION_SIGNATURE} TO {_RUNTIME}"
    )
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_ACCESS_DEFINER}")
    op.execute(
        f"ALTER FUNCTION public.{_RUNTIME_SOURCE_VERSION_FUNCTION}"
        f"{_RUNTIME_SOURCE_VERSION_SIGNATURE} OWNER TO {_ACCESS_DEFINER}"
    )
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_ACCESS_DEFINER}")
    for table_name in ("article_access_policy", "article_access_group_membership"):
        actor = _CURRENT_USER_ACTOR.format(table_name=table_name)
        if table_name == "article_access_policy":
            actor += """
            AND article_access_policy.published IS TRUE
            AND public.context_runtime_article_source_version_allows(
                article_access_policy.organization_id,
                article_access_policy.resource_ref,
                article_access_policy.source_version_ref
            )
            AND (
                article_access_policy.policy_kind = 'organization'
                OR (
                    article_access_policy.policy_kind = 'private'
                    AND EXISTS (
                        SELECT 1 FROM public.resource_access_policy AS access_policy
                        WHERE access_policy.organization_id =
                            article_access_policy.organization_id
                          AND access_policy.resource_ref =
                            article_access_policy.resource_ref
                          AND access_policy.principal_ref = current_setting(
                            'app.principal_ref', true
                          )
                          AND access_policy.access_state = 'allowed'
                    )
                )
                OR (
                    article_access_policy.policy_kind = 'groups'
                    AND EXISTS (
                        SELECT 1
                        FROM public.article_access_group_membership AS group_member
                        WHERE group_member.organization_id =
                            article_access_policy.organization_id
                          AND group_member.group_ref = ANY(
                            article_access_policy.group_refs
                          )
                          AND group_member.membership_id = NULLIF(
                            current_setting('app.membership_id', true), ''
                          )::uuid
                          AND group_member.membership_version = NULLIF(
                            current_setting('app.membership_version', true), ''
                          )::bigint
                    )
                )
            )
            """
        else:
            actor += """
            AND article_access_group_membership.membership_id = NULLIF(
                current_setting('app.membership_id', true), ''
            )::uuid
            AND article_access_group_membership.membership_version = NULLIF(
                current_setting('app.membership_version', true), ''
            )::bigint
            """
        op.execute(
            f"CREATE POLICY {table_name}_current_user_actor ON {table_name} "
            f"FOR SELECT TO {_RUNTIME} USING ({actor})"
        )
    resource_actor = _CURRENT_USER_ACTOR.format(table_name="context_resource")
    op.execute("DROP POLICY context_resource_current_user_actor ON context_resource")
    op.execute(
        "CREATE POLICY context_resource_current_user_actor ON context_resource "
        f"FOR SELECT TO {_RUNTIME} USING ({resource_actor} "
        "AND tombstoned IS FALSE AND "
        "public.context_runtime_file_source_lifecycle_allows("
        "context_resource.organization_id, context_resource.source_ref) "
        "AND EXISTS ("
        "SELECT 1 FROM public.article_access_policy AS article_policy "
        "WHERE article_policy.organization_id = context_resource.organization_id "
        "AND article_policy.resource_ref = context_resource.resource_ref))"
    )
    op.execute(
        f"GRANT SELECT ON TABLE article_access_policy, article_access_group_membership "
        f"TO {_RUNTIME}"
    )
    for table_name, expression in (
        (
            "context_fragment",
            _fragment_runtime_expression(article=True),
        ),
        (
            "context_fragment_field",
            _field_runtime_expression(article=True),
        ),
        (
            "membership_resource_field_right",
            _field_right_runtime_expression(article=True),
        ),
    ):
        op.execute(f"DROP POLICY {table_name}_current_user_actor ON {table_name}")
        op.execute(
            f"CREATE POLICY {table_name}_current_user_actor ON {table_name} "
            f"AS PERMISSIVE FOR SELECT TO {_RUNTIME} USING ({expression})"
        )
    for table_name, commands in _ACCESS_DEFINER_OPERATIONS.items():
        if commands:
            op.execute(
                f"GRANT {', '.join(commands)} ON TABLE {table_name} "
                f"TO {_ACCESS_DEFINER}"
            )
    op.execute(
        "CREATE POLICY source_version_access_policy_definer_select "
        f"ON source_version FOR SELECT TO {_ACCESS_DEFINER} USING ({_TENANT})"
    )
    op.execute(f"GRANT SELECT ON TABLE source_version TO {_ACCESS_DEFINER}")
    op.execute(
        "INSERT INTO organization_article_policy_default "
        "(organization_id, policy_kind, group_refs, default_version) "
        "SELECT organization_id, 'private', ARRAY[]::text[], 1 FROM organization"
    )
    op.execute(
        "INSERT INTO source_article_policy_default "
        "(organization_id, source_ref, policy_kind, group_refs, default_version) "
        "SELECT organization_id, source_id::text, 'private', ARRAY[]::text[], 1 "
        "FROM context_source"
    )
    op.execute(
        """
        CREATE FUNCTION public.organization_initialize_article_policy_default()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $function$
        BEGIN
            INSERT INTO public.organization_article_policy_default (
                organization_id, policy_kind, group_refs, default_version
            ) VALUES (NEW.organization_id, 'private', ARRAY[]::text[], 1);
            RETURN NULL;
        END; $function$
        """
    )
    op.execute(
        "CREATE TRIGGER organization_initialize_article_policy_default "
        "AFTER INSERT ON organization FOR EACH ROW EXECUTE FUNCTION "
        "public.organization_initialize_article_policy_default()"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION "
        "public.organization_initialize_article_policy_default() FROM PUBLIC"
    )
    op.execute(
        """
        CREATE FUNCTION public.context_source_initialize_article_policy_default()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog SET row_security = on
        AS $function$
        BEGIN
            PERFORM pg_catalog.set_config(
                'app.organization_id', NEW.organization_id::text, true
            );
            INSERT INTO public.source_article_policy_default (
                organization_id, source_ref, policy_kind, group_refs,
                default_version
            ) VALUES (
                NEW.organization_id, NEW.source_id::text, 'private',
                ARRAY[]::text[], 1
            );
            RETURN NULL;
        END; $function$
        """
    )
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_ACCESS_DEFINER}")
    op.execute(
        "ALTER FUNCTION public.context_source_initialize_article_policy_default() "
        f"OWNER TO {_ACCESS_DEFINER}"
    )
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_ACCESS_DEFINER}")
    op.execute(
        "REVOKE ALL ON FUNCTION "
        "public.context_source_initialize_article_policy_default() FROM PUBLIC"
    )
    op.execute(
        "CREATE TRIGGER context_source_initialize_article_policy_default "
        "AFTER INSERT ON context_source FOR EACH ROW EXECUTE FUNCTION "
        "public.context_source_initialize_article_policy_default()"
    )
    op.execute(
        f"""
        CREATE FUNCTION public.context_source_advance_article_evidence_epoch()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog SET row_security = on
        AS $function$
        DECLARE next_epoch bigint;
        BEGIN
            IF NEW.active_version_id IS NOT DISTINCT FROM OLD.active_version_id
               OR NOT EXISTS (
                    SELECT 1
                    FROM public.article_access_policy AS policy
                    WHERE policy.organization_id = NEW.organization_id
                      AND policy.source_version_ref = OLD.active_version_id
               )
            THEN RETURN NEW; END IF;
            PERFORM pg_catalog.set_config(
                'app.organization_id', NEW.organization_id::text, true
            );
            UPDATE public.organization_policy_epoch AS epoch
            SET policy_epoch = epoch.policy_epoch + 1
            WHERE epoch.organization_id = NEW.organization_id
              AND epoch.policy_epoch < {_MAX}
            RETURNING epoch.policy_epoch INTO next_epoch;
            IF next_epoch IS NULL THEN
                RAISE EXCEPTION USING ERRCODE = '40001',
                    MESSAGE = 'SourceVersion evidence invalidation was not accepted';
            END IF;
            RETURN NEW;
        END; $function$
        """
    )
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_ACCESS_DEFINER}")
    op.execute(
        "ALTER FUNCTION public.context_source_advance_article_evidence_epoch() "
        f"OWNER TO {_ACCESS_DEFINER}"
    )
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_ACCESS_DEFINER}")
    op.execute(
        "REVOKE ALL ON FUNCTION "
        "public.context_source_advance_article_evidence_epoch() FROM PUBLIC"
    )
    op.execute(
        "CREATE TRIGGER context_source_advance_article_evidence_epoch "
        "BEFORE UPDATE OF active_version_id ON context_source FOR EACH ROW "
        "EXECUTE FUNCTION public.context_source_advance_article_evidence_epoch()"
    )
    _rewrite_source_version_writer_fences(install=True)

    op.execute(
        f"""
        CREATE FUNCTION public.context_fix_article_access_policy(
            requested_organization_id uuid, requested_resource_ref text
        ) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog SET row_security = on
        AS $function$
        DECLARE
            resource_row public.context_resource%ROWTYPE;
            explicit_row public.article_explicit_policy_setting%ROWTYPE;
            source_default public.source_article_policy_default%ROWTYPE;
            tenant_default public.organization_article_policy_default%ROWTYPE;
            observation public.article_source_acl_observation%ROWTYPE;
            local_kind text;
            local_groups text[] := ARRAY[]::text[];
            effective_kind text;
            effective_groups text[] := ARRAY[]::text[];
            rung text := 'isolation';
            epoch bigint;
            declared_evidence_mode text;
        BEGIN
            IF SESSION_USER NOT IN ('{_WORKER}', '{_MIGRATOR}')
               OR NULLIF(current_setting('app.organization_id', true), '')::uuid
                    IS DISTINCT FROM requested_organization_id
            THEN RETURN false; END IF;
            SELECT * INTO resource_row FROM public.context_resource AS resource
            WHERE resource.organization_id = requested_organization_id
              AND resource.resource_ref = requested_resource_ref;
            IF resource_row.resource_ref IS NULL THEN RETURN false; END IF;
            IF EXISTS (SELECT 1 FROM public.article_access_policy AS policy
                       WHERE policy.organization_id = requested_organization_id
                         AND policy.resource_ref = requested_resource_ref)
            THEN RETURN false; END IF;

            SELECT * INTO explicit_row
            FROM public.article_explicit_policy_setting AS setting
            WHERE setting.organization_id = requested_organization_id
              AND setting.resource_ref = requested_resource_ref
              AND setting.source_ref = resource_row.source_ref;
            IF explicit_row.resource_ref IS NOT NULL THEN
                local_kind := explicit_row.policy_kind;
                local_groups := explicit_row.group_refs;
                rung := 'explicit_article';
            ELSE
                SELECT * INTO source_default
                FROM public.source_article_policy_default AS source_policy
                WHERE source_policy.organization_id = requested_organization_id
                  AND source_policy.source_ref = resource_row.source_ref;
                IF source_default.source_ref IS NOT NULL
                   AND source_default.policy_kind IS NOT NULL THEN
                    local_kind := source_default.policy_kind;
                    local_groups := source_default.group_refs;
                    rung := 'source_default';
                ELSE
                    SELECT * INTO tenant_default
                    FROM public.organization_article_policy_default AS tenant_policy
                    WHERE tenant_policy.organization_id = requested_organization_id;
                    IF tenant_default.policy_kind IS NOT NULL THEN
                        local_kind := tenant_default.policy_kind;
                        local_groups := tenant_default.group_refs;
                        rung := 'tenant_default';
                    END IF;
                END IF;
            END IF;
            IF local_kind = 'groups' AND EXISTS (
                SELECT 1 FROM unnest(local_groups) AS requested(group_ref)
                WHERE NOT EXISTS (
                    SELECT 1 FROM public.article_access_group AS owned
                    WHERE owned.organization_id = requested_organization_id
                      AND owned.group_ref = requested.group_ref
                )
            ) THEN
                local_kind := NULL;
                local_groups := ARRAY[]::text[];
                rung := 'isolation';
            END IF;

            SELECT * INTO observation
            FROM public.article_source_acl_observation AS source_acl
            WHERE source_acl.organization_id = requested_organization_id
              AND source_acl.resource_ref = requested_resource_ref
              AND source_acl.source_ref = resource_row.source_ref;
            IF resource_row.source_ref ~
                '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-'
                '[0-9a-f]{{4}}-[0-9a-f]{{12}}$'
            THEN
                SELECT version.capability_manifest->>'aclEvidenceMode'
                INTO declared_evidence_mode
                FROM public.context_source AS source
                JOIN public.source_version AS version
                  ON version.organization_id = source.organization_id
                 AND version.source_id = source.source_id
                 AND version.version_id = source.active_version_id
                WHERE source.organization_id = requested_organization_id
                  AND source.source_id = resource_row.source_ref::uuid
                  AND source.lifecycle_state = 'active';
            ELSE
                declared_evidence_mode := 'mirrored';
            END IF;
            IF declared_evidence_mode IS NULL
               OR (observation.resource_ref IS NOT NULL
                   AND observation.evidence_mode <> declared_evidence_mode)
            THEN
                observation := NULL;
            END IF;
            IF local_kind IS NOT NULL
               AND observation.observation_status = 'resolved'
               AND NOT (observation.policy_kind = 'groups' AND EXISTS (
                    SELECT 1 FROM unnest(observation.group_refs) AS requested(group_ref)
                    WHERE NOT EXISTS (
                        SELECT 1 FROM public.article_access_group AS owned
                        WHERE owned.organization_id = requested_organization_id
                          AND owned.group_ref = requested.group_ref
                    )
               )) THEN
                IF local_kind = 'private' OR observation.policy_kind = 'private' THEN
                    effective_kind := 'private';
                ELSIF local_kind = 'organization' THEN
                    effective_kind := observation.policy_kind;
                    effective_groups := observation.group_refs;
                ELSIF observation.policy_kind = 'organization' THEN
                    effective_kind := local_kind;
                    effective_groups := local_groups;
                ELSE
                    SELECT COALESCE(
                        array_agg(group_ref ORDER BY group_ref),
                        ARRAY[]::text[]
                    )
                    INTO effective_groups
                    FROM (
                        SELECT unnest(local_groups) AS group_ref
                        INTERSECT SELECT unnest(observation.group_refs)
                    ) AS shared;
                    IF cardinality(effective_groups) > 0 THEN
                        effective_kind := 'groups';
                    END IF;
                END IF;
            END IF;
            SELECT policy_epoch INTO epoch
            FROM public.organization_policy_epoch
            WHERE organization_id = requested_organization_id;
            IF epoch IS NULL THEN RETURN false; END IF;
            INSERT INTO public.article_access_policy (
                organization_id, resource_ref, policy_version,
                local_policy_kind, local_group_refs, policy_kind, group_refs,
                published, resolution_rung, source_evidence_mode,
                source_observation_status, source_observation_version,
                source_version_ref, source_acl_as_of,
                source_declared_lag_seconds,
                fixed_at_policy_epoch
            ) VALUES (
                requested_organization_id, requested_resource_ref, 1,
                local_kind, local_groups, effective_kind, effective_groups,
                effective_kind IS NOT NULL, rung,
                declared_evidence_mode,
                COALESCE(observation.observation_status, 'missing'),
                observation.observation_version,
                observation.source_version_ref, observation.acl_as_of,
                observation.declared_lag_seconds, epoch
            );
            RETURN true;
        END; $function$
        """
    )
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_ACCESS_DEFINER}")
    op.execute(
        "ALTER FUNCTION public.context_fix_article_access_policy(uuid,text) "
        f"OWNER TO {_ACCESS_DEFINER}"
    )
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_ACCESS_DEFINER}")
    op.execute(
        "REVOKE ALL ON FUNCTION "
        "public.context_fix_article_access_policy(uuid,text) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        f"public.context_fix_article_access_policy(uuid,text) TO {_MIGRATOR}"
    )

    # Existing rows predate the Article policy carrier. Preserve their current
    # principal-scoped behavior as a PRIVATE local policy. UUID-backed File rows
    # bind the exact active immutable SourceVersion; otherwise they isolate.
    op.execute(
        """
        INSERT INTO article_source_acl_observation (
            organization_id, source_ref, resource_ref, evidence_mode,
            observation_status, policy_kind, group_refs,
            observation_version, observed_at, source_id,
            source_version_ref, acl_as_of, declared_lag_seconds
        )
        SELECT resource.organization_id, resource.source_ref,
               resource.resource_ref, 'mirrored', 'resolved', 'private',
               ARRAY[]::text[], 1, pg_catalog.statement_timestamp(),
               source.source_id, version.version_id,
               pg_catalog.statement_timestamp(), 0
        FROM context_resource AS resource
        LEFT JOIN context_source AS source
          ON source.organization_id = resource.organization_id
         AND source.source_id::text = resource.source_ref
         AND source.source_kind = 'file'
         AND source.lifecycle_state = 'active'
        LEFT JOIN source_version AS version
          ON version.organization_id = source.organization_id
         AND version.source_id = source.source_id
         AND version.version_id = source.active_version_id
         AND version.capability_manifest->>'aclEvidenceMode' = 'mirrored'
        WHERE EXISTS (
            SELECT 1 FROM resource_access_policy AS access_policy
            WHERE access_policy.organization_id = resource.organization_id
              AND access_policy.resource_ref = resource.resource_ref
              AND access_policy.access_state = 'allowed'
        )
          AND (resource.source_ref !~
                '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-'
                '[0-9a-f]{4}-[0-9a-f]{12}$'
               OR version.version_id IS NOT NULL)
        """
    )
    op.execute(
        """
        INSERT INTO article_access_policy (
            organization_id, resource_ref, policy_version,
            local_policy_kind, local_group_refs, policy_kind, group_refs,
            published, resolution_rung, source_evidence_mode,
            source_observation_status, source_observation_version,
            source_version_ref, source_acl_as_of,
            source_declared_lag_seconds,
            fixed_at_policy_epoch
        )
        SELECT resource.organization_id, resource.resource_ref, 1,
               'private', ARRAY[]::text[], 'private', ARRAY[]::text[],
               true, 'explicit_article', 'mirrored', 'resolved', 1,
               observation.source_version_ref, observation.acl_as_of,
               observation.declared_lag_seconds, epoch.policy_epoch
        FROM context_resource AS resource
        JOIN organization_policy_epoch AS epoch
          ON epoch.organization_id = resource.organization_id
        JOIN article_source_acl_observation AS observation
          ON observation.organization_id = resource.organization_id
         AND observation.resource_ref = resource.resource_ref
        WHERE EXISTS (
            SELECT 1 FROM resource_access_policy AS access_policy
            WHERE access_policy.organization_id = resource.organization_id
              AND access_policy.resource_ref = resource.resource_ref
              AND access_policy.access_state = 'allowed'
        )
        """
    )
    op.execute(
        """
        INSERT INTO article_access_policy (
            organization_id, resource_ref, policy_version,
            local_policy_kind, local_group_refs, policy_kind, group_refs,
            published, resolution_rung, source_evidence_mode,
            source_observation_status, source_observation_version,
            source_version_ref, source_acl_as_of,
            source_declared_lag_seconds,
            fixed_at_policy_epoch
        )
        SELECT resource.organization_id, resource.resource_ref, 1,
               'private', ARRAY[]::text[], NULL, ARRAY[]::text[],
               false, 'explicit_article', 'mirrored', 'missing', NULL,
               NULL, NULL, NULL, epoch.policy_epoch
        FROM context_resource AS resource
        JOIN organization_policy_epoch AS epoch
          ON epoch.organization_id = resource.organization_id
        ON CONFLICT (organization_id, resource_ref) DO NOTHING
        """
    )

    op.execute(
        f"""
        CREATE FUNCTION public.article_access_policy_fix_from_file_access_grant()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog SET row_security = on
        AS $function$
        DECLARE
            source_value text;
            declared_mode text := 'mirrored';
            active_source_id uuid;
            active_source_version_ref uuid;
            observation_time timestamptz := pg_catalog.statement_timestamp();
            prior_policy public.article_access_policy%ROWTYPE;
            next_epoch bigint;
        BEGIN
            PERFORM pg_catalog.set_config(
                'app.organization_id', NEW.organization_id::text, true
            );
            SELECT source_ref INTO source_value FROM public.context_resource
            WHERE organization_id = NEW.organization_id
              AND resource_ref = NEW.resource_ref;
            IF source_value IS NULL THEN RETURN NULL; END IF;
            IF source_value ~
                '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-'
                '[0-9a-f]{{4}}-[0-9a-f]{{12}}$'
            THEN
                SELECT source.source_id, version.version_id,
                       version.capability_manifest->>'aclEvidenceMode'
                INTO active_source_id, active_source_version_ref, declared_mode
                FROM public.context_source AS source
                JOIN public.source_version AS version
                  ON version.organization_id = source.organization_id
                 AND version.source_id = source.source_id
                 AND version.version_id = source.active_version_id
                WHERE source.organization_id = NEW.organization_id
                  AND source.source_id = source_value::uuid
                  AND source.lifecycle_state = 'active';
                IF declared_mode <> 'mirrored' THEN RETURN NULL; END IF;
            END IF;
            IF NEW.access_state = 'allowed' THEN
                INSERT INTO public.article_source_acl_observation (
                organization_id, source_ref, resource_ref, evidence_mode,
                observation_status, policy_kind, group_refs,
                observation_version, observed_at, source_id,
                source_version_ref, acl_as_of, declared_lag_seconds
            ) VALUES (
                NEW.organization_id, source_value, NEW.resource_ref,
                declared_mode, 'resolved', 'private', ARRAY[]::text[], 1,
                observation_time, active_source_id, active_source_version_ref,
                observation_time, 0
                ) ON CONFLICT (organization_id, resource_ref) DO NOTHING;
                PERFORM public.context_fix_article_access_policy(
                    NEW.organization_id, NEW.resource_ref
                );
                RETURN NULL;
            END IF;
            IF OLD.access_state = 'allowed' AND NEW.access_state = 'revoked' THEN
                SELECT * INTO prior_policy
                FROM public.article_access_policy AS policy
                WHERE policy.organization_id = NEW.organization_id
                  AND policy.resource_ref = NEW.resource_ref
                FOR UPDATE;
                IF prior_policy.resource_ref IS NULL
                   OR prior_policy.policy_version >= {_MAX}
                   OR prior_policy.published IS FALSE THEN RETURN NULL; END IF;
                SELECT epoch.policy_epoch + 1 INTO next_epoch
                FROM public.organization_policy_epoch AS epoch
                WHERE epoch.organization_id = NEW.organization_id
                  AND epoch.policy_epoch < {_MAX};
                IF next_epoch IS NULL THEN
                    RAISE EXCEPTION USING ERRCODE = '40001',
                        MESSAGE = 'Article policy revocation was not accepted';
                END IF;
                UPDATE public.article_source_acl_observation AS observation
                SET observation_status = 'failed', policy_kind = NULL,
                    group_refs = ARRAY[]::text[],
                    observation_version = observation.observation_version + 1,
                    observed_at = observation_time,
                    acl_as_of = observation_time,
                    declared_lag_seconds = 0
                WHERE observation.organization_id = NEW.organization_id
                  AND observation.resource_ref = NEW.resource_ref
                  AND observation.observation_version < {_MAX};
                UPDATE public.article_access_policy AS policy
                SET policy_version = policy.policy_version + 1,
                    policy_kind = NULL, group_refs = ARRAY[]::text[],
                    published = false, source_observation_status = 'failed',
                    source_observation_version =
                        COALESCE(policy.source_observation_version, 0) + 1,
                    source_acl_as_of = observation_time,
                    source_declared_lag_seconds = 0,
                    fixed_at_policy_epoch = next_epoch
                WHERE policy.organization_id = NEW.organization_id
                  AND policy.resource_ref = NEW.resource_ref;
            END IF;
            RETURN NULL;
        END; $function$
        """
    )
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_ACCESS_DEFINER}")
    op.execute(
        "ALTER FUNCTION public.article_access_policy_fix_from_file_access_grant() "
        f"OWNER TO {_ACCESS_DEFINER}"
    )
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_ACCESS_DEFINER}")
    op.execute(
        "REVOKE ALL ON FUNCTION "
        "public.article_access_policy_fix_from_file_access_grant() FROM PUBLIC"
    )
    op.execute(
        "CREATE TRIGGER resource_access_policy_fix_article_policy "
        "AFTER INSERT OR UPDATE OF access_state ON resource_access_policy "
        "FOR EACH ROW EXECUTE FUNCTION "
        "public.article_access_policy_fix_from_file_access_grant()"
    )

    op.execute(
        f"""
        CREATE FUNCTION public.context_control_set_tenant_article_policy_default(
            requested_organization_id uuid, expected_version bigint,
            requested_policy_kind text, requested_group_refs text[]
        ) RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog SET row_security = on AS $function$
        DECLARE next_version bigint;
        BEGIN
            IF SESSION_USER <> '{_CONTROL}'
               OR NULLIF(current_setting('app.organization_id', true), '')::uuid
                    IS DISTINCT FROM requested_organization_id
               OR requested_group_refs IS NULL
               OR NOT ((requested_policy_kind IS NULL
                        AND cardinality(requested_group_refs) = 0)
                    OR (requested_policy_kind IN ('private','organization')
                        AND cardinality(requested_group_refs) = 0)
                    OR (requested_policy_kind = 'groups'
                        AND cardinality(requested_group_refs) > 0))
               OR EXISTS (
                    SELECT 1 FROM unnest(requested_group_refs) requested(group_ref)
                    WHERE NOT EXISTS (
                        SELECT 1 FROM public.article_access_group owned
                        WHERE owned.organization_id = requested_organization_id
                          AND owned.group_ref = requested.group_ref))
            THEN RETURN NULL; END IF;
            UPDATE public.organization_article_policy_default AS tenant_default
            SET policy_kind = requested_policy_kind,
                group_refs = requested_group_refs,
                default_version = tenant_default.default_version + 1
            WHERE tenant_default.organization_id = requested_organization_id
              AND tenant_default.default_version = expected_version
              AND tenant_default.default_version < {_MAX}
            RETURNING tenant_default.default_version INTO next_version;
            RETURN next_version;
        END; $function$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION public.context_control_set_source_article_policy_default(
            requested_organization_id uuid, requested_source_ref text,
            expected_version bigint, requested_policy_kind text,
            requested_group_refs text[]
        ) RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog SET row_security = on AS $function$
        DECLARE next_version bigint;
        BEGIN
            IF SESSION_USER <> '{_CONTROL}'
               OR NULLIF(current_setting('app.organization_id', true), '')::uuid
                    IS DISTINCT FROM requested_organization_id
               OR requested_source_ref !~
                    '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-'
                    '[0-9a-f]{{4}}-[0-9a-f]{{12}}$'
               OR requested_group_refs IS NULL
               OR NOT ((requested_policy_kind IS NULL
                        AND cardinality(requested_group_refs) = 0)
                    OR (requested_policy_kind IN ('private','organization')
                        AND cardinality(requested_group_refs) = 0)
                    OR (requested_policy_kind = 'groups'
                        AND cardinality(requested_group_refs) > 0))
               OR NOT EXISTS (
                    SELECT 1 FROM public.context_source source
                    WHERE source.organization_id = requested_organization_id
                      AND source.source_id = requested_source_ref::uuid
                      AND source.lifecycle_state = 'active')
               OR EXISTS (
                    SELECT 1 FROM unnest(requested_group_refs) requested(group_ref)
                    WHERE NOT EXISTS (
                        SELECT 1 FROM public.article_access_group owned
                        WHERE owned.organization_id = requested_organization_id
                          AND owned.group_ref = requested.group_ref))
            THEN RETURN NULL; END IF;
            UPDATE public.source_article_policy_default AS source_default
            SET policy_kind = requested_policy_kind,
                group_refs = requested_group_refs,
                default_version = source_default.default_version + 1
            WHERE source_default.organization_id = requested_organization_id
              AND source_default.source_ref = requested_source_ref
              AND source_default.default_version = expected_version
              AND source_default.default_version < {_MAX}
            RETURNING source_default.default_version INTO next_version;
            RETURN next_version;
        END; $function$
        """
    )
    for function_signature in (
        "context_control_set_tenant_article_policy_default(uuid,bigint,text,text[])",
        "context_control_set_source_article_policy_default(uuid,text,bigint,text,text[])",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION public.{function_signature} FROM PUBLIC")
        op.execute(
            f"GRANT EXECUTE ON FUNCTION public.{function_signature} TO {_CONTROL}"
        )
        op.execute(f"GRANT CREATE ON SCHEMA public TO {_ACCESS_DEFINER}")
        op.execute(
            f"ALTER FUNCTION public.{function_signature} OWNER TO {_ACCESS_DEFINER}"
        )
        op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_ACCESS_DEFINER}")

    # Explicit-Article, group-membership, and historical-policy administration
    # remain persistence-only seams until #130/#132 introduce their own proof-
    # bound operations.  In particular, no caller may author SourceAclEvidence.


def downgrade() -> None:
    """Remove only the ADR-0077 Article policy carrier."""

    connection = op.get_bind()
    # Join every File-operation fence in the exact order used by 0040 before
    # taking table locks. Otherwise an in-flight scheduler, dispatcher, or
    # status writer can hold a later shared fence while waiting for a relation
    # locked here, and the following 0040 downgrade can deadlock behind it.
    for migration_fence in _FILE_OPERATION_FENCES:
        connection.execute(
            sa.text(
                "SELECT pg_catalog.pg_advisory_xact_lock("
                "pg_catalog.hashtextextended(:migration_fence, 0))"
            ),
            {"migration_fence": migration_fence},
        )
    # Exclude concurrent policy/default/source-authority mutations between the
    # representability decision and DDL. PostgreSQL holds these locks until the
    # migration transaction commits or rolls back.
    for table_name in (
        "context_source",
        "source_version",
        "organization_policy_epoch",
        "resource_access_policy",
        "article_access_policy",
        "article_source_acl_observation",
        "organization_article_policy_default",
        "source_article_policy_default",
        "article_explicit_policy_setting",
        "article_access_group_membership",
        "article_access_group",
    ):
        connection.execute(sa.text(f"LOCK TABLE {table_name} IN ACCESS EXCLUSIVE MODE"))
    unsafe = connection.execute(
        sa.text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM article_access_policy AS policy
                WHERE (policy.published IS TRUE
                       AND policy.policy_kind <> 'private')
                   OR (EXISTS (
                        SELECT 1 FROM resource_access_policy AS legacy
                        WHERE legacy.organization_id = policy.organization_id
                          AND legacy.resource_ref = policy.resource_ref
                          AND legacy.access_state = 'allowed'
                   ) AND (
                        policy.published IS FALSE
                        OR EXISTS (
                            SELECT 1
                            FROM context_resource AS resource
                            WHERE resource.organization_id =
                                  policy.organization_id
                              AND resource.resource_ref = policy.resource_ref
                              AND resource.source_ref ~
                                  '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-'
                                  '[0-9a-f]{4}-[0-9a-f]{12}$'
                              AND EXISTS (
                                  SELECT 1
                                  FROM context_source AS source
                                  WHERE source.organization_id =
                                        resource.organization_id
                                    AND source.source_id =
                                        resource.source_ref::uuid
                                    AND source.source_kind = 'file'
                                    AND source.lifecycle_state = 'active'
                                    AND source.active_version_id IS DISTINCT FROM
                                        policy.source_version_ref
                              )
                        )
                   ))
                UNION ALL
                SELECT 1
                FROM organization_article_policy_default
                WHERE default_version <> 1 OR policy_kind <> 'private'
                   OR cardinality(group_refs) <> 0
                UNION ALL
                SELECT 1
                FROM source_article_policy_default
                WHERE default_version <> 1 OR policy_kind <> 'private'
                   OR cardinality(group_refs) <> 0
                UNION ALL SELECT 1 FROM article_explicit_policy_setting
                UNION ALL SELECT 1 FROM article_access_group_membership
                UNION ALL SELECT 1 FROM article_access_group
            )
            """
        )
    ).scalar_one()
    if unsafe is True:
        raise RuntimeError(
            "ADR-0077 Article policy state is not safely representable by 0040"
        )

    for function_signature in (
        "context_control_set_source_article_policy_default(uuid,text,bigint,text,text[])",
        "context_control_set_tenant_article_policy_default(uuid,bigint,text,text[])",
    ):
        op.execute(f"DROP FUNCTION public.{function_signature}")
    _rewrite_source_version_writer_fences(install=False)
    op.execute(
        "DROP TRIGGER context_source_advance_article_evidence_epoch "
        "ON context_source"
    )
    op.execute(
        "DROP FUNCTION public.context_source_advance_article_evidence_epoch()"
    )
    resource_actor = _CURRENT_USER_ACTOR.format(table_name="context_resource")
    op.execute("DROP POLICY context_resource_current_user_actor ON context_resource")
    op.execute(
        "CREATE POLICY context_resource_current_user_actor ON context_resource "
        f"FOR SELECT TO {_RUNTIME} USING ({resource_actor} AND tombstoned IS FALSE "
        "AND public.context_runtime_file_source_lifecycle_allows("
        "context_resource.organization_id, context_resource.source_ref))"
    )
    for table_name, expression in (
        (
            "membership_resource_field_right",
            _field_right_runtime_expression(article=False),
        ),
        (
            "context_fragment_field",
            _field_runtime_expression(article=False),
        ),
        (
            "context_fragment",
            _fragment_runtime_expression(article=False),
        ),
    ):
        op.execute(f"DROP POLICY {table_name}_current_user_actor ON {table_name}")
        op.execute(
            f"CREATE POLICY {table_name}_current_user_actor ON {table_name} "
            f"AS PERMISSIVE FOR SELECT TO {_RUNTIME} USING ({expression})"
        )
    op.execute(
        "DROP TRIGGER resource_access_policy_fix_article_policy "
        "ON resource_access_policy"
    )
    op.execute(
        "DROP FUNCTION public.article_access_policy_fix_from_file_access_grant()"
    )
    op.execute("DROP FUNCTION public.context_fix_article_access_policy(uuid,text)")
    op.execute(
        "DROP TRIGGER organization_initialize_article_policy_default ON organization"
    )
    op.execute(
        "DROP TRIGGER context_source_initialize_article_policy_default "
        "ON context_source"
    )
    op.execute(
        "DROP FUNCTION public.context_source_initialize_article_policy_default()"
    )
    op.execute("DROP FUNCTION public.organization_initialize_article_policy_default()")
    op.execute(
        "DROP POLICY source_version_access_policy_definer_select ON source_version"
    )
    op.execute(f"REVOKE SELECT ON TABLE source_version FROM {_ACCESS_DEFINER}")
    for table_name in reversed(_TABLES):
        op.drop_table(table_name)
    op.execute(
        f"DROP FUNCTION public.{_RUNTIME_SOURCE_VERSION_FUNCTION}"
        f"{_RUNTIME_SOURCE_VERSION_SIGNATURE}"
    )
