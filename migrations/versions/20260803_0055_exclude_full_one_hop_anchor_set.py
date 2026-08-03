# ruff: noqa: E501
"""Exclude every main-path anchor from one-hop expansion.

Revision ID: 20260803_0055
Revises: 20260803_0054
Create Date: 2026-08-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260803_0055"
down_revision: str | None = "20260803_0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFINER = "context_engine_graph_definer"
_RUNTIME = "context_engine_runtime"
_FUNCTION = "context_runtime_resolve_one_hop_graph"
_SIGNATURE = "(uuid[],text[],text[],uuid[],text[],integer,integer)"


def _replace_function(*, exclude_full_anchor_set: bool) -> None:
    if exclude_full_anchor_set:
        anchor_exclusion = """
                WHERE NOT EXISTS (
                    SELECT 1 FROM anchors AS requested_anchor
                    WHERE expanded.organization_id =
                              requested_anchor.organization_id
                      AND expanded.source_ref = requested_anchor.source_ref
                      AND expanded.resource_ref = requested_anchor.resource_ref
                      AND expanded.revision_id = requested_anchor.revision_id
                      AND expanded.fragment_ref = requested_anchor.fragment_ref
                )
        """
    else:
        anchor_exclusion = """
                WHERE NOT (
                    expanded.organization_id = expanded.anchor_organization_id
                    AND expanded.source_ref = expanded.anchor_source_ref
                    AND expanded.resource_ref = expanded.anchor_resource_ref
                    AND expanded.revision_id = expanded.anchor_revision_id
                    AND expanded.fragment_ref = expanded.anchor_fragment_ref
                )
        """
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.{_FUNCTION}(
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
                {anchor_exclusion}
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
    op.execute(f"REVOKE ALL ON FUNCTION public.{_FUNCTION}{_SIGNATURE} FROM PUBLIC")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_FUNCTION}{_SIGNATURE} TO {_RUNTIME}"
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


def upgrade() -> None:
    """Exclude any exact main-path CandidateRef from graph expansion."""

    _replace_function(exclude_full_anchor_set=True)


def downgrade() -> None:
    """Restore the per-root anchor exclusion."""

    _replace_function(exclude_full_anchor_set=False)
