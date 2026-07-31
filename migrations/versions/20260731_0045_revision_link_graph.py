# ruff: noqa: E501
"""Activate leased rich Markdown and immutable Revision link edges.

Revision ID: 20260731_0045
Revises: 20260730_0044
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260731_0045"
down_revision: str | None = "20260730_0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MIGRATOR = "context_engine_migrator"
_RUNTIME = "context_engine_runtime"
_WORKER = "context_engine_worker"
_WORKER_DEFINER = "context_engine_worker_lease_definer"
_GRAPH_DEFINER = "context_engine_graph_definer"
_ACQUIRE = (
    "context_worker_acquire_file_publication"
    "(uuid,uuid,uuid,text,text,uuid,text,text,text,text,text,jsonb,jsonb,"
    "bigint,bigint,bytea,timestamp with time zone,timestamp with time zone)"
)
_PREPARE = (
    "context_worker_prepare_file_publication"
    "(uuid,uuid,uuid,text,text,uuid,text,jsonb,jsonb,jsonb,bigint,bigint,bytea,"
    "timestamp with time zone,timestamp with time zone)"
)
_INDEX = (
    "context_worker_index_file_publication"
    "(uuid,uuid,uuid,text,text,uuid,text,jsonb,jsonb,bigint,bigint,bytea,"
    "timestamp with time zone,timestamp with time zone)"
)
_TENANT = (
    "organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid"
)
_GRAPH_COLUMNS_BY_TABLE = {
    "context_resource": (
        "organization_id, source_ref, resource_ref, active_revision_id, tombstoned"
    ),
    "context_fragment": (
        "organization_id, resource_ref, revision_id, fragment_ref, ordinal"
    ),
    "file_acquisition": (
        "organization_id, source_id, acquisition_id, relative_path"
    ),
    "file_revision_snapshot": (
        "organization_id, acquisition_id, resource_ref, revision_id"
    ),
    "membership": (
        "organization_id, user_id, membership_id, membership_version, status, "
        "valid_from, valid_until"
    ),
}
_V3_RECOVERY_BRANCH = """\
                  OR
                  (recovery.compiler_version = 'context-engine-markdown-v3'
                   AND requested_compilation_document->>'compilationDigest'
                       = recovery.compilation_digest)"""
_PREPARE_V2_BRANCH = """\
                  (recovery.compiler_version = 'context-engine-markdown-v2'
                   AND requested_compilation_document->>'compilationDigest'
                       = recovery.compilation_digest)"""
_V3_ACQUIRE_BRANCH = r""" OR (
            requested_compiler_version = 'context-engine-markdown-v3'
            AND requested_config_version = 'markdown-config-v3'
            AND jsonb_typeof(requested_compilation_document) = 'object'
            AND requested_compilation_document->>'canonicalText'
                IS NOT DISTINCT FROM requested_canonical_text
            AND requested_compilation_document->>'contentHash'
                IS NOT DISTINCT FROM requested_content_hash
            AND requested_compilation_document->>'compilationDigest'
                IS NOT DISTINCT FROM requested_compilation_digest
            AND requested_compilation_document#>>'{provenance,compilerVersion}'
                IS NOT DISTINCT FROM requested_compiler_version
            AND requested_compilation_document#>>'{provenance,configVersion}'
                IS NOT DISTINCT FROM requested_config_version
            AND requested_compilation_document#>>'{provenance,canonicalizationProfile}'
                IS NOT DISTINCT FROM 'markdown-rich-structural-v3'
            AND requested_compilation_document#>>'{provenance,compilationDigestProfile}'
                IS NOT DISTINCT FROM 'rfc8785-sha256-v3'
            AND requested_compilation_document#>>'{provenance,tokenCeiling}'
                ~ '^[1-9][0-9]*$'
            AND jsonb_typeof(requested_compilation_document->'sections') = 'array'
            AND jsonb_typeof(requested_compilation_document->'fragments') = 'array'
            AND jsonb_array_length(requested_compilation_document->'fragments')
                BETWEEN 1 AND 4096
            AND jsonb_array_length(requested_compilation_document->'sections')
                = jsonb_array_length(requested_compilation_document->'fragments')
            AND NOT EXISTS (
                SELECT 1
                FROM jsonb_array_elements(
                    requested_compilation_document->'fragments'
                ) AS item(fragment)
                WHERE jsonb_typeof(item.fragment) IS DISTINCT FROM 'object'
                   OR COALESCE(
                        item.fragment->>'fragmentRef'
                            !~ '^fragment:(heading|paragraph|list|fenced_code|table):[1-9][0-9]*$',
                        true
                   )
                   OR COALESCE(
                        item.fragment->>'kind'
                            NOT IN ('heading', 'paragraph', 'list', 'fenced_code', 'table'),
                        true
                   )
                   OR jsonb_typeof(item.fragment->'path') IS DISTINCT FROM 'array'
                   OR jsonb_typeof(item.fragment->'position') IS DISTINCT FROM 'object'
                   OR COALESCE(btrim(item.fragment->>'sourceText') = '', true)
                   OR COALESCE(btrim(item.fragment->>'contextualText') = '', true)
                   OR jsonb_typeof(item.fragment->'searchPhrases')
                        IS DISTINCT FROM 'array'
                   OR jsonb_array_length(item.fragment->'searchPhrases')
                        NOT BETWEEN 1 AND 4096
            )
            AND (
                SELECT count(DISTINCT item.fragment->>'fragmentRef')
                FROM jsonb_array_elements(
                    requested_compilation_document->'fragments'
                ) AS item(fragment)
            ) = jsonb_array_length(requested_compilation_document->'fragments')
            AND requested_artifact_document = (
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'fragmentRef', item.fragment->'fragmentRef',
                        'contextualText', item.fragment->'contextualText',
                        'searchPhrases', item.fragment->'searchPhrases'
                    ) ORDER BY item.ordinal
                )
                FROM jsonb_array_elements(
                    requested_compilation_document->'fragments'
                ) WITH ORDINALITY AS item(fragment, ordinal)
            )
            AND jsonb_typeof(requested_compilation_document->'revisionLinks')
                = 'array'
            AND jsonb_array_length(requested_compilation_document->'revisionLinks')
                BETWEEN 0 AND 4096
            AND NOT EXISTS (
                SELECT 1
                FROM jsonb_array_elements(
                    requested_compilation_document->'revisionLinks'
                ) AS edge(item)
                WHERE jsonb_typeof(edge.item) IS DISTINCT FROM 'object'
                   OR (SELECT count(*) FROM jsonb_object_keys(edge.item)) <> 2
                   OR edge.item->>'kind'
                        NOT IN ('wikilink', 'embed', 'markdown_link')
                   OR COALESCE(
                        edge.item->>'targetPath'
                            !~ '^([^/\\]+/)*[^/\\]*\.[mM][dD]$',
                        true
                   )
                   OR edge.item->>'targetPath' ~ '(^|/)(\.|\.\.)(/|$)'
                   OR char_length(edge.item->>'targetPath') > 255
            )
            AND (
                SELECT count(DISTINCT edge.item->>'targetPath')
                FROM jsonb_array_elements(
                    requested_compilation_document->'revisionLinks'
                ) AS edge(item)
            ) = jsonb_array_length(requested_compilation_document->'revisionLinks')
        )"""
_ACQUIRE_V3_ANCHOR = """\
        )
    )
            THEN RETURN; END IF;
            PERFORM pg_catalog.set_config("""
_ACQUIRE_V3_REPLACEMENT = (
    "        )" + _V3_ACQUIRE_BRANCH + "\n    )\n            THEN RETURN; END IF;\n"
    "            PERFORM pg_catalog.set_config("
)
_PREPARE_INSERT_ANCHOR = """\
            ORDER BY item.ordinal;
            INSERT INTO public.revision_publication_event VALUES ("""
_PREPARE_INSERT_GRAPH = """\
            ORDER BY item.ordinal;
            INSERT INTO public.revision_link_edge (
                organization_id, source_resource_ref, source_revision_id,
                ordinal, target_path, link_kind
            )
            SELECT requested_organization_id, requested_resource_ref,
                   requested_revision_id, (edge.ordinal - 1)::integer,
                   edge.item->>'targetPath', edge.item->>'kind'
            FROM jsonb_array_elements(
                requested_compilation_document->'revisionLinks'
            ) WITH ORDINALITY AS edge(item, ordinal)
            ORDER BY edge.ordinal;
            INSERT INTO public.revision_publication_event VALUES ("""
_SNAPSHOT_MATCH_ANCHOR = """\
                      AND snapshot.compilation_document IS NOT DISTINCT FROM requested_compilation_document
                      AND ("""
_SNAPSHOT_MATCH_GRAPH = """\
                      AND snapshot.compilation_document IS NOT DISTINCT FROM requested_compilation_document
                      AND public.context_internal_revision_link_edges_match(
                          requested_organization_id, requested_resource_ref,
                          snapshot.revision_id, requested_compilation_document
                      )
                      AND ("""
_ACQUIRE_SNAPSHOT_MATCH_ANCHOR = """\
                      AND snapshot.compilation_document IS NOT DISTINCT FROM
                          requested_compilation_document
                      AND ("""
_ACQUIRE_SNAPSHOT_MATCH_GRAPH = """\
                      AND snapshot.compilation_document IS NOT DISTINCT FROM
                          requested_compilation_document
                      AND public.context_internal_revision_link_edges_match(
                          requested_organization_id, requested_resource_ref,
                          snapshot.revision_id, requested_compilation_document
                      )
                      AND ("""


def _definition(regprocedure: str) -> str:
    value = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT pg_catalog.pg_get_functiondef("
                "CAST(:regprocedure AS regprocedure))"
            ),
            {"regprocedure": f"public.{regprocedure}"},
        )
        .scalar_one()
    )
    if not isinstance(value, str):
        raise RuntimeError("File publication function definition is unavailable")
    return value


def _replace(regprocedure: str, pairs: tuple[tuple[str, str], ...]) -> None:
    definition = _definition(regprocedure)
    for searched, replacement in pairs:
        if definition.count(searched) != 1:
            raise RuntimeError("File publication function shape was not recognized")
        definition = definition.replace(searched, replacement)
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_WORKER_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_WORKER_DEFINER}")
    op.execute(definition)
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_WORKER_DEFINER}")


def _secure_graph_table() -> None:
    for role in ("PUBLIC", _RUNTIME, _WORKER, _WORKER_DEFINER, _GRAPH_DEFINER):
        op.execute(f"REVOKE ALL ON TABLE revision_link_edge FROM {role}")
    op.execute("ALTER TABLE revision_link_edge ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE revision_link_edge FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY revision_link_edge_migrator_administration "
        "ON revision_link_edge FOR ALL TO context_engine_migrator "
        "USING (true) WITH CHECK (true)"
    )
    op.execute(
        "CREATE POLICY revision_link_edge_worker_insert ON revision_link_edge "
        f"FOR INSERT TO {_WORKER_DEFINER} WITH CHECK ({_TENANT})"
    )
    op.execute(
        "CREATE POLICY revision_link_edge_graph_select ON revision_link_edge "
        f"FOR SELECT TO {_GRAPH_DEFINER} USING ({_TENANT})"
    )
    op.execute(f"GRANT INSERT ON revision_link_edge TO {_WORKER_DEFINER}")
    op.execute(f"GRANT SELECT ON revision_link_edge TO {_GRAPH_DEFINER}")


def _create_graph_functions() -> None:
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_GRAPH_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_GRAPH_DEFINER}")
    op.execute(
        f"""
        CREATE FUNCTION public.context_internal_revision_link_edges_match(
            requested_organization_id uuid, requested_resource_ref text,
            requested_revision_id uuid, requested_compilation_document jsonb
        ) RETURNS boolean LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$
        BEGIN
            IF SESSION_USER <> '{_WORKER}'
               OR jsonb_typeof(requested_compilation_document->'revisionLinks')
                    IS DISTINCT FROM 'array'
            THEN RETURN false; END IF;
            PERFORM set_config(
                'app.organization_id', requested_organization_id::text, true
            );
            RETURN COALESCE(
                (
                    SELECT jsonb_agg(
                        jsonb_build_object(
                            'kind', stored.link_kind,
                            'targetPath', stored.target_path
                        ) ORDER BY stored.ordinal
                    )
                    FROM public.revision_link_edge AS stored
                    WHERE stored.organization_id = requested_organization_id
                      AND stored.source_resource_ref = requested_resource_ref
                      AND stored.source_revision_id = requested_revision_id
                ),
                '[]'::jsonb
            ) = requested_compilation_document->'revisionLinks';
        END; $function$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION public.context_runtime_resolve_one_hop_graph(
            requested_organization_ids uuid[], requested_source_refs text[],
            requested_resource_refs text[], requested_revision_ids uuid[],
            requested_fragment_refs text[], requested_limit integer,
            requested_offset integer
        ) RETURNS TABLE (
            anchor_organization_id uuid, anchor_source_ref text,
            anchor_resource_ref text, anchor_revision_id uuid,
            anchor_fragment_ref text, organization_id uuid, source_ref text,
            resource_ref text, revision_id uuid, fragment_ref text
        ) LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$
        BEGIN
            IF SESSION_USER <> '{_RUNTIME}'
               OR requested_limit IS NULL
               OR requested_limit NOT BETWEEN 1 AND 64
               OR requested_offset IS NULL
               OR requested_offset < 0
               OR cardinality(requested_organization_ids) = 0
               OR cardinality(requested_organization_ids)
                    IS DISTINCT FROM cardinality(requested_source_refs)
               OR cardinality(requested_organization_ids)
                    IS DISTINCT FROM cardinality(requested_resource_refs)
               OR cardinality(requested_organization_ids)
                    IS DISTINCT FROM cardinality(requested_revision_ids)
               OR cardinality(requested_organization_ids)
                    IS DISTINCT FROM cardinality(requested_fragment_refs)
               OR EXISTS (
                    SELECT 1 FROM unnest(requested_organization_ids) AS item(value)
                    WHERE item.value IS DISTINCT FROM NULLIF(
                        current_setting('app.organization_id', true), ''
                    )::uuid
               )
               OR current_setting('app.actor_kind', true) <> 'user'
               OR NOT EXISTS (
                    SELECT 1 FROM public.membership AS actor_membership
                    WHERE actor_membership.organization_id = NULLIF(
                            current_setting('app.organization_id', true), ''
                          )::uuid
                      AND actor_membership.user_id = NULLIF(
                            current_setting('app.user_id', true), ''
                          )::uuid
                      AND actor_membership.membership_id = NULLIF(
                            current_setting('app.membership_id', true), ''
                          )::uuid
                      AND actor_membership.membership_version = NULLIF(
                            current_setting('app.membership_version', true), ''
                          )::bigint
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
            THEN RETURN; END IF;
            RETURN QUERY
            WITH anchors AS (
                SELECT * FROM unnest(
                    requested_organization_ids, requested_source_refs,
                    requested_resource_refs, requested_revision_ids,
                    requested_fragment_refs
                ) AS anchor(
                    organization_id, source_ref, resource_ref,
                    revision_id, fragment_ref
                )
            ), expanded AS (
                SELECT anchor.organization_id AS anchor_organization_id,
                       anchor.source_ref AS anchor_source_ref,
                       anchor.resource_ref AS anchor_resource_ref,
                       anchor.revision_id AS anchor_revision_id,
                       anchor.fragment_ref AS anchor_fragment_ref,
                       target.organization_id, target.source_ref,
                       target.resource_ref,
                       target.active_revision_id AS revision_id,
                       fragment.fragment_ref, 0 AS direction_order,
                       edge.ordinal AS edge_ordinal,
                       fragment.ordinal AS fragment_ordinal
                FROM anchors AS anchor
                JOIN public.revision_link_edge AS edge
                  ON edge.organization_id = anchor.organization_id
                 AND edge.source_resource_ref = anchor.resource_ref
                 AND edge.source_revision_id = anchor.revision_id
                JOIN public.file_acquisition AS acquisition
                  ON acquisition.organization_id = edge.organization_id
                 AND acquisition.source_id::text = anchor.source_ref
                 AND acquisition.relative_path = edge.target_path
                JOIN public.file_revision_snapshot AS target_snapshot
                  ON target_snapshot.organization_id = acquisition.organization_id
                 AND target_snapshot.acquisition_id = acquisition.acquisition_id
                JOIN public.context_resource AS target
                  ON target.organization_id = target_snapshot.organization_id
                 AND target.source_ref = acquisition.source_id::text
                 AND target.resource_ref = target_snapshot.resource_ref
                 AND target.active_revision_id = target_snapshot.revision_id
                 AND target.tombstoned IS FALSE
                JOIN public.context_fragment AS fragment
                  ON fragment.organization_id = target.organization_id
                 AND fragment.resource_ref = target.resource_ref
                 AND fragment.revision_id = target.active_revision_id
                UNION ALL
                SELECT anchor.organization_id, anchor.source_ref,
                       anchor.resource_ref, anchor.revision_id,
                       anchor.fragment_ref, backlink.organization_id,
                       backlink.source_ref, backlink.resource_ref,
                       backlink.active_revision_id, fragment.fragment_ref,
                       1, edge.ordinal, fragment.ordinal
                FROM anchors AS anchor
                JOIN public.file_revision_snapshot AS anchor_snapshot
                  ON anchor_snapshot.organization_id = anchor.organization_id
                 AND anchor_snapshot.resource_ref = anchor.resource_ref
                 AND anchor_snapshot.revision_id = anchor.revision_id
                JOIN public.file_acquisition AS anchor_acquisition
                  ON anchor_acquisition.organization_id = anchor.organization_id
                 AND anchor_acquisition.source_id::text = anchor.source_ref
                 AND anchor_acquisition.acquisition_id =
                     anchor_snapshot.acquisition_id
                JOIN public.revision_link_edge AS edge
                  ON edge.organization_id = anchor.organization_id
                 AND edge.target_path = anchor_acquisition.relative_path
                JOIN public.context_resource AS backlink
                  ON backlink.organization_id = edge.organization_id
                 AND backlink.source_ref = anchor.source_ref
                 AND backlink.resource_ref = edge.source_resource_ref
                 AND backlink.active_revision_id = edge.source_revision_id
                 AND backlink.tombstoned IS FALSE
                JOIN public.context_fragment AS fragment
                  ON fragment.organization_id = backlink.organization_id
                 AND fragment.resource_ref = backlink.resource_ref
                 AND fragment.revision_id = backlink.active_revision_id
            ), deduplicated AS (
                SELECT DISTINCT ON (
                    expanded.organization_id, expanded.source_ref,
                    expanded.resource_ref, expanded.revision_id,
                    expanded.fragment_ref
                ) expanded.* FROM expanded
                WHERE NOT (
                    expanded.organization_id = expanded.anchor_organization_id
                    AND expanded.source_ref = expanded.anchor_source_ref
                    AND expanded.resource_ref = expanded.anchor_resource_ref
                    AND expanded.revision_id = expanded.anchor_revision_id
                    AND expanded.fragment_ref = expanded.anchor_fragment_ref
                )
                ORDER BY expanded.organization_id, expanded.source_ref,
                         expanded.resource_ref, expanded.revision_id,
                         expanded.fragment_ref, expanded.direction_order,
                         expanded.edge_ordinal, expanded.fragment_ordinal
            )
            SELECT deduplicated.anchor_organization_id,
                   deduplicated.anchor_source_ref,
                   deduplicated.anchor_resource_ref,
                   deduplicated.anchor_revision_id,
                   deduplicated.anchor_fragment_ref,
                   deduplicated.organization_id, deduplicated.source_ref,
                   deduplicated.resource_ref, deduplicated.revision_id,
                   deduplicated.fragment_ref
            FROM deduplicated
            ORDER BY deduplicated.direction_order,
                     deduplicated.edge_ordinal,
                     deduplicated.fragment_ordinal,
                     deduplicated.source_ref, deduplicated.resource_ref,
                     deduplicated.revision_id, deduplicated.fragment_ref
            LIMIT requested_limit OFFSET requested_offset;
        END; $function$
        """
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_GRAPH_DEFINER}")
    for signature in (
        "context_internal_revision_link_edges_match(uuid,text,uuid,jsonb)",
        "context_runtime_resolve_one_hop_graph(uuid[],text[],text[],uuid[],text[],integer,integer)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
    op.execute(
        "GRANT EXECUTE ON FUNCTION public."
        "context_internal_revision_link_edges_match(uuid,text,uuid,jsonb) "
        f"TO {_WORKER_DEFINER}"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION public."
        "context_runtime_resolve_one_hop_graph"
        "(uuid[],text[],text[],uuid[],text[],integer,integer) "
        f"TO {_RUNTIME}"
    )


def _graph_definer_read_policies() -> None:
    for table, columns in _GRAPH_COLUMNS_BY_TABLE.items():
        op.execute(
            f"CREATE POLICY {table}_graph_definer_select ON {table} "
            f"FOR SELECT TO {_GRAPH_DEFINER} USING ({_TENANT})"
        )
        op.execute(f"GRANT SELECT ({columns}) ON {table} TO {_GRAPH_DEFINER}")


def upgrade() -> None:
    """Persist content-free link edges and expose a bounded locator resolver."""

    op.create_table(
        "revision_link_edge",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_resource_ref", sa.Text(), nullable=False),
        sa.Column("source_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("target_path", sa.Text(), nullable=False),
        sa.Column("link_kind", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "source_resource_ref",
            "source_revision_id",
            "ordinal",
            name="pk_revision_link_edge",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "source_resource_ref",
            "source_revision_id",
            "target_path",
            name="uq_revision_link_edge_target",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_resource_ref", "source_revision_id"],
            [
                "context_revision.organization_id",
                "context_revision.resource_ref",
                "context_revision.revision_id",
            ],
            name="fk_revision_link_edge_revision_same_organization",
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_revision_link_edge_ordinal"),
        sa.CheckConstraint(
            "link_kind IN ('wikilink', 'embed', 'markdown_link')",
            name="ck_revision_link_edge_kind",
        ),
        sa.CheckConstraint(
            "target_path ~ '^([^/\\\\]+/)*[^/\\\\]*\\.[mM][dD]$' "
            "AND target_path !~ '(^|/)(\\.|\\.\\.)(/|$)' "
            "AND char_length(target_path) <= 255",
            name="ck_revision_link_edge_target_path",
        ),
    )
    op.execute(
        "CREATE TRIGGER revision_link_edge_immutable "
        "BEFORE UPDATE OR DELETE ON revision_link_edge FOR EACH ROW "
        "EXECUTE FUNCTION public.context_content_reject_mutation()"
    )
    _secure_graph_table()
    _graph_definer_read_policies()
    _create_graph_functions()

    op.drop_constraint(
        "ck_file_publication_recovery_compiler",
        "file_publication_recovery",
        type_="check",
    )
    op.create_check_constraint(
        "ck_file_publication_recovery_compiler",
        "file_publication_recovery",
        "(compiler_version = 'context-engine-markdown-v1' AND config_version = 'markdown-config-v1') OR "
        "(compiler_version = 'context-engine-markdown-v2' AND config_version = 'markdown-config-v2') OR "
        "(compiler_version = 'context-engine-markdown-v3' AND config_version = 'markdown-config-v3')",
    )
    op.drop_constraint(
        "ck_file_revision_snapshot_structural_document",
        "file_revision_snapshot",
        type_="check",
    )
    op.create_check_constraint(
        "ck_file_revision_snapshot_structural_document",
        "file_revision_snapshot",
        "(compilation_document IS NULL AND compiler_version = 'context-engine-markdown-v1' AND config_version = 'markdown-config-v1') OR "
        "(compilation_document IS NOT NULL AND compiler_version = 'context-engine-markdown-v2' AND config_version = 'markdown-config-v2' AND compilation_document->>'compilationDigest' IS NOT DISTINCT FROM compilation_digest) OR "
        "(compilation_document IS NOT NULL AND compiler_version = 'context-engine-markdown-v3' AND config_version = 'markdown-config-v3' AND compilation_document->>'compilationDigest' IS NOT DISTINCT FROM compilation_digest AND compilation_document#>>'{provenance,canonicalizationProfile}' = 'markdown-rich-structural-v3' AND compilation_document#>>'{provenance,compilationDigestProfile}' = 'rfc8785-sha256-v3' AND jsonb_typeof(compilation_document->'revisionLinks') = 'array')",
    )

    _replace(
        _ACQUIRE,
        (
            (_ACQUIRE_V3_ANCHOR, _ACQUIRE_V3_REPLACEMENT),
            (_ACQUIRE_SNAPSHOT_MATCH_ANCHOR, _ACQUIRE_SNAPSHOT_MATCH_GRAPH),
        ),
    )
    _replace(
        _PREPARE,
        (
            (
                _PREPARE_V2_BRANCH,
                _PREPARE_V2_BRANCH + "\n" + _V3_RECOVERY_BRANCH,
            ),
            (_PREPARE_INSERT_ANCHOR, _PREPARE_INSERT_GRAPH),
        ),
    )
    _replace(
        _INDEX,
        ((_SNAPSHOT_MATCH_ANCHOR, _SNAPSHOT_MATCH_GRAPH),),
    )


def downgrade() -> None:
    """Refuse removal while active or recoverable rich graph state remains."""

    connection = op.get_bind()
    retained = connection.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM file_revision_snapshot WHERE compiler_version = 'context-engine-markdown-v3') "
            "OR EXISTS (SELECT 1 FROM file_publication_recovery WHERE compiler_version = 'context-engine-markdown-v3') "
            "OR EXISTS (SELECT 1 FROM revision_link_edge)"
        )
    ).scalar_one()
    if retained is True:
        raise RuntimeError(
            "rich Markdown graph downgrade requires no retained v3 state"
        )
    _replace(
        _INDEX,
        ((_SNAPSHOT_MATCH_GRAPH, _SNAPSHOT_MATCH_ANCHOR),),
    )
    _replace(
        _PREPARE,
        (
            (_PREPARE_INSERT_GRAPH, _PREPARE_INSERT_ANCHOR),
            (
                _PREPARE_V2_BRANCH + "\n" + _V3_RECOVERY_BRANCH,
                _PREPARE_V2_BRANCH,
            ),
        ),
    )
    _replace(
        _ACQUIRE,
        (
            (_ACQUIRE_SNAPSHOT_MATCH_GRAPH, _ACQUIRE_SNAPSHOT_MATCH_ANCHOR),
            (_ACQUIRE_V3_REPLACEMENT, _ACQUIRE_V3_ANCHOR),
        ),
    )
    op.drop_constraint(
        "ck_file_revision_snapshot_structural_document",
        "file_revision_snapshot",
        type_="check",
    )
    op.create_check_constraint(
        "ck_file_revision_snapshot_structural_document",
        "file_revision_snapshot",
        "(compilation_document IS NULL AND compiler_version = 'context-engine-markdown-v1' AND config_version = 'markdown-config-v1') OR "
        "(compilation_document IS NOT NULL AND compiler_version = 'context-engine-markdown-v2' AND config_version = 'markdown-config-v2' AND compilation_document->>'compilationDigest' IS NOT DISTINCT FROM compilation_digest)",
    )
    op.drop_constraint(
        "ck_file_publication_recovery_compiler",
        "file_publication_recovery",
        type_="check",
    )
    op.create_check_constraint(
        "ck_file_publication_recovery_compiler",
        "file_publication_recovery",
        "(compiler_version = 'context-engine-markdown-v1' AND config_version = 'markdown-config-v1') OR "
        "(compiler_version = 'context-engine-markdown-v2' AND config_version = 'markdown-config-v2')",
    )
    op.execute(
        "DROP FUNCTION public.context_runtime_resolve_one_hop_graph"
        "(uuid[],text[],text[],uuid[],text[],integer,integer)"
    )
    op.execute(
        "DROP FUNCTION public.context_internal_revision_link_edges_match"
        "(uuid,text,uuid,jsonb)"
    )
    for table, columns in reversed(_GRAPH_COLUMNS_BY_TABLE.items()):
        op.execute(f"DROP POLICY {table}_graph_definer_select ON {table}")
        op.execute(f"REVOKE SELECT ({columns}) ON {table} FROM {_GRAPH_DEFINER}")
    op.drop_table("revision_link_edge")
