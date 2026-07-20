"""Establish the empty PostgreSQL harness migration baseline.

Revision ID: 20260720_0001
Revises: None
Create Date: 2026-07-20

The Alembic version table is the only relation created. Organization,
Membership, tenant tables, and RLS policies belong to later owning issues.
"""

from collections.abc import Sequence

revision: str = "20260720_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Keep the application schema empty at the first migration revision."""


def downgrade() -> None:
    """Return to Alembic base without changing shared extensions or roles."""
