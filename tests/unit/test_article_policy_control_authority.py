from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest

from engine.control import (
    ArticlePolicyDefaultStorePort,
    ContextControl,
    ControlOperation,
    ControlOperatorAuthenticationRejected,
    ControlOperatorAuthority,
    ControlStorePort,
    SetTenantArticlePolicyDefault,
    SourceNotAvailable,
    TrustedControlCall,
    VerifiedControlOperatorIdentity,
)
from engine.runtime.article_access_policy import (
    ArticleAccessPolicyKind,
    ArticleAccessPolicySetting,
)

ORGANIZATION_ID = UUID("161260a4-76cd-4fae-b719-1dc8e3764f6e")
OTHER_ORGANIZATION_ID = UUID("d66d3458-8565-4664-b92f-fbc18563a6b4")
NOW = datetime(2026, 7, 30, 9, 0, tzinfo=UTC)
COMMAND = SetTenantArticlePolicyDefault(
    expected_version=1,
    setting=ArticleAccessPolicySetting(ArticleAccessPolicyKind.ORGANIZATION),
)


class _Authenticator:
    def authenticate(self, opaque_credential: str) -> VerifiedControlOperatorIdentity:
        if opaque_credential != "policy-credential":
            raise ControlOperatorAuthenticationRejected
        return VerifiedControlOperatorIdentity(
            organization_id=ORGANIZATION_ID,
            operator_ref="operator:article-policy",
            authentication_binding_ref="binding:article-policy",
            authority_ref="authority:article-policy",
            allowed_operations=frozenset(
                {
                    ControlOperation.SET_TENANT_ARTICLE_POLICY_DEFAULT,
                    ControlOperation.READ_SOURCE,
                }
            ),
            valid_from=NOW - timedelta(minutes=1),
            expires_at=NOW + timedelta(hours=1),
        )


class _PolicyStore:
    def __init__(self) -> None:
        self.calls = 0

    def set_tenant_article_policy_default(
        self,
        call: TrustedControlCall,
        command: SetTenantArticlePolicyDefault,
    ) -> int:
        assert call.organization_id == ORGANIZATION_ID
        assert call.operation is ControlOperation.SET_TENANT_ARTICLE_POLICY_DEFAULT
        assert command is COMMAND
        self.calls += 1
        return 2

    def set_source_article_policy_default(self, *args: object) -> int:
        raise AssertionError("unexpected source Article default call")

    def __getattr__(self, name: str) -> object:
        # ContextControl's constructor requires the complete ordinary Control
        # port. These methods must never be reached by this focused authority
        # test, while the Article method above remains an honest store spy.
        return lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError(f"unexpected Control store call: {name}")
        )


def _authority() -> ControlOperatorAuthority:
    return ControlOperatorAuthority(
        _Authenticator(),
        call_ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    )


def _control(
    store: _PolicyStore,
    authority: ControlOperatorAuthority,
) -> ContextControl:
    return ContextControl(
        store=cast(ControlStorePort, store),
        article_policy_store=cast(ArticlePolicyDefaultStorePort, store),
        authority=authority,
        clock=lambda: NOW,
    )


def test_article_policy_change_rejects_wrong_operation_before_store() -> None:
    store = _PolicyStore()
    authority = _authority()
    control = _control(store, authority)

    with (
        authority.authorize(
            opaque_credential="policy-credential",
            operation=ControlOperation.READ_SOURCE,
            request_id="wrong-operation",
        ) as call,
        pytest.raises(SourceNotAvailable),
    ):
        control.set_tenant_article_policy_default(call, COMMAND)

    assert store.calls == 0


def test_article_policy_change_call_is_one_shot_before_store() -> None:
    store = _PolicyStore()
    authority = _authority()
    control = _control(store, authority)

    with authority.authorize(
        opaque_credential="policy-credential",
        operation=ControlOperation.SET_TENANT_ARTICLE_POLICY_DEFAULT,
        request_id="one-shot-policy-change",
    ) as call:
        assert control.set_tenant_article_policy_default(call, COMMAND) == 2
        with pytest.raises(SourceNotAvailable):
            control.set_tenant_article_policy_default(call, COMMAND)

    assert store.calls == 1


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("organization_id", OTHER_ORGANIZATION_ID),
        ("operation", ControlOperation.READ_SOURCE),
        ("request_id", "tampered-request"),
        ("expires_at", NOW + timedelta(days=1)),
    ],
)
def test_article_policy_change_rederives_tamper_evidence_before_store(
    field_name: str,
    replacement: object,
) -> None:
    store = _PolicyStore()
    authority = _authority()
    control = _control(store, authority)

    with authority.authorize(
        opaque_credential="policy-credential",
        operation=ControlOperation.SET_TENANT_ARTICLE_POLICY_DEFAULT,
        request_id="tamper-policy-change",
    ) as call:
        object.__setattr__(call, field_name, replacement)
        with pytest.raises(SourceNotAvailable):
            control.set_tenant_article_policy_default(call, COMMAND)

    assert store.calls == 0


def test_article_policy_control_surface_is_narrowed_to_future_defaults() -> None:
    assert {
        operation
        for operation in ControlOperation
        if "article" in operation.value
    } == {
        ControlOperation.SET_SOURCE_ARTICLE_POLICY_DEFAULT,
        ControlOperation.SET_TENANT_ARTICLE_POLICY_DEFAULT,
    }
    for deferred_surface in (
        "observe_article_source_acl",
        "register_article_access_group",
        "set_article_access_group_membership",
        "set_explicit_article_policy",
    ):
        assert not hasattr(ContextControl, deferred_surface)
