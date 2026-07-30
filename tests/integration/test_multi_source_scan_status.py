from __future__ import annotations

import json
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from applications.operator_authentication import (
    CONTROL_OPERATOR_OPERATIONS_ENV,
    OPERATOR_ORGANIZATION_ENV,
)
from engine.persistence import DatabaseConfiguration, create_database_engine
from tests.integration.test_file_scan_operator_process import _control, _worker

pytestmark = pytest.mark.integration
pytest_plugins = ("tests.integration.test_file_scan_operator_process",)


def _register_activated(
    *,
    organization_id: UUID,
    root_ref: str,
    idempotency_key: str,
    environment: dict[str, str],
) -> UUID:
    registered = _control(
        [
            "register-file-source",
            "--organization-id",
            str(organization_id),
            "--display-name",
            "Multi-source fixture",
            "--root-ref",
            root_ref,
            "--idempotency-key",
            idempotency_key,
        ],
        environment=environment,
    )
    source_ref = UUID(json.loads(registered.stdout)["sourceRef"])
    for subcommand in ("activate-change-feed", "activate-delete-observations"):
        _control(
            [
                subcommand,
                "--organization-id",
                str(organization_id),
                "--source-ref",
                str(source_ref),
            ],
            environment=environment,
        )
    return source_ref


def test_scan_all_and_status_discover_every_active_source_without_source_args(
    migration_configuration: DatabaseConfiguration,
    file_scan_scenario: tuple[UUID, UUID, UUID, Path, dict[str, str]],
) -> None:
    organization_id, _membership_id, _receiver_id, first_root, environment = (
        file_scan_scenario
    )
    second_root = first_root.parent / "operator-scan-root-two"
    second_root.mkdir()
    (first_root / "first.md").write_text("# First\n\nFirst source.\n", encoding="utf-8")
    private_file_name = "private-refusal-path.md"
    (first_root / private_file_name).write_bytes(b"\xff\xfe")
    (second_root / "second.md").write_text(
        "# Second\n\nSecond source.\n",
        encoding="utf-8",
    )
    environment["CONTEXT_ENGINE_WORKER_FILE_ROOTS_JSON"] = json.dumps(
        {
            "operator-scan-root": str(first_root),
            "operator-scan-root-two": str(second_root),
        }
    )
    first_source = _register_activated(
        organization_id=organization_id,
        root_ref="operator-scan-root",
        idempotency_key="multi-source-first",
        environment=environment,
    )
    second_source = _register_activated(
        organization_id=organization_id,
        root_ref="operator-scan-root-two",
        idempotency_key="multi-source-second",
        environment=environment,
    )

    disabled_registration = _control(
        [
            "register-file-source",
            "--organization-id",
            str(organization_id),
            "--display-name",
            "Disabled history fixture",
            "--root-ref",
            "operator-scan-root",
            "--idempotency-key",
            "multi-source-disabled",
        ],
        environment=environment,
    )
    disabled_source = UUID(json.loads(disabled_registration.stdout)["sourceRef"])
    other_organization_id = uuid4()
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text("INSERT INTO organization (organization_id) VALUES (:org)"),
                {"org": other_organization_id},
            )
            connection.execute(
                text(
                    "UPDATE context_source SET lifecycle_state = 'disabled', "
                    "disabled_version_id = active_version_id, "
                    "disabled_at = statement_timestamp() "
                    "WHERE organization_id = :org AND source_id = :source"
                ),
                {"org": organization_id, "source": disabled_source},
            )
        other_environment = environment | {
            OPERATOR_ORGANIZATION_ENV: str(other_organization_id)
        }
        other_registration = _control(
            [
                "register-file-source",
                "--organization-id",
                str(other_organization_id),
                "--display-name",
                "Other Organization fixture",
                "--root-ref",
                "other-organization-root",
                "--idempotency-key",
                "other-organization-source",
            ],
            environment=other_environment,
        )
        other_source = UUID(json.loads(other_registration.stdout)["sourceRef"])

        without_source_read = environment.copy()
        without_source_read[CONTROL_OPERATOR_OPERATIONS_ENV] = without_source_read[
            CONTROL_OPERATOR_OPERATIONS_ENV
        ].replace(
            "read_source,",
            "",
        )
        for arguments in (
            ["scan-all", "--organization-id", str(organization_id)],
            ["status", "--organization-id", str(organization_id)],
        ):
            refused = _control(
                arguments,
                environment=without_source_read,
                check=False,
            )
            assert refused.returncode != 0
            assert refused.stdout == ""
            assert refused.stderr == "context-engine-control: operation refused\n"

        without_progress_read = environment.copy()
        without_progress_read[CONTROL_OPERATOR_OPERATIONS_ENV] = without_progress_read[
            CONTROL_OPERATOR_OPERATIONS_ENV
        ].replace(
            "read_source_progress,",
            "",
        )
        progress_refused = _control(
            ["status", "--organization-id", str(organization_id)],
            environment=without_progress_read,
            check=False,
        )
        assert progress_refused.returncode != 0
        assert progress_refused.stdout == ""
        assert progress_refused.stderr == "context-engine-control: operation refused\n"

        scan_arguments = ["scan-all", "--organization-id", str(organization_id)]
        scan = cast(
            dict[str, object],
            json.loads(_control(scan_arguments, environment=environment).stdout),
        )
        worker_outcomes = [cast(str, _worker(environment)["outcome"]) for _ in range(3)]
        assert sorted(worker_outcomes) == ["dispatched", "dispatched", "refused"]
        status_arguments = ["status", "--organization-id", str(organization_id)]
        status = cast(
            dict[str, object],
            json.loads(_control(status_arguments, environment=environment).stdout),
        )

        assert "--source-ref" not in scan_arguments
        assert "--source-ref" not in status_arguments
        assert scan["summary"] == {
            "changesAccepted": 3,
            "compilationRefusals": 1,
            "deletesObserved": 0,
            "importsScheduled": 3,
            "pathsObserved": 3,
            "refusalCount": 0,
            "sourceCount": 2,
        }
        assert scan["refusals"] == []
        scan_sources = cast(list[dict[str, object]], scan["sources"])
        status_sources = cast(list[dict[str, object]], status["sources"])
        expected_refs = sorted((str(first_source), str(second_source)))
        assert [item["sourceRef"] for item in scan_sources] == expected_refs
        assert [item["sourceRef"] for item in status_sources] == expected_refs
        assert cast(dict[str, object], status["summary"])["sourceCount"] == 2
        assert cast(dict[str, object], status["summary"])["refusalCount"] == 1
        rendered = json.dumps({"scan": scan, "status": status}, sort_keys=True)
        assert private_file_name not in rendered
        assert '"category": "invalid_utf8", "count": 1' in rendered
        assert str(disabled_source) not in rendered
        assert str(other_organization_id) not in rendered
        assert str(other_source) not in rendered
    finally:
        try:
            with migration_engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE source_version "
                        "DISABLE TRIGGER source_version_immutable"
                    )
                )
                connection.execute(
                    text(
                        "DELETE FROM context_source "
                        "WHERE organization_id = :organization_id"
                    ),
                    {"organization_id": other_organization_id},
                )
                connection.execute(
                    text(
                        "DELETE FROM source_version "
                        "WHERE organization_id = :organization_id"
                    ),
                    {"organization_id": other_organization_id},
                )
                connection.execute(
                    text(
                        "DELETE FROM organization "
                        "WHERE organization_id = :organization_id"
                    ),
                    {"organization_id": other_organization_id},
                )
                connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
                connection.execute(
                    text(
                        "ALTER TABLE source_version "
                        "ENABLE TRIGGER source_version_immutable"
                    )
                )
        finally:
            migration_engine.dispose()


def test_scan_all_reports_one_refusal_and_continues_with_later_sources(
    file_scan_scenario: tuple[UUID, UUID, UUID, Path, dict[str, str]],
) -> None:
    organization_id, _membership_id, _receiver_id, first_root, environment = (
        file_scan_scenario
    )
    roots = {
        "operator-scan-root": first_root,
        "operator-scan-root-two": first_root.parent / "operator-scan-root-two",
        "operator-scan-root-three": first_root.parent / "operator-scan-root-three",
    }
    for index, root in enumerate(roots.values(), start=1):
        root.mkdir(exist_ok=True)
        (root / f"source-{index}.md").write_text(
            f"# Source {index}\n\nContinue after a bounded refusal.\n",
            encoding="utf-8",
        )
    environment["CONTEXT_ENGINE_WORKER_FILE_ROOTS_JSON"] = json.dumps(
        {root_ref: str(root) for root_ref, root in roots.items()}
    )
    source_roots = {
        _register_activated(
            organization_id=organization_id,
            root_ref=root_ref,
            idempotency_key=f"continue-scan-{index}",
            environment=environment,
        ): root_ref
        for index, root_ref in enumerate(roots, start=1)
    }
    ordered_refs = sorted(source_roots)
    refusing_ref = ordered_refs[1]
    environment["CONTEXT_ENGINE_WORKER_FILE_ROOTS_JSON"] = json.dumps(
        {
            root_ref: str(roots[root_ref])
            for source_ref, root_ref in source_roots.items()
            if source_ref != refusing_ref
        }
    )

    completed = _control(
        ["scan-all", "--organization-id", str(organization_id)],
        environment=environment,
    )

    report = cast(dict[str, object], json.loads(completed.stdout))
    successful_sources = cast(list[dict[str, object]], report["sources"])
    refusals = cast(list[dict[str, object]], report["refusals"])
    assert [source["sourceRef"] for source in successful_sources] == [
        str(ordered_refs[0]),
        str(ordered_refs[2]),
    ]
    assert refusals == [
        {
            "reasonCategory": "operation_refused",
            "sourceRef": str(refusing_ref),
        }
    ]
    assert report["summary"] == {
        "changesAccepted": 2,
        "compilationRefusals": 0,
        "deletesObserved": 0,
        "importsScheduled": 2,
        "pathsObserved": 2,
        "refusalCount": 1,
        "sourceCount": 3,
    }


def test_scan_all_keeps_shared_configuration_failure_command_fatal(
    file_scan_scenario: tuple[UUID, UUID, UUID, Path, dict[str, str]],
) -> None:
    organization_id, _membership_id, _receiver_id, root, environment = (
        file_scan_scenario
    )
    (root / "configured.md").write_text(
        "# Configured\n\nShared configuration must fail the whole command.\n",
        encoding="utf-8",
    )
    _register_activated(
        organization_id=organization_id,
        root_ref="operator-scan-root",
        idempotency_key="scan-all-shared-configuration",
        environment=environment,
    )
    absent_configuration = environment.copy()
    del absent_configuration["CONTEXT_ENGINE_FILE_CHANGE_PROVIDER_SIGNING_KEY_HEX"]

    refused = _control(
        ["scan-all", "--organization-id", str(organization_id)],
        environment=absent_configuration,
        check=False,
    )

    assert refused.returncode != 0
    assert refused.stdout == ""
    assert refused.stderr == "context-engine-control: operation refused\n"
