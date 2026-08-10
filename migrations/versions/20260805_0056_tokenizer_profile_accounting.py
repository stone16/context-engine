# ruff: noqa: E501
"""Bind immutable tokenizer profiles to Runtime Release lineage.

Revision ID: 20260805_0056
Revises: 20260803_0055
Create Date: 2026-08-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260805_0056"
down_revision: str | None = "20260803_0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_HISTORICAL_DOCUMENT = '{"accountingVersion":"utf8-byte-budget-v1","artifactDigest":"55d6f5f903a5c4f68fc0ae7754eedb64a0b80c9f847182cd66b1c220e9e68a3a","countingContract":"historical-delivered-block-utf8-bytes-v1","normalizationRef":"none","profileRef":"utf8-byte-budget-v1","vocabularyRef":"utf8-octets"}'
_HISTORICAL_DIGEST = "8bd781457c3ca4f089789dd035ed8beebee1a0d92674b9dbd13f96c0b8fc96b1"


def upgrade() -> None:
    """Label historical rows without recounting and admit future v1 lineage."""

    op.execute("LOCK TABLE public.release_manifest IN ACCESS EXCLUSIVE MODE")
    op.add_column(
        "release_manifest",
        sa.Column(
            "runtime_tokenizer_profile_document",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.literal_column(f"'{_HISTORICAL_DOCUMENT}'::jsonb"),
        ),
    )
    op.add_column(
        "release_manifest",
        sa.Column(
            "runtime_tokenizer_profile_digest",
            sa.Text(),
            nullable=False,
            server_default=_HISTORICAL_DIGEST,
        ),
    )
    op.create_check_constraint(
        "ck_release_manifest_tokenizer_profile",
        "release_manifest",
        "jsonb_typeof(runtime_tokenizer_profile_document) = 'object' "
        "AND char_length(runtime_tokenizer_profile_digest) = 64 "
        "AND runtime_tokenizer_profile_digest = lower(runtime_tokenizer_profile_digest) "
        "AND runtime_tokenizer_profile_digest ~ '^[0-9a-f]{64}$' "
        "AND (runtime_package_schema_ref <> 'context-package-openapi-v1' "
        "OR runtime_tokenizer_profile_document->>'profileRef' = runtime_tokenizer_ref)",
    )
    op.alter_column(
        "release_manifest", "runtime_tokenizer_profile_document", server_default=None
    )
    op.alter_column(
        "release_manifest", "runtime_tokenizer_profile_digest", server_default=None
    )


def downgrade() -> None:
    """Refuse to erase any admitted non-historical tokenizer lineage."""

    op.execute("LOCK TABLE public.release_manifest IN ACCESS EXCLUSIVE MODE")
    retained_v1 = op.get_bind().execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM public.release_manifest "
            "WHERE runtime_package_schema_ref = 'context-package-openapi-v1' "
            "OR runtime_tokenizer_profile_digest <> :digest)"
        ),
        {"digest": _HISTORICAL_DIGEST},
    ).scalar_one()
    if retained_v1 is True:
        raise RuntimeError("tokenizer profile downgrade requires historical-only lineage")
    op.drop_constraint(
        "ck_release_manifest_tokenizer_profile",
        "release_manifest",
        type_="check",
    )
    op.drop_column("release_manifest", "runtime_tokenizer_profile_digest")
    op.drop_column("release_manifest", "runtime_tokenizer_profile_document")
