from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import UUID

import pytest

from engine.runtime.scope import MISSING_TRUSTED_SCOPE, ScopeSet, ScopeTarget
from engine.runtime.scope_authority import (
    TrustedScopeSnapshot,
    _close_scope_authority_scope,
    _construct_trusted_scope_snapshot,
    _open_scope_authority_scope,
    _require_active_trusted_scope_snapshot,
    _trusted_operands_from_snapshot,
)

CHECKED_AT = datetime(2026, 7, 21, 9, 0, tzinfo=UTC)
ORGANIZATION_ID = UUID("81e18bca-86a1-478a-937d-7675c6fe69b0")
USER_ID = UUID("d3d9893f-82d2-4890-8cb2-4c7e57a56f16")
MEMBERSHIP_ID = UUID("9c9e9f4c-a5ec-4417-9408-0346e1c6c998")


def test_trusted_scope_snapshot_is_nominal_frozen_and_scope_lived() -> None:
    with pytest.raises(TypeError, match="trusted scope authority"):
        TrustedScopeSnapshot()

    authority_scope = _open_scope_authority_scope()
    snapshot = _construct_trusted_scope_snapshot(
        authority_scope=authority_scope,
        organization_id=ORGANIZATION_ID,
        user_id=USER_ID,
        membership_id=MEMBERSHIP_ID,
        membership_version=3,
        principal_ref="principal-1",
        agent_version_ref="agent-version-1",
        purpose="context.answer",
        request_id="request-1",
        authentication_binding_ref="binding-1",
        checked_at=CHECKED_AT,
        organization_boundary=ScopeSet(frozenset()),
        membership_rights=MISSING_TRUSTED_SCOPE,
        principal_grants=ScopeSet(frozenset()),
        agent_ceiling=ScopeSet(frozenset()),
        source_native_acl=ScopeSet(frozenset()),
        resource_acl=ScopeSet(frozenset()),
        purpose_policy=ScopeSet(frozenset()),
    )

    _require_active_trusted_scope_snapshot(snapshot)
    rendered = repr(snapshot)
    assert str(ORGANIZATION_ID) not in rendered
    assert str(USER_ID) not in rendered
    assert str(MEMBERSHIP_ID) not in rendered
    assert "principal-1" not in rendered
    assert "agent-version-1" not in rendered
    assert "request-1" not in rendered
    assert "binding-1" not in rendered
    with pytest.raises(FrozenInstanceError):
        snapshot.principal_ref = "mutated"  # type: ignore[misc]

    _close_scope_authority_scope(authority_scope)
    with pytest.raises(ValueError, match="active trusted scope authority"):
        _require_active_trusted_scope_snapshot(snapshot)


def test_trusted_scope_snapshot_rejects_cross_organization_targets() -> None:
    authority_scope = _open_scope_authority_scope()
    cross_organization = ScopeSet(
        frozenset(
            {
                ScopeTarget(
                    organization_id=UUID(int=9),
                    source_ref="source-cross-org",
                )
            }
        )
    )

    with pytest.raises(ValueError, match="stay in Organization"):
        _construct_trusted_scope_snapshot(
            authority_scope=authority_scope,
            organization_id=ORGANIZATION_ID,
            user_id=USER_ID,
            membership_id=MEMBERSHIP_ID,
            membership_version=3,
            principal_ref="principal-1",
            agent_version_ref="agent-version-1",
            purpose="context.answer",
            request_id="request-1",
            authentication_binding_ref="binding-1",
            checked_at=CHECKED_AT,
            organization_boundary=cross_organization,
            membership_rights=MISSING_TRUSTED_SCOPE,
            principal_grants=ScopeSet(frozenset()),
            agent_ceiling=ScopeSet(frozenset()),
            source_native_acl=ScopeSet(frozenset()),
            resource_acl=ScopeSet(frozenset()),
            purpose_policy=ScopeSet(frozenset()),
        )

    _close_scope_authority_scope(authority_scope)


def test_trusted_scope_snapshot_revalidates_mutated_operands_on_consumption(
) -> None:
    authority_scope = _open_scope_authority_scope()
    snapshot = _construct_trusted_scope_snapshot(
        authority_scope=authority_scope,
        organization_id=ORGANIZATION_ID,
        user_id=USER_ID,
        membership_id=MEMBERSHIP_ID,
        membership_version=3,
        principal_ref="principal-1",
        agent_version_ref="agent-version-1",
        purpose="context.answer",
        request_id="request-1",
        authentication_binding_ref="binding-1",
        checked_at=CHECKED_AT,
        organization_boundary=ScopeSet(frozenset()),
        membership_rights=ScopeSet(frozenset()),
        principal_grants=ScopeSet(frozenset()),
        agent_ceiling=ScopeSet(frozenset()),
        source_native_acl=ScopeSet(frozenset()),
        resource_acl=ScopeSet(frozenset()),
        purpose_policy=ScopeSet(frozenset()),
    )
    cross_organization = ScopeSet(
        frozenset({ScopeTarget(UUID(int=9), "source-cross-org")})
    )
    object.__setattr__(snapshot, "principal_grants", cross_organization)

    with pytest.raises(ValueError, match="stay in Organization"):
        _trusted_operands_from_snapshot(snapshot)

    _close_scope_authority_scope(authority_scope)


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (
        ("organization_id", str(ORGANIZATION_ID)),
        ("user_id", str(USER_ID)),
        ("membership_id", str(MEMBERSHIP_ID)),
        ("membership_version", 0),
        ("membership_version", 1 << 63),
        ("principal_ref", " "),
        ("agent_version_ref", ""),
        ("purpose", object()),
        ("request_id", ""),
        ("authentication_binding_ref", True),
        ("checked_at", datetime(2026, 7, 21, 9, 0)),
    ),
)
def test_trusted_scope_snapshot_rejects_malformed_binding_facts(
    field_name: str,
    invalid_value: object,
) -> None:
    authority_scope = _open_scope_authority_scope()
    values: dict[str, object] = {
        "authority_scope": authority_scope,
        "organization_id": ORGANIZATION_ID,
        "user_id": USER_ID,
        "membership_id": MEMBERSHIP_ID,
        "membership_version": 3,
        "principal_ref": "principal-1",
        "agent_version_ref": "agent-version-1",
        "purpose": "context.answer",
        "request_id": "request-1",
        "authentication_binding_ref": "binding-1",
        "checked_at": CHECKED_AT,
        "organization_boundary": ScopeSet(frozenset()),
        "membership_rights": MISSING_TRUSTED_SCOPE,
        "principal_grants": ScopeSet(frozenset()),
        "agent_ceiling": ScopeSet(frozenset()),
        "source_native_acl": ScopeSet(frozenset()),
        "resource_acl": ScopeSet(frozenset()),
        "purpose_policy": ScopeSet(frozenset()),
    }
    values[field_name] = invalid_value

    with pytest.raises((TypeError, ValueError)):
        _construct_trusted_scope_snapshot(**values)  # type: ignore[arg-type]

    _close_scope_authority_scope(authority_scope)
