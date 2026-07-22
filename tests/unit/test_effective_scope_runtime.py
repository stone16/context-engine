from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import UUID

import pytest

from engine.runtime.actor import (
    _close_membership_authority_scope,
    _construct_current_membership_verification,
    _open_membership_authority_scope,
)
from engine.runtime.construction import Runtime, required_kernel_dependencies
from engine.runtime.content_io import RuntimeContentIo
from engine.runtime.contracts import Acquire, ContextNeed, RequestNarrowing, Resolved
from engine.runtime.delivery import (
    TrustedDeliveryContext,
    _construct_direct_delivery_context,
)
from engine.runtime.invocation import (
    AuthenticatedInvocation,
    _construct_authenticated_http_invocation,
)
from engine.runtime.organization import (
    _construct_existing_http_organization_verification,
)
from engine.runtime.policy_epoch import (
    _close_policy_epoch_authority_scope,
    _construct_policy_epoch_session,
    _observe_current_policy_epoch,
    _open_policy_epoch_authority_scope,
)
from engine.runtime.scope import (
    MISSING_TRUSTED_SCOPE,
    MissingTrustedScope,
    ScopeSet,
    ScopeTarget,
    TrustedScopeOperands,
)
from engine.runtime.scope_authority import (
    _close_scope_authority_scope,
    _construct_trusted_scope_snapshot,
    _open_scope_authority_scope,
)
from tests.support.context_run import (
    TEST_QUERY_DIGEST_KEYRING,
    recording_context_run_session,
)

AS_OF = datetime(2026, 7, 21, 9, 0, tzinfo=UTC)
ORGANIZATION_ID = UUID("81e18bca-86a1-478a-937d-7675c6fe69b0")
USER_ID = UUID("d3d9893f-82d2-4890-8cb2-4c7e57a56f16")
MEMBERSHIP_ID = UUID("9c9e9f4c-a5ec-4417-9408-0346e1c6c998")
TARGET_A = ScopeTarget(ORGANIZATION_ID, "source:a", "resource:a")
TARGET_B = ScopeTarget(ORGANIZATION_ID, "source:b", "resource:b")
ALL_TARGETS = ScopeSet(frozenset({TARGET_A, TARGET_B}))


class ContentIoSpy:
    def __init__(self) -> None:
        self.calls = 0

    def discover(self, request: Acquire) -> tuple[()]:
        del request
        self.calls += 1
        return ()

    def authorize_and_project(self) -> tuple[()]:
        self.calls += 1
        return ()

    def read_content(self) -> tuple[()]:
        self.calls += 1
        return ()


def make_runtime(spy: ContentIoSpy) -> Runtime:
    return Runtime(
        required_kernel_dependencies(),
        content_io=RuntimeContentIo(index=spy, provider=spy, source_content=spy),
        clock=lambda: AS_OF,
        query_digest_keyring=TEST_QUERY_DIGEST_KEYRING,
    )


def make_operands(
    **changes: ScopeSet | MissingTrustedScope,
) -> TrustedScopeOperands:
    values: dict[str, ScopeSet | MissingTrustedScope] = {
        "organization_boundary": ALL_TARGETS,
        "membership_rights": ALL_TARGETS,
        "principal_grants": ALL_TARGETS,
        "agent_ceiling": ALL_TARGETS,
        "source_native_acl": ALL_TARGETS,
        "resource_acl": ALL_TARGETS,
        "purpose_policy": ALL_TARGETS,
    }
    values.update(changes)
    return TrustedScopeOperands(**values)


@contextmanager
def bound_runtime_inputs(
    operands: TrustedScopeOperands,
) -> Iterator[tuple[AuthenticatedInvocation, TrustedDeliveryContext]]:
    membership_scope = _open_membership_authority_scope()
    policy_epoch_scope = _open_policy_epoch_authority_scope()
    scope_authority_scope = _open_scope_authority_scope()
    try:

        class CurrentEpochPort:
            def read_current_epoch(self, organization_id: UUID) -> object:
                assert organization_id == ORGANIZATION_ID
                return 3

        policy_epoch_verification = _observe_current_policy_epoch(
            _construct_policy_epoch_session(
                authority_scope=policy_epoch_scope,
                organization_id=ORGANIZATION_ID,
                port=CurrentEpochPort(),
            )
        )
        organization_verification = _construct_existing_http_organization_verification(
            organization_id=ORGANIZATION_ID,
            request_id="request-1",
            authentication_binding_ref="binding-1",
            verified_at=AS_OF,
        )
        with recording_context_run_session() as (persistence_session, _):
            membership_verification = _construct_current_membership_verification(
                authority_scope=membership_scope,
                organization_id=ORGANIZATION_ID,
                user_id=USER_ID,
                membership_id=MEMBERSHIP_ID,
                membership_version=3,
                principal_ref="principal-1",
                request_id="request-1",
                authentication_binding_ref="binding-1",
                checked_at=AS_OF,
                policy_epoch_verification=policy_epoch_verification,
                context_run_persistence_session=persistence_session,
            )
            scope_snapshot = _construct_trusted_scope_snapshot(
                authority_scope=scope_authority_scope,
                organization_id=ORGANIZATION_ID,
                user_id=USER_ID,
                membership_id=MEMBERSHIP_ID,
                membership_version=3,
                policy_epoch=3,
                principal_ref="principal-1",
                agent_version_ref="agent-version-1",
                purpose="context.answer",
                request_id="request-1",
                authentication_binding_ref="binding-1",
                checked_at=AS_OF,
                organization_boundary=operands.organization_boundary,
                membership_rights=operands.membership_rights,
                principal_grants=operands.principal_grants,
                agent_ceiling=operands.agent_ceiling,
                source_native_acl=operands.source_native_acl,
                resource_acl=operands.resource_acl,
                purpose_policy=operands.purpose_policy,
            )
            invocation = _construct_authenticated_http_invocation(
                request_id="request-1",
                authenticated_organization_ref=str(ORGANIZATION_ID),
                organization_verification=organization_verification,
                user_ref=str(USER_ID),
                principal_ref="principal-1",
                membership_ref=str(MEMBERSHIP_ID),
                membership_version=3,
                current_membership_verification=membership_verification,
                agent_version_ref="agent-version-1",
                authenticated_application_ref="application-1",
                authentication_binding_ref="binding-1",
                trusted_purpose="context.answer",
                received_at=AS_OF,
                trusted_scope_snapshot=scope_snapshot,
            )
            delivery = _construct_direct_delivery_context(
                purpose="context.answer",
                authenticated_application_ref="application-1",
                delivery_binding_ref="binding-1",
                established_at=AS_OF,
            )
            yield invocation, delivery
    finally:
        _close_policy_epoch_authority_scope(policy_epoch_scope)
        _close_scope_authority_scope(scope_authority_scope)
        _close_membership_authority_scope(membership_scope)


def resolve(
    operands: TrustedScopeOperands,
    *,
    narrowing: RequestNarrowing | None = None,
    spy: ContentIoSpy | None = None,
) -> Resolved:
    selected_spy = spy or ContentIoSpy()
    with bound_runtime_inputs(operands) as (invocation, delivery):
        outcome = make_runtime(selected_spy).resolve(
            invocation,
            delivery,
            Acquire(
                need=ContextNeed(query="scope decision probe"),
                narrowing=narrowing,
            ),
        )
    assert type(outcome) is Resolved
    return outcome


@pytest.mark.security_evidence(id="RUNTIME-SCOPE-INTERSECTION-004", layer="runtime")
def test_runtime_observes_only_effective_scope_and_returns_empty_package() -> None:
    spy = ContentIoSpy()

    outcome = resolve(make_operands(), spy=spy)

    assert outcome.scope_decision.target_count == 2
    assert outcome.scope_decision.is_empty is False
    assert len(outcome.scope_decision.digest) == 64
    assert spy.calls == 0
    assert outcome.package.blocks == ()
    assert outcome.package.evidence == ()
    assert outcome.package.gaps == ()
    public_package = asdict(outcome.package)
    serialized_package = repr(public_package)
    assert "source:a" not in serialized_package
    assert "resource:a" not in serialized_package
    assert outcome.scope_decision.digest not in serialized_package


def test_agent_ceiling_can_only_reduce_other_trusted_grants(
) -> None:
    broad_spy = ContentIoSpy()
    broad_agent = resolve(
        make_operands(
            principal_grants=ScopeSet(frozenset({TARGET_A})),
            agent_ceiling=ALL_TARGETS,
        ),
        spy=broad_spy,
    )
    narrow_spy = ContentIoSpy()
    narrow_agent = resolve(
        make_operands(
            principal_grants=ALL_TARGETS,
            agent_ceiling=ScopeSet(frozenset({TARGET_A})),
        ),
        spy=narrow_spy,
    )

    assert broad_agent.scope_decision.target_count == 1
    assert narrow_agent.scope_decision.target_count == 1
    assert broad_agent.scope_decision.digest == narrow_agent.scope_decision.digest
    assert broad_spy.calls == narrow_spy.calls == 0
    assert broad_agent.package.evidence == narrow_agent.package.evidence == ()


def test_overbroad_request_refs_do_not_expand_the_trusted_intersection() -> None:
    operands = make_operands()

    omitted = resolve(operands)

    exact = resolve(
        operands,
        narrowing=RequestNarrowing(
            source_refs=("source:a",),
            resource_refs=("resource:a",),
        ),
    )
    mixed_with_unknown_and_denied = resolve(
        operands,
        narrowing=RequestNarrowing(
            source_refs=("source:a", "source:b", "source:unknown"),
            resource_refs=("resource:a", "resource:b", "resource:unknown"),
        ),
    )

    assert omitted.scope_decision.target_count == 2
    assert exact.scope_decision.target_count == 1
    assert mixed_with_unknown_and_denied.scope_decision.target_count == 2
    assert mixed_with_unknown_and_denied.scope_decision == omitted.scope_decision


@pytest.mark.parametrize(
    "operand_name",
    (
        "organization_boundary",
        "membership_rights",
        "principal_grants",
        "agent_ceiling",
        "source_native_acl",
        "resource_acl",
        "purpose_policy",
    ),
)
@pytest.mark.parametrize(
    "unavailable",
    (MISSING_TRUSTED_SCOPE, ScopeSet(frozenset())),
    ids=("missing", "empty"),
)
def test_each_missing_or_empty_trusted_operand_absorbs_to_empty_without_io(
    operand_name: str,
    unavailable: ScopeSet | MissingTrustedScope,
) -> None:
    spy = ContentIoSpy()

    outcome = resolve(make_operands(**{operand_name: unavailable}), spy=spy)

    assert outcome.scope_decision.target_count == 0
    assert outcome.scope_decision.is_empty is True
    assert spy.calls == 0
    assert outcome.package.coverage.reason == "no_authorized_evidence"


def test_runtime_rejects_scope_snapshot_mutated_to_another_purpose_before_io() -> None:
    spy = ContentIoSpy()

    with bound_runtime_inputs(make_operands()) as (invocation, delivery):
        object.__setattr__(
            invocation.trusted_scope_snapshot,
            "purpose",
            "context.summarize",
        )
        with pytest.raises(ValueError, match="trusted scope snapshot"):
            make_runtime(spy).resolve(
                invocation,
                delivery,
                Acquire(need=ContextNeed(query="purpose replay")),
            )

    assert spy.calls == 0


def test_runtime_rejects_scope_snapshot_after_its_authority_scope_closes() -> None:
    spy = ContentIoSpy()
    with bound_runtime_inputs(make_operands()) as inputs:
        invocation, delivery = inputs
        _close_scope_authority_scope(invocation.trusted_scope_snapshot._authority_scope)

        with pytest.raises(ValueError, match="active trusted scope authority"):
            make_runtime(spy).resolve(
                invocation,
                delivery,
                Acquire(need=ContextNeed(query="expired scope")),
            )

    assert spy.calls == 0
