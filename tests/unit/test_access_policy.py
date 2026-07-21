from __future__ import annotations

from uuid import uuid4

import pytest

from engine.persistence.access_policy import PolicyEpoch, ResourceAccessRevocation


def test_access_revocation_requires_one_exact_positive_versioned_grant() -> None:
    organization_id = uuid4()
    revocation = ResourceAccessRevocation(
        organization_id=organization_id,
        resource_ref="resource:payroll",
        principal_ref="principal:member-a",
        expected_access_version=7,
    )

    assert revocation.organization_id == organization_id
    assert revocation.resource_ref == "resource:payroll"
    assert revocation.principal_ref == "principal:member-a"
    assert revocation.expected_access_version == 7


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"organization_id": "not-a-uuid"}, TypeError),
        ({"resource_ref": "  "}, ValueError),
        ({"principal_ref": ""}, ValueError),
        ({"expected_access_version": 0}, ValueError),
        ({"expected_access_version": True}, ValueError),
        ({"expected_access_version": 2**63}, ValueError),
    ],
)
def test_access_revocation_rejects_ambiguous_or_unbounded_values(
    overrides: dict[str, object],
    error: type[Exception],
) -> None:
    values: dict[str, object] = {
        "organization_id": uuid4(),
        "resource_ref": "resource:payroll",
        "principal_ref": "principal:member-a",
        "expected_access_version": 1,
        **overrides,
    }

    with pytest.raises(error):
        ResourceAccessRevocation(**values)  # type: ignore[arg-type]


def test_policy_epoch_is_explicitly_bound_to_one_organization() -> None:
    organization_id = uuid4()

    epoch = PolicyEpoch(organization_id=organization_id, value=9)

    assert epoch.organization_id == organization_id
    assert epoch.value == 9


@pytest.mark.parametrize("value", [0, True, 2**63])
def test_policy_epoch_rejects_non_positive_or_out_of_range_values(
    value: object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        PolicyEpoch(organization_id=uuid4(), value=value)  # type: ignore[arg-type]
