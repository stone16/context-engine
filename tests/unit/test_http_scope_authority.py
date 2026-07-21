from __future__ import annotations

import pickle
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, cast
from uuid import UUID

import pytest

from adapters.http.scope_authority import (
    MissingTrustedScopeAuthority,
    ScopeAuthority,
    ScopeAuthorityIdentity,
)
from engine.runtime.scope import MISSING_TRUSTED_SCOPE
from engine.runtime.scope_authority import (
    TrustedScopeSnapshot,
    _require_active_trusted_scope_snapshot,
    _trusted_operands_from_snapshot,
)

CHECKED_AT = datetime(2026, 7, 21, 9, 30, tzinfo=UTC)
ORGANIZATION_ID = UUID("81e18bca-86a1-478a-937d-7675c6fe69b0")
USER_ID = UUID("d3d9893f-82d2-4890-8cb2-4c7e57a56f16")
MEMBERSHIP_ID = UUID("9c9e9f4c-a5ec-4417-9408-0346e1c6c998")


def identity() -> ScopeAuthorityIdentity:
    return ScopeAuthorityIdentity(
        organization_id=ORGANIZATION_ID,
        user_id=USER_ID,
        membership_id=MEMBERSHIP_ID,
        membership_version=7,
        principal_ref="principal-from-auth",
        agent_version_ref="agent-version-from-server",
        purpose="context.answer",
        request_id="request-1",
        authentication_binding_ref="binding-from-auth",
        checked_at=CHECKED_AT,
    )


def _accepts_scope_authority(authority: ScopeAuthority) -> ScopeAuthority:
    return authority


def test_missing_authority_is_a_scope_authority_and_binds_every_identity_fact() -> None:
    expected = identity()
    authority = _accepts_scope_authority(MissingTrustedScopeAuthority())

    with authority.current_scope(expected) as snapshot:
        assert type(snapshot) is TrustedScopeSnapshot
        assert snapshot.organization_id == expected.organization_id
        assert snapshot.user_id == expected.user_id
        assert snapshot.membership_id == expected.membership_id
        assert snapshot.membership_version == expected.membership_version
        assert snapshot.principal_ref == expected.principal_ref
        assert snapshot.agent_version_ref == expected.agent_version_ref
        assert snapshot.purpose == expected.purpose
        assert snapshot.request_id == expected.request_id
        assert (
            snapshot.authentication_binding_ref
            == expected.authentication_binding_ref
        )
        assert snapshot.checked_at == expected.checked_at
        _require_active_trusted_scope_snapshot(snapshot)

        operands = _trusted_operands_from_snapshot(snapshot)
        assert operands.organization_boundary is MISSING_TRUSTED_SCOPE
        assert operands.membership_rights is MISSING_TRUSTED_SCOPE
        assert operands.principal_grants is MISSING_TRUSTED_SCOPE
        assert operands.agent_ceiling is MISSING_TRUSTED_SCOPE
        assert operands.source_native_acl is MISSING_TRUSTED_SCOPE
        assert operands.resource_acl is MISSING_TRUSTED_SCOPE
        assert operands.purpose_policy is MISSING_TRUSTED_SCOPE

    with pytest.raises(ValueError, match="active trusted scope authority"):
        _require_active_trusted_scope_snapshot(snapshot)


def test_missing_authority_closes_snapshot_when_caller_raises() -> None:
    captured: TrustedScopeSnapshot | None = None

    with (
        pytest.raises(RuntimeError, match="runtime failed"),
        MissingTrustedScopeAuthority().current_scope(identity()) as snapshot,
    ):
        captured = snapshot
        raise RuntimeError("runtime failed")

    assert captured is not None
    with pytest.raises(ValueError, match="active trusted scope authority"):
        _require_active_trusted_scope_snapshot(captured)


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (
        ("organization_id", str(ORGANIZATION_ID)),
        ("user_id", str(USER_ID)),
        ("membership_id", str(MEMBERSHIP_ID)),
        ("membership_version", 0),
        ("membership_version", True),
        ("membership_version", 1 << 63),
        ("principal_ref", " "),
        ("agent_version_ref", ""),
        ("purpose", object()),
        ("request_id", ""),
        ("authentication_binding_ref", True),
        ("checked_at", datetime(2026, 7, 21, 9, 30)),
        (
            "checked_at",
            datetime(
                2026,
                7,
                21,
                10,
                30,
                tzinfo=timezone(timedelta(hours=1)),
            ),
        ),
    ),
)
def test_scope_authority_identity_is_closed_and_exact(
    field_name: str,
    invalid_value: object,
) -> None:
    values: dict[str, object] = {
        "organization_id": ORGANIZATION_ID,
        "user_id": USER_ID,
        "membership_id": MEMBERSHIP_ID,
        "membership_version": 7,
        "principal_ref": "principal-from-auth",
        "agent_version_ref": "agent-version-from-server",
        "purpose": "context.answer",
        "request_id": "request-1",
        "authentication_binding_ref": "binding-from-auth",
        "checked_at": CHECKED_AT,
    }
    values[field_name] = invalid_value

    with pytest.raises((TypeError, ValueError), match="Scope authority"):
        ScopeAuthorityIdentity(**cast(Any, values))


def test_scope_authority_rejects_non_nominal_identity() -> None:
    with pytest.raises(TypeError, match="Scope authority identity"):
        MissingTrustedScopeAuthority().current_scope(cast(Any, object()))


def test_scope_authority_identity_is_frozen_slotted_and_non_serializable() -> None:
    expected = identity()

    with pytest.raises(FrozenInstanceError):
        expected.principal_ref = "mutated"  # type: ignore[misc]
    with pytest.raises(TypeError, match="__dict__"):
        vars(expected)
    with pytest.raises(TypeError, match="not serializable"):
        pickle.dumps(expected)


def test_scope_authority_identity_repr_does_not_expose_trusted_refs() -> None:
    rendered = repr(identity())

    assert "principal-from-auth" not in rendered
    assert "agent-version-from-server" not in rendered
    assert "context.answer" not in rendered
    assert "request-1" not in rendered
    assert "binding-from-auth" not in rendered
