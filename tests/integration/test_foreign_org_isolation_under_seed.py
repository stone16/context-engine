"""Retained foreign-Organization File lineage stays unreadable under FORCE RLS.

The corpus-independence evidence deliberately leaves another Organization's
nested File lineage in the harness. Order-independent downgrade guards must
never be bought with weaker tenant isolation, so this proves the seeded rows are
real, invisible to every non-owner role, and invisible across Organizations
under the tenant-scoped policy.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from engine.persistence import DatabaseConfiguration, create_database_engine
from tests.support.retained_corpus import retained_file_lineage

pytestmark = pytest.mark.integration
RETAINED_LINEAGE_TABLES = (
    "file_acquisition",
    "file_source_change",
    "file_source_change_page",
)
TENANT_POLICY_DEFINER = "context_engine_worker_lease_definer"


@pytest.fixture
def foreign_organizations(
    migration_configuration: DatabaseConfiguration,
) -> Iterator[tuple[UUID, ...]]:
    with retained_file_lineage(migration_configuration) as organization_ids:
        yield organization_ids


def _owner_rows(configuration: DatabaseConfiguration, organization_id: UUID) -> int:
    engine = create_database_engine(configuration)
    try:
        with engine.connect() as connection:
            return sum(
                connection.execute(
                    text(
                        f"SELECT count(*) FROM {table} "  # noqa: S608
                        "WHERE organization_id = :organization_id"
                    ),
                    {"organization_id": organization_id},
                ).scalar_one()
                for table in RETAINED_LINEAGE_TABLES
            )
    finally:
        engine.dispose()


def test_seeded_lineage_exists_for_the_owner(
    foreign_organizations: tuple[UUID, ...],
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Isolation assertions measure hidden rows, never absent ones."""

    for organization_id in foreign_organizations:
        assert _owner_rows(migration_configuration, organization_id) > 0


@pytest.mark.parametrize("table", RETAINED_LINEAGE_TABLES)
def test_seeded_lineage_is_unreadable_by_every_non_owner_role(
    table: str,
    foreign_organizations: tuple[UUID, ...],
    guarded_control_engine: Engine,
    guarded_runtime_engine: Engine,
    guarded_worker_engine: Engine,
    guarded_scheduler_engine: Engine,
) -> None:
    assert foreign_organizations
    for engine in (
        guarded_control_engine,
        guarded_runtime_engine,
        guarded_worker_engine,
        guarded_scheduler_engine,
    ):
        with pytest.raises(DBAPIError), engine.connect() as connection:
            connection.execute(text(f"SELECT count(*) FROM {table}"))  # noqa: S608


@pytest.mark.parametrize("table", RETAINED_LINEAGE_TABLES)
def test_seeded_lineage_stays_inside_its_own_organization(
    table: str,
    foreign_organizations: tuple[UUID, ...],
    migration_configuration: DatabaseConfiguration,
) -> None:
    """One Organization's tenant context never reaches another's retained rows."""

    reader, hidden = foreign_organizations[0], foreign_organizations[1]
    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            connection.execute(text(f"SET LOCAL ROLE {TENANT_POLICY_DEFINER}"))
            connection.execute(
                text("SELECT set_config('app.organization_id', :reader, true)"),
                {"reader": str(reader)},
            )
            visible_to_reader = connection.execute(
                text(
                    f"SELECT count(*) FROM {table} "  # noqa: S608
                    "WHERE organization_id = :reader"
                ),
                {"reader": reader},
            ).scalar_one()
            visible_across_tenants = connection.execute(
                text(
                    f"SELECT count(*) FROM {table} "  # noqa: S608
                    "WHERE organization_id = :hidden"
                ),
                {"hidden": hidden},
            ).scalar_one()
    finally:
        engine.dispose()
    assert visible_to_reader > 0
    assert visible_across_tenants == 0


@pytest.mark.parametrize("table", RETAINED_LINEAGE_TABLES)
def test_retained_lineage_tables_force_row_level_security(
    table: str,
    migration_configuration: DatabaseConfiguration,
) -> None:
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            enabled, forced = connection.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity "
                    "FROM pg_catalog.pg_class WHERE oid = CAST(:table AS regclass)"
                ),
                {"table": table},
            ).one()
            tenant_expressions = connection.execute(
                text(
                    "SELECT pg_catalog.pg_get_expr(polqual, polrelid) "
                    "FROM pg_catalog.pg_policy "
                    "WHERE polrelid = CAST(:table AS regclass)"
                ),
                {"table": table},
            ).scalars().all()
    finally:
        engine.dispose()
    assert (enabled, forced) == (True, True)
    assert any(
        expression is not None and "app.organization_id" in expression
        for expression in tenant_expressions
    )
