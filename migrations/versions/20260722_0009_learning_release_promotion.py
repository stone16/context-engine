"""Add the sole Organization-scoped ContextLearning release promotion boundary.

Revision ID: 20260722_0009
Revises: 20260722_0008
Create Date: 2026-07-22

Release lineage is immutable and tenant-owned.  A fresh Organization has no
active pointer.  The only pointer/audit mutation is the generation-bound
``context_learning_promote_release`` SECURITY DEFINER function.

This migration intentionally contains no ReleaseManifest seed or bootstrap
promotion.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260722_0009"
down_revision: str | None = "20260722_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MIGRATOR_ROLE = "context_engine_migrator"
_LEARNING_ROLE = "context_engine_learning"
_RELEASE_DEFINER_ROLE = "context_engine_release_definer"
_OTHER_APPLICATION_ROLES = (
    "context_engine_control",
    "context_engine_runtime",
    "context_engine_worker",
    "context_engine_security_operator",
)

_MANIFEST_TABLE = "release_manifest"
_CANDIDATE_TABLE = "release_candidate"
_EVALUATION_TABLE = "release_evaluation"
_ACTIVE_POINTER_TABLE = "active_release_manifest"
_OPERATOR_GRANT_TABLE = "release_operator_grant"
_PROMOTION_AUDIT_TABLE = "release_promotion_audit"

_IMMUTABILITY_FUNCTION = "release_lineage_reject_mutation"
_PROMOTE_FUNCTION = "public.context_learning_promote_release"
_PROMOTE_SIGNATURE = (
    "(uuid,text,text,text,text,text,text,text,text,text,text,text,"
    "bigint,bytea,bigint,text,timestamptz,timestamptz,text,text,text)"
)
_MAX_SIGNED_BIGINT = 9_223_372_036_854_775_807


def _hex_digest(column_name: str) -> str:
    return (
        f"char_length({column_name}) = 64 "
        f"AND {column_name} = lower({column_name}) "
        f"AND {column_name} ~ '^[0-9a-f]{{64}}$'"
    )


def _bounded_ref(column_name: str) -> str:
    return (
        f"char_length({column_name}) BETWEEN 1 AND 255 "
        f"AND {column_name} = btrim({column_name}) "
        f"AND {column_name} !~ '[[:space:]]'"
    )


def _json_string_array(column_name: str) -> str:
    return (
        f"jsonb_typeof({column_name}) = 'array' "
        f"AND NOT jsonb_path_exists({column_name}, "
        "'$[*] ? (@.type() != \"string\")')"
    )


_LINEAGE_TABLES = (
    _MANIFEST_TABLE,
    _CANDIDATE_TABLE,
    _EVALUATION_TABLE,
)
_ALL_RELEASE_TABLES = (
    _MANIFEST_TABLE,
    _CANDIDATE_TABLE,
    _EVALUATION_TABLE,
    _OPERATOR_GRANT_TABLE,
    _ACTIVE_POINTER_TABLE,
    _PROMOTION_AUDIT_TABLE,
)
_PROMOTION_LOCK_TABLES = (
    _OPERATOR_GRANT_TABLE,
    _CANDIDATE_TABLE,
    _EVALUATION_TABLE,
    _MANIFEST_TABLE,
    _ACTIVE_POINTER_TABLE,
    _PROMOTION_AUDIT_TABLE,
)


def _create_manifest_table() -> None:
    op.create_table(
        _MANIFEST_TABLE,
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("manifest_ref", sa.Text(), nullable=False),
        sa.Column("manifest_digest", sa.Text(), nullable=False),
        sa.Column("lineage_digest", sa.Text(), nullable=False),
        sa.Column("content_profile_ref", sa.Text(), nullable=False),
        sa.Column("content_profile_digest", sa.Text(), nullable=False),
        sa.Column("content_schema_ref", sa.Text(), nullable=False),
        sa.Column("index_profile_ref", sa.Text(), nullable=False),
        sa.Column("index_profile_digest", sa.Text(), nullable=False),
        sa.Column("index_content_profile_digest", sa.Text(), nullable=False),
        sa.Column("index_content_schema_ref", sa.Text(), nullable=False),
        sa.Column("index_schema_ref", sa.Text(), nullable=False),
        sa.Column("runtime_profile_ref", sa.Text(), nullable=False),
        sa.Column("runtime_profile_digest", sa.Text(), nullable=False),
        sa.Column("runtime_content_profile_digest", sa.Text(), nullable=False),
        sa.Column("runtime_index_profile_digest", sa.Text(), nullable=False),
        sa.Column("runtime_content_schema_ref", sa.Text(), nullable=False),
        sa.Column("runtime_index_schema_ref", sa.Text(), nullable=False),
        sa.Column("runtime_tokenizer_ref", sa.Text(), nullable=False),
        sa.Column("runtime_package_schema_ref", sa.Text(), nullable=False),
        sa.Column("curation_profile_ref", sa.Text(), nullable=False),
        sa.Column("curation_profile_digest", sa.Text(), nullable=False),
        sa.Column("curation_mode", sa.Text(), nullable=False),
        sa.Column("curation_snapshot_ref", sa.Text(), nullable=True),
        sa.Column(
            "compatible_revision_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("curation_evaluation_digest", sa.Text(), nullable=True),
        sa.Column(
            "active_revision_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("statement_timestamp()"),
        ),
        sa.PrimaryKeyConstraint(
            "organization_id", "manifest_ref", name="pk_release_manifest"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "manifest_ref",
            "manifest_digest",
            name="uq_release_manifest_exact_digest",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.organization_id"],
            name="fk_release_manifest_organization",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            " AND ".join(
                _bounded_ref(column)
                for column in (
                    "manifest_ref",
                    "content_profile_ref",
                    "content_schema_ref",
                    "index_profile_ref",
                    "index_content_schema_ref",
                    "index_schema_ref",
                    "runtime_profile_ref",
                    "runtime_content_schema_ref",
                    "runtime_index_schema_ref",
                    "runtime_tokenizer_ref",
                    "runtime_package_schema_ref",
                    "curation_profile_ref",
                )
            ),
            name="ck_release_manifest_refs_bounded",
        ),
        sa.CheckConstraint(
            " AND ".join(
                _hex_digest(column)
                for column in (
                    "manifest_digest",
                    "lineage_digest",
                    "content_profile_digest",
                    "index_profile_digest",
                    "index_content_profile_digest",
                    "runtime_profile_digest",
                    "runtime_content_profile_digest",
                    "runtime_index_profile_digest",
                    "curation_profile_digest",
                )
            ),
            name="ck_release_manifest_digests",
        ),
        sa.CheckConstraint(
            "index_content_profile_digest = content_profile_digest "
            "AND index_content_schema_ref = content_schema_ref "
            "AND runtime_content_profile_digest = content_profile_digest "
            "AND runtime_index_profile_digest = index_profile_digest "
            "AND runtime_content_schema_ref = content_schema_ref "
            "AND runtime_index_schema_ref = index_schema_ref",
            name="ck_release_manifest_profile_compatibility",
        ),
        sa.CheckConstraint(
            _json_string_array("compatible_revision_refs")
            + " AND "
            + _json_string_array("active_revision_refs"),
            name="ck_release_manifest_revision_ref_arrays",
        ),
        sa.CheckConstraint(
            "(curation_mode = 'curation_off' "
            "AND curation_snapshot_ref IS NULL "
            "AND jsonb_array_length(compatible_revision_refs) = 0 "
            "AND curation_evaluation_digest IS NULL) OR "
            "(curation_mode = 'curation_on' "
            "AND curation_snapshot_ref IS NOT NULL "
            "AND char_length(curation_snapshot_ref) BETWEEN 1 AND 255 "
            "AND curation_snapshot_ref = btrim(curation_snapshot_ref) "
            "AND curation_snapshot_ref !~ '[[:space:]]' "
            "AND jsonb_array_length(compatible_revision_refs) > 0 "
            "AND compatible_revision_refs = active_revision_refs "
            "AND curation_evaluation_digest IS NOT NULL "
            f"AND {_hex_digest('curation_evaluation_digest')})",
            name="ck_release_manifest_curation_shape",
        ),
    )


def _gate_columns() -> list[sa.Column[str]]:
    columns: list[sa.Column[str]] = []
    for gate in ("security", "reliability", "quality", "budget"):
        columns.extend(
            [
                sa.Column[str](f"{gate}_status", sa.Text, nullable=False),
                sa.Column[str](
                    f"{gate}_evidence_digest", sa.Text, nullable=False
                ),
            ]
        )
    return columns


def _create_candidate_table() -> None:
    op.create_table(
        _CANDIDATE_TABLE,
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_ref", sa.Text(), nullable=False),
        sa.Column("candidate_digest", sa.Text(), nullable=False),
        sa.Column("manifest_ref", sa.Text(), nullable=False),
        sa.Column("manifest_digest", sa.Text(), nullable=False),
        sa.Column("expected_active_generation", sa.BigInteger(), nullable=False),
        sa.Column("expected_base_manifest_digest", sa.Text(), nullable=True),
        *_gate_columns(),
        sa.Column("capability_coverage_digest", sa.Text(), nullable=False),
        sa.Column("fixture_digest", sa.Text(), nullable=False),
        sa.Column(
            "verification_commands",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("statement_timestamp()"),
        ),
        sa.PrimaryKeyConstraint(
            "organization_id", "candidate_ref", name="pk_release_candidate"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "candidate_ref",
            "candidate_digest",
            name="uq_release_candidate_exact_digest",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "manifest_ref", "manifest_digest"],
            [
                "release_manifest.organization_id",
                "release_manifest.manifest_ref",
                "release_manifest.manifest_digest",
            ],
            name="fk_release_candidate_manifest_exact",
        ),
        sa.CheckConstraint(
            _bounded_ref("candidate_ref") + " AND " + _bounded_ref("manifest_ref"),
            name="ck_release_candidate_refs_bounded",
        ),
        sa.CheckConstraint(
            " AND ".join(
                _hex_digest(column)
                for column in (
                    "candidate_digest",
                    "manifest_digest",
                    "security_evidence_digest",
                    "reliability_evidence_digest",
                    "quality_evidence_digest",
                    "budget_evidence_digest",
                    "capability_coverage_digest",
                    "fixture_digest",
                )
            ),
            name="ck_release_candidate_digests",
        ),
        sa.CheckConstraint(
            f"expected_active_generation BETWEEN 0 AND {_MAX_SIGNED_BIGINT - 1}",
            name="ck_release_candidate_generation_incrementable",
        ),
        sa.CheckConstraint(
            "(expected_active_generation = 0 "
            "AND expected_base_manifest_digest IS NULL) OR "
            "(expected_active_generation > 0 "
            "AND expected_base_manifest_digest IS NOT NULL "
            f"AND {_hex_digest('expected_base_manifest_digest')})",
            name="ck_release_candidate_expected_base",
        ),
        sa.CheckConstraint(
            "security_status IN ('pass', 'fail') "
            "AND reliability_status IN ('pass', 'fail') "
            "AND quality_status IN ('pass', 'fail') "
            "AND budget_status IN ('pass', 'fail')",
            name="ck_release_candidate_gate_statuses",
        ),
        sa.CheckConstraint(
            _json_string_array("verification_commands")
            + " AND jsonb_array_length(verification_commands) > 0",
            name="ck_release_candidate_commands",
        ),
    )


def _create_evaluation_table() -> None:
    op.create_table(
        _EVALUATION_TABLE,
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evaluation_ref", sa.Text(), nullable=False),
        sa.Column("evaluation_digest", sa.Text(), nullable=False),
        sa.Column("candidate_ref", sa.Text(), nullable=False),
        sa.Column("candidate_digest", sa.Text(), nullable=False),
        sa.Column("manifest_ref", sa.Text(), nullable=False),
        sa.Column("manifest_digest", sa.Text(), nullable=False),
        sa.Column("expected_active_generation", sa.BigInteger(), nullable=False),
        sa.Column("expected_base_manifest_digest", sa.Text(), nullable=True),
        *_gate_columns(),
        sa.Column("compatibility_passed", sa.Boolean(), nullable=False),
        sa.Column("compatibility_evidence_digest", sa.Text(), nullable=False),
        sa.Column("capability_coverage_digest", sa.Text(), nullable=False),
        sa.Column("fixture_digest", sa.Text(), nullable=False),
        sa.Column(
            "verification_commands",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("digest_profile", sa.Text(), nullable=False),
        sa.Column("signature_profile", sa.Text(), nullable=False),
        sa.Column("signing_key_version", sa.BigInteger(), nullable=False),
        sa.Column("signature", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("statement_timestamp()"),
        ),
        sa.PrimaryKeyConstraint(
            "organization_id", "evaluation_ref", name="pk_release_evaluation"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "evaluation_ref",
            "evaluation_digest",
            name="uq_release_evaluation_exact_digest",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "candidate_ref", "candidate_digest"],
            [
                "release_candidate.organization_id",
                "release_candidate.candidate_ref",
                "release_candidate.candidate_digest",
            ],
            name="fk_release_evaluation_candidate_exact",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "manifest_ref", "manifest_digest"],
            [
                "release_manifest.organization_id",
                "release_manifest.manifest_ref",
                "release_manifest.manifest_digest",
            ],
            name="fk_release_evaluation_manifest_exact",
        ),
        sa.CheckConstraint(
            _bounded_ref("evaluation_ref")
            + " AND "
            + _bounded_ref("candidate_ref")
            + " AND "
            + _bounded_ref("manifest_ref"),
            name="ck_release_evaluation_refs_bounded",
        ),
        sa.CheckConstraint(
            " AND ".join(
                _hex_digest(column)
                for column in (
                    "evaluation_digest",
                    "candidate_digest",
                    "manifest_digest",
                    "security_evidence_digest",
                    "reliability_evidence_digest",
                    "quality_evidence_digest",
                    "budget_evidence_digest",
                    "compatibility_evidence_digest",
                    "capability_coverage_digest",
                    "fixture_digest",
                )
            ),
            name="ck_release_evaluation_digests",
        ),
        sa.CheckConstraint(
            f"expected_active_generation BETWEEN 0 AND {_MAX_SIGNED_BIGINT - 1}",
            name="ck_release_evaluation_generation_incrementable",
        ),
        sa.CheckConstraint(
            "(expected_active_generation = 0 "
            "AND expected_base_manifest_digest IS NULL) OR "
            "(expected_active_generation > 0 "
            "AND expected_base_manifest_digest IS NOT NULL "
            f"AND {_hex_digest('expected_base_manifest_digest')})",
            name="ck_release_evaluation_expected_base",
        ),
        sa.CheckConstraint(
            "security_status IN ('pass', 'fail') "
            "AND reliability_status IN ('pass', 'fail') "
            "AND quality_status IN ('pass', 'fail') "
            "AND budget_status IN ('pass', 'fail')",
            name="ck_release_evaluation_gate_statuses",
        ),
        sa.CheckConstraint(
            _json_string_array("verification_commands")
            + " AND jsonb_array_length(verification_commands) > 0",
            name="ck_release_evaluation_commands",
        ),
        sa.CheckConstraint(
            "digest_profile = 'release-evaluation-rfc8785-sha256-v1' "
            "AND signature_profile = 'release-evaluation-hmac-sha256-v1' "
            f"AND signing_key_version BETWEEN 1 AND {_MAX_SIGNED_BIGINT} "
            "AND octet_length(signature) = 32",
            name="ck_release_evaluation_signature_profile",
        ),
    )


def _create_authority_and_activation_tables() -> None:
    op.create_table(
        _OPERATOR_GRANT_TABLE,
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("authority_ref", sa.Text(), nullable=False),
        sa.Column("authority_digest", sa.Text(), nullable=False),
        sa.Column("operator_ref", sa.Text(), nullable=False),
        sa.Column("authentication_binding_ref", sa.Text(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint(
            "organization_id", "authority_ref", name="pk_release_operator_grant"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "authority_ref",
            "authority_digest",
            name="uq_release_operator_grant_exact_digest",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.organization_id"],
            name="fk_release_operator_grant_organization",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            _bounded_ref("authority_ref")
            + " AND "
            + _bounded_ref("operator_ref")
            + " AND "
            + _bounded_ref("authentication_binding_ref"),
            name="ck_release_operator_grant_refs_bounded",
        ),
        sa.CheckConstraint(
            _hex_digest("authority_digest"),
            name="ck_release_operator_grant_digest",
        ),
        sa.CheckConstraint(
            "expires_at > valid_from "
            "AND (revoked_at IS NULL OR revoked_at >= valid_from)",
            name="ck_release_operator_grant_lifetime",
        ),
    )
    op.create_table(
        _ACTIVE_POINTER_TABLE,
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("active_generation", sa.BigInteger(), nullable=False),
        sa.Column("manifest_ref", sa.Text(), nullable=False),
        sa.Column("manifest_digest", sa.Text(), nullable=False),
        sa.Column("promotion_ref", sa.Text(), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id", name="pk_active_release_manifest"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "promotion_ref",
            name="uq_active_release_manifest_promotion",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "manifest_ref", "manifest_digest"],
            [
                "release_manifest.organization_id",
                "release_manifest.manifest_ref",
                "release_manifest.manifest_digest",
            ],
            name="fk_active_release_manifest_exact",
        ),
        sa.CheckConstraint(
            f"active_generation BETWEEN 1 AND {_MAX_SIGNED_BIGINT}",
            name="ck_active_release_manifest_generation",
        ),
        sa.CheckConstraint(
            _bounded_ref("manifest_ref")
            + " AND "
            + _bounded_ref("promotion_ref")
            + " AND "
            + _hex_digest("manifest_digest"),
            name="ck_active_release_manifest_bindings",
        ),
    )
    op.create_table(
        _PROMOTION_AUDIT_TABLE,
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("active_generation", sa.BigInteger(), nullable=False),
        sa.Column("promotion_ref", sa.Text(), nullable=False),
        sa.Column("operator_ref", sa.Text(), nullable=False),
        sa.Column("authentication_binding_ref", sa.Text(), nullable=False),
        sa.Column("authority_ref", sa.Text(), nullable=False),
        sa.Column("authority_digest", sa.Text(), nullable=False),
        sa.Column("candidate_ref", sa.Text(), nullable=False),
        sa.Column("candidate_digest", sa.Text(), nullable=False),
        sa.Column("manifest_ref", sa.Text(), nullable=False),
        sa.Column("manifest_digest", sa.Text(), nullable=False),
        sa.Column("evaluation_ref", sa.Text(), nullable=False),
        sa.Column("evaluation_digest", sa.Text(), nullable=False),
        sa.Column("expected_active_generation", sa.BigInteger(), nullable=False),
        sa.Column("expected_base_manifest_digest", sa.Text(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("audit_reason_digest", sa.Text(), nullable=False),
        sa.Column("promotion_call_digest", sa.Text(), nullable=False),
        sa.Column("call_issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("call_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "active_generation",
            name="pk_release_promotion_audit",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "promotion_ref",
            name="uq_release_promotion_audit_ref",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "candidate_ref", "candidate_digest"],
            [
                "release_candidate.organization_id",
                "release_candidate.candidate_ref",
                "release_candidate.candidate_digest",
            ],
            name="fk_release_promotion_audit_candidate_exact",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "manifest_ref", "manifest_digest"],
            [
                "release_manifest.organization_id",
                "release_manifest.manifest_ref",
                "release_manifest.manifest_digest",
            ],
            name="fk_release_promotion_audit_manifest_exact",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "evaluation_ref", "evaluation_digest"],
            [
                "release_evaluation.organization_id",
                "release_evaluation.evaluation_ref",
                "release_evaluation.evaluation_digest",
            ],
            name="fk_release_promotion_audit_evaluation_exact",
        ),
        sa.CheckConstraint(
            "active_generation = expected_active_generation + 1 "
            f"AND active_generation BETWEEN 1 AND {_MAX_SIGNED_BIGINT}",
            name="ck_release_promotion_audit_generation",
        ),
        sa.CheckConstraint(
            "call_expires_at > call_issued_at AND promoted_at >= call_issued_at",
            name="ck_release_promotion_audit_lifetime",
        ),
        sa.CheckConstraint(
            " AND ".join(
                _bounded_ref(column)
                for column in (
                    "promotion_ref",
                    "operator_ref",
                    "authentication_binding_ref",
                    "authority_ref",
                    "candidate_ref",
                    "manifest_ref",
                    "evaluation_ref",
                    "request_id",
                )
            ),
            name="ck_release_promotion_audit_refs_bounded",
        ),
        sa.CheckConstraint(
            " AND ".join(
                _hex_digest(column)
                for column in (
                    "authority_digest",
                    "candidate_digest",
                    "manifest_digest",
                    "evaluation_digest",
                    "audit_reason_digest",
                    "promotion_call_digest",
                )
            ),
            name="ck_release_promotion_audit_digests",
        ),
        sa.CheckConstraint(
            "(expected_active_generation = 0 "
            "AND expected_base_manifest_digest IS NULL) OR "
            "(expected_active_generation > 0 "
            "AND expected_base_manifest_digest IS NOT NULL "
            f"AND {_hex_digest('expected_base_manifest_digest')})",
            name="ck_release_promotion_audit_expected_base",
        ),
    )


def _create_release_security() -> None:
    op.execute(
        f"""
        CREATE FUNCTION public.{_IMMUTABILITY_FUNCTION}()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog
        AS $function$
        BEGIN
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                MESSAGE = 'release lineage and promotion audit are immutable';
        END;
        $function$
        """
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION public.{_IMMUTABILITY_FUNCTION}() FROM PUBLIC"
    )
    for table_name in (*_LINEAGE_TABLES, _PROMOTION_AUDIT_TABLE):
        op.execute(
            f"CREATE TRIGGER {table_name}_reject_mutation "
            f"BEFORE UPDATE OR DELETE ON public.{table_name} FOR EACH ROW "
            f"EXECUTE FUNCTION public.{_IMMUTABILITY_FUNCTION}()"
        )

    for table_name in _ALL_RELEASE_TABLES:
        op.execute(f"REVOKE ALL ON TABLE public.{table_name} FROM PUBLIC")
        for role_name in (
            _LEARNING_ROLE,
            _RELEASE_DEFINER_ROLE,
            *_OTHER_APPLICATION_ROLES,
        ):
            op.execute(
                f"REVOKE ALL ON TABLE public.{table_name} FROM {role_name}"
            )
        op.execute(f"ALTER TABLE public.{table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table_name} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table_name}_migrator_administration "
            f"ON public.{table_name} AS PERMISSIVE FOR ALL TO {_MIGRATOR_ROLE} "
            "USING (true) WITH CHECK (true)"
        )

    tenant_expression = (
        "organization_id = NULLIF(current_setting("
        "'app.organization_id', true), '')::uuid"
    )
    for table_name in _LINEAGE_TABLES:
        op.execute(
            f"CREATE POLICY {table_name}_learning_insert "
            f"ON public.{table_name} AS PERMISSIVE FOR INSERT TO {_LEARNING_ROLE} "
            f"WITH CHECK ({tenant_expression})"
        )
        op.execute(
            f"CREATE POLICY {table_name}_learning_select "
            f"ON public.{table_name} AS PERMISSIVE FOR SELECT TO {_LEARNING_ROLE} "
            f"USING ({tenant_expression})"
        )
        op.execute(
            f"GRANT SELECT, INSERT ON TABLE public.{table_name} TO {_LEARNING_ROLE}"
        )

    for table_name in _ALL_RELEASE_TABLES:
        op.execute(
            f"CREATE POLICY {table_name}_release_definer "
            f"ON public.{table_name} AS PERMISSIVE FOR ALL "
            f"TO {_RELEASE_DEFINER_ROLE} USING ({tenant_expression}) "
            f"WITH CHECK ({tenant_expression})"
        )
    op.execute(
        "GRANT SELECT ON TABLE public.release_manifest, public.release_candidate, "
        "public.release_evaluation, public.release_operator_grant, "
        "public.active_release_manifest TO context_engine_release_definer"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON TABLE public.active_release_manifest "
        "TO context_engine_release_definer"
    )
    op.execute(
        "GRANT SELECT, INSERT ON TABLE public.release_promotion_audit "
        "TO context_engine_release_definer"
    )


def _create_promote_function() -> None:
    op.execute(
        f"""
        CREATE FUNCTION {_PROMOTE_FUNCTION}(
            requested_organization_id uuid,
            requested_promotion_ref text,
            requested_operator_ref text,
            requested_authentication_binding_ref text,
            requested_authority_ref text,
            requested_authority_digest text,
            requested_candidate_ref text,
            requested_candidate_digest text,
            requested_manifest_ref text,
            requested_manifest_digest text,
            requested_evaluation_ref text,
            requested_evaluation_digest text,
            requested_evaluation_signing_key_version bigint,
            requested_evaluation_signature bytea,
            requested_expected_active_generation bigint,
            requested_expected_base_manifest_digest text,
            requested_issued_at timestamptz,
            requested_expires_at timestamptz,
            requested_request_id text,
            requested_audit_reason_digest text,
            requested_promotion_call_digest text
        ) RETURNS TABLE (
            promotion_ref text,
            active_generation bigint,
            manifest_ref text,
            manifest_digest text,
            promoted_at timestamptz
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on
        AS $function$
        DECLARE
            database_promoted_at timestamptz;
            current_generation bigint;
            current_manifest_digest text;
            next_generation bigint;
            candidate_row public.release_candidate%ROWTYPE;
            evaluation_row public.release_evaluation%ROWTYPE;
        BEGIN
            IF SESSION_USER <> '{_LEARNING_ROLE}' THEN
                RAISE EXCEPTION USING
                    ERRCODE = '42501',
                    MESSAGE = 'release promotion was not accepted';
            END IF;
            IF requested_organization_id IS NULL
               OR requested_promotion_ref IS NULL
               OR requested_operator_ref IS NULL
               OR requested_authentication_binding_ref IS NULL
               OR requested_authority_ref IS NULL
               OR requested_authority_digest IS NULL
               OR requested_candidate_ref IS NULL
               OR requested_candidate_digest IS NULL
               OR requested_manifest_ref IS NULL
               OR requested_manifest_digest IS NULL
               OR requested_evaluation_ref IS NULL
               OR requested_evaluation_digest IS NULL
               OR requested_evaluation_signing_key_version IS NULL
               OR requested_evaluation_signature IS NULL
               OR requested_expected_active_generation IS NULL
               OR requested_issued_at IS NULL
               OR requested_expires_at IS NULL
               OR requested_request_id IS NULL
               OR requested_audit_reason_digest IS NULL
               OR requested_promotion_call_digest IS NULL
            THEN
                RAISE EXCEPTION USING
                    ERRCODE = '42501',
                    MESSAGE = 'release promotion was not accepted';
            END IF;

            PERFORM pg_catalog.set_config(
                'app.organization_id', requested_organization_id::text, true
            );
            database_promoted_at := pg_catalog.statement_timestamp();
            PERFORM pg_catalog.pg_advisory_xact_lock(
                pg_catalog.hashtextextended(
                    'context-engine.release:' || requested_organization_id::text,
                    0
                )
            );

            IF database_promoted_at < requested_issued_at
               OR database_promoted_at >= requested_expires_at
               OR requested_expires_at <= requested_issued_at
               OR requested_expected_active_generation < 0
               OR requested_expected_active_generation >= {_MAX_SIGNED_BIGINT}
               OR (requested_expected_active_generation = 0
                   AND requested_expected_base_manifest_digest IS NOT NULL)
               OR (requested_expected_active_generation > 0
                   AND requested_expected_base_manifest_digest IS NULL)
               OR NOT EXISTS (
                    SELECT 1
                    FROM public.release_operator_grant AS grant_row
                    WHERE grant_row.organization_id = requested_organization_id
                      AND grant_row.authority_ref = requested_authority_ref
                      AND grant_row.authority_digest = requested_authority_digest
                      AND grant_row.operator_ref = requested_operator_ref
                      AND grant_row.authentication_binding_ref =
                          requested_authentication_binding_ref
                      AND grant_row.valid_from <= database_promoted_at
                      AND grant_row.expires_at > database_promoted_at
                      AND grant_row.revoked_at IS NULL
               )
            THEN
                RAISE EXCEPTION USING
                    ERRCODE = '42501',
                    MESSAGE = 'release promotion was not accepted';
            END IF;

            SELECT candidate.*
            INTO candidate_row
            FROM public.release_candidate AS candidate
            WHERE candidate.organization_id = requested_organization_id
              AND candidate.candidate_ref = requested_candidate_ref
              AND candidate.candidate_digest = requested_candidate_digest;
            IF NOT FOUND
               OR candidate_row.manifest_ref <> requested_manifest_ref
               OR candidate_row.manifest_digest <> requested_manifest_digest
               OR candidate_row.expected_active_generation <>
                  requested_expected_active_generation
               OR candidate_row.expected_base_manifest_digest IS DISTINCT FROM
                  requested_expected_base_manifest_digest
               OR candidate_row.security_status <> 'pass'
               OR candidate_row.reliability_status <> 'pass'
               OR candidate_row.quality_status <> 'pass'
               OR candidate_row.budget_status <> 'pass'
            THEN
                RAISE EXCEPTION USING
                    ERRCODE = '42501',
                    MESSAGE = 'release promotion was not accepted';
            END IF;

            SELECT evaluation.*
            INTO evaluation_row
            FROM public.release_evaluation AS evaluation
            WHERE evaluation.organization_id = requested_organization_id
              AND evaluation.evaluation_ref = requested_evaluation_ref
              AND evaluation.evaluation_digest = requested_evaluation_digest;
            IF NOT FOUND
               OR evaluation_row.candidate_ref <> requested_candidate_ref
               OR evaluation_row.candidate_digest <> requested_candidate_digest
               OR evaluation_row.manifest_ref <> requested_manifest_ref
               OR evaluation_row.manifest_digest <> requested_manifest_digest
               OR evaluation_row.expected_active_generation <>
                  requested_expected_active_generation
               OR evaluation_row.expected_base_manifest_digest IS DISTINCT FROM
                  requested_expected_base_manifest_digest
               OR evaluation_row.security_status <> 'pass'
               OR evaluation_row.reliability_status <> 'pass'
               OR evaluation_row.quality_status <> 'pass'
               OR evaluation_row.budget_status <> 'pass'
               OR evaluation_row.security_status <>
                  candidate_row.security_status
               OR evaluation_row.security_evidence_digest <>
                  candidate_row.security_evidence_digest
               OR evaluation_row.reliability_status <>
                  candidate_row.reliability_status
               OR evaluation_row.reliability_evidence_digest <>
                  candidate_row.reliability_evidence_digest
               OR evaluation_row.quality_status <>
                  candidate_row.quality_status
               OR evaluation_row.quality_evidence_digest <>
                  candidate_row.quality_evidence_digest
               OR evaluation_row.budget_status <>
                  candidate_row.budget_status
               OR evaluation_row.budget_evidence_digest <>
                  candidate_row.budget_evidence_digest
               OR evaluation_row.compatibility_passed IS NOT TRUE
               OR evaluation_row.compatibility_evidence_digest IS NULL
               OR evaluation_row.evaluated_at IS NULL
               OR evaluation_row.digest_profile <>
                  'release-evaluation-rfc8785-sha256-v1'
               OR evaluation_row.signature_profile <>
                  'release-evaluation-hmac-sha256-v1'
               OR evaluation_row.signing_key_version <>
                  requested_evaluation_signing_key_version
               OR evaluation_row.signature <> requested_evaluation_signature
               OR evaluation_row.capability_coverage_digest <>
                  candidate_row.capability_coverage_digest
               OR evaluation_row.fixture_digest <> candidate_row.fixture_digest
               OR evaluation_row.verification_commands <>
                  candidate_row.verification_commands
               OR NOT EXISTS (
                    SELECT 1
                    FROM public.release_manifest AS manifest_row
                    WHERE manifest_row.organization_id =
                          requested_organization_id
                      AND manifest_row.manifest_ref = requested_manifest_ref
                      AND manifest_row.manifest_digest =
                          requested_manifest_digest
                      AND manifest_row.curation_mode = 'curation_off'
               )
            THEN
                RAISE EXCEPTION USING
                    ERRCODE = '42501',
                    MESSAGE = 'release promotion was not accepted';
            END IF;

            SELECT pointer.active_generation, pointer.manifest_digest
            INTO current_generation, current_manifest_digest
            FROM public.active_release_manifest AS pointer
            WHERE pointer.organization_id = requested_organization_id
            FOR UPDATE;
            IF NOT FOUND THEN
                current_generation := 0;
                current_manifest_digest := NULL;
            END IF;
            IF current_generation <> requested_expected_active_generation
               OR current_manifest_digest IS DISTINCT FROM
                  requested_expected_base_manifest_digest
            THEN
                RAISE EXCEPTION USING
                    ERRCODE = '40001',
                    MESSAGE = 'release promotion was not accepted';
            END IF;

            next_generation := requested_expected_active_generation + 1;
            INSERT INTO public.active_release_manifest (
                organization_id, active_generation, manifest_ref,
                manifest_digest, promotion_ref, activated_at
            ) VALUES (
                requested_organization_id, next_generation,
                requested_manifest_ref, requested_manifest_digest,
                requested_promotion_ref, database_promoted_at
            )
            ON CONFLICT (organization_id) DO UPDATE
            SET active_generation = EXCLUDED.active_generation,
                manifest_ref = EXCLUDED.manifest_ref,
                manifest_digest = EXCLUDED.manifest_digest,
                promotion_ref = EXCLUDED.promotion_ref,
                activated_at = EXCLUDED.activated_at
            WHERE active_release_manifest.active_generation =
                    requested_expected_active_generation
              AND active_release_manifest.manifest_digest IS NOT DISTINCT FROM
                    requested_expected_base_manifest_digest;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING
                    ERRCODE = '40001',
                    MESSAGE = 'release promotion was not accepted';
            END IF;

            INSERT INTO public.release_promotion_audit (
                organization_id, active_generation, promotion_ref,
                operator_ref, authentication_binding_ref, authority_ref,
                authority_digest, candidate_ref, candidate_digest,
                manifest_ref, manifest_digest, evaluation_ref,
                evaluation_digest, expected_active_generation,
                expected_base_manifest_digest, request_id,
                audit_reason_digest, promotion_call_digest,
                call_issued_at, call_expires_at, promoted_at
            ) VALUES (
                requested_organization_id, next_generation,
                requested_promotion_ref, requested_operator_ref,
                requested_authentication_binding_ref, requested_authority_ref,
                requested_authority_digest, requested_candidate_ref,
                requested_candidate_digest, requested_manifest_ref,
                requested_manifest_digest, requested_evaluation_ref,
                requested_evaluation_digest,
                requested_expected_active_generation,
                requested_expected_base_manifest_digest, requested_request_id,
                requested_audit_reason_digest, requested_promotion_call_digest,
                requested_issued_at, requested_expires_at, database_promoted_at
            );

            promotion_ref := requested_promotion_ref;
            active_generation := next_generation;
            manifest_ref := requested_manifest_ref;
            manifest_digest := requested_manifest_digest;
            promoted_at := database_promoted_at;
            RETURN NEXT;
        END;
        $function$
        """
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION {_PROMOTE_FUNCTION}{_PROMOTE_SIGNATURE} "
        "FROM PUBLIC"
    )
    for role_name in (_LEARNING_ROLE, *_OTHER_APPLICATION_ROLES):
        op.execute(
            f"REVOKE ALL ON FUNCTION {_PROMOTE_FUNCTION}{_PROMOTE_SIGNATURE} "
            f"FROM {role_name}"
        )
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_RELEASE_DEFINER_ROLE}")
    op.execute(
        f"ALTER FUNCTION {_PROMOTE_FUNCTION}{_PROMOTE_SIGNATURE} "
        f"OWNER TO {_RELEASE_DEFINER_ROLE}"
    )
    op.execute(f"SET LOCAL ROLE {_RELEASE_DEFINER_ROLE}")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION {_PROMOTE_FUNCTION}{_PROMOTE_SIGNATURE} "
        f"TO {_LEARNING_ROLE}"
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_RELEASE_DEFINER_ROLE}")


def _require_expected_roles() -> None:
    """Fail closed instead of creating credential roles inside a migration."""

    connection = op.get_bind()
    missing = connection.execute(
        sa.text(
            """
            SELECT expected_role
            FROM unnest(CAST(:expected_roles AS text[])) AS expected_role
            WHERE NOT EXISTS (
                SELECT 1
                FROM pg_catalog.pg_roles AS configured_role
                WHERE configured_role.rolname = expected_role
            )
            ORDER BY expected_role
            """
        ),
        {
            "expected_roles": [
                _LEARNING_ROLE,
                _RELEASE_DEFINER_ROLE,
            ]
        },
    ).scalars()
    missing_roles = list(missing)
    if missing_roles:
        raise RuntimeError(
            "Issue #49 requires provisioned PostgreSQL roles: "
            + ", ".join(missing_roles)
        )


def upgrade() -> None:
    """Create the release lineage and sole atomic promotion boundary."""

    _require_expected_roles()
    _create_manifest_table()
    _create_candidate_table()
    _create_evaluation_table()
    _create_authority_and_activation_tables()
    _create_release_security()
    _create_promote_function()


def downgrade() -> None:
    """Serialize with promotion and refuse every populated release-state row."""

    connection = op.get_bind()
    connection.execute(
        sa.text(
            "LOCK TABLE "
            + ", ".join(
                f"public.{table_name}" for table_name in _PROMOTION_LOCK_TABLES
            )
            + " IN ACCESS EXCLUSIVE MODE"
        )
    )
    populated = [
        table_name
        for table_name in _ALL_RELEASE_TABLES
        if connection.execute(
            sa.text(f"SELECT EXISTS (SELECT 1 FROM public.{table_name})")
        ).scalar_one()
    ]
    if populated:
        raise RuntimeError(
            "Issue #49 downgrade requires empty release state; use a forward fix "
            "for release authority or lineage"
        )
    op.execute(f"SET LOCAL ROLE {_RELEASE_DEFINER_ROLE}")
    op.execute(f"DROP FUNCTION {_PROMOTE_FUNCTION}{_PROMOTE_SIGNATURE}")
    op.execute("RESET ROLE")
    for table_name in reversed(_ALL_RELEASE_TABLES):
        op.drop_table(table_name)
    op.execute(f"DROP FUNCTION public.{_IMMUTABILITY_FUNCTION}()")
