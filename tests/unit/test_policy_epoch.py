from dataclasses import FrozenInstanceError
from uuid import UUID

import pytest

from engine.runtime.policy_epoch import (
    PolicyEpochAuthorityUnavailable,
    PolicyEpochSession,
    PolicyEpochVerification,
    _close_policy_epoch_authority_scope,
    _construct_policy_epoch_session,
    _observe_current_policy_epoch,
    _open_policy_epoch_authority_scope,
    _policy_epoch_is_current,
    _require_active_policy_epoch_verification,
)

ORGANIZATION_ID = UUID("81e18bca-86a1-478a-937d-7675c6fe69b0")


class MutablePolicyEpochPort:
    def __init__(self, current: object = 7) -> None:
        self.current = current
        self.reads: list[UUID] = []

    def read_current_epoch(self, organization_id: UUID) -> object:
        self.reads.append(organization_id)
        return self.current


def test_policy_epoch_verification_is_nominal_org_bound_and_scope_lived() -> None:
    with pytest.raises(TypeError, match="trusted transaction"):
        PolicyEpochSession()
    with pytest.raises(TypeError, match="trusted Policy Epoch authority"):
        PolicyEpochVerification()

    authority_scope = _open_policy_epoch_authority_scope()
    port = MutablePolicyEpochPort()
    session = _construct_policy_epoch_session(
        authority_scope=authority_scope,
        organization_id=ORGANIZATION_ID,
        port=port,
    )
    verification = _observe_current_policy_epoch(session)

    assert verification.organization_id == ORGANIZATION_ID
    assert verification.policy_epoch == 7
    assert verification.validation_session is session
    assert port.reads == [ORGANIZATION_ID]
    _require_active_policy_epoch_verification(verification)
    with pytest.raises(FrozenInstanceError):
        verification.policy_epoch = 8  # type: ignore[misc]
    assert str(ORGANIZATION_ID) not in repr(verification)

    _close_policy_epoch_authority_scope(authority_scope)
    with pytest.raises(ValueError, match="active Policy Epoch authority"):
        _require_active_policy_epoch_verification(verification)


@pytest.mark.parametrize("malformed", (None, True, 0, -1, 1 << 63, "7"))
def test_missing_or_malformed_policy_epoch_is_authority_unavailable(
    malformed: object,
) -> None:
    authority_scope = _open_policy_epoch_authority_scope()
    session = _construct_policy_epoch_session(
        authority_scope=authority_scope,
        organization_id=ORGANIZATION_ID,
        port=MutablePolicyEpochPort(malformed),
    )

    with pytest.raises(PolicyEpochAuthorityUnavailable):
        _observe_current_policy_epoch(session)

    _close_policy_epoch_authority_scope(authority_scope)


@pytest.mark.security_evidence(id="PROP-REVOCATION-006", layer="property")
def test_current_validation_re_reads_durable_epoch_and_fails_closed_on_change(
) -> None:
    authority_scope = _open_policy_epoch_authority_scope()
    port = MutablePolicyEpochPort(11)
    session = _construct_policy_epoch_session(
        authority_scope=authority_scope,
        organization_id=ORGANIZATION_ID,
        port=port,
    )
    verification = _observe_current_policy_epoch(session)

    assert _policy_epoch_is_current(verification) is True
    port.current = 12
    assert _policy_epoch_is_current(verification) is False
    assert port.reads == [ORGANIZATION_ID, ORGANIZATION_ID, ORGANIZATION_ID]

    _close_policy_epoch_authority_scope(authority_scope)


def test_validation_rejects_a_mutated_or_cross_org_session_binding() -> None:
    authority_scope = _open_policy_epoch_authority_scope()
    session = _construct_policy_epoch_session(
        authority_scope=authority_scope,
        organization_id=ORGANIZATION_ID,
        port=MutablePolicyEpochPort(),
    )
    verification = _observe_current_policy_epoch(session)
    object.__setattr__(session, "organization_id", UUID(int=9))

    with pytest.raises(ValueError, match="Organization binding"):
        _policy_epoch_is_current(verification)

    _close_policy_epoch_authority_scope(authority_scope)
