from __future__ import annotations

from typing import cast

import pytest
from sqlalchemy import Engine, text

from engine.persistence import assert_learning_role, assert_runtime_role

pytestmark = pytest.mark.integration

UNAVAILABLE_M0_TABLE_STEMS = (
    "citation",
    "model_gateway",
    "model_input",
)
UNAVAILABLE_M0_FUNCTION_STEMS = (
    "citation",
    "model_gateway",
    "model_input",
)
ORGANIZATION_BOUND_LEARNING_TABLES = {
    "active_release_manifest",
    "release_candidate",
    "release_evaluation",
    "release_manifest",
    "release_operator_grant",
    "release_promotion_audit",
}


def _public_application_objects(engine: Engine) -> tuple[set[str], set[str]]:
    with engine.connect() as connection:
        tables = {
            cast(str, row.table_name)
            for row in connection.execute(
                text(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_type = 'BASE TABLE'
                    """
                )
            )
        }
        functions = {
            cast(str, row.function_name)
            for row in connection.execute(
                text(
                    """
                    SELECT procedure.proname AS function_name
                    FROM pg_proc AS procedure
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = procedure.pronamespace
                    WHERE namespace.nspname = 'public'
                    """
                )
            )
        }
    return tables, functions


@pytest.mark.security_evidence(id="PG-CITATION-AUTH-010", layer="postgres")
def test_unavailable_citation_and_real_provider_carriers_fail_closed(
    guarded_runtime_engine: Engine,
) -> None:
    """Activated egress state does not activate citation or real-provider state."""

    tables, functions = _public_application_objects(guarded_runtime_engine)
    with guarded_runtime_engine.connect() as connection:
        assert_runtime_role(connection)

    assert all(
        stem not in table_name.casefold()
        for stem in UNAVAILABLE_M0_TABLE_STEMS
        for table_name in tables
    )
    assert all(
        stem not in function_name.casefold()
        for stem in UNAVAILABLE_M0_FUNCTION_STEMS
        for function_name in functions
    )


@pytest.mark.security_evidence(id="PG-CROSS-ORG-LEARN-015", layer="postgres")
def test_learning_persistence_is_organization_bound(
    guarded_learning_engine: Engine,
) -> None:
    """Every active M0 Learning artifact has forced Organization-scoped RLS."""

    with guarded_learning_engine.connect() as connection:
        assert_learning_role(connection)
        rows = connection.execute(
            text(
                """
                SELECT
                    table_record.relname AS table_name,
                    table_record.relrowsecurity,
                    table_record.relforcerowsecurity,
                    EXISTS (
                        SELECT 1
                        FROM pg_attribute AS column_record
                        WHERE column_record.attrelid = table_record.oid
                          AND column_record.attname = 'organization_id'
                          AND column_record.attnum > 0
                          AND NOT column_record.attisdropped
                    ) AS has_organization_id,
                        COALESCE(
                            bool_and(
                                position(
                                    'organization_id' IN
                                    COALESCE(policy.qual, '') || ' ' ||
                                    COALESCE(policy.with_check, '')
                                ) > 0
                            ) FILTER (
                                WHERE policy.policyname IS NOT NULL
                                  AND NOT (
                                      'context_engine_migrator' = ANY(policy.roles)
                                  )
                            ),
                            false
                        ) AS every_policy_is_organization_bound
                FROM pg_class AS table_record
                JOIN pg_namespace AS namespace
                  ON namespace.oid = table_record.relnamespace
                LEFT JOIN pg_policies AS policy
                  ON policy.schemaname = namespace.nspname
                 AND policy.tablename = table_record.relname
                WHERE namespace.nspname = 'public'
                  AND table_record.relname = ANY(:table_names)
                GROUP BY table_record.oid, table_record.relname
                ORDER BY table_record.relname
                """
            ),
            {"table_names": sorted(ORGANIZATION_BOUND_LEARNING_TABLES)},
        ).mappings().all()

    observed = {cast(str, row["table_name"]): row for row in rows}
    assert set(observed) == ORGANIZATION_BOUND_LEARNING_TABLES
    for table_name in sorted(ORGANIZATION_BOUND_LEARNING_TABLES):
        row = observed[table_name]
        assert row["has_organization_id"] is True
        assert row["relrowsecurity"] is True
        assert row["relforcerowsecurity"] is True
        assert row["every_policy_is_organization_bound"] is True
