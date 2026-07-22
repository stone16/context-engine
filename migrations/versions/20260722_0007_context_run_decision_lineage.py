"""Persist authorized ContextRun lineage and restricted DecisionAudit records.

Revision ID: 20260722_0007
Revises: 20260722_0006
Create Date: 2026-07-22

The Runtime may append lineage only for the exact current UserActor.  Runtime
cannot read either table.  An independent control connection may issue one
opaque, exact-Organization and exact-decision operator-read ticket.  A
security-operator read deletes that ticket before projecting the restricted
row, and the application commits before returning it; neither application role
can read the underlying tables.  DecisionAudit has no carrier for query,
content, denied identifiers, or counts, and its parent lineage must match the
exact current UserActor's delivered-empty ContextRun.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260722_0007"
down_revision: str | None = "20260722_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MIGRATOR_ROLE = "context_engine_migrator"
_CONTROL_ROLE = "context_engine_control"
_RUNTIME_ROLE = "context_engine_runtime"
_WORKER_ROLE = "context_engine_worker"
_SECURITY_OPERATOR_ROLE = "context_engine_security_operator"
_CONTEXT_RUN_READER_DEFINER_ROLE = "context_engine_context_run_reader_definer"
_RUN_TABLE = "context_run"
_AUDIT_TABLE = "decision_audit"
_OPERATOR_TICKET_TABLE = "context_run_operator_read_ticket"
_AUDIT_PARENT_GUARD_FUNCTION = "decision_audit_require_exact_empty_parent"
_AUDIT_PARENT_GUARD_TRIGGER = "decision_audit_exact_empty_parent_guard"
_ISSUE_OPERATOR_TICKET_FUNCTION = "public.issue_context_run_operator_read_ticket"
_REVOKE_OPERATOR_TICKET_FUNCTION = "public.revoke_context_run_operator_read_ticket"
_READ_BY_OPERATOR_TICKET_FUNCTION = "public.read_context_run_by_operator_ticket"
_ISSUE_OPERATOR_TICKET_SIGNATURE = "(text, uuid, text, text, text, text)"
_REVOKE_OPERATOR_TICKET_SIGNATURE = "(text)"
_READ_BY_OPERATOR_TICKET_SIGNATURE = "(text, uuid, text)"
_MAX_SIGNED_BIGINT = 9_223_372_036_854_775_807
_QUERY_DIGEST_PROFILE = "context-query-json-hmac-sha256-v1"
_PACKAGE_DIGEST_PROFILE = "context-package-canonical-json-v1"
_OPERATOR_TICKET_TTL_SECONDS = 60

_CURRENT_USER_ACTOR = """
{table_name}.organization_id = NULLIF(
    current_setting('app.organization_id', true), ''
)::uuid
AND current_setting('app.actor_kind', true) = 'user'
AND {table_name}.user_id = NULLIF(
    current_setting('app.user_id', true), ''
)::uuid
AND {table_name}.membership_id = NULLIF(
    current_setting('app.membership_id', true), ''
)::uuid
AND {table_name}.membership_version = NULLIF(
    current_setting('app.membership_version', true), ''
)::bigint
AND {table_name}.principal_ref = current_setting(
    'app.principal_ref', true
)
AND {table_name}.request_id = current_setting(
    'app.request_id', true
)
AND {table_name}.authentication_binding_ref = current_setting(
    'app.authentication_binding_ref', true
)
AND {table_name}.accepted_at = NULLIF(
    current_setting('app.checked_at', true), ''
)::timestamptz
AND EXISTS (
    SELECT 1
    FROM public.membership AS actor_membership
    WHERE actor_membership.organization_id = {table_name}.organization_id
      AND actor_membership.user_id = {table_name}.user_id
      AND actor_membership.membership_id = {table_name}.membership_id
      AND actor_membership.membership_version = {table_name}.membership_version
      AND actor_membership.status = 'active'
      AND actor_membership.valid_from <= {table_name}.accepted_at
      AND (
          actor_membership.valid_until IS NULL
          OR actor_membership.valid_until > {table_name}.accepted_at
      )
)
""".strip()

_CURRENT_USER_ACTOR_CONTEXT = """
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


def _current_user_actor(table_name: str) -> str:
    template = (
        _CURRENT_USER_ACTOR if table_name == _RUN_TABLE else _CURRENT_USER_ACTOR_CONTEXT
    )
    return template.format(table_name=table_name)


def _operator_ticket_context(table_name: str, *, read_only: bool = False) -> str:
    modes = "= 'read'" if read_only else "IN ('issue', 'read')"
    return (
        f"{table_name}.organization_id = NULLIF("
        "current_setting('app.context_run_operator_ticket_organization_id', "
        "true), '')::uuid "
        f"AND {table_name}.decision_ref = current_setting("
        "'app.context_run_operator_ticket_decision_ref', true) "
        "AND current_setting('app.context_run_operator_ticket_mode', true) "
        f"{modes}"
    )


def _operator_ticket_row_context(*, insert: bool = False) -> str:
    if insert:
        mode_and_binding = (
            "current_setting('app.context_run_operator_ticket_mode', true) = "
            "'issue' AND "
            "context_run_operator_read_ticket.organization_id = NULLIF("
            "current_setting("
            "'app.context_run_operator_ticket_organization_id', true), "
            "'')::uuid AND "
            "context_run_operator_read_ticket.decision_ref = current_setting("
            "'app.context_run_operator_ticket_decision_ref', true) AND "
        )
    else:
        mode_and_binding = (
            "(current_setting('app.context_run_operator_ticket_mode', true) = "
            "'revoke' OR ("
            "current_setting('app.context_run_operator_ticket_mode', true) = "
            "'read' AND "
            "context_run_operator_read_ticket.organization_id = NULLIF("
            "current_setting("
            "'app.context_run_operator_ticket_organization_id', true), "
            "'')::uuid AND "
            "context_run_operator_read_ticket.decision_ref = current_setting("
            "'app.context_run_operator_ticket_decision_ref', true))) AND "
        )
    return (
        mode_and_binding + "context_run_operator_read_ticket.ticket_digest = "
        "pg_catalog.decode(NULLIF(current_setting("
        "'app.context_run_operator_ticket_digest', true), ''), 'hex')"
    )


def _hex_digest(column_name: str) -> str:
    return (
        f"char_length({column_name}) = 64 "
        f"AND {column_name} = lower({column_name}) "
        f"AND {column_name} ~ '^[0-9a-f]{{64}}$'"
    )


def upgrade() -> None:
    """Create the Issue #19 durable lineage and restricted audit boundary."""

    op.create_table(
        _RUN_TABLE,
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_ref", sa.Text(), nullable=False),
        sa.Column("decision_ref", sa.Text(), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("membership_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("membership_version", sa.BigInteger(), nullable=False),
        sa.Column("principal_ref", sa.Text(), nullable=False),
        sa.Column("agent_version_ref", sa.Text(), nullable=False),
        sa.Column("authenticated_application_ref", sa.Text(), nullable=False),
        sa.Column("authentication_binding_ref", sa.Text(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("policy_snapshot_ref", sa.Text(), nullable=False),
        sa.Column("policy_epoch", sa.BigInteger(), nullable=False),
        sa.Column("effective_scope_digest", sa.Text(), nullable=False),
        sa.Column("query_digest_profile", sa.Text(), nullable=False),
        sa.Column("query_digest_key_version", sa.BigInteger(), nullable=False),
        sa.Column("query_digest", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("package_digest_profile", sa.Text(), nullable=False),
        sa.Column("package_digest", sa.Text(), nullable=False),
        sa.Column("package_retention_mode", sa.Text(), nullable=False),
        sa.Column(
            "authorized_evidence_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("effective_max_tokens", sa.BigInteger(), nullable=False),
        sa.Column("effective_max_provider_calls", sa.BigInteger(), nullable=False),
        sa.Column("effective_max_cost_microunits", sa.BigInteger(), nullable=False),
        sa.Column("effective_max_elapsed_ms", sa.BigInteger(), nullable=False),
        sa.Column("usage_tokens", sa.BigInteger(), nullable=False),
        sa.Column("usage_provider_calls", sa.BigInteger(), nullable=False),
        sa.Column("usage_cost_microunits", sa.BigInteger(), nullable=False),
        sa.Column("usage_elapsed_ms", sa.BigInteger(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("package_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("package_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "run_ref",
            name="pk_context_run",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "decision_ref",
            name="uq_context_run_decision_ref",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "run_ref",
            "decision_ref",
            name="uq_context_run_lineage",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.organization_id"],
            name="fk_context_run_organization",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "membership_id"],
            ["membership.organization_id", "membership.membership_id"],
            name="fk_context_run_membership_same_organization",
        ),
        sa.CheckConstraint(
            " AND ".join(
                f"btrim({column_name}) <> ''"
                for column_name in (
                    "run_ref",
                    "decision_ref",
                    "principal_ref",
                    "agent_version_ref",
                    "authenticated_application_ref",
                    "authentication_binding_ref",
                    "request_id",
                    "purpose",
                    "policy_snapshot_ref",
                )
            ),
            name="ck_context_run_reference_fields_nonblank",
        ),
        sa.CheckConstraint(
            f"membership_version BETWEEN 1 AND {_MAX_SIGNED_BIGINT}",
            name="ck_context_run_membership_version_positive",
        ),
        sa.CheckConstraint(
            f"policy_epoch BETWEEN 1 AND {_MAX_SIGNED_BIGINT}",
            name="ck_context_run_policy_epoch_positive",
        ),
        sa.CheckConstraint(
            _hex_digest("effective_scope_digest"),
            name="ck_context_run_effective_scope_digest_sha256",
        ),
        sa.CheckConstraint(
            f"query_digest_profile = '{_QUERY_DIGEST_PROFILE}'",
            name="ck_context_run_query_digest_profile",
        ),
        sa.CheckConstraint(
            f"query_digest_key_version BETWEEN 1 AND {_MAX_SIGNED_BIGINT}",
            name="ck_context_run_query_digest_key_version_positive",
        ),
        sa.CheckConstraint(
            _hex_digest("query_digest"),
            name="ck_context_run_query_digest_sha256",
        ),
        sa.CheckConstraint(
            "outcome IN ('delivered_authorized', 'delivered_empty')",
            name="ck_context_run_outcome",
        ),
        sa.CheckConstraint(
            f"package_digest_profile = '{_PACKAGE_DIGEST_PROFILE}'",
            name="ck_context_run_package_digest_profile",
        ),
        sa.CheckConstraint(
            _hex_digest("package_digest"),
            name="ck_context_run_package_digest_sha256",
        ),
        sa.CheckConstraint(
            "package_retention_mode = 'digest_only'",
            name="ck_context_run_package_retention_digest_only",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(authorized_evidence_refs) = 'array' "
            "AND NOT jsonb_path_exists("
            "authorized_evidence_refs, "
            '\'$[*] ? (@.type() != "string" || '
            '!(@ like_regex "^ev_[0-9a-f]{64}$"))\''
            ")",
            name="ck_context_run_authorized_evidence_refs_array",
        ),
        sa.CheckConstraint(
            "effective_max_tokens > 0 "
            "AND effective_max_provider_calls > 0 "
            "AND effective_max_cost_microunits > 0 "
            "AND effective_max_elapsed_ms > 0",
            name="ck_context_run_budget_ceilings_positive",
        ),
        sa.CheckConstraint(
            "usage_tokens >= 0 "
            "AND usage_provider_calls >= 0 "
            "AND usage_cost_microunits >= 0 "
            "AND usage_elapsed_ms >= 0",
            name="ck_context_run_budget_usage_nonnegative",
        ),
        sa.CheckConstraint(
            "usage_tokens <= effective_max_tokens "
            "AND usage_provider_calls <= effective_max_provider_calls "
            "AND usage_cost_microunits <= effective_max_cost_microunits "
            "AND usage_elapsed_ms <= effective_max_elapsed_ms",
            name="ck_context_run_budget_usage_within_ceiling",
        ),
        sa.CheckConstraint(
            "(outcome = 'delivered_authorized' "
            "AND jsonb_array_length(authorized_evidence_refs) > 0) "
            "OR (outcome = 'delivered_empty' "
            "AND jsonb_array_length(authorized_evidence_refs) = 0)",
            name="ck_context_run_outcome_evidence_consistency",
        ),
        sa.CheckConstraint(
            "finalized_at >= accepted_at "
            "AND package_as_of = finalized_at "
            "AND package_expires_at > package_as_of",
            name="ck_context_run_timestamp_order",
        ),
    )

    op.create_table(
        _AUDIT_TABLE,
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_ref", sa.Text(), nullable=False),
        sa.Column("decision_ref", sa.Text(), nullable=False),
        sa.Column("policy_snapshot_ref", sa.Text(), nullable=False),
        sa.Column("policy_epoch", sa.BigInteger(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "decision_ref",
            name="pk_decision_audit",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_ref", "decision_ref"],
            [
                "context_run.organization_id",
                "context_run.run_ref",
                "context_run.decision_ref",
            ],
            name="fk_decision_audit_context_run_same_organization",
        ),
        sa.CheckConstraint(
            "btrim(policy_snapshot_ref) <> ''",
            name="ck_decision_audit_policy_snapshot_ref_nonblank",
        ),
        sa.CheckConstraint(
            f"policy_epoch BETWEEN 1 AND {_MAX_SIGNED_BIGINT}",
            name="ck_decision_audit_policy_epoch_positive",
        ),
        sa.CheckConstraint(
            "category = 'no_authorized_evidence'",
            name="ck_decision_audit_category_no_authorized_evidence",
        ),
    )

    op.create_table(
        _OPERATOR_TICKET_TABLE,
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision_ref", sa.Text(), nullable=False),
        sa.Column("ticket_digest", postgresql.BYTEA(), nullable=False),
        sa.Column("operator_ref", sa.Text(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("authentication_binding_ref", sa.Text(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "ticket_digest",
            name="pk_context_run_operator_read_ticket",
        ),
        sa.UniqueConstraint(
            "ticket_digest",
            name="uq_context_run_operator_read_ticket_digest",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "decision_ref"],
            ["context_run.organization_id", "context_run.decision_ref"],
            name="fk_context_run_operator_ticket_exact_decision",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "octet_length(ticket_digest) = 32",
            name="ck_context_run_operator_ticket_digest_sha256",
        ),
        sa.CheckConstraint(
            "btrim(decision_ref) <> '' "
            "AND btrim(operator_ref) <> '' "
            "AND btrim(request_id) <> '' "
            "AND btrim(authentication_binding_ref) <> ''",
            name="ck_context_run_operator_ticket_bindings_nonblank",
        ),
        sa.CheckConstraint(
            "expires_at = issued_at + interval '60 seconds'",
            name="ck_context_run_operator_ticket_exact_ttl",
        ),
    )

    op.execute(
        f"""
        CREATE FUNCTION {_AUDIT_PARENT_GUARD_FUNCTION}()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $function$
        BEGIN
            IF session_user = '{_MIGRATOR_ROLE}' THEN
                RETURN NEW;
            END IF;

            IF session_user <> '{_RUNTIME_ROLE}'
               OR current_setting('app.actor_kind', true) <> 'user'
               OR NOT EXISTS (
                    SELECT 1
                    FROM public.context_run AS parent_run
                    WHERE parent_run.organization_id = NEW.organization_id
                      AND parent_run.run_ref = NEW.run_ref
                      AND parent_run.decision_ref = NEW.decision_ref
                      AND parent_run.user_id = NULLIF(
                          current_setting('app.user_id', true), ''
                      )::uuid
                      AND parent_run.membership_id = NULLIF(
                          current_setting('app.membership_id', true), ''
                      )::uuid
                      AND parent_run.membership_version = NULLIF(
                          current_setting('app.membership_version', true), ''
                      )::bigint
                      AND parent_run.principal_ref = current_setting(
                          'app.principal_ref', true
                      )
                      AND parent_run.request_id = current_setting(
                          'app.request_id', true
                      )
                      AND parent_run.authentication_binding_ref = current_setting(
                          'app.authentication_binding_ref', true
                      )
                      AND parent_run.accepted_at = NULLIF(
                          current_setting('app.checked_at', true), ''
                      )::timestamptz
                      AND parent_run.outcome = 'delivered_empty'
                      AND jsonb_array_length(
                          parent_run.authorized_evidence_refs
                      ) = 0
                      AND parent_run.policy_snapshot_ref = NEW.policy_snapshot_ref
                      AND parent_run.policy_epoch = NEW.policy_epoch
                      AND parent_run.finalized_at = NEW.recorded_at
               ) THEN
                RAISE EXCEPTION USING
                    ERRCODE = '42501',
                    MESSAGE = 'decision audit parent lineage was not accepted';
            END IF;
            RETURN NEW;
        END;
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION {_AUDIT_PARENT_GUARD_FUNCTION}() FROM PUBLIC")
    for role_name in (
        _CONTROL_ROLE,
        _RUNTIME_ROLE,
        _WORKER_ROLE,
        _SECURITY_OPERATOR_ROLE,
    ):
        op.execute(
            f"REVOKE ALL ON FUNCTION {_AUDIT_PARENT_GUARD_FUNCTION}() FROM {role_name}"
        )
    op.execute(
        f"CREATE TRIGGER {_AUDIT_PARENT_GUARD_TRIGGER} "
        f"BEFORE INSERT ON public.{_AUDIT_TABLE} "
        "FOR EACH ROW "
        f"EXECUTE FUNCTION {_AUDIT_PARENT_GUARD_FUNCTION}()"
    )

    for table_name in (_RUN_TABLE, _AUDIT_TABLE, _OPERATOR_TICKET_TABLE):
        op.execute(f"REVOKE ALL ON TABLE {table_name} FROM PUBLIC")
        for role_name in (
            _CONTROL_ROLE,
            _RUNTIME_ROLE,
            _WORKER_ROLE,
            _SECURITY_OPERATOR_ROLE,
            _CONTEXT_RUN_READER_DEFINER_ROLE,
        ):
            op.execute(f"REVOKE ALL ON TABLE {table_name} FROM {role_name}")
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")

    for table_name in (_RUN_TABLE, _AUDIT_TABLE):
        operator_ticket_context = _operator_ticket_context(
            table_name,
            read_only=table_name == _AUDIT_TABLE,
        )
        op.execute(
            f"CREATE POLICY {table_name}_current_user_actor_insert "
            f"ON {table_name} AS PERMISSIVE FOR INSERT TO {_RUNTIME_ROLE} "
            f"WITH CHECK ({_current_user_actor(table_name)})"
        )
        op.execute(
            f"CREATE POLICY {table_name}_context_run_reader_definer_read "
            f"ON {table_name} AS PERMISSIVE FOR SELECT "
            f"TO {_CONTEXT_RUN_READER_DEFINER_ROLE} "
            f"USING ({operator_ticket_context})"
        )
        op.execute(
            f"CREATE POLICY {table_name}_migrator_administration "
            f"ON {table_name} AS PERMISSIVE FOR ALL TO {_MIGRATOR_ROLE} "
            "USING (true) WITH CHECK (true)"
        )
        op.execute(f"GRANT INSERT ON TABLE {table_name} TO {_RUNTIME_ROLE}")
        op.execute(
            f"GRANT SELECT ON TABLE {table_name} TO {_CONTEXT_RUN_READER_DEFINER_ROLE}"
        )

    op.execute(
        f"CREATE POLICY {_OPERATOR_TICKET_TABLE}_context_run_reader_definer_insert "
        f"ON {_OPERATOR_TICKET_TABLE} AS PERMISSIVE FOR INSERT "
        f"TO {_CONTEXT_RUN_READER_DEFINER_ROLE} "
        f"WITH CHECK ({_operator_ticket_row_context(insert=True)})"
    )
    op.execute(
        f"CREATE POLICY {_OPERATOR_TICKET_TABLE}_context_run_reader_definer_delete "
        f"ON {_OPERATOR_TICKET_TABLE} AS PERMISSIVE FOR DELETE "
        f"TO {_CONTEXT_RUN_READER_DEFINER_ROLE} "
        f"USING ({_operator_ticket_row_context()})"
    )
    op.execute(
        f"CREATE POLICY {_OPERATOR_TICKET_TABLE}_context_run_reader_definer_select "
        f"ON {_OPERATOR_TICKET_TABLE} AS PERMISSIVE FOR SELECT "
        f"TO {_CONTEXT_RUN_READER_DEFINER_ROLE} "
        f"USING ({_operator_ticket_row_context()})"
    )
    op.execute(
        f"CREATE POLICY {_OPERATOR_TICKET_TABLE}_migrator_administration "
        f"ON {_OPERATOR_TICKET_TABLE} AS PERMISSIVE FOR ALL "
        f"TO {_MIGRATOR_ROLE} USING (true) WITH CHECK (true)"
    )
    op.execute(
        f"GRANT SELECT, INSERT, DELETE ON TABLE {_OPERATOR_TICKET_TABLE} "
        f"TO {_CONTEXT_RUN_READER_DEFINER_ROLE}"
    )

    op.execute(
        f"""
        CREATE FUNCTION {_ISSUE_OPERATOR_TICKET_FUNCTION}(
            requested_ticket text,
            requested_organization_id uuid,
            requested_decision_ref text,
            requested_operator_ref text,
            requested_request_id text,
            requested_authentication_binding_ref text
        ) RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE
            ticket_digest_hex text;
            database_issued_at timestamptz;
        BEGIN
            IF session_user <> '{_CONTROL_ROLE}' THEN
                RAISE EXCEPTION USING
                    ERRCODE = '42501',
                    MESSAGE = 'ContextRun operator ticket operation was not accepted';
            END IF;
            IF requested_ticket IS NULL
               OR pg_catalog.char_length(requested_ticket) <> 64
               OR requested_ticket <> pg_catalog.lower(requested_ticket)
               OR requested_ticket !~ '^[0-9a-f]{{64}}$'
               OR requested_organization_id IS NULL
               OR requested_decision_ref IS NULL
               OR pg_catalog.btrim(requested_decision_ref) = ''
               OR requested_operator_ref IS NULL
               OR pg_catalog.btrim(requested_operator_ref) = ''
               OR requested_request_id IS NULL
               OR pg_catalog.btrim(requested_request_id) = ''
               OR requested_authentication_binding_ref IS NULL
               OR pg_catalog.btrim(requested_authentication_binding_ref) = ''
            THEN
                RETURN false;
            END IF;

            ticket_digest_hex := pg_catalog.encode(
                public.digest(
                    pg_catalog.convert_to(requested_ticket, 'UTF8'), 'sha256'
                ),
                'hex'
            );
            PERFORM pg_catalog.set_config(
                'app.context_run_operator_ticket_mode', 'issue', true
            );
            PERFORM pg_catalog.set_config(
                'app.context_run_operator_ticket_organization_id',
                requested_organization_id::text,
                true
            );
            PERFORM pg_catalog.set_config(
                'app.context_run_operator_ticket_decision_ref',
                requested_decision_ref,
                true
            );
            PERFORM pg_catalog.set_config(
                'app.context_run_operator_ticket_digest',
                ticket_digest_hex,
                true
            );
            database_issued_at := pg_catalog.statement_timestamp();

            INSERT INTO public.{_OPERATOR_TICKET_TABLE} (
                organization_id, decision_ref, ticket_digest,
                operator_ref, request_id, authentication_binding_ref,
                issued_at, expires_at
            )
            SELECT
                requested_organization_id, requested_decision_ref,
                pg_catalog.decode(ticket_digest_hex, 'hex'),
                requested_operator_ref, requested_request_id,
                requested_authentication_binding_ref,
                database_issued_at,
                database_issued_at + pg_catalog.make_interval(
                    secs => {_OPERATOR_TICKET_TTL_SECONDS}
                )
            FROM public.{_RUN_TABLE} AS requested_run
            WHERE requested_run.organization_id = requested_organization_id
              AND requested_run.decision_ref = requested_decision_ref
            ON CONFLICT DO NOTHING;

            RETURN FOUND;
        END;
        $function$
        """
    )

    op.execute(
        f"""
        CREATE FUNCTION {_REVOKE_OPERATOR_TICKET_FUNCTION}(requested_ticket text)
        RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE
            ticket_digest_hex text;
        BEGIN
            IF session_user <> '{_CONTROL_ROLE}' THEN
                RAISE EXCEPTION USING
                    ERRCODE = '42501',
                    MESSAGE = 'ContextRun operator ticket operation was not accepted';
            END IF;
            IF requested_ticket IS NULL
               OR pg_catalog.char_length(requested_ticket) <> 64
               OR requested_ticket <> pg_catalog.lower(requested_ticket)
               OR requested_ticket !~ '^[0-9a-f]{{64}}$'
            THEN
                RETURN false;
            END IF;

            ticket_digest_hex := pg_catalog.encode(
                public.digest(
                    pg_catalog.convert_to(requested_ticket, 'UTF8'), 'sha256'
                ),
                'hex'
            );
            PERFORM pg_catalog.set_config(
                'app.context_run_operator_ticket_mode', 'revoke', true
            );
            PERFORM pg_catalog.set_config(
                'app.context_run_operator_ticket_digest',
                ticket_digest_hex,
                true
            );
            DELETE FROM public.{_OPERATOR_TICKET_TABLE} AS issued_ticket
            WHERE issued_ticket.ticket_digest = pg_catalog.decode(
                ticket_digest_hex, 'hex'
            );
            RETURN FOUND;
        END;
        $function$
        """
    )

    op.execute(
        f"""
        CREATE FUNCTION {_READ_BY_OPERATOR_TICKET_FUNCTION}(
            requested_ticket text,
            requested_organization_id uuid,
            requested_decision_ref text
        ) RETURNS TABLE (
            organization_id uuid,
            run_ref text,
            decision_ref text,
            user_id uuid,
            membership_id uuid,
            membership_version bigint,
            principal_ref text,
            agent_version_ref text,
            authenticated_application_ref text,
            authentication_binding_ref text,
            request_id text,
            purpose text,
            policy_snapshot_ref text,
            policy_epoch bigint,
            effective_scope_digest text,
            query_digest_profile text,
            query_digest_key_version bigint,
            query_digest text,
            outcome text,
            package_digest_profile text,
            package_digest text,
            package_retention_mode text,
            authorized_evidence_refs jsonb,
            effective_max_tokens bigint,
            effective_max_provider_calls bigint,
            effective_max_cost_microunits bigint,
            effective_max_elapsed_ms bigint,
            usage_tokens bigint,
            usage_provider_calls bigint,
            usage_cost_microunits bigint,
            usage_elapsed_ms bigint,
            accepted_at timestamptz,
            finalized_at timestamptz,
            package_as_of timestamptz,
            package_expires_at timestamptz,
            audit_category text,
            audit_recorded_at timestamptz
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE
            ticket_digest_hex text;
            consumed boolean := false;
        BEGIN
            IF session_user <> '{_SECURITY_OPERATOR_ROLE}' THEN
                RAISE EXCEPTION USING
                    ERRCODE = '42501',
                    MESSAGE = 'ContextRun operator ticket operation was not accepted';
            END IF;
            IF requested_ticket IS NULL
               OR pg_catalog.char_length(requested_ticket) <> 64
               OR requested_ticket <> pg_catalog.lower(requested_ticket)
               OR requested_ticket !~ '^[0-9a-f]{{64}}$'
               OR requested_organization_id IS NULL
               OR requested_decision_ref IS NULL
               OR pg_catalog.btrim(requested_decision_ref) = ''
            THEN
                RETURN;
            END IF;

            ticket_digest_hex := pg_catalog.encode(
                public.digest(
                    pg_catalog.convert_to(requested_ticket, 'UTF8'), 'sha256'
                ),
                'hex'
            );
            PERFORM pg_catalog.set_config(
                'app.context_run_operator_ticket_mode', 'read', true
            );
            PERFORM pg_catalog.set_config(
                'app.context_run_operator_ticket_organization_id',
                requested_organization_id::text,
                true
            );
            PERFORM pg_catalog.set_config(
                'app.context_run_operator_ticket_decision_ref',
                requested_decision_ref,
                true
            );
            PERFORM pg_catalog.set_config(
                'app.context_run_operator_ticket_digest',
                ticket_digest_hex,
                true
            );

            DELETE FROM public.{_OPERATOR_TICKET_TABLE} AS issued_ticket
            WHERE issued_ticket.organization_id = requested_organization_id
              AND issued_ticket.decision_ref = requested_decision_ref
              AND issued_ticket.ticket_digest = pg_catalog.decode(
                  ticket_digest_hex, 'hex'
              )
            RETURNING (
                issued_ticket.expires_at > pg_catalog.statement_timestamp()
            ) INTO consumed;
            IF consumed IS DISTINCT FROM true THEN
                RETURN;
            END IF;

            RETURN QUERY
            SELECT
                run.organization_id, run.run_ref, run.decision_ref,
                run.user_id, run.membership_id, run.membership_version,
                run.principal_ref, run.agent_version_ref,
                run.authenticated_application_ref,
                run.authentication_binding_ref, run.request_id, run.purpose,
                run.policy_snapshot_ref, run.policy_epoch,
                run.effective_scope_digest, run.query_digest_profile,
                run.query_digest_key_version, run.query_digest, run.outcome,
                run.package_digest_profile, run.package_digest,
                run.package_retention_mode, run.authorized_evidence_refs,
                run.effective_max_tokens, run.effective_max_provider_calls,
                run.effective_max_cost_microunits,
                run.effective_max_elapsed_ms, run.usage_tokens,
                run.usage_provider_calls, run.usage_cost_microunits,
                run.usage_elapsed_ms, run.accepted_at, run.finalized_at,
                run.package_as_of, run.package_expires_at,
                audit.category, audit.recorded_at
            FROM public.{_RUN_TABLE} AS run
            LEFT JOIN public.{_AUDIT_TABLE} AS audit
              ON audit.organization_id = run.organization_id
             AND audit.run_ref = run.run_ref
             AND audit.decision_ref = run.decision_ref
            WHERE run.organization_id = requested_organization_id
              AND run.decision_ref = requested_decision_ref;
        END;
        $function$
        """
    )

    op.execute(f"GRANT CREATE ON SCHEMA public TO {_CONTEXT_RUN_READER_DEFINER_ROLE}")
    for function_name, signature in (
        (_ISSUE_OPERATOR_TICKET_FUNCTION, _ISSUE_OPERATOR_TICKET_SIGNATURE),
        (_REVOKE_OPERATOR_TICKET_FUNCTION, _REVOKE_OPERATOR_TICKET_SIGNATURE),
        (_READ_BY_OPERATOR_TICKET_FUNCTION, _READ_BY_OPERATOR_TICKET_SIGNATURE),
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {function_name}{signature} FROM PUBLIC")
        for role_name in (
            _CONTROL_ROLE,
            _RUNTIME_ROLE,
            _WORKER_ROLE,
            _SECURITY_OPERATOR_ROLE,
        ):
            op.execute(
                f"REVOKE ALL ON FUNCTION {function_name}{signature} FROM {role_name}"
            )

    op.execute(
        f"GRANT EXECUTE ON FUNCTION {_ISSUE_OPERATOR_TICKET_FUNCTION}"
        f"{_ISSUE_OPERATOR_TICKET_SIGNATURE} TO {_CONTROL_ROLE}"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION {_REVOKE_OPERATOR_TICKET_FUNCTION}"
        f"{_REVOKE_OPERATOR_TICKET_SIGNATURE} TO {_CONTROL_ROLE}"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION {_READ_BY_OPERATOR_TICKET_FUNCTION}"
        f"{_READ_BY_OPERATOR_TICKET_SIGNATURE} TO {_SECURITY_OPERATOR_ROLE}"
    )

    for function_name, signature in (
        (_ISSUE_OPERATOR_TICKET_FUNCTION, _ISSUE_OPERATOR_TICKET_SIGNATURE),
        (_REVOKE_OPERATOR_TICKET_FUNCTION, _REVOKE_OPERATOR_TICKET_SIGNATURE),
        (_READ_BY_OPERATOR_TICKET_FUNCTION, _READ_BY_OPERATOR_TICKET_SIGNATURE),
    ):
        op.execute(
            f"ALTER FUNCTION {function_name}{signature} "
            f"OWNER TO {_CONTEXT_RUN_READER_DEFINER_ROLE}"
        )
    op.execute(
        f"REVOKE CREATE ON SCHEMA public FROM {_CONTEXT_RUN_READER_DEFINER_ROLE}"
    )


def downgrade() -> None:
    """Remove only the Issue #19 durable decision-lineage schema."""

    for function_name, signature in (
        (_ISSUE_OPERATOR_TICKET_FUNCTION, _ISSUE_OPERATOR_TICKET_SIGNATURE),
        (_REVOKE_OPERATOR_TICKET_FUNCTION, _REVOKE_OPERATOR_TICKET_SIGNATURE),
        (_READ_BY_OPERATOR_TICKET_FUNCTION, _READ_BY_OPERATOR_TICKET_SIGNATURE),
    ):
        op.execute(f"SET LOCAL ROLE {_CONTEXT_RUN_READER_DEFINER_ROLE}")
        op.execute(f"DROP FUNCTION {function_name}{signature}")
        op.execute("RESET ROLE")
    op.drop_table(_OPERATOR_TICKET_TABLE)
    op.drop_table(_AUDIT_TABLE)
    op.execute(f"DROP FUNCTION {_AUDIT_PARENT_GUARD_FUNCTION}()")
    op.drop_table(_RUN_TABLE)
