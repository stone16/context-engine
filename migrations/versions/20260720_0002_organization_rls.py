"""Add the first Organization-owned row protected by forced RLS.

Revision ID: 20260720_0002
Revises: 20260720_0001
Create Date: 2026-07-20

This revision deliberately proves only the first database-isolation slice:
Organization is the global security root and organization_record is the sole
tenant-owned representative. Later owning issues add the domain tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260720_0002"
down_revision: str | None = "20260720_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUNTIME_ROLE = "context_engine_runtime"
_WORKER_ROLE = "context_engine_worker"
_TENANT_EXPRESSION = (
    "organization_id = "
    "COALESCE("
    "NULLIF(current_setting('app.organization_id', true), ''), "
    "'missing-organization-context'"
    ")::uuid"
)


def upgrade() -> None:
    """Create the bounded Organization ownership and RLS proof schema."""

    op.create_table(
        "organization",
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "organization_id",
            name="pk_organization",
        ),
    )
    op.create_table(
        "organization_record",
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "record_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "parent_record_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "record_id",
            name="pk_organization_record",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.organization_id"],
            name="fk_organization_record_organization",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "parent_record_id"],
            [
                "organization_record.organization_id",
                "organization_record.record_id",
            ],
            name="fk_organization_record_parent_same_organization",
        ),
    )

    op.execute("REVOKE ALL ON TABLE organization FROM PUBLIC")
    op.execute(f"REVOKE ALL ON TABLE organization FROM {_RUNTIME_ROLE}")
    op.execute(f"REVOKE ALL ON TABLE organization FROM {_WORKER_ROLE}")
    op.execute("REVOKE ALL ON TABLE organization_record FROM PUBLIC")
    op.execute(f"REVOKE ALL ON TABLE organization_record FROM {_RUNTIME_ROLE}")
    op.execute(f"REVOKE ALL ON TABLE organization_record FROM {_WORKER_ROLE}")

    op.execute("ALTER TABLE organization_record ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organization_record FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY organization_record_organization_isolation "
        "ON organization_record "
        "AS PERMISSIVE "
        "FOR ALL "
        f"TO {_RUNTIME_ROLE} "
        f"USING ({_TENANT_EXPRESSION}) "
        f"WITH CHECK ({_TENANT_EXPRESSION})"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        f"ON TABLE organization_record TO {_RUNTIME_ROLE}"
    )


def downgrade() -> None:
    """Remove only the bounded Issue #8 ownership proof schema."""

    op.execute(
        "REVOKE SELECT, INSERT, UPDATE, DELETE "
        f"ON TABLE organization_record FROM {_RUNTIME_ROLE}"
    )
    op.drop_table("organization_record")
    op.drop_table("organization")
