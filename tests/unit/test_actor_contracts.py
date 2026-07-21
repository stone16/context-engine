from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, cast
from uuid import UUID

import pytest

from adapters.http.authentication import (
    InvalidAuthenticationContext,
    VerifiedAuthenticationContext,
)
from adapters.http.scope_authority import (
    MissingTrustedScopeAuthority,
    ScopeAuthorityIdentity,
)
from engine.runtime.actor import (
    CurrentMembershipVerification,
    MembershipVerificationProvenance,
    UserActor,
    UserActorConstructionProvenance,
    _close_membership_authority_scope,
    _construct_current_membership_verification,
    _construct_user_actor,
    _MembershipAuthorityScope,
    _open_membership_authority_scope,
    _require_active_current_membership_verification,
    _require_active_user_actor,
)
from engine.runtime.invocation import _construct_authenticated_http_invocation
from engine.runtime.organization import (
    _construct_existing_http_organization_verification,
)
from engine.runtime.policy_epoch import (
    PolicyEpochVerification,
    _construct_policy_epoch_session,
    _observe_current_policy_epoch,
    _open_policy_epoch_authority_scope,
)

CHECKED_AT = datetime(2026, 7, 21, 6, 0, tzinfo=UTC)
ORGANIZATION_ID = UUID("81e18bca-86a1-478a-937d-7675c6fe69b0")
USER_ID = UUID("d3d9893f-82d2-4890-8cb2-4c7e57a56f16")
MEMBERSHIP_ID = UUID("9c9e9f4c-a5ec-4417-9408-0346e1c6c998")


class _CurrentEpochPort:
    def read_current_epoch(self, organization_id: UUID) -> object:
        del organization_id
        return 7


_POLICY_EPOCH_SCOPE = _open_policy_epoch_authority_scope()


def policy_epoch_verification(
    organization_id: UUID = ORGANIZATION_ID,
) -> PolicyEpochVerification:
    return _observe_current_policy_epoch(
        _construct_policy_epoch_session(
            authority_scope=_POLICY_EPOCH_SCOPE,
            organization_id=organization_id,
            port=_CurrentEpochPort(),
        )
    )


def verified_authentication_context(
    *,
    user_ref: object = str(USER_ID),
    membership_ref: object = str(MEMBERSHIP_ID),
    membership_version: object = 7,
) -> VerifiedAuthenticationContext:
    return VerifiedAuthenticationContext(
        organization_ref=str(ORGANIZATION_ID),
        user_ref=user_ref,  # type: ignore[arg-type]
        principal_ref="principal-not-the-user-id",
        membership_ref=membership_ref,  # type: ignore[arg-type]
        membership_version=membership_version,  # type: ignore[arg-type]
        agent_version_ref="agent-version-1",
        authenticated_application_ref="application-1",
        authentication_binding_ref="binding-1",
    )


def current_membership_proof(
    *,
    scope: object,
    organization_id: UUID = ORGANIZATION_ID,
    user_id: UUID = USER_ID,
    membership_id: UUID = MEMBERSHIP_ID,
    membership_version: int = 7,
    principal_ref: str = "principal-not-the-user-id",
    request_id: str = "request-1",
    authentication_binding_ref: str = "binding-1",
    checked_at: datetime = CHECKED_AT,
    epoch_verification: PolicyEpochVerification | None = None,
) -> CurrentMembershipVerification:
    return _construct_current_membership_verification(
        authority_scope=scope,  # type: ignore[arg-type]
        organization_id=organization_id,
        user_id=user_id,
        membership_id=membership_id,
        membership_version=membership_version,
        principal_ref=principal_ref,
        request_id=request_id,
        authentication_binding_ref=authentication_binding_ref,
        checked_at=checked_at,
        policy_epoch_verification=(
            epoch_verification or policy_epoch_verification(organization_id)
        ),
    )


def scope_authority_identity() -> ScopeAuthorityIdentity:
    return ScopeAuthorityIdentity(
        organization_id=ORGANIZATION_ID,
        user_id=USER_ID,
        membership_id=MEMBERSHIP_ID,
        membership_version=7,
        policy_epoch=7,
        principal_ref="principal-not-the-user-id",
        agent_version_ref="agent-version-1",
        purpose="context.answer",
        request_id="request-1",
        authentication_binding_ref="binding-1",
        checked_at=CHECKED_AT,
    )


def test_current_membership_proof_is_nominal_frozen_and_scope_lived() -> None:
    with pytest.raises(TypeError):
        CurrentMembershipVerification()

    scope = _open_membership_authority_scope()
    proof = _construct_current_membership_verification(
        authority_scope=scope,
        organization_id=ORGANIZATION_ID,
        user_id=USER_ID,
        membership_id=MEMBERSHIP_ID,
        membership_version=7,
        principal_ref="principal-from-auth",
        request_id="request-1",
        authentication_binding_ref="binding-1",
        checked_at=CHECKED_AT,
        policy_epoch_verification=policy_epoch_verification(),
    )

    _require_active_current_membership_verification(proof)
    with pytest.raises(FrozenInstanceError):
        proof.membership_version = 8  # type: ignore[misc]
    assert "_authority_scope" not in repr(proof)

    _close_membership_authority_scope(scope)
    with pytest.raises(ValueError, match="active Membership authority scope"):
        _require_active_current_membership_verification(proof)


@pytest.mark.parametrize(
    ("field_name", "invalid_value", "error_type"),
    (
        ("organization_id", str(ORGANIZATION_ID), TypeError),
        ("user_id", str(USER_ID), TypeError),
        ("membership_id", str(MEMBERSHIP_ID), TypeError),
        ("membership_version", True, ValueError),
        ("membership_version", 0, ValueError),
        ("membership_version", 1 << 63, ValueError),
        ("principal_ref", " ", ValueError),
        ("request_id", "", ValueError),
        ("authentication_binding_ref", 42, ValueError),
        ("checked_at", datetime(2026, 7, 21, 6, 0), ValueError),
        (
            "checked_at",
            datetime(2026, 7, 21, 14, 0, tzinfo=timezone(timedelta(hours=8))),
            ValueError,
        ),
    ),
)
def test_current_membership_proof_rejects_malformed_authority_facts(
    field_name: str,
    invalid_value: object,
    error_type: type[Exception],
) -> None:
    scope = _open_membership_authority_scope()
    values: dict[str, object] = {
        "organization_id": ORGANIZATION_ID,
        "user_id": USER_ID,
        "membership_id": MEMBERSHIP_ID,
        "membership_version": 7,
        "principal_ref": "principal-not-the-user-id",
        "request_id": "request-1",
        "authentication_binding_ref": "binding-1",
        "checked_at": CHECKED_AT,
    }
    values[field_name] = invalid_value

    with pytest.raises(error_type):
        current_membership_proof(scope=scope, **cast(Any, values))
    _close_membership_authority_scope(scope)


def test_current_membership_proof_rejects_forged_or_closed_authority_scope() -> None:
    forged_scope = object.__new__(_MembershipAuthorityScope)
    with pytest.raises(ValueError, match="active Membership authority scope"):
        current_membership_proof(scope=forged_scope)

    closed_scope = _open_membership_authority_scope()
    _close_membership_authority_scope(closed_scope)
    with pytest.raises(ValueError, match="active Membership authority scope"):
        current_membership_proof(scope=closed_scope)


def test_current_membership_proofs_from_distinct_authority_scopes_are_distinct(
) -> None:
    first_scope = _open_membership_authority_scope()
    second_scope = _open_membership_authority_scope()

    assert current_membership_proof(
        scope=first_scope
    ) != current_membership_proof(scope=second_scope)

    _close_membership_authority_scope(first_scope)
    _close_membership_authority_scope(second_scope)


def test_user_actor_is_nominal_exact_and_keeps_principal_distinct_from_user() -> None:
    with pytest.raises(TypeError):
        UserActor()

    scope = _open_membership_authority_scope()
    proof = _construct_current_membership_verification(
        authority_scope=scope,
        organization_id=ORGANIZATION_ID,
        user_id=USER_ID,
        membership_id=MEMBERSHIP_ID,
        membership_version=7,
        principal_ref="principal-not-the-user-id",
        request_id="request-1",
        authentication_binding_ref="binding-1",
        checked_at=CHECKED_AT,
        policy_epoch_verification=policy_epoch_verification(),
    )
    actor = _construct_user_actor(proof)

    assert actor.organization_id == ORGANIZATION_ID
    assert actor.user_id == USER_ID
    assert actor.membership_id == MEMBERSHIP_ID
    assert actor.membership_version == 7
    assert actor.principal_ref == "principal-not-the-user-id"
    assert actor.principal_ref != str(actor.user_id)
    assert actor.current_membership_verification is proof
    _require_active_user_actor(actor)
    with pytest.raises(FrozenInstanceError):
        actor.principal_ref = "changed"  # type: ignore[misc]

    _close_membership_authority_scope(scope)
    with pytest.raises(ValueError, match="active Membership authority scope"):
        _require_active_user_actor(actor)


def test_verified_authentication_requires_canonical_user_membership_and_version(
) -> None:
    context = verified_authentication_context(
        user_ref=str(USER_ID).replace("-", "").upper(),
        membership_ref=str(MEMBERSHIP_ID).replace("-", "").upper(),
    )

    assert context.user_ref == str(USER_ID)
    assert context.membership_ref == str(MEMBERSHIP_ID)
    assert context.membership_version == 7
    assert context.principal_ref == "principal-not-the-user-id"
    assert context.principal_ref != context.user_ref

    for field_name, invalid_value in (
        ("user_ref", None),
        ("user_ref", "not-a-uuid"),
        ("membership_ref", None),
        ("membership_ref", "not-a-uuid"),
        ("membership_version", None),
        ("membership_version", True),
        ("membership_version", 0),
        ("membership_version", 1 << 63),
    ):
        with pytest.raises(InvalidAuthenticationContext):
            values = {
                "user_ref": str(USER_ID),
                "membership_ref": str(MEMBERSHIP_ID),
                "membership_version": 7,
            }
            values[field_name] = invalid_value
            verified_authentication_context(**values)


def test_authenticated_invocation_binds_one_current_user_actor() -> None:
    scope = _open_membership_authority_scope()
    proof = current_membership_proof(scope=scope)
    organization_proof = _construct_existing_http_organization_verification(
        organization_id=ORGANIZATION_ID,
        request_id="request-1",
        authentication_binding_ref="binding-1",
        verified_at=CHECKED_AT,
    )

    with MissingTrustedScopeAuthority().current_scope(
        scope_authority_identity()
    ) as scope_snapshot:
        invocation = _construct_authenticated_http_invocation(
            request_id="request-1",
            authenticated_organization_ref=str(ORGANIZATION_ID),
            organization_verification=organization_proof,
            user_ref=str(USER_ID),
            principal_ref="principal-not-the-user-id",
            membership_ref=str(MEMBERSHIP_ID),
            membership_version=7,
            current_membership_verification=proof,
            agent_version_ref="agent-version-1",
            authenticated_application_ref="application-1",
            authentication_binding_ref="binding-1",
            trusted_purpose="context.answer",
            received_at=CHECKED_AT,
            trusted_scope_snapshot=scope_snapshot,
        )

    assert invocation.user_ref == str(USER_ID)
    assert invocation.membership_ref == str(MEMBERSHIP_ID)
    assert invocation.membership_version == 7
    assert invocation.user_actor.current_membership_verification is proof
    assert invocation.principal_ref != invocation.user_ref
    _close_membership_authority_scope(scope)


@pytest.mark.parametrize(
    ("proof_override", "invocation_override"),
    (
        ({"organization_id": UUID(int=9)}, {}),
        ({"user_id": UUID(int=10)}, {}),
        ({"membership_id": UUID(int=11)}, {}),
        ({"membership_version": 8}, {}),
        ({"principal_ref": "other-principal"}, {}),
        ({"request_id": "other-request"}, {}),
        ({"authentication_binding_ref": "other-binding"}, {}),
        ({"checked_at": datetime(2026, 7, 21, 5, 59, tzinfo=UTC)}, {}),
        ({}, {"user_ref": str(UUID(int=12))}),
        ({}, {"membership_ref": str(UUID(int=13))}),
        ({}, {"membership_version": 9}),
    ),
)
def test_authenticated_invocation_rejects_mismatched_current_membership(
    proof_override: dict[str, object],
    invocation_override: dict[str, object],
) -> None:
    scope = _open_membership_authority_scope()
    proof = current_membership_proof(
        scope=scope,
        **cast(Any, proof_override),
    )
    organization_proof = _construct_existing_http_organization_verification(
        organization_id=ORGANIZATION_ID,
        request_id="request-1",
        authentication_binding_ref="binding-1",
        verified_at=CHECKED_AT,
    )
    values: dict[str, object] = {
        "request_id": "request-1",
        "authenticated_organization_ref": str(ORGANIZATION_ID),
        "organization_verification": organization_proof,
        "user_ref": str(USER_ID),
        "principal_ref": "principal-not-the-user-id",
        "membership_ref": str(MEMBERSHIP_ID),
        "membership_version": 7,
        "current_membership_verification": proof,
        "agent_version_ref": "agent-version-1",
        "authenticated_application_ref": "application-1",
        "authentication_binding_ref": "binding-1",
        "trusted_purpose": "context.answer",
        "received_at": CHECKED_AT,
    }
    values.update(invocation_override)

    with MissingTrustedScopeAuthority().current_scope(
        scope_authority_identity()
    ) as scope_snapshot:
        values["trusted_scope_snapshot"] = scope_snapshot
        with pytest.raises(ValueError, match="current Membership"):
            cast(Any, _construct_authenticated_http_invocation)(**values)

    _close_membership_authority_scope(scope)


def test_authenticated_invocation_rejects_closed_membership_authority_scope() -> None:
    scope = _open_membership_authority_scope()
    proof = current_membership_proof(scope=scope)
    _close_membership_authority_scope(scope)
    organization_proof = _construct_existing_http_organization_verification(
        organization_id=ORGANIZATION_ID,
        request_id="request-1",
        authentication_binding_ref="binding-1",
        verified_at=CHECKED_AT,
    )

    with (
        MissingTrustedScopeAuthority().current_scope(
            scope_authority_identity()
        ) as scope_snapshot,
        pytest.raises(ValueError, match="active Membership authority scope"),
    ):
        _construct_authenticated_http_invocation(
            request_id="request-1",
            authenticated_organization_ref=str(ORGANIZATION_ID),
            organization_verification=organization_proof,
            user_ref=str(USER_ID),
            principal_ref="principal-not-the-user-id",
            membership_ref=str(MEMBERSHIP_ID),
            membership_version=7,
            current_membership_verification=proof,
            agent_version_ref="agent-version-1",
            authenticated_application_ref="application-1",
            authentication_binding_ref="binding-1",
            trusted_purpose="context.answer",
            received_at=CHECKED_AT,
            trusted_scope_snapshot=scope_snapshot,
        )


@pytest.mark.parametrize("target", ("proof", "actor"))
def test_current_membership_authority_rejects_mutated_provenance(target: str) -> None:
    scope = _open_membership_authority_scope()
    proof = current_membership_proof(scope=scope)
    actor = _construct_user_actor(proof)
    if target == "proof":
        object.__setattr__(
            proof,
            "construction_provenance",
            cast(MembershipVerificationProvenance, cast(Any, "untrusted")),
        )
        expected = "active Membership authority scope"
    else:
        object.__setattr__(
            actor,
            "construction_provenance",
            cast(UserActorConstructionProvenance, cast(Any, "untrusted")),
        )
        expected = "invalid construction provenance"

    with pytest.raises(ValueError, match=expected):
        _require_active_user_actor(actor)
    _close_membership_authority_scope(scope)
