"""Reconcile connector ACL freshness with lease and database authority.

Revision ID: 20260731_0049
Revises: 20260731_0048
Create Date: 2026-07-31
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260731_0049"
down_revision: str | None = "20260731_0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFINER = "context_engine_worker_lease_definer"
_ACCEPT_REGPROCEDURE = (
    "context_supply_accept_connector_page"
    "(uuid,uuid,uuid,uuid,text,bytea,bigint,bigint,bytea,"
    "timestamp with time zone,timestamp with time zone,bigint,text,"
    "uuid[],text[],timestamp with time zone)"
)
_PRIOR_ACL_FRESHNESS = """\
                       OR payload_acl->>'organization_id'
                            IS DISTINCT FROM requested_organization_id::text
                       OR payload_acl->>'evidence_class' IS NULL
"""
_LEASE_BOUND_ACL_FRESHNESS = """\
                       OR payload_acl->>'organization_id'
                            IS DISTINCT FROM requested_organization_id::text
                       OR jsonb_typeof(payload_acl->'policy_epoch')
                            IS DISTINCT FROM 'number'
                       OR payload_acl->>'policy_epoch' !~ '^[1-9][0-9]*$'
                       OR (payload_acl->>'policy_epoch')::numeric
                            > requested_policy_epoch
                       OR jsonb_typeof(payload_acl->'observed_at')
                            IS DISTINCT FROM 'string'
                       OR payload_acl->>'observed_at'
                            !~ 'T.*(Z|[+-][0-9]{2}:[0-9]{2})$'
                       OR (payload_acl->>'observed_at')::timestamptz > now_at
                       OR payload_acl->>'evidence_class' IS NULL
"""
_PRIOR_EXCEPTION_HANDLER = """\
            EXCEPTION WHEN OTHERS THEN
                RETURN;
            END;
"""
_FAIL_CLOSED_EXCEPTION_HANDLER = """\
            EXCEPTION WHEN data_exception THEN
                RETURN;
            END;
"""


def _replace_acceptance_validation(searched: str, replacement: str) -> None:
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"""
        DO $block$
        DECLARE
            definition text;
            replacement_definition text;
        BEGIN
            definition := pg_catalog.pg_get_functiondef(
                'public.{_ACCEPT_REGPROCEDURE}'::regprocedure
            );
            replacement_definition := pg_catalog.replace(
                definition,
                $search${searched}$search$,
                $replacement${replacement}$replacement$
            );
            IF replacement_definition = definition THEN
                RAISE EXCEPTION
                    'Supply ACL freshness validation body was not recognized';
            END IF;
            EXECUTE replacement_definition;
        END;
        $block$
        """
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


def upgrade() -> None:
    """Refuse self-attested ACL freshness ahead of database authority."""

    _replace_acceptance_validation(
        _PRIOR_ACL_FRESHNESS,
        _LEASE_BOUND_ACL_FRESHNESS,
    )
    _replace_acceptance_validation(
        _PRIOR_EXCEPTION_HANDLER,
        _FAIL_CLOSED_EXCEPTION_HANDLER,
    )


def downgrade() -> None:
    """Restore the migration-0043 ACL validation body."""

    _replace_acceptance_validation(
        _FAIL_CLOSED_EXCEPTION_HANDLER,
        _PRIOR_EXCEPTION_HANDLER,
    )
    _replace_acceptance_validation(
        _LEASE_BOUND_ACL_FRESHNESS,
        _PRIOR_ACL_FRESHNESS,
    )
