"""Activate a registered File source directly for change scanning.

Revision ID: 20260727_0037
Revises: 20260726_0036
Create Date: 2026-07-27
"""

# ruff: noqa: E501

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260727_0037"
down_revision: str | None = "20260726_0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFINER = "context_engine_worker_lease_definer"
_FUNCTION = "context_control_activate_file_change_feed"
_REGPROCEDURE = f"{_FUNCTION}(uuid,uuid,uuid)"
_V1 = """{"aclEvidenceMode":"mirrored","authorizeAndProject":"unavailable","batchLimits":"unavailable","checkpoint":"unavailable","checkpointSemantics":"unavailable","consistencyGuarantees":"unavailable","contentKinds":["markdown"],"cursorSemantics":"unavailable","declarationVersion":"file-capabilities-v1","deletion":"unavailable","describeCapabilities":"unavailable","discover":"unavailable","fileSourceAccess":"unavailable","freshness":"unavailable","ingestionJobs":"unavailable","projectionFields":[],"readChanges":"unavailable","resourceKinds":["markdown_document"],"sourceMode":"materialized"}"""
_V2 = """{"aclEvidenceMode":"mirrored","authorizeAndProject":"unavailable","batchLimits":"unavailable","checkpoint":"unavailable","checkpointSemantics":"unavailable","consistencyGuarantees":"unavailable","contentKinds":["markdown"],"cursorSemantics":"unavailable","declarationVersion":"file-capabilities-v2","deletion":"unavailable","describeCapabilities":"unavailable","discover":"unavailable","fileSourceAccess":"available","freshness":"unavailable","ingestionJobs":"available","projectionFields":[],"readChanges":"unavailable","resourceKinds":["markdown_document"],"sourceMode":"materialized"}"""
_V2_ONLY = f"""            IF selected_capabilities <> '{_V2}'::jsonb THEN RETURN; END IF;"""
_V1_OR_V2 = f"""            IF selected_capabilities NOT IN (
                '{_V1}'::jsonb, '{_V2}'::jsonb
            ) THEN RETURN; END IF;"""


def _function_definition() -> str:
    definition = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT pg_catalog.pg_get_functiondef("
                f"'public.{_REGPROCEDURE}'::regprocedure)"
            )
        )
        .scalar_one()
    )
    if not isinstance(definition, str):
        raise RuntimeError("File change activation function is unavailable")
    return definition


def _replace_exact(searched: str, replacement: str) -> None:
    definition = _function_definition()
    if definition.count(searched) != 1:
        raise RuntimeError("File change activation function shape was not recognized")
    replacement_definition = definition.replace(searched, replacement)
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(replacement_definition)
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


def upgrade() -> None:
    """Allow exact v1 or v2 state to advance to the existing immutable v3."""

    _replace_exact(_V2_ONLY, _V1_OR_V2)


def downgrade() -> None:
    """Restore the former v2-only activation precondition."""

    _replace_exact(_V1_OR_V2, _V2_ONLY)
