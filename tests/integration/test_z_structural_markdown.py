from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine

from engine.persistence import DatabaseConfiguration
from engine.runtime.package_digest import QueryDigestKeyring
from tests.integration.test_file_import_tracer import (
    _assert_structural_file_import_returns_coherent_authorized_units_over_http,
)

pytestmark = pytest.mark.integration


def test_structural_file_import_returns_coherent_authorized_units_over_http(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
) -> None:
    """Run after historical migration tests so immutable v2 evidence stays intact."""

    _assert_structural_file_import_returns_coherent_authorized_units_over_http(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        guarded_worker_engine,
        guarded_runtime_engine,
        query_digest_keyring,
    )
