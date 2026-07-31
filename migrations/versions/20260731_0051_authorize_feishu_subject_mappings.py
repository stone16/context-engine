"""Authorize Feishu subject mappings and retain source freshness provenance.

Revision ID: 20260731_0051
Revises: 20260731_0050
Create Date: 2026-07-31
"""

# ruff: noqa: E501

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260731_0051"
down_revision: str | None = "20260731_0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONTROL = "context_engine_control"
_DEFINER = "context_engine_access_policy_definer"
_MIGRATOR = "context_engine_migrator"
_APPLY = "context_control_apply_feishu_acl_observation"
_APPLY_SIGNATURE = "(uuid,uuid,uuid,text,text,boolean)"
_VERIFY = "context_feishu_verify_acl_artifact"
_VERIFY_SIGNATURE = "(uuid,uuid,jsonb)"
_MAX = 9223372036854775807


def _replace_apply_function(searched: str, replacement: str) -> None:
    searched = searched.strip()
    replacement = replacement.strip()
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"""
        DO $block$
        DECLARE definition text;
        DECLARE replacement_definition text;
        BEGIN
            definition := pg_catalog.pg_get_functiondef(
                'public.{_APPLY}{_APPLY_SIGNATURE}'::regprocedure
            );
            replacement_definition := pg_catalog.replace(
                definition,
                $search${searched}$search$,
                $replacement${replacement}$replacement$
            );
            IF replacement_definition = definition THEN
                RAISE EXCEPTION
                    'Feishu ACL application body was not recognized';
            END IF;
            EXECUTE replacement_definition;
        END;
        $block$
        """
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


_PRIOR_DECLARATION = """        DECLARE artifact_principals text[] := ARRAY[]::text[];
        DECLARE observed_at timestamptz;
        DECLARE prior_observation public.article_source_acl_observation%ROWTYPE;"""
_FRESHNESS_DECLARATION = """        DECLARE artifact_principals text[] := ARRAY[]::text[];
        DECLARE observed_at timestamptz;
        DECLARE acl_as_of timestamptz;
        DECLARE observed_policy_epoch bigint;
        DECLARE prior_observation public.article_source_acl_observation%ROWTYPE;"""

_PRIOR_PARSE = """            observed_at := (acl->>'observed_at')::timestamptz;
                artifact := pg_catalog.convert_from("""
_FRESHNESS_PARSE = """            observed_at := (acl->>'observed_at')::timestamptz;
                acl_as_of := observed_at;
                observed_policy_epoch := (acl->>'policy_epoch')::bigint;
                artifact := pg_catalog.convert_from("""

_PRIOR_ARTIFACT_VALIDATION = """IF artifact->>'schema_version' IS DISTINCT FROM 'feishu-acl-observation-v1'
               OR artifact->>'document_ref' IS DISTINCT FROM requested_document_ref
               OR artifact->'flattening'->>'artifact_version'
                    IS DISTINCT FROM 'feishu-group-flattening-v1'
               OR artifact->'flattening'->>'digest' !~ '^[0-9a-f]{64}$'
               OR jsonb_typeof(artifact->'flattening'->'local_group_refs')
                    IS DISTINCT FROM 'array'
               OR jsonb_typeof(artifact->'flattening'->'local_principal_refs')
                    IS DISTINCT FROM 'array'
               OR jsonb_typeof(artifact->'flattening'->'unresolved_group_refs')
                    IS DISTINCT FROM 'array'
            THEN RETURN; END IF;
            artifact_status := artifact->>'status';
            artifact_policy_kind := artifact->>'policy_kind';
            SELECT COALESCE(pg_catalog.array_agg(value ORDER BY value), ARRAY[]::text[])
            INTO artifact_groups
            FROM jsonb_array_elements_text(
                artifact->'flattening'->'local_group_refs'
            ) AS item(value);
            SELECT COALESCE(pg_catalog.array_agg(value ORDER BY value), ARRAY[]::text[])
            INTO artifact_principals
            FROM jsonb_array_elements_text(
                artifact->'flattening'->'local_principal_refs'
            ) AS item(value);
            IF artifact_status NOT IN ('resolved', 'failed', 'unresolved_group')
               OR (artifact_status = 'resolved'
                   AND artifact_policy_kind NOT IN ('private', 'organization', 'groups'))
               OR (artifact_status <> 'resolved' AND artifact_policy_kind IS NOT NULL)
               OR (artifact_policy_kind = 'groups' AND cardinality(artifact_groups) = 0)
               OR (artifact_policy_kind <> 'groups' AND cardinality(artifact_groups) <> 0)
               OR (artifact_status = 'unresolved_group' AND jsonb_array_length(
                    artifact->'flattening'->'unresolved_group_refs') = 0)
               OR EXISTS (
                    SELECT 1 FROM pg_catalog.unnest(artifact_principals) AS value
                    WHERE pg_catalog.btrim(value) = '' OR value ~ '[[:space:]]'
               )
               OR EXISTS (
                    SELECT 1 FROM pg_catalog.unnest(artifact_groups) AS requested(group_ref)
                    WHERE NOT EXISTS (
                        SELECT 1 FROM public.article_access_group AS owned
                        WHERE owned.organization_id = requested_organization_id
                          AND owned.group_ref = requested.group_ref
                    )
               )
            THEN
                artifact_status := 'unresolved_group';
                artifact_policy_kind := NULL;
                artifact_groups := ARRAY[]::text[];
                artifact_principals := ARRAY[]::text[];
            END IF;
"""
_AUTHORITATIVE_ARTIFACT_VALIDATION = f"""        IF artifact->>'document_ref'
                IS DISTINCT FROM requested_document_ref
        THEN
            artifact_status := 'unresolved_group';
            artifact_policy_kind := NULL;
            artifact_groups := ARRAY[]::text[];
            artifact_principals := ARRAY[]::text[];
        ELSE
            SELECT verified.artifact_status,
               verified.artifact_policy_kind,
               verified.artifact_groups,
               verified.artifact_principals
            INTO artifact_status, artifact_policy_kind,
                 artifact_groups, artifact_principals
            FROM public.{_VERIFY}(
                requested_organization_id, source_id, artifact
            ) AS verified;
        END IF;
        IF artifact_status IS NULL THEN
            artifact_status := 'unresolved_group';
            artifact_policy_kind := NULL;
            artifact_groups := ARRAY[]::text[];
            artifact_principals := ARRAY[]::text[];
        END IF;
"""

_PRIOR_GRANT_UPDATE_GUARD = """            IF article.resource_ref IS NOT NULL
               AND existing_policy.resource_ref IS NOT NULL
               AND NOT requested_delete_observation
               AND artifact_status = 'resolved'
               AND artifact_policy_kind = 'private'
            THEN"""
_AUTHORITATIVE_GRANT_UPDATE_GUARD = """            IF article.resource_ref IS NOT NULL
               AND existing_policy.resource_ref IS NOT NULL
               AND NOT requested_delete_observation
               AND (artifact_status <> 'resolved'
                    OR artifact_policy_kind <> 'private')
            THEN
                UPDATE public.resource_access_policy AS access
                SET access_state = 'revoked',
                    access_version = access.access_version + 1,
                    revoked_at = pg_catalog.statement_timestamp()
                WHERE access.organization_id = requested_organization_id
                  AND access.resource_ref = requested_document_ref
                  AND access.access_state = 'allowed'
                  AND access.access_version < 9223372036854775807;
            END IF;
            IF article.resource_ref IS NOT NULL
               AND existing_policy.resource_ref IS NOT NULL
               AND NOT requested_delete_observation
               AND artifact_status = 'resolved'
               AND artifact_policy_kind = 'private'
            THEN"""

_PRIOR_STALE = """        IF prior_observation.resource_ref IS NOT NULL
               AND observed_at <= prior_observation.observed_at
            THEN RETURN; END IF;"""
_FAIL_CLOSED_STALE = """        IF prior_observation.resource_ref IS NOT NULL
               AND observed_at <= prior_observation.observed_at
            THEN
                artifact_status := 'failed';
                artifact_policy_kind := NULL;
                artifact_groups := ARRAY[]::text[];
                artifact_principals := ARRAY[]::text[];
                observed_at := pg_catalog.statement_timestamp();
            END IF;"""

_PRIOR_OBSERVATION_COLUMNS = """            group_refs, observation_version, observed_at, acl_as_of,
                declared_lag_seconds
            ) VALUES ("""
_FRESH_OBSERVATION_COLUMNS = """            group_refs, observation_version, observed_at, acl_as_of,
                declared_lag_seconds, source_policy_epoch
            ) VALUES ("""
_PRIOR_OBSERVATION_VALUES = """            next_observation_version, observed_at, observed_at, 0
            ) ON CONFLICT"""
_FRESH_OBSERVATION_VALUES = """            next_observation_version, observed_at, acl_as_of, 0,
                observed_policy_epoch
            ) ON CONFLICT"""
_PRIOR_OBSERVATION_UPDATE = """            acl_as_of = EXCLUDED.acl_as_of,
                declared_lag_seconds = EXCLUDED.declared_lag_seconds;"""
_FRESH_OBSERVATION_UPDATE = """            acl_as_of = EXCLUDED.acl_as_of,
                declared_lag_seconds = EXCLUDED.declared_lag_seconds,
                source_policy_epoch = EXCLUDED.source_policy_epoch;"""
_PRIOR_POLICY_FRESHNESS = """                    source_acl_as_of = observed_at,
                        source_declared_lag_seconds = 0,
                        fixed_at_policy_epoch = current_epoch + 1"""
_FRESH_POLICY_FRESHNESS = """                    source_acl_as_of = acl_as_of,
                        source_declared_lag_seconds = 0,
                        source_policy_epoch = observed_policy_epoch,
                        fixed_at_policy_epoch = current_epoch + 1"""


def upgrade() -> None:
    """Move every local grant decision behind engine-owned mapping authority."""

    op.create_table(
        "feishu_subject_mapping",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_kind", sa.Text(), nullable=False),
        sa.Column("external_ref", sa.Text(), nullable=False),
        sa.Column("local_ref", sa.Text(), nullable=False),
        sa.Column("mapping_version", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "source_id",
            "subject_kind",
            "external_ref",
            name="pk_feishu_subject_mapping",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_id"],
            ["context_source.organization_id", "context_source.source_id"],
            name="fk_feishu_subject_mapping_source",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "subject_kind IN ('identity', 'group')",
            name="ck_feishu_subject_mapping_kind",
        ),
        sa.CheckConstraint(
            "char_length(external_ref) BETWEEN 1 AND 256 "
            "AND external_ref = btrim(external_ref) "
            "AND external_ref !~ '[[:space:]]' "
            "AND char_length(local_ref) BETWEEN 1 AND 256 "
            "AND local_ref = btrim(local_ref) "
            "AND local_ref !~ '[[:space:]]'",
            name="ck_feishu_subject_mapping_refs",
        ),
        sa.CheckConstraint(
            f"mapping_version BETWEEN 1 AND {_MAX}",
            name="ck_feishu_subject_mapping_version",
        ),
    )
    op.execute("REVOKE ALL ON TABLE feishu_subject_mapping FROM PUBLIC")
    for role in (_CONTROL, _DEFINER):
        op.execute(f"REVOKE ALL ON TABLE feishu_subject_mapping FROM {role}")
    op.execute("ALTER TABLE feishu_subject_mapping ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE feishu_subject_mapping FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY feishu_subject_mapping_migrator_administration "
        f"ON feishu_subject_mapping FOR ALL TO {_MIGRATOR} "
        "USING (true) WITH CHECK (true)"
    )
    tenant = (
        "organization_id = NULLIF("
        "current_setting('app.organization_id', true), '')::uuid"
    )
    op.execute(
        "CREATE POLICY feishu_subject_mapping_access_definer_select "
        f"ON feishu_subject_mapping FOR SELECT TO {_DEFINER} USING ({tenant})"
    )
    op.execute(f"GRANT SELECT ON TABLE feishu_subject_mapping TO {_DEFINER}")

    op.add_column(
        "article_source_acl_observation",
        sa.Column("source_policy_epoch", sa.BigInteger(), nullable=True),
    )
    op.create_check_constraint(
        "ck_article_source_acl_observation_source_epoch",
        "article_source_acl_observation",
        f"source_policy_epoch IS NULL OR source_policy_epoch BETWEEN 1 AND {_MAX}",
    )
    op.add_column(
        "article_access_policy",
        sa.Column("source_policy_epoch", sa.BigInteger(), nullable=True),
    )
    op.create_check_constraint(
        "ck_article_access_policy_source_epoch",
        "article_access_policy",
        f"source_policy_epoch IS NULL OR source_policy_epoch BETWEEN 1 AND {_MAX}",
    )

    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"""
        CREATE FUNCTION public.{_VERIFY}(
            requested_organization_id uuid,
            requested_source_id uuid,
            artifact jsonb
        ) RETURNS TABLE (
            artifact_status text,
            artifact_policy_kind text,
            artifact_groups text[],
            artifact_principals text[]
        )
        LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog, public
        SET row_security = on
        AS $function$
        DECLARE flattening jsonb;
        DECLARE canonical_graph_text text;
        DECLARE canonical_graph jsonb;
        DECLARE claimed_groups text[] := ARRAY[]::text[];
        DECLARE claimed_principals text[] := ARRAY[]::text[];
        DECLARE computed_groups text[] := ARRAY[]::text[];
        DECLARE computed_principals text[] := ARRAY[]::text[];
        DECLARE unresolved boolean := false;
        BEGIN
            IF requested_organization_id IS NULL
               OR requested_source_id IS NULL
               OR artifact->>'schema_version'
                    IS DISTINCT FROM 'feishu-acl-observation-v1'
               OR jsonb_typeof(artifact->'flattening') IS DISTINCT FROM 'object'
            THEN RETURN; END IF;
            flattening := artifact->'flattening';
            IF flattening->>'artifact_version'
                    IS DISTINCT FROM 'feishu-group-flattening-v1'
               OR jsonb_typeof(flattening->'canonical_graph')
                    IS DISTINCT FROM 'string'
               OR jsonb_typeof(flattening->'requested_group_refs')
                    IS DISTINCT FROM 'array'
               OR jsonb_typeof(flattening->'direct_identity_refs')
                    IS DISTINCT FROM 'array'
               OR jsonb_typeof(flattening->'local_group_refs')
                    IS DISTINCT FROM 'array'
               OR jsonb_typeof(flattening->'local_principal_refs')
                    IS DISTINCT FROM 'array'
            THEN RETURN; END IF;
            canonical_graph_text := flattening->>'canonical_graph';
            BEGIN
                canonical_graph := canonical_graph_text::jsonb;
            EXCEPTION WHEN data_exception THEN RETURN;
            END;
            IF flattening->>'digest' IS DISTINCT FROM pg_catalog.encode(
                public.digest(
                    pg_catalog.convert_to(canonical_graph_text, 'UTF8'),
                    'sha256'
                ),
                'hex'
            ) OR jsonb_typeof(canonical_graph->'nodes') IS DISTINCT FROM 'array'
            THEN RETURN; END IF;
            SELECT COALESCE(array_agg(value ORDER BY value), ARRAY[]::text[])
            INTO claimed_groups
            FROM jsonb_array_elements_text(flattening->'local_group_refs')
                 AS claimed(value);
            SELECT COALESCE(array_agg(value ORDER BY value), ARRAY[]::text[])
            INTO claimed_principals
            FROM jsonb_array_elements_text(flattening->'local_principal_refs')
                 AS claimed(value);

            WITH RECURSIVE nodes AS (
                SELECT node->>'external_ref' AS external_ref,
                       node->>'local_group_ref' AS claimed_local_ref,
                       node->'identity_refs' AS identity_refs,
                       node->'child_group_refs' AS child_group_refs
                FROM jsonb_array_elements(canonical_graph->'nodes') AS item(node)
                WHERE jsonb_typeof(node) = 'object'
                  AND jsonb_typeof(node->'identity_refs') = 'array'
                  AND jsonb_typeof(node->'child_group_refs') = 'array'
            ), reachable(external_ref) AS (
                SELECT requested.value
                FROM jsonb_array_elements_text(
                    flattening->'requested_group_refs'
                ) AS requested(value)
                UNION
                SELECT child.value
                FROM reachable
                JOIN nodes ON nodes.external_ref = reachable.external_ref
                CROSS JOIN LATERAL jsonb_array_elements_text(
                    nodes.child_group_refs
                ) AS child(value)
            ), distinct_reachable AS (
                SELECT DISTINCT external_ref FROM reachable
            )
            SELECT EXISTS (
                SELECT 1
                FROM distinct_reachable AS reachable_group
                LEFT JOIN nodes
                  ON nodes.external_ref = reachable_group.external_ref
                LEFT JOIN public.feishu_subject_mapping AS mapping
                  ON mapping.organization_id = requested_organization_id
                 AND mapping.source_id = requested_source_id
                 AND mapping.subject_kind = 'group'
                 AND mapping.external_ref = reachable_group.external_ref
                WHERE nodes.external_ref IS NULL
                   OR mapping.local_ref IS NULL
                   OR mapping.local_ref IS DISTINCT FROM nodes.claimed_local_ref
            ) INTO unresolved;

            WITH RECURSIVE nodes AS (
                SELECT node->>'external_ref' AS external_ref,
                       node->'identity_refs' AS identity_refs,
                       node->'child_group_refs' AS child_group_refs
                FROM jsonb_array_elements(canonical_graph->'nodes') AS item(node)
            ), reachable(external_ref) AS (
                SELECT requested.value
                FROM jsonb_array_elements_text(
                    flattening->'requested_group_refs'
                ) AS requested(value)
                UNION
                SELECT child.value
                FROM reachable
                JOIN nodes ON nodes.external_ref = reachable.external_ref
                CROSS JOIN LATERAL jsonb_array_elements_text(
                    nodes.child_group_refs
                ) AS child(value)
            ), distinct_reachable AS (
                SELECT DISTINCT external_ref FROM reachable
            ), external_identities AS (
                SELECT direct.value AS external_ref
                FROM jsonb_array_elements_text(
                    flattening->'direct_identity_refs'
                ) AS direct(value)
                UNION
                SELECT identity.value
                FROM distinct_reachable AS reachable_group
                JOIN nodes ON nodes.external_ref = reachable_group.external_ref
                CROSS JOIN LATERAL jsonb_array_elements_text(
                    nodes.identity_refs
                ) AS identity(value)
            )
            SELECT COALESCE(array_agg(DISTINCT mapping.local_ref
                                      ORDER BY mapping.local_ref),
                            ARRAY[]::text[])
            INTO computed_principals
            FROM external_identities AS identity
            JOIN public.feishu_subject_mapping AS mapping
              ON mapping.organization_id = requested_organization_id
             AND mapping.source_id = requested_source_id
             AND mapping.subject_kind = 'identity'
             AND mapping.external_ref = identity.external_ref;

            WITH RECURSIVE nodes AS (
                SELECT node->>'external_ref' AS external_ref,
                       node->'child_group_refs' AS child_group_refs
                FROM jsonb_array_elements(canonical_graph->'nodes') AS item(node)
            ), reachable(external_ref) AS (
                SELECT requested.value
                FROM jsonb_array_elements_text(
                    flattening->'requested_group_refs'
                ) AS requested(value)
                UNION
                SELECT child.value
                FROM reachable
                JOIN nodes ON nodes.external_ref = reachable.external_ref
                CROSS JOIN LATERAL jsonb_array_elements_text(
                    nodes.child_group_refs
                ) AS child(value)
            )
            SELECT COALESCE(array_agg(DISTINCT mapping.local_ref
                                      ORDER BY mapping.local_ref),
                            ARRAY[]::text[])
            INTO computed_groups
            FROM (SELECT DISTINCT external_ref FROM reachable) AS group_ref
            JOIN public.feishu_subject_mapping AS mapping
              ON mapping.organization_id = requested_organization_id
             AND mapping.source_id = requested_source_id
             AND mapping.subject_kind = 'group'
             AND mapping.external_ref = group_ref.external_ref
            JOIN public.article_access_group AS owned
              ON owned.organization_id = requested_organization_id
             AND owned.group_ref = mapping.local_ref;

            artifact_status := artifact->>'status';
            artifact_policy_kind := artifact->>'policy_kind';
            IF artifact_status = 'failed' THEN
                artifact_policy_kind := NULL;
                artifact_groups := ARRAY[]::text[];
                artifact_principals := ARRAY[]::text[];
                RETURN NEXT;
                RETURN;
            END IF;
            IF artifact_status NOT IN ('resolved', 'unresolved_group')
               OR unresolved
               OR claimed_groups IS DISTINCT FROM computed_groups
               OR claimed_principals IS DISTINCT FROM computed_principals
               OR artifact_status = 'unresolved_group'
            THEN
                artifact_status := 'unresolved_group';
                artifact_policy_kind := NULL;
                artifact_groups := ARRAY[]::text[];
                artifact_principals := ARRAY[]::text[];
                RETURN NEXT;
                RETURN;
            END IF;
            IF artifact_policy_kind NOT IN ('private', 'organization', 'groups')
               OR (artifact_policy_kind = 'groups'
                   AND cardinality(computed_groups) = 0)
               OR (artifact_policy_kind <> 'groups'
                   AND cardinality(computed_groups) <> 0)
            THEN
                artifact_status := 'unresolved_group';
                artifact_policy_kind := NULL;
                artifact_groups := ARRAY[]::text[];
                artifact_principals := ARRAY[]::text[];
            ELSE
                artifact_groups := computed_groups;
                artifact_principals := computed_principals;
            END IF;
            RETURN NEXT;
        END;
        $function$
        """
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")
    op.execute(f"REVOKE ALL ON FUNCTION public.{_VERIFY}{_VERIFY_SIGNATURE} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.{_VERIFY}{_VERIFY_SIGNATURE} TO {_DEFINER}")

    for searched, replacement in (
        (_PRIOR_DECLARATION, _FRESHNESS_DECLARATION),
        (_PRIOR_PARSE, _FRESHNESS_PARSE),
        (_PRIOR_ARTIFACT_VALIDATION, _AUTHORITATIVE_ARTIFACT_VALIDATION),
        (_PRIOR_GRANT_UPDATE_GUARD, _AUTHORITATIVE_GRANT_UPDATE_GUARD),
        (_PRIOR_STALE, _FAIL_CLOSED_STALE),
        (_PRIOR_OBSERVATION_COLUMNS, _FRESH_OBSERVATION_COLUMNS),
        (_PRIOR_OBSERVATION_VALUES, _FRESH_OBSERVATION_VALUES),
        (_PRIOR_OBSERVATION_UPDATE, _FRESH_OBSERVATION_UPDATE),
        (_PRIOR_POLICY_FRESHNESS, _FRESH_POLICY_FRESHNESS),
    ):
        _replace_apply_function(searched, replacement)


def downgrade() -> None:
    """Restore 0050 only when no retained Feishu authorization state exists."""

    # Hold the retained-state decision stable through the function/schema
    # replacement below.  Every writer of the Feishu authorization relations
    # must first participate in one of these source/subject/observation tables,
    # so PostgreSQL serializes an in-flight commit ahead of this decision and
    # blocks new writes until the migration transaction finishes.
    for table_name in (
        "context_source",
        "source_version",
        "feishu_subject_mapping",
        "article_source_acl_observation",
    ):
        op.execute(f"LOCK TABLE {table_name} IN ACCESS EXCLUSIVE MODE")
    op.execute(
        """
        DO $block$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM context_source
                WHERE source_kind = 'feishu_docs'
            ) OR EXISTS (
                SELECT 1 FROM source_version
                WHERE source_kind = 'feishu_docs'
            ) OR EXISTS (
                SELECT 1 FROM feishu_subject_mapping
            ) OR EXISTS (
                SELECT 1 FROM article_source_acl_observation
                WHERE source_id IS NOT NULL
                  AND EXISTS (
                      SELECT 1 FROM source_version
                      WHERE source_version.organization_id =
                                article_source_acl_observation.organization_id
                        AND source_version.source_id =
                                article_source_acl_observation.source_id
                        AND source_version.source_kind = 'feishu_docs'
                  )
            )
            THEN RAISE EXCEPTION USING ERRCODE = '55000',
                MESSAGE = 'cannot downgrade with retained Feishu authorization state';
            END IF;
        END;
        $block$
        """
    )
    for searched, replacement in reversed(
        (
            (_PRIOR_DECLARATION, _FRESHNESS_DECLARATION),
            (_PRIOR_PARSE, _FRESHNESS_PARSE),
            (_PRIOR_ARTIFACT_VALIDATION, _AUTHORITATIVE_ARTIFACT_VALIDATION),
            (_PRIOR_GRANT_UPDATE_GUARD, _AUTHORITATIVE_GRANT_UPDATE_GUARD),
            (_PRIOR_STALE, _FAIL_CLOSED_STALE),
            (_PRIOR_OBSERVATION_COLUMNS, _FRESH_OBSERVATION_COLUMNS),
            (_PRIOR_OBSERVATION_VALUES, _FRESH_OBSERVATION_VALUES),
            (_PRIOR_OBSERVATION_UPDATE, _FRESH_OBSERVATION_UPDATE),
            (_PRIOR_POLICY_FRESHNESS, _FRESH_POLICY_FRESHNESS),
        )
    ):
        _replace_apply_function(replacement, searched)
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(f"DROP FUNCTION public.{_VERIFY}{_VERIFY_SIGNATURE}")
    op.execute("RESET ROLE")
    op.drop_constraint(
        "ck_article_access_policy_source_epoch",
        "article_access_policy",
        type_="check",
    )
    op.drop_column("article_access_policy", "source_policy_epoch")
    op.drop_constraint(
        "ck_article_source_acl_observation_source_epoch",
        "article_source_acl_observation",
        type_="check",
    )
    op.drop_column("article_source_acl_observation", "source_policy_epoch")
    op.drop_table("feishu_subject_mapping")
