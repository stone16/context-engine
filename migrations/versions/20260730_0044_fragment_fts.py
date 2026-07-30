"""Add transactionally maintained native PostgreSQL FTS for Fragments.

Revision ID: 20260730_0044
Revises: 20260730_0043
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260730_0044"
down_revision: str | None = "20260730_0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TEXT_SEARCH_CONFIGURATION = "simple"


def upgrade() -> None:
    """Index immutable Fragment text synchronously in the publishing transaction."""

    op.add_column(
        "context_fragment",
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('simple'::regconfig, COALESCE(content, ''))",
                persisted=True,
            ),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_context_fragment_search_vector_gin",
        "context_fragment",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
    )


def downgrade() -> None:
    """Remove the native FTS projection without changing Fragment content."""

    op.drop_index(
        "ix_context_fragment_search_vector_gin",
        table_name="context_fragment",
    )
    op.drop_column("context_fragment", "search_vector")
