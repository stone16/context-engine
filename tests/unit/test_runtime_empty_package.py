from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID

import pytest

from adapters.http.scope_authority import (
    MissingTrustedScopeAuthority,
    ScopeAuthorityIdentity,
)
from engine.runtime.actor import (
    _close_membership_authority_scope,
    _construct_current_membership_verification,
    _open_membership_authority_scope,
)
from engine.runtime.budget import PackageBudget, PackageBudgetRequest
from engine.runtime.construction import (
    AuthorizationKernel,
    Runtime,
    required_kernel_dependencies,
)
from engine.runtime.content_io import RuntimeContentIo
from engine.runtime.contracts import (
    Acquire,
    ContextNeed,
)
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

AS_OF = datetime(2026, 7, 21, 5, 0, tzinfo=UTC)
INTERNAL_ORGANIZATION_REF = "81e18bca-86a1-478a-937d-7675c6fe69b0"
INTERNAL_USER_REF = "d3d9893f-82d2-4890-8cb2-4c7e57a56f16"
INTERNAL_MEMBERSHIP_REF = "9c9e9f4c-a5ec-4417-9408-0346e1c6c998"
SERVER_BUDGET = PackageBudget(
    max_tokens=1_000,
    max_provider_calls=8,
    max_cost_microunits=25_000,
    max_elapsed_ms=2_500,
)


class ContentIoSpy:
    def __init__(self) -> None:
        self.index_calls = 0
        self.provider_calls = 0
        self.source_content_calls = 0

    def discover(self, request: Acquire) -> tuple[()]:
        self.index_calls += 1
        return ()

    def authorize_and_project(self) -> tuple[()]:
        self.provider_calls += 1
        return ()

    def read_content(self) -> tuple[()]:
        self.source_content_calls += 1
        return ()

    @property
    def total_calls(self) -> int:
        return self.index_calls + self.provider_calls + self.source_content_calls


@contextmanager
def trusted_operands() -> Iterator[
    tuple[AuthenticatedInvocation, TrustedDeliveryContext]
]:
    authority_scope = _open_membership_authority_scope()
    policy_epoch_scope = _open_policy_epoch_authority_scope()

    class CurrentEpochPort:
        def read_current_epoch(self, organization_id: UUID) -> object:
            assert organization_id == UUID(INTERNAL_ORGANIZATION_REF)
            return 1

    policy_epoch_verification = _observe_current_policy_epoch(
        _construct_policy_epoch_session(
            authority_scope=policy_epoch_scope,
            organization_id=UUID(INTERNAL_ORGANIZATION_REF),
            port=CurrentEpochPort(),
        )
    )
    verification = _construct_existing_http_organization_verification(
        organization_id=UUID(INTERNAL_ORGANIZATION_REF),
        request_id="request-1",
        authentication_binding_ref="binding-internal",
        verified_at=AS_OF,
    )
    membership_verification = _construct_current_membership_verification(
        authority_scope=authority_scope,
        organization_id=UUID(INTERNAL_ORGANIZATION_REF),
        user_id=UUID(INTERNAL_USER_REF),
        membership_id=UUID(INTERNAL_MEMBERSHIP_REF),
        membership_version=1,
        principal_ref="principal-internal",
        request_id="request-1",
        authentication_binding_ref="binding-internal",
        checked_at=AS_OF,
        policy_epoch_verification=policy_epoch_verification,
    )
    try:
        scope_identity = ScopeAuthorityIdentity(
            organization_id=UUID(INTERNAL_ORGANIZATION_REF),
            user_id=UUID(INTERNAL_USER_REF),
            membership_id=UUID(INTERNAL_MEMBERSHIP_REF),
            membership_version=1,
            policy_epoch=1,
            principal_ref="principal-internal",
            agent_version_ref="agent-version-internal",
            purpose="context.answer",
            request_id="request-1",
            authentication_binding_ref="binding-internal",
            checked_at=AS_OF,
        )
        with MissingTrustedScopeAuthority().current_scope(
            scope_identity
        ) as scope_snapshot:
            invocation = _construct_authenticated_http_invocation(
                request_id="request-1",
                authenticated_organization_ref=INTERNAL_ORGANIZATION_REF,
                organization_verification=verification,
                user_ref=INTERNAL_USER_REF,
                principal_ref="principal-internal",
                membership_ref=INTERNAL_MEMBERSHIP_REF,
                membership_version=1,
                current_membership_verification=membership_verification,
                agent_version_ref="agent-version-internal",
                authenticated_application_ref="application-internal",
                authentication_binding_ref="binding-internal",
                trusted_purpose="context.answer",
                received_at=AS_OF,
                trusted_scope_snapshot=scope_snapshot,
            )
            delivery = _construct_direct_delivery_context(
                purpose="context.answer",
                authenticated_application_ref="application-internal",
                delivery_binding_ref="binding-internal",
                established_at=AS_OF,
            )
            yield invocation, delivery
    finally:
        _close_policy_epoch_authority_scope(policy_epoch_scope)
        _close_membership_authority_scope(authority_scope)


def runtime(content_io_spy: ContentIoSpy | None = None) -> Runtime:
    content_io = None
    if content_io_spy is not None:
        content_io = RuntimeContentIo(
            index=content_io_spy,
            provider=content_io_spy,
            source_content=content_io_spy,
        )
    return Runtime(
        required_kernel_dependencies(),
        package_ttl_seconds=300,
        server_budget=SERVER_BUDGET,
        content_io=content_io,
        clock=lambda: AS_OF,
    )


def test_resolve_returns_one_tenant_safe_empty_package() -> None:
    with trusted_operands() as (invocation, delivery):
        outcome = runtime().resolve(
            invocation,
            delivery,
            Acquire(need=ContextNeed(query="What constrains Runtime delivery?")),
        )

    assert outcome.kind == "resolved"
    package = outcome.package
    assert package.organization_ref.startswith("orgpkg_")
    assert len(package.organization_ref) == len("orgpkg_") + 32
    assert package.organization_ref != INTERNAL_ORGANIZATION_REF
    assert package.purpose == "context.answer"
    assert package.ttl_seconds == 300
    assert package.as_of == AS_OF
    assert package.expires_at == datetime(2026, 7, 21, 5, 5, tzinfo=UTC)
    assert package.decision_ref.startswith("dec_")
    assert len(package.decision_ref) == len("dec_") + 32
    assert package.blocks == ()
    assert package.evidence == ()
    assert package.gaps == ()
    assert package.coverage.status == "empty"
    assert package.coverage.reason == "no_authorized_evidence"
    assert package.budget_usage.tokens == 0
    assert package.budget_usage.provider_calls == 0
    assert package.budget_usage.cost_microunits == 0
    assert package.budget_usage.elapsed_ms == 0


def test_empty_path_performs_zero_index_provider_or_source_content_io() -> None:
    spy = ContentIoSpy()

    with trusted_operands() as (invocation, delivery):
        runtime(spy).resolve(
            invocation,
            delivery,
            Acquire(need=ContextNeed(query="zero I/O probe")),
        )

    assert spy.total_calls == 0


def test_content_io_spy_would_detect_every_runtime_dependency_call() -> None:
    spy = ContentIoSpy()
    candidate = runtime(spy)
    request = Acquire(need=ContextNeed(query="mutation control"))

    candidate._content_io.index.discover(request)
    candidate._content_io.provider.authorize_and_project()
    candidate._content_io.source_content.read_content()

    assert spy.index_calls == spy.provider_calls == spy.source_content_calls == 1


def test_resolve_records_the_finite_effective_budget_without_exposing_usage() -> None:
    with trusted_operands() as (invocation, delivery):
        inherited = runtime().resolve(
            invocation,
            delivery,
            Acquire(need=ContextNeed(query="inherit budget")),
        )
        narrowed = runtime().resolve(
            invocation,
            delivery,
            Acquire(
                need=ContextNeed(query="narrow budget"),
                package_budget=PackageBudgetRequest(
                    max_tokens=100,
                    max_elapsed_ms=1_000,
                ),
            ),
        )

    assert inherited.effective_budget == SERVER_BUDGET
    assert narrowed.effective_budget == PackageBudget(
        max_tokens=100,
        max_provider_calls=8,
        max_cost_microunits=25_000,
        max_elapsed_ms=1_000,
    )
    assert narrowed.package.budget_usage.tokens == 0


def test_internal_org_ref_must_be_trusted_uuid_not_opaque_output_ref() -> None:
    with trusted_operands() as (invocation, delivery):
        object.__setattr__(
            invocation,
            "organization_ref",
            "orgpkg_00000000000000000000000000000001",
        )

        with pytest.raises(ValueError, match="existing-Organization verification"):
            runtime().resolve(
                invocation,
                delivery,
                Acquire(need=ContextNeed(query="replay package ref")),
            )


def test_runtime_rejects_mismatched_operands_before_allocating_refs() -> None:
    with trusted_operands() as (invocation, delivery):
        object.__setattr__(delivery, "delivery_binding_ref", "other-binding")
        candidate = Runtime(
            required_kernel_dependencies(),
            package_ttl_seconds=300,
            server_budget=SERVER_BUDGET,
            clock=lambda: AS_OF,
        )

        with pytest.raises(ValueError, match="trusted delivery context"):
            candidate.resolve(
                invocation,
                delivery,
                Acquire(need=ContextNeed(query="mismatch")),
            )


def test_runtime_rejects_organization_proof_mismatch() -> None:
    with trusted_operands() as (invocation, delivery):
        object.__setattr__(invocation, "organization_ref", str(UUID(int=9)))
        candidate = Runtime(
            required_kernel_dependencies(),
            server_budget=SERVER_BUDGET,
            clock=lambda: AS_OF,
        )

        with pytest.raises(ValueError, match="existing-Organization verification"):
            candidate.resolve(
                invocation,
                delivery,
                Acquire(need=ContextNeed(query="switched organization")),
            )


def test_runtime_rejects_user_actor_after_authority_scope_closes_before_io() -> None:
    spy = ContentIoSpy()
    with trusted_operands() as operands:
        invocation, delivery = operands

    with pytest.raises(ValueError, match="active Membership authority scope"):
        runtime(spy).resolve(
            invocation,
            delivery,
            Acquire(need=ContextNeed(query="expired actor authority")),
        )

    assert spy.total_calls == 0


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (
        ("user_ref", str(UUID(int=10))),
        ("membership_ref", str(UUID(int=11))),
        ("membership_version", 2),
        ("principal_ref", "other-principal"),
        ("request_id", "other-request"),
        ("authentication_binding_ref", "other-binding"),
        ("received_at", datetime(2026, 7, 21, 5, 1, tzinfo=UTC)),
    ),
)
def test_runtime_revalidates_every_user_actor_binding_before_io(
    field_name: str,
    invalid_value: object,
) -> None:
    spy = ContentIoSpy()
    with trusted_operands() as (invocation, delivery):
        object.__setattr__(invocation, field_name, invalid_value)

        with pytest.raises(ValueError):
            runtime(spy).resolve(
                invocation,
                delivery,
                Acquire(need=ContextNeed(query="mutated actor binding")),
            )

    assert spy.total_calls == 0


@pytest.mark.parametrize("invalid_ttl", [0, -1, True, 1.5])
def test_runtime_profile_requires_a_finite_positive_exact_ttl(
    invalid_ttl: object,
) -> None:
    with pytest.raises(ValueError, match="package_ttl_seconds"):
        Runtime(
            required_kernel_dependencies(),
            package_ttl_seconds=invalid_ttl,  # type: ignore[arg-type]
            server_budget=SERVER_BUDGET,
        )


def test_runtime_issues_closed_fresh_server_refs_without_factory_injection() -> None:
    with trusted_operands() as (invocation, delivery):
        candidate = runtime()
        request = Acquire(need=ContextNeed(query="fresh refs"))

        first = candidate.resolve(invocation, delivery, request).package
        second = candidate.resolve(invocation, delivery, request).package

    assert first.organization_ref != second.organization_ref
    assert first.decision_ref != second.decision_ref
    assert UUID(INTERNAL_ORGANIZATION_REF).hex not in first.organization_ref
    assert UUID(INTERNAL_ORGANIZATION_REF).hex not in first.decision_ref
    assert "organization_ref_factory" not in Runtime.__init__.__annotations__
    assert "decision_ref_factory" not in Runtime.__init__.__annotations__


def test_authorization_kernel_is_sealed_inside_runtime_composition() -> None:
    candidate = runtime()

    assert type(candidate._kernel) is AuthorizationKernel
    assert "kernel" not in Runtime.__init__.__annotations__
    assert "kernel" not in Runtime.__init__.__code__.co_varnames[
        : Runtime.__init__.__code__.co_argcount
        + Runtime.__init__.__code__.co_kwonlyargcount
    ]
