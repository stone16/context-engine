"""Allow Runtime to observe, never publish, the active release.

Revision ID: 20260723_0021
Revises: 20260723_0020
Create Date: 2026-07-23
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260723_0021"
down_revision: str | None = "20260723_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CURRENT_USER_ACTOR = """
{table_name}.organization_id = NULLIF(
    current_setting('app.organization_id', true), ''
)::uuid
AND current_setting('app.actor_kind', true) = 'user'
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
      AND NULLIF(current_setting('app.principal_ref', true), '') IS NOT NULL
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


def upgrade() -> None:
    op.drop_constraint(
        "ck_context_run_package_digest_profile",
        "context_run",
        type_="check",
    )
    op.create_check_constraint(
        "ck_context_run_package_digest_profile",
        "context_run",
        "package_digest_profile IN ("
        "'context-package-canonical-json-v1', "
        "'context-package-canonical-json-v2', "
        "'context-package-canonical-json-v3'"
        ")",
    )
    for table_name in ("release_manifest", "active_release_manifest"):
        op.execute(
            f"CREATE POLICY {table_name}_runtime_select "
            f"ON public.{table_name} AS PERMISSIVE FOR SELECT "
            "TO context_engine_runtime "
            f"USING ({_CURRENT_USER_ACTOR.format(table_name=table_name)})"
        )
        op.execute(
            f"GRANT SELECT ON TABLE public.{table_name} TO context_engine_runtime"
        )


def downgrade() -> None:
    op.execute(
        """
        DO $body$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM public.context_run
                WHERE package_digest_profile =
                    'context-package-canonical-json-v3'
            ) THEN
                RAISE EXCEPTION USING
                    ERRCODE = '55006',
                    MESSAGE = 'runtime release observation downgrade refused: '
                        'v3 ContextRun lineage exists';
            END IF;
        END
        $body$
        """
    )
    for table_name in ("release_manifest", "active_release_manifest"):
        op.execute(
            f"REVOKE SELECT ON TABLE public.{table_name} FROM context_engine_runtime"
        )
        op.execute(f"DROP POLICY {table_name}_runtime_select ON public.{table_name}")
    op.drop_constraint(
        "ck_context_run_package_digest_profile",
        "context_run",
        type_="check",
    )
    op.create_check_constraint(
        "ck_context_run_package_digest_profile",
        "context_run",
        "package_digest_profile IN ("
        "'context-package-canonical-json-v1', "
        "'context-package-canonical-json-v2'"
        ")",
    )
