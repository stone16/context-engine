from __future__ import annotations

from uuid import UUID

import pytest

from engine.supply.execution import (
    ConnectorCheckpointBinding,
    SupplyBridgeExecution,
)
from engine.supply.jobs import WorkerLeaseToken

SOURCE_VERSION_ID = UUID("0198fba1-f20d-78a2-b156-0706bc0abc8b")
WORKER_JOB_ID = UUID("0198fba2-0ed8-7649-912d-e5b9fc02ad0a")
ORGANIZATION_ID = UUID("0198fba3-3486-7e90-b8be-0bbfaeebf433")


def test_supply_bridge_execution_cannot_be_constructed_without_organization() -> None:
    with pytest.raises(TypeError):
        SupplyBridgeExecution(  # type: ignore[call-arg]
            source_version_id=SOURCE_VERSION_ID,
            worker_job_id=WORKER_JOB_ID,
            worker_lease=WorkerLeaseToken("synthetic.opaque.lease"),
        )


@pytest.mark.parametrize("organization_id", [None, "", "org-1"])
def test_supply_bridge_execution_rejects_invalid_tenant_context(
    organization_id: object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        SupplyBridgeExecution(
            organization_id=organization_id,  # type: ignore[arg-type]
            source_version_id=SOURCE_VERSION_ID,
            worker_job_id=WORKER_JOB_ID,
            worker_lease=WorkerLeaseToken("synthetic.opaque.lease"),
        )


def test_checkpoint_binding_has_no_default_tenant_context() -> None:
    with pytest.raises(TypeError):
        ConnectorCheckpointBinding(  # type: ignore[call-arg]
            source_version_id=SOURCE_VERSION_ID,
            worker_job_id=WORKER_JOB_ID,
        )


@pytest.mark.parametrize(
    ("field_name", "invalid"),
    [
        ("source_version_id", None),
        ("source_version_id", "version-1"),
        ("worker_job_id", None),
        ("worker_job_id", "job-1"),
        ("worker_lease", None),
        ("worker_lease", "synthetic.opaque.lease"),
    ],
)
def test_supply_bridge_execution_rejects_every_invalid_exact_binding_field(
    field_name: str,
    invalid: object,
) -> None:
    values: dict[str, object] = {
        "organization_id": ORGANIZATION_ID,
        "source_version_id": SOURCE_VERSION_ID,
        "worker_job_id": WORKER_JOB_ID,
        "worker_lease": WorkerLeaseToken("synthetic.opaque.lease"),
    }
    values[field_name] = invalid

    with pytest.raises((TypeError, ValueError)):
        SupplyBridgeExecution(**values)  # type: ignore[arg-type]
