"""Add preview-bound bulk Article policy administration.

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

_MIGRATOR = "context_engine_migrator"
_CONTROL = "context_engine_control"
_ACCESS_DEFINER = "context_engine_access_policy_definer"
_RUNTIME = "context_engine_runtime"
_WORKER = "context_engine_worker"
_MAX = 2**63 - 1
_AUDIT_TABLE = "bulk_article_policy_change_audit"
_PREVIEW_FUNCTION = "context_control_preview_bulk_article_policy_change"
_PREVIEW_SIGNATURE = "(uuid,text[],text,text[])"
_FUNCTION = "context_control_bulk_change_article_policy"
_SIGNATURE = "(uuid,text[],bigint[],text,text[],text,text,text,text)"


def upgrade() -> None:
    """Create one audited atomic path for historical Article policy changes."""

    op.create_table(
        _AUDIT_TABLE,
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "audit_ref",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("policy_epoch", sa.BigInteger(), nullable=False),
        sa.Column("article_count", sa.BigInteger(), nullable=False),
        sa.Column("preview_digest", sa.Text(), nullable=False),
        sa.Column("target_policy_digest", sa.Text(), nullable=False),
        sa.Column("operator_digest", sa.Text(), nullable=False),
        sa.Column("authority_digest", sa.Text(), nullable=False),
        sa.Column("request_digest", sa.Text(), nullable=False),
        sa.Column("reason_category", sa.Text(), nullable=False),
        sa.Column(
            "committed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("statement_timestamp()"),
        ),
        sa.PrimaryKeyConstraint(
            "organization_id", "audit_ref", name="pk_bulk_article_policy_change_audit"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.organization_id"],
            name="fk_bulk_article_policy_change_audit_organization",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            f"policy_epoch BETWEEN 2 AND {_MAX}",
            name="ck_bulk_article_policy_change_audit_epoch",
        ),
        sa.CheckConstraint(
            "article_count BETWEEN 1 AND 1000",
            name="ck_bulk_article_policy_change_audit_count",
        ),
        sa.CheckConstraint(
            "preview_digest ~ '^[0-9a-f]{64}$' "
            "AND target_policy_digest ~ '^[0-9a-f]{64}$' "
            "AND operator_digest ~ '^[0-9a-f]{64}$' "
            "AND authority_digest ~ '^[0-9a-f]{64}$' "
            "AND request_digest ~ '^[0-9a-f]{64}$'",
            name="ck_bulk_article_policy_change_audit_digests",
        ),
        sa.CheckConstraint(
            "reason_category = 'operator_confirmed_visibility_change'",
            name="ck_bulk_article_policy_change_audit_reason",
        ),
    )
    op.execute(f"REVOKE ALL ON TABLE {_AUDIT_TABLE} FROM PUBLIC")
    for role in (_CONTROL, _RUNTIME, _WORKER):
        op.execute(f"REVOKE ALL ON TABLE {_AUDIT_TABLE} FROM {role}")
    op.execute(f"ALTER TABLE {_AUDIT_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_AUDIT_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {_AUDIT_TABLE}_migrator_administration "
        f"ON {_AUDIT_TABLE} FOR ALL TO {_MIGRATOR} "
        "USING (true) WITH CHECK (true)"
    )
    op.execute(
        f"CREATE POLICY {_AUDIT_TABLE}_access_definer_insert "
        f"ON {_AUDIT_TABLE} FOR INSERT TO {_ACCESS_DEFINER} WITH CHECK ("
        "organization_id = NULLIF("
        "current_setting('app.organization_id', true), '')::uuid)"
    )
    op.execute(f"GRANT INSERT ON TABLE {_AUDIT_TABLE} TO {_ACCESS_DEFINER}")

    op.execute(
        f"""
        CREATE FUNCTION public.{_PREVIEW_FUNCTION}(
            requested_organization_id uuid,
            requested_resource_refs text[],
            requested_policy_kind text,
            requested_group_refs text[]
        ) RETURNS TABLE(
            resource_ref text,
            policy_version bigint,
            policy_kind text,
            group_refs text[],
            published boolean,
            resolution_rung text
        ) LANGUAGE plpgsql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        SET row_security = on
        AS $function$
        BEGIN
            IF SESSION_USER <> '{_CONTROL}'
               OR requested_organization_id IS NULL
               OR NULLIF(current_setting('app.organization_id', true), '')::uuid
                    IS DISTINCT FROM requested_organization_id
               OR requested_resource_refs IS NULL
               OR requested_group_refs IS NULL
               OR cardinality(requested_resource_refs) NOT BETWEEN 1 AND 1000
               OR EXISTS (
                    SELECT 1
                    FROM unnest(requested_resource_refs) WITH ORDINALITY
                         requested(value, ordinal)
                    WHERE requested.value IS NULL
                       OR btrim(requested.value) = ''
                       OR char_length(requested.value) > 512
                       OR requested.value ~ '[[:space:]]'
                       OR (requested.ordinal > 1 AND requested.value <=
                            requested_resource_refs[requested.ordinal - 1])
               )
               OR NOT (
                    (requested_policy_kind IN ('private', 'organization')
                     AND cardinality(requested_group_refs) = 0)
                    OR (requested_policy_kind = 'groups'
                        AND cardinality(requested_group_refs) BETWEEN 1 AND 1000)
               )
               OR EXISTS (
                    SELECT 1
                    FROM unnest(requested_group_refs) WITH ORDINALITY
                         requested(group_ref, ordinal)
                    WHERE requested.group_ref IS NULL
                       OR btrim(requested.group_ref) = ''
                       OR char_length(requested.group_ref) > 256
                       OR requested.group_ref ~ '[[:space:]]'
                       OR (requested.ordinal > 1 AND requested.group_ref <=
                            requested_group_refs[requested.ordinal - 1])
                       OR NOT EXISTS (
                            SELECT 1
                            FROM public.article_access_group AS owned_group
                            WHERE owned_group.organization_id =
                                  requested_organization_id
                              AND owned_group.group_ref = requested.group_ref
                       )
               )
            THEN
                RAISE EXCEPTION USING ERRCODE = 'P0001',
                    MESSAGE = 'bulk Article policy preview was not accepted';
            END IF;
            RETURN QUERY
            SELECT policy.resource_ref, policy.policy_version,
                   policy.policy_kind, policy.group_refs, policy.published,
                   policy.resolution_rung
            FROM public.article_access_policy AS policy
            WHERE policy.organization_id = requested_organization_id
              AND policy.resource_ref = ANY(requested_resource_refs)
            ORDER BY policy.resource_ref;
        END;
        $function$
        """
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION public.{_PREVIEW_FUNCTION}"
        f"{_PREVIEW_SIGNATURE} FROM PUBLIC"
    )
    for role in (_RUNTIME, _WORKER):
        op.execute(
            f"REVOKE ALL ON FUNCTION public.{_PREVIEW_FUNCTION}"
            f"{_PREVIEW_SIGNATURE} FROM {role}"
        )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_PREVIEW_FUNCTION}"
        f"{_PREVIEW_SIGNATURE} TO {_CONTROL}"
    )
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_ACCESS_DEFINER}")
    op.execute(
        f"ALTER FUNCTION public.{_PREVIEW_FUNCTION}{_PREVIEW_SIGNATURE} "
        f"OWNER TO {_ACCESS_DEFINER}"
    )
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_ACCESS_DEFINER}")

    op.execute(
        f"""
        CREATE FUNCTION public.{_FUNCTION}(
            requested_organization_id uuid,
            requested_resource_refs text[],
            expected_policy_versions bigint[],
            requested_policy_kind text,
            requested_group_refs text[],
            requested_preview_digest text,
            requested_operator_ref text,
            requested_authority_ref text,
            requested_request_id text
        ) RETURNS TABLE(next_epoch bigint, audit_ref uuid, changed_articles bigint)
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        SET row_security = on
        AS $function$
        DECLARE
            current_epoch bigint;
            current_count bigint;
            updated_count bigint;
            generated_audit_ref uuid;
            target_policy_digest text;
        BEGIN
            IF SESSION_USER <> '{_CONTROL}'
               OR requested_organization_id IS NULL
               OR NULLIF(current_setting('app.organization_id', true), '')::uuid
                    IS DISTINCT FROM requested_organization_id
               OR requested_resource_refs IS NULL
               OR expected_policy_versions IS NULL
               OR requested_group_refs IS NULL
               OR cardinality(requested_resource_refs) NOT BETWEEN 1 AND 1000
               OR cardinality(expected_policy_versions)
                    <> cardinality(requested_resource_refs)
               OR EXISTS (
                    SELECT 1
                    FROM unnest(
                        requested_resource_refs, expected_policy_versions
                    ) WITH ORDINALITY requested(resource_ref, policy_version, ordinal)
                    WHERE requested.resource_ref IS NULL
                       OR btrim(requested.resource_ref) = ''
                       OR char_length(requested.resource_ref) > 512
                       OR requested.resource_ref ~ '[[:space:]]'
                       OR requested.policy_version NOT BETWEEN 1 AND {_MAX - 1}
                       OR (requested.ordinal > 1 AND requested.resource_ref <=
                            requested_resource_refs[requested.ordinal - 1])
               )
               OR NOT (
                    (requested_policy_kind IN ('private', 'organization')
                     AND cardinality(requested_group_refs) = 0)
                    OR (requested_policy_kind = 'groups'
                        AND cardinality(requested_group_refs) BETWEEN 1 AND 1000)
               )
               OR EXISTS (
                    SELECT 1
                    FROM unnest(requested_group_refs) WITH ORDINALITY
                         requested(group_ref, ordinal)
                    WHERE requested.group_ref IS NULL
                       OR btrim(requested.group_ref) = ''
                       OR char_length(requested.group_ref) > 256
                       OR requested.group_ref ~ '[[:space:]]'
                       OR (requested.ordinal > 1 AND requested.group_ref <=
                            requested_group_refs[requested.ordinal - 1])
                       OR NOT EXISTS (
                            SELECT 1
                            FROM public.article_access_group AS owned_group
                            WHERE owned_group.organization_id =
                                  requested_organization_id
                              AND owned_group.group_ref = requested.group_ref
                       )
               )
               OR requested_preview_digest !~ '^[0-9a-f]{{64}}$'
               OR requested_operator_ref IS NULL
               OR btrim(requested_operator_ref) = ''
               OR char_length(requested_operator_ref) > 256
               OR requested_authority_ref IS NULL
               OR btrim(requested_authority_ref) = ''
               OR char_length(requested_authority_ref) > 256
               OR requested_request_id IS NULL
               OR btrim(requested_request_id) = ''
               OR char_length(requested_request_id) > 256
            THEN
                RAISE EXCEPTION USING ERRCODE = 'P0001',
                    MESSAGE = 'bulk Article policy change was not accepted';
            END IF;

            SELECT epoch.policy_epoch INTO current_epoch
            FROM public.organization_policy_epoch AS epoch
            WHERE epoch.organization_id = requested_organization_id
            FOR UPDATE;
            IF current_epoch IS NULL OR current_epoch >= {_MAX} THEN
                RAISE EXCEPTION USING ERRCODE = 'P0001',
                    MESSAGE = 'bulk Article policy change was not accepted';
            END IF;

            PERFORM 1
            FROM public.article_access_policy AS policy
            WHERE policy.organization_id = requested_organization_id
              AND policy.resource_ref = ANY(requested_resource_refs)
            ORDER BY policy.resource_ref
            FOR UPDATE;

            SELECT count(*) INTO current_count
            FROM public.article_access_policy AS policy
            JOIN unnest(requested_resource_refs, expected_policy_versions)
                 AS requested(resource_ref, policy_version)
              ON requested.resource_ref = policy.resource_ref
             AND requested.policy_version = policy.policy_version
            JOIN public.article_source_acl_observation AS observation
              ON observation.organization_id = policy.organization_id
             AND observation.resource_ref = policy.resource_ref
            WHERE policy.organization_id = requested_organization_id
              AND policy.policy_version < {_MAX}
              AND observation.evidence_mode = 'mirrored'
              AND observation.observation_status = 'resolved'
              AND (
                    requested_policy_kind = 'private'
                    OR observation.policy_kind = 'organization'
                    OR (
                        requested_policy_kind = 'groups'
                        AND observation.policy_kind = 'groups'
                        AND requested_group_refs <@ observation.group_refs
                    )
              );
            IF current_count <> cardinality(requested_resource_refs) THEN
                RAISE EXCEPTION USING ERRCODE = 'P0001',
                    MESSAGE = 'bulk Article policy change was not accepted';
            END IF;

            next_epoch := current_epoch + 1;
            UPDATE public.article_access_policy AS policy
            SET policy_version = policy.policy_version + 1,
                local_policy_kind = requested_policy_kind,
                local_group_refs = requested_group_refs,
                policy_kind = requested_policy_kind,
                group_refs = requested_group_refs,
                published = true,
                resolution_rung = 'explicit_article',
                fixed_at_policy_epoch = next_epoch
            FROM unnest(requested_resource_refs, expected_policy_versions)
                 AS requested(resource_ref, policy_version)
            WHERE policy.organization_id = requested_organization_id
              AND policy.resource_ref = requested.resource_ref
              AND policy.policy_version = requested.policy_version;
            GET DIAGNOSTICS updated_count = ROW_COUNT;
            IF updated_count <> cardinality(requested_resource_refs) THEN
                RAISE EXCEPTION USING ERRCODE = '40001',
                    MESSAGE = 'bulk Article policy change was not accepted';
            END IF;

            UPDATE public.organization_policy_epoch AS epoch
            SET policy_epoch = next_epoch
            WHERE epoch.organization_id = requested_organization_id
              AND epoch.policy_epoch = current_epoch;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING ERRCODE = '40001',
                    MESSAGE = 'bulk Article policy change was not accepted';
            END IF;

            target_policy_digest := encode(public.digest(
                convert_to(requested_policy_kind || ':' ||
                    array_to_string(requested_group_refs, ','), 'UTF8'),
                'sha256'), 'hex');
            generated_audit_ref := pg_catalog.gen_random_uuid();
            INSERT INTO public.{_AUDIT_TABLE} (
                organization_id, audit_ref, policy_epoch, article_count,
                preview_digest, target_policy_digest, operator_digest,
                authority_digest, request_digest, reason_category
            ) VALUES (
                requested_organization_id, generated_audit_ref,
                next_epoch, updated_count,
                requested_preview_digest, target_policy_digest,
                encode(public.digest(convert_to(requested_operator_ref, 'UTF8'),
                    'sha256'), 'hex'),
                encode(public.digest(convert_to(requested_authority_ref, 'UTF8'),
                    'sha256'), 'hex'),
                encode(public.digest(convert_to(requested_request_id, 'UTF8'),
                    'sha256'), 'hex'),
                'operator_confirmed_visibility_change'
            );

            audit_ref := generated_audit_ref;
            changed_articles := updated_count;
            RETURN NEXT;
        END;
        $function$
        """
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION public.{_FUNCTION}{_SIGNATURE} FROM PUBLIC"
    )
    for role in (_RUNTIME, _WORKER):
        op.execute(
            f"REVOKE ALL ON FUNCTION public.{_FUNCTION}{_SIGNATURE} FROM {role}"
        )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_FUNCTION}{_SIGNATURE} TO {_CONTROL}"
    )
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_ACCESS_DEFINER}")
    op.execute(
        f"ALTER FUNCTION public.{_FUNCTION}{_SIGNATURE} OWNER TO {_ACCESS_DEFINER}"
    )
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_ACCESS_DEFINER}")


def downgrade() -> None:
    """Remove the bulk path only when its retained audit lineage is empty."""

    connection = op.get_bind()
    retained = connection.execute(
        sa.text(f"SELECT EXISTS (SELECT 1 FROM {_AUDIT_TABLE})")
    ).scalar_one()
    if retained:
        raise RuntimeError("cannot remove retained bulk Article policy audit lineage")
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_ACCESS_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_ACCESS_DEFINER}")
    op.execute(f"DROP FUNCTION public.{_FUNCTION}{_SIGNATURE}")
    op.execute(
        f"DROP FUNCTION IF EXISTS public.{_PREVIEW_FUNCTION}"
        f"{_PREVIEW_SIGNATURE}"
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_ACCESS_DEFINER}")
    op.drop_table(_AUDIT_TABLE)
