from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine, text

from engine.persistence import DatabaseConfiguration, create_database_engine
from tests.support.file_imports import (
    delete_file_import_scenario,
    prepare_file_import_scenario,
    prepare_repeat_file_import,
    run_file_import,
)

pytestmark = pytest.mark.integration


def test_rich_file_publication_persists_exact_immutable_revision_links(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=(
            b"# Synthetic root\n\n"
            b"Adjacent [[adjacent]] and [backlink](folder/backlink.md).\n"
            b"Duplicate [[adjacent#section|label]].\n"
        ),
    )
    assert scenario.token is not None
    engine = create_database_engine(migration_configuration)
    try:
        published = run_file_import(
            scenario,
            scenario.prepared,
            scenario.token,
            guarded_worker_engine,
            config_version="markdown-config-v3",
        )
        revision_id = UUID(published.candidate_refs[0].revision_ref)

        with engine.connect() as connection:
            snapshot = connection.execute(
                text(
                    """
                    SELECT compiler_version, config_version,
                           compilation_document->'revisionLinks' AS revision_links
                    FROM file_revision_snapshot
                    WHERE organization_id = :organization_id
                      AND resource_ref = :resource_ref
                      AND revision_id = :revision_id
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "resource_ref": published.candidate_refs[0].resource_ref,
                    "revision_id": revision_id,
                },
            ).one()
            edges = tuple(
                tuple(row)
                for row in connection.execute(
                    text(
                        """
                        SELECT ordinal, target_path, link_kind
                        FROM revision_link_edge
                        WHERE organization_id = :organization_id
                          AND source_resource_ref = :resource_ref
                          AND source_revision_id = :revision_id
                        ORDER BY ordinal
                        """
                    ),
                    {
                        "organization_id": scenario.organization_id,
                        "resource_ref": published.candidate_refs[0].resource_ref,
                        "revision_id": revision_id,
                    },
                )
            )
            graph_privileges = tuple(
                tuple(row)
                for row in connection.execute(
                    text(
                        "SELECT table_name, column_name "
                        "FROM information_schema.column_privileges "
                        "WHERE table_schema = 'public' "
                        "AND grantee = 'context_engine_graph_definer' "
                        "AND privilege_type = 'SELECT' "
                        "ORDER BY table_name, column_name"
                    )
                )
            )

        assert snapshot.compiler_version == "context-engine-markdown-v3"
        assert snapshot.config_version == "markdown-config-v3"
        assert snapshot.revision_links == [
            {"kind": "wikilink", "targetPath": "adjacent.md"},
            {"kind": "markdown_link", "targetPath": "folder/backlink.md"},
        ]
        assert edges == (
            (0, "adjacent.md", "wikilink"),
            (1, "folder/backlink.md", "markdown_link"),
        )
        assert graph_privileges == (
            ("context_fragment", "fragment_ref"),
            ("context_fragment", "ordinal"),
            ("context_fragment", "organization_id"),
            ("context_fragment", "resource_ref"),
            ("context_fragment", "revision_id"),
            ("context_resource", "active_revision_id"),
            ("context_resource", "organization_id"),
            ("context_resource", "resource_ref"),
            ("context_resource", "source_ref"),
            ("context_resource", "tombstoned"),
            ("file_acquisition", "acquisition_id"),
            ("file_acquisition", "organization_id"),
            ("file_acquisition", "relative_path"),
            ("file_acquisition", "source_id"),
            ("file_revision_snapshot", "acquisition_id"),
            ("file_revision_snapshot", "organization_id"),
            ("file_revision_snapshot", "resource_ref"),
            ("file_revision_snapshot", "revision_id"),
            ("membership", "membership_id"),
            ("membership", "membership_version"),
            ("membership", "organization_id"),
            ("membership", "status"),
            ("membership", "user_id"),
            ("membership", "valid_from"),
            ("membership", "valid_until"),
            ("revision_link_edge", "link_kind"),
            ("revision_link_edge", "ordinal"),
            ("revision_link_edge", "organization_id"),
            ("revision_link_edge", "source_resource_ref"),
            ("revision_link_edge", "source_revision_id"),
            ("revision_link_edge", "target_path"),
        )

        repeated, repeated_token = prepare_repeat_file_import(
            scenario,
            guarded_control_engine,
            idempotency_key="repeat-rich-link-publication",
        )
        unchanged = run_file_import(
            scenario,
            repeated,
            repeated_token,
            guarded_worker_engine,
            config_version="markdown-config-v3",
        )
        assert unchanged.outcome == "unchanged"
        assert unchanged.candidate_refs[0].revision_ref == str(revision_id)

        with (
            pytest.raises(Exception, match="immutable"),
            engine.begin() as connection,
        ):
            connection.execute(
                text(
                    """
                    UPDATE revision_link_edge SET target_path = 'changed.md'
                    WHERE organization_id = :organization_id
                      AND source_revision_id = :revision_id
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "revision_id": revision_id,
                },
            )
    finally:
        engine.dispose()
        delete_file_import_scenario(
            migration_configuration,
            scenario.organization_id,
        )
