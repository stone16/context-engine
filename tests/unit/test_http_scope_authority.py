from __future__ import annotations

import pickle
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, cast
from uuid import UUID

import pytest

from adapters.http.scope_authority import (
    DogfoodFileScopeAuthority,
    MissingTrustedScopeAuthority,
    ScopeAuthority,
    ScopeAuthorityIdentity,
    ScopeAuthorityUnavailable,
)
from engine.runtime.materialized import (
    MaterializedProjectionPort,
    MaterializedScopeOperands,
    _close_materialized_projection_scope,
    _construct_materialized_projection_session,
    _open_materialized_projection_scope,
)
from engine.runtime.release_lineage import (
    QWEN_VECTOR_INDEX_PROFILE_DIGEST_V1,
    QWEN_VECTOR_INDEX_PROFILE_REF_V1,
)
from engine.runtime.scope import MISSING_TRUSTED_SCOPE, ScopeSet, ScopeTarget
from engine.runtime.scope_authority import (
    TrustedScopeSnapshot,
    _require_active_trusted_scope_snapshot,
    _trusted_operands_from_snapshot,
)
from engine.supply import QWEN3_EMBEDDING_PROFILE
from tests.support.releases import active_runtime_release

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
        policy_epoch=7,
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
        "policy_epoch": 7,
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


def test_dogfood_scope_carries_independent_durable_operands() -> None:
    targets = tuple(
        ScopeTarget(ORGANIZATION_ID, "source:file", f"resource:{name}")
        for name in ("a", "b", "c", "d", "e")
    )

    class OperandPort:
        def current_scope_operands(
            self,
            active_revision_ids: tuple[UUID, ...],
        ) -> MaterializedScopeOperands:
            assert active_revision_ids == (
                UUID("0425904c-480f-4022-930f-15e8dd949a7e"),
            )
            return MaterializedScopeOperands(
                organization_boundary=frozenset(targets),
                membership_rights=frozenset(targets[:4]),
                principal_grants=frozenset(targets[:3]),
                source_native_acl=frozenset(targets[:2]),
                resource_acl=frozenset(targets[:1]),
            )

        def source_is_active(self, source_ref: UUID) -> bool:
            del source_ref
            return True

        def discover_vector(self, *args: object, **kwargs: object) -> tuple[()]:
            del args, kwargs
            return ()

        def discover_exact_phrase(self, phrase_digest: str) -> tuple[()]:
            del phrase_digest
            return ()

        def observe_publication(self, candidate_ref: object) -> None:
            del candidate_ref

        def locate(self, candidate_ref: object) -> None:
            del candidate_ref

        def project(self, locator: object) -> None:
            del locator

    projection_scope = _open_materialized_projection_scope()
    session = _construct_materialized_projection_session(
        authority_scope=projection_scope,
        port=cast(MaterializedProjectionPort, OperandPort()),
    )
    release = active_runtime_release(
        ORGANIZATION_ID,
        active_revision_refs=("0425904c-480f-4022-930f-15e8dd949a7e",),
        index_profile_ref=QWEN_VECTOR_INDEX_PROFILE_REF_V1,
        index_profile_digest=QWEN_VECTOR_INDEX_PROFILE_DIGEST_V1,
        embedding_provider_profile=QWEN3_EMBEDDING_PROFILE,
    )
    bound = replace(
        identity(),
        materialized_projection_session=session,
        active_runtime_release=release,
    )
    authority = DogfoodFileScopeAuthority(
        organization_id=ORGANIZATION_ID,
        principal_ref="principal-from-auth",
        agent_version_ref="agent-version-from-server",
        purposes=frozenset({"context.answer", "citation.open"}),
    )
    try:
        for purpose in ("context.answer", "citation.open"):
            with authority.current_scope(replace(bound, purpose=purpose)) as snapshot:
                operands = _trusted_operands_from_snapshot(snapshot)
                assert operands.organization_boundary == ScopeSet(frozenset(targets))
                assert operands.membership_rights == ScopeSet(frozenset(targets[:4]))
                assert operands.principal_grants == ScopeSet(frozenset(targets[:3]))
                assert operands.source_native_acl == ScopeSet(frozenset(targets[:2]))
                assert operands.resource_acl == ScopeSet(frozenset(targets[:1]))
                assert operands.agent_ceiling == ScopeSet(frozenset(targets))
                assert operands.purpose_policy == ScopeSet(frozenset(targets))
        for mismatched in (
            replace(bound, agent_version_ref="agent-version-not-authorized"),
            replace(bound, purpose="context.not-authorized"),
        ):
            with (
                pytest.raises(
                    ScopeAuthorityUnavailable,
                    match="scope binding is unavailable",
                ),
                authority.current_scope(mismatched),
            ):
                pass
    finally:
        _close_materialized_projection_scope(projection_scope)
