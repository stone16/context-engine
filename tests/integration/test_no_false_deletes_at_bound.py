from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import text

from applications.file_root_configuration import (
    WORKER_FILE_CURATED_SUBTREES_ENV,
    WORKER_MAX_FILE_CHANGE_BASELINE_SIZE_ENV,
)
from engine.persistence import DatabaseConfiguration, create_database_engine
from tests.integration.test_file_scan_operator_process import (
    _control,
    _register_activated_source,
    _scan,
    _status,
    _worker,
)
from tests.integration.test_file_scan_operator_process import (
    file_scan_scenario as _file_scan_scenario,
)

file_scan_scenario = _file_scan_scenario

pytestmark = pytest.mark.integration


def _delete_effects(
    configuration: DatabaseConfiguration,
    organization_id: UUID,
    source_ref: UUID,
) -> tuple[int, ...]:
    engine = create_database_engine(configuration)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*) FROM file_source_change_page
                       WHERE organization_id = :organization_id
                         AND source_id = :source_id),
                      (SELECT count(*) FROM file_source_change
                       WHERE organization_id = :organization_id
                         AND source_id = :source_id
                         AND change_kind = 'delete'),
                      (SELECT count(*) FROM file_delete_observation_execution
                       WHERE organization_id = :organization_id
                         AND source_id = :source_id),
                      (SELECT count(*) FROM file_resource_cleanup_intent
                       WHERE organization_id = :organization_id
                         AND source_id = :source_id),
                      (SELECT count(*) FROM context_resource
                       WHERE organization_id = :organization_id
                         AND source_ref = CAST(:source_id AS text)
                         AND tombstoned IS TRUE)
                    """
                ),
                {
                    "organization_id": organization_id,
                    "source_id": source_ref,
                },
            ).one()
    finally:
        engine.dispose()
    return tuple(row)


def test_bound_refusal_never_turns_an_unobserved_path_into_a_delete(
    migration_configuration: DatabaseConfiguration,
    file_scan_scenario: tuple[UUID, UUID, UUID, Path, dict[str, str]],
) -> None:
    organization_id, _membership_id, _receiver_id, root, environment = (
        file_scan_scenario
    )
    (root / "alpha.md").write_text("# Alpha\n", encoding="utf-8")
    (root / "beta.md").write_text("# Beta\n", encoding="utf-8")
    source_ref = _register_activated_source(organization_id, environment)
    _scan(organization_id, source_ref, environment)
    assert [_worker(environment)["outcome"] for _ in range(3)] == [
        "dispatched",
        "dispatched",
        "no_work",
    ]

    before = _delete_effects(
        migration_configuration,
        organization_id,
        source_ref,
    )
    (root / "beta.md").unlink()
    (root / "gamma.md").write_text("# Gamma\n", encoding="utf-8")
    bounded = environment | {WORKER_MAX_FILE_CHANGE_BASELINE_SIZE_ENV: "1"}

    refused = _control(
        [
            "scan",
            "--organization-id",
            str(organization_id),
            "--source-ref",
            str(source_ref),
        ],
        environment=bounded,
        check=False,
    )

    assert refused.returncode != 0
    assert refused.stdout == ""
    assert refused.stderr == "context-engine-control: operation refused\n"
    assert _delete_effects(
        migration_configuration,
        organization_id,
        source_ref,
    ) == before
    assert _status(organization_id, source_ref, bounded)["scanRefusal"] == {
        "category": "scan_bound_exceeded",
        "scanBound": 1,
    }

    completed = _scan(organization_id, source_ref, environment)
    assert completed["deletesObserved"] == 1
    assert _status(organization_id, source_ref, environment)["scanRefusal"] is None


def test_curated_selection_change_refuses_without_reinterpreting_baseline_paths(
    migration_configuration: DatabaseConfiguration,
    file_scan_scenario: tuple[UUID, UUID, UUID, Path, dict[str, str]],
) -> None:
    organization_id, _membership_id, _receiver_id, root, environment = (
        file_scan_scenario
    )
    curated = root / "curated"
    curated.mkdir()
    (curated / "inside.md").write_text("# Inside\n", encoding="utf-8")
    (root / "outside.md").write_text("# Outside\n", encoding="utf-8")
    source_ref = _register_activated_source(organization_id, environment)
    _scan(organization_id, source_ref, environment)
    before = _delete_effects(migration_configuration, organization_id, source_ref)
    selected = environment | {
        WORKER_FILE_CURATED_SUBTREES_ENV: json.dumps(
            {"operator-scan-root": "curated"}
        )
    }

    refused = _control(
        [
            "scan",
            "--organization-id",
            str(organization_id),
            "--source-ref",
            str(source_ref),
        ],
        environment=selected,
        check=False,
    )

    assert refused.returncode != 0
    assert refused.stdout == ""
    assert refused.stderr == "context-engine-control: operation refused\n"
    assert (
        _delete_effects(migration_configuration, organization_id, source_ref) == before
    )
