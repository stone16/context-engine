"""Register one Organization-owned File ContextSource and SourceVersion.

Revision ID: 20260722_0010
Revises: 20260722_0009
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260722_0010"
down_revision: str | None = "20260722_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MIGRATOR_ROLE = "context_engine_migrator"
_CONTROL_ROLE = "context_engine_control"
_RUNTIME_ROLE = "context_engine_runtime"
_WORKER_ROLE = "context_engine_worker"
_LEARNING_ROLE = "context_engine_learning"
_OPERATOR_ROLE = "context_engine_security_operator"
_SOURCE_TABLE = "context_source"
_VERSION_TABLE = "source_version"
_IMMUTABILITY_FUNCTION = "public.source_version_reject_mutation"
_IMMUTABILITY_TRIGGER = "source_version_immutable"
_TENANT_EXPRESSION = (
    "organization_id = NULLIF("
    "current_setting('app.organization_id', true), ''"
    ")::uuid"
)


def _secure_tenant_table(table_name: str) -> None:
    for role in (
        "PUBLIC",
        _CONTROL_ROLE,
        _RUNTIME_ROLE,
        _WORKER_ROLE,
        _LEARNING_ROLE,
        _OPERATOR_ROLE,
    ):
        op.execute(f"REVOKE ALL ON TABLE {table_name} FROM {role}")
    op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table_name}_migrator_administration "
        f"ON {table_name} AS PERMISSIVE FOR ALL TO {_MIGRATOR_ROLE} "
        "USING (true) WITH CHECK (true)"
    )
    op.execute(
        f"CREATE POLICY {table_name}_control_select "
        f"ON {table_name} AS PERMISSIVE FOR SELECT TO {_CONTROL_ROLE} "
        f"USING ({_TENANT_EXPRESSION})"
    )
    op.execute(
        f"CREATE POLICY {table_name}_control_insert "
        f"ON {table_name} AS PERMISSIVE FOR INSERT TO {_CONTROL_ROLE} "
        f"WITH CHECK ({_TENANT_EXPRESSION})"
    )
    op.execute(
        f"GRANT SELECT, INSERT ON TABLE {table_name} TO {_CONTROL_ROLE}"
    )


def upgrade() -> None:
    """Create the atomic immutable File source-registration boundary."""

    op.create_table(
        _SOURCE_TABLE,
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("source_kind", sa.Text(), nullable=False),
        sa.Column("registration_operation", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("registration_digest", sa.Text(), nullable=False),
        sa.Column(
            "active_version_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id", "source_id", name="pk_context_source"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "registration_operation",
            "idempotency_key",
            name="uq_context_source_registration_idempotency",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.organization_id"],
            name="fk_context_source_organization",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "source_kind = 'file'", name="ck_context_source_kind_file"
        ),
        sa.CheckConstraint(
            "registration_operation = 'register_source'",
            name="ck_context_source_registration_operation",
        ),
        sa.CheckConstraint(
            "btrim(display_name) <> '' AND char_length(display_name) <= 200 "
            "AND display_name !~ '[[:cntrl:]]'",
            name="ck_context_source_display_name",
        ),
        sa.CheckConstraint(
            "idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'",
            name="ck_context_source_idempotency_key",
        ),
        sa.CheckConstraint(
            "registration_digest ~ '^[0-9a-f]{64}$'",
            name="ck_context_source_registration_digest",
        ),
    )
    op.create_table(
        _VERSION_TABLE,
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_kind", sa.Text(), nullable=False),
        sa.Column("root_ref", sa.Text(), nullable=False),
        sa.Column(
            "capability_manifest",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "source_id",
            "version_id",
            name="pk_source_version",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_id"],
            ["context_source.organization_id", "context_source.source_id"],
            name="fk_source_version_source_same_organization",
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint(
            "source_kind = 'file'", name="ck_source_version_kind_file"
        ),
        sa.CheckConstraint(
            "root_ref ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' "
            "AND root_ref NOT IN ('.', '..')",
            name="ck_source_version_logical_root_ref",
        ),
        sa.CheckConstraint(
            "capability_manifest = "
            "'{\"aclEvidenceMode\": \"mirrored\", "
            "\"authorizeAndProject\": \"unavailable\", "
            "\"batchLimits\": \"unavailable\", "
            "\"checkpoint\": \"unavailable\", "
            "\"checkpointSemantics\": \"unavailable\", "
            "\"contentKinds\": [\"markdown\"], "
            "\"consistencyGuarantees\": \"unavailable\", "
            "\"cursorSemantics\": \"unavailable\", "
            "\"declarationVersion\": \"file-capabilities-v1\", "
            "\"deletion\": \"unavailable\", "
            "\"describeCapabilities\": \"unavailable\", "
            "\"discover\": \"unavailable\", "
            "\"fileSourceAccess\": \"unavailable\", "
            "\"freshness\": \"unavailable\", "
            "\"ingestionJobs\": \"unavailable\", "
            "\"projectionFields\": [], "
            "\"readChanges\": \"unavailable\", "
            "\"resourceKinds\": [\"markdown_document\"], "
            "\"sourceMode\": \"materialized\"}'::jsonb",
            name="ck_source_version_issue_21_capabilities",
        ),
    )
    op.create_foreign_key(
        "fk_context_source_active_version_same_organization",
        _SOURCE_TABLE,
        _VERSION_TABLE,
        ["organization_id", "source_id", "active_version_id"],
        ["organization_id", "source_id", "version_id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )

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
                MESSAGE = 'SourceVersion is immutable';
        END;
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION {_IMMUTABILITY_FUNCTION}() FROM PUBLIC")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION {_IMMUTABILITY_FUNCTION}() "
        f"TO {_MIGRATOR_ROLE}"
    )
    op.execute(
        f"CREATE TRIGGER {_IMMUTABILITY_TRIGGER} "
        f"BEFORE UPDATE OR DELETE ON {_VERSION_TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION {_IMMUTABILITY_FUNCTION}()"
    )

    for table_name in (_SOURCE_TABLE, _VERSION_TABLE):
        _secure_tenant_table(table_name)


def downgrade() -> None:
    """Remove only the Issue #21 source-registration schema."""

    op.drop_constraint(
        "fk_context_source_active_version_same_organization",
        _SOURCE_TABLE,
        type_="foreignkey",
    )
    op.drop_table(_VERSION_TABLE)
    op.execute(f"DROP FUNCTION {_IMMUTABILITY_FUNCTION}()")
    op.drop_table(_SOURCE_TABLE)
