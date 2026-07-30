from __future__ import annotations

import json
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from tests.integration.test_file_scan_operator_process import _control

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


def test_scan_all_and_status_discover_every_registered_source_without_source_args(
    file_scan_scenario: tuple[UUID, UUID, UUID, Path, dict[str, str]],
) -> None:
    organization_id, _membership_id, _receiver_id, first_root, environment = (
        file_scan_scenario
    )
    second_root = first_root.parent / "operator-scan-root-two"
    second_root.mkdir()
    (first_root / "first.md").write_text("# First\n\nFirst source.\n", encoding="utf-8")
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

    scan_arguments = ["scan-all", "--organization-id", str(organization_id)]
    scan = cast(
        dict[str, object],
        json.loads(_control(scan_arguments, environment=environment).stdout),
    )
    status_arguments = ["status", "--organization-id", str(organization_id)]
    status = cast(
        dict[str, object],
        json.loads(_control(status_arguments, environment=environment).stdout),
    )

    assert "--source-ref" not in scan_arguments
    assert "--source-ref" not in status_arguments
    assert scan["summary"] == {
        "changesAccepted": 2,
        "compilationRefusals": 0,
        "deletesObserved": 0,
        "importsScheduled": 2,
        "pathsObserved": 2,
        "sourceCount": 2,
    }
    scan_sources = cast(list[dict[str, object]], scan["sources"])
    status_sources = cast(list[dict[str, object]], status["sources"])
    expected_refs = sorted((str(first_source), str(second_source)))
    assert [item["sourceRef"] for item in scan_sources] == expected_refs
    assert [item["sourceRef"] for item in status_sources] == expected_refs
    assert cast(dict[str, object], status["summary"])["sourceCount"] == 2
